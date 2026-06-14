"""Tests for the record/replay tap (component 4: the Hybrid fidelity gate).

Covers:
  1. RecordingLuaExecutor transparently delegates AND records to a tape.
  2. ReplayLuaExecutor re-enacts a tape with no underlying executor.
  3. A record→replay round-trip reproduces results in order.
  4. Replay raises on call-sequence divergence and on exhaustion.
"""

from __future__ import annotations

import pytest

from opendwarf.sim import (
    RecordingLuaExecutor,
    ReplayLuaExecutor,
    SimulatedLuaExecutor,
    SimWorld,
)


def test_recording_delegates_and_records(tmp_path):
    sim = SimulatedLuaExecutor(SimWorld.wolf_survival())
    tape = tmp_path / "tape.jsonl"
    rec = RecordingLuaExecutor(sim, tape)

    state = rec.extract_state()          # delegated → real sim result
    rec.execute_action("A_MOVE_E")       # delegated → mutates the inner world

    # Delegation worked: the inner world actually moved.
    assert sim.world.pos == (51, 50, 10)
    assert state["adventurer"]["position"] == {"x": 50, "y": 50, "z": 10}

    # Both calls are on the tape, in order.
    rec.close()
    lines = tape.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    import json
    assert json.loads(lines[0])["method"] == "extract_state"
    assert json.loads(lines[1])["method"] == "execute_action"
    assert json.loads(lines[1])["args"] == ["A_MOVE_E"]


def test_replay_reenacts_without_inner(tmp_path):
    # Record a short session...
    sim = SimulatedLuaExecutor(SimWorld.wolf_survival())
    tape = tmp_path / "tape.jsonl"
    rec = RecordingLuaExecutor(sim, tape)
    s0 = rec.extract_state()
    rec.execute_action("A_MOVE_E")
    s1 = rec.extract_state()
    rec.close()

    # ...then replay it with NO underlying executor.
    replay = ReplayLuaExecutor(tape)
    assert replay.extract_state() == s0
    assert replay.execute_action("A_MOVE_E") == []   # sim returns [] for actions
    assert replay.extract_state() == s1
    assert replay.exhausted


def test_replay_raises_on_divergence(tmp_path):
    sim = SimulatedLuaExecutor(SimWorld.wolf_survival())
    tape = tmp_path / "tape.jsonl"
    rec = RecordingLuaExecutor(sim, tape)
    rec.extract_state()
    rec.close()

    replay = ReplayLuaExecutor(tape)
    # The tape's first call was extract_state, not execute_action.
    with pytest.raises(AssertionError, match="divergence"):
        replay.execute_action("A_MOVE_E")


def test_replay_raises_when_exhausted(tmp_path):
    sim = SimulatedLuaExecutor(SimWorld.wolf_survival())
    tape = tmp_path / "tape.jsonl"
    rec = RecordingLuaExecutor(sim, tape)
    rec.extract_state()
    rec.close()

    replay = ReplayLuaExecutor(tape)
    replay.extract_state()               # consumes the only entry
    with pytest.raises(AssertionError, match="exhausted"):
        replay.extract_state()
