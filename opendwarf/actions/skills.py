"""Multi-tick deterministic skills.

A skill is activated when the LLM picks its intent, then stepped once per game
tick by the loop until it reports DONE or INTERRUPTED — no LLM calls in between.
Each terminal result carries a *factual* outcome string that feeds the agent's
decision history and scratchpad (closing the feedback loop).
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from opendwarf.memory.asked_topics import AskedTopics
from opendwarf.spatial.chunk_map import Cell
from opendwarf.spatial.compass import DELTA_TO_KEY, NAME_TO_DELTA, sign

if TYPE_CHECKING:
    from opendwarf.dfhack.lua_executor import LuaExecutor
    from opendwarf.spatial.chunk_map import ChunkMap, Pos
    from opendwarf.spatial.extractor import MapExtractor
    from opendwarf.spatial.pathfinder import Pathfinder
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

# Compass tables (DELTA_TO_KEY / NAME_TO_DELTA) live in opendwarf.spatial.compass.

# Vertical traversal keys. LIVE-VERIFY: exact interface_key names for adventure
# stair/ramp climbing are unconfirmed offline; adjust after testing in DF.
_CLIMB_UP_KEY = "A_MOVE_UP"
_CLIMB_DOWN_KEY = "A_MOVE_DOWN"


class SkillStatus(enum.Enum):
    RUNNING = "running"
    DONE = "done"
    INTERRUPTED = "interrupted"


@dataclass
class SkillResult:
    status: SkillStatus
    outcome: str = ""  # factual, e.g. "moved 14 tiles, reached Ironhold gate"

    @classmethod
    def running(cls) -> "SkillResult":
        return cls(SkillStatus.RUNNING)

    @classmethod
    def done(cls, outcome: str) -> "SkillResult":
        return cls(SkillStatus.DONE, outcome)

    @classmethod
    def interrupted(cls, reason: str) -> "SkillResult":
        return cls(SkillStatus.INTERRUPTED, reason)


@dataclass
class SkillContext:
    """Shared handles available to every skill."""
    lua: "LuaExecutor"
    chunk_map: "ChunkMap"
    pathfinder: "Pathfinder"
    extractor: "MapExtractor"
    asked_topics: "AskedTopics | None" = None
    conv_tracker: "object | None" = None  # loop's _ConversationTracker (record_choice/start)


class Skill:
    """Base class. Subclasses implement step()."""

    name: str = "skill"

    def __init__(self, ctx: SkillContext) -> None:
        self.ctx = ctx

    def step(self, state: "GameState") -> SkillResult:  # pragma: no cover - abstract
        raise NotImplementedError

    # Shared interrupt policy — combat / forced conversation / pending text
    def _check_interrupts(self, state: "GameState") -> SkillResult | None:
        if state.hostile_units:
            return SkillResult.interrupted("hostile unit appeared")
        if state.conversation_phase != "none":
            return SkillResult.interrupted("conversation started")
        if state.showing_announcements:
            return SkillResult.interrupted("announcement appeared")
        return None


# ----------------------------------------------------------------------
# RouteExecutor — follow an A* path to a position or unit
# ----------------------------------------------------------------------

class RouteExecutor(Skill):
    """Path to a fixed absolute position, a tracked unit, or a frontier."""

    name = "route"

    def __init__(
        self,
        ctx: SkillContext,
        *,
        goal: "Pos | None" = None,
        target_unit_id: int | None = None,
        frontier_dir: tuple[int, int] | None = None,
        label: str = "",
        max_steps: int = 40,
    ) -> None:
        super().__init__(ctx)
        self._goal = goal
        self._target_unit_id = target_unit_id
        self._frontier_dir = frontier_dir
        self._label = label or (goal and f"{goal}") or "target"
        self._max_steps = max_steps
        self._steps = 0
        self._start_abs: Pos | None = None
        self._expected: Pos | None = None  # where we expect to be after last move
        self._last_total_move: int | None = None
        self._path: list[Pos] = []
        self._blocked_replans = 0

    # -- goal resolution -------------------------------------------------

    def _resolve_goal(self, state: "GameState") -> "Pos | None":
        if self._target_unit_id is not None:
            for u in state.nearby_units:
                if u.id == self._target_unit_id and u.position is not None:
                    return self.ctx.extractor.to_abs(u.position.x, u.position.y, u.position.z)
            return None  # unit no longer visible
        return self._goal

    # -- main loop -------------------------------------------------------

    def step(self, state: "GameState") -> SkillResult:
        interrupt = self._check_interrupts(state)
        if interrupt is not None:
            return interrupt

        cur = self.ctx.extractor.adventurer_abs(state)
        if cur is None:
            return SkillResult.interrupted("lost local position")
        if self._start_abs is None:
            self._start_abs = cur

        # Verify the previous move actually happened; if not, the tile we tried
        # to enter is wrong — downgrade it and force a replan.
        if self._expected is not None:
            moved = self._observed_move(state, cur)
            if not moved:
                logger.info("RouteExecutor: blocked entering %s, downgrading + replan", self._expected)
                self.ctx.chunk_map.downgrade(*self._expected)
                self._path = []
                self._blocked_replans += 1
                # Stuck guard: repeated blocks with no net displacement means the
                # map model disagrees with reality here — stop wandering and hand
                # back to the LLM with an honest outcome.
                if self._blocked_replans >= 5 and self._net_tiles(cur) < 2:
                    return self._finish(
                        f"stuck: {self._blocked_replans} blocked moves with no progress", cur)
            else:
                self._blocked_replans = 0
                if cur[2] != self._expected[2] and self._prev_pos is not None:
                    # Observed a z-transition — confirm the ramp/stair edge we used
                    self.ctx.chunk_map.confirm_vertical(*self._prev_pos, cur[2] - self._prev_pos[2])
        self._expected = None

        # Arrival check
        goal = self._resolve_goal(state)
        if goal is None and self._target_unit_id is not None:
            return self._finish("target unit no longer visible", cur)
        if goal is not None and self._arrived(state, cur, goal):
            return self._finish(f"arrived at {self._label}", cur)

        if self._steps >= self._max_steps:
            return self._finish(
                f"stopped after {self._steps} moves ({self._label} not reached"
                f"{self._remaining_str(state)})", cur)

        # (Re)compute path if needed
        if not self._path:
            self._path = self._compute_path(state, cur, goal)
            if not self._path:
                return self._finish(f"no path toward {self._label}", cur)

        # Pop already-reached prefix
        while self._path and self._path[0] == cur:
            self._path.pop(0)
        if not self._path:
            return self._finish(f"arrived at {self._label}", cur)

        nxt = self._path[0]
        key = self._move_key(cur, nxt)
        if key is None:
            return self._finish(f"unreachable next tile {nxt} from {cur}", cur)

        self._prev_pos = cur
        self._expected = nxt
        self._last_total_move = state.total_move
        self.ctx.lua.execute_action(key)
        self._steps += 1
        return SkillResult.running()

    # -- helpers ---------------------------------------------------------

    _prev_pos: "Pos | None" = None

    def _observed_move(self, state: "GameState", cur: "Pos") -> bool:
        if state.total_move >= 0 and self._last_total_move is not None and self._last_total_move >= 0:
            return state.total_move != self._last_total_move
        return cur == self._expected  # fall back to position equality

    def _arrived(self, state: "GameState", cur: "Pos", goal: "Pos") -> bool:
        if self._target_unit_id is not None:
            for u in state.nearby_units:
                if u.id == self._target_unit_id:
                    return u.distance <= 1
            return False
        return cur == goal

    def _compute_path(self, state: "GameState", cur: "Pos", goal: "Pos | None") -> list["Pos"]:
        pf = self.ctx.pathfinder
        if self._frontier_dir is not None:
            path = pf.frontier_path(cur, self._frontier_dir, now_tick=state.tick_counter)
            if path:
                return path
            # No known frontier in that cone (e.g. standing in unfetched space
            # where everything around is UNKNOWN). Fall back to best-effort
            # directional progress: partial A* toward a synthetic goal N tiles
            # out — UNKNOWN is traversable at high cost, so this keeps moving.
            dx, dy = self._frontier_dir
            synth = (cur[0] + dx * 12, cur[1] + dy * 12, cur[2])
            return pf.find_path(cur, synth, now_tick=state.tick_counter, partial=True) or []
        if goal is None:
            return []
        return pf.find_path(cur, goal, now_tick=state.tick_counter, partial=True) or []

    @staticmethod
    def _move_key(cur: "Pos", nxt: "Pos") -> str | None:
        dx, dy, dz = nxt[0] - cur[0], nxt[1] - cur[1], nxt[2] - cur[2]
        if dz != 0 and dx == 0 and dy == 0:
            return _CLIMB_UP_KEY if dz > 0 else _CLIMB_DOWN_KEY
        return DELTA_TO_KEY.get((dx, dy))

    def _net_tiles(self, cur: "Pos | None") -> int:
        """Net displacement from where the skill started (chebyshev, xy)."""
        if cur is None or self._start_abs is None:
            return 0
        return max(abs(cur[0] - self._start_abs[0]), abs(cur[1] - self._start_abs[1]))

    def _remaining_str(self, state: "GameState") -> str:
        if self._target_unit_id is None:
            return ""
        for u in state.nearby_units:
            if u.id == self._target_unit_id:
                return f", still {u.distance} tiles away"
        return ", target no longer visible"

    def _finish(self, outcome: str, cur: "Pos | None" = None) -> SkillResult:
        # Report NET displacement, not keys sent — "40 tiles moved" while
        # ping-ponging in place misled the LLM into retrying (observed live).
        net = self._net_tiles(cur)
        return SkillResult.done(f"{outcome} ({self._steps} moves, net {net} tiles)")


# ----------------------------------------------------------------------
# FastTravelController — deterministic world-map travel to a site
# ----------------------------------------------------------------------

class FastTravelController(Skill):
    """Enter travel mode, steer toward a site by army-position delta, stop at it.

    Army coords are 3x embark-tile coords; the help dialog needs a mouse click
    (auto-dismissed in opendwarf--act.lua) but travel exit is the A_END_TRAVEL
    key (LIVE-VERIFIED 2026-06-11). Entering travel while obstructed (site
    walls/rivers) wedges the UI: menu=Travel with no army created and all
    travel input rejected — detected below via fast_travel_active without an
    army position, recovered with travel_exit.
    """

    name = "fast_travel"
    _STOP_DISTANCE = 2  # DF often can't get closer than ~2 tiles to a site center
    # The travel army is created only AFTER the first travel-map move (see step());
    # allow this many move attempts to form it before declaring the spot obstructed.
    _ARMY_FORM_ATTEMPTS = 5
    # World-map steering is a straight-line nudge; if the army_pos does not change
    # for this many consecutive steering moves, a terrain barrier is pinning it.
    _STALL_LIMIT = 6

    def __init__(self, ctx: SkillContext, *, site_id: int | None, site_name: str, max_steps: int = 60) -> None:
        super().__init__(ctx)
        self._site_id = site_id
        self._site_name = site_name
        self._max_steps = max_steps
        self._steps = 0
        self._phase = "enter"  # enter -> travel -> exit -> done
        self._origin_site = ""
        self._no_army_steps = 0
        self._engage_waits = 0
        # Target direction captured pre-travel (from the real adventurer position,
        # so accurate) — used to nudge the army into existence before army_pos exists.
        self._initial_dir: str | None = None
        # No-progress (stall) tracking once the army exists.
        self._last_army_pos: "Position | None" = None
        self._no_progress_steps = 0

    def step(self, state: "GameState") -> SkillResult:
        if state.hostile_units:
            # Forced out of travel into combat
            return SkillResult.interrupted("hostile encounter during travel")

        if self._phase == "enter":
            self._origin_site = state.site_name or ""
            # Capture the target direction now, while we still have the accurate
            # pre-travel adventurer position (nearby_sites directions are computed
            # from army_pos during travel, which does not exist yet).
            tgt = self._find_target(state)
            self._initial_dir = tgt.direction.lower() if tgt and tgt.direction else None
            self.ctx.lua.execute_action("travel_enter")
            self._phase = "travel"
            return SkillResult.running()

        if self._phase == "exit":
            self.ctx.lua.execute_action("travel_exit")
            self._phase = "done"
            return SkillResult.running()

        if self._phase == "done":
            return SkillResult.done(f"arrived near {self._site_name}")

        # --- travel phase ---
        if not state.fast_travel_active:
            # Not (yet) in travel mode; if we've already taken steps, assume exited
            if self._steps == 0:
                # Travel may never engage (obstructed by site walls/rivers —
                # the game shows a message and stays in Default). Bounded wait.
                self._engage_waits += 1
                if self._engage_waits >= 3:
                    msg = (state.message or "").strip()
                    detail = f" (game says: {msg!r})" if msg else ""
                    return SkillResult.interrupted(
                        "travel mode did not engage — likely obstructed by site "
                        f"walls/rivers{detail}; move to open ground first"
                    )
                return SkillResult.running()  # wait for travel mode to engage
            return SkillResult.done(f"left travel mode near {self._site_name}")

        if state.fast_travel_army_pos is None:
            # The travel army is created only AFTER the first travel-map move is
            # issued (LIVE-VERIFIED 2026-06-12: army_id=-1 / not_moved=1 right
            # after travel_enter; one A_MOVE forms it with a valid army_pos). So
            # we MUST move here, not wait — the old code waited for an army that
            # never comes without a move and bailed every time, which is why full
            # fast-travel e2e never engaged. Nudge toward the target; only if
            # repeated moves still form no army is the spot genuinely obstructed
            # (site walls/rivers/map edge — observed: some tiles reject the move,
            # not_moved stays 1).
            self._no_army_steps += 1
            if self._no_army_steps > self._ARMY_FORM_ATTEMPTS:
                self.ctx.lua.execute_action("travel_exit")
                self._phase = "done"
                return SkillResult.interrupted(
                    f"travel blocked — army never formed after {self._ARMY_FORM_ATTEMPTS} "
                    "moves (obstructed by site walls/rivers/edge); move to open ground first"
                )
            key = self._formation_key(state)
            if key is None:
                self.ctx.lua.execute_action("travel_exit")
                self._phase = "done"
                return SkillResult.interrupted("no direction toward target to begin travel")
            self.ctx.lua.execute_action(key)
            self._steps += 1
            return SkillResult.running()
        self._no_army_steps = 0

        target = self._find_target(state)
        if target is not None and target.distance is not None and target.distance <= self._STOP_DISTANCE:
            self._phase = "exit"
            return SkillResult.running()

        if self._steps >= self._max_steps:
            self._phase = "exit"
            return SkillResult.running()

        # No-progress stall detection. Straight-line world-map steering cannot
        # route around terrain barriers (mountains/oceans/site edges) — LIVE-VERIFIED
        # 2026-06-12: leaving this town eastward the army advances ~3 world-tiles
        # then a barrier pins army_pos. Rather than burn the whole step budget
        # pushing into the wall, hand back with an honest outcome so the LLM can
        # try another route. (World-level routing is JourneyBehavior, M3.)
        ap = state.fast_travel_army_pos
        if ap == self._last_army_pos:
            self._no_progress_steps += 1
            if self._no_progress_steps >= self._STALL_LIMIT:
                self._phase = "exit"
                tinfo = f" toward {self._site_name}" if self._site_name else ""
                return SkillResult.interrupted(
                    f"travel stalled at world {ap}{tinfo} — terrain blocks the direct "
                    f"route ({self._steps} moves); approach from another direction"
                )
        else:
            self._no_progress_steps = 0
            self._last_army_pos = ap

        direction = target.direction.lower() if target else None
        delta = NAME_TO_DELTA.get(direction or "", None)
        if delta is None:
            # No target / unknown direction — stop and hand back
            self._phase = "exit"
            return SkillResult.running()

        key = DELTA_TO_KEY[delta]
        self.ctx.lua.execute_action(key)
        self._steps += 1
        return SkillResult.running()

    def _formation_key(self, state: "GameState") -> str | None:
        """Direction key to nudge the army into existence before army_pos exists.

        Prefer the target direction captured pre-travel (accurate); fall back to
        the current approximate target if that is unavailable.
        """
        direction = self._initial_dir
        if direction is None:
            tgt = self._find_target(state)
            direction = tgt.direction.lower() if tgt and tgt.direction else None
        delta = NAME_TO_DELTA.get(direction or "")
        return DELTA_TO_KEY[delta] if delta else None

    def _find_target(self, state: "GameState"):
        candidates = [
            s for s in state.nearby_sites
            if s.name != self._origin_site and (s.distance is None or s.distance > 0)
        ]
        if self._site_id is not None:
            for s in candidates:
                if s.id == self._site_id:
                    return s
        for s in candidates:
            if s.name == self._site_name:
                return s
        return candidates[0] if candidates else None


# ----------------------------------------------------------------------
# MenuSkill — generic multi-step menu sequence (pickup/drop/wield/quest log)
# ----------------------------------------------------------------------

@dataclass
class _MenuStep:
    action: str  # passed to lua.execute_action
    done_when: Callable[["GameState"], bool] | None = None


class QuestLogSkill(Skill):
    """Open the adventure log, read quest objectives while it's on screen, close it.

    Relies on opendwarf--state.lua reading viewscreen_adventure_logst when open
    (state.quests is populated). LIVE-VERIFY: the interface key to open the log
    ('A_LOG') and the log viewscreen quest fields are unconfirmed offline.
    """

    name = "quest_log"

    def __init__(self, ctx: SkillContext) -> None:
        super().__init__(ctx)
        self._phase = "open"
        self._quests: list[str] = []

    def step(self, state: "GameState") -> SkillResult:
        if self._phase == "open":
            self.ctx.lua.execute_action("A_LOG")
            self._phase = "read"
            return SkillResult.running()
        if self._phase == "read":
            # Fresh extraction (the loop re-extracts between steps) sees the open log
            self._quests = list(state.quests)
            self.ctx.lua.execute_action("LEAVESCREEN")
            self._phase = "done"
            return SkillResult.running()
        if self._quests:
            return SkillResult.done("quests: " + "; ".join(self._quests[:5]))
        return SkillResult.done("no quests found in the log")


class FleeSkill(Skill):
    """Route away from all hostile units, re-planning each step.

    Strategy: compute the absolute direction away from the hostile centroid, then
    use A* (partial path) toward a flee target 30 tiles away in that direction.
    Re-plans every step because hostiles move. Terminates when:
    - no hostiles remain, OR
    - nearest hostile >= SAFE_DISTANCE tiles away, OR
    - max steps reached.
    """

    name = "flee"
    SAFE_DISTANCE = 15
    MAX_STEPS = 50
    FLEE_REACH = 30  # tiles ahead to target when computing flee path

    def __init__(self, ctx: SkillContext) -> None:
        super().__init__(ctx)
        self._steps = 0
        self._last_total_move: int | None = None
        self._expected: "Pos | None" = None

    def step(self, state: "GameState") -> SkillResult:
        if not state.hostile_units:
            return SkillResult.done(f"fled — no hostiles ({self._steps} steps)")
        min_dist = min(u.distance for u in state.hostile_units)
        if min_dist >= self.SAFE_DISTANCE:
            return SkillResult.done(f"safe ({min_dist} tiles from nearest hostile)")
        if self._steps >= self.MAX_STEPS:
            return SkillResult.done(f"fled {self._steps} steps (reached step limit)")

        cur = self.ctx.extractor.adventurer_abs(state)
        if cur is None:
            return SkillResult.interrupted("lost local position")

        # Detect if last move was blocked; downgrade that tile
        if self._expected is not None:
            moved = (state.total_move >= 0 and self._last_total_move is not None
                     and state.total_move != self._last_total_move)
            if not moved:
                self.ctx.chunk_map.downgrade(*self._expected)
            self._expected = None

        # Compute flee direction: away from hostile centroid (abs coords)
        flee_dir = self._flee_direction(state, cur)
        if flee_dir is None:
            return SkillResult.interrupted("cannot determine flee direction")

        # Target 30 tiles away in flee direction
        flee_goal = (
            cur[0] + flee_dir[0] * self.FLEE_REACH,
            cur[1] + flee_dir[1] * self.FLEE_REACH,
            cur[2],
        )
        path = self.ctx.pathfinder.find_path(cur, flee_goal, now_tick=state.tick_counter, partial=True)
        if not path:
            # Fallback: frontier in flee direction
            path = self.ctx.pathfinder.frontier_path(cur, flee_dir, now_tick=state.tick_counter)
        if not path or len(path) < 2:
            return SkillResult.interrupted("no flee path")

        # Skip already-at nodes
        while len(path) > 1 and path[0] == cur:
            path.pop(0)

        nxt = path[0] if path[0] != cur else path[1] if len(path) > 1 else None
        if nxt is None or nxt == cur:
            return SkillResult.interrupted("no movement possible")

        key = RouteExecutor._move_key(cur, nxt)
        if key is None:
            return SkillResult.interrupted(f"no move key for {cur}->{nxt}")

        self._last_total_move = state.total_move
        self._expected = nxt
        self.ctx.lua.execute_action(key)
        self._steps += 1
        return SkillResult.running()

    def _flee_direction(self, state: "GameState", cur: "Pos") -> tuple[int, int] | None:
        """Return unit 8-direction vector pointing away from hostile centroid."""
        xs, ys = [], []
        for u in state.hostile_units:
            if u.position is None:
                continue
            try:
                abs_pos = self.ctx.extractor.to_abs(u.position.x, u.position.y, u.position.z)
                xs.append(abs_pos[0])
                ys.append(abs_pos[1])
            except RuntimeError:
                pass
        if not xs:
            return None
        cx = sum(xs) // len(xs)
        cy = sum(ys) // len(ys)
        dx = cur[0] - cx
        dy = cur[1] - cy
        if dx == 0 and dy == 0:
            return (0, -1)  # default north if already on top
        return sign(dx, dy)


class SleepSkill(Skill):
    """Open the sleep menu and sleep until dawn.

    Four-phase flow (LIVE-VERIFIED 2026-06-10):
      A_SLEEP → opens menu (or Help dialog first, handled by auto-handler)
      A_SLEEP_SLEEP → selects "Sleep" mode (default is "Wait")
      A_SLEEP_DAWN → selects "Until dawn" duration
      SELECT → confirms and starts the sleep time-skip

    After SELECT the game fast-forwards to dawn; the loop resumes when
    TAKING_INPUT returns True. Ambush during sleep auto-interrupts via the
    announcement auto-handler (the loop handles showing_announcements already).

    L2 note: outdoors at night risks bogeymen unless companions are present;
    towns, inns, and structures are safe. Ask building owners for permission.
    """

    name = "sleep"

    def __init__(self, ctx: SkillContext) -> None:
        super().__init__(ctx)
        self._phase = "open"
        self._wait_ticks = 0

    def step(self, state: "GameState") -> SkillResult:
        if self._phase == "open":
            self.ctx.lua.execute_action("A_SLEEP")
            self._phase = "wait_menu"
            return SkillResult.running()

        if self._phase == "wait_menu":
            # Wait for Sleep menu to appear (Help dialog is handled by auto-handler)
            if state.focus_state and "Sleep" in state.focus_state:
                self.ctx.lua.execute_action("A_SLEEP_SLEEP")  # select 's Sleep' (vs 'w Wait')
                self._phase = "dawn"
                return SkillResult.running()
            self._wait_ticks += 1
            if self._wait_ticks > 8:
                return SkillResult.interrupted("sleep menu did not appear")
            return SkillResult.running()

        if self._phase == "dawn":
            self.ctx.lua.execute_action("A_SLEEP_DAWN")  # select 'd Until dawn'
            self._phase = "confirm"
            return SkillResult.running()

        if self._phase == "confirm":
            self.ctx.lua.execute_action("SELECT")  # 'Enter Go ahead'
            return SkillResult.done("slept until dawn")

        return SkillResult.done("sleep complete")


class TalkToSkill(Skill):
    """Route to a specific NPC and initiate conversation.

    Phase flow:
      route → RouteExecutor to get adjacent (dist ≤ 1) to target unit
      open  → send A_TALK (auto-handler selects start_shoutingst in the background)
      wait  → wait for dialogue phase, then DONE

    DF picks who you talk to based on proximity — being adjacent to the target NPC
    ensures they're the one selected by start_shoutingst.

    Attributes set on completion (read by the loop to update ConversationTracker):
      selected_npc_name: str | None
      selected_npc_hf_id: int | None
    """

    name = "talk_to"

    def __init__(self, ctx: SkillContext, *, unit_id: int, npc_name: str) -> None:
        super().__init__(ctx)
        self._unit_id = unit_id
        self._npc_name = npc_name
        self._phase = "route"
        self._wait_ticks = 0
        self._route: RouteExecutor | None = None
        self.selected_npc_name: str | None = None
        self.selected_npc_hf_id: int | None = None

    def step(self, state: "GameState") -> SkillResult:
        if state.hostile_units:
            return SkillResult.interrupted("hostile unit appeared")

        if self._phase == "route":
            # Check if already adjacent
            unit = next((u for u in state.nearby_units if u.id == self._unit_id), None)
            if unit is None:
                return SkillResult.interrupted(f"{self._npc_name} no longer visible")
            if unit.distance <= 1:
                self._phase = "open"
                return self.step(state)  # advance immediately
            # Route to be adjacent
            if self._route is None:
                self._route = RouteExecutor(self.ctx, target_unit_id=self._unit_id,
                                            label=self._npc_name, max_steps=30)
            result = self._route.step(state)
            if result.status is SkillStatus.DONE:
                self._phase = "open"
            elif result.status is SkillStatus.INTERRUPTED:
                return SkillResult.interrupted(f"could not reach {self._npc_name}: {result.outcome}")
            return SkillResult.running()

        if self._phase == "open":
            self._record_npc(state)
            self.ctx.lua.execute_action("A_TALK")
            self._phase = "wait"
            self._wait_ticks = 0
            return SkillResult.running()

        if self._phase == "wait":
            # Auto-handler selects start_shoutingst between steps; we just wait for dialogue
            if state.conversation_phase == "dialogue":
                return SkillResult.done(f"talking to {self._npc_name}")
            if state.conversation_phase == "none" and self._wait_ticks > 2:
                return SkillResult.interrupted("conversation did not start")
            self._wait_ticks += 1
            if self._wait_ticks > 12:
                return SkillResult.interrupted("dialogue did not start after talk")
            return SkillResult.running()

        return SkillResult.done(f"talking to {self._npc_name}")

    def _record_npc(self, state: "GameState") -> None:
        self.selected_npc_name = self._npc_name
        for u in state.nearby_units:
            if u.id == self._unit_id and u.hist_fig_id >= 0:
                self.selected_npc_hf_id = u.hist_fig_id
                return


# Topic-priority keywords for the deterministic conversation sweep. Higher tier
# is asked first. Matched against the AskedTopics-normalized choice text.
_TOPIC_HIGH = (
    "trouble", "rumor", "recent event", "happened", "news", "incident",
    "beast", "monster", "creature", "bandit", "criminal", "outlaw",
    "missing", "murder", "kidnap", "conflict", "war", "raid", "attack",
)
_TOPIC_MED = (
    "ruler", "lord", "leader", "lady", "hero", "legend", "artifact",
    "temple", "keep", "tomb", "lair", "site", "town", "directions",
    "whereabouts", "where", "family", "relationship",
)
# Role-play emotes / statements / social actions — the adventurer SAYS these
# rather than gathering information, and some are socially harmful (accusations,
# insults). The deterministic sweep skips them entirely (live-observed the blind
# sweep otherwise wastes budget on "accuse listener of being a night creature"
# and "state feelings about a snow storm"). Matched on the raw lowercased text.
_TOPIC_AVOID = (
    "accuse", "insult", "demand", "state feelings", "state your",
    "express", "complain", "boast", "claim to", "threaten", "convey",
    "make an introduction", "introduce yourself", "say farewell",
)


class ConverseSkill(Skill):
    """Hold a full multi-turn conversation with one NPC, deterministically.

    Subsumes TalkToSkill's route+open, then *sweeps* the dialogue: each round it
    picks the highest-priority topic the agent has NOT already asked this NPC
    (per AskedTopics), records it, and selects it; when DF closes the dialogue
    after the exchange (its usual behavior) it re-initiates A_TALK and continues,
    until no new top-level topic remains or the topic budget is hit. This removes
    the per-round LLM hop of re-issuing talk_to + re-picking a topic (ROADMAP 3.1
    tail): one `converse:<id>` intent -> a whole conversation, zero LLM between.

    Scope (v1): top-level dialogue topics only. It never enters "(new menu)"
    submenus (e.g. the 98-item directions list) — AskedTopics.is_topic() already
    rejects "(new menu)"/meta choices, so when only those remain it finishes DONE
    and hands back to the LLM, which can dive a submenu deliberately.

    Bookkeeping uses the loop-shared handles on SkillContext (asked_topics,
    conv_tracker) so dedup + transcript accumulation match the LLM path exactly.
    """

    name = "converse"

    _MAX_TOPICS = 4   # topics asked per sweep
    _OPEN_WAIT = 12   # ticks to wait for dialogue after A_TALK
    _RESP_WAIT = 8    # ticks to wait for response/close after a pick

    def __init__(self, ctx: SkillContext, *, unit_id: int, npc_name: str,
                 npc_hf_id: int | None = None, max_topics: int | None = None) -> None:
        super().__init__(ctx)
        self._unit_id = unit_id
        self._npc_name = npc_name
        self._npc_hf_id = npc_hf_id
        self._max_topics = max_topics or self._MAX_TOPICS
        self._phase = "route"
        self._route: RouteExecutor | None = None
        self._wait = 0
        self._asked_count = 0
        self._reengages = 0
        self._npc_selected = False
        self.selected_npc_name: str | None = npc_name
        self.selected_npc_hf_id: int | None = npc_hf_id

    # NPC identity key — same scheme as _ConversationGuard.key / AskedTopics:
    # str(hist_fig_id) when >= 0, else "name:<name>".
    def _key(self) -> str | None:
        if self._npc_hf_id is not None and self._npc_hf_id >= 0:
            return str(self._npc_hf_id)
        if self._npc_name:
            return f"name:{self._npc_name}"
        return None

    def _select_npc_choice(self, state: "GameState") -> int | None:
        """Index to pick on the A_TALK select_npc menu to reach our target.

        Prefer an exact named match (rare — only if DF lists our NPC directly);
        otherwise the "address nearest" system option — we routed adjacent, so
        the nearest IS our target. This is talk_new_conversationst on first
        contact and talk_existing_conversationst on re-engagement (both
        live-verified); match any *_conversationst option. Never select
        assume_identity (the identity-creation derail)."""
        choices = state.conversation_choices
        named = next((c for c in choices
                      if "adventure_option_" not in c.text.lower()
                      and c.text == self._npc_name), None)
        if named is not None:
            return named.index
        sysmatch = next(
            (c for c in choices
             if "assume_identity" not in c.text.lower()
             and ("_conversationst" in c.text.lower()
                  or any(k in c.text.lower() for k in ("start_shout", "address")))),
            None)
        return sysmatch.index if sysmatch is not None else None

    def step(self, state: "GameState") -> SkillResult:
        if state.hostile_units:
            return SkillResult.interrupted("hostile unit appeared")

        if self._phase == "route":
            unit = next((u for u in state.nearby_units if u.id == self._unit_id), None)
            if unit is None:
                return SkillResult.interrupted(f"{self._npc_name} no longer visible")
            if (self._npc_hf_id is None or self._npc_hf_id < 0) and unit.hist_fig_id >= 0:
                self._npc_hf_id = unit.hist_fig_id
                self.selected_npc_hf_id = unit.hist_fig_id
            if unit.distance <= 1:
                self._phase = "open"
                return self.step(state)
            if self._route is None:
                self._route = RouteExecutor(self.ctx, target_unit_id=self._unit_id,
                                            label=self._npc_name, max_steps=30)
            result = self._route.step(state)
            if result.status is SkillStatus.DONE:
                self._phase = "open"
            elif result.status is SkillStatus.INTERRUPTED:
                return SkillResult.interrupted(f"could not reach {self._npc_name}: {result.outcome}")
            return SkillResult.running()

        if self._phase == "open":
            tracker = getattr(self.ctx, "conv_tracker", None)
            if tracker is not None:
                tracker.start(self._npc_name, self._npc_hf_id)
            self.ctx.lua.execute_action("A_TALK")
            self._phase = "await"
            self._wait = 0
            self._npc_selected = False
            return SkillResult.running()

        if self._phase == "await":
            if state.conversation_phase == "dialogue" and state.conversation_choices:
                self._phase = "pick"
                return self.step(state)
            # A_TALK opens a select_npc menu. DF does NOT list arbitrary nearby
            # units — it offers one specific named NPC plus a "talk_new /
            # address-nearest" system option (live-verified). We routed adjacent,
            # so "address nearest" reliably reaches our target. Select it once;
            # never touch assume_identity. (The loop auto-handler only shouts when
            # there are zero named choices, so the skill must drive this itself.)
            if (state.conversation_phase == "select_npc" and state.conversation_choices
                    and not self._npc_selected):
                idx = self._select_npc_choice(state)
                if idx is not None:
                    self.ctx.lua.execute_action(f"conversation:{idx}")
                    self._npc_selected = True
                    return SkillResult.running()
            self._wait += 1
            if self._wait > self._OPEN_WAIT:
                if self._asked_count > 0:
                    return self._finish()
                return SkillResult.interrupted("dialogue did not start")
            return SkillResult.running()

        if self._phase == "pick":
            if self._asked_count >= self._max_topics:
                return self._leave(state)
            choice = self._choose_topic(state)
            if choice is None:
                return self._leave(state)
            tracker = getattr(self.ctx, "conv_tracker", None)
            asked = getattr(self.ctx, "asked_topics", None)
            if tracker is not None:
                tracker.record_choice(choice.text)
            if asked is not None:
                asked.record(self._key(), choice.text, state.tick_counter)
            self.ctx.lua.execute_action(f"conversation:{choice.index}")
            self._asked_count += 1
            self._phase = "response"
            self._wait = 0
            return SkillResult.running()

        if self._phase == "response":
            # DF usually closes the dialogue after one exchange. Wait for it to
            # either re-present the menu (some NPCs keep talking) or drop to none.
            if state.conversation_phase == "dialogue" and state.conversation_choices:
                self._phase = "pick"
                return self.step(state)
            if state.conversation_phase == "none":
                if self._asked_count >= self._max_topics:
                    return self._finish()
                self._reengages += 1
                if self._reengages > self._max_topics + 2:
                    return self._finish()
                self._phase = "open"
                return SkillResult.running()
            self._wait += 1
            if self._wait > self._RESP_WAIT:
                return self._finish()
            return SkillResult.running()

        return self._finish()

    # -- helpers --

    def _choose_topic(self, state: "GameState"):
        key = self._key()
        asked = getattr(self.ctx, "asked_topics", None)
        best = None
        best_score = -1
        for c in state.conversation_choices:
            low = c.text.lower()
            if "adventure_option_" in low:
                continue
            if not AskedTopics.is_topic(c.text):  # meta/nav/(new menu) — skip in v1
                continue
            if "menu)" in low:  # any submenu-opener (e.g. "… (group naming menu)")
                continue
            if any(a in low for a in _TOPIC_AVOID):  # emotes/statements/accusations
                continue
            if asked is not None and asked.was_asked(key, c.text):
                continue
            score = self._score(c.text)
            if score > best_score:
                best, best_score = c, score
        return best

    @staticmethod
    def _score(text: str) -> int:
        low = AskedTopics.normalize(text)
        if any(k in low for k in _TOPIC_HIGH):
            return 3
        if any(k in low for k in _TOPIC_MED):
            return 2
        return 1

    def _leave(self, state: "GameState") -> SkillResult:
        # Close politely if a goodbye/leave choice is on the current menu.
        if state.conversation_phase == "dialogue":
            bye = next((c for c in state.conversation_choices
                        if any(s in c.text.lower()
                               for s in ("say goodbye", "goodbye", "leave", "stop talking"))),
                       None)
            if bye is not None:
                self.ctx.lua.execute_action(f"conversation:{bye.index}")
        return self._finish()

    def _finish(self) -> SkillResult:
        n = self._asked_count
        return SkillResult.done(
            f"talked with {self._npc_name}: asked {n} topic{'s' if n != 1 else ''}")


class CombatStrikeSkill(Skill):
    """Drive the adventure attack menu to land one default melee strike.

    DF v50's `A_ATTACK` opens `dungeonmode/Attack` — a MOUSE-DRIVEN multi-step
    menu (keyboard SELECT/scroll do NOT advance it, LIVE-VERIFIED v0.53.14). This
    is the only way to strike a *neutral* creature: bump-to-attack auto-strikes
    genuine hostiles but merely opens this menu (dealing no damage) for wildlife.

    The menu advances through `adventure.attack.mode`, surfaced as
    `state.attack_menu_mode`. We click one option per tick and wait for `mode` to
    change (re-extracted between steps), via these stages (all LIVE-VERIFIED):

      open      press A_ATTACK                              → mode 0 (+ first-use Help)
      mode 0    click target row (by unit_choice index)     → mode 2
      mode 2    click "Strike"                              → mode 3
      mode 3    click body part (first = "upper body")      → mode 4
      mode 4    click weapon/attack (first = primary weapon)→ resolves, menu closes

    All picks are deterministic defaults — nearest target, plain Strike, upper
    body, primary weapon. LLM strike-choice (aim a wound, wrestle, charge) is a
    later upgrade (NORTHSTAR M2). The first A_ATTACK of a session stacks a Help
    overlay; we dismiss it via clickok ourselves so the skill works inside a
    Behavior too (where the loop's Help auto-handler is bypassed)."""

    name = "combat_strike"
    _MAX_WAIT = 8  # ticks to wait for a mode transition before bailing

    def __init__(self, ctx: SkillContext, *, unit_id: int, target_name: str = "") -> None:
        super().__init__(ctx)
        self._unit_id = unit_id
        self._target_name = target_name or "creature"
        self._opened = False
        self._struck = False
        self._last_mode = -99
        self._wait = 0

    def step(self, state: "GameState") -> SkillResult:
        focus = state.focus_state or ""
        # First A_ATTACK stacks a Help overlay over the Attack menu; clear it.
        if "Help" in focus:
            try:
                self.ctx.lua.run_script("opendwarf--clickok")
            except Exception:  # noqa: BLE001
                pass
            return SkillResult.running()

        if not self._opened:
            # A_ATTACK is swallowed while a prior strike's combat animation plays
            # (player_control_state != TAKING_INPUT) — wait for input before pressing,
            # else a back-to-back grind strike fails with "menu did not open".
            if not state.taking_input:
                return SkillResult.running()
            self.ctx.lua.execute_action("press:A_ATTACK")
            self._opened = True
            self._wait = 0
            return SkillResult.running()

        if not state.attack_menu_open:
            # Menu closed: either the strike resolved (success) or it never opened.
            if self._struck:
                return SkillResult.done(f"struck {self._target_name} via attack menu")
            if not state.taking_input:
                return SkillResult.running()  # still resolving — don't count toward bail
            self._wait += 1
            if self._wait > self._MAX_WAIT:
                return SkillResult.interrupted(
                    f"attack menu did not open against {self._target_name}")
            return SkillResult.running()

        mode = state.attack_menu_mode
        # Wait for the previous click to take effect (the menu re-renders a frame
        # after the click; mode advances then). Only act when mode changes.
        if mode == self._last_mode:
            self._wait += 1
            if self._wait > self._MAX_WAIT:
                self.ctx.lua.execute_action("press:LEAVESCREEN")  # back out cleanly
                return SkillResult.interrupted(
                    f"attack menu stuck at mode {mode} on {self._target_name}")
            return SkillResult.running()
        self._last_mode = mode
        self._wait = 0

        if mode == 0:          # pick target
            self.ctx.lua.execute_action(f"attack_pick:{self._target_index(state)}")
        elif mode == 2:        # pick move — the default lethal "Strike"
            self.ctx.lua.execute_action("attack_strike")
        elif mode == 3:        # pick body part — first row ("upper body": solid hit)
            self.ctx.lua.execute_action("attack_pick:0")
        elif mode == 4:        # pick weapon/attack — first row (primary weapon) resolves
            self.ctx.lua.execute_action("attack_pick:0")
            self._struck = True
        else:                  # unexpected intermediate mode — take the first option
            self.ctx.lua.execute_action("attack_pick:0")
        return SkillResult.running()

    def _target_index(self, state: "GameState") -> int:
        """Row index of our target in the unit_choice list (screen-row order).
        Defaults to 0 (the nearest target) when the id isn't listed."""
        try:
            return state.attack_unit_choice.index(self._unit_id)
        except ValueError:
            return 0


class MenuSkill(Skill):
    """Runs a fixed sequence of menu inputs. Each step optionally waits for a
    state predicate before advancing. Used for item interaction."""

    name = "menu"

    def __init__(self, ctx: SkillContext, steps: list[_MenuStep], *, label: str, outcome: str) -> None:
        super().__init__(ctx)
        self._steps = steps
        self._i = 0
        self._label = label
        self._outcome = outcome
        self._fired = False

    def step(self, state: "GameState") -> SkillResult:
        if self._i >= len(self._steps):
            return SkillResult.done(self._outcome)
        cur = self._steps[self._i]
        if not self._fired:
            self.ctx.lua.execute_action(cur.action)
            self._fired = True
            return SkillResult.running()
        # Advance when predicate satisfied (or immediately if none)
        if cur.done_when is None or cur.done_when(state):
            self._i += 1
            self._fired = False
        return SkillResult.running() if self._i < len(self._steps) else SkillResult.done(self._outcome)


# ----------------------------------------------------------------------
# UnstickSkill — deterministic recovery ladder for unknown / wedged screens
# ----------------------------------------------------------------------

# Known-safe focus strings that indicate successful recovery.
_RECOVERED_FOCUS_PATTERNS = (
    "dungeonmode/Default",
    "dungeonmode/Conversation",
    "dungeonmode/Sleep",
    "dungeonmode/Travel",
)

# Keys to NEVER send during recovery (irreversible or dangerous).
_RECOVERY_BANNED_KEYS: frozenset[str] = frozenset({
    "LEAVESCREEN_ALL", "A_RETIRE", "A_ABANDON",
    "QUIT", "MAIN_MENU", "RESET_INTERFACE_CONFIRM",
})


def _focus_recovered(focus: str | None) -> bool:
    if not focus:
        return False
    return any(p in focus for p in _RECOVERED_FOCUS_PATTERNS)


class UnstickSkill(Skill):
    """Deterministic recovery ladder for unknown / wedged UI screens.

    Ladder (NORTHSTAR II.7 layer 3):
      Step 1 — inspect_ui(); if DFHack Lua screens sit above viewscreen_dungeonmodest,
               dismiss them via dismiss_dfhack_screens.
      Step 2 — send LEAVESCREEN up to 2 times, checking focus after each.
      Step 3 — derive key candidates from focus-string tokens via find_keys(),
               try them one at a time (focus-checked), max 5 attempts,
               prioritising A_END_*-style names.
      Step 4 — give up: INTERRUPTED carrying inspect_ui summary + key candidates,
               so the escape-hatch LLM prompt is enriched.

    The skill is invoked by the loop's unknown-focus path *before* triggering the
    LLM escape hatch, so a recoverable wedge never costs an LLM call.
    """

    name = "unstick"

    # Maximum key candidates to try in step 3.
    _MAX_KEY_ATTEMPTS = 5

    def __init__(self, ctx: SkillContext, *, wedged_focus: str | None = None) -> None:
        super().__init__(ctx)
        self._wedged_focus: str | None = wedged_focus
        self._phase = "inspect"          # inspect → dismiss → leavescreen → keys → give_up
        self._leavescreen_count = 0
        self._key_candidates: list[str] = []
        self._key_attempt = 0
        self._inspect_summary: str = ""
        self._last_focus: str | None = wedged_focus
        # Only declare recovery when focus actually *changes* from the starting focus.
        # This prevents the skill from immediately returning DONE when the wedged
        # focus happens to be in _RECOVERED_FOCUS_PATTERNS (e.g. dungeonmode/Travel).
        self._first_step_done: bool = False

    # -- main loop -------------------------------------------------------

    def step(self, state: "GameState") -> SkillResult:
        cur_focus = state.focus_state or ""

        # Successful recovery: focus changed to a known-safe state AND it's
        # different from where we started (prevents false positives when the
        # wedged focus is itself in the recovered-patterns set).
        if self._first_step_done and _focus_recovered(cur_focus) and cur_focus != self._wedged_focus:
            return SkillResult.done(f"recovered to {cur_focus}")

        self._first_step_done = True

        if self._phase == "inspect":
            return self._do_inspect()

        if self._phase == "dismiss":
            return self._do_dismiss(cur_focus)

        if self._phase == "leavescreen":
            return self._do_leavescreen(cur_focus)

        if self._phase == "keys":
            return self._do_keys(cur_focus)

        # give_up
        return self._give_up()

    # -- phase handlers --------------------------------------------------

    def _do_inspect(self) -> SkillResult:
        try:
            ui = self.ctx.lua.inspect_ui()
            parts: list[str] = []
            if ui.get("focus_strings"):
                parts.append("focus=" + str(ui["focus_strings"]))
            if ui.get("menu"):
                m = ui["menu"]
                parts.append(f"menu={m.get('name','?')}({m.get('value','?')})")
            if ui.get("viewscreen_stack"):
                parts.append("stack=" + str(ui["viewscreen_stack"]))
            if ui.get("travel"):
                t = ui["travel"]
                parts.append(
                    f"travel(origin={t.get('origin_x')},{t.get('origin_y')},"
                    f"army_id={t.get('player_army_id')})"
                )
            self._inspect_summary = "; ".join(parts) or "(inspect empty)"
        except Exception as exc:  # noqa: BLE001
            self._inspect_summary = f"(inspect failed: {exc})"

        # Determine if DFHack screens need dismissal.
        stack = []
        try:
            ui_data = self.ctx.lua.inspect_ui()
            stack = ui_data.get("viewscreen_stack", [])
        except Exception:  # noqa: BLE001
            pass

        has_dfhack_screen = any(
            ("dfhack" in s.lower() or ("lua" in s.lower() and "dungeonmode" not in s.lower()))
            for s in stack
        )
        if has_dfhack_screen:
            self._phase = "dismiss"
        else:
            self._phase = "leavescreen"
        return SkillResult.running()

    def _do_dismiss(self, cur_focus: str) -> SkillResult:
        try:
            result = self.ctx.lua.execute_action("dismiss_dfhack_screens")
            logger.info("UnstickSkill dismiss_dfhack_screens: %s", result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("UnstickSkill: dismiss failed: %s", exc)
        time.sleep(0.3)
        self._phase = "leavescreen"
        return SkillResult.running()

    def _do_leavescreen(self, cur_focus: str) -> SkillResult:
        if self._leavescreen_count >= 2:
            self._phase = "keys"
            return self._prepare_key_candidates(cur_focus)

        self.ctx.lua.execute_action("LEAVESCREEN")
        self._leavescreen_count += 1
        time.sleep(0.4)
        return SkillResult.running()

    def _prepare_key_candidates(self, cur_focus: str) -> SkillResult:
        """Derive key candidates from focus-string tokens via find_keys()."""
        candidates: list[str] = []
        # Extract tokens from focus string (e.g. "dungeonmode/Travel" → ["TRAVEL"])
        tokens: list[str] = []
        for part in cur_focus.replace("/", " ").replace("_", " ").split():
            t = part.strip().upper()
            if t and len(t) >= 3:
                tokens.append(t)

        seen: set[str] = set()
        for token in tokens:
            try:
                keys = self.ctx.lua.find_keys(token)
            except Exception:  # noqa: BLE001
                keys = []
            for k in keys:
                if k not in seen and k not in _RECOVERY_BANNED_KEYS:
                    seen.add(k)
                    candidates.append(k)

        # Prioritise: A_END_* and LEAVESCREEN-style names first
        def _priority(k: str) -> int:
            ku = k.upper()
            if ku.startswith("A_END_"):
                return 0
            if "LEAVE" in ku:
                return 1
            if ku.startswith("A_"):
                return 2
            return 3

        candidates.sort(key=_priority)
        self._key_candidates = candidates[: self._MAX_KEY_ATTEMPTS]
        self._key_attempt = 0
        logger.info("UnstickSkill key candidates for focus %r: %s", cur_focus, self._key_candidates)
        return SkillResult.running()

    def _do_keys(self, cur_focus: str) -> SkillResult:
        if not self._key_candidates or self._key_attempt >= len(self._key_candidates):
            return self._give_up()

        key = self._key_candidates[self._key_attempt]
        self._key_attempt += 1
        logger.info("UnstickSkill: trying key %r (attempt %d)", key, self._key_attempt)
        self.ctx.lua.execute_action(f"press:{key}")
        time.sleep(0.5)
        return SkillResult.running()

    def _give_up(self) -> SkillResult:
        msg = (
            f"UnstickSkill gave up — {self._inspect_summary}; "
            f"key_candidates={self._key_candidates}"
        )
        logger.warning(msg)
        return SkillResult.interrupted(msg)
