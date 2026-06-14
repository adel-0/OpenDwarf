"""Unit tests for FastTravelController (ROADMAP 3.4 — fast-travel e2e).

Encodes the 2026-06-12 live finding that drove the fix: the world-travel ARMY is
created only AFTER the first travel-map move is issued (army_pos is None right
after entering travel), so the controller must MOVE to form it — the old code
waited for an army that never came and bailed every time, so fast travel never
engaged. Also covers honest bail when no army forms (genuinely obstructed) and
no-progress stall detection when straight-line steering hits a terrain barrier.
"""

from __future__ import annotations

from _fakes import SimulatedDF
from opendwarf.actions.skills import FastTravelController, SkillContext, SkillStatus
from opendwarf.state.game_state import GameState, NearbySite, Position


def _ctx(lua):
    # FastTravelController only uses ctx.lua; map handles are unused.
    return SkillContext(lua, None, None, None)


def _site(sid, name, dist, direction, stype="Town"):
    return NearbySite(id=sid, name=name, site_type=stype, distance=dist, direction=direction)


def _state(*, here="HOME", sites=None, ft=False, army=None):
    s = GameState()
    s.site_name = here
    s.nearby_sites = sites or []
    s.fast_travel_active = ft
    s.fast_travel_army_pos = army
    return s


_DEST = [_site(7, "DEST", 40, "E")]


def _enter(sk, lua):
    """Run the enter phase and clear the recorded travel_enter."""
    sk.step(_state(sites=_DEST))
    lua.actions.clear()


def test_enter_issues_travel_and_captures_direction():
    lua = SimulatedDF()
    sk = FastTravelController(_ctx(lua), site_id=7, site_name="DEST")
    r = sk.step(_state(sites=_DEST))
    assert r.status is SkillStatus.RUNNING
    assert lua.actions == ["travel_enter"]
    assert sk._initial_dir == "e"
    assert sk._phase == "travel"


def test_formation_issues_move_not_passive_wait():
    """With army_pos still None, the controller MOVES toward the target to form
    the army — it must not silently wait (the original bug)."""
    lua = SimulatedDF()
    sk = FastTravelController(_ctx(lua), site_id=7, site_name="DEST")
    _enter(sk, lua)
    r = sk.step(_state(sites=_DEST, ft=True, army=None))
    assert r.status is SkillStatus.RUNNING
    assert lua.actions == ["A_MOVE_E"]  # nudged toward the target, not a wait
    assert sk._steps == 1


def test_bail_when_army_never_forms():
    lua = SimulatedDF()
    sk = FastTravelController(_ctx(lua), site_id=7, site_name="DEST")
    _enter(sk, lua)
    st = _state(sites=_DEST, ft=True, army=None)
    res = None
    for _ in range(FastTravelController._ARMY_FORM_ATTEMPTS + 3):
        res = sk.step(st)
        if res.status is SkillStatus.INTERRUPTED:
            break
    assert res.status is SkillStatus.INTERRUPTED
    assert "never formed" in res.outcome
    assert "travel_exit" in lua.actions
    # It tried exactly _ARMY_FORM_ATTEMPTS moves before giving up.
    assert lua.actions.count("A_MOVE_E") == FastTravelController._ARMY_FORM_ATTEMPTS


def test_steers_toward_target_once_army_exists():
    lua = SimulatedDF()
    sk = FastTravelController(_ctx(lua), site_id=7, site_name="DEST")
    _enter(sk, lua)
    r = sk.step(_state(sites=_DEST, ft=True, army=Position(100, 100, 0)))
    assert r.status is SkillStatus.RUNNING
    assert lua.actions == ["A_MOVE_E"]
    assert sk._no_army_steps == 0


def test_arrival_within_stop_distance_completes():
    lua = SimulatedDF()
    sk = FastTravelController(_ctx(lua), site_id=7, site_name="DEST")
    _enter(sk, lua)
    near = [_site(7, "DEST", 1, "E")]  # within _STOP_DISTANCE
    army = Position(100, 100, 0)
    sk.step(_state(sites=near, ft=True, army=army))   # arrival -> phase=exit
    assert sk._phase == "exit"
    sk.step(_state(sites=near, ft=True, army=army))   # issues travel_exit -> done
    assert "travel_exit" in lua.actions
    r = sk.step(_state(sites=near, ft=True, army=army))
    assert r.status is SkillStatus.DONE
    assert "DEST" in r.outcome


def test_stall_detection_hands_back():
    """Army exists but army_pos never changes (terrain barrier) -> the controller
    stops after _STALL_LIMIT no-progress steps instead of burning the budget."""
    lua = SimulatedDF()
    sk = FastTravelController(_ctx(lua), site_id=7, site_name="DEST")
    _enter(sk, lua)
    st = _state(sites=_DEST, ft=True, army=Position(50, 50, 0))  # never moves
    res = None
    for _ in range(FastTravelController._STALL_LIMIT + 5):
        res = sk.step(st)
        if res.status is SkillStatus.INTERRUPTED:
            break
    assert res.status is SkillStatus.INTERRUPTED
    assert "stalled" in res.outcome
    assert "DEST" in res.outcome


def test_progress_resets_stall_counter():
    """Advancing army_pos resets the stall counter so a long-but-moving journey
    is not killed by the stall guard."""
    lua = SimulatedDF()
    sk = FastTravelController(_ctx(lua), site_id=7, site_name="DEST")
    _enter(sk, lua)
    for i in range(FastTravelController._STALL_LIMIT + 4):
        # army advances east every step
        st = _state(sites=_DEST, ft=True, army=Position(50 + i, 50, 0))
        r = sk.step(st)
        assert r.status is SkillStatus.RUNNING
    assert sk._no_progress_steps == 0
