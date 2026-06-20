"""File-bridge LLM client.

Lets an *external* decision-maker (e.g. a Claude Code subagent) act as the
OpenDwarf brain without any API call. Each ``decide()`` writes the full
system+user prompt to ``<bridge>/req_<n>.json`` and blocks until a matching
``<bridge>/resp_<n>.json`` appears, then parses the decision out of it — the
exact same contract the real LLM clients honor.

The external responder reads pending requests with ``bridge_wait.py`` and posts
answers with ``bridge_reply.py``. Bridge dir: ``OPENDWARF_BRIDGE_DIR`` (default
``logs/bridge``). On response timeout the client returns a safe ``wait`` so a
slow/absent responder degrades gracefully instead of deadlocking the game loop.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from opendwarf.llm.base import LLMClient, PromptBundle, parse_decision

if TYPE_CHECKING:
    from opendwarf.observability import EventLogger

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 0.5
_DEFAULT_TIMEOUT = float(os.getenv("OPENDWARF_BRIDGE_TIMEOUT", "900"))


class BridgeLLM(LLMClient):
    def __init__(self, event_logger: "EventLogger | None" = None) -> None:
        self.dir = Path(os.getenv("OPENDWARF_BRIDGE_DIR", "logs/bridge"))
        self.dir.mkdir(parents=True, exist_ok=True)
        self._event_logger = event_logger
        self._n = 0
        # Clear any stale request/response files from a previous run.
        for f in self.dir.glob("req_*.json"):
            f.unlink()
        for f in self.dir.glob("resp_*.json"):
            f.unlink()
        logger.info("LLM provider: bridge (dir=%s)", self.dir)

    def decide(self, bundle: PromptBundle, *, caller: str = "tactical") -> dict:
        self._n += 1
        n = self._n
        req = {
            "turn": n,
            "caller": caller,
            "system": bundle.system_text(),
            "user": bundle.user,
        }
        req_path = self.dir / f"req_{n}.json"
        resp_path = self.dir / f"resp_{n}.json"
        tmp = req_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(req), encoding="utf-8")
        tmp.rename(req_path)  # atomic publish

        deadline = time.monotonic() + _DEFAULT_TIMEOUT
        while time.monotonic() < deadline:
            if resp_path.exists():
                try:
                    raw = resp_path.read_text(encoding="utf-8")
                    decision = parse_decision(raw)
                except (ValueError, OSError) as e:
                    logger.warning("bridge resp_%d unparsable (%s); retrying", n, e)
                    time.sleep(_POLL_INTERVAL)
                    continue
                resp_path.unlink(missing_ok=True)
                req_path.unlink(missing_ok=True)
                return decision
            time.sleep(_POLL_INTERVAL)

        logger.warning("bridge: no response for turn %d after %.0fs — defaulting to wait",
                       n, _DEFAULT_TIMEOUT)
        req_path.unlink(missing_ok=True)
        return {"action": "wait", "reasoning": "bridge timeout (no responder)"}
