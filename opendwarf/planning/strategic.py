"""StrategicPlanner — Layer 2: decomposes active leaf goal into ordered tactical steps."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opendwarf.goals.model import Goal
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

_STRATEGIC_SYSTEM = """\
You are the strategic planner for an AI adventurer in Dwarf Fortress Adventure Mode.
Given the adventurer's current active goal and world context, decompose the goal into
3–6 concrete, ordered tactical steps the adventurer should execute next.

Steps should be specific and actionable (e.g. "Move northeast toward the market district",
not "go somewhere"). Each step represents a short-term objective that the tactical loop
can execute over several turns.

Respond with ONLY a JSON object:
{
  "steps": ["step 1", "step 2", ...],
  "reasoning": "<brief explanation of the plan>"
}

Keep steps concise (one sentence each). If the goal is already in progress, only include
remaining steps.
"""


class StrategicPlanner:
    """Decomposes active leaf goals into ordered step lists for the tactical layer."""

    def __init__(self, llm: object) -> None:
        self.llm = llm
        self._steps: list[str] = []
        self._current_step: int = 0
        self._goal_id: str | None = None

    @property
    def has_plan(self) -> bool:
        return bool(self._steps) and self._current_step < len(self._steps)

    @property
    def current_step_text(self) -> str:
        if self.has_plan:
            return self._steps[self._current_step]
        return ""

    @property
    def steps_remaining(self) -> int:
        return max(0, len(self._steps) - self._current_step)

    def advance(self) -> bool:
        """Move to next step. Returns True if more steps remain."""
        if self._current_step < len(self._steps) - 1:
            self._current_step += 1
            logger.info(
                "Plan step advanced to %d/%d: %s",
                self._current_step + 1, len(self._steps), self.current_step_text,
            )
            return True
        logger.info("Plan complete — all %d steps done", len(self._steps))
        return False

    def reset(self) -> None:
        self._steps = []
        self._current_step = 0
        self._goal_id = None

    def generate(self, goal: "Goal", state: "GameState") -> None:
        """Call LLM to generate a plan for the given goal. Replaces current plan."""
        turn_prompt = f"""\
Active goal: {goal.description}
Goal type: {goal.type.value}
Goal priority: {goal.priority:.2f}

World context:
  Adventurer: {state.adventurer_name or "unknown"}
  Position: {state.adventurer_position}
  Site: {state.site_name or state.region_name or "unknown area"}
  Health: {state.health_pct:.0f}%
  Nearby hostiles: {len(state.hostile_units)}

Current inventory summary:
{_inventory_summary(state)}

Decompose this goal into 3–6 concrete tactical steps."""

        try:
            result = self.llm.decide(_STRATEGIC_SYSTEM, turn_prompt)
        except Exception:
            logger.exception("Strategic planner LLM call failed")
            # Fall back to a single generic step
            self._steps = [f"Work toward: {goal.description}"]
            self._current_step = 0
            self._goal_id = goal.id
            return

        steps = result.get("steps", [])
        reasoning = result.get("reasoning", "")

        if not steps:
            logger.warning("Strategic planner returned no steps; using goal description")
            steps = [goal.description]

        self._steps = [str(s) for s in steps]
        self._current_step = 0
        self._goal_id = goal.id
        logger.info(
            "Generated plan for goal '%s' (%d steps): %s",
            goal.description, len(self._steps), reasoning,
        )
        for i, step in enumerate(self._steps):
            logger.debug("  Step %d: %s", i + 1, step)

    def plan_summary(self) -> str:
        """Return a compact text block for injection into tactical prompts."""
        if not self._steps:
            return ""
        lines = [
            f"Current plan ({self._current_step + 1}/{len(self._steps)}):",
            f"  NOW: {self.current_step_text}",
        ]
        remaining = self._steps[self._current_step + 1:]
        if remaining:
            lines.append(f"  NEXT: {remaining[0]}")
        return "\n".join(lines)

    def needs_replan(self, goal: "Goal") -> bool:
        """True if goal changed or no plan exists."""
        return self._goal_id != goal.id or not self._steps


def _inventory_summary(state: "GameState") -> str:
    from opendwarf.state.game_state import InventoryItem
    equipped = [i for i in state.inventory if i.mode not in ("Hauled", "hauled")]
    hauled = [i for i in state.inventory if i.mode in ("Hauled", "hauled")]
    parts = []
    if equipped:
        parts.append("Equipped: " + ", ".join(i.name for i in equipped[:4]))
    if hauled:
        parts.append("Hauled: " + ", ".join(i.name for i in hauled[:4]))
    return "\n  ".join(parts) if parts else "none"
