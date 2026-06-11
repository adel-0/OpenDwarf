"""Predicate evaluator for eval scenario success checks.

Predicates run over a session's decisions.jsonl stream.  Each predicate is a
small dict from the YAML scenario spec; they are composed with ``all_of`` /
``any_of``.

Supported leaf predicates
--------------------------

event_count(type, min, max=None)
    Count lines that have an ``"event"`` field equal to *type*.
    Pass if count >= min (and <= max if given).

decision_count(min, max=None)
    Count lines that have NO ``"event"`` field (i.e. normal LLM decision turns).
    Pass if count >= min (and <= max if given).

llm_calls(max)
    Alias for decision_count(min=0, max=max).  Convenience for exit-criterion
    specs like "< 20 LLM calls".

survived(true|false)
    Checks ``health_pct`` on the LAST decision line (non-event line).
    survived: true  → health_pct > 0
    survived: false → health_pct == 0

no_event(type, max)
    Passes if lines with event==type appear AT MOST *max* times.

action_count(action_prefix, min, max=None)
    Count decision lines where ``action`` starts with *action_prefix*.
    Useful for "at least N grind_combat turns" etc.

skill_level_gained(skill, min_levels)
    Counts ``behavior_ended`` or ``behavior_suspended`` digest lines that
    mention "+N <SKILL>" patterns (e.g. "+1 AXE").  Passes if total >= min_levels.
    NOTE: this depends on EventDigest including "+N SKILL" tokens in the one_line
    summary written to the "digest" field of behavior_ended events.  If the
    digest format changes this predicate will silently return 0 gains — see
    evals/README.md for the TODO.

Composition
-----------

all_of(predicates: list)   — all must pass
any_of(predicates: list)   — at least one must pass
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class PredicateResult:
    name: str
    passed: bool
    detail: str


# ---------------------------------------------------------------------------
# Event loading
# ---------------------------------------------------------------------------

def load_events(decisions_jsonl: Path) -> list[dict]:
    """Load all lines from a decisions.jsonl file, skipping malformed lines."""
    events: list[dict] = []
    if not decisions_jsonl.exists():
        return events
    with decisions_jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decision_lines(events: list[dict]) -> list[dict]:
    """Lines that are normal LLM decision turns (no 'event' field)."""
    return [e for e in events if "event" not in e]


def _event_lines(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e.get("event") == event_type]


def _parse_skill_gains(events: list[dict], skill: str) -> int:
    """Sum +N gains for *skill* from behavior_ended/behavior_suspended digest fields.

    If skill == 'ANY', sums all +N <SKILL> patterns across all digests.
    """
    skill_upper = skill.upper()
    if skill_upper == "ANY":
        # Match any "+N WORD" pattern (WORD is the skill name)
        pattern = re.compile(r"\+(\d+)\s+[A-Z_]+", re.IGNORECASE)
    else:
        pattern = re.compile(r"\+(\d+)\s+" + re.escape(skill_upper) + r"\b", re.IGNORECASE)
    total = 0
    for ev in events:
        if ev.get("event") in ("behavior_ended", "behavior_suspended"):
            digest = ev.get("digest", "")
            for m in pattern.finditer(digest):
                total += int(m.group(1))
    return total


# ---------------------------------------------------------------------------
# Leaf evaluators
# ---------------------------------------------------------------------------

def _eval_event_count(spec: dict, events: list[dict]) -> PredicateResult:
    event_type = spec["type"]
    min_count = int(spec.get("min", 0))
    max_count = spec.get("max")
    count = len(_event_lines(events, event_type))
    if max_count is not None:
        passed = min_count <= count <= int(max_count)
        detail = f"event '{event_type}' count={count} (wanted {min_count}–{max_count})"
    else:
        passed = count >= min_count
        detail = f"event '{event_type}' count={count} (wanted >= {min_count})"
    return PredicateResult(f"event_count({event_type})", passed, detail)


def _eval_decision_count(spec: dict, events: list[dict]) -> PredicateResult:
    min_count = int(spec.get("min", 0))
    max_count = spec.get("max")
    count = len(_decision_lines(events))
    if max_count is not None:
        passed = min_count <= count <= int(max_count)
        detail = f"decision count={count} (wanted {min_count}–{max_count})"
    else:
        passed = count >= min_count
        detail = f"decision count={count} (wanted >= {min_count})"
    return PredicateResult("decision_count", passed, detail)


def _eval_llm_calls(spec: dict, events: list[dict]) -> PredicateResult:
    """decision_count with max only — the common "< N LLM calls" pattern."""
    max_count = int(spec["max"])
    count = len(_decision_lines(events))
    passed = count <= max_count
    detail = f"LLM calls={count} (wanted <= {max_count})"
    return PredicateResult("llm_calls", passed, detail)


def _eval_survived(spec: dict, events: list[dict]) -> PredicateResult:
    want_alive = bool(spec["value"])
    decisions = _decision_lines(events)
    if not decisions:
        return PredicateResult("survived", False, "no decision lines found — session may be empty")
    last = decisions[-1]
    health = last.get("health_pct", 0)
    is_alive = health > 0
    passed = is_alive == want_alive
    detail = f"final health_pct={health} ({'alive' if is_alive else 'dead'})"
    return PredicateResult("survived", passed, detail)


def _eval_no_event(spec: dict, events: list[dict]) -> PredicateResult:
    event_type = spec["type"]
    max_count = int(spec.get("max", 0))
    count = len(_event_lines(events, event_type))
    passed = count <= max_count
    detail = f"event '{event_type}' count={count} (wanted <= {max_count})"
    return PredicateResult(f"no_event({event_type})", passed, detail)


def _eval_action_count(spec: dict, events: list[dict]) -> PredicateResult:
    prefix = spec["action_prefix"]
    min_count = int(spec.get("min", 0))
    max_count = spec.get("max")
    count = sum(1 for e in _decision_lines(events)
                if str(e.get("action", "")).startswith(prefix))
    if max_count is not None:
        passed = min_count <= count <= int(max_count)
        detail = f"action '{prefix}*' count={count} (wanted {min_count}–{max_count})"
    else:
        passed = count >= min_count
        detail = f"action '{prefix}*' count={count} (wanted >= {min_count})"
    return PredicateResult(f"action_count({prefix})", passed, detail)


def _eval_skill_level_gained(spec: dict, events: list[dict]) -> PredicateResult:
    skill = spec["skill"].upper()
    min_levels = int(spec["min_levels"])
    gained = _parse_skill_gains(events, skill)
    passed = gained >= min_levels
    detail = f"skill '{skill}' levels gained={gained} (wanted >= {min_levels})"
    return PredicateResult(f"skill_level_gained({skill})", passed, detail)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_LEAF_EVALUATORS = {
    "event_count": _eval_event_count,
    "decision_count": _eval_decision_count,
    "llm_calls": _eval_llm_calls,
    "survived": _eval_survived,
    "no_event": _eval_no_event,
    "action_count": _eval_action_count,
    "skill_level_gained": _eval_skill_level_gained,
}


def evaluate_predicate(spec: Any, events: list[dict]) -> list[PredicateResult]:
    """Evaluate a predicate spec (may be nested with all_of/any_of).

    Returns a flat list of PredicateResult objects, one per leaf check,
    plus a synthetic composite result for each all_of/any_of node.
    """
    if not isinstance(spec, dict):
        return [PredicateResult("invalid", False, f"predicate must be a dict, got {type(spec).__name__}")]

    if "all_of" in spec:
        children_specs = spec["all_of"]
        all_results: list[PredicateResult] = []
        for child in children_specs:
            all_results.extend(evaluate_predicate(child, events))
        composite_passed = all(r.passed for r in all_results)
        all_results.append(PredicateResult(
            "all_of",
            composite_passed,
            f"{sum(r.passed for r in all_results[:-1])}/{len(all_results) - 1} sub-predicates passed",
        ))
        return all_results

    if "any_of" in spec:
        children_specs = spec["any_of"]
        any_results: list[PredicateResult] = []
        for child in children_specs:
            any_results.extend(evaluate_predicate(child, events))
        composite_passed = any(r.passed for r in any_results)
        any_results.append(PredicateResult(
            "any_of",
            composite_passed,
            f"{sum(r.passed for r in any_results[:-1])}/{len(any_results) - 1} sub-predicates passed",
        ))
        return any_results

    # Leaf: find the predicate type key
    for key, evaluator in _LEAF_EVALUATORS.items():
        if key in spec:
            if key == "survived":
                return [evaluator({"value": spec[key]}, events)]
            if key == "llm_calls":
                return [evaluator({"max": spec[key]}, events)]
            return [evaluator(spec, events)]

    return [PredicateResult("unknown", False, f"unknown predicate keys: {list(spec.keys())}")]
