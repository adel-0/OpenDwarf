"""PatrolBehavior — walk a fixed loop of waypoints, re-pathing, self-serving
food/water from inventory per Policy. The M1 exit-criterion behavior: testable
unattended in a safe town with no combat. It demonstrates the whole layer —
child-skill composition, the digest, physio self-service, and clean handback to
the LLM on any interrupt (which the loop checks before each step).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opendwarf.actions.skills import RouteExecutor, SkillStatus
from opendwarf.behaviors.base import Behavior, BehaviorResult

if TYPE_CHECKING:
    from opendwarf.actions.skills import Skill, SkillContext
    from opendwarf.behaviors.policy import Policy
    from opendwarf.spatial.chunk_map import Pos
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)


class PatrolBehavior(Behavior):
    name = "patrol"

    def __init__(
        self,
        ctx: "SkillContext",
        policy: "Policy",
        *,
        waypoints: "list[Pos] | None" = None,
        radius: int = 8,
        laps: int | None = None,
    ) -> None:
        super().__init__(ctx, policy)
        self._waypoints: list[Pos] = list(waypoints) if waypoints else []
        self._radius = max(2, radius)
        self._laps_limit = laps
        self._wp_index = 0
        self._laps_done = 0
        self._route: "Skill | None" = None
        self._physio: "Skill | None" = None

    # ------------------------------------------------------------------

    def _step(self, state: "GameState") -> BehaviorResult:
        # 1. Finish any in-flight physio sub-skill before anything else.
        if self._physio is not None:
            res = self._physio.step(state)
            if res.status is SkillStatus.RUNNING:
                return BehaviorResult.running()
            self.digest.add(f"physio: {res.outcome}")
            self.digest.mark_action()
            self._physio = None
            return BehaviorResult.running()

        # 2. Serve hunger/thirst opportunistically (per policy) before walking.
        if self._maybe_serve_physio(state):
            return BehaviorResult.running()

        # 3. Lazily generate the patrol loop from the current position.
        if not self._waypoints:
            self._waypoints = self._generate_loop(state)
            if not self._waypoints:
                return BehaviorResult.needs_llm("could not establish patrol waypoints")
            logger.info("PatrolBehavior: %d waypoints, radius %d", len(self._waypoints), self._radius)

        # 4. Walk toward the current waypoint.
        if self._route is None:
            target = self._waypoints[self._wp_index]
            self._route = RouteExecutor(
                self.ctx, goal=target, label=f"waypoint {self._wp_index + 1}", max_steps=self._radius * 4
            )
        res = self._route.step(state)
        if res.status is SkillStatus.RUNNING:
            self.digest.mark_action()
            return BehaviorResult.running()

        # Route terminal (DONE or INTERRUPTED — interrupts are caught by the loop
        # before step(); a route INTERRUPTED here is a lost-position edge case).
        self.digest.add(f"reached waypoint {self._wp_index + 1}")
        self._route = None
        self._advance_waypoint()
        if self._laps_limit is not None and self._laps_done >= self._laps_limit:
            return BehaviorResult.done(f"completed {self._laps_done} patrol lap(s)")
        return BehaviorResult.running()

    # ------------------------------------------------------------------
    # Physiology self-service
    # ------------------------------------------------------------------

    def handles_physio(self, state: "GameState", policy: "Policy | None") -> bool:
        """Patrol can resolve hunger/thirst if it carries food/water and policy
        allows. It cannot resolve drowsiness (needs a safe sleep decision) — so a
        critical drowsy state still hands back to the LLM.
        """
        if state.drowsy_critical:
            return False
        pol = policy or self.policy
        if state.hungry_critical and not (pol.eat_when_hungry and self._first_food(state) is not None):
            return False
        if state.thirsty_critical and not (pol.drink_when_thirsty and self._first_drink(state) is not None):
            return False
        return True

    def _maybe_serve_physio(self, state: "GameState") -> bool:
        """Start an eat/drink sub-skill if needed and allowed. Returns True if one
        was started (caller should yield RUNNING)."""
        if self.policy.eat_when_hungry and state.hungry:
            idx = self._first_food(state)
            if idx is not None and self._start_consume(state, idx):
                self.digest.add(f"ate {state.inventory[idx].name}")
                return True
        if self.policy.drink_when_thirsty and state.thirsty:
            idx = self._first_drink(state)
            if idx is not None and self._start_consume(state, idx):
                self.digest.add(f"drank {state.inventory[idx].name}")
                return True
        return False

    def _start_consume(self, state: "GameState", inv_index: int) -> bool:
        from opendwarf.actions.registry import default_registry

        item = state.inventory[inv_index]
        verb = "eat" if item.is_food else "drink"
        dispatch = default_registry().resolve(f"{verb}_{inv_index}", state, self.ctx)
        if dispatch.skill is None:
            logger.warning("PatrolBehavior: could not build %s skill for inv %d", verb, inv_index)
            return False
        self._physio = dispatch.skill
        return True

    @staticmethod
    def _first_food(state: "GameState") -> int | None:
        return next((i for i, it in enumerate(state.inventory) if it.is_food), None)

    @staticmethod
    def _first_drink(state: "GameState") -> int | None:
        return next((i for i, it in enumerate(state.inventory) if it.is_drink), None)

    # ------------------------------------------------------------------
    # Waypoints
    # ------------------------------------------------------------------

    def _advance_waypoint(self) -> None:
        self._wp_index += 1
        if self._wp_index >= len(self._waypoints):
            self._wp_index = 0
            self._laps_done += 1
            self.digest.add("completed patrol lap")

    def _generate_loop(self, state: "GameState") -> "list[Pos]":
        center = self.ctx.extractor.adventurer_abs(state)
        if center is None:
            return []
        cx, cy, cz = center
        r = self._radius
        # A diamond loop around the start; RouteExecutor handles unreachable
        # corners gracefully (partial path / downgrade), so exact walkability of
        # each point need not be guaranteed here.
        return [
            (cx + r, cy, cz),
            (cx, cy + r, cz),
            (cx - r, cy, cz),
            (cx, cy - r, cz),
        ]
