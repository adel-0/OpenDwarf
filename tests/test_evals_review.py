"""Unit tests for the escape-hatch review doc generator (evals/review.py).

Writes synthetic decisions.jsonl files into a tmp logs dir, then checks the
clustering and the rendered markdown.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.review import Review, collect, render


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_session(logs_dir: Path, name: str, lines: list[dict]) -> None:
    sdir = logs_dir / name
    sdir.mkdir(parents=True)
    with (sdir / "decisions.jsonl").open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


def _esc(turn, focus, episode=1):
    return {"event": "escape_hatch", "turn": turn, "tick": 100 * turn,
            "focus": focus, "episode": episode}


def _console_err(turn, action, errors):
    return {"event": "console_error", "turn": turn, "action": action,
            "errors": errors, "tick": 100 * turn}


def _unstick_fail(turn, outcome):
    return {"event": "unstick_failed", "turn": turn, "outcome": outcome,
            "tick": 100 * turn}


def _decision(turn, action="wait"):
    return {"turn": turn, "tick": 100 * turn, "action": action, "health_pct": 100}


# ---------------------------------------------------------------------------
# collect()
# ---------------------------------------------------------------------------

def test_collect_clusters_escape_hatch_by_focus(tmp_path):
    _write_session(tmp_path, "session_a", [
        _decision(1),
        _esc(2, ["dungeonmode/Trade"]),
        _esc(3, ["dungeonmode/Trade"]),
        _esc(4, ["dungeonmode/Lever"]),
    ])
    _write_session(tmp_path, "session_b", [
        _esc(1, ["dungeonmode/Trade"]),
    ])
    review = collect(tmp_path)

    assert review.sessions_scanned == 2
    assert review.total_events == 4
    trade = review.escape_hatch["dungeonmode/Trade"]
    assert trade.count == 3
    assert trade.sessions == {"session_a", "session_b"}
    assert review.escape_hatch["dungeonmode/Lever"].count == 1


def test_collect_focus_list_joined_and_missing_is_unknown(tmp_path):
    _write_session(tmp_path, "s", [
        {"event": "escape_hatch", "turn": 1},  # no focus
        _esc(2, ["a/B", "c/D"]),
    ])
    review = collect(tmp_path)
    assert "unknown" in review.escape_hatch
    assert "a/B, c/D" in review.escape_hatch


def test_collect_console_error_clusters_by_action_with_samples(tmp_path):
    _write_session(tmp_path, "s", [
        _console_err(1, "attack:5", ["boom: nil value"]),
        _console_err(2, "attack:5", ["boom: nil value"]),
        _console_err(3, "eatdrink:0", ["other error"]),
    ])
    review = collect(tmp_path)
    atk = review.console_error["attack:5"]
    assert atk.count == 2
    assert atk.samples == ["boom: nil value"]  # deduped
    assert review.console_error["eatdrink:0"].count == 1


def test_collect_unstick_failed_clusters_by_outcome(tmp_path):
    _write_session(tmp_path, "s", [
        _unstick_fail(1, "still_stuck"),
        _unstick_fail(2, "still_stuck"),
    ])
    review = collect(tmp_path)
    assert review.unstick_failed["still_stuck"].count == 2


def test_collect_ignores_other_events_and_empty_sessions(tmp_path):
    _write_session(tmp_path, "s_empty", [])
    _write_session(tmp_path, "s_noise", [
        _decision(1),
        {"event": "policy_revised", "turn": 2, "diff": {}},
    ])
    review = collect(tmp_path)
    # Empty session contributes nothing and is not counted as scanned.
    assert review.sessions_scanned == 1
    assert review.total_events == 0


def test_collect_missing_logs_dir(tmp_path):
    review = collect(tmp_path / "nope")
    assert review.sessions_scanned == 0
    assert review.total_events == 0


# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------

def test_render_empty_has_no_signals_note():
    out = render(Review())
    assert "No stall signals recorded yet" in out
    assert "_None recorded._" in out


def test_render_orders_clusters_by_count_desc(tmp_path):
    _write_session(tmp_path, "s", [
        _esc(1, ["focus/Rare"]),
        _esc(2, ["focus/Hot"]),
        _esc(3, ["focus/Hot"]),
        _esc(4, ["focus/Hot"]),
    ])
    out = render(collect(tmp_path))
    assert out.index("focus/Hot") < out.index("focus/Rare")
    assert "focus/Hot" in out
    # Sample error text appears in the table for console errors.
    assert "promotion queue" in out
