"""GrindCombatBehavior — autopilot that gets the adventurer strong by fighting.

This is the core "getting strong" behavior (NORTHSTAR M2). It loops a simple
state machine within a radius of an anchor point:

  SEEK    — no hostile in sight: wander the area (ring waypoints) to find one.
  ENGAGE  — a policy-authorized hostile is present: close to it and bump-attack
            (DF v50 bump-to-attack — moving into the tile delivers the strike).
  RECOVER — folded into the top of each step: eat/drink from inventory per policy.
  CHECK   — `until` predicate (skill level reached or tick budget spent) → DONE.

The loop's interrupt checker runs *before* every behavior step, so by the time
`_step` sees a hostile it is already known policy-authorized (unauthorized races,
too many opponents, or low health interrupt to the LLM instead). That keeps this
behavior simple: it never decides *whether* to fight, only *how* to grind the
fights the Policy already sanctioned. Flee is the LLM/skill path via the health
and hostile interrupts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opendwarf.actions.skills import RouteExecutor, SkillStatus, _DELTA_TO_KEY
from opendwarf.behaviors.base import Behavior, BehaviorResult
from opendwarf.behaviors.tiers import tier_of

if TYPE_CHECKING:
    from opendwarf.actions.skills import Skill, SkillContext
    from opendwarf.behaviors.policy import Policy
    from opendwarf.spatial.chunk_map import Pos
    from opendwarf.state.game_state import GameState, UnitInfo

logger = logging.getLogger(__name__)


class GrindCombatBehavior(Behavior):
    name = "grind_combat"

    def __init__(
        self,
        ctx: "SkillContext",
        policy: "Policy",
        *,
        radius: int = 12,
        until: dict | None = None,
    ) -> None:
        super().__init__(ctx, policy)
        self._radius = max(4, radius)
        self._until = dict(until or {})
        self._center: "Pos | None" = None
        self._waypoints: list["Pos"] = []
        self._wp_index = 0
        self._seek_route: "Skill | None" = None
        self._physio: "Skill | None" = None
        self._skill_levels: dict[str, int] = {}   # last-seen skill levels (for level-up digest)
        self._engaged_ids: set[int] = set()        # hostile ids we were fighting last step
        self._kills = 0

    # ------------------------------------------------------------------

    def _step(self, state: "GameState") -> BehaviorResult:
        self._record_skill_levels(state)

        # 1. Finish any in-flight physio sub-skill before anything else.
        if self._physio is not None:
            res = self._physio.step(state)
            if res.status is SkillStatus.RUNNING:
                return BehaviorResult.running()
            self._physio = None
            self.digest.mark_action()
            return BehaviorResult.running()

        # 2. `until` budget / target reached?
        done = self._until_reached(state)
        if done is not None:
            return BehaviorResult.done(done)

        # 3. Opportunistic eat/drink (RECOVER) — only when not mid-fight.
        if not state.hostile_units and self._maybe_serve_physio(state):
            return BehaviorResult.running()

        # 4. ENGAGE — any present hostile is already policy-authorized (interrupt
        #    checker ran first), so fight the nearest one.
        if state.hostile_units:
            self._seek_route = None
            return self._engage(state)

        # 5. SEEK — no hostiles: note any kills, then wander to find more.
        self._note_kills(state)
        return self._seek(state)

    # ------------------------------------------------------------------
    # ENGAGE
    # ------------------------------------------------------------------

    def _engage(self, state: "GameState") -> BehaviorResult:
        target = self._nearest_hostile(state)
        if target is None:
            return BehaviorResult.running()
        self._engaged_ids = {u.id for u in state.hostile_units}

        if self._adjacent(state, target):
            # Adjacent — bump-attack via the registry's target-aware resolver,
            # which maps to a directional move key into the hostile's tile.
            from opendwarf.actions.registry import default_registry

            dispatch = default_registry().resolve(f"attack:{target.id}", state, self.ctx)
            if dispatch.error or not dispatch.key:
                logger.debug("grind_combat: attack resolve failed (%s); stepping toward target", dispatch.error)
            else:
                self.ctx.lua.execute_action(dispatch.key)
                self.digest.add(f"struck {target.race or 'enemy'} (tier {tier_of(target.race)})")
                self.digest.mark_action()
                return BehaviorResult.running()

        # Not adjacent (or wrong z) — take one step toward it. We cannot use
        # RouteExecutor here (it interrupts on any hostile), so step manually.
        if self._step_toward(state, target):
            self.digest.mark_action()
            return BehaviorResult.running()
        # Could not advance — hand back; the LLM/flee path takes over.
        return BehaviorResult.needs_llm(f"cannot reach hostile {target.race} to engage")

    def _step_toward(self, state: "GameState", unit: "UnitInfo") -> bool:
        """Send one move toward `unit`. Returns False if no step is possible."""
        cur = self.ctx.extractor.adventurer_abs(state)
        if cur is None or unit.position is None:
            return False
        try:
            goal = self.ctx.extractor.to_abs(unit.position.x, unit.position.y, unit.position.z)
        except RuntimeError:
            return False
        path = self.ctx.pathfinder.find_path(cur, goal, now_tick=state.tick_counter, partial=True)
        nxt = next((p for p in path if p != cur), None) if path else None
        if nxt is None:
            # Fall back to a straight directional step toward the target.
            dx = (goal[0] > cur[0]) - (goal[0] < cur[0])
            dy = (goal[1] > cur[1]) - (goal[1] < cur[1])
            key = _DELTA_TO_KEY.get((dx, dy))
        else:
            key = RouteExecutor._move_key(cur, nxt)
        if key is None:
            return False
        self.ctx.lua.execute_action(key)
        return True

    # ------------------------------------------------------------------
    # SEEK
    # ------------------------------------------------------------------

    def _seek(self, state: "GameState") -> BehaviorResult:
        if not self._waypoints and not self._build_waypoints(state):
            return BehaviorResult.needs_llm("could not establish a search area to grind in")

        if self._seek_route is None:
            target = self._waypoints[self._wp_index]
            self._seek_route = RouteExecutor(
                self.ctx, goal=target, label=f"seek {self._wp_index + 1}", max_steps=self._radius * 3
            )
        res = self._seek_route.step(state)
        if res.status is SkillStatus.RUNNING:
            self.digest.mark_action()
            return BehaviorResult.running()
        # Route terminal (DONE, or INTERRUPTED on a hostile that appeared mid-route —
        # next step's ENGAGE branch picks it up).
        self._seek_route = None
        self._wp_index = (self._wp_index + 1) % len(self._waypoints)
        return BehaviorResult.running()

    def _build_waypoints(self, state: "GameState") -> bool:
        center = self.ctx.extractor.adventurer_abs(state)
        if center is None:
            return False
        self._center = center
        cx, cy, cz = center
        r = self._radius
        # A ring of 8 points around the anchor; RouteExecutor degrades gracefully
        # on unreachable points (partial path / downgrade), so exact walkability
        # need not be guaranteed here.
        self._waypoints = [
            (cx + r, cy, cz), (cx + r, cy + r, cz), (cx, cy + r, cz), (cx - r, cy + r, cz),
            (cx - r, cy, cz), (cx - r, cy - r, cz), (cx, cy - r, cz), (cx + r, cy - r, cz),
        ]
        logger.info("GrindCombatBehavior: search ring of %d waypoints, radius %d", len(self._waypoints), r)
        return True

    # ------------------------------------------------------------------
    # Physiology (RECOVER) — mirrors PatrolBehavior's self-service
    # ------------------------------------------------------------------

    def handles_physio(self, state: "GameState", policy: "Policy | None") -> bool:
        if state.drowsy_critical:
            return False  # sleep needs a safe-location decision — hand back
        pol = policy or self.policy
        if state.hungry_critical and not (pol.eat_when_hungry and self._first_food(state) is not None):
            return False
        if state.thirsty_critical and not (pol.drink_when_thirsty and self._first_drink(state) is not None):
            return False
        return True

    def _maybe_serve_physio(self, state: "GameState") -> bool:
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
    # Progress tracking
    # ------------------------------------------------------------------

    def _record_skill_levels(self, state: "GameState") -> None:
        for sk in state.skills:
            prev = self._skill_levels.get(sk.id)
            if prev is not None and sk.level > prev:
                self.digest.add(f"+{sk.level - prev} {sk.id}", sk.level - prev)
            self._skill_levels[sk.id] = sk.level

    def _note_kills(self, state: "GameState") -> None:
        """Hostiles we were engaging that are no longer present count as defeated."""
        if not self._engaged_ids:
            return
        present = {u.id for u in state.nearby_units}
        gone = self._engaged_ids - present
        if gone:
            self._kills += len(gone)
            self.digest.add("defeated enemy", len(gone))
        self._engaged_ids = set()

    def _until_reached(self, state: "GameState") -> str | None:
        """Return a DONE outcome string if the `until` predicate is met, else None."""
        for key, want in self._until.items():
            if key == "max_ticks":
                if isinstance(want, int) and self.digest.ticks >= want:
                    return f"grind budget spent ({self.digest.ticks} ticks, {self._kills} kills)"
                continue
            if key == "max_kills":
                if isinstance(want, int) and self._kills >= want:
                    return f"reached {self._kills} kills"
                continue
            # Otherwise treat the key as a skill id and `want` as a target level.
            if isinstance(want, int):
                level = self._skill_levels.get(key)
                if level is not None and level >= want:
                    return f"{key} reached level {level} (target {want})"
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _adjacent(state: "GameState", unit: "UnitInfo") -> bool:
        """Chebyshev-adjacent on the same z-level (a bump-attack target). Diagonal
        neighbours are adjacent for combat even though their Manhattan distance is 2."""
        pos = state.adventurer_position
        if pos is None or unit.position is None or unit.position.z != pos.z:
            return False
        return max(abs(unit.position.x - pos.x), abs(unit.position.y - pos.y)) == 1

    @staticmethod
    def _nearest_hostile(state: "GameState") -> "UnitInfo | None":
        hostiles = [u for u in state.hostile_units if u.position is not None]
        if not hostiles:
            return None
        return min(hostiles, key=lambda u: (u.distance, u.id))
