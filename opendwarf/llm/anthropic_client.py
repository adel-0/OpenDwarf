"""Anthropic Claude client with prompt caching on the static prefix.

The PromptBundle's cacheable system blocks (base prompt + DF mechanics +
postmortems) form a stable prefix; we put a single cache_control breakpoint on
the last cacheable block so it is reused turn to turn. Dynamic blocks and the
user message follow and are not cached.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from opendwarf.llm.base import LLMClient, PromptBundle, parse_decision

if TYPE_CHECKING:
    from opendwarf.observability import EventLogger

logger = logging.getLogger(__name__)

# Default models per caller. Override via env (see _model_for). The tactical
# loop runs every turn, so it is the obvious place to drop to a cheaper model.
_DEFAULT_MODEL = "claude-opus-4-8"


class AnthropicLLM(LLMClient):
    def __init__(self, event_logger: "EventLogger | None" = None) -> None:
        import anthropic

        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY / profile
        self.default_model = os.getenv("OPENDWARF_ANTHROPIC_MODEL", _DEFAULT_MODEL)
        self.effort = os.getenv("OPENDWARF_LLM_EFFORT", "medium")  # low|medium|high|max
        self.max_tokens = int(os.getenv("OPENDWARF_LLM_MAX_TOKENS", "4096"))
        self._event_logger = event_logger

    def _model_for(self, caller: str) -> str:
        # e.g. OPENDWARF_ANTHROPIC_MODEL_TACTICAL, _GOAL_REVISION, _REFLECTION
        key = f"OPENDWARF_ANTHROPIC_MODEL_{caller.upper()}"
        return os.getenv(key, self.default_model)

    def _build_system(self, bundle: PromptBundle) -> list[dict]:
        """System content blocks with a cache breakpoint after the static prefix."""
        blocks: list[dict] = []
        cache_idx = bundle.cache_prefix_len() - 1  # last cacheable block
        for i, b in enumerate(bundle.system_blocks):
            if not b.text:
                continue
            block: dict = {"type": "text", "text": b.text}
            if i == cache_idx:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        return blocks

    def decide(self, bundle: PromptBundle, *, caller: str = "tactical") -> dict:
        t0 = time.monotonic()
        error_msg: str | None = None
        text = ""
        system_blocks = self._build_system(bundle)
        model = self._model_for(caller)
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=self.max_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                system=system_blocks,
                messages=[{"role": "user", "content": bundle.user}],
            )
            text = next((b.text for b in response.content if b.type == "text"), "").strip()
            if not text:
                raise ValueError(f"empty response (stop_reason={response.stop_reason!r})")
            usage = response.usage
            logger.debug(
                "Anthropic[%s] cache_read=%s cache_write=%s in=%s out=%s",
                caller,
                getattr(usage, "cache_read_input_tokens", 0),
                getattr(usage, "cache_creation_input_tokens", 0),
                usage.input_tokens, usage.output_tokens,
            )
            return parse_decision(text)
        except Exception as exc:
            error_msg = str(exc)
            raise
        finally:
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            if self._event_logger:
                self._event_logger.log_llm_call(
                    caller=caller,
                    system_prompt=bundle.system_text(),
                    turn_prompt=bundle.user,
                    response_raw=text or None,
                    elapsed_ms=elapsed_ms,
                    error=error_msg,
                )
