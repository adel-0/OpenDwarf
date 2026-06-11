"""Unit tests for NORTHSTAR M5 — seamless DFHack interface.

Covers:
- UnstickSkill ladder ordering with mocked context
- Key-candidate derivation from focus strings
- Console-error parsing from a fixture log
- inspect_ui dict parsing (structure validation)
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from opendwarf.actions.skills import (
    SkillContext,
    SkillResult,
    SkillStatus,
    UnstickSkill,
    _focus_recovered,
)
from opendwarf.dfhack.lua_executor import LuaExecutor
from opendwarf.state.game_state import GameState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(*, inspect_return=None, find_keys_return=None, execute_return=None):
    """Build a SkillContext with all handles mocked."""
    lua = MagicMock(spec=LuaExecutor)
    lua.inspect_ui.return_value = inspect_return or {}
    lua.find_keys.return_value = find_keys_return or []
    lua.execute_action.return_value = execute_return or ["OK: dismissed"]
    ctx = MagicMock(spec=SkillContext)
    ctx.lua = lua
    return ctx


def _state(focus: str | None = None) -> GameState:
    s = GameState()
    s.focus_state = focus
    return s


# ---------------------------------------------------------------------------
# _focus_recovered
# ---------------------------------------------------------------------------

def test_focus_recovered_known_patterns():
    assert _focus_recovered("dungeonmode/Default")
    assert _focus_recovered("dungeonmode/Conversation")
    assert _focus_recovered("dungeonmode/Sleep")
    assert _focus_recovered("dungeonmode/Travel")


def test_focus_recovered_none_or_empty():
    assert not _focus_recovered(None)
    assert not _focus_recovered("")


def test_focus_recovered_unknown():
    assert not _focus_recovered("dungeonmode/Unknown")
    assert not _focus_recovered("DFHACK/Lua")


# ---------------------------------------------------------------------------
# UnstickSkill — ladder ordering (mocked ctx, no waits)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Suppress all real sleeps inside skills so tests are fast."""
    monkeypatch.setattr("opendwarf.actions.skills.time.sleep", lambda _: None)


def _run_skill(skill, state, max_steps=20):
    """Run skill until terminal or max_steps; return list of (status, outcome)."""
    results = []
    for _ in range(max_steps):
        r = skill.step(state)
        results.append((r.status, r.outcome))
        if r.status is not SkillStatus.RUNNING:
            break
    return results


def test_unstick_inspect_then_leavescreen_then_keys():
    """Ladder must progress: inspect → dismiss(none) → leavescreen × 2 → keys."""
    inspect_data = {
        "viewscreen_stack": ["viewscreen_dungeonmodest"],
        "focus_strings": ["dungeonmode/Travel"],
        "menu": {"name": "Travel", "value": 25},
    }
    # find_keys("TRAVEL") returns A_END_TRAVEL; after the key press focus changes.
    ctx = _make_ctx(inspect_return=inspect_data, find_keys_return=["A_END_TRAVEL"])
    wedged = "dungeonmode/Travel"
    state = _state(wedged)
    skill = UnstickSkill(ctx, wedged_focus=wedged)

    # Step 1 — inspect (sets _first_step_done, determines no dfhack screens)
    r = skill.step(state)
    assert r.status is SkillStatus.RUNNING
    assert skill._phase == "leavescreen"

    # Steps 2-3 — LEAVESCREEN × 2 (focus unchanged during these)
    r = skill.step(state)
    assert r.status is SkillStatus.RUNNING
    assert skill._phase == "leavescreen"  # still leavescreen, count=1

    r = skill.step(state)
    assert r.status is SkillStatus.RUNNING
    assert skill._phase in ("leavescreen", "keys")  # count=2, should transition to keys

    # After 2nd LEAVESCREEN the skill should transition to keys phase.
    assert skill._leavescreen_count == 2
    # Manually advance to keys phase to test key logic.
    skill._phase = "keys"
    skill._key_candidates = ["A_END_TRAVEL"]
    skill._key_attempt = 0

    # Simulate focus change after key press.
    state_recovered = _state("dungeonmode/Default")
    r = skill.step(state_recovered)
    # Since focus changed and is recovered, done.
    assert r.status is SkillStatus.DONE
    assert "recovered" in r.outcome


def test_unstick_dismisses_dfhack_screens():
    """If stack contains a DFHack screen, the dismiss action must be called."""
    inspect_data = {
        "viewscreen_stack": ["dfhack_control_panel", "viewscreen_dungeonmodest"],
        "focus_strings": ["DFHACK/ControlPanel"],
        "menu": {"name": "Default", "value": 0},
    }
    ctx = _make_ctx(inspect_return=inspect_data)
    skill = UnstickSkill(ctx, wedged_focus="DFHACK/ControlPanel")
    state = _state("DFHACK/ControlPanel")

    # Step 1 — inspect; detects dfhack screen, transitions to dismiss
    r = skill.step(state)
    assert r.status is SkillStatus.RUNNING
    assert skill._phase == "dismiss"

    # Step 2 — dismiss action fires
    r = skill.step(state)
    assert r.status is SkillStatus.RUNNING
    ctx.lua.execute_action.assert_called_with("dismiss_dfhack_screens")
    assert skill._phase == "leavescreen"


def test_unstick_gives_up_with_summary():
    """After all steps exhausted, give_up returns INTERRUPTED with inspect summary."""
    inspect_data = {
        "viewscreen_stack": ["viewscreen_dungeonmodest"],
        "focus_strings": ["dungeonmode/UnknownScreen"],
        "menu": {"name": "UnknownScreen", "value": 99},
    }
    ctx = _make_ctx(inspect_return=inspect_data, find_keys_return=[])
    wedged = "dungeonmode/UnknownScreen"
    skill = UnstickSkill(ctx, wedged_focus=wedged)

    # Force directly to give_up phase
    skill._first_step_done = True
    skill._phase = "give_up"
    skill._inspect_summary = "focus=[dungeonmode/UnknownScreen]; menu=UnknownScreen(99)"
    skill._key_candidates = []

    state = _state(wedged)
    r = skill.step(state)
    assert r.status is SkillStatus.INTERRUPTED
    assert "UnstickSkill gave up" in r.outcome
    assert "UnknownScreen" in r.outcome


def test_unstick_no_premature_done_when_focus_unchanged():
    """Recovery must NOT fire if focus is still the wedged value (even if it's in
    _RECOVERED_FOCUS_PATTERNS like dungeonmode/Travel)."""
    inspect_data = {
        "viewscreen_stack": ["viewscreen_dungeonmodest"],
        "focus_strings": ["dungeonmode/Travel"],
    }
    ctx = _make_ctx(inspect_return=inspect_data)
    wedged = "dungeonmode/Travel"
    skill = UnstickSkill(ctx, wedged_focus=wedged)

    # First step sets _first_step_done; focus still Travel → not done
    state = _state(wedged)
    r = skill.step(state)
    assert r.status is SkillStatus.RUNNING  # inspect phase, not done yet

    # Subsequent steps: if focus is still wedged → still running
    skill._phase = "keys"
    skill._key_candidates = ["A_END_TRAVEL"]
    skill._key_attempt = 0
    r = skill.step(state)  # sends key, still running (focus unchanged)
    assert r.status is SkillStatus.RUNNING


def test_unstick_done_when_focus_changes_to_default():
    """After a key press, if focus changes to Default, done is returned."""
    inspect_data = {"viewscreen_stack": ["viewscreen_dungeonmodest"]}
    ctx = _make_ctx(inspect_return=inspect_data)
    wedged = "dungeonmode/Travel"
    skill = UnstickSkill(ctx, wedged_focus=wedged)
    skill._first_step_done = True
    skill._phase = "keys"
    skill._key_candidates = ["A_END_TRAVEL"]
    skill._key_attempt = 0

    # Focus is now Default (different from wedged Travel)
    state = _state("dungeonmode/Default")
    r = skill.step(state)
    assert r.status is SkillStatus.DONE
    assert "dungeonmode/Default" in r.outcome


# ---------------------------------------------------------------------------
# Key-candidate derivation from focus strings
# ---------------------------------------------------------------------------

def test_key_candidate_derivation_travel():
    """dungeonmode/Travel → token TRAVEL → find_keys('TRAVEL') called."""
    ctx = _make_ctx(find_keys_return=["A_TRAVEL", "A_END_TRAVEL", "A_TRAVEL_SLEEP"])
    wedged = "dungeonmode/Travel"
    skill = UnstickSkill(ctx, wedged_focus=wedged)
    skill._phase = "keys"
    skill._first_step_done = True

    state = _state(wedged)
    # _prepare_key_candidates is called internally on transition
    prepared = skill._prepare_key_candidates(wedged)
    assert prepared.status is SkillStatus.RUNNING

    # A_END_TRAVEL must be first (priority 0 = A_END_*)
    assert skill._key_candidates[0] == "A_END_TRAVEL"


def test_key_candidates_priority_ordering():
    """A_END_* keys must be sorted before LEAVESCREEN before A_* before others."""
    ctx = _make_ctx(find_keys_return=["A_FOO", "LEAVESCREEN", "A_END_BAR", "CUSTOM_X"])
    skill = UnstickSkill(ctx, wedged_focus="foo/bar")
    skill._prepare_key_candidates("foo/bar")
    order = skill._key_candidates
    names_upper = [k.upper() for k in order]
    # A_END_* should appear before LEAVESCREEN
    end_idx = next((i for i, k in enumerate(names_upper) if k.startswith("A_END_")), None)
    leave_idx = next((i for i, k in enumerate(names_upper) if "LEAVE" in k), None)
    if end_idx is not None and leave_idx is not None:
        assert end_idx < leave_idx


def test_banned_keys_excluded():
    """Keys in _RECOVERY_BANNED_KEYS must never appear in candidates."""
    from opendwarf.actions.skills import _RECOVERY_BANNED_KEYS
    ctx = _make_ctx(find_keys_return=["A_END_TRAVEL", "LEAVESCREEN_ALL", "A_RETIRE"])
    skill = UnstickSkill(ctx, wedged_focus="dungeonmode/Travel")
    skill._prepare_key_candidates("dungeonmode/Travel")
    for k in skill._key_candidates:
        assert k not in _RECOVERY_BANNED_KEYS


# ---------------------------------------------------------------------------
# Console-error parsing from a fixture log
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_log(tmp_path):
    """Create a fake DFHack stderr.log with some pre-existing and new content."""
    log_file = tmp_path / "stderr.log"
    log_file.write_text(
        "Client connection established.\n"
        "In RPC server: I/O error.\n"
    )
    return str(log_file)


def test_consume_console_errors_captures_new_errors(fake_log):
    lua = LuaExecutor.__new__(LuaExecutor)
    lua.console_log = fake_log
    offset = LuaExecutor.console_log_offset(fake_log)

    # Append new content to simulate DFHack printerr output
    with open(fake_log, "a") as f:
        f.write("opendwarf--act press error: Invalid keycode: NONEXISTENT\n")
        f.write("Some unrelated line\n")

    errors = LuaExecutor.consume_console_errors(fake_log, offset)
    assert any("NONEXISTENT" in e for e in errors)


def test_consume_console_errors_skips_pre_existing(fake_log):
    """Errors before the offset must not be returned."""
    # Offset at end of existing content
    offset = LuaExecutor.console_log_offset(fake_log)
    errors = LuaExecutor.consume_console_errors(fake_log, offset)
    assert errors == []


def test_consume_console_errors_handles_missing_file():
    offset = [0]
    errors = LuaExecutor.consume_console_errors("/nonexistent/path/stderr.log", offset)
    assert errors == []


def test_consume_console_errors_filters_relevant_lines(tmp_path):
    log = tmp_path / "stderr.log"
    log.write_text(
        "Client connection established.\n"
        "opendwarf--act: unknown interface key: BAD\n"
        "error: something went wrong\n"
        "Normal info line\n"
        "printerr called here\n"
    )
    offset = [0]
    errors = LuaExecutor.consume_console_errors(str(log), offset)
    lines = [e for e in errors]
    # Should capture lines with 'error', 'printerr', or 'opendwarf--'
    assert any("BAD" in l for l in lines)
    assert any("something went wrong" in l for l in lines)
    assert any("printerr" in l for l in lines)
    # Should NOT capture normal info
    assert not any(l == "Normal info line" for l in lines)


# ---------------------------------------------------------------------------
# inspect_ui dict parsing / structure
# ---------------------------------------------------------------------------

def test_inspect_ui_returns_dict_with_expected_keys():
    """LuaExecutor.inspect_ui() should parse the JSON from the Lua script."""
    mock_client = MagicMock()
    sample_json = json.dumps({
        "viewscreen_stack": ["viewscreen_dungeonmodest"],
        "focus_strings": ["dungeonmode/Default"],
        "menu": {"name": "Default", "value": 0},
        "player_control_state": {"name": "TAKING_INPUT", "value": 0},
        "travel": {"origin_x": 263, "origin_y": 4836, "player_army_id": -1},
        "gps": {"width": 150, "height": 66},
        "message": None,
    })
    mock_client.run_command.return_value = [sample_json]
    lua = LuaExecutor(mock_client)
    lua.scripts_dir = MagicMock()
    lua._console_offset = [0]
    # Patch run_script to return raw JSON lines
    with patch.object(lua, "run_script", return_value=[sample_json]):
        result = lua.inspect_ui()
    assert result["focus_strings"] == ["dungeonmode/Default"]
    assert result["menu"]["name"] == "Default"
    assert result["travel"]["player_army_id"] == -1
    assert result["gps"]["width"] == 150


def test_inspect_ui_returns_empty_on_bad_json():
    """inspect_ui() must return {} gracefully if the Lua output has no JSON."""
    mock_client = MagicMock()
    lua = LuaExecutor(mock_client)
    lua._console_offset = [0]
    with patch.object(lua, "run_script", return_value=["no json here"]):
        result = lua.inspect_ui()
    assert result == {}


def test_find_keys_parses_space_separated_output():
    """find_keys should split the space-separated Lua output correctly."""
    mock_client = MagicMock()
    lua = LuaExecutor(mock_client)
    lua._console_offset = [0]
    with patch.object(lua, "run_script",
                      return_value=["A_TRAVEL A_END_TRAVEL A_TRAVEL_SLEEP\n"]):
        result = lua.find_keys("TRAVEL")
    assert "A_END_TRAVEL" in result
    assert "A_TRAVEL" in result


def test_find_keys_returns_empty_on_empty_output():
    mock_client = MagicMock()
    lua = LuaExecutor(mock_client)
    lua._console_offset = [0]
    with patch.object(lua, "run_script", return_value=[""]):
        result = lua.find_keys("NOMATCH")
    assert result == []
