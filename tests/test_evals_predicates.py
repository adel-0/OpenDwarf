"""Unit tests for the evals predicate evaluator.

Uses synthetic JSONL fixtures (dicts built in memory) — no file I/O, no DF.
"""

from __future__ import annotations

import pytest

from evals.predicates import evaluate_predicate, load_events, PredicateResult


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _decision(turn=0, tick=100, action="wait", health_pct=100, in_combat=False):
    """A normal LLM decision line (no 'event' field)."""
    return {
        "turn": turn, "tick": tick, "action": action,
        "health_pct": health_pct, "in_combat": in_combat,
        "position": "(0,0,0)", "site": "TESTING",
        "reasoning": "test", "llm_ms": 500,
        "active_goal": None, "plan_step": None,
    }


def _event(event_type, turn=0, tick=100, **kwargs):
    """A special event line (has 'event' field — not counted as an LLM call)."""
    return {"event": event_type, "turn": turn, "tick": tick, **kwargs}


def _behavior_ended(digest="", outcome="done", turn=0):
    return _event("behavior_ended", turn=turn, digest=digest, reason=outcome)


def _behavior_suspended(digest="", reason="HOSTILE_UNHANDLED", turn=0):
    return _event("behavior_suspended", turn=turn, digest=digest, reason=reason)


# ---------------------------------------------------------------------------
# event_count
# ---------------------------------------------------------------------------

class TestEventCount:
    def test_pass_when_count_meets_min(self):
        events = [
            _event("escape_hatch"),
            _event("escape_hatch"),
            _decision(),
        ]
        results = evaluate_predicate({"event_count": "escape_hatch", "type": "escape_hatch", "min": 2}, events)
        assert results[0].passed

    def test_fail_when_count_below_min(self):
        events = [_event("escape_hatch"), _decision()]
        results = evaluate_predicate({"event_count": "escape_hatch", "type": "escape_hatch", "min": 3}, events)
        assert not results[0].passed

    def test_pass_with_range(self):
        events = [_event("behavior_ended")] * 3
        results = evaluate_predicate(
            {"event_count": "behavior_ended", "type": "behavior_ended", "min": 2, "max": 5}, events
        )
        assert results[0].passed

    def test_fail_exceeds_max(self):
        events = [_event("behavior_ended")] * 6
        results = evaluate_predicate(
            {"event_count": "behavior_ended", "type": "behavior_ended", "min": 0, "max": 4}, events
        )
        assert not results[0].passed

    def test_zero_events(self):
        events = [_decision()]
        results = evaluate_predicate({"event_count": "escape_hatch", "type": "escape_hatch", "min": 1}, events)
        assert not results[0].passed


# ---------------------------------------------------------------------------
# decision_count / llm_calls
# ---------------------------------------------------------------------------

class TestDecisionCount:
    def test_counts_only_non_event_lines(self):
        events = [
            _decision(turn=0),
            _decision(turn=1),
            _event("escape_hatch"),
            _decision(turn=2),
        ]
        results = evaluate_predicate({"decision_count": True, "min": 3}, events)
        assert results[0].passed

    def test_llm_calls_alias_pass(self):
        events = [_decision()] * 15
        results = evaluate_predicate({"llm_calls": 20}, events)
        assert results[0].passed

    def test_llm_calls_alias_fail(self):
        events = [_decision()] * 25
        results = evaluate_predicate({"llm_calls": 20}, events)
        assert not results[0].passed

    def test_decision_count_with_max(self):
        events = [_decision()] * 5
        results = evaluate_predicate({"decision_count": True, "min": 3, "max": 7}, events)
        assert results[0].passed

    def test_empty_session(self):
        results = evaluate_predicate({"llm_calls": 10}, [])
        assert results[0].passed  # 0 <= 10


# ---------------------------------------------------------------------------
# survived
# ---------------------------------------------------------------------------

class TestSurvived:
    def test_survived_true_alive(self):
        events = [_decision(health_pct=75)]
        results = evaluate_predicate({"survived": True}, events)
        assert results[0].passed

    def test_survived_true_dead(self):
        events = [_decision(health_pct=0)]
        results = evaluate_predicate({"survived": True}, events)
        assert not results[0].passed

    def test_survived_false_dead(self):
        events = [_decision(health_pct=0)]
        results = evaluate_predicate({"survived": False}, events)
        assert results[0].passed

    def test_survived_uses_last_decision(self):
        events = [
            _decision(turn=0, health_pct=100),
            _decision(turn=1, health_pct=0),   # died on last turn
            _event("escape_hatch"),             # event after death
        ]
        results = evaluate_predicate({"survived": True}, events)
        assert not results[0].passed

    def test_survived_no_decisions(self):
        events = [_event("escape_hatch")]
        results = evaluate_predicate({"survived": True}, events)
        assert not results[0].passed


# ---------------------------------------------------------------------------
# no_event
# ---------------------------------------------------------------------------

class TestNoEvent:
    def test_no_events_passes(self):
        events = [_decision()]
        results = evaluate_predicate({"no_event": "escape_hatch", "type": "escape_hatch", "max": 0}, events)
        assert results[0].passed

    def test_within_tolerance_passes(self):
        events = [_event("escape_hatch")] * 2 + [_decision()]
        results = evaluate_predicate({"no_event": "escape_hatch", "type": "escape_hatch", "max": 2}, events)
        assert results[0].passed

    def test_exceeds_tolerance_fails(self):
        events = [_event("escape_hatch")] * 3
        results = evaluate_predicate({"no_event": "escape_hatch", "type": "escape_hatch", "max": 2}, events)
        assert not results[0].passed


# ---------------------------------------------------------------------------
# action_count
# ---------------------------------------------------------------------------

class TestActionCount:
    def test_counts_prefix_matches(self):
        events = [
            _decision(action="grind_combat:12"),
            _decision(action="grind_combat:8"),
            _decision(action="patrol"),
            _decision(action="wait"),
        ]
        results = evaluate_predicate(
            {"action_count": "grind_combat", "action_prefix": "grind_combat", "min": 2}, events
        )
        assert results[0].passed

    def test_prefix_no_match(self):
        events = [_decision(action="wait")] * 5
        results = evaluate_predicate(
            {"action_count": "grind_combat", "action_prefix": "grind_combat", "min": 1}, events
        )
        assert not results[0].passed

    def test_action_count_with_max(self):
        events = [_decision(action="pickup")] * 3
        results = evaluate_predicate(
            {"action_count": "pickup", "action_prefix": "pickup", "min": 1, "max": 5}, events
        )
        assert results[0].passed


# ---------------------------------------------------------------------------
# skill_level_gained
# ---------------------------------------------------------------------------

class TestSkillLevelGained:
    def test_single_skill_gain_in_digest(self):
        events = [
            _behavior_ended(digest="grind_combat: killed wolf (1), +2 AXE — done"),
        ]
        results = evaluate_predicate(
            {"skill_level_gained": "AXE", "skill": "AXE", "min_levels": 2}, events
        )
        assert results[0].passed

    def test_insufficient_gains_fails(self):
        events = [
            _behavior_ended(digest="grind_combat: +1 AXE — done"),
        ]
        results = evaluate_predicate(
            {"skill_level_gained": "AXE", "skill": "AXE", "min_levels": 3}, events
        )
        assert not results[0].passed

    def test_gains_summed_across_multiple_events(self):
        events = [
            _behavior_ended(digest="+1 AXE — done", turn=0),
            _behavior_suspended(digest="+2 AXE — interrupted", turn=1),
        ]
        results = evaluate_predicate(
            {"skill_level_gained": "AXE", "skill": "AXE", "min_levels": 3}, events
        )
        assert results[0].passed

    def test_any_skill_sums_all_gains(self):
        events = [
            _behavior_ended(digest="+1 AXE +2 DODGING +1 FIGHTING — done"),
        ]
        results = evaluate_predicate(
            {"skill_level_gained": "ANY", "skill": "ANY", "min_levels": 3}, events
        )
        assert results[0].passed

    def test_any_skill_fails_insufficient(self):
        events = [
            _behavior_ended(digest="+1 AXE — done"),
        ]
        results = evaluate_predicate(
            {"skill_level_gained": "ANY", "skill": "ANY", "min_levels": 3}, events
        )
        assert not results[0].passed

    def test_no_behavior_events_returns_zero(self):
        events = [_decision(), _decision()]
        results = evaluate_predicate(
            {"skill_level_gained": "AXE", "skill": "AXE", "min_levels": 1}, events
        )
        assert not results[0].passed

    def test_other_skills_not_counted_for_specific_skill(self):
        events = [
            _behavior_ended(digest="+3 DODGING — done"),
        ]
        results = evaluate_predicate(
            {"skill_level_gained": "AXE", "skill": "AXE", "min_levels": 1}, events
        )
        assert not results[0].passed


# ---------------------------------------------------------------------------
# Composition: all_of / any_of
# ---------------------------------------------------------------------------

class TestComposition:
    def test_all_of_all_pass(self):
        events = [_decision()] * 5
        spec = {
            "all_of": [
                {"survived": True},
                {"llm_calls": 10},
            ]
        }
        results = evaluate_predicate(spec, events)
        composite = results[-1]
        assert composite.name == "all_of"
        assert composite.passed

    def test_all_of_one_fails(self):
        events = [_decision(health_pct=0)]
        spec = {
            "all_of": [
                {"survived": True},  # fails
                {"llm_calls": 10},   # passes
            ]
        }
        results = evaluate_predicate(spec, events)
        composite = results[-1]
        assert not composite.passed

    def test_any_of_one_passes(self):
        events = [_decision(health_pct=0)] * 30  # 30 calls, dead
        spec = {
            "any_of": [
                {"survived": True},  # fails (dead)
                {"llm_calls": 50},   # passes (30 <= 50)
            ]
        }
        results = evaluate_predicate(spec, events)
        composite = results[-1]
        assert composite.name == "any_of"
        assert composite.passed

    def test_any_of_all_fail(self):
        events = [_decision(health_pct=0)] * 60
        spec = {
            "any_of": [
                {"survived": True},  # fails
                {"llm_calls": 50},   # fails (60 > 50)
            ]
        }
        results = evaluate_predicate(spec, events)
        composite = results[-1]
        assert not composite.passed

    def test_nested_all_of_in_any_of(self):
        events = [_decision()] * 5
        spec = {
            "any_of": [
                {"survived": False},       # fails (alive)
                {
                    "all_of": [
                        {"survived": True},    # passes
                        {"llm_calls": 10},     # passes
                    ]
                },
            ]
        }
        results = evaluate_predicate(spec, events)
        outer_composite = results[-1]
        assert outer_composite.passed

    def test_wolf_survival_scenario_spec(self):
        """Test the exact spec shape from wolf-survival.yaml."""
        events = [_decision(health_pct=80)] * 10
        spec = {
            "all_of": [
                {"survived": True},
                {"llm_calls": 50},
            ]
        }
        results = evaluate_predicate(spec, events)
        assert results[-1].passed

    def test_grind_scenario_with_any_skill(self):
        """Test the grind-3-levels scenario predicate shape."""
        events = (
            [_decision()] * 20
            + [_behavior_ended(digest="+3 AXE +1 DODGING — done")]
        )
        spec = {
            "all_of": [
                {"survived": True},
                {"llm_calls": 500},
                {"skill_level_gained": "ANY", "skill": "ANY", "min_levels": 3},
            ]
        }
        results = evaluate_predicate(spec, events)
        assert results[-1].passed


# ---------------------------------------------------------------------------
# Unknown / malformed predicates
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unknown_predicate_key_returns_failure(self):
        results = evaluate_predicate({"does_not_exist": 42}, [])
        assert not results[0].passed

    def test_non_dict_predicate(self):
        results = evaluate_predicate("not a dict", [])
        assert not results[0].passed

    def test_empty_all_of(self):
        results = evaluate_predicate({"all_of": []}, [])
        composite = results[-1]
        assert composite.name == "all_of"
        # vacuously true (all of zero predicates pass)
        assert composite.passed
