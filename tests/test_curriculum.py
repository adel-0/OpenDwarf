"""Tests for the autotelic learning-progress curriculum (breadth engine)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opendwarf.goals.curriculum import (
    TRIGGER_CAPABILITY_BUMPS,
    Capability,
    CompetenceLedger,
    select_focus,
)
from opendwarf.goals.manager import GoalManager
from opendwarf.goals.model import GoalStatus
from opendwarf.state.game_state import GameState, Skill


# ---------------------------------------------------------------------------
# CompetenceLedger
# ---------------------------------------------------------------------------

def test_observe_is_monotone_and_clamped():
    led = CompetenceLedger()
    led.observe(Capability.COMBAT, 0.5, tick=10)
    assert led.competence(Capability.COMBAT) == 0.5
    # A lower sample does not erode competence.
    led.observe(Capability.COMBAT, 0.3, tick=20)
    assert led.competence(Capability.COMBAT) == 0.5
    # Out-of-range samples are clamped.
    led.observe(Capability.COMBAT, 5.0, tick=30)
    assert led.competence(Capability.COMBAT) == 1.0


def test_history_only_grows_on_change():
    led = CompetenceLedger()
    led.observe(Capability.SOCIAL, 0.2, tick=1)
    led.observe(Capability.SOCIAL, 0.2, tick=2)  # no change → no new sample
    led.observe(Capability.SOCIAL, 0.2, tick=3)
    assert led._caps[Capability.SOCIAL].history == [(1, 0.2)]


def test_bump_has_diminishing_returns():
    led = CompetenceLedger()
    led.bump(Capability.WEALTH, 0.5, tick=1)   # 0 + 0.5*(1-0)   = 0.5
    led.bump(Capability.WEALTH, 0.5, tick=2)   # 0.5 + 0.5*0.5   = 0.75
    assert led.competence(Capability.WEALTH) == pytest.approx(0.75)
    assert led.competence(Capability.WEALTH) < 1.0


def test_learning_progress_zero_without_history():
    led = CompetenceLedger()
    assert led.learning_progress(Capability.COMBAT) == 0.0
    led.observe(Capability.COMBAT, 0.4, tick=1)
    assert led.learning_progress(Capability.COMBAT) == 0.0  # single sample


def test_learning_progress_detects_movement():
    led = CompetenceLedger()
    for i, c in enumerate([0.1, 0.2, 0.3, 0.5, 0.7, 0.9]):
        led.observe(Capability.COMBAT, c, tick=i)
    # Recent window clearly higher than older → positive LP.
    assert led.learning_progress(Capability.COMBAT) > 0.1


def test_observe_from_skills_maps_families():
    led = CompetenceLedger()
    led.observe_from_skills(
        [Skill("AXE", 9), Skill("CONVERSATION", 3), Skill("MINING", 12)],
        tick=5,
    )
    # AXE 9/15 = 0.6 combat; CONVERSATION 3/15 = 0.2 social; MINING is unmapped.
    assert led.competence(Capability.COMBAT) == pytest.approx(0.6)
    assert led.competence(Capability.SOCIAL) == pytest.approx(0.2)
    assert led.competence(Capability.KNOWLEDGE) == 0.0


def test_persistence_round_trip(tmp_path: Path):
    p = tmp_path / "competence.json"
    led = CompetenceLedger(p)
    led.observe(Capability.COMBAT, 0.4, tick=1)
    led.observe(Capability.COMBAT, 0.6, tick=2)
    led.mark_focus(Capability.COMBAT, tick=2)
    led.save()

    led2 = CompetenceLedger(p)
    assert led2.competence(Capability.COMBAT) == pytest.approx(0.6)
    assert led2.attempts(Capability.COMBAT) == 1
    assert len(led2._caps[Capability.COMBAT].history) == 2
    # File is valid JSON with the expected shape.
    data = json.loads(p.read_text())
    assert "combat" in data["capabilities"]


# ---------------------------------------------------------------------------
# select_focus
# ---------------------------------------------------------------------------

def test_cold_start_prefers_least_practised():
    led = CompetenceLedger()
    # Give every capability one attempt except KNOWLEDGE.
    for c in Capability:
        if c is not Capability.KNOWLEDGE:
            led.mark_focus(c, tick=1)
    assert select_focus(led) is Capability.KNOWLEDGE


def test_high_learning_progress_wins():
    led = CompetenceLedger()
    # All equally attempted so optimism is equal; SOCIAL has rising competence.
    for c in Capability:
        led.mark_focus(c, tick=0)
        led.mark_focus(c, tick=0)
    for i, comp in enumerate([0.1, 0.2, 0.4, 0.6, 0.8]):
        led.observe(Capability.SOCIAL, comp, tick=i)
    assert select_focus(led) is Capability.SOCIAL


def test_mastered_flat_dimension_is_deprioritised():
    led = CompetenceLedger()
    # COMBAT mastered long ago (flat, LP≈0, high competence); EXPLORATION fresh.
    led.observe(Capability.COMBAT, 1.0, tick=0)
    for c in Capability:
        led.mark_focus(c, tick=0)
    # Despite many attempts being equal, the mastery penalty pushes COMBAT down and
    # a fresh dimension should not be COMBAT.
    assert select_focus(led) is not Capability.COMBAT


def test_epsilon_greedy_uses_rng():
    led = CompetenceLedger()

    class _Rng:
        def random(self):
            return 0.0  # always below epsilon → explore

        def choice(self, seq):
            return seq[-1]

    chosen = select_focus(led, rng=_Rng(), epsilon=0.5)
    assert chosen is list(Capability)[-1]


def test_select_focus_is_deterministic():
    led = CompetenceLedger()
    led.observe(Capability.WEALTH, 0.3, tick=1)
    led.observe(Capability.WEALTH, 0.5, tick=2)
    a = select_focus(led)
    b = select_focus(led)
    assert a is b


def test_trigger_bumps_table_is_sane():
    for trigger, (cap, delta) in TRIGGER_CAPABILITY_BUMPS.items():
        assert isinstance(cap, Capability)
        assert 0.0 < delta < 1.0


# ---------------------------------------------------------------------------
# GoalManager integration (fake LLM, no DF)
# ---------------------------------------------------------------------------

class _FakeLLM:
    """Captures the prompt and returns a canned revision result."""

    def __init__(self, result: dict):
        self.result = result
        self.last_user_prompt: str | None = None

    def decide(self, bundle, caller: str = "") -> dict:
        self.last_user_prompt = bundle.user
        return self.result


def _mgr(tmp_path: Path, result: dict) -> tuple[GoalManager, _FakeLLM]:
    llm = _FakeLLM(result)
    mgr = GoalManager(llm, goals_dir=tmp_path)
    return mgr, llm


def test_revision_injects_campaign_focus_hint(tmp_path: Path):
    mgr, llm = _mgr(tmp_path, {"reasoning": "ok", "goals": [], "plan_steps": []})
    state = GameState(tick_counter=100)
    mgr.revise_and_plan("session_start", state)
    assert "CAMPAIGN FOCUS" in (llm.last_user_prompt or "")


def test_revision_tags_goal_capability(tmp_path: Path):
    result = {
        "reasoning": "fight",
        "goals": [{"id": None, "description": "Hunt a wolf", "status": "ACTIVE", "capability": "combat"}],
        "plan_steps": [],
    }
    mgr, _ = _mgr(tmp_path, result)
    mgr.revise_and_plan("session_start", GameState(tick_counter=1))
    top = mgr.top_goal()
    assert top is not None
    assert top.capability == "combat"


def test_done_goal_bumps_competence(tmp_path: Path):
    result = {
        "reasoning": "achieved",
        "goals": [{"id": None, "description": "Killed a wolf", "status": "DONE", "capability": "combat"}],
        "plan_steps": [],
    }
    mgr, _ = _mgr(tmp_path, result)
    mgr.revise_and_plan("combat_resolved", GameState(tick_counter=1))
    # combat_resolved trigger bump + DONE-goal bump both land on COMBAT.
    assert mgr.ledger.competence(Capability.COMBAT) > 0.0


def test_revision_observes_skills_and_persists_ledger(tmp_path: Path):
    mgr, _ = _mgr(tmp_path, {"reasoning": "", "goals": [], "plan_steps": []})
    state = GameState(tick_counter=50, skills=[Skill("SWORD", 6)])
    mgr.revise_and_plan("session_start", state)
    assert mgr.ledger.competence(Capability.COMBAT) == pytest.approx(0.4)
    assert (tmp_path / "competence.json").exists()


def test_unknown_capability_tag_is_ignored(tmp_path: Path):
    result = {
        "reasoning": "",
        "goals": [{"id": None, "description": "do a thing", "status": "ACTIVE", "capability": "telekinesis"}],
        "plan_steps": [],
    }
    mgr, _ = _mgr(tmp_path, result)
    mgr.revise_and_plan("session_start", GameState(tick_counter=1))
    assert mgr.top_goal().capability is None
