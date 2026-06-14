"""Offline eval driver — run a scenario against the in-memory simulator.

This is the convergence point of the dev harness and the production harness: it
drives the *real* `TacticalLoop` (the same orchestrator that plays live DF) but
swaps the live `LuaExecutor` for a `SimulatedLuaExecutor`.  No DFHack, no save
file, no wall-clock wait — the same scenario predicates are evaluated against the
decision log the loop produces.

Only scenarios with a sim model (see ``opendwarf.sim.scenarios``) can run here;
others stay live-DF-only.  The LLM is injected so callers choose a real provider
(``build_llm``) for an end-to-end check or a scripted stand-in for a fast,
network-free regression.

Usage (real LLM)::

    uv run python -m evals.run --offline wolf-survival
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from evals.predicates import PredicateResult, evaluate_predicate, load_events
from evals.scenario import Scenario
from opendwarf.agent.loop import TacticalLoop
from opendwarf.sim import SimulatedLuaExecutor
from opendwarf.sim.scenarios import build_world, has_sim

logger = logging.getLogger(__name__)


def run_offline(
    scenario: Scenario,
    llm: Any,
    *,
    work_dir: Path,
    max_turns: int | None = None,
) -> tuple[bool, list[PredicateResult], Path]:
    """Run *scenario* against the simulator and evaluate its predicates.

    Parameters
    ----------
    scenario:
        The scenario to run (must have a sim model — check ``has_sim`` first).
    llm:
        An LLM client exposing ``decide(bundle, *, caller)`` — a real provider or
        a scripted stand-in.
    work_dir:
        Scratch directory for the session's logs, spatial map, goals and
        scratchpad.  Nothing under the repo is touched.
    max_turns:
        Hard cap on loop ticks.  Defaults to the scenario's ``max_llm_calls`` (or
        50) — enough head-room that the loop, not the cap, decides the outcome.

    Returns ``(all_passed, results, session_dir)``.
    """
    if not has_sim(scenario.name):
        raise ValueError(
            f"scenario {scenario.name!r} has no offline sim model; run it live instead"
        )

    cap = max_turns if max_turns is not None else (scenario.max_llm_calls or 50)

    world = build_world(scenario.name)
    sim = SimulatedLuaExecutor(world)

    session_dir = work_dir / f"offline_{scenario.name}"
    spatial_dir = work_dir / "spatial"
    goals_dir = work_dir / "goals"
    for d in (session_dir, spatial_dir, goals_dir):
        d.mkdir(parents=True, exist_ok=True)

    loop = TacticalLoop(
        sim,
        llm,
        poll_interval=0.0,                       # no real time passes in the sim
        logs_dir=session_dir,
        spatial_dir=spatial_dir,
        scratchpad_path=work_dir / "scratchpad.md",
        policy_path=goals_dir / "policy.json",
        asked_topics_path=goals_dir / "asked_topics.json",
    )

    for turn in range(cap):
        if world.adventurer_dead:
            # One more tick lets the loop observe death and run its handler.
            loop._tick()
            break
        loop._tick()

    loop._log_file.flush()

    decisions = session_dir / "decisions.jsonl"
    events = load_events(decisions)
    results = evaluate_predicate(scenario.success_predicate, events)
    all_passed = all(r.passed for r in results)
    return all_passed, results, session_dir
