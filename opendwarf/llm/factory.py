"""Select an LLM client by env var (OPENDWARF_LLM_PROVIDER)."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from opendwarf.llm.base import LLMClient

if TYPE_CHECKING:
    from opendwarf.observability import EventLogger

logger = logging.getLogger(__name__)


def build_llm(event_logger: "EventLogger | None" = None) -> LLMClient:
    provider = os.getenv("OPENDWARF_LLM_PROVIDER", "azure").lower()
    if provider == "anthropic":
        from opendwarf.llm.anthropic_client import AnthropicLLM
        logger.info("LLM provider: anthropic")
        return AnthropicLLM(event_logger=event_logger)
    if provider == "azure":
        from opendwarf.llm.azure_client import AzureOpenAILLM
        logger.info("LLM provider: azure")
        return AzureOpenAILLM(event_logger=event_logger)
    if provider == "openrouter":
        from opendwarf.llm.openrouter_client import OpenRouterLLM
        logger.info("LLM provider: openrouter")
        return OpenRouterLLM(event_logger=event_logger)
    if provider == "bridge":
        from opendwarf.llm.bridge_client import BridgeLLM
        logger.info("LLM provider: bridge")
        return BridgeLLM(event_logger=event_logger)
    raise ValueError(
        f"Unknown OPENDWARF_LLM_PROVIDER: {provider!r} "
        "(expected 'azure', 'anthropic', 'openrouter', or 'bridge')"
    )
