"""Record/replay tap on the LuaExecutor seam — the Hybrid fidelity gate.

The offline simulator (`SimulatedLuaExecutor`) is fast and deterministic but only
*approximates* DF.  To keep it honest we record real DF sessions at the
LuaExecutor boundary and replay them as fixtures:

  * ``RecordingLuaExecutor`` wraps a *real* executor.  Every method call is
    transparently delegated to the inner executor and its (method, args, result)
    appended to a JSONL *tape*.  Drop it in for any live run to capture a fixture.

  * ``ReplayLuaExecutor`` reads a tape back.  Each method call returns the next
    recorded result for that method, in recorded order — no DFHack, no network.
    Diverging from the recorded call sequence raises, so a replay is a faithful
    re-enactment of exactly what DF returned.

The fidelity gate (a future eval) drives the *simulator* with the same action
sequence as a tape and compares its `extract_state()` against the recorded one,
flagging any field the sim models incorrectly.  This module provides the capture
and playback halves of that loop; both are pure I/O over JSON, no DF dependency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RecordingLuaExecutor:
    """Transparent recording wrapper around a real ``LuaExecutor``.

    Every attribute access is delegated to the inner executor.  Method calls are
    delegated *and* recorded to a JSONL tape (one call per line).  Non-callable
    attributes pass straight through.

    Parameters
    ----------
    inner:
        The real executor to wrap (anything with the LuaExecutor surface).
    tape_path:
        File to append the recorded call tape to (created if missing).
    """

    # Real instance attributes — listed so __getattr__ never shadows them.
    __slots__ = ("_inner", "_tape_path", "_fh")

    def __init__(self, inner: Any, tape_path: str | Path) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_tape_path", Path(tape_path))
        self._tape_path.parent.mkdir(parents=True, exist_ok=True)
        object.__setattr__(self, "_fh", self._tape_path.open("a", encoding="utf-8"))

    def __getattr__(self, name: str) -> Any:
        # Only reached for names not in __slots__ — i.e. the inner executor's API.
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            self._record(name, args, kwargs, result)
            return result

        return wrapper

    def _record(self, method: str, args: tuple, kwargs: dict, result: Any) -> None:
        entry = {
            "method": method,
            "args": list(args),
            "kwargs": dict(kwargs),
            "result": result,
        }
        # A non-serialisable result would silently corrupt the tape — fail loud.
        self._fh.write(json.dumps(entry) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class ReplayLuaExecutor:
    """Replays a tape recorded by :class:`RecordingLuaExecutor`.

    Each method call consumes the next recorded entry and returns its stored
    result.  The call must match the recorded method name (in order); a mismatch
    means the code under test diverged from the captured session, which is a real
    fidelity failure and raises ``AssertionError``.
    """

    __slots__ = ("_calls", "_i")

    def __init__(self, tape_path: str | Path) -> None:
        text = Path(tape_path).read_text(encoding="utf-8")
        object.__setattr__(
            self, "_calls",
            [json.loads(line) for line in text.splitlines() if line.strip()],
        )
        object.__setattr__(self, "_i", 0)

    def __getattr__(self, name: str) -> Any:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return self._next(name)

        return wrapper

    def _next(self, method: str) -> Any:
        if self._i >= len(self._calls):
            raise AssertionError(
                f"replay exhausted: call #{self._i} ({method!r}) has no recorded entry"
            )
        entry = self._calls[self._i]
        object.__setattr__(self, "_i", self._i + 1)
        if entry["method"] != method:
            raise AssertionError(
                f"replay divergence at call #{self._i - 1}: "
                f"expected {entry['method']!r}, got {method!r}"
            )
        return entry["result"]

    @property
    def exhausted(self) -> bool:
        return self._i >= len(self._calls)
