"""Per-NPC asked-topics store — persisted dedup for conversation topic tracking.

Tracks which topics the agent has already asked each NPC about, keyed by NPC
identity (str(hist_fig_id) when >= 0, else "name:<npc_name>"), so the LLM
does not re-ask the same topics across dialogue re-engagements or sessions.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Conversational prefixes stripped before normalizing a topic text.
_STRIP_PREFIXES = (
    "tell me about ",
    "ask about ",
    "ask for ",
    "ask the ",
    "talk about ",
    "inquire about ",
    "bring up ",
    "discuss ",
    "ask ",
    "tell ",
    "say ",
)

# Substrings whose presence (in lowercased original text) marks a choice as a
# navigation/meta option — never a topic to record or annotate.
_NAV_SUBSTRINGS = (
    "(new menu)",
    "change the subject",
    "never mind",
    "nevermind",
    "say goodbye",
    "goodbye",
    "stop talking",
    "leave",
    "start a new conversation",
)


class AskedTopics:
    """Persisted per-NPC set of already-asked conversation topics.

    Internal data: ``dict[npc_key, dict[normalized_topic, last_tick]]``.
    NPC key follows the same scheme as ``_ConversationGuard.key``:
    ``str(hist_fig_id)`` when >= 0, else ``"name:<npc_name>"``.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, dict[str, int]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                self._data = {
                    k: v for k, v in loaded.items()
                    if isinstance(k, str) and isinstance(v, dict)
                }
            else:
                logger.warning("asked_topics: unexpected JSON type at %s; starting empty", self._path)
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("asked_topics: could not load %s; starting empty", self._path, exc_info=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def normalize(text: str) -> str:
        """Normalize a choice text to a canonical topic string.

        Strips a leading conversational prefix (first match), collapses
        internal whitespace, and strips trailing punctuation and surrounding
        whitespace.
        """
        t = re.sub(r"\s+", " ", text).strip()
        t_low = t.lower()
        for prefix in _STRIP_PREFIXES:
            if t_low.startswith(prefix):
                t = t[len(prefix):]
                break
        t = t.strip().rstrip(".?!")
        return t.lower()

    @staticmethod
    def is_topic(text: str) -> bool:
        """Return False for navigation/meta choices that should never be recorded.

        Returns False if the normalized form is empty, or if the original
        lowercased text contains any navigation substring.
        """
        low = text.lower()
        if any(nav in low for nav in _NAV_SUBSTRINGS):
            return False
        return bool(AskedTopics.normalize(text))

    def record(self, npc_key: str | None, text: str, tick: int) -> None:
        """Record that the agent asked about *text* with the NPC identified by *npc_key*.

        No-op if npc_key is None or text is not a topic.
        """
        if npc_key is None or not self.is_topic(text):
            return
        norm = self.normalize(text)
        if npc_key not in self._data:
            self._data[npc_key] = {}
        self._data[npc_key][norm] = tick
        self.save()

    def was_asked(self, npc_key: str | None, text: str) -> bool:
        """Return True iff this topic was previously recorded for the given NPC."""
        if npc_key is None or not self.is_topic(text):
            return False
        norm = self.normalize(text)
        return norm in self._data.get(npc_key, {})

    def asked(self, npc_key: str | None) -> list[str]:
        """Return normalized topics asked of this NPC, most-recent-first.

        Returns an empty list if npc_key is None or unknown.
        """
        if npc_key is None:
            return []
        bucket = self._data.get(npc_key, {})
        return [topic for topic, _ in sorted(bucket.items(), key=lambda kv: kv[1], reverse=True)]

    def save(self) -> None:
        """Atomically persist the store to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            logger.exception("asked_topics: failed to save %s", self._path)
            try:
                os.unlink(tmp)
            except OSError:
                pass
