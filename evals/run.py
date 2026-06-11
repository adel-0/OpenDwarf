"""Eval harness runner.

Usage
-----
Run a scenario (assumes DF+DFHack is already running with the save loaded):

    uv run python -m evals.run <scenario>

    Options:
      --logs-dir DIR        Where to write session logs (default: logs/)
      --goals-dir DIR       Where to store goals (default: goals/)
      --memory-dir DIR      Where memory lives (default: memory/)
      --host HOST           DFHack host (default: 127.0.0.1)
      --port PORT           DFHack port (default: 5000)
      --verbose, -v         Verbose logging from the agent

Evaluate an existing session without running:

    uv run python -m evals.run --judge-only <session_dir> <scenario>

    Example:
      uv run python -m evals.run --judge-only logs/session_20260611_120000 wolf-survival

Exit codes
----------
  0 — all predicates passed
  1 — one or more predicates failed
  2 — runner / setup error (scenario not found, timeout, etc.)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from evals.predicates import evaluate_predicate, load_events
from evals.scenario import Scenario

_SCENARIOS_DIR = Path(__file__).parent / "scenarios"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _print_results(scenario: Scenario, results, session_dir: Path | None = None) -> bool:
    """Print predicate results and return True if all passed."""
    print(f"\n{'='*60}")
    print(f"EVAL: {scenario.name}")
    print(f"  {scenario.description}")
    if session_dir:
        print(f"  Session: {session_dir}")
    print(f"{'='*60}")

    # Filter out composite all_of/any_of summary lines for display — show leaves
    # then show the final verdict.
    leaf_results = [r for r in results if r.name not in ("all_of", "any_of")]
    composite_results = [r for r in results if r.name in ("all_of", "any_of")]

    for r in leaf_results:
        icon = "PASS" if r.passed else "FAIL"
        print(f"  [{icon}] {r.name}: {r.detail}")

    all_passed = all(r.passed for r in results)
    print(f"{'='*60}")
    print(f"  VERDICT: {'PASS' if all_passed else 'FAIL'}")
    print(f"{'='*60}\n")
    return all_passed


# ---------------------------------------------------------------------------
# Judge only
# ---------------------------------------------------------------------------

def run_judge_only(session_dir: Path, scenario: Scenario) -> bool:
    """Evaluate an existing session directory against a scenario's predicates."""
    decisions_jsonl = session_dir / "decisions.jsonl"
    if not decisions_jsonl.exists():
        print(f"ERROR: {decisions_jsonl} does not exist.", file=sys.stderr)
        return False
    events = load_events(decisions_jsonl)
    print(f"Loaded {len(events)} events from {decisions_jsonl}")
    results = evaluate_predicate(scenario.success_predicate, events)
    return _print_results(scenario, results, session_dir)


# ---------------------------------------------------------------------------
# Live run
# ---------------------------------------------------------------------------

def run_scenario(scenario: Scenario, args: argparse.Namespace) -> bool:
    """Spawn the OpenDwarf agent as a subprocess and evaluate results on finish."""
    print(f"\n{'='*60}")
    print(f"EVAL SCENARIO: {scenario.name}")
    print(f"  {scenario.description}")
    print()
    print(f"  *** MANUAL STEP REQUIRED ***")
    print(f"  Load DF save '{scenario.save}' in Dwarf Fortress before continuing.")
    print(f"  The adventurer should be positioned for the scenario when you unpause.")
    print(f"  DFHack must be running and connected on {args.host}:{args.port}.")
    print(f"{'='*60}\n")
    input("Press Enter when the save is loaded and DF is paused at the scenario start... ")

    session_name = f"eval_{scenario.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    logs_dir = Path(args.logs_dir)
    session_dir = logs_dir / session_name

    # Build the opendwarf command
    cmd = [
        sys.executable, "-m", "opendwarf.main",
        "--host", args.host,
        "--port", str(args.port),
        "--logs-dir", str(logs_dir),
        "--goals-dir", str(args.goals_dir),
        "--memory-dir", str(args.memory_dir),
    ]
    if args.verbose:
        cmd.append("--verbose")

    # Override the session naming: main.py uses its own timestamp-based name, so
    # we look for the most recently modified session dir after the run completes.
    print(f"Starting agent (wall-clock limit: {scenario.max_wallclock_seconds}s)...")
    print(f"Command: {' '.join(cmd)}\n")

    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(cmd)
    except Exception as exc:
        print(f"ERROR: failed to start agent process: {exc}", file=sys.stderr)
        return False

    timed_out = False
    try:
        proc.wait(timeout=scenario.max_wallclock_seconds)
    except subprocess.TimeoutExpired:
        print(f"\nWall-clock limit reached ({scenario.max_wallclock_seconds}s) — killing agent.")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        timed_out = True
    elapsed = time.monotonic() - t0
    print(f"Agent finished in {elapsed:.1f}s (timed_out={timed_out})")

    # Find the session dir created by this run (most recently modified under logs_dir)
    session_dirs = sorted(
        [d for d in logs_dir.iterdir() if d.is_dir() and d.name.startswith("session_")],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if not session_dirs:
        print(f"ERROR: no session dir found under {logs_dir}", file=sys.stderr)
        return False
    actual_session_dir = session_dirs[0]
    print(f"Session dir: {actual_session_dir}")

    decisions_jsonl = actual_session_dir / "decisions.jsonl"
    events = load_events(decisions_jsonl)
    print(f"Loaded {len(events)} events from {decisions_jsonl}")
    results = evaluate_predicate(scenario.success_predicate, events)
    return _print_results(scenario, results, actual_session_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenDwarf eval harness runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--judge-only", nargs=2, metavar=("SESSION_DIR", "SCENARIO"),
        help="Evaluate an existing session dir against a scenario without running the agent.",
    )
    parser.add_argument(
        "scenario", nargs="?",
        help="Scenario name (from evals/scenarios/) or path to a .yaml file.",
    )
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--goals-dir", default="goals")
    parser.add_argument("--memory-dir", default="memory")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if args.judge_only:
        session_dir_str, scenario_name = args.judge_only
        session_dir = Path(session_dir_str)
        if not session_dir.exists():
            print(f"ERROR: session dir '{session_dir}' does not exist.", file=sys.stderr)
            sys.exit(2)
        try:
            scenario = Scenario.find(scenario_name, _SCENARIOS_DIR)
        except FileNotFoundError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(2)
        passed = run_judge_only(session_dir, scenario)
        sys.exit(0 if passed else 1)

    if not args.scenario:
        parser.print_help()
        sys.exit(2)

    try:
        scenario = Scenario.find(args.scenario, _SCENARIOS_DIR)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    passed = run_scenario(scenario, args)
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
