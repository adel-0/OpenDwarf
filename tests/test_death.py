"""Unit tests for M2-tail death detection and postmortem wiring.

Covers:
  - GameState.adventurer_dead: synthetic from_raw() fixtures (flags, nil adv, focus)
  - _build_death_cause: various cause combinations
  - handle_death: mock sequence (postmortem, reflection, behavior note, archival, log event)
"""

from __future__ import annotations

import json
import io
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile
import shutil

import pytest

from opendwarf.agent.death_handler import handle_death
from opendwarf.agent.loop import _build_death_cause
from opendwarf.state.game_state import GameState, Position, UnitInfo, Wound


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _base_raw() -> dict:
    """Minimal raw state dict that represents a living adventurer."""
    return {
        "game": {
            "is_adventure_mode": True,
            "player_control_state": "TAKING_INPUT",
            "tick_counter": 1000,
            "focus_state": "dungeonmode/Default",
            "menu_state": "Default",
            "total_move": 5,
            "message": "",
        },
        "adventurer": {
            "name": "Test Dwarf",
            "position": {"x": 10, "y": 10, "z": 100},
            "blood_count": 4980,
            "blood_max": 4980,
            "hunger_timer": 0,
            "thirst_timer": 0,
            "sleepiness_timer": 0,
            "exhaustion": 0,
            "skills": [],
            "wounds": [],
        },
        "adventurer_dead": False,
        "fast_travel": {"active": False},
        "nearby_units": [],
        "inventory": [],
        "floor_items": [],
        "map_tiles": [],
        "party": [],
        "showing_announcements": False,
        "announcement_text": [],
        "in_combat": False,
        "combat_log": [],
        "conversation_phase": "none",
        "conversation_choices": [],
        "adventurer_entities": [],
        "npc_relationships": [],
        "quests": [],
        "nearby_sites": [],
        "world": {"world_name": "Test World", "region_name": "Forest", "site_name": "", "site_type": ""},
    }


def _state_with_hostile(race: str = "WOLF") -> GameState:
    s = GameState()
    s.adventurer_position = Position(0, 0, 0)
    u = UnitInfo(id=1, name=race.title(), race=race, position=Position(1, 1, 0),
                 is_hostile=True, distance=2)
    s.nearby_units.append(u)
    s.hostile_units.append(u)
    return s


def _state_with_wounds(*statuses: str) -> GameState:
    s = GameState()
    s.adventurer_position = Position(0, 0, 0)
    for i, status in enumerate(statuses):
        s.wounds.append(Wound(part=f"part_{i}", status=status))
    return s


# ----------------------------------------------------------------------
# GameState.adventurer_dead: from_raw() detection
# ----------------------------------------------------------------------

class TestAdventurerDeadFlag:
    def test_alive_adventurer_not_dead(self):
        raw = _base_raw()
        state = GameState.from_raw(raw)
        assert not state.adventurer_dead

    def test_explicit_dead_flag_in_raw(self):
        raw = _base_raw()
        raw["adventurer_dead"] = True
        state = GameState.from_raw(raw)
        assert state.adventurer_dead

    def test_death_focus_pattern_dungeonmode_end(self):
        raw = _base_raw()
        raw["game"]["focus_state"] = "dungeonmode/end"
        state = GameState.from_raw(raw)
        assert state.adventurer_dead

    def test_death_focus_pattern_adventure_over(self):
        raw = _base_raw()
        raw["game"]["focus_state"] = "adventure_over/something"
        state = GameState.from_raw(raw)
        assert state.adventurer_dead

    def test_normal_focus_not_dead(self):
        raw = _base_raw()
        raw["game"]["focus_state"] = "dungeonmode/Default"
        state = GameState.from_raw(raw)
        assert not state.adventurer_dead

    def test_title_focus_alone_not_dead(self):
        # "title" focus can appear on the main menu even before/after adventure;
        # it is NOT enough on its own — the Lua extractor's adventurer_dead=True
        # flag is what catches game-over. Without the flag, title focus is normal.
        raw = _base_raw()
        raw["game"]["focus_state"] = "title/start"
        state = GameState.from_raw(raw)
        assert not state.adventurer_dead

    def test_nil_adventurer_not_fast_travel_sets_dead(self):
        # Simulated: Lua returns adventurer_dead=True when adv=nil and not fast travel
        raw = _base_raw()
        raw["adventurer"] = []  # Lua empty table → list
        raw["adventurer_dead"] = True
        raw["fast_travel"] = {"active": False}
        state = GameState.from_raw(raw)
        assert state.adventurer_dead

    def test_nil_adventurer_during_fast_travel_not_dead(self):
        # During fast travel, getAdventurer() returns nil — NOT death.
        raw = _base_raw()
        raw["adventurer"] = []
        raw["adventurer_dead"] = False  # Lua would NOT set dead=True during fast travel
        raw["fast_travel"] = {"active": True}
        state = GameState.from_raw(raw)
        assert not state.adventurer_dead


# ----------------------------------------------------------------------
# _build_death_cause
# ----------------------------------------------------------------------

class TestBuildDeathCause:
    def test_hostile_cause(self):
        s = _state_with_hostile("WOLF")
        cause = _build_death_cause(s)
        assert "WOLF" in cause
        assert "killed" in cause

    def test_wound_cause(self):
        s = _state_with_wounds("severed", "bleeding")
        cause = _build_death_cause(s)
        assert "wound" in cause.lower()

    def test_starvation_cause(self):
        s = GameState()
        s.adventurer_position = Position(0, 0, 0)
        s.hunger_timer = s._HUNGRY_CRITICAL + 1
        cause = _build_death_cause(s)
        assert "starvation" in cause

    def test_dehydration_cause(self):
        s = GameState()
        s.adventurer_position = Position(0, 0, 0)
        s.thirst_timer = s._THIRSTY_CRITICAL + 1
        cause = _build_death_cause(s)
        assert "dehydration" in cause

    def test_unknown_cause_fallback(self):
        s = GameState()
        s.adventurer_position = Position(0, 0, 0)
        cause = _build_death_cause(s)
        assert "unknown" in cause

    def test_multiple_causes_joined(self):
        s = _state_with_hostile("GOBLIN")
        s.wounds.append(Wound(part="head", status="bleeding"))
        cause = _build_death_cause(s)
        assert ";" in cause  # multiple parts joined


# ----------------------------------------------------------------------
# handle_death: mock-based sequence tests
# ----------------------------------------------------------------------

class TestHandleDeath:
    def _make_log_file(self):
        return io.StringIO()

    def _make_state(self):
        raw = _base_raw()
        s = GameState.from_raw(raw)
        s.adventurer_dead = True
        return s

    def test_postmortem_called(self):
        pm = MagicMock()
        llm = MagicMock()
        state = self._make_state()
        handle_death(
            state=state, cause="test death",
            llm=llm, postmortem_buffer=pm,
            reflection_engine=None, memory_writer=None,
            active_behavior=None, suspended_behavior=None,
            log_file=self._make_log_file(), turn_count=42,
            session_log_dir=None,
        )
        pm.generate_and_append.assert_called_once()
        call_kwargs = pm.generate_and_append.call_args
        assert call_kwargs.kwargs["cause"] == "test death"
        assert call_kwargs.kwargs["llm"] is llm

    def test_reflection_flushed(self):
        engine = MagicMock()
        engine.reflect.return_value = []
        state = self._make_state()
        handle_death(
            state=state, cause="death",
            llm=MagicMock(), postmortem_buffer=None,
            reflection_engine=engine, memory_writer=None,
            active_behavior=None, suspended_behavior=None,
            log_file=self._make_log_file(), turn_count=10,
            session_log_dir=None,
        )
        engine.reflect.assert_called_once_with(state)

    def test_behavior_digest_written(self):
        from opendwarf.behaviors.digest import EventDigest

        writer = MagicMock()
        behavior = MagicMock()
        digest = EventDigest()
        digest.add("killed bandit")
        behavior.digest = digest
        behavior.name = "grind_combat"

        state = self._make_state()
        handle_death(
            state=state, cause="death",
            llm=MagicMock(), postmortem_buffer=None,
            reflection_engine=None, memory_writer=writer,
            active_behavior=behavior, suspended_behavior=None,
            log_file=self._make_log_file(), turn_count=5,
            session_log_dir=None,
        )
        writer.write_observation.assert_called_once()
        call_args = writer.write_observation.call_args
        content = call_args.args[0]
        assert "grind_combat" in content
        assert "death" in call_args.kwargs["tags"]

    def test_empty_behavior_digest_not_written(self):
        from opendwarf.behaviors.digest import EventDigest

        writer = MagicMock()
        behavior = MagicMock()
        behavior.digest = EventDigest()  # empty
        behavior.name = "patrol"

        state = self._make_state()
        handle_death(
            state=state, cause="death",
            llm=MagicMock(), postmortem_buffer=None,
            reflection_engine=None, memory_writer=writer,
            active_behavior=behavior, suspended_behavior=None,
            log_file=self._make_log_file(), turn_count=5,
            session_log_dir=None,
        )
        writer.write_observation.assert_not_called()

    def test_session_logs_archived(self):
        tmpdir = Path(tempfile.mkdtemp())
        try:
            session_dir = tmpdir / "session_20260611_120000"
            session_dir.mkdir()
            (session_dir / "decisions.jsonl").write_text('{"event":"test"}\n')

            state = self._make_state()
            handle_death(
                state=state, cause="death",
                llm=MagicMock(), postmortem_buffer=None,
                reflection_engine=None, memory_writer=None,
                active_behavior=None, suspended_behavior=None,
                log_file=self._make_log_file(), turn_count=1,
                session_log_dir=session_dir,
            )
            archive = tmpdir / "archive" / "session_20260611_120000"
            assert archive.exists()
            assert (archive / "decisions.jsonl").read_text() == '{"event":"test"}\n'
        finally:
            shutil.rmtree(str(tmpdir))

    def test_death_event_logged(self):
        log = io.StringIO()
        state = self._make_state()
        handle_death(
            state=state, cause="killed by WOLF",
            llm=MagicMock(), postmortem_buffer=None,
            reflection_engine=None, memory_writer=None,
            active_behavior=None, suspended_behavior=None,
            log_file=log, turn_count=99,
            session_log_dir=None,
        )
        log.seek(0)
        entry = json.loads(log.read().strip())
        assert entry["event"] == "adventurer_death"
        assert entry["turn"] == 99
        assert entry["cause"] == "killed by WOLF"

    def test_postmortem_failure_does_not_prevent_reflection(self):
        pm = MagicMock()
        pm.generate_and_append.side_effect = RuntimeError("LLM down")
        engine = MagicMock()
        engine.reflect.return_value = []

        state = self._make_state()
        # Should not raise despite postmortem failure
        handle_death(
            state=state, cause="death",
            llm=MagicMock(), postmortem_buffer=pm,
            reflection_engine=engine, memory_writer=None,
            active_behavior=None, suspended_behavior=None,
            log_file=self._make_log_file(), turn_count=1,
            session_log_dir=None,
        )
        engine.reflect.assert_called_once()

    def test_no_postmortem_buffer_no_error(self):
        state = self._make_state()
        handle_death(
            state=state, cause="death",
            llm=MagicMock(), postmortem_buffer=None,
            reflection_engine=None, memory_writer=None,
            active_behavior=None, suspended_behavior=None,
            log_file=self._make_log_file(), turn_count=0,
            session_log_dir=None,
        )

    def test_suspended_behavior_used_when_no_active(self):
        from opendwarf.behaviors.digest import EventDigest

        writer = MagicMock()
        suspended = MagicMock()
        digest = EventDigest()
        digest.add("explored area")
        suspended.digest = digest
        suspended.name = "patrol"

        state = self._make_state()
        handle_death(
            state=state, cause="death",
            llm=MagicMock(), postmortem_buffer=None,
            reflection_engine=None, memory_writer=writer,
            active_behavior=None, suspended_behavior=suspended,
            log_file=self._make_log_file(), turn_count=3,
            session_log_dir=None,
        )
        writer.write_observation.assert_called_once()
        content = writer.write_observation.call_args.args[0]
        assert "patrol" in content
