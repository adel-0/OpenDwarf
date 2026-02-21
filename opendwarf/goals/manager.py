"""GoalManager — in-memory goal tree with lifecycle management and LLM revision."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from opendwarf.goals.model import Goal, GoalStatus, GoalType

if TYPE_CHECKING:
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

# Goal types eligible under each survival condition.
# These are hard gates checked BEFORE the LLM revision call.
_SURVIVAL_ONLY = frozenset({GoalType.SURVIVAL})
_PHYSIOLOGICAL_PLUS = frozenset({GoalType.SURVIVAL, GoalType.PHYSIOLOGICAL})
_ALL_TYPES = frozenset(GoalType)

_GOAL_MANAGER_SYSTEM = """\
You are the goal management system for an AI adventurer in Dwarf Fortress Adventure Mode.
Your job is to maintain a coherent, prioritised goal tree. Goals have a lifecycle:
CANDIDATE → ACTIVE → ACHIEVED (or DROPPED or FAILED → back to CANDIDATE via replan).

You receive:
- The triggering event
- The current goal tree
- The current world context

Respond with ONLY a JSON object in this format:
{
  "reasoning": "<brief explanation of your decisions>",
  "updates": [
    {"id": "<goal_id>", "action": "adopt"},
    {"id": "<goal_id>", "action": "set_priority", "priority": 0.8},
    {"id": "<goal_id>", "action": "drop", "reason": "<why>"},
    {"id": "<goal_id>", "action": "fail", "reason": "<why>"},
    {"id": "<goal_id>", "action": "achieve"}
  ],
  "new_goals": [
    {
      "description": "<natural language goal>",
      "type": "SURVIVAL|PHYSIOLOGICAL|SOCIAL|EXPLORATION|RENOWN|NARRATIVE",
      "priority": 0.0,
      "status": "CANDIDATE|ACTIVE",
      "notes": "<optional context>"
    }
  ]
}

Rules:
- There must always be at least one ACTIVE goal. If there are no active goals, you MUST set one to ACTIVE (either via "adopt" in updates, or set status="ACTIVE" in new_goals).
- Keep at most 1–2 ACTIVE top-level goals at a time. Sub-goals can stack.
- Survival is a hard gate — if health < 25% or hostile nearby, only propose SURVIVAL goals.
- Generate 3–5 CANDIDATE goals when the active tree has fewer than 2 leaf goals.
- Eligible goal types will be noted in the prompt — do not propose ineligible types.
- Always justify drops and failures with a reason.
"""


class GoalManager:
    """Manages the goal tree: lifecycle, persistence, and LLM-driven revision."""

    def __init__(self, llm: object, goals_dir: Path = Path("goals")) -> None:
        self.llm = llm  # LLMClient with decide(system, turn) -> dict
        self.goals_dir = goals_dir
        self.goals_file = goals_dir / "active_goals.json"
        self._goals: dict[str, Goal] = {}
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if self.goals_file.exists():
            try:
                data = json.loads(self.goals_file.read_text(encoding="utf-8"))
                self._goals = {g["id"]: Goal.from_dict(g) for g in data.get("goals", [])}
                logger.info("Loaded %d goals from %s", len(self._goals), self.goals_file)
            except Exception:
                logger.exception("Failed to load goals file; starting fresh")
                self._goals = {}
        else:
            logger.info("No goals file at %s; starting with empty goal tree", self.goals_file)

    def save(self) -> None:
        self.goals_dir.mkdir(parents=True, exist_ok=True)
        data = {"goals": [g.to_dict() for g in self._goals.values()]}
        self.goals_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def all_goals(self) -> list[Goal]:
        return list(self._goals.values())

    def active_goals(self) -> list[Goal]:
        return [g for g in self._goals.values() if g.is_active()]

    def candidate_goals(self) -> list[Goal]:
        return [g for g in self._goals.values() if g.is_candidate()]

    def active_leaf(self) -> Goal | None:
        """Return the highest-priority ACTIVE goal that has no ACTIVE sub-goals."""
        active = self.active_goals()
        if not active:
            return None
        leaves = [
            g for g in active
            if not any(self._goals[sid].is_active() for sid in g.sub_goal_ids if sid in self._goals)
        ]
        if not leaves:
            # Fall back to the highest-priority active goal
            leaves = active
        return max(leaves, key=lambda g: g.priority)

    def get(self, goal_id: str) -> Goal | None:
        return self._goals.get(goal_id)

    def add(self, goal: Goal) -> None:
        self._goals[goal.id] = goal
        if goal.parent_id and goal.parent_id in self._goals:
            parent = self._goals[goal.parent_id]
            if goal.id not in parent.sub_goal_ids:
                parent.sub_goal_ids.append(goal.id)

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def adopt(self, goal_id: str) -> bool:
        goal = self._goals.get(goal_id)
        if goal and goal.is_candidate():
            goal.status = GoalStatus.ACTIVE
            logger.info("Goal adopted: %s", goal.summary_line())
            self.save()
            return True
        return False

    def achieve(self, goal_id: str) -> None:
        goal = self._goals.get(goal_id)
        if not goal:
            return
        goal.status = GoalStatus.ACHIEVED
        logger.info("Goal achieved: %s", goal.description)
        # Propagate: if all siblings achieved, mark parent achieved
        if goal.parent_id:
            parent = self._goals.get(goal.parent_id)
            if parent and parent.is_active():
                siblings = [self._goals[sid] for sid in parent.sub_goal_ids if sid in self._goals]
                if all(s.status == GoalStatus.ACHIEVED for s in siblings):
                    self.achieve(parent.id)
        self.save()

    def drop(self, goal_id: str, reason: str = "") -> None:
        goal = self._goals.get(goal_id)
        if goal:
            goal.status = GoalStatus.DROPPED
            goal.fail_reason = reason
            logger.info("Goal dropped: %s — %s", goal.description, reason)
            self.save()

    def fail(self, goal_id: str, reason: str = "") -> None:
        goal = self._goals.get(goal_id)
        if not goal:
            return
        goal.status = GoalStatus.FAILED
        goal.fail_reason = reason
        logger.warning("Goal failed: %s — %s", goal.description, reason)
        # Propagate failure to parent
        if goal.parent_id:
            parent = self._goals.get(goal.parent_id)
            if parent and parent.is_active():
                self.fail(parent.id, f"Sub-goal failed: {goal.description}")
        self.save()

    def set_priority(self, goal_id: str, priority: float) -> None:
        goal = self._goals.get(goal_id)
        if goal:
            goal.priority = max(0.0, min(1.0, priority))

    # ------------------------------------------------------------------
    # Survival gate
    # ------------------------------------------------------------------

    def eligible_types(self, state: "GameState") -> frozenset[GoalType]:
        """Return which GoalTypes are eligible given the current survival context."""
        health_critical = state.health_pct < 25
        hostile_nearby = state.in_combat or bool(state.hostile_units)

        if health_critical or hostile_nearby:
            return _SURVIVAL_ONLY

        # Check physiological pressure (hunger/thirst/exhaustion)
        # GameState doesn't currently track these — treat as non-critical for now
        return _ALL_TYPES

    # ------------------------------------------------------------------
    # LLM revision
    # ------------------------------------------------------------------

    def revise(self, trigger: str, state: "GameState") -> str:
        """Run an LLM revision call. Returns the LLM's reasoning string."""
        eligible = self.eligible_types(state)
        eligible_names = sorted(t.value for t in eligible)

        # Build goal tree summary
        tree_lines: list[str] = []
        for g in sorted(self._goals.values(), key=lambda x: (-x.priority, x.id)):
            if g.is_terminal():
                continue  # Skip completed goals
            indent = "  " if g.parent_id else ""
            tree_lines.append(f"{indent}- [{g.id}] {g.summary_line()}")

        if not tree_lines:
            tree_lines = ["(no active or candidate goals)"]

        # Leaf count for generation trigger
        leaf_count = sum(
            1 for g in self.active_goals()
            if not any(self._goals.get(sid, Goal.new("", GoalType.NARRATIVE, 0, 0)).is_active()
                       for sid in g.sub_goal_ids)
        )

        turn_prompt = f"""\
Trigger: {trigger}

World context:
  Adventurer: {state.adventurer_name or "unknown"}
  Position: {state.adventurer_position}
  Site: {state.site_name or state.region_name or "unknown"}
  Health: {state.health_pct:.0f}%
  In combat: {state.in_combat}
  Tick: {state.tick_counter}

Eligible goal types (survival gate): {', '.join(eligible_names)}

Current goal tree ({len(self.active_goals())} active, {len(self.candidate_goals())} candidate, {leaf_count} active leaves):
{chr(10).join(tree_lines)}
{f"NOTE: Active tree has {leaf_count} active leaf goals — generate 3–5 new CANDIDATE goals and adopt at least one as ACTIVE." if leaf_count < 2 else ""}

Respond with the JSON revision object."""

        try:
            result = self.llm.decide(_GOAL_MANAGER_SYSTEM, turn_prompt)
        except Exception:
            logger.exception("Goal manager LLM call failed")
            return "(revision failed)"

        reasoning = result.get("reasoning", "")
        logger.info("Goal revision [%s]: %s", trigger, reasoning)

        # Apply updates
        for upd in result.get("updates", []):
            gid = upd.get("id", "")
            action = upd.get("action", "")
            if action == "adopt":
                self.adopt(gid)
            elif action == "set_priority":
                self.set_priority(gid, float(upd.get("priority", 0.5)))
            elif action == "drop":
                self.drop(gid, upd.get("reason", ""))
            elif action == "fail":
                self.fail(gid, upd.get("reason", ""))
            elif action == "achieve":
                self.achieve(gid)

        # Add new goals
        for ng in result.get("new_goals", []):
            try:
                goal = Goal.new(
                    description=ng["description"],
                    type=GoalType(ng["type"]),
                    priority=float(ng.get("priority", 0.5)),
                    created_tick=state.tick_counter,
                    status=GoalStatus(ng.get("status", "CANDIDATE")),
                    notes=ng.get("notes", ""),
                )
                self.add(goal)
                logger.info("New goal added: %s", goal.summary_line())
            except Exception:
                logger.exception("Failed to add new goal: %s", ng)

        self.save()
        return reasoning

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    def tree_summary(self) -> str:
        """Short text summary of the non-terminal goal tree for LLM prompts."""
        lines: list[str] = []
        for g in sorted(self._goals.values(), key=lambda x: (-x.priority, x.id)):
            if g.is_terminal():
                continue
            lines.append(g.summary_line())
        return "\n".join(lines) if lines else "(no goals)"

    def check_exploration_budget(self, current_tick: int) -> None:
        """Auto-fail goals that have exceeded their exploration budget."""
        for goal in list(self.active_goals()):
            if goal.exploration_budget is not None:
                age = current_tick - goal.created_tick
                if age >= goal.exploration_budget:
                    self.fail(goal.id, f"Exploration budget exceeded ({age} ticks)")
