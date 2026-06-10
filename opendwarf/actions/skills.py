"""Multi-tick deterministic skills.

A skill is activated when the LLM picks its intent, then stepped once per game
tick by the loop until it reports DONE or INTERRUPTED — no LLM calls in between.
Each terminal result carries a *factual* outcome string that feeds the agent's
decision history and scratchpad (closing the feedback loop).
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from opendwarf.spatial.chunk_map import Cell

if TYPE_CHECKING:
    from opendwarf.dfhack.lua_executor import LuaExecutor
    from opendwarf.spatial.chunk_map import ChunkMap, Pos
    from opendwarf.spatial.extractor import MapExtractor
    from opendwarf.spatial.pathfinder import Pathfinder
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

# Absolute-delta -> move key (8 compass directions)
_DELTA_TO_KEY: dict[tuple[int, int], str] = {
    (0, -1): "A_MOVE_N", (0, 1): "A_MOVE_S", (1, 0): "A_MOVE_E", (-1, 0): "A_MOVE_W",
    (1, -1): "A_MOVE_NE", (-1, -1): "A_MOVE_NW", (1, 1): "A_MOVE_SE", (-1, 1): "A_MOVE_SW",
}
# Vertical traversal keys. LIVE-VERIFY: exact interface_key names for adventure
# stair/ramp climbing are unconfirmed offline; adjust after testing in DF.
_CLIMB_UP_KEY = "A_MOVE_UP"
_CLIMB_DOWN_KEY = "A_MOVE_DOWN"

_NAME_TO_DELTA: dict[str, tuple[int, int]] = {
    "n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0),
    "ne": (1, -1), "nw": (-1, -1), "se": (1, 1), "sw": (-1, 1),
}


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
            elif cur[2] != self._expected[2] and self._prev_pos is not None:
                # Observed a z-transition — confirm the ramp/stair edge we used
                self.ctx.chunk_map.confirm_vertical(*self._prev_pos, cur[2] - self._prev_pos[2])
        self._expected = None

        # Arrival check
        goal = self._resolve_goal(state)
        if goal is None and self._target_unit_id is not None:
            return self._finish("target unit no longer visible")
        if goal is not None and self._arrived(state, cur, goal):
            return self._finish(f"arrived at {self._label}")

        if self._steps >= self._max_steps:
            return self._finish(f"stopped after {self._steps} tiles ({self._label} not reached)")

        # (Re)compute path if needed
        if not self._path:
            self._path = self._compute_path(state, cur, goal)
            if not self._path:
                return self._finish(f"no path toward {self._label}")

        # Pop already-reached prefix
        while self._path and self._path[0] == cur:
            self._path.pop(0)
        if not self._path:
            return self._finish(f"arrived at {self._label}")

        nxt = self._path[0]
        key = self._move_key(cur, nxt)
        if key is None:
            return self._finish(f"unreachable next tile {nxt} from {cur}")

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
            return pf.frontier_path(cur, self._frontier_dir, now_tick=state.tick_counter) or []
        if goal is None:
            return []
        return pf.find_path(cur, goal, now_tick=state.tick_counter, partial=True) or []

    @staticmethod
    def _move_key(cur: "Pos", nxt: "Pos") -> str | None:
        dx, dy, dz = nxt[0] - cur[0], nxt[1] - cur[1], nxt[2] - cur[2]
        if dz != 0 and dx == 0 and dy == 0:
            return _CLIMB_UP_KEY if dz > 0 else _CLIMB_DOWN_KEY
        return _DELTA_TO_KEY.get((dx, dy))

    def _finish(self, outcome: str) -> SkillResult:
        dist = 0
        if self._start_abs and self.ctx.extractor.has_offset:
            pass  # distance computed from steps is more reliable than coords here
        return SkillResult.done(f"{outcome} ({self._steps} tiles moved)")


# ----------------------------------------------------------------------
# FastTravelController — deterministic world-map travel to a site
# ----------------------------------------------------------------------

class FastTravelController(Skill):
    """Enter travel mode, steer toward a site by army-position delta, stop at it.

    LIVE-VERIFY: army coords are 3x embark-tile coords; help dialog + 'x' exit
    button require mouse clicks (handled in opendwarf--act.lua).
    """

    name = "fast_travel"
    _STOP_DISTANCE = 2  # DF often can't get closer than ~2 tiles to a site center

    def __init__(self, ctx: SkillContext, *, site_id: int | None, site_name: str, max_steps: int = 60) -> None:
        super().__init__(ctx)
        self._site_id = site_id
        self._site_name = site_name
        self._max_steps = max_steps
        self._steps = 0
        self._phase = "enter"  # enter -> travel -> exit -> done
        self._origin_site = ""

    def step(self, state: "GameState") -> SkillResult:
        if state.hostile_units:
            # Forced out of travel into combat
            return SkillResult.interrupted("hostile encounter during travel")

        if self._phase == "enter":
            self._origin_site = state.site_name or ""
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
                return SkillResult.running()  # wait for travel mode to engage
            return SkillResult.done(f"left travel mode near {self._site_name}")

        target = self._find_target(state)
        if target is not None and target.distance is not None and target.distance <= self._STOP_DISTANCE:
            self._phase = "exit"
            return SkillResult.running()

        if self._steps >= self._max_steps:
            self._phase = "exit"
            return SkillResult.running()

        direction = target.direction.lower() if target else None
        delta = _NAME_TO_DELTA.get(direction or "", None)
        if delta is None:
            # No target / unknown direction — stop and hand back
            self._phase = "exit"
            return SkillResult.running()

        key = _DELTA_TO_KEY[delta]
        self.ctx.lua.execute_action(key)
        self._steps += 1
        return SkillResult.running()

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
        sx = 1 if dx > 0 else -1 if dx < 0 else 0
        sy = 1 if dy > 0 else -1 if dy < 0 else 0
        return (sx, sy)


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
