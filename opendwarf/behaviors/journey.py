"""JourneyBehavior — autopilot that travels to a distant site across the world map.

This is the M3 keystone (NORTHSTAR §II.3.2). It is the persistent, multi-leg
counterpart to `FastTravelController`: where the FTC is a single straight push
that hands back on the first complication (it powers the one-shot `goto_site`
intent), `JourneyBehavior` keeps going — re-entering travel after every forced
exit and routing *around* terrain barriers — until it reaches the destination.

It owns the travel loop directly (it does NOT compose `FastTravelController`,
which would exit/re-enter travel every leg). The state, per step:

  (not in travel)  → re-enter travel (`travel_enter`). Capture the destination
                     bearing first, from the accurate pre-travel position.
  (in travel, no army) → the army forms only after the first travel-map move
                     (LIVE-VERIFIED 2026-06-12): nudge toward the bearing to form
                     it; bail to the LLM if it never forms (obstructed tile).
  (in travel, army)  → STEER one embark-tile toward the destination bearing.
                     Straight-line steering cannot cross mountains/oceans/site
                     edges — when `army_pos` stops changing the heading is blocked,
                     so ROTATE the heading (±45°, ±90°, ±135°) to slip around the
                     barrier (a collision-feedback "bug" router; we have no world
                     terrain map, only whether each move advanced the army).
  (arrived)          → exit travel → DONE.

Forced exits (an encounter, a night event, critical hunger) are handled by the
loop's interrupt checker, which suspends this behavior *before* `_step` runs and
reaches the LLM with the digest. On `resume`, this behavior sees "not in travel,
not arrived" and re-enters travel — that is the multi-leg journey. Critical
physiology is NOT self-served (see `handles_physio`): the journey hands back so
the LLM can eat/drink/sleep, then resumes.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from opendwarf.actions.skills import _DELTA_TO_KEY, _NAME_TO_DELTA
from opendwarf.behaviors.base import Behavior, BehaviorResult

if TYPE_CHECKING:
    from opendwarf.actions.skills import SkillContext
    from opendwarf.behaviors.policy import Policy
    from opendwarf.state.game_state import GameState, NearbySite, Position

logger = logging.getLogger(__name__)

# Compass ring, clockwise. Detour offsets rotate the base heading by this many
# 45° steps; ordered to try the shallowest deviations first (slip past a corner
# before committing to a wide swing), alternating right/left.
_RING = ("n", "ne", "e", "se", "s", "sw", "w", "nw")
_DETOUR_OFFSETS = (0, 1, -1, 2, -2, 3, -3)

# atan2(dy, dx) sector index (round(angle/45) % 8) → ring name. DF: +x=East,
# +y=South, so E=0°, SE=45°, S=90°, … going clockwise.
_SECTOR = ("e", "se", "s", "sw", "w", "nw", "n", "ne")


def _compass_ring(dx: int, dy: int) -> str | None:
    """Ring name (n/ne/…) toward (dx, dy) in embark-tile deltas, or None if zero."""
    if dx == 0 and dy == 0:
        return None
    return _SECTOR[round(math.degrees(math.atan2(dy, dx)) / 45) % 8]


class JourneyBehavior(Behavior):
    name = "journey"

    _STOP_DISTANCE = 2          # embark tiles; DF rarely lands exactly on a site center
    _FORM_ATTEMPTS = 5          # moves allowed to form the army before declaring obstruction
    _ENTER_ATTEMPTS = 3         # travel_enter attempts before declaring obstruction
    _STALL_LIMIT = 4            # army_pos unchanged this many steers ⇒ heading is blocked
    # Detour search exhausts at the last offset index; after that, give up to the LLM.
    _MAX_DETOUR = len(_DETOUR_OFFSETS) - 1

    def __init__(
        self,
        ctx: "SkillContext",
        policy: "Policy",
        *,
        site_id: int | None,
        site_name: str = "",
        world_pos: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(ctx, policy)
        self._site_id = site_id
        self._site_name = site_name
        # Optional absolute destination (embark-tile centre). Lets the behavior
        # steer toward a rumored site that is NOT yet in the nearby-site list.
        self._world_pos = world_pos
        self._initial_bearing: str | None = None
        self._enter_attempts = 0
        self._form_steps = 0
        self._detour = 0                       # index into _DETOUR_OFFSETS
        self._stall = 0
        self._last_army_pos: "Position | None" = None
        self._best_dist: int | None = None     # closest dest distance seen (progress marker)
        self._arriving = False                 # travel_exit issued, awaiting Default next tick

    # ------------------------------------------------------------------

    def _step(self, state: "GameState") -> BehaviorResult:
        dest_label = self._site_name or (f"site {self._site_id}" if self._site_id is not None else "destination")

        # Final hand-off: we issued travel_exit last tick on arrival.
        if self._arriving:
            return BehaviorResult.done(f"arrived near {dest_label}")

        # 1. Arrival — works whether or not we are still in travel mode.
        if self._arrived(state):
            if state.fast_travel_active:
                self.ctx.lua.execute_action("travel_exit")
                self._arriving = True
                self.digest.add(f"reached {dest_label}")
                return BehaviorResult.running()
            return BehaviorResult.done(f"arrived near {dest_label}")

        # 2. Not in travel ⇒ (re-)enter it. Capture the bearing now, while the
        #    pre-travel adventurer position still gives accurate site directions.
        if not state.fast_travel_active:
            if self._enter_attempts >= self._ENTER_ATTEMPTS:
                return BehaviorResult.needs_llm(
                    f"travel will not engage toward {dest_label} from here "
                    "(obstructed by site walls/rivers); move to open ground first"
                )
            self._capture_bearing(state)
            if self._initial_bearing is None:
                return BehaviorResult.needs_llm(
                    f"{dest_label} is not among the nearby sites — cannot set a travel bearing"
                )
            self.ctx.lua.execute_action("travel_enter")
            self._enter_attempts += 1
            return BehaviorResult.running()

        self._enter_attempts = 0

        # 3. In travel, army not yet formed — the first move creates it.
        if state.fast_travel_army_pos is None:
            self._form_steps += 1
            if self._form_steps > self._FORM_ATTEMPTS:
                self.ctx.lua.execute_action("travel_exit")
                return BehaviorResult.needs_llm(
                    f"travel army never formed after {self._FORM_ATTEMPTS} moves toward "
                    f"{dest_label} (obstructed tile/edge); move to open ground first"
                )
            key, _ = self._heading_key(state)
            if key is None:
                self.ctx.lua.execute_action("travel_exit")
                return BehaviorResult.needs_llm(f"lost the bearing to {dest_label}")
            self.ctx.lua.execute_action(key)
            return BehaviorResult.running()
        self._form_steps = 0

        # 4. STEER toward the destination, routing around barriers.
        return self._steer(state, dest_label)

    # ------------------------------------------------------------------
    # STEER + detour routing
    # ------------------------------------------------------------------

    def _steer(self, state: "GameState", dest_label: str) -> BehaviorResult:
        ap = state.fast_travel_army_pos

        # Progress: getting closer cancels any in-progress detour search and
        # resets to a direct heading (we found a way around).
        dist = self._dest_distance(state)
        if dist is not None and (self._best_dist is None or dist < self._best_dist):
            self._best_dist = dist
            self._detour = 0

        # Stall detection on the army position. Unchanged ⇒ the current heading is
        # pinned by terrain; rotate to the next detour heading.
        if ap == self._last_army_pos:
            self._stall += 1
            if self._stall >= self._STALL_LIMIT:
                self._stall = 0
                self._detour += 1
                if self._detour > self._MAX_DETOUR:
                    self.ctx.lua.execute_action("travel_exit")
                    return BehaviorResult.needs_llm(
                        f"cannot route around terrain to {dest_label} from world {ap} "
                        f"(tried {self._MAX_DETOUR} detour headings); approach from another "
                        "direction or pick a nearer goal"
                    )
                self.digest.add(f"barrier at {ap}: detour heading #{self._detour}")
        else:
            self._stall = 0
            self._last_army_pos = ap
            self.digest.mark_action()

        key, heading = self._heading_key(state)
        if key is None:
            self.ctx.lua.execute_action("travel_exit")
            return BehaviorResult.needs_llm(f"lost the bearing to {dest_label}")
        self.ctx.lua.execute_action(key)
        return BehaviorResult.running()

    # ------------------------------------------------------------------
    # Bearing / destination resolution
    # ------------------------------------------------------------------

    def _capture_bearing(self, state: "GameState") -> None:
        d = self._base_bearing(state)
        if d is not None:
            self._initial_bearing = d

    def _base_bearing(self, state: "GameState") -> str | None:
        """Direction (ring name) toward the destination from current info; falls
        back to the last captured bearing when the dest is momentarily out of the
        nearby-site list."""
        dest = self._find_dest(state)
        if dest is not None and dest.direction:
            d = dest.direction.lower()
            if d in _NAME_TO_DELTA:
                self._initial_bearing = d
                return d
        # No nearby-site bearing — steer by the absolute world position if we have
        # one (rumored/distant site not yet in view).
        if self._world_pos is not None:
            cur = self._current_world(state)
            if cur is not None:
                d = _compass_ring(self._world_pos[0] - cur[0], self._world_pos[1] - cur[1])
                if d is not None:
                    self._initial_bearing = d
                    return d
        return self._initial_bearing

    @staticmethod
    def _current_world(state: "GameState") -> tuple[int, int] | None:
        """Current adventurer position in embark-tile coords. During travel the
        army position (3× embark coords) is the live source; otherwise the
        extractor's player_world_x/y."""
        ap = state.fast_travel_army_pos
        if ap is not None:
            return (ap.x // 3, ap.y // 3)
        if state.player_world_x >= 0 and state.player_world_y >= 0:
            return (state.player_world_x, state.player_world_y)
        return None

    def _current_heading(self, state: "GameState") -> str | None:
        base = self._base_bearing(state)
        if base is None:
            return None
        if self._detour == 0:
            return base
        offset = _DETOUR_OFFSETS[min(self._detour, len(_DETOUR_OFFSETS) - 1)]
        idx = (_RING.index(base) + offset) % 8
        return _RING[idx]

    def _heading_key(self, state: "GameState") -> tuple[str | None, str | None]:
        h = self._current_heading(state)
        if h is None:
            return None, None
        delta = _NAME_TO_DELTA.get(h)
        if delta is None:
            return None, None
        return _DELTA_TO_KEY[delta], h

    def _find_dest(self, state: "GameState") -> "NearbySite | None":
        if self._site_id is not None:
            for s in state.nearby_sites:
                if s.id == self._site_id:
                    return s
        if self._site_name:
            for s in state.nearby_sites:
                if s.name == self._site_name:
                    return s
        return None

    def _dest_distance(self, state: "GameState") -> int | None:
        dest = self._find_dest(state)
        if dest is not None and dest.distance is not None:
            return dest.distance
        # Fall back to absolute-position distance for a rumored/distant target.
        if self._world_pos is not None:
            cur = self._current_world(state)
            if cur is not None:
                return abs(self._world_pos[0] - cur[0]) + abs(self._world_pos[1] - cur[1])
        return None

    def _arrived(self, state: "GameState") -> bool:
        # Standing inside the named destination (not while the travel overlay is up).
        if self._site_name and not state.fast_travel_active and state.site_name == self._site_name:
            return True
        dist = self._dest_distance(state)
        return dist is not None and dist <= self._STOP_DISTANCE
