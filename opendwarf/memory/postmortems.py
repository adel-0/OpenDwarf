"""PostmortemBuffer — session-lesson log written on death or FAILED root goal.

The entire file is injected at every session start (zero retrieval latency).
Max 10 entries; oldest dropped when full.
Near-duplicate detection prevents repeating the same lesson ad nauseam.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 10

_POSTMORTEM_SYSTEM = """\
You are writing a post-mortem for a Dwarf Fortress Adventure Mode run.
Given the cause of failure and current game state, produce a 1–2 sentence lesson.

Format: "[tick N, <cause>] <what went wrong>. <what to do differently next time>."

Be concrete and game-specific. Focus on actionable tactical or strategic lessons.
Examples:
  [tick 4200, death] Engaged two goblins simultaneously without checking HP first. Never fight multiple opponents when below 60% health without a clear retreat path.
  [tick 8100, goal_failed] Spent 3000 ticks searching for a quest target who had died. Always verify named targets are still alive before committing to a find-person goal.
"""


class PostmortemBuffer:
    """Manages the postmortems.md flat file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> str:
        """Return full file contents for session start injection."""
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8").strip()

    def append(self, entry: str, llm: object | None = None) -> None:
        """Append a post-mortem entry, enforcing the 10-entry cap."""
        entry = entry.strip()
        if not entry:
            return

        entries = self._parse_entries()

        # Dedup: skip if very similar (> 0.7 word overlap) to any existing entry
        if self._is_near_duplicate(entry, entries):
            logger.info("Postmortem deduplicated (near-duplicate exists): %s", entry[:60])
            existing_idx = self._find_most_similar(entry, entries)
            if existing_idx >= 0:
                # Update in place
                entries[existing_idx] = entry
                self._write_entries(entries)
            return

        entries.append(entry)

        # Enforce cap: drop oldest
        if len(entries) > _MAX_ENTRIES:
            dropped = entries.pop(0)
            logger.debug("Postmortem cap: dropped oldest: %s", dropped[:60])

        self._write_entries(entries)
        logger.info("Postmortem appended: %s", entry[:80])

    def generate_and_append(self, cause: str, state_summary: str, llm: object) -> None:
        """Use the LLM to generate a post-mortem and append it."""
        turn_prompt = (
            f"Cause of failure: {cause}\n\n"
            f"Final game state summary:\n{state_summary}\n\n"
            f"Write the post-mortem lesson (1–2 sentences, format as described)."
        )
        try:
            # LLM returns a dict, but the postmortem system prompt expects plain text.
            # We'll encode the request as a JSON-returning prompt.
            pm_system = _POSTMORTEM_SYSTEM + '\nRespond with ONLY: {"lesson": "<the lesson text>"}'
            from opendwarf.llm.base import PromptBundle
            result = llm.decide(PromptBundle.simple(pm_system, turn_prompt), caller="postmortem")
            lesson = result.get("lesson", "").strip()
            if lesson:
                self.append(lesson)
        except Exception:
            logger.exception("Post-mortem LLM call failed")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_entries(self) -> list[str]:
        if not self.path.exists():
            return []
        text = self.path.read_text(encoding="utf-8").strip()
        if not text:
            return []
        # Entries are separated by blank lines
        raw_entries = re.split(r"\n{2,}", text)
        return [e.strip() for e in raw_entries if e.strip()]

    def _write_entries(self, entries: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n\n".join(entries) + "\n", encoding="utf-8")

    @staticmethod
    def _word_overlap(a: str, b: str) -> float:
        wa = set(a.lower().split())
        wb = set(b.lower().split())
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / min(len(wa), len(wb))

    def _is_near_duplicate(self, entry: str, entries: list[str], threshold: float = 0.7) -> bool:
        return any(self._word_overlap(entry, e) >= threshold for e in entries)

    def _find_most_similar(self, entry: str, entries: list[str]) -> int:
        if not entries:
            return -1
        scores = [self._word_overlap(entry, e) for e in entries]
        best_idx = max(range(len(scores)), key=lambda i: scores[i])
        return best_idx if scores[best_idx] >= 0.7 else -1
