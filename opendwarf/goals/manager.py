"""GoalManager — flat goal list with merged goal revision + strategic planning."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

from opendwarf.goals.model import CompletionType, Goal, GoalStatus, PlanStep

if TYPE_CHECKING:
    from opendwarf.observability import EventLogger
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

MAX_ACTIVE_GOALS = 3

_GOAL_REVISION_SYSTEM = """\
You are the goal and planning system for an AI adventurer in Dwarf Fortress Adventure Mode.
Your job is to maintain a short list of goals (max 3 active) and produce a tactical plan.

## Agent Perception Model — What The Agent Sees Each Turn
- A wide top-down map (~21x21 tiles) around the adventurer, built from a persistent
  explored-tile memory: . = floor, # = wall, + = door, < > = stairs, ~ = water,
  @ = you, u = friendly unit, h = hostile. Unexplored tiles show as ?.
- A list of nearby units (name, race, distance, compass direction, hostile/friendly).
- Nearby sites (towns, fortresses, etc.) with distance and direction.
- Current inventory, health, current site name, active quests (when known).
- Conversation choices (when in dialogue).
- A running scratchpad of its own notes carried across turns.

## Agent Actions — What The Agent Can Do
- **goto_site:[id]**: Deterministic fast-travel to a known site. Handles the whole journey.
- **goto_unit:[id]**: Pathfind (A*) to a specific visible unit; stops adjacent.
- **explore:[direction]**: Pathfind toward unexplored frontier in a compass direction.
- **goto_stairs:[up|down]**: Pathfind to the nearest known stairway for z-level travel.
- **move_[dir]**: Single-tile step (for precise positioning / combat).
- **talk**: Open conversation with an adjacent NPC, then select options by index.
- **attack**: Attack an adjacent hostile.
- **read_quest_log**: Open and read the adventure log for quest objectives.
- **pickup_N / drop_N / wield_N**: Item interaction.
- **wait / wait_long / rest**: Wait or rest.
- Pathfinding handles walls, doors, and routing automatically — the agent no longer
  gets stuck wall-following. It CAN now path to sites/units/stairs reliably.

## Plan Step Format
Each plan step MUST include a `completion` field — a machine-checkable condition.

Completion types:
- "goto" — reach a goto_* target (site, unit, frontier, stairs). Done when the
  movement skill arrives. Use this for most travel/approach steps now.
- "reach_site" — arrive at a named site. Done when site_name changes.
- "talk" — have a conversation. Done when a conversation ends.
- "approach_npc" — get adjacent to any non-hostile NPC. Done when dist<=1.
- "combat" — fight something. Done when combat resolves.
- "get_item" — acquire an item. Done when inventory increases.
- "action" — completes when a specific agent action finishes; requires
  "action_prefix", e.g. {"description": "Read the quest log", "completion":
  "action", "action_prefix": "read_quest_log"}.
- "travel" — move in a compass direction ~8+ tiles. REQUIRES "direction". (legacy)
- "generic" — no specific condition; timeout-only fallback (6 turns). Avoid.

Respond with ONLY a JSON object:
{
  "reasoning": "<brief explanation>",
  "goals": [
    {"id": "<existing_id or null for new>", "description": "...", "status": "ACTIVE|DONE|DROPPED"}
  ],
  "plan_steps": [
    {"description": "Fast-travel to the nearest town", "completion": "reach_site"},
    {"description": "Read the quest log for objectives", "completion": "action", "action_prefix": "read_quest_log"},
    {"description": "Talk to a townsperson about rumors", "completion": "talk"}
  ]
}

## Rules
- The goals list is the COMPLETE set of goals after revision. Omitted old goals are dropped.
- There must be 1-3 ACTIVE goals. First goal = most important.
- Mark goals DONE when achieved, DROPPED when no longer relevant.
- plan_steps: 3-6 steps for the top active goal. Each step MUST have "description" and "completion".
- Prefer concrete goals derived from quests and nearby sites/NPCs over invented ambitions.
- If health is critical or hostiles are nearby, focus goals on immediate survival.

## What Makes a Good Plan Step
GOOD: {"description": "Fast-travel to Ironhold to find the armorer", "completion": "reach_site"}
GOOD: {"description": "Approach and talk to the nearby townsperson", "completion": "talk"}
GOOD: {"description": "Explore east for an unmapped settlement", "completion": "goto"}

BAD: "Scan the area for settlements" — use explore/goto instead.
BAD: vague goals like "gain renown" or "ensure adequate supplies".
"""


class GoalManager:
    """Manages a flat goal list with integrated plan generation."""

    def __init__(self, llm: object, goals_dir: Path = Path("goals"), event_logger: "EventLogger | None" = None) -> None:
        self.llm = llm  # LLMClient with decide(system, turn) -> dict
        self.goals_dir = goals_dir
        self._event_logger = event_logger
        self.goals_file = goals_dir / "active_goals.json"
        self._goals: list[Goal] = []
        self._load()

        # Plan state (merged from StrategicPlanner)
        self._plan_steps: list[PlanStep] = []
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
    def current_step(self) -> PlanStep | None:
        if self.has_plan:
            return self._plan_steps[self._current_step]
        return None

    @property
    def current_step_text(self) -> str:
        step = self.current_step
        return step.description if step else ""

    def advance_step(self, reason: str = "completed", current_position: tuple[int, int, int] | None = None) -> bool:
        """Move to next plan step. Returns True if more steps remain."""
        if self._current_step < len(self._plan_steps) - 1:
            self._current_step += 1
            step = self._plan_steps[self._current_step]
            step.turns_elapsed = 0
            step.start_position = current_position  # Capture NOW, before any movement
            step.start_inventory_count = -1
            step.triggered = False
            logger.info(
                "Plan step advanced (%s) to %d/%d: %s (start_pos=%s)",
                reason, self._current_step + 1, len(self._plan_steps),
                step.description, current_position,
            )
            return True
        logger.info("Plan complete — all %d steps done (%s)", len(self._plan_steps), reason)
        return False

    def reset_plan(self) -> None:
        self._plan_steps = []
        self._current_step = 0

    def plan_summary(self) -> str:
        """Compact text block for injection into tactical prompts."""
        if not self._plan_steps:
            return ""
        step = self._plan_steps[self._current_step]
        lines = [
            f"Current plan ({self._current_step + 1}/{len(self._plan_steps)}):",
            f"  NOW: {step.description}",
        ]
        remaining = self._plan_steps[self._current_step + 1:]
        if remaining:
            lines.append(f"  NEXT: {remaining[0].description}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Plan step completion checking (6.5)
    # ------------------------------------------------------------------

    def check_step_completion(
        self,
        state: "GameState",
        triggers: list[str],
        last_action: str | None = None,
    ) -> bool:
        """Check if the current plan step's completion condition is met.

        Returns True if the step was completed and advanced (or plan exhausted).
        `last_action` is the loop's last finished action string (used by ACTION steps).
        """
        step = self.current_step
        if step is None:
            return False

        step.turns_elapsed += 1

        # Initialize start state on first check
        if step.start_position is None and state.adventurer_position:
            pos = state.adventurer_position
            step.start_position = (pos.x, pos.y, pos.z)
        if step.start_inventory_count < 0:
            step.start_inventory_count = len(state.inventory)

        completed = False
        reason = ""

        ct = step.completion_type

        if ct == CompletionType.TRAVEL:
            # Check if we've moved enough tiles from start position
            if step.start_position and state.adventurer_position:
                pos = state.adventurer_position
                dx = pos.x - step.start_position[0]
                dy = pos.y - step.start_position[1]
                dist = math.sqrt(dx * dx + dy * dy)
                if dist >= step.min_tiles:
                    completed = True
                    reason = f"traveled {dist:.0f} tiles"

        elif ct == CompletionType.TALK:
            if "dialogue_ended" in triggers:
                completed = True
                reason = "conversation completed"

        elif ct == CompletionType.REACH_SITE:
            if state.site_name and state.site_name != "unknown":
                completed = True
                reason = f"reached site: {state.site_name}"

        elif ct == CompletionType.COMBAT:
            if "combat_resolved" in triggers:
                completed = True
                reason = "combat resolved"

        elif ct == CompletionType.GET_ITEM:
            current_count = len(state.inventory)
            if step.start_inventory_count >= 0 and current_count > step.start_inventory_count:
                completed = True
                reason = f"inventory increased ({step.start_inventory_count} -> {current_count})"

        elif ct == CompletionType.APPROACH_NPC:
            # Check if any non-hostile unit is adjacent (dist <= 1)
            for u in state.nearby_units:
                if not u.is_hostile and u.distance <= 1:
                    completed = True
                    reason = f"adjacent to {u.name}"
                    break

        elif ct == CompletionType.GOTO:
            if "goto_arrived" in triggers:
                completed = True
                reason = "reached goto target"

        elif ct == CompletionType.ACTION:
            # Completes when the last finished action starts with action_prefix.
            if last_action and step.action_prefix and last_action.startswith(step.action_prefix):
                completed = True
                reason = f"action completed: {last_action}"

        # Fallback timeout for all step types
        if not completed and step.turns_elapsed >= step.max_turns:
            completed = True
            reason = f"timeout ({step.max_turns} turns)"
            logger.info("Plan step timed out: %s", step.description)

        if completed:
            step.triggered = True
            # Pass current position so next step can capture start position
            current_pos = None
            if state.adventurer_position:
                pos = state.adventurer_position
                current_pos = (pos.x, pos.y, pos.z)
            advanced = self.advance_step(reason, current_position=current_pos)
            if not advanced:
                self.reset_plan()
            return True

        return False

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

        # Build nearby units summary
        nearby_lines: list[str] = []
        for u in state.nearby_units[:8]:
            hostility = "HOSTILE" if u.is_hostile else "friendly"
            direction = ""
            if state.adventurer_position and u.position:
                dx = u.position.x - state.adventurer_position.x
                dy = u.position.y - state.adventurer_position.y
                direction = state._compass(dx, dy)
            nearby_lines.append(f"  {u.name} ({u.race}, {hostility}, dist={u.distance}, {direction})")
        nearby_text = "\n".join(nearby_lines) if nearby_lines else "  none"

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

Nearby units:
{nearby_text}

Current goals:
{chr(10).join(goal_lines)}

Current inventory summary:
{_inventory_summary(state)}

Respond with the JSON revision+plan object."""

        goals_before = [g.summary_line() for g in self._goals if g.is_active()]

        try:
            from opendwarf.llm.base import PromptBundle
            result = self.llm.decide(
                PromptBundle.simple(_GOAL_REVISION_SYSTEM, turn_prompt), caller="goal_revision"
            )
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

        # Apply plan steps (structured PlanStep objects)
        raw_steps = result.get("plan_steps", [])
        if raw_steps:
            parsed_steps: list[PlanStep] = []
            for s in raw_steps:
                if isinstance(s, dict):
                    parsed_steps.append(PlanStep.from_dict(s))
                elif isinstance(s, str):
                    # Backward compat: plain string → generic step
                    parsed_steps.append(PlanStep(description=s, completion_type=CompletionType.GENERIC))
                else:
                    continue
            self._plan_steps = parsed_steps
            self._current_step = 0
            # Capture current position for the first step
            if parsed_steps and state.adventurer_position:
                pos = state.adventurer_position
                parsed_steps[0].start_position = (pos.x, pos.y, pos.z)
                parsed_steps[0].start_inventory_count = len(state.inventory)
            logger.info("Plan generated (%d steps): %s",
                        len(self._plan_steps),
                        [(s.description[:40], s.completion_type.value) for s in self._plan_steps])
        else:
            top = self.top_goal()
            self._plan_steps = [PlanStep(description=top.description, completion_type=CompletionType.GENERIC)] if top else []
            self._current_step = 0

        self.save()

        if self._event_logger:
            self._event_logger.log_goal_event(
                event="revision",
                trigger=trigger,
                goals_before=goals_before,
                goals_after=[g.summary_line() for g in self._goals if g.is_active()],
                plan_steps=[s.to_dict() for s in self._plan_steps],
                reasoning=reasoning,
            )

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
