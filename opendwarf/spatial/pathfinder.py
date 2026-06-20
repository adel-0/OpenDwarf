"""A* pathfinding over the ChunkMap.

Design points (see ROADMAP spatial design):
- 8-connected movement per z-level.
- UNKNOWN tiles are traversable at high cost — the agent paths through
  unexplored space when no known route exists, rather than failing.
- Stale tiles are treated like UNKNOWN (passability is dynamic in DF).
- Vertical edges at stairs; RAMP edges are traversed optimistically —
  ramps are ordinary directional moves that happen to change z-level.
  Walking onto a ramp in a direction goes to the adjacent tile one level
  up (or stepping off an edge above a ramp descends). RouteExecutor
  validates each move via total_move and replans on failure, so
  optimistic ramp edges are self-correcting.
- Bounded node expansion; on failure returns the best partial path toward
  the goal so movement still makes progress.
"""

from __future__ import annotations

import heapq
import logging
import math

from opendwarf.spatial.chunk_map import (
    Cell,
    ChunkMap,
    Pos,
    WALKABLE_CELLS,
)

logger = logging.getLogger(__name__)

# Traversal cost per cell type (multiplied by step length)
_CELL_COST: dict[Cell, float] = {
    Cell.PASSABLE: 1.0,
    Cell.STAIR_UP: 1.0,
    Cell.STAIR_DOWN: 1.0,
    Cell.STAIR_UPDOWN: 1.0,
    Cell.RAMP: 1.0,
    Cell.DOOR: 1.5,    # may be closed; small penalty
    Cell.UNKNOWN: 5.0,  # explorable but uncertain
    Cell.WATER: 12.0,   # last resort
}

_NEIGHBORS_8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]

_MAX_EXPANSIONS = 20_000


class Pathfinder:
    def __init__(self, chunk_map: ChunkMap):
        self.map = chunk_map

    # ------------------------------------------------------------------
    # Cost model
    # ------------------------------------------------------------------

    def _enter_cost(self, x: int, y: int, z: int, now_tick: int) -> float | None:
        """Cost to step into a tile, or None if impassable."""
        cell = self.map.get(x, y, z)
        if cell in (Cell.WALL, Cell.EMPTY):
            return None
        if cell is not Cell.UNKNOWN and self.map.is_stale(x, y, z, now_tick):
            cell = Cell.UNKNOWN
        return _CELL_COST.get(cell)

    def _vertical_edges(self, x: int, y: int, z: int) -> list[int]:
        """Possible pure-vertical (same x,y) dz moves from this tile.

        Stairs: both directions if matching stair type is above/below.
        RAMP: only confirmed transitions (kept for legacy / confirmed routes).
        New directional ramp edges are generated in _neighbors, not here.
        """
        cell = self.map.get(x, y, z)
        edges: list[int] = []
        if cell in (Cell.STAIR_UP, Cell.STAIR_UPDOWN):
            above = self.map.get(x, y, z + 1)
            if above in (Cell.STAIR_DOWN, Cell.STAIR_UPDOWN, Cell.UNKNOWN):
                edges.append(1)
        if cell in (Cell.STAIR_DOWN, Cell.STAIR_UPDOWN):
            below = self.map.get(x, y, z - 1)
            if below in (Cell.STAIR_UP, Cell.STAIR_UPDOWN, Cell.UNKNOWN):
                edges.append(-1)
        if cell is Cell.RAMP:
            # Confirmed transitions: kept so proven routes win ties vs optimistic ones.
            for dz in (1, -1):
                if self.map.vertical_confirmed(x, y, z, dz):
                    edges.append(dz)
        return edges

    def _neighbors(self, current: Pos, now_tick: int) -> list[tuple[Pos, float]]:
        """All reachable neighbors from current, with step costs.

        Produces:
        - 8 same-z neighbors via _enter_cost.
        - Pure-vertical stair/confirmed-ramp edges via _vertical_edges.
        - Optimistic ramp-up edges: standing on a RAMP, move diagonally-ish
          to an adjacent tile one z-level up.
        - Optimistic ramp-down edges: stepping off a floor tile to a
          lower tile that has a RAMP below it (descending a hillside).
        """
        cx, cy, cz = current
        result: list[tuple[Pos, float]] = []

        # Same-z 8-connected
        for dx, dy in _NEIGHBORS_8:
            nx, ny = cx + dx, cy + dy
            cost = self._enter_cost(nx, ny, cz, now_tick)
            if cost is not None:
                step = cost * (1.4 if dx and dy else 1.0)
                result.append(((nx, ny, cz), step))

        # Pure-vertical edges (stairs + confirmed ramp)
        for dz in self._vertical_edges(cx, cy, cz):
            cost = self._enter_cost(cx, cy, cz + dz, now_tick)
            if cost is not None:
                result.append(((cx, cy, cz + dz), cost + 1.0))

        cell = self.map.get(cx, cy, cz)

        # Ramp-up: standing on a RAMP, each of 8 directions leads to z+1
        if cell is Cell.RAMP:
            for dx, dy in _NEIGHBORS_8:
                nx, ny, nz = cx + dx, cy + dy, cz + 1
                cost = self._enter_cost(nx, ny, nz, now_tick)
                if cost is not None:
                    step = cost * 1.4 + 0.5  # diagonal-ish + small vertical overhead
                    result.append(((nx, ny, nz), step))

        # Ramp-down: neighbor tile at same z is EMPTY/UNKNOWN above a RAMP at z-1
        # (stepping off a hillside edge onto a lower ramp)
        for dx, dy in _NEIGHBORS_8:
            nx, ny = cx + dx, cy + dy
            neighbor_below = self.map.get(nx, ny, cz - 1)
            if neighbor_below is Cell.RAMP:
                neighbor_here = self.map.get(nx, ny, cz)
                if neighbor_here in (Cell.EMPTY, Cell.UNKNOWN):
                    ramp_cost = _CELL_COST[Cell.RAMP] * 1.4 + 0.5
                    result.append(((nx, ny, cz - 1), ramp_cost))

        return result

    # ------------------------------------------------------------------
    # A*
    # ------------------------------------------------------------------

    @staticmethod
    def _heuristic(a: Pos, b: Pos) -> float:
        dx, dy, dz = abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2])
        return max(dx, dy) + 2.0 * dz  # Chebyshev + vertical weight

    def find_path(
        self,
        start: Pos,
        goal: Pos,
        now_tick: int = 0,
        partial: bool = True,
    ) -> list[Pos] | None:
        """A* from start to goal. Returns tile list excluding start, or None.

        With partial=True, an unreachable/over-budget goal yields the path to
        the expanded node closest to the goal (if it improves on start).
        """
        if start == goal:
            return []

        open_heap: list[tuple[float, int, Pos]] = [(self._heuristic(start, goal), 0, start)]
        g_score: dict[Pos, float] = {start: 0.0}
        came_from: dict[Pos, Pos] = {}
        best_node, best_h = start, self._heuristic(start, goal)
        counter = 0
        expansions = 0

        while open_heap and expansions < _MAX_EXPANSIONS:
            _, _, current = heapq.heappop(open_heap)
            if current == goal:
                return self._reconstruct(came_from, current)
            expansions += 1

            for npos, step_cost in self._neighbors(current, now_tick):
                tentative = g_score[current] + step_cost
                if tentative < g_score.get(npos, math.inf):
                    g_score[npos] = tentative
                    came_from[npos] = current
                    h = self._heuristic(npos, goal)
                    counter += 1
                    heapq.heappush(open_heap, (tentative + h, counter, npos))
                    if h < best_h:
                        best_h, best_node = h, npos

        if partial and best_node != start:
            logger.debug("Path to %s not found; partial path to %s (h=%.1f)", goal, best_node, best_h)
            return self._reconstruct(came_from, best_node)
        return None

    @staticmethod
    def _reconstruct(came_from: dict[Pos, Pos], node: Pos) -> list[Pos]:
        path = [node]
        while node in came_from:
            node = came_from[node]
            path.append(node)
        path.reverse()
        return path[1:]  # exclude start

    # ------------------------------------------------------------------
    # Frontier exploration
    # ------------------------------------------------------------------

    def frontier_path(
        self,
        start: Pos,
        direction: tuple[int, int],
        now_tick: int = 0,
        min_dist: int = 3,
    ) -> list[Pos] | None:
        """Path to the nearest known-walkable tile that borders UNKNOWN space
        and lies roughly in `direction` (unit-ish vector, e.g. (1,-1) for NE).

        Uses uniform-cost search over known walkable tiles only (UNKNOWN tiles
        are not expanded beyond 1 step, so the search finds the nearest *known*
        tile bordering unexplored space rather than wandering through unknowns).

        3D: uses _neighbors for expansion, so ramps and stairs are crossed
        naturally. Frontier acceptance check uses x,y only (ignore z for cone).

        Fallback: if no frontier tile matched, returns path to the expanded node
        with the best projection onto `direction` if that projection >= min_dist.
        """
        dlen = math.hypot(*direction) or 1.0
        ux, uy = direction[0] / dlen, direction[1] / dlen

        open_heap: list[tuple[float, int, Pos]] = [(0.0, 0, start)]
        g_score: dict[Pos, float] = {start: 0.0}
        came_from: dict[Pos, Pos] = {}
        counter = 0
        expansions = 0

        # Track best-direction node for fallback
        best_proj_node: Pos = start
        best_proj: float = 0.0

        while open_heap and expansions < _MAX_EXPANSIONS:
            cost, _, current = heapq.heappop(open_heap)
            expansions += 1
            cx, cy, cz = current

            # Frontier check: walkable tile adjacent to UNKNOWN on its own z,
            # in the direction cone (cone computed from x,y only, ignore z).
            dx, dy = cx - start[0], cy - start[1]
            dist = math.hypot(dx, dy)
            if dist >= min_dist:
                in_cone = (dx * ux + dy * uy) / (dist or 1.0) >= 0.5  # within ~60 deg
                if in_cone and any(
                    self.map.get(cx + ox, cy + oy, cz) is Cell.UNKNOWN
                    for ox, oy in _NEIGHBORS_8
                ):
                    return self._reconstruct(came_from, current)

            # Track best projection for fallback
            proj = dx * ux + dy * uy
            if proj > best_proj:
                best_proj = proj
                best_proj_node = current

            # Expand via full 3D neighbors (includes ramps); skip UNKNOWN tiles.
            for npos, step_cost in self._neighbors(current, now_tick):
                nx, ny, nz = npos
                ncell = self.map.get(nx, ny, nz)
                if ncell is Cell.UNKNOWN:
                    continue  # don't wander through unknown during expansion
                tentative = cost + step_cost
                if tentative < g_score.get(npos, math.inf):
                    g_score[npos] = tentative
                    came_from[npos] = current
                    counter += 1
                    heapq.heappush(open_heap, (tentative, counter, npos))

        # Fallback: best-progress node in the requested direction
        if best_proj >= min_dist and best_proj_node != start:
            logger.debug(
                "frontier_path: no frontier found; fallback to best-progress node %s (proj=%.1f)",
                best_proj_node, best_proj,
            )
            return self._reconstruct(came_from, best_proj_node)
        return None

    # ------------------------------------------------------------------
    # Structure seeking (find the inhabited core of a settlement)
    # ------------------------------------------------------------------

    def nearest_structure(
        self,
        start: Pos,
        now_tick: int = 0,
        min_dist: int = 2,
    ) -> Pos | None:
        """Nearest known DOOR tile reachable through known walkable space.

        Doors are the strongest "inhabited building" signal the map carries, so
        steering toward one drives the agent into a town's populated core instead
        of frontier-exploring the empty fields fast-travel drops it in. Uniform-
        cost search over KNOWN walkable tiles only (UNKNOWN is not expanded), so a
        returned tile is genuinely reachable now; returns None if no door is known
        within reach (caller should fall back to frontier exploration).
        """
        open_heap: list[tuple[float, int, Pos]] = [(0.0, 0, start)]
        g_score: dict[Pos, float] = {start: 0.0}
        counter = 0
        expansions = 0

        while open_heap and expansions < _MAX_EXPANSIONS:
            cost, _, current = heapq.heappop(open_heap)
            expansions += 1
            cx, cy, cz = current

            dist = abs(cx - start[0]) + abs(cy - start[1])
            if dist >= min_dist and self.map.get(cx, cy, cz) is Cell.DOOR:
                return current

            for npos, step_cost in self._neighbors(current, now_tick):
                nx, ny, nz = npos
                if self.map.get(nx, ny, nz) is Cell.UNKNOWN:
                    continue  # only traverse known space — guarantees reachability
                tentative = cost + step_cost
                if tentative < g_score.get(npos, math.inf):
                    g_score[npos] = tentative
                    counter += 1
                    heapq.heappush(open_heap, (tentative, counter, npos))
        return None

    # ------------------------------------------------------------------
    # Water seeking (reach a tile from which the agent can drink)
    # ------------------------------------------------------------------

    def nearest_water(
        self,
        start: Pos,
        now_tick: int = 0,
        min_dist: int = 0,
    ) -> Pos | None:
        """Nearest known walkable tile adjacent to a WATER tile, reachable now.

        A dehydrated agent can only `drink` when `water_adjacent` is true — i.e. a
        WATER cell sits in one of its 8 same-z neighbours (water itself is NOT
        walkable, see WALKABLE_CELLS). This routes toward the closest standing
        spot that satisfies that condition. Uniform-cost search over KNOWN
        walkable tiles only (UNKNOWN is not expanded), so the returned tile is
        genuinely reachable now; ramps/stairs cross z-levels via `_neighbors`, so
        water one z-level down is reached when a vertical connector is known.
        Returns None if no such tile is reachable (caller should fall back to
        frontier exploration to discover water).
        """
        open_heap: list[tuple[float, int, Pos]] = [(0.0, 0, start)]
        g_score: dict[Pos, float] = {start: 0.0}
        counter = 0
        expansions = 0

        while open_heap and expansions < _MAX_EXPANSIONS:
            cost, _, current = heapq.heappop(open_heap)
            expansions += 1
            cx, cy, cz = current

            dist = abs(cx - start[0]) + abs(cy - start[1])
            if dist >= min_dist and any(
                self.map.get(cx + ox, cy + oy, cz) is Cell.WATER
                for ox, oy in _NEIGHBORS_8
            ):
                return current

            for npos, step_cost in self._neighbors(current, now_tick):
                nx, ny, nz = npos
                if self.map.get(nx, ny, nz) is Cell.UNKNOWN:
                    continue  # only traverse known space — guarantees reachability
                tentative = cost + step_cost
                if tentative < g_score.get(npos, math.inf):
                    g_score[npos] = tentative
                    counter += 1
                    heapq.heappush(open_heap, (tentative, counter, npos))
        return None
