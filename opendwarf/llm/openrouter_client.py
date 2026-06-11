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

    def _call_once(
        self,
        model: str,
        system_prompt: str,
        turn_prompt: str,
        *,
        with_json_format: bool = True,
    ) -> str:
        """Make one chat completion call and return the response text."""
        kwargs: dict = {
            "model": model,
            "max_tokens": 3000,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": turn_prompt},
            ],
        }
        if with_json_format:
            kwargs["response_format"] = {"type": "json_object"}
        response = self.client.chat.completions.create(**kwargs)
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError(
                f"LLM returned empty response (finish_reason={response.choices[0].finish_reason!r})"
            )
        return text

    def decide(self, bundle: PromptBundle, *, caller: str = "tactical") -> dict:
        system_prompt = bundle.system_text()
        turn_prompt = bundle.user
        model = self._model_for(caller)
        t0 = time.monotonic()
        error_msg: str | None = None
        text = ""
        try:
            # Attempt 1: with json_object response_format.
            json_format = True
            try:
                text = self._call_once(model, system_prompt, turn_prompt, with_json_format=True)
            except Exception as exc:
                exc_str = str(exc)
                if "response_format" in exc_str or "json_object" in exc_str:
                    logger.warning("response_format not supported by model %s; retrying without it", model)
                    json_format = False
                    text = self._call_once(model, system_prompt, turn_prompt, with_json_format=False)
                else:
                    raise

            # Attempt 1 parse.
            try:
                return parse_decision(text)
            except Exception as parse_exc:
                logger.warning(
                    "parse_decision failed for caller %r (json_format=%s): %s — retrying once",
                    caller, json_format, parse_exc,
                )

            # Attempt 2: retry with a truncation-recovery suffix.
            retry_prompt = (
                turn_prompt
                + "\n\nYour previous reply was invalid or truncated JSON. "
                "Reply with ONLY one complete JSON object."
            )
            text = self._call_once(
                model, system_prompt, retry_prompt, with_json_format=json_format
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
