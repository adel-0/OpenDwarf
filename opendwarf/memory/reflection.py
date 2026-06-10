"""ReflectionEngine — consolidates recent episodic memories into semantic/procedural insights.

Triggered when:
- Sum of importance scores of last 20 episodic writes exceeds 120
- Session end (always runs before shutdown)

Output: 1–3 higher-order insight notes (semantic or procedural) stored with source=reflection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opendwarf.memory.model import MemoryNote
from opendwarf.memory.store import MemoryStore

if TYPE_CHECKING:
    from opendwarf.observability import EventLogger
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

_REFLECTION_SYSTEM = """\
You are synthesizing recent Dwarf Fortress adventure memories into higher-order insights.

Given a batch of recent episodic memories, produce 1–3 concise insight notes.

Rules:
- Only produce insights that generalise beyond a single event ("Eastern ruins consistently spawn undead" not "I fought a zombie")
- Prefer tactical/strategic lessons and location-type generalizations
- Distinguish mechanics from flavor (DF atmospheric text has no gameplay value)
- Do NOT invent facts not supported by the provided memories

Respond with ONLY a JSON object:
{
  "insights": [
    {
      "type": "semantic|procedural",
      "content": "<2-4 sentence insight>",
      "tags": ["tag1", "tag2"],
      "importance": 7
    }
  ]
}
"""


class ReflectionEngine:
    """Runs consolidation passes to convert episodic clusters into semantic/procedural notes."""

    def __init__(self, store: MemoryStore, llm: object, event_logger: "EventLogger | None" = None) -> None:
        self.store = store
        self.llm = llm
        self._event_logger = event_logger

    def reflect(self, state: "GameState") -> list[MemoryNote]:
        """Run reflection on recent episodic memories. Returns newly created notes."""
        # Load recent high-importance episodic notes
        all_notes = self.store.load_all()
        episodic = [
            n for n in all_notes
            if n.type == "episodic" and not n.expired and n.source == "observed"
        ]
        # Sort by tick descending, take up to 20
        episodic.sort(key=lambda n: n.tick, reverse=True)
        recent = episodic[:20]

        if not recent:
            logger.info("Reflection: no recent episodic memories to process")
            return []

        batch_text = "\n\n".join(
            f"[tick {n.tick} imp={n.importance} tags={n.tags}]\n{n.content}"
            for n in recent
        )
        turn_prompt = (
            f"Recent episodic memories (last {len(recent)}):\n\n"
            f"{batch_text}\n\n"
            "Synthesise 1–3 insight notes. Respond with JSON."
        )

        try:
            from opendwarf.llm.base import PromptBundle
            result = self.llm.decide(
                PromptBundle.simple(_REFLECTION_SYSTEM, turn_prompt), caller="reflection"
            )
        except Exception:
            logger.exception("Reflection LLM call failed")
            return []

        new_notes: list[MemoryNote] = []
        for ins in result.get("insights", []):
            try:
                note = MemoryNote.new(
                    type=ins.get("type", "semantic"),
                    tick=state.tick_counter,
                    importance=int(ins.get("importance", 7)),
                    tags=list(ins.get("tags", [])),
                    content=ins["content"],
                    source="reflection",
                    confidence=0.7,  # Reflection-inferred, not direct observation
                    cross_session=True,
                )
                self.store.write(note)
                new_notes.append(note)
                logger.info("Reflection note created: %s", note.content[:80])
            except Exception:
                logger.exception("Failed to process insight: %s", ins)

        if self._event_logger and new_notes:
            self._event_logger.log_memory_event(
                event="reflect",
                input_note_count=len(recent),
                output_note_ids=[n.id for n in new_notes],
                output_note_count=len(new_notes),
            )

        return new_notes
