"""Component 5: the eval harness runs offline against the simulator.

Proves the *real* TacticalLoop drives a SimulatedLuaExecutor end-to-end with no
DFHack, and that the scenario predicates evaluate against the decision log it
produces.  The LLM is a scripted stand-in (no network).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.offline import run_offline
from evals.scenario import Scenario
from opendwarf.sim.scenarios import has_sim

_SCENARIOS = Path(__file__).resolve().parent.parent / "evals" / "scenarios"


class ScriptedLLM:
    """Returns a fixed tactical decision each turn; records callers."""

    def __init__(self, action: str = "wait") -> None:
        self._action = action
        self.callers: list[str] = []

    def decide(self, bundle, *, caller="tactical"):
        self.callers.append(caller)
        return {"action": self._action, "reasoning": "scripted", "scratchpad": None}


def _load(name: str) -> Scenario:
    return Scenario.find(name, _SCENARIOS)


def test_wolf_survival_has_sim_model():
    assert has_sim("wolf-survival")


def test_wolf_survival_passes_offline(tmp_path):
    # Waiting out a *neutral* wolf keeps the adventurer at full blood → survives.
    scenario = _load("wolf-survival")
    passed, results, session_dir = run_offline(
        scenario, ScriptedLLM("wait"), work_dir=tmp_path, max_turns=8,
    )
    assert passed, [(r.name, r.detail) for r in results]
    # The 'survived' leaf must be present and true.
    survived = next(r for r in results if r.name == "survived")
    assert survived.passed


def test_offline_run_produces_decision_log(tmp_path):
    scenario = _load("wolf-survival")
    llm = ScriptedLLM("wait")
    _passed, _results, session_dir = run_offline(
        scenario, llm, work_dir=tmp_path, max_turns=5,
    )
    decisions = session_dir / "decisions.jsonl"
    assert decisions.exists()
    lines = [ln for ln in decisions.read_text().splitlines() if ln.strip()]
    assert len(lines) >= 1
    # Every tactical turn went through the injected LLM.
    assert llm.callers and all(c == "tactical" for c in llm.callers)


def test_offline_refuses_unmodelled_scenario(tmp_path, monkeypatch):
    scenario = _load("wolf-survival")
    monkeypatch.setattr(scenario, "name", "buy-item")   # no sim model
    with pytest.raises(ValueError, match="no offline sim model"):
        run_offline(scenario, ScriptedLLM(), work_dir=tmp_path)
