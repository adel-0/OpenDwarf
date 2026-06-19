"""Prompt assembly for the tactical loop.

``PromptAssembler`` gathers the dynamic blocks that feed into the per-turn
LLM prompt: survival/guard hints, memory retrieval, announcement/combat
log, conversation transcript, and action annotations.
It receives exactly the shared state it needs via constructor injection —
it does not reach back into ``TacticalLoop``.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from opendwarf.goals import survival as survival_gates_mod

if TYPE_CHECKING:
    from opendwarf.agent.loop import (
        _ConversationGuard,
        _ConversationTracker,
    )
    from opendwarf.agent.scratchpad import Scratchpad
    from opendwarf.memory.asked_topics import AskedTopics
    from opendwarf.memory.retriever import MemoryRetriever
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

_SEVERE_WOUNDS = ("severed", "missing", "bleeding")
_FAILURE_SUBSTRINGS = ("no path", "blocked", "no effect", "ERROR", "FAILED",
                       "did not start", "no quests found", "unreachable")
_BAN_WINDOW = 4


class PromptAssembler:
    """Builds the dynamic blocks of the per-turn tactical prompt.

    Injected dependencies (all shared references from ``TacticalLoop``):
    - ``conv`` / ``conv_guard`` / ``asked_topics`` — conversation bookkeeping
    - ``scratchpad`` — current scratchpad text
    - ``memory_retriever`` — episodic/semantic memory

    All mutable state that the assembler reads (``announcements``,
    ``empty_talk_count``, ``recent_failures``, ``turn_count``) is passed
    per-call rather than stored, so the object is stateless between calls.
    """

    def __init__(
        self,
        conv: "_ConversationTracker",
        conv_guard: "_ConversationGuard",
        asked_topics: "AskedTopics",
        scratchpad: "Scratchpad",
        memory_retriever: "MemoryRetriever | None",
        log_event_fn,  # callable(**fields) — the loop's _log_event
    ) -> None:
        self._conv = conv
        self._conv_guard = conv_guard
        self._asked_topics = asked_topics
        self._scratchpad = scratchpad
        self._memory_retriever = memory_retriever
        self._log_event = log_event_fn

    # ------------------------------------------------------------------
    # Public API called from _tick
    # ------------------------------------------------------------------

    def build_hint(
        self,
        state: "GameState",
        empty_talk_count: int,
        announcements: list[str],
        recent_failures: dict[str, tuple[int, str]],
        turn_count: int,
    ) -> str:
        """Survival gates, wound warnings, conversation guard hints, banned-action notes."""
        from opendwarf.agent.loop import _ConversationGuard  # avoid circular at import time
        parts: list[str] = []

        gates = survival_gates_mod.evaluate(state)
        hint = gates.hint()
        if hint:
            parts.append(hint)

        severe = [w for w in state.wounds if any(k in w.status.lower() for k in _SEVERE_WOUNDS)]
        if (state.health_pct < 30 or severe) and not gates.in_danger:
            wound_note = f" Serious wounds: {', '.join(str(w) for w in severe[:3])}." if severe else ""
            parts.append(f"WARNING: low condition (HP {state.health_pct}%).{wound_note} "
                         "Rest to recover; avoid combat.")

        if empty_talk_count >= 2:
            busy = []
            for ann in announcements:
                m = re.match(r"The (.+?) \(to the (.+?)\):", ann)
                if m:
                    busy += [m.group(1), m.group(2)]
            note = ("NOTE: the talk menu had no addressable NPCs"
                    + (f"; nearby NPCs are busy talking to each other ({', '.join(busy[:3])})" if busy else "")
                    + ". Move to a different NPC, wait_long, or travel elsewhere.")
            parts.append(note)

        exhausted_nearby = next(
            (u for u in state.nearby_units
             if not u.is_hostile and u.hist_fig_id >= 0
             and self._conv_guard.is_exhausted(
                 _ConversationGuard.key(u.hist_fig_id, u.name), turn_count)),
            None,
        )
        if exhausted_nearby is not None:
            dest = next((s for s in state.nearby_sites
                         if s.id is not None and s.id >= 0
                         and (s.name or "") != (state.site_name or "")), None)
            travel_hint = (
                f"travel onward (e.g. goto_site:{dest.id} to {dest.name})"
                if dest is not None else "travel to a known site (goto_site:<id>)"
            )
            parts.append(
                f"NOTE: you've talked to {exhausted_nearby.name} repeatedly with no new "
                "leads — this town is talked out. Stop re-engaging the locals. Act on what "
                f"you learned: {travel_hint}, journey to a rumored site, explore "
                "(explore:<dir>) for the unmapped frontier, or read your quest log."
            )

        # Asked-topics reminder
        topic_key: str | None = None
        topic_name: str | None = None
        conv_key = _ConversationGuard.key(self._conv.npc_hist_fig_id, self._conv.npc_name)
        if state.conversation_phase != "none" and conv_key:
            topic_key, topic_name = conv_key, self._conv.npc_name
        else:
            for u in state.nearby_units:
                if u.is_hostile or u.hist_fig_id < 0:
                    continue
                k = _ConversationGuard.key(u.hist_fig_id, u.name)
                if self._asked_topics.asked(k):
                    topic_key, topic_name = k, u.name
                    break
        if topic_key:
            topics = self._asked_topics.asked(topic_key)
            if topics:
                parts.append(
                    f"NOTE: with {topic_name or 'this NPC'} you have already asked about: "
                    f"{', '.join(topics[:6])}. Ask something different or move on — do not re-ask these."
                )

        # Banned-action note
        banned_note_parts: list[str] = []
        cutoff = turn_count - _BAN_WINDOW
        for action, (fail_turn, outcome) in recent_failures.items():
            if fail_turn >= cutoff:
                banned_note_parts.append(f"{action} ({outcome[:60]})")
        if banned_note_parts:
            parts.append(
                "NOTE: recently failed, temporarily unavailable: "
                + ", ".join(banned_note_parts)
            )

        return "\n".join(parts)

    def build_announcement_block(self, state: "GameState", announcements: list[str]) -> str:
        """Combine announcement buffer, combat log, and current conversation transcript."""
        block = ""
        if announcements:
            block = "-- Recent Announcements (NPC speech / events) --\n" + "\n".join(
                f"  {l}" for l in announcements[-10:])
        if state.combat_log:
            block += ("\n" if block else "") + "-- Combat Log --\n" + "\n".join(
                f"  {l}" for l in state.combat_log[-5:])
        if self._conv.has_content:
            cv = self._conv.format_for_prompt()
            block = cv + ("\n\n" + block if block else "")
        return block

    def retrieve_memories(self, state: "GameState") -> str:
        """Return a formatted memory block for the current state context."""
        if self._memory_retriever is None:
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
            parts += [c.text for c in state.conversation_choices
                      if "adventure_option_" not in c.text.lower()]
        query = " ".join(p for p in parts if p).strip() or "adventure"
        notes = self._memory_retriever.retrieve(
            query=query, context_type=ctx, k=5, game_tick=state.tick_counter)
        return self._memory_retriever.format_for_prompt(notes)

    def conversation_annotations(self, state: "GameState") -> dict[str, str] | None:
        """Return action_str -> annotation map for already-asked conversation choices.

        Returns ``None`` (rather than an empty dict) when there is nothing to
        annotate, so callers can cheaply skip passing annotations to build_block.
        """
        from opendwarf.agent.loop import _ConversationGuard  # avoid circular at import time
        if state.conversation_phase == "none":
            return None
        key = _ConversationGuard.key(self._conv.npc_hist_fig_id, self._conv.npc_name)
        if not key:
            return None
        ann = {
            f"conversation_{c.index}": "[already asked]"
            for c in state.conversation_choices
            if self._asked_topics.was_asked(key, c.text)
        }
        return ann or None
