"""Layer 1 tactical decision loop."""

from __future__ import annotations

import json
import logging
import time

from opendwarf.agent.prompts import SYSTEM_PROMPT, build_turn_prompt
from opendwarf.dfhack.lua_executor import LuaExecutor
from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

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
    "wait": "A_MOVE_SAME_SQUARE",
    "wait_long": "A_MOVE_SAME_SQUARE",  # act.lua handles the difference
    "attack": "A_ATTACK",
    "look": "A_LOOK",
    "escape": "LEAVESCREEN",
    "select": "SELECT",
}


class LLMClient:
    """Abstract LLM interface. Subclass for real API calls."""

    def decide(self, system_prompt: str, turn_prompt: str) -> dict:
        raise NotImplementedError


class SimulatedLLM(LLMClient):
    """Simulated LLM that picks simple actions for testing."""

    def decide(self, system_prompt: str, turn_prompt: str) -> dict:
        # Simple heuristic: if hostile units nearby, attack; otherwise wait
        if "[HOSTILE]" in turn_prompt:
            return {"action": "attack", "reasoning": "Hostile unit nearby, engaging in combat."}
        if "Conversation" in turn_prompt:
            return {"action": "conversation_0", "reasoning": "Selecting first conversation option."}
        return {"action": "wait", "reasoning": "No immediate threats. Waiting."}


class AnthropicLLM(LLMClient):
    """Real LLM using the Anthropic SDK."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001"):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def decide(self, system_prompt: str, turn_prompt: str) -> dict:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=256,
            system=system_prompt,
            messages=[{"role": "user", "content": turn_prompt}],
        )
        text = response.content[0].text.strip()
        # Parse JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text)


class TacticalLoop:
    """Main game loop: read state -> decide -> act -> repeat."""

    def __init__(self, lua: LuaExecutor, llm: LLMClient, poll_interval: float = 0.5):
        self.lua = lua
        self.llm = llm
        self.poll_interval = poll_interval
        self.running = False
        self.turn_count = 0

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

        # Build prompt and get decision
        summary = state.summary()
        turn_prompt = build_turn_prompt(summary)
        logger.info("Turn %d:\n%s", self.turn_count, summary)

        decision = self.llm.decide(SYSTEM_PROMPT, turn_prompt)
        action = decision.get("action", "wait")
        reasoning = decision.get("reasoning", "")
        logger.info("Decision: %s — %s", action, reasoning)

        # Execute action
        self._execute(action)
        self.turn_count += 1

        # Brief pause to let the game process
        time.sleep(self.poll_interval)

    def _execute(self, action: str) -> None:
        """Translate action name to a game command and execute."""
        if action.startswith("conversation_"):
            idx = action.split("_", 1)[1]
            self.lua.execute_action(f"conversation:{idx}")
        elif action in ACTION_MAP:
            self.lua.execute_action(ACTION_MAP[action])
        else:
            logger.warning("Unknown action: %s, defaulting to wait", action)
            self.lua.execute_action("A_MOVE_SAME_SQUARE")
