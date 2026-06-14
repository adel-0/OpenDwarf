"""Unit tests for CombatStrikeSkill (NORTHSTAR M2 / ROADMAP 2.1 attack depth).

Drives the mouse-only adventure attack menu by watching `attack_menu_mode`:
target → Strike → body part → weapon, one click per mode transition. The mode
progression and clickable rows are LIVE-VERIFIED v0.53.14; these tests pin the
deterministic state-machine logic against that observed protocol."""

from __future__ import annotations

from _fakes import SimulatedDF
from opendwarf.actions.skills import CombatStrikeSkill, SkillContext, SkillStatus
from opendwarf.state.game_state import GameState


def _ctx(lua):
    return SkillContext(lua, None, None, None)


def _state(*, focus="dungeonmode/Attack", open=True, mode=0, unit_choice=(7,)):
    s = GameState()
    s.focus_state = focus
    s.attack_menu_open = open
    s.attack_menu_mode = mode
    s.attack_unit_choice = list(unit_choice)
    return s


def test_full_strike_sequence():
    lua = SimulatedDF()
    sk = CombatStrikeSkill(_ctx(lua), unit_id=7, target_name="wolf")
    # 1. opens the menu
    assert sk.step(_state(focus="dungeonmode/Default", open=False, mode=-1)).status is SkillStatus.RUNNING
    # 2. mode 0 → pick target (index 0, since unit_choice=[7])
    assert sk.step(_state(mode=0)).status is SkillStatus.RUNNING
    # lag tick: same mode, no new action
    assert sk.step(_state(mode=0)).status is SkillStatus.RUNNING
    # 3. mode 2 → Strike
    assert sk.step(_state(mode=2)).status is SkillStatus.RUNNING
    # 4. mode 3 → body part
    assert sk.step(_state(mode=3)).status is SkillStatus.RUNNING
    # 5. mode 4 → weapon (resolves)
    assert sk.step(_state(mode=4)).status is SkillStatus.RUNNING
    # 6. menu closed → DONE
    res = sk.step(_state(open=False, mode=4))
    assert res.status is SkillStatus.DONE and "wolf" in res.outcome
    assert lua.actions == [
        "press:A_ATTACK", "attack_pick:0", "attack_strike", "attack_pick:0", "attack_pick:0",
    ]


def test_target_index_maps_to_unit_choice_row():
    lua = SimulatedDF()
    sk = CombatStrikeSkill(_ctx(lua), unit_id=7, target_name="wolf")
    sk.step(_state(focus="dungeonmode/Default", open=False, mode=-1))  # open
    # target 7 is the SECOND row in unit_choice → attack_pick:1
    sk.step(_state(mode=0, unit_choice=(9999, 7)))
    assert lua.actions[-1] == "attack_pick:1"


def test_unknown_target_defaults_to_first_row():
    lua = SimulatedDF()
    sk = CombatStrikeSkill(_ctx(lua), unit_id=12345, target_name="wolf")
    sk.step(_state(focus="dungeonmode/Default", open=False, mode=-1))
    sk.step(_state(mode=0, unit_choice=(7, 8)))  # 12345 not listed
    assert lua.actions[-1] == "attack_pick:0"


def test_help_overlay_dismissed_via_clickok():
    lua = SimulatedDF()
    sk = CombatStrikeSkill(_ctx(lua), unit_id=7, target_name="wolf")
    sk.step(_state(focus="dungeonmode/Default", open=False, mode=-1))  # press A_ATTACK
    # The first A_ATTACK stacks a Help overlay — the skill clears it itself.
    res = sk.step(_state(focus="dungeonmode/Help", open=True, mode=0))
    assert res.status is SkillStatus.RUNNING
    assert lua.scripts == ["opendwarf--clickok"]
    assert lua.actions == ["press:A_ATTACK"]  # no menu click while Help is up


def test_stuck_menu_backs_out():
    lua = SimulatedDF()
    sk = CombatStrikeSkill(_ctx(lua), unit_id=7, target_name="wolf")
    sk.step(_state(focus="dungeonmode/Default", open=False, mode=-1))
    sk.step(_state(mode=0))  # first action at mode 0
    # mode never advances past 0 — eventually bail out cleanly
    res = SkillStatus.RUNNING
    for _ in range(CombatStrikeSkill._MAX_WAIT + 2):
        r = sk.step(_state(mode=0))
        res = r.status
    assert res is SkillStatus.INTERRUPTED
    assert "press:LEAVESCREEN" in lua.actions
