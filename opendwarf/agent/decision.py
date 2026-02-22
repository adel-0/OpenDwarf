"""Layer 1 tactical decision loop."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from opendwarf.agent.prompts import build_system_prompt, build_turn_prompt
from opendwarf.dfhack.lua_executor import LuaExecutor
from opendwarf.state.game_state import GameState

if TYPE_CHECKING:
    from opendwarf.goals.manager import GoalManager
    from opendwarf.memory.postmortems import PostmortemBuffer
    from opendwarf.memory.reflection import ReflectionEngine
    from opendwarf.memory.retriever import MemoryRetriever
    from opendwarf.memory.writer import MemoryWriter
    from opendwarf.planning.strategic import StrategicPlanner

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


# ------------------------------------------------------------------
# Trigger detection helpers
# ------------------------------------------------------------------

_HEALTH_THRESHOLDS = (25, 10)


class _TriggerDetector:
    """Detects goal revision triggers by comparing successive game states."""

    def __init__(self) -> None:
        self._prev: GameState | None = None
        self._health_thresholds_hit: set[int] = set()
        self._last_site: str | None = None
        self._session_started = False

    def detect(self, state: GameState, last_action: str | None) -> list[str]:
        """Return a list of trigger names that fired this tick."""
        triggers: list[str] = []
        prev = self._prev

        if not self._session_started:
            triggers.append("session_start")
            self._session_started = True

        if prev is not None:
            # Combat resolved: was in combat, now not
            if prev.in_combat and not state.in_combat:
                triggers.append("combat_resolved")

            # Dialogue ended: was in conversation, now not (and we didn't just force-exit)
            if prev.conversation_phase != "none" and state.conversation_phase == "none":
                triggers.append("dialogue_ended")

            # Forced dialogue: unexpected conversation screen appeared
            if (
                prev.conversation_phase == "none"
                and state.conversation_phase != "none"
                and last_action not in ("talk", None)
            ):
                triggers.append("dialogue_forced")

            # Health thresholds crossed (only fire once per threshold)
            for threshold in _HEALTH_THRESHOLDS:
                if (
                    threshold not in self._health_thresholds_hit
                    and prev.health_pct >= threshold
                    and state.health_pct < threshold
                ):
                    triggers.append(f"health_threshold_{threshold}")
                    self._health_thresholds_hit.add(threshold)

            # Reset threshold tracking when health recovers
            for threshold in list(self._health_thresholds_hit):
                if state.health_pct >= threshold + 10:
                    self._health_thresholds_hit.discard(threshold)

            # New named location discovered
            current_site = state.site_name or state.region_name
            if current_site and current_site != self._last_site and self._last_site is not None:
                triggers.append("location_discovered")
            self._last_site = current_site

        # wait_long is a natural reflection moment
        if last_action == "wait_long":
            triggers.append("wait_long")

        self._prev = state
        return triggers


class TacticalLoop:
    """Main game loop: read state -> decide -> act -> repeat."""

    def __init__(
        self,
        lua: LuaExecutor,
        llm: LLMClient,
        poll_interval: float = 0.5,
        goal: str | None = None,
        goal_manager: "GoalManager | None" = None,
        strategic_planner: "StrategicPlanner | None" = None,
        memory_writer: "MemoryWriter | None" = None,
        memory_retriever: "MemoryRetriever | None" = None,
        postmortem_buffer: "PostmortemBuffer | None" = None,
        reflection_engine: "ReflectionEngine | None" = None,
        df_mechanics: str = "",
    ):
        self.lua = lua
        self.llm = llm
        self.poll_interval = poll_interval
        self._initial_goal_str = goal  # Legacy string goal; used only if no goal manager
        self.goal_manager = goal_manager
        self.strategic_planner = strategic_planner
        self.memory_writer = memory_writer
        self.memory_retriever = memory_retriever
        self.postmortem_buffer = postmortem_buffer
        self.reflection_engine = reflection_engine
        self.df_mechanics = df_mechanics
        self.running = False
        self.turn_count = 0
        self._last_tick = 0
        self._last_action: str | None = None
        self._trigger_detector = _TriggerDetector()

        # JSONL decision log
        log_path = Path("decisions") / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
        log_path.parent.mkdir(exist_ok=True)
        self._log_file = log_path.open("a", encoding="utf-8")
        logger.info("Decision log: %s", log_path)

    def run(self) -> None:
        """Run the tactical decision loop."""
        self.running = True
        logger.info("Starting tactical loop")

        try:
            while self.running:
                try:
                    self._tick()
                except KeyboardInterrupt:
                    logger.info("Loop interrupted by user")
                    self.running = False
                except Exception:
                    logger.exception("Error in tactical loop tick")
                    time.sleep(1.0)
        finally:
            self._on_session_end()

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

        # Advance decay clock using tick delta (capped per-action at 1,000)
        if self._last_tick and self.memory_retriever:
            tick_delta = max(0, state.tick_counter - self._last_tick)
            self.memory_retriever.advance_decay(tick_delta)

        # Check if previous action took effect
        if self._last_tick and state.tick_counter == self._last_tick:
            logger.debug("Tick unchanged (%d), action may still be processing", self._last_tick)

        # --- Goal / plan management ---
        self._handle_goal_revision(state)
        plan_summary = self._update_plan(state)
        goal_summary = self._build_goal_summary()

        # --- Memory retrieval ---
        memory_block = self._retrieve_memories(state)

        # Build prompt and get decision
        summary = state.summary()
        postmortems = self.postmortem_buffer.load() if self.postmortem_buffer else ""
        turn_prompt = build_turn_prompt(summary, plan_summary, memory_block)
        logger.info("Turn %d:\n%s", self.turn_count, summary)
        if plan_summary:
            logger.info("Plan context:\n%s", plan_summary)

        t0 = time.monotonic()
        decision = self.llm.decide(
            build_system_prompt(goal_summary, self.df_mechanics, postmortems),
            turn_prompt,
        )
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
        self._last_action = action
        self._execute(action)

        # Log decision to JSONL
        self._log_decision(state, action, reasoning, elapsed_ms, plan_summary)
        self.turn_count += 1

        # Wait for the deferred action to take effect.
        # Multi-step actions (conversation, item pickup/drop/wield) use extra frames.
        multi_step = action.startswith("conversation_") or any(
            action.startswith(p) for p in ("pickup_", "drop_", "wield_")
        )
        wait = 0.6 if multi_step else max(self.poll_interval, 0.3)
        time.sleep(wait)

    # ------------------------------------------------------------------
    # Session end
    # ------------------------------------------------------------------

    def _on_session_end(self) -> None:
        """Run reflection consolidation at session end."""
        if self.reflection_engine is None:
            return
        # We need a state for tick context — use a dummy if we don't have one
        try:
            raw = self.lua.extract_state()
            state = GameState.from_raw(raw)
        except Exception:
            state = GameState()
        logger.info("Running end-of-session reflection consolidation")
        notes = self.reflection_engine.reflect(state)
        logger.info("Reflection produced %d insight notes", len(notes))

    # ------------------------------------------------------------------
    # Goal & plan management
    # ------------------------------------------------------------------

    def _handle_goal_revision(self, state: GameState) -> None:
        """Detect triggers and run goal revision if needed."""
        if self.goal_manager is None:
            return

        triggers = self._trigger_detector.detect(state, self._last_action)
        if not triggers:
            return

        # Check exploration budgets
        self.goal_manager.check_exploration_budget(state.tick_counter)

        for trigger in triggers:
            logger.info("Goal revision triggered: %s", trigger)
            self.goal_manager.revise(trigger, state)
            # Hook memory writes to goal-revision triggers
            if self.memory_writer:
                self.memory_writer.on_trigger(trigger, state)
                # Check reflection consolidation threshold
                if self.memory_writer.should_reflect() and self.reflection_engine:
                    logger.info("Reflection threshold reached, running consolidation")
                    self.reflection_engine.reflect(state)
                    self.memory_writer.reset_reflection_counter()

    def _retrieve_memories(self, state: GameState) -> str:
        """Retrieve top-5 relevant memories for the current context."""
        if self.memory_retriever is None:
            return ""
        # Determine context type from state
        if state.in_combat or state.hostile_units:
            context_type = "combat"
        elif state.conversation_phase != "none":
            context_type = "conversation"
        else:
            context_type = "exploration"

        # Query = current situation summary (short)
        query = (
            f"{state.site_name or state.region_name or ''} "
            f"{' '.join(u.race for u in state.hostile_units[:3])} "
            f"{' '.join(r.name for r in state.npc_relationships[:3])}"
        ).strip() or "adventure"

        notes = self.memory_retriever.retrieve(
            query=query,
            context_type=context_type,
            k=5,
            game_tick=state.tick_counter,
        )
        return self.memory_retriever.format_for_prompt(notes)

    def _update_plan(self, state: GameState) -> str:
        """Ensure we have a plan for the active leaf goal. Returns plan_summary string."""
        if self.strategic_planner is None or self.goal_manager is None:
            return ""

        leaf = self.goal_manager.active_leaf()
        if leaf is None:
            return ""

        if self.strategic_planner.needs_replan(leaf):
            logger.info("Generating strategic plan for goal: %s", leaf.description)
            self.strategic_planner.generate(leaf, state)

        return self.strategic_planner.plan_summary()

    def _build_goal_summary(self) -> str | None:
        """Build goal context for the system prompt."""
        if self.goal_manager is not None:
            summary = self.goal_manager.tree_summary()
            if summary and summary != "(no goals)":
                return summary
        # Fall back to legacy string goal
        return self._initial_goal_str

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _log_decision(
        self,
        state: GameState,
        action: str,
        reasoning: str,
        elapsed_ms: int,
        plan_summary: str = "",
    ) -> None:
        leaf = self.goal_manager.active_leaf() if self.goal_manager else None
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
            "active_goal": leaf.description if leaf else self._initial_goal_str,
            "plan_step": plan_summary.split("\n")[1].replace("  NOW: ", "").strip() if plan_summary else None,
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
