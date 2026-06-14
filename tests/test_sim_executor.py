"""Round-trip tests for component 3a: SimWorld + SimulatedLuaExecutor.

These tests verify that:
  - ``SimulatedLuaExecutor.extract_state()`` produces a dict that
    ``GameState.from_raw()`` accepts without error.
  - The parsed ``GameState`` carries the correct values for the wolf-survival
    scenario.
  - Mutating ``SimWorld`` fields is immediately reflected in the next
    ``extract_state()`` call (live-state semantics, not a snapshot).
  - The ``execute_action`` stub contract (records + returns []) holds.

Note: tests/ is NOT a package; pytest prepend-mode puts both tests/ and the
repo root on sys.path, so both ``from opendwarf.sim import …`` and
``from opendwarf.state.game_state import GameState`` work without any
``__init__.py`` in tests/.
"""

from opendwarf.sim import SimulatedLuaExecutor, SimWorld
from opendwarf.state.game_state import GameState, Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _round_trip(world: SimWorld) -> GameState:
    """Wrap *world* in an executor, extract, parse, return GameState."""
    executor = SimulatedLuaExecutor(world)
    raw = executor.extract_state()
    return GameState.from_raw(raw)


# ---------------------------------------------------------------------------
# Test 1 — wolf survival scenario baseline
# ---------------------------------------------------------------------------


class TestWolfSurvivalBaseline:
    """Verify the canonical wolf-survival scenario from SimWorld.wolf_survival()."""

    def setup_method(self):
        self.world = SimWorld.wolf_survival()
        self.state = _round_trip(self.world)

    def test_taking_input(self):
        assert self.state.taking_input is True

    def test_is_adventure_mode(self):
        assert self.state.is_adventure_mode is True

    def test_health_pct_full(self):
        assert self.state.health_pct == 100

    def test_adventurer_position(self):
        assert self.state.adventurer_position == Position(50, 50, 10)

    def test_exactly_one_nearby_unit(self):
        assert len(self.state.nearby_units) == 1

    def test_wolf_is_the_unit(self):
        wolf = self.state.nearby_units[0]
        assert wolf.id == 1001
        assert wolf.name == "Wolf"
        assert wolf.race == "WOLF"

    def test_wolf_not_hostile(self):
        """Wild wolf must NOT appear in hostile_units (isDanger=false in real DF)."""
        assert self.state.hostile_units == []

    def test_wolf_in_huntable_units(self):
        """Wild wolf must appear in huntable_units (non-historic, not tame, not citizen)."""
        huntable_ids = [u.id for u in self.state.huntable_units]
        assert huntable_ids == [1001]

    def test_wolf_distance(self):
        """Wolf is at (52,50,10); adventurer at (50,50,10) → Manhattan distance = 2."""
        wolf = self.state.nearby_units[0]
        assert wolf.distance == 2

    def test_focus_state(self):
        assert self.state.focus_state == "dungeonmode/Default"

    def test_site_name_empty(self):
        """Wolf-survival scenario is in open wilderness — no site."""
        assert self.state.site_name == ""

    def test_adventurer_not_dead(self):
        assert self.state.adventurer_dead is False

    def test_no_inventory(self):
        assert self.state.inventory == []

    def test_no_wounds(self):
        assert self.state.wounds == []


# ---------------------------------------------------------------------------
# Test 2 — live-state semantics: mutations are reflected immediately
# ---------------------------------------------------------------------------


class TestLiveStateMutation:
    """Verify that extract_state reads live world state, not a snapshot."""

    def test_position_mutation(self):
        world = SimWorld.wolf_survival()
        executor = SimulatedLuaExecutor(world)

        # Move adventurer one step east (now wolf is 1 tile east instead of 2)
        world.pos = (51, 50, 10)

        state = GameState.from_raw(executor.extract_state())
        assert state.adventurer_position == Position(51, 50, 10)
        # Wolf at (52,50,10), adventurer at (51,50,10) → distance = 1
        assert state.nearby_units[0].distance == 1

    def test_blood_count_mutation(self):
        world = SimWorld.wolf_survival()
        executor = SimulatedLuaExecutor(world)

        world.blood_count = 50  # half health

        state = GameState.from_raw(executor.extract_state())
        assert state.health_pct == 50

    def test_both_mutations_together(self):
        world = SimWorld.wolf_survival()
        executor = SimulatedLuaExecutor(world)

        world.pos = (60, 60, 10)
        world.blood_count = 25

        state = GameState.from_raw(executor.extract_state())
        assert state.adventurer_position == Position(60, 60, 10)
        assert state.health_pct == 25
        # Wolf still at (52,50,10); distance from (60,60,10) = |60-52|+|60-50|+0 = 18
        wolf = state.nearby_units[0]
        assert wolf.distance == 18

    def test_distance_recomputed_on_each_extract(self):
        """Each extract_state call must recompute distance from current pos."""
        world = SimWorld.wolf_survival()
        executor = SimulatedLuaExecutor(world)

        # First extract — adventurer at (50,50,10)
        s1 = GameState.from_raw(executor.extract_state())
        assert s1.nearby_units[0].distance == 2

        # Move closer and re-extract
        world.pos = (51, 50, 10)
        s2 = GameState.from_raw(executor.extract_state())
        assert s2.nearby_units[0].distance == 1

        # Move to wolf's tile — distance = 0
        world.pos = (52, 50, 10)
        s3 = GameState.from_raw(executor.extract_state())
        assert s3.nearby_units[0].distance == 0


# ---------------------------------------------------------------------------
# Test 3 — execute_action stub contract
# ---------------------------------------------------------------------------


class TestExecuteActionStub:
    """Verify the stub behaviour promised to component 3b callers."""

    def test_returns_empty_list(self):
        world = SimWorld.wolf_survival()
        executor = SimulatedLuaExecutor(world)
        result = executor.execute_action("A_MOVE_E")
        assert result == []

    def test_action_recorded(self):
        world = SimWorld.wolf_survival()
        executor = SimulatedLuaExecutor(world)
        executor.execute_action("A_MOVE_E")
        assert "A_MOVE_E" in executor.actions

    def test_multiple_actions_accumulate(self):
        world = SimWorld.wolf_survival()
        executor = SimulatedLuaExecutor(world)
        for key in ["A_MOVE_N", "A_MOVE_S", "A_ATTACK"]:
            executor.execute_action(key)
        assert executor.actions == ["A_MOVE_N", "A_MOVE_S", "A_ATTACK"]

    def test_world_mutated_by_move(self):
        """Component 3b: A_MOVE_E on an empty tile advances the adventurer east.

        (Wolf is at (52,50,10); the intermediate tile (51,50,10) is empty, so
        the adventurer moves there and total_move increments.)
        """
        world = SimWorld.wolf_survival()
        executor = SimulatedLuaExecutor(world)
        executor.execute_action("A_MOVE_E")
        assert world.pos == (51, 50, 10)
        assert world.total_move > 0


# ---------------------------------------------------------------------------
# Test 4 — extract_screen_context is an alias for extract_state
# ---------------------------------------------------------------------------


def test_extract_screen_context_alias():
    world = SimWorld.wolf_survival()
    executor = SimulatedLuaExecutor(world)
    assert executor.extract_screen_context() == executor.extract_state()


# ---------------------------------------------------------------------------
# Test 5 — attack_menu serialization
# ---------------------------------------------------------------------------


def test_attack_menu_serialization():
    world = SimWorld.wolf_survival()
    world.attack_menu_open = True
    world.attack_menu_mode = 0
    world.attack_unit_choice = [1001]

    state = _round_trip(world)
    assert state.attack_menu_open is True
    assert state.attack_menu_mode == 0
    assert state.attack_unit_choice == [1001]


# ---------------------------------------------------------------------------
# Test 6 — fast travel serialization
# ---------------------------------------------------------------------------


def test_fast_travel_active():
    world = SimWorld.wolf_survival()
    world.fast_travel_active = True
    world.fast_travel_army_pos = (10, 20, 0)

    state = _round_trip(world)
    assert state.fast_travel_active is True
    assert state.fast_travel_army_pos == Position(10, 20, 0)


def test_fast_travel_inactive():
    world = SimWorld.wolf_survival()
    assert world.fast_travel_active is False
    assert world.fast_travel_army_pos is None

    state = _round_trip(world)
    assert state.fast_travel_active is False
    assert state.fast_travel_army_pos is None


# ---------------------------------------------------------------------------
# Test 7 — physiological timers pass through
# ---------------------------------------------------------------------------


def test_physiological_timers():
    world = SimWorld.wolf_survival()
    world.hunger_timer = 80_000
    world.thirst_timer = 55_000
    world.sleepiness_timer = 60_000
    world.exhaustion = 1500

    state = _round_trip(world)
    assert state.hunger_timer == 80_000
    assert state.thirst_timer == 55_000
    assert state.sleepiness_timer == 60_000
    assert state.exhaustion == 1500
    # These thresholds are defined in GameState
    assert state.hungry is True
    assert state.thirsty is True


# ---------------------------------------------------------------------------
# Test 8 — skills and wounds pass through
# ---------------------------------------------------------------------------


def test_skills_pass_through():
    world = SimWorld.wolf_survival()
    world.skills = [{"id": "SWORD", "level": 3, "experience": 500}]

    state = _round_trip(world)
    assert len(state.skills) == 1
    assert state.skills[0].id == "SWORD"
    assert state.skills[0].level == 3
    assert state.skills[0].experience == 500


def test_wounds_pass_through():
    world = SimWorld.wolf_survival()
    world.wounds = [{"part": "right arm", "status": "broken"}]

    state = _round_trip(world)
    assert len(state.wounds) == 1
    assert state.wounds[0].part == "right arm"
    assert state.wounds[0].status == "broken"


# ---------------------------------------------------------------------------
# Test 9 — adventurer_dead flag
# ---------------------------------------------------------------------------


def test_adventurer_dead_flag():
    world = SimWorld.wolf_survival()
    world.adventurer_dead = True

    state = _round_trip(world)
    assert state.adventurer_dead is True
