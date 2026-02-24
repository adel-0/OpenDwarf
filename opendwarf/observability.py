"""Centralized observability — session-scoped JSONL event logs.

Writes three log streams:
  - llm_calls.jsonl     — every LLM call (tactical, goal_revision, importance, reflection, postmortem)
  - memory_events.jsonl — memory writes, retrievals, expirations, reflections
  - goal_events.jsonl   — goal revisions, plan changes
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class EventLogger:
    """Writes session-scoped JSONL logs for LLM calls, memory events, and goal events."""

    def __init__(self, session_dir: Path) -> None:
        self._session_dir = session_dir
        self._session_dir.mkdir(parents=True, exist_ok=True)
        # Lazy file handles
        self._handles: dict[str, Any] = {}

    def _get_handle(self, name: str) -> Any:
        if name not in self._handles:
            path = self._session_dir / f"{name}.jsonl"
            self._handles[name] = path.open("a", encoding="utf-8")
        return self._handles[name]

    def _write(self, stream: str, entry: dict) -> None:
        entry["ts"] = time.time()
        fh = self._get_handle(stream)
        fh.write(json.dumps(entry, default=str) + "\n")
        fh.flush()

    # ------------------------------------------------------------------
    # LLM calls
    # ------------------------------------------------------------------

    def log_llm_call(
        self,
        *,
        caller: str,
        system_prompt: str,
        turn_prompt: str,
        response_raw: str | None = None,
        elapsed_ms: int = 0,
        error: str | None = None,
    ) -> None:
        self._write("llm_calls", {
            "caller": caller,
            "system_prompt": system_prompt[:200],
            "turn_prompt": turn_prompt,
            "response_raw": response_raw,
            "elapsed_ms": elapsed_ms,
            "error": error,
        })

    # ------------------------------------------------------------------
    # Memory events
    # ------------------------------------------------------------------

    def log_memory_event(self, *, event: str, **kwargs: Any) -> None:
        entry: dict[str, Any] = {"event": event}
        entry.update(kwargs)
        self._write("memory_events", entry)

    # ------------------------------------------------------------------
    # Goal events
    # ------------------------------------------------------------------

    def log_goal_event(self, *, event: str, **kwargs: Any) -> None:
        entry: dict[str, Any] = {"event": event}
        entry.update(kwargs)
        self._write("goal_events", entry)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        for fh in self._handles.values():
            fh.close()
        self._handles.clear()
