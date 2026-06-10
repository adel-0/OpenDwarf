"""A* pathfinding over the ChunkMap.

Design points (see ROADMAP spatial design):
- 8-connected movement per z-level.
- UNKNOWN tiles are traversable at high cost — the agent paths through
  unexplored space when no known route exists, rather than failing.
- Stale tiles are treated like UNKNOWN (passability is dynamic in DF).
- Vertical edges at stairs; RAMP edges only after an observed successful
  z-transition (ramps often have walls above — unreliable until proven).
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
        """Possible dz moves from this tile based on its cell type."""
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
            # Ramps are unreliable — only use confirmed transitions
            for dz in (1, -1):
                if self.map.vertical_confirmed(x, y, z, dz):
                    edges.append(dz)
        return edges

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

            cx, cy, cz = current
            neighbors: list[tuple[Pos, float]] = []
            for dx, dy in _NEIGHBORS_8:
                nx, ny = cx + dx, cy + dy
                cost = self._enter_cost(nx, ny, cz, now_tick)
                if cost is not None:
                    step = cost * (1.4 if dx and dy else 1.0)
                    neighbors.append(((nx, ny, cz), step))
            for dz in self._vertical_edges(cx, cy, cz):
                cost = self._enter_cost(cx, cy, cz + dz, now_tick)
                if cost is not None:
                    neighbors.append(((cx, cy, cz + dz), cost + 1.0))

            for npos, step_cost in neighbors:
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

        Uses uniform-cost search over known walkable tiles only.
        """
        dlen = math.hypot(*direction) or 1.0
        ux, uy = direction[0] / dlen, direction[1] / dlen

        open_heap: list[tuple[float, int, Pos]] = [(0.0, 0, start)]
        g_score: dict[Pos, float] = {start: 0.0}
        came_from: dict[Pos, Pos] = {}
        counter = 0
        expansions = 0

        while open_heap and expansions < _MAX_EXPANSIONS:
            cost, _, current = heapq.heappop(open_heap)
            expansions += 1
            cx, cy, cz = current

            # Frontier check: walkable tile adjacent to UNKNOWN, in the cone
            dx, dy = cx - start[0], cy - start[1]
            dist = math.hypot(dx, dy)
            if dist >= min_dist:
                in_cone = (dx * ux + dy * uy) / (dist or 1.0) >= 0.5  # within ~60 deg
                if in_cone and any(
                    self.map.get(cx + ox, cy + oy, cz) is Cell.UNKNOWN
                    for ox, oy in _NEIGHBORS_8
                ):
                    return self._reconstruct(came_from, current)

            for ox, oy in _NEIGHBORS_8:
                nx, ny = cx + ox, cy + oy
                cell = self.map.get(nx, ny, cz)
                if cell not in WALKABLE_CELLS:
                    continue
                step = (1.4 if ox and oy else 1.0) * _CELL_COST.get(cell, 1.0)
                npos = (nx, ny, cz)
                tentative = cost + step
                if tentative < g_score.get(npos, math.inf):
                    g_score[npos] = tentative
                    came_from[npos] = current
                    counter += 1
                    heapq.heappush(open_heap, (tentative, counter, npos))

        return None
