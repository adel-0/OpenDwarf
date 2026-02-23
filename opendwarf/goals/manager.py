"""GoalManager — flat goal list with merged goal revision + strategic planning."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from opendwarf.goals.model import Goal, GoalStatus

if TYPE_CHECKING:
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

MAX_ACTIVE_GOALS = 3

_GOAL_REVISION_SYSTEM = """\
You are the goal and planning system for an AI adventurer in Dwarf Fortress Adventure Mode.
Your job is to maintain a short list of goals (max 3 active) and produce a tactical plan.

You receive:
- The triggering event
- The current goal list
- The current world context

Respond with ONLY a JSON object:
{
  "reasoning": "<brief explanation>",
  "goals": [
    {"id": "<existing_id or null for new>", "description": "...", "status": "ACTIVE|DONE|DROPPED"}
  ],
  "plan_steps": ["step 1", "step 2", ...]
}

Rules:
- The goals list is the COMPLETE set of goals after revision. Omitted old goals are implicitly dropped.
- There must be 1-3 ACTIVE goals. First goal = most important.
- Mark goals DONE when achieved, DROPPED when no longer relevant.
- Terminal goals (DONE/DROPPED) in your response are acknowledged then discarded.
- plan_steps: 3-6 concrete tactical steps for the top active goal.
- If health is critical or hostiles are nearby, focus goals on immediate survival.

Make goals specific to Dwarf Fortress. Examples:
- "Ask the tavern-keeper about nearby lairs"
- "Travel to Snarlingtombs to slay the night creature"
- "Find a weapon upgrade — current copper short sword is inadequate"
- "Talk to the lord of Oakstown to get a quest"
- "Explore the fortress ruins to the northeast"

Do NOT generate generic RPG goals like "gain renown" or "ensure adequate supplies".
Steps should be specific and actionable (e.g. "Move northeast toward the market district",
not "go somewhere"). Each step is a short-term objective for the tactical loop.

IMPORTANT — the adventurer CANNOT:
- "Look around", "scan the area", "survey the surroundings", "listen"
- Inspect tiles beyond the 5x5 visible grid
Do NOT suggest look-mode, scanning, or surveying steps.

Available tactical actions: move in 8 directions, wait, talk to adjacent NPC, attack adjacent hostile,
pickup/drop/wield items, start/continue conversations, rest to heal.
"""


class GoalManager:
    """Manages a flat goal list with integrated plan generation."""

    def __init__(self, llm: object, goals_dir: Path = Path("goals")) -> None:
        self.llm = llm  # LLMClient with decide(system, turn) -> dict
        self.goals_dir = goals_dir
        self.goals_file = goals_dir / "active_goals.json"
        self._goals: list[Goal] = []
        self._load()

        # Plan state (merged from StrategicPlanner)
        self._plan_steps: list[str] = []
        self._current_step: int = 0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self.goals_file.exists():
            try:
                data = json.loads(self.goals_file.read_text(encoding="utf-8"))
                self._goals = [Goal.from_dict(g) for g in data.get("goals", [])]
                # Drop terminal goals on load
                self._goals = [g for g in self._goals if not g.is_terminal()]
                logger.info("Loaded %d active goals from %s", len(self._goals), self.goals_file)
            except Exception:
                logger.exception("Failed to load goals file; starting fresh")
                self._goals = []
        else:
            logger.info("No goals file at %s; starting with empty goal list", self.goals_file)

    def save(self) -> None:
        self.goals_dir.mkdir(parents=True, exist_ok=True)
        # Only persist active goals
        active = [g for g in self._goals if g.is_active()]
        data = {"goals": [g.to_dict() for g in active]}
        self.goals_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def active_goals(self) -> list[Goal]:
        return [g for g in self._goals if g.is_active()]

    def top_goal(self) -> Goal | None:
        active = self.active_goals()
        return active[0] if active else None

    # ------------------------------------------------------------------
    # Plan state
    # ------------------------------------------------------------------

    @property
    def has_plan(self) -> bool:
        return bool(self._plan_steps) and self._current_step < len(self._plan_steps)

    @property
    def current_step_text(self) -> str:
        if self.has_plan:
            return self._plan_steps[self._current_step]
        return ""

    def advance_step(self) -> bool:
        """Move to next plan step. Returns True if more steps remain."""
        if self._current_step < len(self._plan_steps) - 1:
            self._current_step += 1
            logger.info(
                "Plan step advanced to %d/%d: %s",
                self._current_step + 1, len(self._plan_steps), self.current_step_text,
            )
            return True
        logger.info("Plan complete — all %d steps done", len(self._plan_steps))
        return False

    def reset_plan(self) -> None:
        self._plan_steps = []
        self._current_step = 0

    def plan_summary(self) -> str:
        """Compact text block for injection into tactical prompts."""
        if not self._plan_steps:
            return ""
        lines = [
            f"Current plan ({self._current_step + 1}/{len(self._plan_steps)}):",
            f"  NOW: {self.current_step_text}",
        ]
        remaining = self._plan_steps[self._current_step + 1:]
        if remaining:
            lines.append(f"  NEXT: {remaining[0]}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # LLM revision + planning (merged)
    # ------------------------------------------------------------------

    def revise_and_plan(self, trigger: str, state: "GameState") -> str:
        """Run a single LLM call to revise goals AND generate plan steps.

        Returns the LLM's reasoning string.
        """
        # Build current goal summary
        goal_lines: list[str] = []
        for g in self._goals:
            if g.is_active():
                goal_lines.append(f"- [{g.id}] {g.summary_line()}")
        if not goal_lines:
            goal_lines = ["(no active goals)"]

        turn_prompt = f"""\
Trigger: {trigger}

World context:
  Adventurer: {state.adventurer_name or "unknown"}
  Position: {state.adventurer_position}
  Site: {state.site_name or state.region_name or "unknown"}
  Health: {state.health_pct:.0f}%
  In combat: {state.in_combat}
  Nearby hostiles: {len(state.hostile_units)}
  Tick: {state.tick_counter}

Current goals:
{chr(10).join(goal_lines)}

Current inventory summary:
{_inventory_summary(state)}

Respond with the JSON revision+plan object."""

        try:
            result = self.llm.decide(_GOAL_REVISION_SYSTEM, turn_prompt)
        except Exception:
            logger.exception("Goal revision LLM call failed")
            return "(revision failed)"

        reasoning = result.get("reasoning", "")
        logger.info("Goal revision [%s]: %s", trigger, reasoning)

        # Apply goal updates — the LLM returns the complete goal set
        new_goals: list[Goal] = []
        for gdata in result.get("goals", []):
            status = GoalStatus(gdata.get("status", "ACTIVE"))
            existing_id = gdata.get("id")

            if status in (GoalStatus.DONE, GoalStatus.DROPPED):
                # Terminal — acknowledge but don't keep
                logger.info("Goal %s: %s — %s", status.value, gdata.get("description", ""), existing_id or "new")
                continue

            if existing_id:
                # Update existing goal
                old = next((g for g in self._goals if g.id == existing_id), None)
                if old:
                    old.description = gdata.get("description", old.description)
                    old.status = status
                    new_goals.append(old)
                    continue

            # New goal
            goal = Goal.new(
                description=gdata["description"],
                created_tick=state.tick_counter,
                status=status,
            )
            new_goals.append(goal)
            logger.info("New goal: %s", goal.summary_line())

        # Cap at MAX_ACTIVE_GOALS
        self._goals = new_goals[:MAX_ACTIVE_GOALS]

        # Apply plan steps
        steps = result.get("plan_steps", [])
        if steps:
            self._plan_steps = [str(s) for s in steps]
            self._current_step = 0
            logger.info("Plan generated (%d steps)", len(self._plan_steps))
        else:
            top = self.top_goal()
            self._plan_steps = [top.description] if top else []
            self._current_step = 0

        self.save()
        return reasoning

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    def goal_summary(self) -> str:
        """Short text summary for LLM system prompts."""
        active = self.active_goals()
        if not active:
            return "(no goals)"
        lines = [f"{i+1}. {g.description}" for i, g in enumerate(active)]
        return "\n".join(lines)


def _inventory_summary(state: "GameState") -> str:
    equipped = [i for i in state.inventory if i.mode not in ("Hauled", "hauled")]
    hauled = [i for i in state.inventory if i.mode in ("Hauled", "hauled")]
    parts = []
    if equipped:
        parts.append("Equipped: " + ", ".join(i.name for i in equipped[:4]))
    if hauled:
        parts.append("Hauled: " + ", ".join(i.name for i in hauled[:4]))
    return "\n  ".join(parts) if parts else "none"
