"""Agent scratchpad — the cross-turn continuity mechanism.

Each tactical turn the LLM may return an optional "scratchpad" field that
wholesale-replaces this note. It carries forward what still matters (current
intent, what's been tried and failed, durable observations) so the agent isn't
amnesiac between stateless calls. Persisted to disk so it survives restarts and
death, complementing postmortems.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_CHARS = 2000  # ~400-500 tokens

_TEMPLATE = """\
## Current intent
(none yet)

## Attempts & outcomes
(none yet)

## Observations
(none yet)"""


class Scratchpad:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._text = ""
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._text = self.path.read_text(encoding="utf-8").strip()
                logger.info("Loaded scratchpad (%d chars) from %s", len(self._text), self.path)
            except Exception:
                logger.exception("Failed to load scratchpad %s", self.path)
                self._text = ""

    @property
    def text(self) -> str:
        return self._text or _TEMPLATE

    def update(self, new_text: str | None) -> None:
        """Replace the scratchpad with the LLM's new version (if non-empty)."""
        if not new_text or not new_text.strip():
            return
        text = new_text.strip()
        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS] + "\n…(truncated)"
        self._text = text
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(text, encoding="utf-8")
        except Exception:
            logger.exception("Failed to persist scratchpad %s", self.path)

    def format_for_prompt(self) -> str:
        return f"-- Scratchpad (your running notes; rewrite via the 'scratchpad' field) --\n{self.text}"
