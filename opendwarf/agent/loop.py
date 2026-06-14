"""Layer-1 tactical loop (slim orchestrator).

Per tick: extract state → run auto-handlers → step the active skill (if any) →
otherwise assemble the prompt, ask the LLM for an intent, and dispatch it via
the action registry. Deterministic movement/travel/menu work happens inside
skills with no LLM calls in between. Cross-turn continuity comes from the
scratchpad and an outcome-annotated action history.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from opendwarf.actions.registry import ActionKind, default_registry
from opendwarf.actions.skills import SkillContext, SkillResult, SkillStatus, UnstickSkill
from opendwarf.agent.death_handler import handle_death
from opendwarf.agent.prompt_assembler import PromptAssembler
from opendwarf.agent.prompts import build_system_bundle, build_turn_prompt
from opendwarf.agent.scratchpad import Scratchpad
from opendwarf.behaviors import interrupts as interrupts_mod
from opendwarf.behaviors.base import BehaviorStatus
from opendwarf.behaviors.grind_combat import GrindCombatBehavior
from opendwarf.behaviors.interrupts import Interrupt
from opendwarf.behaviors.journey import JourneyBehavior
from opendwarf.behaviors.patrol import PatrolBehavior
from opendwarf.behaviors.policy import Policy
from opendwarf.goals import survival as survival_gates_mod
from opendwarf.memory.asked_topics import AskedTopics
from opendwarf.memory.knowledge import KnowledgePack
from opendwarf.memory.rumor_extract import RumorExtractor
from opendwarf.spatial.chunk_map import ChunkMap
from opendwarf.spatial.extractor import MapExtractor
from opendwarf.spatial.pathfinder import Pathfinder
from opendwarf.spatial.sites import SiteRegistry
from opendwarf.state.game_state import GameState

if TYPE_CHECKING:
    from opendwarf.dfhack.lua_executor import LuaExecutor
    from opendwarf.goals.manager import GoalManager
    from opendwarf.llm.base import LLMClient
    from opendwarf.memory.postmortems import PostmortemBuffer
    from opendwarf.memory.reflection import ReflectionEngine
    from opendwarf.memory.retriever import MemoryRetriever
    from opendwarf.memory.writer import MemoryWriter

logger = logging.getLogger(__name__)

_SEVERE_WOUNDS = ("severed", "missing", "bleeding")

# Outcome substrings that indicate an action failed and should be temporarily banned.
_FAILURE_SUBSTRINGS = ("no path", "blocked", "no effect", "ERROR", "FAILED", "did not start",
                       "no quests found", "unreachable")

# Actions that are never banned (escape hatches).
_NEVER_BAN = frozenset({"wait", "escape", "read_screen"})

# How many turns a failure suppresses an action.
_BAN_WINDOW = 4

# Hard cap on consecutive RUNNING ticks for any one skill — a stuck skill must
# never deadlock the whole agent (observed: FastTravelController waiting forever
# for travel mode to engage while obstructed).
_SKILL_TICK_CAP = 300


class _EscalateSignal(Exception):
    """Internal sentinel: tactical decision asked to escalate to a stronger model."""


def _build_death_cause(state: GameState) -> str:
    """Produce a concise human-readable cause-of-death string from game state."""
    parts: list[str] = []
    if state.hostile_units:
        races = ", ".join(sorted({u.race for u in state.hostile_units[:3]}))
        parts.append(f"killed by {races}")
    if state.wounds:
        severe = [w for w in state.wounds if any(k in w.status.lower() for k in _SEVERE_WOUNDS)]
        if severe:
            parts.append(f"wounds: {', '.join(str(w) for w in severe[:3])}")
    if state.hungry_critical:
        parts.append("starvation")
    if state.thirsty_critical:
        parts.append("dehydration")
    if not parts:
        parts.append("unknown cause")
    return "; ".join(parts)


def _normal_play_focus(state: GameState) -> bool:
    """Free exploration — safe to offer a long-running autopilot behavior."""
    return (state.conversation_phase == "none"
            and not state.fast_travel_active
            and not state.hostile_units
            and not state.showing_announcements
            and interrupts_mod.is_known_focus(state.focus_state))

# Focus patterns the loop handles natively — defined once in interrupts.py;
# imported here as the single source of truth (both copies used to diverge).
# Reference via interrupts_mod.KNOWN_FOCUS_PATTERNS / interrupts_mod.is_known_focus.


# ----------------------------------------------------------------------
# Minimal before/after snapshot for measuring single-action outcomes
# ----------------------------------------------------------------------

@dataclass
class _Snapshot:
    tick: int
    total_move: int
    position: str
    menu: str
    conv_phase: str
    inv: int

    @staticmethod
    def of(s: GameState) -> "_Snapshot":
        pos = str(s.fast_travel_army_pos) if s.fast_travel_active and s.fast_travel_army_pos else str(s.adventurer_position)
        return _Snapshot(s.tick_counter, s.total_move, pos, s.menu_state, s.conversation_phase, len(s.inventory))

    def moved(self, other: "_Snapshot") -> bool:
        if self.total_move >= 0 and other.total_move >= 0:
            return self.total_move != other.total_move
        return self.position != other.position

    def changed(self, other: "_Snapshot") -> bool:
        return (self.tick != other.tick or self.position != other.position
                or self.menu != other.menu or self.conv_phase != other.conv_phase
                or self.inv != other.inv)


# ----------------------------------------------------------------------
# Conversation transcript accumulator
# ----------------------------------------------------------------------

class _ConversationTracker:
    def __init__(self) -> None:
        self._entries: list[str] = []
        self.npc_name: str | None = None
        self.npc_hist_fig_id: int | None = None
        self.active = False

    def record_choice(self, text: str) -> None:
        self._entries.append(f"YOU: {text}")

    def record_npc_response(self, lines: list[str]) -> None:
        for line in lines:
            s = line.strip()
            if not s:
                continue
            if s.startswith("You:") or s.startswith("You :"):
                self._entries.append(f"YOU: {s[s.index(':') + 1:].strip()}")
            else:
                self._entries.append(f"NPC: {s}")

    def start(self, name: str | None, hf_id: int | None = None) -> None:
        self.active = True
        if self.npc_name is None:
            self.npc_name, self.npc_hist_fig_id = name, hf_id

    def flush(self) -> tuple[str | None, str | None, int | None]:
        self.active = False
        if not self._entries:
            return None, None, None
        transcript = "\n".join(self._entries)
        name, hf = self.npc_name, self.npc_hist_fig_id
        self._entries.clear()
        self.npc_name = self.npc_hist_fig_id = None
        return transcript, name, hf

    @property
    def has_content(self) -> bool:
        return bool(self._entries)

    def format_for_prompt(self) -> str:
        if not self._entries:
            return ""
        return (f"-- Current Conversation (with {self.npc_name or 'NPC'}) --\n"
                + "\n".join(f"  {e}" for e in self._entries[-10:]))


# ----------------------------------------------------------------------
# Conversation re-engagement guard
# ----------------------------------------------------------------------

_NPC_TALK_LIMIT = 5        # consecutive conversation actions w/ same NPC -> exhausted
_NPC_EXHAUST_COOLDOWN = 12  # turns an exhausted NPC's talk actions stay banned


class _ConversationGuard:
    """Detects unproductive re-engagement of the same NPC and bans their talk
    actions for a cooldown, so the agent moves on instead of looping.

    NPC identity key: str(hist_fig_id) when >= 0, else "name:<npc_name>".
    """

    def __init__(self) -> None:
        self._target: str | None = None      # NPC identity of current talk streak
        self._streak: int = 0                 # consecutive conversation actions w/ _target
        self._exhausted: dict[str, int] = {}  # npc_key -> turn it became exhausted

    @staticmethod
    def key(hist_fig_id: int | None, name: str | None) -> str | None:
        if hist_fig_id is not None and hist_fig_id >= 0:
            return str(hist_fig_id)
        if name:
            return f"name:{name}"
        return None

    def note_conversation(self, npc_key: str | None, turn: int) -> None:
        """Record one conversation action (talk / talk_to / conversation pick)
        aimed at npc_key. Marks the NPC exhausted when the streak hits the limit."""
        if npc_key is None:
            # Unknown target — count it against the current streak target if any,
            # else ignore (can't attribute).
            npc_key = self._target
            if npc_key is None:
                return
        if npc_key == self._target:
            self._streak += 1
        else:
            self._target, self._streak = npc_key, 1
        if self._streak >= _NPC_TALK_LIMIT:
            self._exhausted[npc_key] = turn

    def note_productive(self) -> None:
        """Conversation produced new info (memory transcript written) — reset the
        streak so a genuinely useful chat is not penalised. Does NOT clear an
        already-exhausted mark (cooldown still applies)."""
        self._streak = 0

    def note_other_action(self) -> None:
        """A non-conversation, position-changing action happened — reset streak."""
        self._target, self._streak = None, 0

    def is_exhausted(self, npc_key: str | None, turn: int) -> bool:
        if npc_key is None:
            return False
        t = self._exhausted.get(npc_key)
        if t is None:
            return False
        if turn - t >= _NPC_EXHAUST_COOLDOWN:
            del self._exhausted[npc_key]
            return False
        return True

    def streak_for(self, npc_key: str | None) -> int:
        return self._streak if npc_key == self._target else 0


# ----------------------------------------------------------------------
# Goal-revision trigger detection (ported, unchanged semantics)
# ----------------------------------------------------------------------

_HEALTH_THRESHOLDS = (25, 10)


class _TriggerDetector:
    def __init__(self) -> None:
        self._prev: GameState | None = None
        self._hit: set[int] = set()
        self._last_site: str | None = None
        self._started = False
        self.conversation_had_content = False

    def detect(self, state: GameState, last_action: str | None) -> list[str]:
        triggers: list[str] = []
        prev = self._prev
        if not self._started:
            triggers.append("session_start")
            self._started = True
        if prev is not None:
            if prev.in_combat and not state.in_combat:
                triggers.append("combat_resolved")
            if prev.conversation_phase != "none" and state.conversation_phase == "none":
                if self.conversation_had_content:
                    triggers.append("dialogue_ended")
                self.conversation_had_content = False
            if (prev.conversation_phase == "none" and state.conversation_phase != "none"
                    and last_action not in ("talk", None)):
                triggers.append("dialogue_forced")
            for t in _HEALTH_THRESHOLDS:
                if t not in self._hit and prev.health_pct >= t and state.health_pct < t:
                    triggers.append(f"health_threshold_{t}")
                    self._hit.add(t)
            for t in list(self._hit):
                if state.health_pct >= t + 10:
                    self._hit.discard(t)
            cur = state.site_name or state.region_name
            if cur and cur != self._last_site and self._last_site is not None:
                triggers.append("location_discovered")
            self._last_site = cur
        if last_action == "wait_long":
            triggers.append("wait_long")
        self._prev = state
        return triggers


# ----------------------------------------------------------------------
# The loop
# ----------------------------------------------------------------------

class TacticalLoop:
    def __init__(
        self,
        lua: "LuaExecutor",
        llm: "LLMClient",
        poll_interval: float = 0.5,
        goal: str | None = None,
        goal_manager: "GoalManager | None" = None,
        memory_writer: "MemoryWriter | None" = None,
        memory_retriever: "MemoryRetriever | None" = None,
        postmortem_buffer: "PostmortemBuffer | None" = None,
        reflection_engine: "ReflectionEngine | None" = None,
        df_mechanics: str = "",
        logs_dir: "Path | None" = None,
        spatial_dir: "Path | None" = None,
        scratchpad_path: "Path | None" = None,
        policy_path: "Path | None" = None,
        asked_topics_path: "Path | None" = None,
        knowledge_pack: "KnowledgePack | None" = None,
    ):
        self.lua = lua
        self.llm = llm
        self.poll_interval = poll_interval
        self._initial_goal_str = goal
        self.goal_manager = goal_manager
        self.memory_writer = memory_writer
        self.memory_retriever = memory_retriever
        self.postmortem_buffer = postmortem_buffer
        self.reflection_engine = reflection_engine
        self.df_mechanics = df_mechanics
        self.knowledge_pack = knowledge_pack

        self.running = False
        self.turn_count = 0
        self._last_action: str | None = None
        self._last_state: GameState | None = None
        self._trigger_detector = _TriggerDetector()
        self._conv = _ConversationTracker()
        self._conv_guard = _ConversationGuard()
        self._announcements: list[str] = []
        self._history: deque[str] = deque(maxlen=10)
        self._pending_triggers: list[str] = []
        self._empty_talk_count = 0
        # Failure tracker: maps canonical action → (turn it failed, outcome text)
        self._recent_failures: dict[str, tuple[int, str]] = {}
        self._skill_ticks = 0  # consecutive RUNNING ticks of the active skill

        # Spatial + actions
        spatial_dir = spatial_dir or Path("spatial")
        self._chunk_map = ChunkMap.load(spatial_dir / "chunks.json")
        self._pathfinder = Pathfinder(self._chunk_map)
        self._extractor = MapExtractor(lua, self._chunk_map)
        self._skill_ctx = SkillContext(lua, self._chunk_map, self._pathfinder, self._extractor)
        # Site registry (spatial Layer 3): observed + rumored sites, resolution
        # table for journey:<rumor_id>. Folded from nearby-sites each tick and
        # from conversation rumor extraction on dialogue_ended.
        self._site_registry = SiteRegistry.load(spatial_dir / "sites.json")
        self._rumor_extractor = RumorExtractor(llm, lua)
        self._registry = default_registry()
        self._active_skill = None  # type: ignore[assignment]
        self._screen_text: str = ""  # populated by read_screen or unknown-screen handler
        self._escape_hatch_count: int = 0
        # UnstickSkill: attempted once per unknown-focus episode before LLM escape hatch.
        # Reset when focus becomes known again.
        self._unstick_attempted: bool = False
        self._last_unstick_focus: str | None = None

        # Autopilot behaviors (NORTHSTAR M1). At most one runs at a time; on
        # interrupt it is suspended (kept) so the LLM can `resume` it.
        self._active_behavior = None  # type: ignore[assignment]
        self._suspended_behavior = None  # type: ignore[assignment]
        self._interrupt: "Interrupt | None" = None  # set when a behavior was just suspended

        # Scratchpad
        self._scratchpad = Scratchpad(scratchpad_path or (spatial_dir.parent / "memory" / "scratchpad.md"))

        # Asked-topics dedup: persisted per-NPC record of already-raised topics
        self._asked_topics = AskedTopics(
            asked_topics_path or (spatial_dir.parent / "memory" / "asked_topics.json")
        )

        # Give skills access to the loop-shared conversation bookkeeping
        # (ConverseSkill records picked topics + transcript exactly like the LLM path).
        self._skill_ctx.asked_topics = self._asked_topics
        self._skill_ctx.conv_tracker = self._conv

        # Autopilot policy (standing orders the LLM revises via the "policy" decision key)
        self._policy_path = policy_path or Path("goals") / "policy.json"
        self.policy = Policy.load(self._policy_path)

        log_path = (logs_dir or Path("logs") / f"session_{datetime.now():%Y%m%d_%H%M%S}") / "decisions.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = log_path.open("a", encoding="utf-8")
        self._session_log_dir: Path = log_path.parent
        # Prevent double-firing the death sequence on consecutive dead-state ticks.
        self._death_handled: bool = False
        logger.info("Decision log: %s", log_path)

        # Prompt assembler: builds dynamic prompt blocks each turn.
        self._prompt_assembler = PromptAssembler(
            conv=self._conv,
            conv_guard=self._conv_guard,
            asked_topics=self._asked_topics,
            scratchpad=self._scratchpad,
            knowledge_pack=self.knowledge_pack,
            memory_retriever=self.memory_retriever,
            log_event_fn=self._log_event,
        )

    # ------------------------------------------------------------------

    def run(self) -> None:
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

    def _fresh_state(self) -> GameState:
        return GameState.from_raw(self.lua.extract_state())

    def _tactical_decide(self, bundle) -> dict | None:
        """Call the LLM for a tactical decision with one escalation retry.

        On failure or when the decision contains ``{"escalate": true}``, re-asks
        once with ``caller="tactical_escalated"`` (maps to the strongest configured
        model via ``OPENDWARF_ANTHROPIC_MODEL_TACTICAL_ESCALATED`` / the OpenRouter
        equivalent). If the escalated call also fails, returns ``None`` so the
        caller can fall back to the wait behaviour.
        """
        try:
            decision = self.llm.decide(bundle, caller="tactical")
            if decision.get("escalate"):
                logger.info("Tactical decision requested escalation; re-asking with escalated caller")
                raise _EscalateSignal()
            return decision
        except _EscalateSignal:
            pass
        except Exception:
            logger.exception("Tactical LLM call failed; escalating")

        # --- escalated attempt ---
        try:
            logger.info("Escalated tactical LLM call")
            return self.llm.decide(bundle, caller="tactical_escalated")
        except Exception:
            logger.exception("Escalated tactical LLM call also failed; waiting")
            return None

    def _tick(self) -> None:
        state = self._last_state if self._last_state is not None else self._fresh_state()

        # --- death detection (runs before anything else) ---
        if state.adventurer_dead and not self._death_handled:
            self._handle_death(state)
            return

        if not state.is_adventure_mode or not state.taking_input:
            self._last_state = None
            time.sleep(self.poll_interval)
            return

        # --- fold observed sites into the registry (cheap; ≤5 sites) ---
        self._record_observed_sites(state)

        # --- active behavior: interrupt check is the single source of truth ---
        # Runs BEFORE auto-handlers so a forced conversation/announcement/unknown
        # screen suspends the behavior and reaches the LLM (with the digest),
        # rather than being silently paged away underneath it.
        if self._active_behavior is not None:
            self._run_behavior_tick(state)
            return

        # --- auto-handlers (ordered) ---
        if self._auto_handle(state):
            return

        # --- keep the spatial map fresh ---
        self._extractor.ensure_fresh(state)

        # --- active skill stepping ---
        if self._active_skill is not None:
            self._step_skill(state)
            return

        # --- goal / plan / memory ---
        triggers = self._handle_goal_revision(state)
        plan_summary = self.goal_manager.plan_summary() if self.goal_manager and self.goal_manager.has_plan else ""
        if self.goal_manager and self.goal_manager.has_plan:
            self.goal_manager.check_step_completion(state, triggers, last_action=self._last_action)
            plan_summary = self.goal_manager.plan_summary()
        goal_summary = self._goal_summary()

        # --- render wide map into the summary ---
        view = self._extractor.render_view(state, radius=10)
        if view:
            state.map_tiles = view

        # --- build prompt ---
        banned: set[str] = self._build_banned(state)
        annotations = self._conversation_annotations(state)
        action_block = self._registry.build_block(state, banned, annotations) + self._autopilot_action_lines(state)
        autopilot_block = self._autopilot_status_block()
        hint = self._build_hint(state)
        memory_block = self._retrieve_memories(state)
        announcement_block = self._announcement_block(state)
        history_block = ("-- Recent Actions & Outcomes --\n" + "\n".join(f"  {h}" for h in self._history)
                         if self._history else "")
        scratchpad_block = self._scratchpad.format_for_prompt()

        summary = state.summary()
        postmortems = self.postmortem_buffer.load() if self.postmortem_buffer else ""
        bundle = build_system_bundle(goal_summary, self.df_mechanics, postmortems)
        screen_block = self._screen_text
        self._screen_text = ""  # consume
        knowledge_block = self._build_knowledge_block(state, goal_summary or "")
        bundle.user = build_turn_prompt(
            summary, action_block, plan_summary, memory_block, hint,
            announcement_block=announcement_block, decision_history=history_block,
            scratchpad_block=scratchpad_block, screen_block=screen_block,
            policy_block=self.policy.to_prompt_line(), autopilot_block=autopilot_block,
            knowledge_block=knowledge_block,
        )
        logger.info("Turn %d:\n%s", self.turn_count, summary)

        t0 = time.monotonic()
        decision = self._tactical_decide(bundle)
        if decision is None:
            self._last_state = None
            time.sleep(1.0)
            return
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        action = decision.get("action", "wait")
        reasoning = decision.get("reasoning", "")
        self._scratchpad.update(decision.get("scratchpad"))
        if "policy" in decision:
            self._apply_policy_revision(decision["policy"], state)
        logger.info("Decision: %s — %s", action, reasoning)

        if self._handle_autopilot_action(action, reasoning, state, elapsed_ms, plan_summary):
            return
        self._dispatch(action, reasoning, state, elapsed_ms, plan_summary)

    # ------------------------------------------------------------------
    # Auto-handlers
    # ------------------------------------------------------------------

    def _auto_handle(self, state: GameState) -> bool:
        """Handle screens that don't need the LLM. Returns True if it acted."""
        if self._conv.active is False and state.conversation_phase != "none":
            self._conv.active = True

        if state.focus_state and "Help" in state.focus_state:
            try:
                self.lua.run_script("opendwarf--clickok")
            except Exception:
                self._execute_key("SELECT")
            return self._after_auto(0.3)

        if state.showing_announcements:
            self._record_announcements(state)
            self._execute_key("SELECT")
            return self._after_auto(0.3)

        if state.focus_state and "Look" in state.focus_state:
            self._execute_key("LEAVESCREEN")
            return self._after_auto(0.3)

        # v50 modal dialogs ("Okay"-button popups: divination, quest messages)
        # draw OVER dungeonmode/Default — no separate viewscreen, no
        # main_interface widget flagged open — and swallow ALL input. Screen
        # scan + click is the only reliable detection. Runs every tick, so a
        # dialog appearing mid-skill is dismissed before the skill flails.
        if (state.focus_state == "dungeonmode/Default"
                and state.conversation_phase == "none"
                and not state.showing_announcements
                and not state.fast_travel_active
                and self._dismiss_modal()):
            return self._after_auto(0.5)

        if state.conversation_phase == "select_npc" and state.conversation_choices:
            # Separate named NPC choices from system-option choices.
            # Named choices: text does NOT start with "adventure_option_".
            named = [c for c in state.conversation_choices
                     if "adventure_option_" not in c.text.lower()]
            system = [c for c in state.conversation_choices
                      if "adventure_option_" in c.text.lower()]
            if named:
                # LLM sees the named NPCs and picks; do nothing here.
                pass
            else:
                # No named NPCs — check for start_shoutingst (talk to whoever is closest).
                # In v50+ this is the only way to talk when NPCs are not in direct-talk range.
                shout = next((c for c in system if "start_shout" in c.text.lower()), None)
                if shout is not None:
                    self.lua.execute_action(f"conversation:{shout.index}")
                    if self._last_action == "talk":
                        self._empty_talk_count = 0
                    return self._after_auto(0.4)
                # Only non-shout system options (e.g. assume_identityst alone) — escape
                self._execute_key("LEAVESCREEN")
                if self._last_action == "talk":
                    self._empty_talk_count += 1
                return self._after_auto(0.3)

        # Unknown-screen detection (4.2 / NORTHSTAR M5):
        # On first unknown focus: run UnstickSkill once (deterministic recovery ladder
        # — dismiss DFHack screens, LEAVESCREEN×2, focus-derived key candidates).
        # Only if that already failed (or was skipped) do we fall through to the
        # LLM escape hatch which includes the inspect_ui summary + key candidates.
        if (state.focus_state
                and not state.fast_travel_active
                and self._active_skill is None
                and not interrupts_mod.is_known_focus(state.focus_state)):
            cur_focus = state.focus_state
            if not self._unstick_attempted or self._last_unstick_focus != cur_focus:
                # First encounter with this unknown focus: activate UnstickSkill.
                self._unstick_attempted = True
                self._last_unstick_focus = cur_focus
                logger.info("Unknown focus %r — activating UnstickSkill", cur_focus)
                self._active_skill = UnstickSkill(self._skill_ctx, wedged_focus=cur_focus)
                self._log_event("unstick_started", focus=cur_focus, tick=state.tick_counter)
                self._last_state = None
                return True  # UnstickSkill will run next tick via _step_skill
            else:
                # UnstickSkill already ran for this focus and failed; use LLM escape hatch.
                self._trigger_escape_hatch(state)
        elif state.focus_state and interrupts_mod.is_known_focus(state.focus_state):
            # Focus is now known — reset unstick state for the next wedge.
            self._unstick_attempted = False
            self._last_unstick_focus = None
        return False

    def _trigger_escape_hatch(self, state: GameState) -> None:
        """Read the screen and store it so the LLM gets the full picture.

        Also calls inspect_ui() to enrich the prompt with the viewscreen stack,
        travel fields, and key candidates derived from the focus-string tokens —
        so the LLM has actionable information rather than just raw screen text.
        """
        if self._screen_text:
            return  # already populated this tick
        try:
            data = self.lua.extract_screen_text()
            rows = data.get("rows", [])
            focus_list = data.get("focus", [state.focus_state or "unknown"])
            focus_str = ", ".join(str(f) for f in focus_list)
            self._escape_hatch_count += 1
            logger.warning("Escape hatch triggered (episode #%d): focus=%s",
                           self._escape_hatch_count, state.focus_state)
            self._log_escape_hatch(state, focus_list)

            # Enrich with inspect_ui snapshot.
            inspect_lines: list[str] = []
            try:
                ui = self.lua.inspect_ui()
                if ui.get("viewscreen_stack"):
                    inspect_lines.append(f"  viewscreen_stack: {ui['viewscreen_stack']}")
                if ui.get("menu"):
                    m = ui["menu"]
                    inspect_lines.append(f"  menu: {m.get('name','?')} ({m.get('value','?')})")
                if ui.get("player_control_state"):
                    c = ui["player_control_state"]
                    inspect_lines.append(f"  control_state: {c.get('name','?')}")
                if ui.get("travel"):
                    t = ui["travel"]
                    inspect_lines.append(
                        f"  travel: origin=({t.get('origin_x')},{t.get('origin_y')})"
                        f" army_id={t.get('player_army_id')}"
                    )
                if ui.get("message"):
                    inspect_lines.append(f"  message: {ui['message']}")
            except Exception:  # noqa: BLE001
                inspect_lines.append("  (inspect_ui failed)")

            # Key candidates from focus tokens.
            key_candidates: list[str] = []
            try:
                focus_raw = state.focus_state or ""
                tokens = [p.strip().upper() for p in focus_raw.replace("/", " ").replace("_", " ").split()
                          if len(p.strip()) >= 3]
                seen: set[str] = set()
                for token in tokens:
                    for k in self.lua.find_keys(token)[:8]:
                        if k not in seen:
                            seen.add(k)
                            key_candidates.append(k)
                key_candidates = key_candidates[:10]
            except Exception:  # noqa: BLE001
                pass

            self._screen_text = (
                f"UNRECOGNIZED SCREEN — focus: {focus_str}\n"
                + "\n".join(rows[:30])
                + ("\n\nUI Snapshot:\n" + "\n".join(inspect_lines) if inspect_lines else "")
                + (f"\n\nKey candidates (from focus tokens): {key_candidates}" if key_candidates else "")
            )
        except Exception:  # noqa: BLE001
            logger.exception("Failed to read screen for escape hatch")

    def _log_event(self, event: str, **fields) -> None:
        entry = {"event": event, "turn": self.turn_count, **fields}
        self._log_file.write(json.dumps(entry) + "\n")
        self._log_file.flush()

    def _log_escape_hatch(self, state: GameState, focus_list: list) -> None:
        entry = {
            "event": "escape_hatch",
            "turn": self.turn_count,
            "tick": state.tick_counter,
            "focus": focus_list,
            "episode": self._escape_hatch_count,
        }
        self._log_file.write(json.dumps(entry) + "\n")
        self._log_file.flush()

    def _apply_policy_revision(self, updates: object, state: GameState) -> None:
        diff = self.policy.revise(updates)  # type: ignore[arg-type]
        if not diff:
            return
        self.policy.save(self._policy_path)
        logger.info("Policy revised: %s", diff)
        entry = {
            "event": "policy_revised",
            "turn": self.turn_count,
            "tick": state.tick_counter,
            "diff": diff,
        }
        self._log_file.write(json.dumps(entry) + "\n")
        self._log_file.flush()

    def _dismiss_modal(self) -> bool:
        """Scan the screen for an 'Okay' modal button and click it if present."""
        try:
            lines = self.lua.run_script("opendwarf--clickok")
        except Exception:
            logger.debug("Modal scan failed", exc_info=True)
            return False
        # DFHack's json.encode pretty-prints; output may arrive as one
        # multiline fragment or split across fragments — parse the join.
        text = "\n".join(lines or []).strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return False
        try:
            payload = json.loads(text[start:end + 1])
        except ValueError:
            return False
        if payload.get("found"):
            logger.info("Auto-dismissed modal dialog (Okay at %s,%s)",
                        payload.get("x"), payload.get("y"))
            self._history.append("(auto) dismissed a modal dialog by clicking Okay")
            return True
        return False

    def _after_auto(self, wait: float) -> bool:
        self._last_state = None
        time.sleep(wait)
        return True

    # ------------------------------------------------------------------
    # Skill stepping
    # ------------------------------------------------------------------

    def _step_skill(self, state: GameState) -> None:
        skill = self._active_skill
        result = skill.step(state)
        if result.status is SkillStatus.RUNNING:
            self._skill_ticks += 1
            if self._skill_ticks >= _SKILL_TICK_CAP:
                result = SkillResult.interrupted(
                    f"skill watchdog: still RUNNING after {self._skill_ticks} ticks — aborted")
            else:
                self._last_state = None
                time.sleep(0.35)
                return
        # Terminal
        self._skill_ticks = 0
        name = getattr(skill, "name", "skill")
        self._active_skill = None
        outcome = result.outcome or result.status.value
        self._history.append(f"{name}: {outcome}")
        logger.info("Skill %s ended (%s): %s", name, result.status.value, outcome)
        self._record_outcome(self._last_action or name, outcome)
        if result.status is SkillStatus.DONE and name in ("route", "fast_travel"):
            self._pending_triggers.append("goto_arrived")
        # TalkToSkill exposes the selected NPC so we can prime the conversation tracker
        if result.status is SkillStatus.DONE and name == "talk_to":
            npc_name = getattr(skill, "selected_npc_name", None)
            npc_hf = getattr(skill, "selected_npc_hf_id", None)
            if npc_name:
                self._conv.start(npc_name, npc_hf)
        # ConverseSkill ran the whole dialogue itself; flush its transcript to
        # memory (same path the LLM conversation flow uses).
        if name == "converse":
            self._flush_conversation(state)
        # UnstickSkill INTERRUPTED → enrich escape-hatch prompt with the
        # inspect_ui summary and key candidates so the LLM has full context.
        if name == "unstick" and result.status is SkillStatus.INTERRUPTED:
            unstick_context = f"RECOVERY ATTEMPTED — {outcome}"
            self._screen_text = (unstick_context + "\n\n" + self._screen_text
                                 if self._screen_text else unstick_context)
            self._log_event("unstick_failed", outcome=outcome, tick=state.tick_counter)
        self._last_state = None  # re-extract; auto-handlers/LLM run next tick

    # ------------------------------------------------------------------
    # Behaviors (autopilot under policy — NORTHSTAR M1)
    # ------------------------------------------------------------------

    def _run_behavior_tick(self, state: GameState) -> None:
        behavior = self._active_behavior
        self._extractor.ensure_fresh(state)

        intr = interrupts_mod.check(state, self.policy, behavior)
        if intr is not None:
            self._suspend_behavior(intr)
            return

        # Interrupt check cleared us, but a routine announcement (combat log) may
        # be up that the behavior opted to page itself (handles_announcements).
        # Record it for observability, dismiss it, and stay on autopilot — the
        # behavior can't act while the announcement viewer blocks input anyway.
        if state.showing_announcements:
            self._record_announcements(state)
            self._execute_key("SELECT")
            self._last_state = None
            time.sleep(0.3)
            return

        result = behavior.step(state)
        if result.status is BehaviorStatus.RUNNING:
            self._last_state = None
            time.sleep(0.35)
            return
        if result.status is BehaviorStatus.NEEDS_LLM:
            self._suspend_behavior(Interrupt(interrupts_mod.InterruptReason.STALLED, result.outcome))
            return
        # DONE
        self._end_behavior(state, result.outcome, ended=True)

    def _suspend_behavior(self, intr: "Interrupt") -> None:
        """Park the active behavior (keep it) and surface the interrupt + digest
        to the next LLM turn. The next tick has no active behavior, so the loop
        falls through to the normal LLM decision path."""
        behavior = self._active_behavior
        logger.info("Behavior %s suspended: %s", behavior.name, intr)
        self._suspended_behavior = behavior
        self._active_behavior = None
        self._interrupt = intr
        self._log_event("behavior_suspended", reason=str(intr),
                        digest=behavior.digest.one_line(behavior_name=behavior.name))
        self._last_state = None  # re-extract; auto-handlers + LLM run next tick

    def _end_behavior(self, state: GameState, outcome: str, *, ended: bool) -> None:
        """Terminate a behavior (DONE or aborted): record digest to history, write
        one episodic memory note, and clear the slot."""
        behavior = self._active_behavior or self._suspended_behavior
        if behavior is None:
            return
        one_line = behavior.digest.one_line(behavior_name=behavior.name)
        self._history.append(f"{one_line} — {outcome}")
        logger.info("Behavior %s ended: %s", behavior.name, outcome)
        self._log_event("behavior_ended", reason=outcome, digest=one_line)
        if self.memory_writer is not None and not behavior.digest.is_empty:
            try:
                self.memory_writer.write_observation(
                    f"Autopilot {behavior.name}: {one_line}. Outcome: {outcome}.",
                    tags=["autopilot", behavior.name], state=state)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to write behavior memory note")
        self._active_behavior = None
        self._suspended_behavior = None
        self._interrupt = None
        self._last_state = None

    def _autopilot_action_lines(self, state: GameState) -> str:
        """Append autopilot actions to the action block: always offer `patrol`;
        offer `resume`/`abort_behavior` only when a behavior is suspended."""
        lines: list[str] = []
        if self._suspended_behavior is not None:
            name = self._suspended_behavior.name
            lines.append(f"  resume — continue the suspended {name} autopilot")
            lines.append(f"  abort_behavior — discard the suspended {name} autopilot")
        elif _normal_play_focus(state):
            lines.append("  patrol — auto-walk a loop around here unattended (handles food/water "
                         "per policy; hands back on combat/dialogue/low health). Optional radius: patrol:12")
            if self.policy.engage_species_allow or self.policy.engage_tier_max:
                lines.append("  grind_combat — hunt & fight policy-authorized hostiles near here to "
                             "train combat skills, eating/drinking per policy; hands back on "
                             "unauthorized/excess hostiles or low health. Optional radius and stop "
                             "condition: grind_combat:12 or grind_combat:12:AXE:8 (stop at AXE lv8)")
            else:
                lines.append("  (grind_combat unavailable: set policy.engage_species_allow or "
                             "policy.engage_tier_max first so the autopilot knows what it may fight)")
            distant = [s for s in state.nearby_sites
                       if s.distance and s.distance > 2 and s.name != state.site_name]
            if distant:
                ex = distant[0]
                lines.append(
                    "  journey:<site_id> — travel across the world to a distant site, "
                    "routing around terrain barriers and re-entering travel after each "
                    "interruption; hands back on encounters/critical needs. "
                    f"e.g. journey:{ex.id} ({ex.name}, {ex.distance} tiles {ex.direction})")
            rumor_block = self._site_registry.format_for_prompt()
            if rumor_block:
                lines.append(rumor_block)
        if not lines:
            return ""
        return "\nAutopilot (runs without further LLM turns until interrupted):\n" + "\n".join(lines)

    def _autopilot_status_block(self) -> str:
        if self._interrupt is None or self._suspended_behavior is None:
            return ""
        behavior = self._suspended_behavior
        return (f"-- Autopilot interrupted: {self._interrupt} --\n"
                + behavior.digest.render(behavior_name=behavior.name)
                + "\nChoose `resume` to continue it, `abort_behavior` to drop it, or any other action "
                  "(the behavior stays parked and `resume` re-arms it).")

    @staticmethod
    def _parse_grind_args(action: str) -> tuple[int, dict]:
        """Parse grind_combat[:radius[:SKILL:level]] | grind_combat:radius:max_ticks:N.

        Returns (radius, until_dict). Malformed segments fall back to defaults so a
        bad LLM intent never crashes the turn.
        """
        radius, until = 12, {}
        parts = action.split(":")[1:]  # drop the "grind_combat" head
        if parts and parts[0].strip():
            try:
                radius = max(4, int(parts[0]))
            except ValueError:
                pass
        # Optional stop condition: "<SKILL> <level>" or "max_ticks <n>" / "max_kills <n>".
        if len(parts) >= 3:
            key = parts[1].strip()
            try:
                until[key] = int(parts[2])
            except ValueError:
                pass
        return radius, until

    def _resolve_journey_dest(
        self, arg: str, state: GameState
    ) -> tuple[int | None, str, tuple[int, int] | None]:
        """Resolve a journey argument to (site_id, site_name, world_pos). The arg
        may be a nearby-site id/name OR a registry rumor_id; world_pos (embark-tile
        centre) is filled from the registry so a distant rumored site can be
        steered to even before it enters the nearby-site list. Any field may be
        empty/None when unmatched (a numeric arg still yields its id)."""
        if not arg:
            return None, "", None

        # 1. Nearby sites take precedence (live bearing/distance is most accurate).
        if arg.lstrip("-").isdigit():
            sid = int(arg)
            for s in state.nearby_sites:
                if s.id == sid:
                    return sid, s.name, None
        else:
            low = arg.lower()
            for s in state.nearby_sites:
                if s.name.lower() == low:
                    return s.id, s.name, None
            for s in state.nearby_sites:
                if low in s.name.lower():
                    return s.id, s.name, None

        # 2. Site registry (rumored/known-but-distant sites with a stored position).
        entry = self._site_registry.get(arg)
        if entry is not None:
            return entry.site_id, entry.name, entry.world_pos

        # 3. Bare numeric id with no registry entry — keep it so the behavior can
        #    still steer once the site enters range.
        if arg.lstrip("-").isdigit():
            return int(arg), "", None
        return None, "", None

    def _handle_autopilot_action(self, action: str, reasoning: str, state: GameState,
                                 elapsed_ms: int, plan_summary: str) -> bool:
        """Intercept autopilot control actions before normal dispatch. Returns
        True if the action was an autopilot command and was handled."""
        base = action.split(":", 1)[0].strip()
        if base not in ("patrol", "resume", "abort_behavior", "grind_combat", "journey"):
            return False

        self._last_action = base
        self._log_decision(state, action, reasoning, elapsed_ms, plan_summary)
        self.turn_count += 1

        if base == "resume" and self._suspended_behavior is not None:
            self._active_behavior = self._suspended_behavior
            self._suspended_behavior = None
            self._interrupt = None
            self._history.append(f"resumed {self._active_behavior.name} autopilot")
            self._last_state = None
            return True

        if base == "abort_behavior":
            self._end_behavior(state, "aborted by LLM", ended=False)
            return True

        if base == "patrol":
            radius = 8
            if ":" in action:
                try:
                    radius = max(2, int(action.split(":", 1)[1].strip()))
                except ValueError:
                    pass
            self._active_behavior = PatrolBehavior(self._skill_ctx, self.policy, radius=radius)
            self._suspended_behavior = None
            self._interrupt = None
            logger.info("Started PatrolBehavior (radius %d)", radius)
            self._history.append(f"started patrol autopilot (radius {radius})")
            self._last_state = None
            return True

        if base == "grind_combat":
            radius, until = self._parse_grind_args(action)
            self._active_behavior = GrindCombatBehavior(
                self._skill_ctx, self.policy, radius=radius, until=until)
            self._suspended_behavior = None
            self._interrupt = None
            logger.info("Started GrindCombatBehavior (radius %d, until %s)", radius, until)
            self._history.append(f"started grind_combat autopilot (radius {radius})")
            self._last_state = None
            return True

        if base == "journey":
            arg = action.split(":", 1)[1].strip() if ":" in action else ""
            site_id, site_name, world_pos = self._resolve_journey_dest(arg, state)
            if site_id is None and not site_name and world_pos is None:
                self._history.append(f"journey: no destination site found for {arg!r}")
                self._last_state = None
                return True
            self._active_behavior = JourneyBehavior(
                self._skill_ctx, self.policy, site_id=site_id, site_name=site_name,
                world_pos=world_pos)
            self._suspended_behavior = None
            self._interrupt = None
            label = site_name or f"site {site_id}"
            logger.info("Started JourneyBehavior toward %s", label)
            self._history.append(f"started journey autopilot toward {label}")
            self._last_state = None
            return True

        # `resume` with nothing suspended — treat as no-op handled action.
        self._history.append(f"{base}: no suspended behavior")
        self._last_state = None
        return True

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, action: str, reasoning: str, state: GameState, elapsed_ms: int, plan_summary: str) -> None:
        d = self._registry.resolve(action, state, self._skill_ctx)
        self._last_action = d.canonical
        self._log_decision(state, d.canonical, reasoning, elapsed_ms, plan_summary)
        self.turn_count += 1

        # Conversation guard: record talk/talk_to actions (before SKILL early-return
        # so talk_to:<id> — which resolves to a SKILL — is still captured).
        if d.canonical == "talk" or d.canonical.startswith(("talk_to", "converse")):
            if d.canonical.startswith(("talk_to", "converse")):
                unit_id_str = d.canonical.split(":", 1)[1] if ":" in d.canonical else ""
                try:
                    uid = int(unit_id_str)
                    u = next((u for u in state.nearby_units if u.id == uid), None)
                    npc_key = _ConversationGuard.key(u.hist_fig_id, u.name) if u else None
                except (ValueError, AttributeError):
                    npc_key = None
            else:
                # "talk": use current conversation partner if known, else nearest historic NPC
                if self._conv.npc_hist_fig_id is not None or self._conv.npc_name is not None:
                    npc_key = _ConversationGuard.key(self._conv.npc_hist_fig_id, self._conv.npc_name)
                else:
                    nearest = next(
                        (u for u in sorted(state.nearby_units, key=lambda u: u.distance)
                         if not u.is_hostile and u.hist_fig_id >= 0),
                        None,
                    )
                    npc_key = _ConversationGuard.key(nearest.hist_fig_id, nearest.name) if nearest else None
            self._conv_guard.note_conversation(npc_key, self.turn_count)

        # A resolve-time error means the action cannot run (e.g. "no known
        # stairs", "unit not adjacent", unknown action). Surface it instead of
        # silently executing the fallback wait — otherwise the LLM retries blind.
        if d.error:
            outcome = f"FAILED: {d.error}"
            self._history.append(f"{action} → {outcome}")
            self._record_outcome(action, outcome)
            logger.info("Action %s failed at resolve: %s", action, d.error)
            self._last_state = state  # no game input was sent; state unchanged
            return

        if d.kind is ActionKind.SKILL and d.skill is not None:
            self._active_skill = d.skill
            self._skill_ticks = 0
            self._empty_talk_count = 0
            # Only spatial movement resets the streak — goto_*/explore/flee are
            # what the guard's hint tells the agent to do to escape a talked-out
            # NPC. Non-movement skills (read_quest_log, eatdrink, pickup, sleep,
            # talk_to) must NOT reset it: observed live, read_quest_log between
            # talks kept resetting the streak so exhaustion tripped ~13 turns late.
            if d.canonical.startswith(("goto", "explore")) or d.canonical == "flee":
                self._conv_guard.note_other_action()
            self._last_state = None  # skill steps next tick
            return

        if d.kind is ActionKind.CONTEXT and d.conv_index is not None:
            self._do_conversation(d.conv_index, state)
            return

        # Special: read_screen — execute the screen reader and store the result
        if d.canonical == "read_screen":
            try:
                data = self.lua.extract_screen_text()
                rows = data.get("rows", [])
                focus_list = data.get("focus", [state.focus_state or "unknown"])
                self._screen_text = (
                    f"Screen text (focus: {', '.join(str(f) for f in focus_list)}):\n"
                    + "\n".join(rows[:30])
                )
            except Exception:  # noqa: BLE001
                logger.exception("read_screen failed")
                self._screen_text = "read_screen failed — check logs"
            self._history.append("read_screen → screen text captured for next turn")
            self._last_state = None
            return

        # KEY action
        before = _Snapshot.of(state)
        self._execute_key(d.key or "A_MOVE_SAME_SQUARE")
        mode_switch = d.canonical in ("travel", "stop_travel")
        wait_s = 0.8 if mode_switch else max(self.poll_interval, 0.3)
        time.sleep(wait_s)
        after_state = self._fresh_state()
        after = _Snapshot.of(after_state)
        outcome_desc = self._describe_outcome(d.canonical, before, after)

        # Consume any deferred-callback errors from the DFHack console log.
        console_errors = self.lua.consume_action_errors()
        if console_errors:
            err_summary = "; ".join(console_errors[:3])
            outcome_desc = f"ERROR: {err_summary}"
            self._log_event("console_error", action=d.canonical, errors=console_errors,
                            tick=after_state.tick_counter)
            logger.warning("Console errors for %s: %s", d.canonical, console_errors)

        self._history.append(f"{d.canonical} → {outcome_desc}")
        self._record_outcome(d.canonical, outcome_desc)
        if d.canonical in ("wait_long", "travel", "stop_travel"):
            self._empty_talk_count = 0
        # Conversation guard: reset the talk streak ONLY when the agent actually
        # moves away (whitelist). read_screen / press:* / escape / wait are how
        # the agent thrashes *within* a stuck conversation menu — they must NOT
        # reset the streak, or the guard never trips (observed live: press:ESCAPE
        # between talks kept the streak pinned at 4). Movement skills (goto_*,
        # explore, flee) reset via the SKILL branch above.
        if d.canonical.startswith("move_") or d.canonical.startswith("attack"):
            self._conv_guard.note_other_action()
        if not after_state.showing_announcements:
            self._announcements.clear()
        self._last_state = after_state

    @staticmethod
    def _describe_outcome(action: str, before: _Snapshot, after: _Snapshot) -> str:
        if action.startswith("move_"):
            return "moved" if after.moved(before) else "blocked (no move)"
        if not after.changed(before):
            return "no effect"
        return "ok"

    def _do_conversation(self, idx: int, state: GameState) -> None:
        choice = next((c for c in state.conversation_choices if c.index == idx), None)
        if choice:
            self._conv.record_choice(choice.text)
            if state.conversation_phase == "select_npc":
                hf = next((u.hist_fig_id for u in state.nearby_units
                           if u.name == choice.text and u.hist_fig_id >= 0), None)
                self._conv.start(choice.text, hf)
            elif state.conversation_phase == "dialogue":
                self._trigger_detector.conversation_had_content = True
                if state.npc_relationships:
                    nm = state.npc_relationships[0].name
                    hf = next((u.hist_fig_id for u in state.nearby_units
                               if u.name == nm and u.hist_fig_id >= 0), None)
                    self._conv.start(nm, hf)
            self._history.append(f"spoke: {choice.text[:50]}")

        # Conversation guard: record this conversation pick against the current partner.
        conv_npc_key = _ConversationGuard.key(self._conv.npc_hist_fig_id, self._conv.npc_name)
        self._conv_guard.note_conversation(conv_npc_key, self.turn_count)

        # Asked-topics dedup: persist this topic so we don't re-ask it in future turns.
        if choice is not None and state.conversation_phase == "dialogue":
            self._asked_topics.record(conv_npc_key, choice.text, state.tick_counter)

        self.lua.execute_action(f"conversation:{idx}")
        time.sleep(0.6)
        after = self._fresh_state()

        # Conversation transition: focus still Conversation but choices loading
        if (after.focus_state and "Conversation" in after.focus_state
                and after.conversation_phase == "none" and not after.conversation_choices):
            for _ in range(4):
                time.sleep(0.3)
                after = self._fresh_state()
                if after.conversation_phase != "none":
                    break

        self._flush_conversation(after)
        self._last_state = after

    def _record_observed_sites(self, state: GameState) -> None:
        """Fold the live nearby-site list into the registry as ground-truth
        entries (upgrades any prior rumor for the same place)."""
        if not state.nearby_sites:
            return
        new_site = False
        for site in state.nearby_sites:
            if site.id is None or site.id < 0:
                continue
            if self._site_registry.get(str(site.id)) is None:
                new_site = True
            self._site_registry.record_observed(
                site_id=site.id, name=site.name, site_type=site.site_type,
                world_x=site.world_x, world_y=site.world_y, tick=state.tick_counter)
        # Persist only when a previously-unseen site appears (avoids per-tick I/O).
        if new_site:
            self._site_registry.save()

    def _flush_conversation(self, state: GameState) -> None:
        """Flush the accumulated conversation transcript to memory when the
        dialogue has closed. No-op while still in dialogue or with no content.
        Shared by _do_conversation (LLM path) and ConverseSkill termination."""
        if state.conversation_phase == "none" and self._conv.has_content:
            transcript, npc_name, npc_hf = self._conv.flush()
            if transcript and self.memory_writer:
                self.memory_writer.write_conversation(
                    transcript, npc_name or "unknown NPC", state, npc_hist_fig_id=npc_hf)
                self._conv_guard.note_productive()
            # Rumor extraction (M3): pull travel destinations out of the transcript
            # and fold them into the site registry as journey:<rumor_id> targets.
            if transcript:
                try:
                    n = self._rumor_extractor.harvest(
                        transcript, self._site_registry, tick=state.tick_counter)
                    if n:
                        self._site_registry.save()
                        logger.info("Rumor extraction: %d site candidate(s) from conversation", n)
                except Exception:
                    logger.exception("Rumor extraction failed")

    # ------------------------------------------------------------------
    # Conversation annotation helper
    # ------------------------------------------------------------------

    def _conversation_annotations(self, state: GameState) -> dict[str, str] | None:
        return self._prompt_assembler.conversation_annotations(state)

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------

    def _record_outcome(self, canonical: str, outcome: str) -> None:
        """If the outcome indicates failure, record it for temporary banning."""
        if canonical in _NEVER_BAN or canonical.startswith("press:"):
            return
        if any(sub in outcome for sub in _FAILURE_SUBSTRINGS):
            self._recent_failures[canonical] = (self.turn_count, outcome)

    def _build_banned(self, state: GameState) -> set[str]:
        """Collect actions that failed within the last _BAN_WINDOW turns."""
        banned: set[str] = set()
        cutoff = self.turn_count - _BAN_WINDOW
        for action, (fail_turn, _) in list(self._recent_failures.items()):
            if fail_turn >= cutoff:
                banned.add(action)
            else:
                del self._recent_failures[action]
        # Conversation guard: ban talk actions for NPCs we've talked out.
        for u in state.nearby_units:
            if u.is_hostile or u.hist_fig_id < 0:   # hist_fig_id defaults to -1 (non-historic)
                continue
            key = _ConversationGuard.key(u.hist_fig_id, u.name)
            if self._conv_guard.is_exhausted(key, self.turn_count):
                banned.add("talk")
                banned.add(f"talk_to:{u.id}")
        return banned

    def _build_hint(self, state: GameState) -> str:
        return self._prompt_assembler.build_hint(
            state,
            empty_talk_count=self._empty_talk_count,
            announcements=self._announcements,
            recent_failures=self._recent_failures,
            turn_count=self.turn_count,
        )

    def _build_knowledge_block(self, state: GameState, goal_text: str) -> str:
        return self._prompt_assembler.build_knowledge_block(
            state,
            goal_text=goal_text,
            active_behavior_name=self._active_behavior.name if self._active_behavior else "",
            suspended_behavior_name=self._suspended_behavior.name if self._suspended_behavior else "",
        )

    def _announcement_block(self, state: GameState) -> str:
        return self._prompt_assembler.build_announcement_block(state, self._announcements)

    def _retrieve_memories(self, state: GameState) -> str:
        return self._prompt_assembler.retrieve_memories(state)

    # ------------------------------------------------------------------
    # Goals
    # ------------------------------------------------------------------

    def _handle_goal_revision(self, state: GameState) -> list[str]:
        if self.goal_manager is None:
            return []
        triggers = self._trigger_detector.detect(state, self._last_action)
        if self._pending_triggers:
            triggers = triggers + self._pending_triggers
            self._pending_triggers = []
        for trigger in triggers:
            if trigger == "goto_arrived":
                continue  # plan-completion only; not a full revision
            logger.info("Goal revision triggered: %s", trigger)
            self.goal_manager.revise_and_plan(trigger, state)
            if self.memory_writer:
                self.memory_writer.on_trigger(trigger, state)
                if self.memory_writer.should_reflect() and self.reflection_engine:
                    self.reflection_engine.reflect(state)
                    self.memory_writer.reset_reflection_counter()
        return triggers

    def _goal_summary(self) -> str | None:
        if self.goal_manager is not None:
            s = self.goal_manager.goal_summary()
            if s and s != "(no goals)":
                return s
        return self._initial_goal_str

    # ------------------------------------------------------------------
    # Death handling (M2 tail)
    # ------------------------------------------------------------------

    def _handle_death(self, state: GameState) -> None:
        """Wire the full death sequence and stop the loop gracefully."""
        self._death_handled = True
        cause = _build_death_cause(state)
        handle_death(
            state=state,
            cause=cause,
            llm=self.llm,
            postmortem_buffer=self.postmortem_buffer,
            reflection_engine=self.reflection_engine,
            memory_writer=self.memory_writer,
            active_behavior=self._active_behavior,
            suspended_behavior=self._suspended_behavior,
            log_file=self._log_file,
            turn_count=self.turn_count,
            session_log_dir=self._session_log_dir,
        )
        self.running = False

    # ------------------------------------------------------------------
    # Session end / logging / exec
    # ------------------------------------------------------------------

    def _on_session_end(self) -> None:
        self._chunk_map.save()
        self._site_registry.save()
        # If the session ended via death, death_handler already flushed reflection.
        if self._death_handled:
            return
        if self.reflection_engine is None:
            return
        try:
            state = self._fresh_state()
        except Exception:
            state = GameState()
        logger.info("Running end-of-session reflection")
        self.reflection_engine.reflect(state)

    def _log_decision(self, state: GameState, action: str, reasoning: str, elapsed_ms: int, plan_summary: str) -> None:
        leaf = self.goal_manager.top_goal() if self.goal_manager else None
        entry = {
            "turn": self.turn_count, "tick": state.tick_counter, "action": action,
            "reasoning": reasoning, "llm_ms": elapsed_ms, "health_pct": state.health_pct,
            "in_combat": state.in_combat, "position": str(state.adventurer_position),
            "site": state.site_name or state.region_name,
            "active_goal": leaf.description if leaf else self._initial_goal_str,
            "plan_step": plan_summary.split("\n")[1].replace("  NOW: ", "").strip() if plan_summary else None,
        }
        self._log_file.write(json.dumps(entry) + "\n")
        self._log_file.flush()

    def _record_announcements(self, state: GameState) -> None:
        """Capture pending announcement lines (combat log / NPC speech / events)
        into the rolling buffer and conversation tracker. Paging (SELECT) is the
        caller's job; this only records, so observability survives whether the
        announcement is dismissed by the auto-handler or by an autopilot behavior."""
        for line in state.announcement_text or []:
            if line not in self._announcements:
                self._announcements.append(line)
        self._announcements = self._announcements[-20:]
        if self._conv.active:
            self._conv.record_npc_response(state.announcement_text or [])

    def _execute_key(self, key: str) -> None:
        self.lua.execute_action(key)
