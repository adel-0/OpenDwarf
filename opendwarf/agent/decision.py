"""Layer 1 tactical decision loop."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path

from opendwarf.agent.prompts import build_system_prompt, build_turn_prompt
from opendwarf.dfhack.lua_executor import LuaExecutor
from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

# Direction deltas (dx, dy) in the 5x5 map grid (radius=2, center at [2][2])
# row = 2 + dy, col = 2 + dx
_MOVE_DELTAS: dict[str, tuple[int, int]] = {
    "move_n": (0, -1),
    "move_s": (0, 1),
    "move_e": (1, 0),
    "move_w": (-1, 0),
    "move_ne": (1, -1),
    "move_nw": (-1, -1),
    "move_se": (1, 1),
    "move_sw": (-1, 1),
}
_WALKABLE_CHARS = set(".@<>X")

# Map action names to DFHack input keys
ACTION_MAP = {
    "move_n": "A_MOVE_N",
    "move_s": "A_MOVE_S",
    "move_e": "A_MOVE_E",
    "move_w": "A_MOVE_W",
    "move_ne": "A_MOVE_NE",
    "move_nw": "A_MOVE_NW",
    "move_se": "A_MOVE_SE",
    "move_sw": "A_MOVE_SW",
    "wait": "A_MOVE_SAME_SQUARE",   # numpad-5 stay-in-place (1 instant)
    "wait_long": "A_WAIT",           # 10-instant wait (the '.' key)
    "talk": "A_TALK",
    "attack": "A_ATTACK",
    "look": "A_LOOK",
    "escape": "LEAVESCREEN",
    "select": "SELECT",
    "cursor_up": "CURSOR_UP",
    "cursor_down": "CURSOR_DOWN",
    # Item interaction — open the menu; use pickup_N/drop_N/wield_N for indexed selection
    "pickup": "A_GROUND",
    "drop": "A_INV_DROP",
    "wield": "A_INV_DRAW_WEAPON",
    "wear": "A_INV_WEAR",
    "remove_item": "A_INV_REMOVE",
    "rest": "A_SLEEP",
}


class LLMClient:
    """Abstract LLM interface. Subclass for real API calls."""

    def decide(self, system_prompt: str, turn_prompt: str) -> dict:
        raise NotImplementedError



class AzureOpenAILLM(LLMClient):
    """Real LLM using Azure OpenAI (Microsoft Foundry)."""

    def __init__(self) -> None:
        import os
        from openai import AzureOpenAI

        self.client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ["AZURE_OPENAI_API_VERSION"],
        )
        self.deployment = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]

    def decide(self, system_prompt: str, turn_prompt: str) -> dict:
        response = self.client.chat.completions.create(
            model=self.deployment,
            reasoning_effort="medium",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": turn_prompt},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError(f"LLM returned empty response (finish_reason={response.choices[0].finish_reason!r})")
        # Parse JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text)


def _is_move_valid(action: str, map_tiles: list[str]) -> bool:
    """Check if a move action targets a walkable tile in the 5x5 map grid."""
    if action not in _MOVE_DELTAS or not map_tiles:
        return True  # Can't check — allow it
    dx, dy = _MOVE_DELTAS[action]
    row_idx = 2 + dy
    col_idx = 2 + dx
    if 0 <= row_idx < len(map_tiles):
        row = map_tiles[row_idx]
        if 0 <= col_idx < len(row):
            return row[col_idx] in _WALKABLE_CHARS
    return False


class TacticalLoop:
    """Main game loop: read state -> decide -> act -> repeat."""

    def __init__(self, lua: LuaExecutor, llm: LLMClient, poll_interval: float = 0.5, goal: str | None = None):
        self.lua = lua
        self.llm = llm
        self.poll_interval = poll_interval
        self.goal = goal
        self.running = False
        self.turn_count = 0
        self._last_tick = 0  # Track tick_counter to verify actions took effect
        # JSONL decision log
        log_path = Path("decisions") / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        log_path.parent.mkdir(exist_ok=True)
        self._log_file = log_path.open("a", encoding="utf-8")
        logger.info("Decision log: %s", log_path)

    def run(self) -> None:
        """Run the tactical decision loop."""
        self.running = True
        logger.info("Starting tactical loop")

        while self.running:
            try:
                self._tick()
            except KeyboardInterrupt:
                logger.info("Loop interrupted by user")
                self.running = False
            except Exception:
                logger.exception("Error in tactical loop tick")
                time.sleep(1.0)

    def _tick(self) -> None:
        # Extract state
        raw_state = self.lua.extract_state()
        state = GameState.from_raw(raw_state)

        if not state.is_adventure_mode:
            logger.debug("Not in adventure mode, waiting...")
            time.sleep(self.poll_interval)
            return

        if not state.taking_input:
            logger.debug("Game not taking input (state=%s), waiting...", state.player_control_state)
            time.sleep(self.poll_interval)
            return

        # Check if previous action took effect
        if self._last_tick and state.tick_counter == self._last_tick:
            logger.debug("Tick unchanged (%d), action may still be processing", self._last_tick)

        # Build prompt and get decision
        summary = state.summary()
        turn_prompt = build_turn_prompt(summary)
        logger.info("Turn %d:\n%s", self.turn_count, summary)

        t0 = time.monotonic()
        decision = self.llm.decide(build_system_prompt(self.goal), turn_prompt)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        action = decision.get("action", "wait")
        reasoning = decision.get("reasoning", "")
        logger.info("Decision: %s — %s", action, reasoning)

        # Validate move actions before executing
        if action in _MOVE_DELTAS and not _is_move_valid(action, state.map_tiles):
            logger.warning("Move %s blocked by wall/unknown tile, substituting wait", action)
            action = "wait"

        # Execute action (deferred — fires after RPC lock releases)
        self._last_tick = state.tick_counter
        self._execute(action)

        # Log decision to JSONL
        self._log_decision(state, action, reasoning, elapsed_ms)
        self.turn_count += 1

        # Wait for the deferred action to take effect.
        # Multi-step actions (conversation, item pickup/drop/wield) use extra frames.
        multi_step = action.startswith("conversation_") or any(
            action.startswith(p) for p in ("pickup_", "drop_", "wield_")
        )
        wait = 0.6 if multi_step else max(self.poll_interval, 0.3)
        time.sleep(wait)

    def _log_decision(self, state: GameState, action: str, reasoning: str, elapsed_ms: int) -> None:
        entry = {
            "turn": self.turn_count,
            "tick": state.tick_counter,
            "action": action,
            "reasoning": reasoning,
            "llm_ms": elapsed_ms,
            "health_pct": state.health_pct,
            "in_combat": state.in_combat,
            "position": str(state.adventurer_position),
            "site": state.site_name or state.region_name,
        }
        self._log_file.write(json.dumps(entry) + "\n")
        self._log_file.flush()

    def _execute(self, action: str) -> None:
        """Translate action name to a game command and execute."""
        if action.startswith("conversation_"):
            idx = action.split("_", 1)[1]
            self.lua.execute_action(f"conversation:{idx}")
        elif action.startswith("pickup_"):
            idx = action.split("_", 1)[1]
            self.lua.execute_action(f"pickup:{idx}")
        elif action.startswith("drop_"):
            idx = action.split("_", 1)[1]
            self.lua.execute_action(f"drop:{idx}")
        elif action.startswith("wield_"):
            idx = action.split("_", 1)[1]
            self.lua.execute_action(f"wield:{idx}")
        elif action in ACTION_MAP:
            self.lua.execute_action(ACTION_MAP[action])
        else:
            logger.warning("Unknown action: %s, defaulting to wait", action)
            self.lua.execute_action("A_MOVE_SAME_SQUARE")
