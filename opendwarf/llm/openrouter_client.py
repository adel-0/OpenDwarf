"""OpenRouter client (OpenAI-compatible API at openrouter.ai)."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from opendwarf.llm.base import LLMClient, PromptBundle, parse_decision

if TYPE_CHECKING:
    from opendwarf.observability import EventLogger

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


class OpenRouterLLM(LLMClient):
    def __init__(self, event_logger: "EventLogger | None" = None) -> None:
        from openai import OpenAI

        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            timeout=60.0,
        )
        self.default_model = os.getenv("OPENDWARF_OPENROUTER_MODEL", _DEFAULT_MODEL)
        self._event_logger = event_logger

    def _model_for(self, caller: str) -> str:
        # e.g. OPENDWARF_OPENROUTER_MODEL_TACTICAL, _GOAL_REVISION, _REFLECTION
        key = f"OPENDWARF_OPENROUTER_MODEL_{caller.upper()}"
        return os.getenv(key, self.default_model)

    def decide(self, bundle: PromptBundle, *, caller: str = "tactical") -> dict:
        system_prompt = bundle.system_text()
        turn_prompt = bundle.user
        model = self._model_for(caller)
        t0 = time.monotonic()
        error_msg: str | None = None
        text = ""
        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": turn_prompt},
                ],
            )
            text = (response.choices[0].message.content or "").strip()
            if not text:
                raise ValueError(
                    f"LLM returned empty response (finish_reason={response.choices[0].finish_reason!r})"
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
                    system_prompt=system_prompt,
                    turn_prompt=turn_prompt,
                    response_raw=text or None,
                    elapsed_ms=elapsed_ms,
                    error=error_msg,
                )
