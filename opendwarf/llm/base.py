"""LLM client protocol + cache-aware prompt bundle.

A PromptBundle is an ordered list of system Blocks plus a single user message.
Blocks marked `cacheable=True` form a stable prefix (base system prompt, DF
mechanics, past-life lessons) that does not change turn to turn, so providers
can reuse a cached prefix. Dynamic content (state, actions, history) goes in
later non-cacheable blocks and the user message.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class Block:
    text: str
    cacheable: bool = False


@dataclass
class PromptBundle:
    system_blocks: list[Block] = field(default_factory=list)
    user: str = ""

    @classmethod
    def simple(cls, system: str, user: str) -> "PromptBundle":
        """For infrequent callers that don't need prefix caching."""
        return cls(system_blocks=[Block(system, cacheable=False)], user=user)

    def system_text(self) -> str:
        return "\n".join(b.text for b in self.system_blocks if b.text)

    def cache_prefix_len(self) -> int:
        """Index of the last cacheable block (1-based count), for cache breakpoints."""
        last = 0
        for i, b in enumerate(self.system_blocks):
            if b.cacheable:
                last = i + 1
        return last


def parse_decision(text: str) -> dict:
    """Extract a JSON object from a model response (tolerates markdown fences)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # Tolerate leading prose before the JSON object
    start = text.find("{")
    if start > 0:
        text = text[start:]
    return json.loads(text)


class LLMClient:
    """Abstract interface. Implementations call a specific provider."""

    def decide(self, bundle: PromptBundle, *, caller: str = "tactical") -> dict:
        raise NotImplementedError
