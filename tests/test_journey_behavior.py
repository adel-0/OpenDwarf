"""Unit tests for JourneyBehavior — the M3 world-map autopilot."""

from __future__ import annotations

from opendwarf.actions.skills import SkillContext
from opendwarf.behaviors.base import BehaviorStatus
from opendwarf.behaviors.journey import JourneyBehavior
from opendwarf.behaviors.policy import Policy
from opendwarf.state.game_state import GameState, NearbySite, Position


# ----------------------------------------------------------------------
# Fakes  (same pattern as tests/test_grind_combat.py)
# ----------------------------------------------------------------------

class _FakeLua:
    def __init__(self):
        self.actions: list[str] = []

    def execute_action(self, key):
        self.actions.append(key)


class _FakeExtractor:
    """Absolute coords = local + a fixed region offset."""
    OFF = (1000, 1000, 0)
    has_offset = True

    def adventurer_abs(self, state):
        p = state.adventurer_position
        if p is None:
            return None
        return (self.OFF[0] + p.x, self.OFF[1] + p.y, p.z)

    def to_abs(self, x, y, z):
        return (self.OFF[0] + x, self.OFF[1] + y, z)

    def ensure_fresh(self, state):
        pass


class _FakePathfinder:
    def __init__(self, path=None):
        self.path = path

    def find_path(self, cur, goal, now_tick=0, partial=False):
        return list(self.path) if self.path else []

    def frontier_path(self, cur, direction, now_tick=0):
        return []


def _ctx(lua=None, path=None):
    return SkillContext(lua or _FakeLua(), None, _FakePathfinder(path), _FakeExtractor())


# ----------------------------------------------------------------------
# State / site helpers
# ----------------------------------------------------------------------

def _site(sid=7, name="Ironhold", dist=40, direction="E", stype="fortress") -> NearbySite:
    return NearbySite(id=sid, name=name, site_type=stype, distance=dist, direction=direction)


def _state(
    *,
    active: bool = False,
    army_pos: Position | None = None,
    site_name: str = "",
    sites: list[NearbySite] | None = None,
    tick: int = 0,
) -> GameState:
    s = GameState()
    s.fast_travel_active = active
    s.fast_travel_army_pos = army_pos
    s.site_name = site_name
    s.nearby_sites = list(sites) if sites is not None else []
    s.tick_counter = tick
    return s


def _behavior(lua=None, site_id=7, site_name="Ironhold") -> tuple[JourneyBehavior, _FakeLua]:
    if lua is None:
        lua = _FakeLua()
    ctx = _ctx(lua)
    policy = Policy()
    b = JourneyBehavior(ctx, policy, site_id=site_id, site_name=site_name)
    return b, lua


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

def test_enter_travel_when_not_active():
    """Not in travel + dest site present → first step issues travel_enter and is RUNNING."""
    b, lua = _behavior()
    state = _state(active=False, sites=[_site()])
    result = b.step(state)
    assert result.status is BehaviorStatus.RUNNING
    assert lua.actions == ["travel_enter"]


def test_no_nearby_dest_returns_needs_llm():
    """Dest site absent from nearby_sites → NEEDS_LLM mentioning 'not among the nearby sites'."""
    b, lua = _behavior(site_id=7, site_name="Ironhold")
    state = _state(active=False, sites=[])
    result = b.step(state)
    assert result.status is BehaviorStatus.NEEDS_LLM
    assert "not among the nearby sites" in result.outcome


def test_enter_attempts_exhausted_returns_needs_llm():
    """After _ENTER_ATTEMPTS (3) travel_enter presses without entering travel, next step is NEEDS_LLM."""
    b, lua = _behavior()
    # Keep state not-active so the behavior keeps issuing travel_enter
    for _ in range(JourneyBehavior._ENTER_ATTEMPTS):
        result = b.step(_state(active=False, sites=[_site()]))
        assert result.status is BehaviorStatus.RUNNING, "should still be running"
    # _enter_attempts is now == _ENTER_ATTEMPTS; next step should refuse
    result = b.step(_state(active=False, sites=[_site()]))
    assert result.status is BehaviorStatus.NEEDS_LLM
    assert "will not engage" in result.outcome


def test_form_army_by_moving():
    """In travel with no army yet → nudge step issues a move key to form the army."""
    b, lua = _behavior()
    # First give the behavior the bearing by doing a not-active step (captures bearing)
    b.step(_state(active=False, sites=[_site(dist=40, direction="E")]))
    lua.actions.clear()
    # Now in travel but army not yet formed
    state = _state(active=True, army_pos=None, sites=[_site(dist=40, direction="E")])
    result = b.step(state)
    assert result.status is BehaviorStatus.RUNNING
    # E → delta (1,0) → A_MOVE_E
    assert lua.actions[-1] == "A_MOVE_E"


def test_army_never_forms_returns_needs_llm():
    """Army never forms after _FORM_ATTEMPTS moves → NEEDS_LLM 'army never formed' + travel_exit."""
    b, lua = _behavior()
    # Give bearing first
    b.step(_state(active=False, sites=[_site(dist=40, direction="E")]))
    lua.actions.clear()
    # Step _FORM_ATTEMPTS+1 times in travel with no army
    for i in range(JourneyBehavior._FORM_ATTEMPTS + 1):
        state = _state(active=True, army_pos=None, sites=[_site(dist=40, direction="E")])
        result = b.step(state)
    assert result.status is BehaviorStatus.NEEDS_LLM
    assert "army never formed" in result.outcome
    assert "travel_exit" in lua.actions


def test_steer_toward_dest_while_army_advancing():
    """In travel with army advancing (pos changes each step) → RUNNING, issues A_MOVE_E each step."""
    b, lua = _behavior()
    # Give bearing
    b.step(_state(active=False, sites=[_site(dist=40, direction="E")]))
    lua.actions.clear()

    positions = [Position(300, 150, 0), Position(303, 150, 0)]
    distances = [40, 37]

    for pos, dist in zip(positions, distances):
        state = _state(active=True, army_pos=pos, sites=[_site(dist=dist, direction="E")])
        result = b.step(state)
        assert result.status is BehaviorStatus.RUNNING
        assert lua.actions[-1] == "A_MOVE_E"

    assert "travel_exit" not in lua.actions


def test_stall_rotates_detour_heading():
    """Army pos held constant → after _STALL_LIMIT steers heading rotates away from straight E."""
    b, lua = _behavior()
    # Give bearing
    b.step(_state(active=False, sites=[_site(dist=40, direction="E")]))
    lua.actions.clear()

    frozen_pos = Position(300, 150, 0)
    # The first steer sets _last_army_pos; subsequent ones increment _stall.
    # After _STALL_LIMIT unchanged steers the detour rotates.
    # We run enough steps to trigger the rotation and check that a non-E move appears.
    found_rotated = False
    cap = JourneyBehavior._STALL_LIMIT + 5
    for i in range(cap):
        state = _state(active=True, army_pos=frozen_pos,
                       sites=[_site(dist=40, direction="E")],
                       tick=i * 150)  # advance tick bucket to avoid watchdog
        result = b.step(state)
        if result.status is not BehaviorStatus.RUNNING:
            break
        if lua.actions and lua.actions[-1] != "A_MOVE_E":
            found_rotated = True
            break

    assert found_rotated, f"Expected a rotated (non-A_MOVE_E) move within {cap} steps; actions={lua.actions}"


def test_all_detours_exhausted_returns_needs_llm():
    """All detour headings exhausted → NEEDS_LLM 'cannot route around terrain' + travel_exit issued."""
    b, lua = _behavior()
    # Give bearing
    b.step(_state(active=False, sites=[_site(dist=40, direction="E")]))
    lua.actions.clear()

    frozen_pos = Position(300, 150, 0)
    # _STALL_LIMIT * (_MAX_DETOUR + 1) steps needed plus margin
    max_steps = JourneyBehavior._STALL_LIMIT * (JourneyBehavior._MAX_DETOUR + 1) + 10
    result = None
    for i in range(max_steps):
        state = _state(active=True, army_pos=frozen_pos,
                       sites=[_site(dist=40, direction="E")],
                       tick=i * 150)
        result = b.step(state)
        if result.status is BehaviorStatus.NEEDS_LLM:
            break
    else:
        assert False, f"Expected NEEDS_LLM within {max_steps} steps"

    assert result is not None
    assert result.status is BehaviorStatus.NEEDS_LLM
    assert "cannot route around terrain" in result.outcome
    assert "travel_exit" in lua.actions


def test_arrival_in_travel_exits_then_done():
    """Army within _STOP_DISTANCE → first step issues travel_exit + RUNNING; second step → DONE 'arrived'."""
    b, lua = _behavior()
    # Give bearing first
    b.step(_state(active=False, sites=[_site(dist=40, direction="E")]))
    lua.actions.clear()

    # Now in travel and very close (dist <= _STOP_DISTANCE)
    close_state = _state(active=True, army_pos=Position(400, 150, 0),
                         sites=[_site(dist=JourneyBehavior._STOP_DISTANCE, direction="E")])
    result = b.step(close_state)
    assert result.status is BehaviorStatus.RUNNING
    assert "travel_exit" in lua.actions

    # Next step: _arriving latch should fire → DONE
    result2 = b.step(_state(active=False, sites=[_site(dist=1, direction="E")]))
    assert result2.status is BehaviorStatus.DONE
    assert "arrived" in result2.outcome


def test_arrival_by_standing_in_named_site():
    """Not in travel and site_name == dest name → DONE 'arrived' immediately."""
    b, lua = _behavior(site_id=7, site_name="Ironhold")
    # The _arrived() check: not fast_travel_active AND state.site_name == self._site_name
    # dist=0 also triggers it; use site_name match path for this test
    state = _state(active=False, site_name="Ironhold",
                   sites=[_site(dist=0, direction="E")])
    result = b.step(state)
    assert result.status is BehaviorStatus.DONE
    assert "arrived" in result.outcome


def test_world_pos_steers_when_dest_not_in_nearby_sites():
    """A rumored target (world_pos, no NearbySite) steers by absolute bearing.

    Pre-travel player at world (100, 100); dest at (200, 100) → due East → A_MOVE_E.
    """
    lua = _FakeLua()
    ctx = _ctx(lua)
    b = JourneyBehavior(ctx, Policy(), site_id=42, site_name="Speardread",
                        world_pos=(200, 100))
    s = _state(active=False, sites=[])           # dest NOT in nearby_sites
    s.player_world_x, s.player_world_y = 100, 100
    result = b.step(s)
    assert result.status is BehaviorStatus.RUNNING
    assert lua.actions == ["travel_enter"]        # entered travel, bearing captured
    assert b._initial_bearing == "e"


def test_world_pos_bearing_from_army_during_travel():
    """During travel the army pos (3× embark coords) drives the bearing toward a
    world_pos target. Army at (300,300)//3=(100,100); dest (100,200) → due South."""
    lua = _FakeLua()
    ctx = _ctx(lua)
    b = JourneyBehavior(ctx, Policy(), site_id=42, site_name="Speardread",
                        world_pos=(100, 200))
    # In travel, army formed, dest not in nearby_sites.
    s = _state(active=True, army_pos=Position(300, 300, 0), sites=[])
    result = b.step(s)
    assert result.status is BehaviorStatus.RUNNING
    assert lua.actions[-1] == "A_MOVE_S"


def test_world_pos_arrival_by_distance():
    """Standing within _STOP_DISTANCE of the world_pos (no NearbySite) → arrival."""
    lua = _FakeLua()
    ctx = _ctx(lua)
    b = JourneyBehavior(ctx, Policy(), site_id=42, site_name="Speardread",
                        world_pos=(101, 100))
    s = _state(active=True, army_pos=Position(303, 300, 0), sites=[])  # ->(101,100)
    result = b.step(s)
    assert result.status is BehaviorStatus.RUNNING
    assert "travel_exit" in lua.actions


def test_handles_physio_defaults_false():
    """JourneyBehavior.handles_physio() returns False — it hands back for critical needs."""
    b, lua = _behavior()
    policy = Policy()
    state = _state()
    assert b.handles_physio(state, policy) is False
