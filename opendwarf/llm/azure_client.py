"""Azure OpenAI client. Prefix caching is automatic on the Azure side."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

from opendwarf.llm.base import LLMClient, PromptBundle, parse_decision

if TYPE_CHECKING:
    from opendwarf.observability import EventLogger

logger = logging.getLogger(__name__)


class AzureOpenAILLM(LLMClient):
    def __init__(self, event_logger: "EventLogger | None" = None) -> None:
        from openai import AzureOpenAI

        self.client = AzureOpenAI(
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ["AZURE_OPENAI_API_VERSION"],
            timeout=60.0,
        )
        self.deployment = os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"]
        self.reasoning_effort = os.getenv("AZURE_OPENAI_REASONING_EFFORT", "medium")
        self._event_logger = event_logger

    def decide(self, bundle: PromptBundle, *, caller: str = "tactical") -> dict:
        system_prompt = bundle.system_text()
        turn_prompt = bundle.user
        t0 = time.monotonic()
        error_msg: str | None = None
        text = ""
        try:
            response = self.client.chat.completions.create(
                model=self.deployment,
                reasoning_effort=self.reasoning_effort,
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
