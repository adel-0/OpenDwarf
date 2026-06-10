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
from opendwarf.actions.skills import SkillContext, SkillStatus
from opendwarf.agent.prompts import build_system_bundle, build_turn_prompt
from opendwarf.agent.scratchpad import Scratchpad
from opendwarf.goals import survival as survival_gates_mod
from opendwarf.spatial.chunk_map import ChunkMap
from opendwarf.spatial.extractor import MapExtractor
from opendwarf.spatial.pathfinder import Pathfinder
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

        self.running = False
        self.turn_count = 0
        self._last_action: str | None = None
        self._last_state: GameState | None = None
        self._trigger_detector = _TriggerDetector()
        self._conv = _ConversationTracker()
        self._announcements: list[str] = []
        self._history: deque[str] = deque(maxlen=10)
        self._pending_triggers: list[str] = []
        self._empty_talk_count = 0

        # Spatial + actions
        spatial_dir = spatial_dir or Path("spatial")
        self._chunk_map = ChunkMap.load(spatial_dir / "chunks.json")
        self._pathfinder = Pathfinder(self._chunk_map)
        self._extractor = MapExtractor(lua, self._chunk_map)
        self._skill_ctx = SkillContext(lua, self._chunk_map, self._pathfinder, self._extractor)
        self._registry = default_registry()
        self._active_skill = None  # type: ignore[assignment]

        # Scratchpad
        self._scratchpad = Scratchpad(scratchpad_path or (spatial_dir.parent / "memory" / "scratchpad.md"))

        log_path = (logs_dir or Path("logs") / f"session_{datetime.now():%Y%m%d_%H%M%S}") / "decisions.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = log_path.open("a", encoding="utf-8")
        logger.info("Decision log: %s", log_path)

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

    def _tick(self) -> None:
        state = self._last_state if self._last_state is not None else self._fresh_state()

        if not state.is_adventure_mode or not state.taking_input:
            self._last_state = None
            time.sleep(self.poll_interval)
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
            self.goal_manager.check_step_completion(state, triggers)
            plan_summary = self.goal_manager.plan_summary()
        goal_summary = self._goal_summary()

        # --- render wide map into the summary ---
        view = self._extractor.render_view(state, radius=10)
        if view:
            state.map_tiles = view

        # --- build prompt ---
        banned: set[str] = set()
        action_block = self._registry.build_block(state, banned)
        hint = self._build_hint(state)
        memory_block = self._retrieve_memories(state)
        announcement_block = self._announcement_block(state)
        history_block = ("-- Recent Actions & Outcomes --\n" + "\n".join(f"  {h}" for h in self._history)
                         if self._history else "")
        scratchpad_block = self._scratchpad.format_for_prompt()

        summary = state.summary()
        postmortems = self.postmortem_buffer.load() if self.postmortem_buffer else ""
        bundle = build_system_bundle(goal_summary, self.df_mechanics, postmortems)
        bundle.user = build_turn_prompt(
            summary, action_block, plan_summary, memory_block, hint,
            announcement_block=announcement_block, decision_history=history_block,
            scratchpad_block=scratchpad_block,
        )
        logger.info("Turn %d:\n%s", self.turn_count, summary)

        t0 = time.monotonic()
        try:
            decision = self.llm.decide(bundle, caller="tactical")
        except Exception:
            logger.exception("Tactical LLM call failed; waiting")
            self._last_state = None
            time.sleep(1.0)
            return
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        action = decision.get("action", "wait")
        reasoning = decision.get("reasoning", "")
        self._scratchpad.update(decision.get("scratchpad"))
        logger.info("Decision: %s — %s", action, reasoning)

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
            for line in state.announcement_text or []:
                if line not in self._announcements:
                    self._announcements.append(line)
            self._announcements = self._announcements[-20:]
            if self._conv.active:
                self._conv.record_npc_response(state.announcement_text or [])
            self._execute_key("SELECT")
            return self._after_auto(0.3)

        if state.focus_state and "Look" in state.focus_state:
            self._execute_key("LEAVESCREEN")
            return self._after_auto(0.3)

        if state.conversation_phase == "select_npc" and state.conversation_choices:
            all_system = all("adventure_option_" in c.text.lower() or "shout" in c.text.lower()
                             for c in state.conversation_choices)
            if all_system:
                self._execute_key("LEAVESCREEN")
                if self._last_action == "talk":
                    self._empty_talk_count += 1
                return self._after_auto(0.3)
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
            self._last_state = None
            time.sleep(0.35)
            return
        # Terminal
        name = getattr(skill, "name", "skill")
        self._active_skill = None
        outcome = result.outcome or result.status.value
        self._history.append(f"{name}: {outcome}")
        logger.info("Skill %s ended (%s): %s", name, result.status.value, outcome)
        if result.status is SkillStatus.DONE and name in ("route", "fast_travel"):
            self._pending_triggers.append("goto_arrived")
        self._last_state = None  # re-extract; auto-handlers/LLM run next tick

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, action: str, reasoning: str, state: GameState, elapsed_ms: int, plan_summary: str) -> None:
        d = self._registry.resolve(action, state, self._skill_ctx)
        self._last_action = d.canonical
        self._log_decision(state, d.canonical, reasoning, elapsed_ms, plan_summary)
        self.turn_count += 1

        if d.kind is ActionKind.SKILL and d.skill is not None:
            self._active_skill = d.skill
            self._empty_talk_count = 0
            self._last_state = None  # skill steps next tick
            return

        if d.kind is ActionKind.CONTEXT and d.conv_index is not None:
            self._do_conversation(d.conv_index, state)
            return

        # KEY action
        before = _Snapshot.of(state)
        self._execute_key(d.key or "A_MOVE_SAME_SQUARE")
        mode_switch = d.canonical in ("travel", "stop_travel")
        time.sleep(0.8 if mode_switch else max(self.poll_interval, 0.3))
        after_state = self._fresh_state()
        after = _Snapshot.of(after_state)
        self._history.append(f"{d.canonical} → {self._describe_outcome(d.canonical, before, after)}")
        if d.canonical in ("wait_long", "travel", "stop_travel"):
            self._empty_talk_count = 0
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

        if after.conversation_phase == "none" and self._conv.has_content:
            transcript, npc_name, npc_hf = self._conv.flush()
            if transcript and self.memory_writer:
                self.memory_writer.write_conversation(
                    transcript, npc_name or "unknown NPC", after, npc_hist_fig_id=npc_hf)
        self._last_state = after

    # ------------------------------------------------------------------
    # Prompt helpers
    # ------------------------------------------------------------------

    def _build_hint(self, state: GameState) -> str:
        parts: list[str] = []

        # Survival gates (physio + danger)
        gates = survival_gates_mod.evaluate(state)
        hint = gates.hint()
        if hint:
            parts.append(hint)

        # Wound-based health warning (only if not already covered by gates)
        severe = [w for w in state.wounds if any(k in w.status.lower() for k in _SEVERE_WOUNDS)]
        if (state.health_pct < 30 or severe) and not gates.in_danger:
            wound_note = f" Serious wounds: {', '.join(str(w) for w in severe[:3])}." if severe else ""
            parts.append(f"WARNING: low condition (HP {state.health_pct}%).{wound_note} "
                         "Rest to recover; avoid combat.")

        if self._empty_talk_count >= 2:
            busy = []
            import re
            for ann in self._announcements:
                m = re.match(r"The (.+?) \(to the (.+?)\):", ann)
                if m:
                    busy += [m.group(1), m.group(2)]
            note = ("NOTE: the talk menu had no addressable NPCs"
                    + (f"; nearby NPCs are busy talking to each other ({', '.join(busy[:3])})" if busy else "")
                    + ". Move to a different NPC, wait_long, or travel elsewhere.")
            parts.append(note)
        return "\n".join(parts)

    def _announcement_block(self, state: GameState) -> str:
        block = ""
        if self._announcements:
            block = "-- Recent Announcements (NPC speech / events) --\n" + "\n".join(
                f"  {l}" for l in self._announcements[-10:])
        if state.combat_log:
            block += ("\n" if block else "") + "-- Combat Log --\n" + "\n".join(
                f"  {l}" for l in state.combat_log[-5:])
        if self._conv.has_content:
            cv = self._conv.format_for_prompt()
            block = cv + ("\n\n" + block if block else "")
        return block

    def _retrieve_memories(self, state: GameState) -> str:
        if self.memory_retriever is None:
            return ""
        if state.in_combat or state.hostile_units:
            ctx = "combat"
        elif state.conversation_phase != "none":
            ctx = "conversation"
        else:
            ctx = "exploration"
        parts = [state.site_name or state.region_name or ""]
        parts += [u.race for u in state.hostile_units[:3]]
        parts += [r.name for r in state.npc_relationships[:3]]
        if ctx == "conversation":
            parts += [c.text for c in state.conversation_choices if "adventure_option_" not in c.text.lower()]
        query = " ".join(p for p in parts if p).strip() or "adventure"
        notes = self.memory_retriever.retrieve(query=query, context_type=ctx, k=5, game_tick=state.tick_counter)
        return self.memory_retriever.format_for_prompt(notes)

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
    # Session end / logging / exec
    # ------------------------------------------------------------------

    def _on_session_end(self) -> None:
        self._chunk_map.save()
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

    def _execute_key(self, key: str) -> None:
        self.lua.execute_action(key)
