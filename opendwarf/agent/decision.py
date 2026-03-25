"""Layer 1 tactical decision loop."""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from opendwarf.agent.navigator import DIRECTION_DELTAS, Navigator, NavigatorResult
from opendwarf.agent.prompts import build_action_block, build_system_prompt, build_turn_prompt
from opendwarf.dfhack.lua_executor import LuaExecutor
from opendwarf.state.game_state import GameState

if TYPE_CHECKING:
    from opendwarf.goals.manager import GoalManager
    from opendwarf.memory.postmortems import PostmortemBuffer
    from opendwarf.memory.reflection import ReflectionEngine
    from opendwarf.memory.retriever import MemoryRetriever
    from opendwarf.memory.writer import MemoryWriter
    from opendwarf.observability import EventLogger

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Action outcome detection
# ------------------------------------------------------------------

@dataclass
class _StateSnapshot:
    """Minimal snapshot for before/after comparison."""
    tick: int
    position: str
    menu_state: str
    conversation_phase: str
    inventory_count: int
    in_combat: bool
    focus_state: str

    @staticmethod
    def from_game_state(state: GameState) -> _StateSnapshot:
        # During fast travel, use army position instead of adventurer position
        pos = str(state.fast_travel_army_pos) if state.fast_travel_active and state.fast_travel_army_pos else str(state.adventurer_position)
        return _StateSnapshot(
            tick=state.tick_counter,
            position=pos,
            menu_state=state.menu_state,
            conversation_phase=state.conversation_phase,
            inventory_count=len(state.inventory),
            in_combat=state.in_combat,
            focus_state=state.focus_state,
        )

    def changed_from(self, other: _StateSnapshot) -> bool:
        """Return True if any meaningful field changed."""
        return (
            self.tick != other.tick
            or self.position != other.position
            or self.menu_state != other.menu_state
            or self.conversation_phase != other.conversation_phase
            or self.inventory_count != other.inventory_count
            or self.in_combat != other.in_combat
            or self.focus_state != other.focus_state
        )


@dataclass
class ActionOutcome:
    action: str
    tick_changed: bool
    position_changed: bool
    state_changed: bool  # any field changed
    consecutive_no_effect: int


class _OutcomeTracker:
    """Tracks consecutive no-effect actions and manages temporary bans.

    Detects two patterns:
    1. Same-action repetition with no state change (e.g. talk x3)
    2. Oscillation cycles where the net state returns to where it started
       (e.g. talk→escape→talk→escape — each action "succeeds" but the pair is a no-op)
    """

    _BAN_THRESHOLD = 3   # ban after this many wasted turns
    _BAN_DURATION = 5    # turns the action stays banned

    def __init__(self) -> None:
        self.consecutive_no_effect: int = 0
        self._last_no_effect_action: str | None = None
        self._banned: dict[str, int] = {}  # action -> turns remaining
        # Oscillation detection: track recent (action, snapshot) pairs
        self._history: list[tuple[str, _StateSnapshot]] = []  # last N (action, before_snap)
        self._oscillation_count: int = 0

    def record(self, action: str, before: _StateSnapshot, after: _StateSnapshot) -> ActionOutcome:
        """Compare snapshots and update tracking."""
        changed = after.changed_from(before)
        outcome = ActionOutcome(
            action=action,
            tick_changed=after.tick != before.tick,
            position_changed=after.position != before.position,
            state_changed=changed,
            consecutive_no_effect=0,
        )

        # --- Pattern 1: same-action no state change ---
        if not changed:
            if action == self._last_no_effect_action:
                self.consecutive_no_effect += 1
            else:
                self.consecutive_no_effect = 1
                self._last_no_effect_action = action
            outcome.consecutive_no_effect = self.consecutive_no_effect

            if self.consecutive_no_effect >= self._BAN_THRESHOLD:
                self._banned[action] = self._BAN_DURATION
                logger.info("Banning action '%s' for %d turns (no-effect repeat)", action, self._BAN_DURATION)
        else:
            self.consecutive_no_effect = 0
            self._last_no_effect_action = None

        # --- Pattern 2: oscillation (A→B→A→B returning to same state) ---
        self._history.append((action, before))
        if len(self._history) > 8:
            self._history = self._history[-8:]

        # Check if current state matches a state from 2 or 4 turns ago
        # (i.e. the net effect of the last 2 or 4 actions is zero)
        oscillating = False
        for lookback in (2, 4):
            if len(self._history) >= lookback + 1:
                old_snap = self._history[-(lookback + 1)][1]
                if not after.changed_from(old_snap):
                    oscillating = True
                    break

        if oscillating:
            self._oscillation_count += 1
            if self._oscillation_count >= 2:
                # Ban the action that initiates the cycle (the one from 2 turns ago)
                if len(self._history) >= 3:
                    initiator = self._history[-3][0]
                    if initiator not in self._banned:
                        self._banned[initiator] = self._BAN_DURATION
                        logger.info("Banning action '%s' for %d turns (oscillation cycle)", initiator, self._BAN_DURATION)
                # Also reflect in the no-effect counter for hint escalation
                outcome.consecutive_no_effect = max(outcome.consecutive_no_effect, self._oscillation_count + 1)
        else:
            self._oscillation_count = 0

        # Any genuine progress clears bans
        if changed and not oscillating:
            self._banned.clear()

        # Tick down ban durations
        expired = [a for a, t in self._banned.items() if t <= 0]
        for a in expired:
            del self._banned[a]
        for a in self._banned:
            self._banned[a] -= 1

        return outcome

    def is_banned(self, action: str) -> bool:
        return action in self._banned

    @property
    def banned_actions(self) -> set[str]:
        return set(self._banned.keys())

    def build_hint(self, outcome: ActionOutcome) -> str:
        """Build escalating hint text based on outcome."""
        n = outcome.consecutive_no_effect
        if n == 0:
            return ""
        if n == 1:
            return f"Your last action '{outcome.action}' had no visible effect."
        if n == 2:
            return f"Your last {n} actions had no effect. Try a different approach."
        return f"WARNING: {n} consecutive actions had no effect. You are stuck. You MUST try something fundamentally different."

# ------------------------------------------------------------------
# Conversation tracking
# ------------------------------------------------------------------

class _ConversationTracker:
    """Accumulates a structured transcript during an active conversation.

    Records player choices and NPC responses so that on dialogue_ended,
    the full transcript can be flushed to MemoryWriter for summarization.
    """

    def __init__(self) -> None:
        self._entries: list[str] = []  # "YOU: ..." or "NPC: ..."
        self.npc_name: str | None = None
        self.npc_hist_fig_id: int | None = None
        self.active: bool = False

    def record_choice(self, choice_text: str) -> None:
        self._entries.append(f"YOU: {choice_text}")

    def record_npc_response(self, lines: list[str]) -> None:
        for line in lines:
            stripped = line.strip()
            if stripped:
                # DF announcement buffer includes player speech as "You: ..."
                if stripped.startswith("You:") or stripped.startswith("You :"):
                    self._entries.append(f"YOU: {stripped[stripped.index(':') + 1:].strip()}")
                else:
                    self._entries.append(f"NPC: {stripped}")

    def start(self, npc_name: str | None, npc_hist_fig_id: int | None = None) -> None:
        """Set the NPC name when conversation starts (only if not already set)."""
        self.active = True
        if self.npc_name is None:
            self.npc_name = npc_name
            self.npc_hist_fig_id = npc_hist_fig_id

    def flush(self) -> tuple[str | None, str | None, int | None]:
        """Return (transcript, npc_name, npc_hist_fig_id) and reset."""
        self.active = False
        if not self._entries:
            return None, None, None
        transcript = "\n".join(self._entries)
        name = self.npc_name
        hf_id = self.npc_hist_fig_id
        self._entries.clear()
        self.npc_name = None
        self.npc_hist_fig_id = None
        return transcript, name, hf_id

    @property
    def has_content(self) -> bool:
        return bool(self._entries)

    def format_for_prompt(self) -> str:
        """Format the in-progress transcript for injection into the turn prompt."""
        if not self._entries:
            return ""
        header = f"-- Current Conversation (with {self.npc_name or 'NPC'}) --"
        return header + "\n" + "\n".join(f"  {e}" for e in self._entries[-10:])


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
    "travel": "travel_enter",
    "stop_travel": "travel_exit",
}


class LLMClient:
    """Abstract LLM interface. Subclass for real API calls."""

    def decide(self, system_prompt: str, turn_prompt: str, *, caller: str = "tactical") -> dict:
        raise NotImplementedError


class AzureOpenAILLM(LLMClient):
    """Real LLM using Azure OpenAI (Microsoft Foundry)."""

    def __init__(self, event_logger: "EventLogger | None" = None) -> None:
        import os
        from openai import AzureOpenAI

        self.client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ["AZURE_OPENAI_API_VERSION"],
            timeout=60.0,
        )
        self.deployment = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]
        self._event_logger = event_logger

    def decide(self, system_prompt: str, turn_prompt: str, *, caller: str = "tactical") -> dict:
        t0 = time.monotonic()
        error_msg: str | None = None
        text = ""
        try:
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
        except Exception as exc:
            error_msg = str(exc)
            raise
        finally:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if self._event_logger:
                self._event_logger.log_llm_call(
                    caller=caller,
                    system_prompt=system_prompt,
                    turn_prompt=turn_prompt,
                    response_raw=text or None,
                    elapsed_ms=elapsed_ms,
                    error=error_msg,
                )


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
        self.conversation_had_content: bool = False  # set by TacticalLoop

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

            # Dialogue ended: was in conversation, now not — only trigger revision
            # if the conversation had meaningful content (avoids expensive LLM replan
            # when agent just opens talk menu and immediately escapes)
            if prev.conversation_phase != "none" and state.conversation_phase == "none":
                if self.conversation_had_content:
                    triggers.append("dialogue_ended")
                else:
                    logger.debug("Skipping dialogue_ended trigger (no meaningful content)")
                self.conversation_had_content = False

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
        memory_writer: "MemoryWriter | None" = None,
        memory_retriever: "MemoryRetriever | None" = None,
        postmortem_buffer: "PostmortemBuffer | None" = None,
        reflection_engine: "ReflectionEngine | None" = None,
        df_mechanics: str = "",
        logs_dir: "Path | None" = None,
    ):
        self.lua = lua
        self.llm = llm
        self.poll_interval = poll_interval
        self._initial_goal_str = goal  # Legacy string goal; used only if no goal manager
        self.goal_manager = goal_manager
        self.memory_writer = memory_writer
        self.memory_retriever = memory_retriever
        self.postmortem_buffer = postmortem_buffer
        self.reflection_engine = reflection_engine
        self.df_mechanics = df_mechanics
        self.running = False
        self.turn_count = 0
        self._last_action: str | None = None
        self._trigger_detector = _TriggerDetector()
        self._outcome_tracker = _OutcomeTracker()
        self._last_outcome: ActionOutcome | None = None
        self._last_state: GameState | None = None  # reused as next turn's "before" state
        self._navigator = Navigator(lua)
        self._empty_talk_count: int = 0
        self._announcement_buffer: list[str] = []  # buffered announcement text for LLM context
        self._conversation_tracker = _ConversationTracker()
        self._position_history: list[tuple[int, int, int]] = []  # last N positions for loop detection
        self._area_stuck_turns: int = 0  # how many turns spent in a small area
        self._nav_fail_count: int = 0  # consecutive navigator loop/stuck failures
        self._recent_decisions: deque[tuple[str, str]] = deque(maxlen=5)  # (action, reasoning)

        # JSONL decision log — write to logs_dir alongside other observability files
        if logs_dir:
            log_path = logs_dir / "decisions.jsonl"
        else:
            log_path = Path("logs") / f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}" / "decisions.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
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
        # Extract state (reuse last post-action state if available)
        if self._last_state is not None:
            state = self._last_state
        else:
            raw_state = self.lua.extract_state()
            state = GameState.from_raw(raw_state)

        if not state.is_adventure_mode:
            logger.debug("Not in adventure mode, waiting...")
            self._last_state = None
            time.sleep(self.poll_interval)
            return

        if not state.taking_input:
            logger.debug("Game not taking input (state=%s), waiting...", state.player_control_state)
            self._last_state = None
            time.sleep(self.poll_interval)
            return

        # Auto-activate conversation tracker when entering conversation phase
        if state.conversation_phase != "none" and not self._conversation_tracker.active:
            self._conversation_tracker.active = True

        # Auto-dismiss help dialogs (requires mouse-click on "Okay" button)
        if state.focus_state and "Help" in state.focus_state:
            logger.debug("Auto-dismissing help dialog (focus=%s)", state.focus_state)
            try:
                self.lua.run_script("opendwarf--clickok")
            except Exception as e:
                logger.warning("Failed to click Okay: %s, falling back to SELECT", e)
                self._execute("select")
            self._last_state = None
            time.sleep(0.3)
            return

        # Auto-escape look mode — it provides no extra state info to the LLM
        if state.focus_state and "Look" in state.focus_state:
            logger.info("Auto-escaping look mode (focus=%s)", state.focus_state)
            self._execute("escape")
            self._last_state = None
            time.sleep(0.3)
            return

        # Buffer announcement text before dismissing — this is how we read NPC responses
        if state.showing_announcements:
            if state.announcement_text:
                for line in state.announcement_text:
                    if line not in self._announcement_buffer:
                        self._announcement_buffer.append(line)
                # Keep buffer from growing unbounded (last 20 lines)
                self._announcement_buffer = self._announcement_buffer[-20:]
                # Also add to conversation transcript if in dialogue context
                if self._conversation_tracker.active:
                    self._conversation_tracker.record_npc_response(state.announcement_text)
            ann_preview = " | ".join(state.announcement_text[:2])
            logger.info("Buffered announcement text: %s", ann_preview[:100])
            self._execute("select")
            self._last_state = None
            time.sleep(0.3)
            return

        # Auto-escape empty conversation menu (only system options, no real NPCs)
        if state.conversation_phase == "select_npc" and state.conversation_choices:
            all_system = all(
                "adventure_option_" in c.text.lower() or "shout" in c.text.lower()
                for c in state.conversation_choices
            )
            if all_system:
                logger.info("Auto-escaping empty conversation menu (no real NPCs, %d system options)",
                            len(state.conversation_choices))
                self._execute("escape")
                self._last_state = None
                # Track empty talk attempts — inject hint but don't ban (talk is essential)
                if self._last_action == "talk":
                    self._empty_talk_count += 1
                    logger.info("Empty talk count: %d", self._empty_talk_count)
                time.sleep(0.3)
                return

        # --- Navigator autopilot branch ---
        nav_hint = ""
        if self._navigator.active:
            result = self._navigator.step(state)
            if result == NavigatorResult.MOVED:
                self._last_state = None  # force fresh extraction next tick
                time.sleep(0.3)
                return
            # Navigator done or interrupted — fall through to LLM
            reason = self._navigator.deactivation_reason or ""
            nav_hint = f"Navigation ended: {reason}."
            logger.info("Navigator returned control: %s", reason)
            if "stuck" in reason or "loop" in reason:
                self._nav_fail_count += 1
                logger.info("Navigator fail count: %d", self._nav_fail_count)
            else:
                self._nav_fail_count = 0
            self._navigator.deactivate()
            self._last_outcome = None  # clear stale outcome
            # Build a fresh state after navigator finished
            raw_state = self.lua.extract_state()
            state = GameState.from_raw(raw_state)
            self._last_state = None

        # Build outcome hint from last action's result
        hint = ""
        if nav_hint:
            hint = nav_hint
        elif self._last_outcome is not None:
            hint = self._outcome_tracker.build_hint(self._last_outcome)
            if hint:
                logger.info("Outcome hint: %s", hint)

        # Empty-talk hint: detect busy NPCs and suggest waiting or moving
        if self._empty_talk_count >= 2:
            # Check if NPCs are busy talking to each other (pattern: "The X (to the Y): ...")
            busy_npcs: set[str] = set()
            for ann in self._announcement_buffer:
                m = re.match(r"The (.+?) \(to the (.+?)\):", ann)
                if m:
                    busy_npcs.add(m.group(1))
                    busy_npcs.add(m.group(2))
            if self._empty_talk_count >= 5:
                # Strongly force travel away — staying here is not productive
                talk_hint = (
                    f"IMPORTANT: 'talk' has failed {self._empty_talk_count} times — NO NPCs are addressable here. "
                    "You MUST stop trying to talk. Use 'travel' to enter fast travel and move to a DIFFERENT site. "
                    "Do NOT use stop_travel until you have moved to a new location."
                )
                if state.nearby_sites:
                    # Suggest a site other than current
                    other_sites = [s for s in state.nearby_sites if s.distance and s.distance > 0]
                    if other_sites:
                        s = other_sites[0]
                        talk_hint += f" Nearest other site: {s.name} ({s.site_type}), {s.distance} tiles {s.direction}."
            elif busy_npcs:
                talk_hint = (
                    f"NOTE: 'talk' returned empty {self._empty_talk_count} times because nearby NPCs are busy "
                    f"talking to each other ({', '.join(list(busy_npcs)[:3])}). "
                    "Try 'wait_long' to let their conversations finish, then 'talk' again."
                )
            else:
                talk_hint = (
                    f"NOTE: 'talk' opened an empty menu {self._empty_talk_count} times — "
                    "no NPCs are addressable from here. Move to a different area or use 'travel' to find NPCs."
                )
            hint = f"{hint}\n{talk_hint}" if hint else talk_hint

        # Area-stuck escalation: nudge toward fast travel
        # Use navigator failures as a fast signal — 3 consecutive failures = stuck
        nav_stuck = self._nav_fail_count >= 3
        area_stuck = self._area_stuck_turns >= 3
        if (nav_stuck or area_stuck) and not state.fast_travel_active:
            stuck_msg = (
                "IMPORTANT: Local movement is NOT working — you are stuck. "
                "DO NOT use go_* directions again. "
                "You MUST use 'travel' to enter fast travel mode and move to a nearby site."
            )
            if state.nearby_sites:
                closest = state.nearby_sites[0]
                stuck_msg += f" Closest site: {closest.name} ({closest.site_type}), {closest.distance} tiles {closest.direction}."
            hint = f"{hint}\n{stuck_msg}" if hint else stuck_msg
            logger.info("Stuck hint injected (nav_fails=%d, area_stuck=%d)", self._nav_fail_count, self._area_stuck_turns)

        # --- Goal / plan management ---
        triggers = self._handle_goal_revision(state)
        plan_summary = self._update_plan(state, triggers)
        goal_summary = self._build_goal_summary()

        # Fast travel hint when plan step requires long-distance travel
        if self.goal_manager and self.goal_manager.current_step and not state.fast_travel_active:
            from opendwarf.goals.model import CompletionType
            ct = self.goal_manager.current_step.completion_type
            if ct == CompletionType.REACH_SITE:
                ft_hint = (
                    "HINT: To reach a different site, use 'travel' (fast travel mode), not go_* directions. "
                    "Fast travel moves you across the world map quickly."
                )
                hint = f"{hint}\n{ft_hint}" if hint else ft_hint

        # --- Memory retrieval ---
        memory_block = self._retrieve_memories(state)

        # Build announcement context from buffer (recent NPC speech, combat results)
        announcement_block = ""
        if self._announcement_buffer:
            announcement_block = "-- Recent Announcements (NPC speech / events) --\n"
            announcement_block += "\n".join(f"  {line}" for line in self._announcement_buffer[-10:])
        # Include combat log if present and not already in announcements
        if state.combat_log:
            if not announcement_block:
                announcement_block = "-- Recent Combat Log --\n"
            else:
                announcement_block += "\n-- Combat Log --\n"
            announcement_block += "\n".join(f"  {line}" for line in state.combat_log[-5:])

        # Inject in-progress conversation transcript into announcements block
        if self._conversation_tracker.has_content:
            conv_block = self._conversation_tracker.format_for_prompt()
            if announcement_block:
                announcement_block = conv_block + "\n\n" + announcement_block
            else:
                announcement_block = conv_block

        # Build prompt and get decision
        summary = state.summary()
        postmortems = self.postmortem_buffer.load() if self.postmortem_buffer else ""
        # When stuck, ban all go_* directions to force travel usage
        banned = set(self._outcome_tracker.banned_actions)
        if (nav_stuck or area_stuck) and not state.fast_travel_active:
            go_dirs = ["go_north", "go_south", "go_east", "go_west", "go_ne", "go_nw", "go_se", "go_sw"]
            banned.update(go_dirs)
        action_block = build_action_block(state, banned=banned)
        # Format decision history for prompt
        decision_history = ""
        if self._recent_decisions:
            lines = []
            for i, (act, reason) in enumerate(self._recent_decisions, 1):
                short_reason = reason[:60] if reason else ""
                lines.append(f"  {i}. {act} — {short_reason}")
            decision_history = "-- Recent Actions --\n" + "\n".join(lines)

        turn_prompt = build_turn_prompt(
            summary, action_block, plan_summary, memory_block, hint,
            announcement_block=announcement_block,
            decision_history=decision_history,
        )
        logger.info("Turn %d:\n%s", self.turn_count, summary)
        if plan_summary:
            logger.info("Plan context:\n%s", plan_summary)

        t0 = time.monotonic()
        decision = self.llm.decide(
            build_system_prompt(goal_summary, self.df_mechanics, postmortems),
            turn_prompt,
            caller="tactical",
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        action = decision.get("action", "wait")
        reasoning = decision.get("reasoning", "")
        logger.info("Decision: %s — %s", action, reasoning)
        self._recent_decisions.append((action, reasoning))

        # Enforce action bans (from outcome tracker)
        if self._outcome_tracker.is_banned(action):
            logger.warning("Action '%s' is temporarily banned (repeated no-effect), substituting wait", action)
            action = "wait"

        # Validate move actions before executing (skip in fast travel mode — different map)
        if action in _MOVE_DELTAS and not state.fast_travel_active and not _is_move_valid(action, state.map_tiles):
            logger.warning("Move %s blocked by wall/unknown tile, substituting wait", action)
            action = "wait"

        # Reset stuck counters when entering/exiting fast travel
        if action in ("travel", "stop_travel"):
            self._area_stuck_turns = 0
            self._nav_fail_count = 0
            self._position_history.clear()

        # --- Handle navigator activation for go_* and approach_unit ---
        # Skip navigator during fast travel — use direct move_* instead
        if state.fast_travel_active and action.startswith("go_"):
            # Convert go_direction to move_direction for fast travel
            direction = action[3:]
            _name_map = {
                "north": "n", "south": "s", "east": "e", "west": "w",
                "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
            }
            short = _name_map.get(direction, direction)
            move_action = f"move_{short}"
            if move_action in ACTION_MAP:
                logger.info("Fast travel: converting %s to %s", action, move_action)
                action = move_action
            else:
                action = "wait"

        if action.startswith("go_"):
            direction = action[3:]  # e.g. "go_north" -> "north", "go_ne" -> "ne"
            # Normalize full names to short names
            _name_map = {
                "north": "n", "south": "s", "east": "e", "west": "w",
                "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
            }
            direction = _name_map.get(direction, direction)
            if direction in DIRECTION_DELTAS:
                self._navigator.activate_direction(direction, state.map_tiles)
                self._last_action = action
                self._log_decision(state, action, reasoning, elapsed_ms, plan_summary)
                self.turn_count += 1
                self._last_state = None  # force fresh extraction — navigator needs current position
                self._last_outcome = None
                return
            else:
                logger.warning("Unknown go direction: %s, substituting wait", direction)
                action = "wait"

        if action.startswith("approach_unit:"):
            try:
                unit_id = int(action.split(":", 1)[1])
                # Check if unit is already adjacent — don't bother navigating
                target = next((u for u in state.nearby_units if u.id == unit_id), None)
                if target and target.distance <= 1:
                    logger.info("Unit %d already adjacent (dist=%d), skipping approach — use talk", unit_id, target.distance)
                    # Don't just wait — execute talk since the whole point of approaching is to talk
                    action = "talk"
                else:
                    self._navigator.activate_approach(unit_id)
                    self._last_action = action
                    self._log_decision(state, action, reasoning, elapsed_ms, plan_summary)
                    self.turn_count += 1
                    self._last_state = None  # force fresh extraction
                    self._last_outcome = None
                    return
            except (ValueError, IndexError):
                logger.warning("Invalid approach_unit action: %s, substituting wait", action)
                action = "wait"

        # Capture before-snapshot
        snap_before = _StateSnapshot.from_game_state(state)

        # Signal to trigger detector that conversation has real content
        if action.startswith("conversation_") and state.conversation_phase == "dialogue":
            self._trigger_detector.conversation_had_content = True

        # Record conversation choice in tracker before executing
        if action.startswith("conversation_"):
            try:
                choice_idx = int(action.split("_", 1)[1])
                choice_match = next(
                    (c for c in state.conversation_choices if c.index == choice_idx),
                    None,
                )
                if choice_match:
                    self._conversation_tracker.record_choice(choice_match.text)
                    # In select_npc phase, the choice text IS the NPC name — resolve hist_fig_id
                    if state.conversation_phase == "select_npc":
                        npc_hf_id = None
                        for u in state.nearby_units:
                            if u.name == choice_match.text and u.hist_fig_id >= 0:
                                npc_hf_id = u.hist_fig_id
                                break
                        self._conversation_tracker.start(choice_match.text, npc_hf_id)
            except (ValueError, StopIteration):
                pass
            # In dialogue phase, try to set NPC name from relationships if not already set
            if state.conversation_phase == "dialogue" and state.npc_relationships:
                npc_hf_id = None
                npc_name = state.npc_relationships[0].name
                for u in state.nearby_units:
                    if u.name == npc_name and u.hist_fig_id >= 0:
                        npc_hf_id = u.hist_fig_id
                        break
                self._conversation_tracker.start(npc_name, npc_hf_id)

        # Execute action (deferred — fires after RPC lock releases)
        self._last_action = action
        self._execute(action)

        # Log decision to JSONL
        self._log_decision(state, action, reasoning, elapsed_ms, plan_summary)
        self.turn_count += 1

        # Wait for the deferred action to take effect
        multi_step = action.startswith("conversation_") or any(
            action.startswith(p) for p in ("pickup_", "drop_", "wield_")
        )
        mode_switch = action in ("travel", "stop_travel")
        wait = 0.8 if mode_switch else (0.6 if multi_step else max(self.poll_interval, 0.3))
        time.sleep(wait)

        # Extract state AFTER action for outcome comparison
        raw_after = self.lua.extract_state()
        state_after = GameState.from_raw(raw_after)

        # Conversation transition fix: if we just executed a conversation action and
        # the focus is still Conversation but choices are empty, DF is loading a new menu
        # (e.g. after "Bypass greeting" or "Change the subject"). Retry a few times.
        if (action.startswith("conversation_")
            and state_after.focus_state
            and "Conversation" in state_after.focus_state
            and state_after.conversation_phase == "none"
            and not state_after.conversation_choices):
            for retry in range(4):
                time.sleep(0.3)
                raw_after = self.lua.extract_state()
                state_after = GameState.from_raw(raw_after)
                if state_after.conversation_phase != "none":
                    logger.info("Conversation transition detected after %d retries — new choices loaded", retry + 1)
                    break
            else:
                logger.debug("Conversation transition: no new choices after retries, conversation likely ended")

        snap_after = _StateSnapshot.from_game_state(state_after)

        # Record outcome
        self._last_outcome = self._outcome_tracker.record(action, snap_before, snap_after)
        if not self._last_outcome.state_changed:
            logger.info("Action '%s' had no effect (consecutive=%d)", action, self._last_outcome.consecutive_no_effect)
        # Reset empty-talk counter when doing something other than repositioning to talk
        if action in ("wait_long", "travel", "stop_travel") or action.startswith("conversation_"):
            self._empty_talk_count = 0

        # Clear announcement buffer after the LLM has seen it (on next non-announcement action)
        if not state_after.showing_announcements and not action.startswith("conversation"):
            self._announcement_buffer.clear()

        # Flush conversation transcript when dialogue ends → write to memory
        if state_after.conversation_phase == "none" and self._conversation_tracker.has_content:
            transcript, npc_name, npc_hf_id = self._conversation_tracker.flush()
            if transcript:
                logger.info("Conversation ended with %s (hf=%s). Transcript:\n%s",
                            npc_name or "NPC", npc_hf_id, transcript[:300])
                if self.memory_writer:
                    self.memory_writer.write_conversation(
                        transcript, npc_name or "unknown NPC", state_after,
                        npc_hist_fig_id=npc_hf_id,
                    )

        # Track position history for area-stuck detection
        if state_after.adventurer_position:
            pos = (state_after.adventurer_position.x, state_after.adventurer_position.y, state_after.adventurer_position.z)
            self._position_history.append(pos)
            if len(self._position_history) > 30:
                self._position_history = self._position_history[-30:]
            # Check if stuck in small area (last 8 positions within small bounding box)
            if len(self._position_history) >= 8:
                recent = self._position_history[-8:]
                xs = [p[0] for p in recent]
                ys = [p[1] for p in recent]
                spread = max(xs) - min(xs) + max(ys) - min(ys)
                if spread <= 10:
                    self._area_stuck_turns += 1
                else:
                    self._area_stuck_turns = 0
            else:
                self._area_stuck_turns = 0

        # Advance memory decay using tick delta
        if self.memory_retriever and snap_after.tick != snap_before.tick:
            tick_delta = max(0, snap_after.tick - snap_before.tick)
            self.memory_retriever.advance_decay(tick_delta)

        # Reuse post-action state as next turn's pre-action state
        self._last_state = state_after

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

    def _handle_goal_revision(self, state: GameState) -> list[str]:
        """Detect triggers and run merged goal revision + planning if needed.

        Returns the list of trigger names that fired this tick (for plan completion checks).
        """
        if self.goal_manager is None:
            return []

        triggers = self._trigger_detector.detect(state, self._last_action)
        if not triggers:
            return []

        for trigger in triggers:
            logger.info("Goal revision triggered: %s", trigger)
            self.goal_manager.revise_and_plan(trigger, state)
            # Hook memory writes to goal-revision triggers
            if self.memory_writer:
                self.memory_writer.on_trigger(trigger, state)
                # Check reflection consolidation threshold
                if self.memory_writer.should_reflect() and self.reflection_engine:
                    logger.info("Reflection threshold reached, running consolidation")
                    self.reflection_engine.reflect(state)
                    self.memory_writer.reset_reflection_counter()

        return triggers

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
        query_parts = [state.site_name or state.region_name or ""]
        if state.hostile_units:
            query_parts.extend(u.race for u in state.hostile_units[:3])
        if state.npc_relationships:
            query_parts.extend(r.name for r in state.npc_relationships[:3])
        # During conversations, include NPC names from choices and nearby units
        if context_type == "conversation":
            for c in state.conversation_choices:
                if c.text and "adventure_option_" not in c.text.lower():
                    query_parts.append(c.text)
            for u in state.nearby_units[:5]:
                if not u.is_hostile and u.name not in query_parts:
                    query_parts.append(u.name)
        query = " ".join(p for p in query_parts if p).strip() or "adventure"

        notes = self.memory_retriever.retrieve(
            query=query,
            context_type=context_type,
            k=5,
            game_tick=state.tick_counter,
        )
        return self.memory_retriever.format_for_prompt(notes)

    def _update_plan(self, state: GameState, triggers: list[str] | None = None) -> str:
        """Manage plan step progression via structured completion checks.

        Returns plan_summary string.
        """
        if self.goal_manager is None:
            return ""

        if not self.goal_manager.has_plan:
            return ""

        # Check structured completion condition
        completed = self.goal_manager.check_step_completion(state, triggers or [])
        if completed:
            step = self.goal_manager.current_step
            if step:
                logger.info("Plan step completed, now on: %s (%s)",
                            step.description, step.completion_type.value)
            elif not self.goal_manager.has_plan:
                logger.info("Plan exhausted; will replan on next trigger")

        return self.goal_manager.plan_summary()

    def _build_goal_summary(self) -> str | None:
        """Build goal context for the system prompt."""
        if self.goal_manager is not None:
            summary = self.goal_manager.goal_summary()
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
        leaf = self.goal_manager.top_goal() if self.goal_manager else None
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
