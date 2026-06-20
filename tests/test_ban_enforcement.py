"""Unit tests for BUG-1: ban enforcement at dispatch + open-neighbor feedback.

Banning an action only HIDES it from the prompt; a weaker model can re-pick a
remembered action anyway. ``_dispatch`` must refuse to send game input for a
currently-banned action (surfacing a SKIPPED outcome), name the open directions
for a blocked move, and fall back to a productive default after repeated refusals
instead of refusing forever.
"""

from __future__ import annotations

import unittest.mock as mock

from opendwarf.agent.loop import TacticalLoop
from opendwarf.spatial.chunk_map import ChunkMap, Cell
from opendwarf.state.game_state import GameState, Position


def _bare_loop() -> TacticalLoop:
    """A TacticalLoop with just the fields the dispatch path touches."""
    loop = TacticalLoop.__new__(TacticalLoop)
    loop.turn_count = 0
    loop._last_action = None
    loop._last_state = None
    loop._history = []  # plain list is enough (append used)
    loop._recent_failures = {}
    loop._banned = set()
    loop._refusal_streak = 0
    loop._empty_talk_count = 0
    loop._announcements = []
    loop._conv = mock.MagicMock()
    loop._conv.npc_hist_fig_id = None
    loop._conv.npc_name = None
    loop._conv_guard = mock.MagicMock()
    loop._registry = mock.MagicMock()
    loop._skill_ctx = mock.MagicMock()
    loop._chunk_map = ChunkMap()
    loop._extractor = mock.MagicMock()
    loop._extractor.adventurer_abs.return_value = None
    loop._active_skill = None
    loop.poll_interval = 0.0
    loop._fresh_state = mock.MagicMock(return_value=GameState())
    loop.lua = mock.MagicMock()
    loop.lua.consume_action_errors.return_value = []
    loop._log_event = mock.MagicMock()
    loop._log_decision = mock.MagicMock()
    return loop


def _move_dispatch(canonical: str):
    """A registry Dispatch for a KEY move action."""
    from opendwarf.actions.registry import ActionKind, Dispatch
    return Dispatch(ActionKind.KEY, canonical, key="A_MOVE_E")


class TestBanEnforcement:

    def test_banned_move_is_skipped_not_executed(self):
        loop = _bare_loop()
        loop._banned = {"move_e"}
        loop._recent_failures = {"move_e": (0, "blocked (no move)")}
        loop._registry.resolve.return_value = _move_dispatch("move_e")

        state = GameState()
        loop._dispatch("move_e", "reasoning", state, 10, "")

        # No game input was sent.
        loop.lua.execute_action.assert_not_called()
        # A SKIPPED outcome reached the history.
        assert any("SKIPPED" in h and "move_e" in h for h in loop._history)
        # The ban-enforced event was logged.
        assert loop._log_event.called
        # State unchanged, no tick wasted re-extracting.
        assert loop._last_state is state

    def test_blocked_move_skip_names_open_directions(self):
        loop = _bare_loop()
        loop._banned = {"move_e"}
        loop._recent_failures = {"move_e": (0, "blocked (no move)")}
        loop._registry.resolve.return_value = _move_dispatch("move_e")

        # Adventurer at (10,10,5): east is a wall, N/W/S open.
        loop._extractor.adventurer_abs.return_value = (10, 10, 5)
        loop._chunk_map.set(11, 10, 5, Cell.WALL)     # east wall
        loop._chunk_map.set(10, 9, 5, Cell.PASSABLE)  # north open
        loop._chunk_map.set(9, 10, 5, Cell.PASSABLE)  # west open
        loop._chunk_map.set(10, 11, 5, Cell.PASSABLE)  # south open

        state = GameState()
        loop._dispatch("move_e", "reasoning", state, 10, "")

        hist = "\n".join(loop._history)
        assert "E" in hist  # east named as blocked
        assert "OPEN" in hist
        assert "N" in hist and "W" in hist and "S" in hist

    def test_unbanned_action_executes_normally(self):
        loop = _bare_loop()
        loop._banned = set()
        loop._registry.resolve.return_value = _move_dispatch("move_e")
        loop.lua.consume_action_errors.return_value = []
        # Make the after-state extraction return a plausible moved state.
        loop._fresh_state = mock.MagicMock(return_value=GameState())

        state = GameState()
        loop._dispatch("move_e", "reasoning", state, 10, "")

        loop.lua.execute_action.assert_called_once()

    def test_repeated_refusal_falls_back_to_explore(self):
        from opendwarf.actions.registry import ActionKind, Dispatch
        loop = _bare_loop()
        loop._banned = {"move_e"}
        loop._recent_failures = {"move_e": (0, "blocked (no move)")}
        loop._refusal_streak = 1  # one refusal already this streak

        # west is open so the fallback should pick explore:w (a SKILL).
        loop._extractor.adventurer_abs.return_value = (10, 10, 5)
        loop._chunk_map.set(9, 10, 5, Cell.PASSABLE)

        explore_skill = mock.MagicMock()

        def _resolve(action, state, ctx):
            if action == "move_e":
                return _move_dispatch("move_e")
            return Dispatch(ActionKind.SKILL, action, skill=explore_skill)

        loop._registry.resolve.side_effect = _resolve

        state = GameState()
        loop._dispatch("move_e", "reasoning", state, 10, "")

        # The fallback explore skill became the active skill (productive default).
        assert loop._active_skill is explore_skill
        assert any("falling back" in h for h in loop._history)


class TestOpenNeighbors:

    def test_classifies_open_and_wall(self):
        loop = _bare_loop()
        loop._extractor.adventurer_abs.return_value = (0, 0, 0)
        loop._chunk_map.set(1, 0, 0, Cell.WALL)       # e wall
        loop._chunk_map.set(-1, 0, 0, Cell.PASSABLE)  # w open
        loop._chunk_map.set(0, -1, 0, Cell.DOOR)      # n open (door walkable)
        # s left UNKNOWN -> omitted from both

        state = GameState()
        open_dirs, wall_dirs = loop._open_neighbors(state)
        assert "w" in open_dirs and "n" in open_dirs
        assert "e" in wall_dirs
        assert "s" not in open_dirs and "s" not in wall_dirs

    def test_hint_formatting(self):
        loop = _bare_loop()
        hint = loop._open_dir_hint(["n", "w"], ["e"])
        assert "blocked: E" in hint
        assert "OPEN" in hint
        assert "N/W" in hint
