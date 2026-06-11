"""Map extraction orchestration: when to fetch, coordinate conversion, LLM view.

GameState positions are LOCAL map coordinates; the ChunkMap stores ABSOLUTE
world tiles. The map payload carries the adventurer's position in both systems,
which lets us maintain the local->absolute offset here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opendwarf.spatial.chunk_map import ChunkMap, Pos

if TYPE_CHECKING:
    from opendwarf.dfhack.lua_executor import LuaExecutor
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)


class MapExtractor:
    def __init__(
        self,
        lua: "LuaExecutor",
        chunk_map: ChunkMap,
        radius: int = 40,
        max_turns_stale: int = 30,
    ) -> None:
        self.lua = lua
        self.map = chunk_map
        self.radius = radius
        self.max_turns_stale = max_turns_stale
        self._offset: tuple[int, int] | None = None  # (abs - local) for x, y
        self._last_center: Pos | None = None  # absolute center of last fetch
        self._turns_since_fetch = 0

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------

    @property
    def has_offset(self) -> bool:
        return self._offset is not None

    def to_abs(self, x: int, y: int, z: int) -> Pos:
        """Convert LOCAL map coords to absolute world tiles (z is already absolute)."""
        if self._offset is None:
            raise RuntimeError("No map fetched yet — local->absolute offset unknown")
        return (x + self._offset[0], y + self._offset[1], z)

    def adventurer_abs(self, state: "GameState") -> Pos | None:
        pos = state.adventurer_position
        if pos is None or self._offset is None:
            return None
        return self.to_abs(pos.x, pos.y, pos.z)

    # ------------------------------------------------------------------
    # Fetch policy
    # ------------------------------------------------------------------

    def ensure_fresh(self, state: "GameState") -> bool:
        """Fetch a new map extraction if our knowledge is stale. Returns True if fetched."""
        if state.fast_travel_active or state.adventurer_position is None:
            return False

        self._turns_since_fetch += 1
        needs = False
        reason = ""
        if self._last_center is None or self._offset is None:
            needs, reason = True, "no offset/center yet"
        else:
            pos = state.adventurer_position
            ax, ay, az = self.to_abs(pos.x, pos.y, pos.z)
            cx, cy, cz = self._last_center
            if az != cz:
                needs, reason = True, f"z changed ({cz} -> {az})"
            elif max(abs(ax - cx), abs(ay - cy)) > self.radius // 2:
                needs, reason = True, f"moved {max(abs(ax - cx), abs(ay - cy))} tiles from center"
            elif self._turns_since_fetch >= self.max_turns_stale:
                needs, reason = True, f"stale ({self._turns_since_fetch} turns)"

        if not needs:
            return False
        # Rate limit: extraction costs ~10s. A wedged UI / inconsistent position
        # reading must not turn every tick into a fetch (observed live: refetch
        # loop at 11s/tick during an obstructed-travel wedge).
        if self._turns_since_fetch < 3 and self._last_center is not None:
            logger.debug("Map fetch wanted (%s) but rate-limited (%d turns since last)",
                         reason, self._turns_since_fetch)
            return False
        logger.debug("Map fetch: %s", reason)
        return self.fetch(state)

    def fetch(self, state: "GameState") -> bool:
        """Run the map Lua script and ingest the payload."""
        try:
            payload = self.lua.extract_map(self.radius)
        except Exception:
            logger.exception("Map extraction failed")
            return False
        if not payload or "error" in payload:
            logger.warning("Map extraction returned no data: %s", payload)
            return False

        adv_abs = payload.get("adventurer") or {}
        local = state.adventurer_position
        if local is not None and "x" in adv_abs:
            self._offset = (adv_abs["x"] - local.x, adv_abs["y"] - local.y)
            self._last_center = (adv_abs["x"], adv_abs["y"], adv_abs["z"])

        self.map.ingest(payload, tick=state.tick_counter)
        self._turns_since_fetch = 0
        logger.info(
            "Map ingested: center=%s, %d chunks known",
            self._last_center, self.map.explored_chunk_count,
        )
        self.map.save()
        return True

    # ------------------------------------------------------------------
    # LLM-facing view
    # ------------------------------------------------------------------

    def render_view(self, state: "GameState", radius: int = 10) -> list[str]:
        """ASCII view of current z centered on the adventurer, with unit overlays.

        Overlays: @ = adventurer, h = hostile unit, u = friendly unit.
        """
        center = self.adventurer_abs(state)
        if center is None:
            return []
        rows = self.map.render(center, radius)
        grid = [list(r) for r in rows]

        def put(abs_x: int, abs_y: int, ch: str) -> None:
            col = abs_x - (center[0] - radius)
            row = abs_y - (center[1] - radius)
            if 0 <= row < len(grid) and 0 <= col < len(grid[row]):
                grid[row][col] = ch

        for u in state.nearby_units:
            if u.position is None:
                continue
            ux, uy, uz = self.to_abs(u.position.x, u.position.y, u.position.z)
            if uz == center[2]:
                put(ux, uy, "h" if u.is_hostile else "u")
        put(center[0], center[1], "@")
        return ["".join(r) for r in grid]
