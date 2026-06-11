"""Tests for 3D ramp traversal in the pathfinder (WP1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from opendwarf.spatial.chunk_map import Cell, ChunkMap
from opendwarf.spatial.pathfinder import Pathfinder


def _build_hill_map() -> ChunkMap:
    """Synthetic 2-level hill map.

    z=10: a 5x5 floor pocket enclosed by WALL, with one RAMP on its east edge.
          Layout (x=0..4, y=0..4):
            #####
            #...^
            #...^
            #...^
            #####
          RAMP is at (4, 1..3, 10). The tiles directly above the ramp (4,1..3,11)
          are EMPTY (open air) — as in real DF hillside geometry.

    z=11: floor continuing east from the ramp. (5..9, 1..3) are PASSABLE;
          nothing ingested beyond x=9 (UNKNOWN). The tile above the ramp position
          (4,y,11) is EMPTY — not ingested, so it stays UNKNOWN (treated as open
          air / EMPTY for the ramp-down check).

    Ramp-up path: from pocket (e.g. (2,2,10)), travel east to (4,2,10)[RAMP],
    ramp-up edge → (5,2,11)[PASSABLE], continue east.

    Ramp-down path: from (5,2,11), neighbor (4,2,11) is UNKNOWN (open air), and
    (4,2,10) is RAMP → ramp-down edge fires, reaching z=10.
    """
    cm = ChunkMap()
    # z=10 pocket
    z10_rows = [
        "#####",  # y=0
        "#...^",  # y=1
        "#...^",  # y=2
        "#...^",  # y=3
        "#####",  # y=4
    ]
    cm.ingest({"origin": {"x": 0, "y": 0}, "z_levels": {"10": z10_rows}}, tick=1)
    # z=11 floor east of the ramp. Origin (5,1): covers x=5..9, y=1..3 — PASSABLE.
    # (4,y,11) is NOT ingested → UNKNOWN, acting as open air above the z=10 ramp.
    z11_rows = [
        ".....",  # y=1: x=5..9
        ".....",  # y=2
        ".....",  # y=3
    ]
    cm.ingest({"origin": {"x": 5, "y": 1}, "z_levels": {"11": z11_rows}}, tick=1)
    return cm


def test_find_path_up_ramp():
    """find_path crosses the ramp from pocket center to z=11."""
    cm = _build_hill_map()
    pf = Pathfinder(cm)
    # Start in pocket center, goal on z=11 floor
    path = pf.find_path((2, 2, 10), (7, 2, 11), partial=False)
    assert path is not None, "Should find path from pocket to z=11 via ramp"
    assert path[-1] == (7, 2, 11)
    # Path must traverse z=10 → z=11 somewhere
    zlevels = {pos[2] for pos in path}
    assert 11 in zlevels, "Path must reach z=11"
    assert 10 in zlevels, "Path must start on z=10"


def test_frontier_path_up_ramp():
    """frontier_path eastward returns a path ending on z=11 adjacent to UNKNOWN."""
    cm = _build_hill_map()
    pf = Pathfinder(cm)
    path = pf.frontier_path((2, 2, 10), (1, 0), min_dist=3)
    assert path is not None, "frontier_path east should find a frontier via ramp"
    end = path[-1]
    ex, ey, ez = end
    # Must be on z=11 (crossed the ramp)
    assert ez == 11, f"Frontier endpoint should be on z=11, got z={ez}"
    # Must be adjacent to UNKNOWN on z=11
    neighbors_unknown = any(
        cm.get(ex + ox, ey + oy, ez) is Cell.UNKNOWN
        for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))
    )
    assert neighbors_unknown, f"Frontier tile {end} should be adjacent to UNKNOWN on z=11"


def test_find_path_down_ramp():
    """Ramp-down: path from z=11 back to z=10 pocket works."""
    cm = _build_hill_map()
    pf = Pathfinder(cm)
    path = pf.find_path((7, 2, 11), (2, 2, 10), partial=False)
    assert path is not None, "Should find reverse path from z=11 to z=10 via ramp"
    assert path[-1] == (2, 2, 10)
    zlevels = {pos[2] for pos in path}
    assert 10 in zlevels
    assert 11 in zlevels


# -----------------------------------------------------------------------
# Regression: real chunks.json from spatial/
# -----------------------------------------------------------------------

_CHUNKS_PATH = Path(__file__).parent.parent / "spatial" / "chunks.json"

_CARDINALS = [(1, 0), (-1, 0), (0, 1), (0, -1)]  # E W S N


@pytest.mark.skipif(not _CHUNKS_PATH.exists(), reason="spatial/chunks.json not present")
def test_frontier_from_real_map():
    """frontier_path from the real start position returns >=2 non-None cardinal paths."""
    cm = ChunkMap.load(_CHUNKS_PATH)
    pf = Pathfinder(cm)
    start: tuple[int, int, int] = (1458, 25834, 128)
    successes = 0
    for direction in _CARDINALS:
        path = pf.frontier_path(start, direction, min_dist=3)
        if path is not None:
            successes += 1
    assert successes >= 2, (
        f"Expected >=2 cardinal frontier paths from {start}, got {successes}. "
        "The start position may be walled in on all sides — check the real map."
    )
