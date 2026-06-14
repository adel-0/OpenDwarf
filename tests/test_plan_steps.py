"""Plan-step completion semantics — regression coverage for REACH_SITE.

Live-observed bug (full LLM loop, 2026-06-14): a "fast-travel to LEAP TEMPLE"
step with completion `reach_site` fired the instant it was checked in the origin
town TOME MOUTH, advancing the plan to "talk to an NPC in LEAP TEMPLE" while the
adventurer was still 15 tiles away. REACH_SITE must require a change of site.
"""

from __future__ import annotations

from pathlib import Path

from opendwarf.goals.manager import GoalManager
from opendwarf.goals.model import CompletionType, PlanStep
from opendwarf.state.game_state import GameState


def _manager(tmp_path: Path, steps: list[PlanStep]) -> GoalManager:
    gm = GoalManager(llm=None, goals_dir=tmp_path)
    gm._plan_steps = steps
    gm._current_step = 0
    return gm


def _state(site_name: str = "") -> GameState:
    s = GameState()
    s.site_name = site_name
    return s


def test_reach_site_does_not_fire_on_origin(tmp_path):
    gm = _manager(tmp_path, [
        PlanStep("Fast-travel to LEAP TEMPLE", CompletionType.REACH_SITE),
        PlanStep("Talk to an NPC", CompletionType.GENERIC),
    ])
    # First check records the origin; standing in the origin must NOT complete.
    advanced = gm.check_step_completion(_state("TOME MOUTH"), triggers=[])
    assert advanced is False
    assert gm.current_step_text == "Fast-travel to LEAP TEMPLE"


def test_reach_site_fires_on_arrival_at_new_site(tmp_path):
    gm = _manager(tmp_path, [
        PlanStep("Fast-travel to LEAP TEMPLE", CompletionType.REACH_SITE),
        PlanStep("Talk to an NPC", CompletionType.GENERIC),
    ])
    assert gm.check_step_completion(_state("TOME MOUTH"), triggers=[]) is False
    # Now we arrive somewhere new.
    advanced = gm.check_step_completion(_state("LEAP TEMPLE"), triggers=[])
    assert advanced is True
    assert gm.current_step_text == "Talk to an NPC"


def test_reach_site_origin_is_wilderness(tmp_path):
    """Starting in unnamed wilderness, arriving at any named site completes."""
    gm = _manager(tmp_path, [PlanStep("Reach a town", CompletionType.REACH_SITE)])
    assert gm.check_step_completion(_state(""), triggers=[]) is False
    # site_name "unknown" is not a real arrival.
    assert gm.check_step_completion(_state("unknown"), triggers=[]) is False
    assert gm.check_step_completion(_state("LEAP TEMPLE"), triggers=[]) is True


def test_reach_site_long_timeout_does_not_force_advance_early(tmp_path):
    """A reach_site step parsed from a plan dict gets the long-horizon timeout, so
    a slow journey isn't force-advanced after the 6-turn generic fallback."""
    step = PlanStep.from_dict({"description": "Fast-travel to LEAP TEMPLE",
                               "completion": "reach_site"})
    assert step.max_turns == 30
    gm = _manager(tmp_path, [step, PlanStep("next", CompletionType.GENERIC)])
    for _ in range(10):  # well past the old 6-turn fallback
        assert gm.check_step_completion(_state("TOME MOUTH"), triggers=[]) is False
    assert gm.current_step_text == "Fast-travel to LEAP TEMPLE"


def test_reach_site_baseline_resets_per_step(tmp_path):
    """After advancing, a *second* reach_site step records its own origin and does
    not inherit the previous step's baseline."""
    gm = _manager(tmp_path, [
        PlanStep("Travel to LEAP TEMPLE", CompletionType.REACH_SITE),
        PlanStep("Travel onward to DEATH PINE", CompletionType.REACH_SITE),
    ])
    assert gm.check_step_completion(_state("TOME MOUTH"), triggers=[]) is False
    assert gm.check_step_completion(_state("LEAP TEMPLE"), triggers=[]) is True
    # Step 2 now starts; standing in LEAP TEMPLE (its origin) must not complete it.
    assert gm.current_step_text == "Travel onward to DEATH PINE"
    assert gm.check_step_completion(_state("LEAP TEMPLE"), triggers=[]) is False
    assert gm.check_step_completion(_state("DEATH PINE"), triggers=[]) is True
