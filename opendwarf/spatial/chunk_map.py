"""Persistent tile-level spatial memory (roadmap spatial-memory Layer 1).

Stores explored tiles in 16x16 chunks keyed on (chunk_x, chunk_y, z), all in
ABSOLUTE world-tile coordinates (local map coords + region offset * 16).
Only visited chunks exist. Each tile carries a last-verified tick so dynamic
passability (frozen rivers, locked doors) can be treated as stale knowledge.
"""

from __future__ import annotations

import json
import logging
from enum import IntEnum
from pathlib import Path

logger = logging.getLogger(__name__)

CHUNK_SIZE = 16

# How long (in game ticks) a tile observation stays trusted. Stale tiles are
# still used for pathfinding but at UNKNOWN cost.
STALE_TICKS = 50_000


class Cell(IntEnum):
    UNKNOWN = 0
    PASSABLE = 1
    WALL = 2
    WATER = 3
    DOOR = 4
    STAIR_UP = 5
    STAIR_DOWN = 6
    STAIR_UPDOWN = 7
    RAMP = 8
    EMPTY = 9  # open air / nothing (not walkable, not a wall)


# Char encoding used by opendwarf--map.lua rows
CHAR_TO_CELL: dict[str, Cell] = {
    ".": Cell.PASSABLE,
    "#": Cell.WALL,
    "~": Cell.WATER,
    "+": Cell.DOOR,
    "<": Cell.STAIR_UP,
    ">": Cell.STAIR_DOWN,
    "X": Cell.STAIR_UPDOWN,
    "^": Cell.RAMP,
    " ": Cell.EMPTY,
    "?": Cell.UNKNOWN,
}
CELL_TO_CHAR: dict[Cell, str] = {v: k for k, v in CHAR_TO_CELL.items()}

# Cells an adventurer can stand on / move through
WALKABLE_CELLS = frozenset(
    {Cell.PASSABLE, Cell.DOOR, Cell.STAIR_UP, Cell.STAIR_DOWN, Cell.STAIR_UPDOWN, Cell.RAMP}
)
# Cells that connect z-levels
VERTICAL_CELLS = frozenset({Cell.STAIR_UP, Cell.STAIR_DOWN, Cell.STAIR_UPDOWN, Cell.RAMP})

Pos = tuple[int, int, int]  # (x, y, z) absolute tiles


class _Chunk:
    __slots__ = ("cells", "ticks")

    def __init__(self) -> None:
        self.cells = bytearray(CHUNK_SIZE * CHUNK_SIZE)  # Cell values
        self.ticks = [0] * (CHUNK_SIZE * CHUNK_SIZE)  # last_verified_tick


class ChunkMap:
    """Sparse persistent grid of explored tiles in absolute coordinates."""

    def __init__(self, path: Path | None = None) -> None:
        self._chunks: dict[tuple[int, int, int], _Chunk] = {}
        # Vertical edges confirmed by observed z-transitions (for RAMPs, which
        # are unreliable until proven — roadmap trap #6). Maps pos -> set of dz.
        self._confirmed_vertical: dict[Pos, set[int]] = {}
        self.path = path

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    @staticmethod
    def _split(x: int, y: int, z: int) -> tuple[tuple[int, int, int], int]:
        cx, lx = divmod(x, CHUNK_SIZE)
        cy, ly = divmod(y, CHUNK_SIZE)
        return (cx, cy, z), ly * CHUNK_SIZE + lx

    def get(self, x: int, y: int, z: int) -> Cell:
        key, idx = self._split(x, y, z)
        chunk = self._chunks.get(key)
        if chunk is None:
            return Cell.UNKNOWN
        return Cell(chunk.cells[idx])

    def tick_of(self, x: int, y: int, z: int) -> int:
        key, idx = self._split(x, y, z)
        chunk = self._chunks.get(key)
        return chunk.ticks[idx] if chunk else 0

    def set(self, x: int, y: int, z: int, cell: Cell, tick: int = 0) -> None:
        key, idx = self._split(x, y, z)
        chunk = self._chunks.get(key)
        if chunk is None:
            chunk = self._chunks[key] = _Chunk()
        chunk.cells[idx] = int(cell)
        chunk.ticks[idx] = tick

    def is_stale(self, x: int, y: int, z: int, now_tick: int) -> bool:
        return now_tick - self.tick_of(x, y, z) > STALE_TICKS

    @property
    def explored_chunk_count(self) -> int:
        return len(self._chunks)

    # ------------------------------------------------------------------
    # Ingestion & dynamic updates
    # ------------------------------------------------------------------

    def ingest(self, payload: dict, tick: int = 0) -> None:
        """Merge a map extraction payload from opendwarf--map.lua.

        Payload format: {"origin": {"x","y","z"}, "z_levels": {"<z>": [row, ...]}}
        Origin is the ABSOLUTE coordinate of the top-left tile of each row grid.
        """
        origin = payload.get("origin") or {}
        ox, oy = origin.get("x", 0), origin.get("y", 0)
        for z_str, rows in (payload.get("z_levels") or {}).items():
            z = int(z_str)
            for dy, row in enumerate(rows):
                for dx, ch in enumerate(row):
                    cell = CHAR_TO_CELL.get(ch, Cell.UNKNOWN)
                    if cell is Cell.UNKNOWN:
                        continue  # never overwrite knowledge with ignorance
                    self.set(ox + dx, oy + dy, z, cell, tick)

    def downgrade(self, x: int, y: int, z: int) -> None:
        """A move into this tile failed — our knowledge of it is wrong."""
        logger.debug("Downgrading tile (%d,%d,%d) to UNKNOWN", x, y, z)
        self.set(x, y, z, Cell.UNKNOWN, 0)

    def confirm_vertical(self, x: int, y: int, z: int, dz: int) -> None:
        """Record an observed successful z-transition at this tile."""
        self._confirmed_vertical.setdefault((x, y, z), set()).add(dz)

    def vertical_confirmed(self, x: int, y: int, z: int, dz: int) -> bool:
        return dz in self._confirmed_vertical.get((x, y, z), set())

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path | None = None) -> None:
        path = path or self.path
        if path is None:
            return
        data = {
            "chunks": {
                f"{k[0]},{k[1]},{k[2]}": {
                    "cells": bytes(c.cells).hex(),
                    "ticks": c.ticks,
                }
                for k, c in self._chunks.items()
            },
            "vertical": {
                f"{p[0]},{p[1]},{p[2]}": sorted(dzs)
                for p, dzs in self._confirmed_vertical.items()
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        logger.debug("Saved chunk map (%d chunks) to %s", len(self._chunks), path)

    @classmethod
    def load(cls, path: Path) -> "ChunkMap":
        cm = cls(path)
        if not path.exists():
            return cm
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for key_str, cdata in data.get("chunks", {}).items():
                cx, cy, z = (int(v) for v in key_str.split(","))
                chunk = _Chunk()
                chunk.cells = bytearray(bytes.fromhex(cdata["cells"]))
                chunk.ticks = list(cdata["ticks"])
                cm._chunks[(cx, cy, z)] = chunk
            for pos_str, dzs in data.get("vertical", {}).items():
                x, y, z = (int(v) for v in pos_str.split(","))
                cm._confirmed_vertical[(x, y, z)] = set(dzs)
            logger.info("Loaded chunk map: %d chunks from %s", len(cm._chunks), path)
        except Exception:
            logger.exception("Failed to load chunk map %s; starting fresh", path)
            cm._chunks.clear()
            cm._confirmed_vertical.clear()
        return cm

    # ------------------------------------------------------------------
    # Rendering for the LLM
    # ------------------------------------------------------------------

    def render(self, center: Pos, radius: int = 10) -> list[str]:
        """ASCII view of the current z-level centered on `center` (no overlays)."""
        cx, cy, cz = center
        rows: list[str] = []
        for y in range(cy - radius, cy + radius + 1):
            row = []
            for x in range(cx - radius, cx + radius + 1):
                row.append(CELL_TO_CHAR.get(self.get(x, y, cz), "?"))
            rows.append("".join(row))
        return rows
