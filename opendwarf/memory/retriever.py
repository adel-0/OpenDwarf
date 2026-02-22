"""MemoryRetriever — scores and retrieves relevant memories.

Scoring formula (Generative Agents):
    score = recency × importance_norm × relevance

Decay clock uses a session-level counter (accumulated via MemoryRetriever.advance_decay)
that caps any single action's contribution at 1,000 ticks to prevent macro-time zeroing.
"""

from __future__ import annotations

import logging

from opendwarf.memory.model import MemoryNote
from opendwarf.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# Tactical notes (importance < 5) expire after 5,000 ticks without access.
_TACTICAL_TTL = 5_000

# Context-type → preferred tags for pre-filtering
_CONTEXT_TAGS: dict[str, set[str]] = {
    "combat": {"combat", "threat", "enemy", "fight"},
    "exploration": {"location", "site", "travel", "map", "place"},
    "conversation": {"npc", "faction", "dialogue", "quest", "person"},
}


class MemoryRetriever:
    """Retrieves the top-k most relevant memories for a given query."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self._decay_tick: int = 0  # Session-level decay clock

    def advance_decay(self, game_tick_delta: int) -> None:
        """Advance the decay clock, capping macro-time jumps at 1,000 ticks."""
        self._decay_tick += min(game_tick_delta, 1_000)

    @property
    def decay_tick(self) -> int:
        return self._decay_tick

    def retrieve(
        self,
        query: str,
        context_type: str = "",
        k: int = 5,
        game_tick: int = 0,
    ) -> list[MemoryNote]:
        """Return top-k memories scored by recency × importance × relevance.

        Args:
            query: Natural-language description of the current situation.
            context_type: "combat" | "exploration" | "conversation" | "" (no filter).
            k: Max memories to return (hard-capped at 5 per roadmap).
            game_tick: Current game tick (for last_accessed update).
        """
        k = min(k, 5)
        all_notes = self.store.load_all()
        query_words = set(query.lower().split())
        preferred_tags = _CONTEXT_TAGS.get(context_type, set())

        eligible: list[MemoryNote] = []
        for note in all_notes:
            if note.expired:
                continue

            # Low-confidence inferred notes only retrieved on explicit query
            if note.confidence < 0.5 and context_type == "":
                continue

            # Lazy eviction: tactical notes past TTL
            if note.importance < 5 and note.type != "procedural":
                ticks_since_access = self._decay_tick - note.last_accessed_tick
                if ticks_since_access > _TACTICAL_TTL:
                    logger.debug("Lazily expiring tactical note %s (idle %d ticks)", note.id, ticks_since_access)
                    self.store.mark_expired(note)
                    continue

            # Procedural notes with failure rate below threshold are evicted
            if note.type == "procedural" and note.attempt_count >= 5 and note.success_rate < 0.3:
                logger.debug("Evicting procedural note %s (success_rate=%.2f)", note.id, note.success_rate)
                self.store.mark_expired(note)
                continue

            eligible.append(note)

        # Pre-filter by context tags when specified
        if preferred_tags and eligible:
            tag_filtered = [n for n in eligible if set(n.tags) & preferred_tags]
            if tag_filtered:
                eligible = tag_filtered

        # Score and sort
        scored = [(n, n.score(query_words, self._decay_tick)) for n in eligible]
        scored.sort(key=lambda x: x[1], reverse=True)

        top = [n for n, _ in scored[:k]]

        # Update last_accessed_tick for retrieved notes
        for note in top:
            self.store.mark_accessed(note, game_tick)

        return top

    def format_for_prompt(self, notes: list[MemoryNote]) -> str:
        """Format retrieved memories for injection into the turn prompt."""
        if not notes:
            return ""
        lines = ["-- Retrieved memories (top {}) --".format(len(notes))]
        for note in notes:
            conf_warn = " [LOW CONFIDENCE]" if note.confidence < 0.5 else ""
            lines.append(f"[{note.type.upper()} tick={note.tick} imp={note.importance}{conf_warn}]")
            lines.append(note.content)
        return "\n".join(lines)
