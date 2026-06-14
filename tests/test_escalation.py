"""Unit tests for the Tactician→Director escalation path (Step 2).

The loop's ``_tactical_decide`` tries ``caller="tactical"`` first;
on failure OR when the decision contains ``{"escalate": true}`` it
re-asks once with ``caller="tactical_escalated"``.  If that also fails,
it returns ``None`` so the caller waits and retries.
"""

from __future__ import annotations

from opendwarf.agent.loop import TacticalLoop


# ---------------------------------------------------------------------------
# Minimal stub for the LLM seam
# ---------------------------------------------------------------------------

class _StubLLM:
    """Configurable stub: maps ``caller`` strings to return values or exceptions."""

    def __init__(self, responses: dict):
        self.calls: list[str] = []
        self._responses = responses  # caller -> dict | Exception

    def decide(self, bundle, *, caller: str) -> dict:
        self.calls.append(caller)
        r = self._responses.get(caller)
        if isinstance(r, Exception):
            raise r
        return r  # type: ignore[return-value]


class _StubBundle:
    pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTacticalDecide:

    def _loop_with(self, llm):
        """Build a TacticalLoop with all IO stubs."""
        import unittest.mock as mock
        lua = mock.MagicMock()
        lua.extract_state.return_value = {}
        loop = TacticalLoop.__new__(TacticalLoop)
        loop.llm = llm
        return loop

    def test_success_on_first_call_does_not_escalate(self):
        llm = _StubLLM({"tactical": {"action": "wait"}})
        loop = self._loop_with(llm)
        result = loop._tactical_decide(_StubBundle())
        assert result == {"action": "wait"}
        assert llm.calls == ["tactical"]

    def test_failure_triggers_escalation(self):
        """First call raises → escalated caller is used."""
        llm = _StubLLM({
            "tactical": RuntimeError("timeout"),
            "tactical_escalated": {"action": "wait", "reasoning": "recovered"},
        })
        loop = self._loop_with(llm)
        result = loop._tactical_decide(_StubBundle())
        assert result is not None
        assert result["action"] == "wait"
        assert "tactical" in llm.calls
        assert "tactical_escalated" in llm.calls

    def test_escalate_flag_triggers_escalation(self):
        """``{"escalate": true}`` from the first call is treated as a failure
        and the escalated caller is used for the re-ask."""
        llm = _StubLLM({
            "tactical": {"escalate": True},
            "tactical_escalated": {"action": "flee", "reasoning": "escalated"},
        })
        loop = self._loop_with(llm)
        result = loop._tactical_decide(_StubBundle())
        assert result is not None
        assert result["action"] == "flee"
        assert "tactical_escalated" in llm.calls

    def test_both_fail_returns_none(self):
        """If both calls throw, ``_tactical_decide`` returns ``None``."""
        llm = _StubLLM({
            "tactical": RuntimeError("timeout"),
            "tactical_escalated": RuntimeError("also failed"),
        })
        loop = self._loop_with(llm)
        result = loop._tactical_decide(_StubBundle())
        assert result is None
        assert "tactical" in llm.calls
        assert "tactical_escalated" in llm.calls
