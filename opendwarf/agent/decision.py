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



class AzureOpenAILLM(LLMClient):
    """Real LLM using Azure OpenAI (Microsoft Foundry)."""

    def __init__(self) -> None:
        import os
        from openai import AzureOpenAI

        self.client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
        )
        self.deployment = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]

    def decide(self, system_prompt: str, turn_prompt: str) -> dict:
        response = self.client.chat.completions.create(
            model=self.deployment,
            max_completion_tokens=512,
            reasoning_effort="high",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": turn_prompt},
            ],
        )
        text = response.choices[0].message.content.strip()
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
        self._last_tick = 0  # Track tick_counter to verify actions took effect

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

        decision = self.llm.decide(SYSTEM_PROMPT, turn_prompt)
        action = decision.get("action", "wait")
        reasoning = decision.get("reasoning", "")
        logger.info("Decision: %s — %s", action, reasoning)

        # Execute action (deferred — fires after RPC lock releases)
        self._last_tick = state.tick_counter
        self._execute(action)
        self.turn_count += 1

        # Wait for the deferred action to take effect.
        # The Lua timeout fires on the next frame after RPC returns,
        # then the game processes the input on the following tick(s).
        time.sleep(max(self.poll_interval, 0.3))

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
