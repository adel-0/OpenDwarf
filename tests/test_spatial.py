"""Tests for ChunkMap and Pathfinder."""

from pathlib import Path

from opendwarf.spatial.chunk_map import Cell, ChunkMap
from opendwarf.spatial.pathfinder import Pathfinder


def grid_map(rows: list[str], z: int = 0, origin: tuple[int, int] = (0, 0)) -> ChunkMap:
    """Build a ChunkMap from ASCII rows (same encoding as the Lua payload)."""
    cm = ChunkMap()
    cm.ingest({"origin": {"x": origin[0], "y": origin[1]}, "z_levels": {str(z): rows}}, tick=1)
    return cm


# ----------------------------------------------------------------------
# ChunkMap
# ----------------------------------------------------------------------

def test_ingest_and_get():
    cm = grid_map([
        "..#",
        ".~+",
        "<>X",
    ])
    assert cm.get(0, 0, 0) is Cell.PASSABLE
    assert cm.get(2, 0, 0) is Cell.WALL
    assert cm.get(1, 1, 0) is Cell.WATER
    assert cm.get(2, 1, 0) is Cell.DOOR
    assert cm.get(0, 2, 0) is Cell.STAIR_UP
    assert cm.get(1, 2, 0) is Cell.STAIR_DOWN
    assert cm.get(2, 2, 0) is Cell.STAIR_UPDOWN
    assert cm.get(5, 5, 0) is Cell.UNKNOWN  # never observed


def test_ingest_never_overwrites_with_unknown():
    cm = grid_map(["."])
    cm.ingest({"origin": {"x": 0, "y": 0}, "z_levels": {"0": ["?"]}}, tick=2)
    assert cm.get(0, 0, 0) is Cell.PASSABLE  # '?' did not erase knowledge


def test_negative_coords_and_chunk_boundaries():
    cm = ChunkMap()
    cm.set(-1, -1, 0, Cell.WALL, tick=1)
    cm.set(15, 15, 0, Cell.PASSABLE, tick=1)
    cm.set(16, 16, 0, Cell.DOOR, tick=1)
    assert cm.get(-1, -1, 0) is Cell.WALL
    assert cm.get(15, 15, 0) is Cell.PASSABLE
    assert cm.get(16, 16, 0) is Cell.DOOR


def test_downgrade():
    cm = grid_map(["."])
    cm.downgrade(0, 0, 0)
    assert cm.get(0, 0, 0) is Cell.UNKNOWN


def test_persistence_roundtrip(tmp_path: Path):
    cm = grid_map(["..#", ".~+"])
    cm.confirm_vertical(1, 0, 0, 1)
    path = tmp_path / "chunks.json"
    cm.save(path)

    loaded = ChunkMap.load(path)
    assert loaded.get(2, 0, 0) is Cell.WALL
    assert loaded.get(1, 1, 0) is Cell.WATER
    assert loaded.tick_of(0, 0, 0) == 1
    assert loaded.vertical_confirmed(1, 0, 0, 1)
    assert not loaded.vertical_confirmed(1, 0, 0, -1)


def test_staleness():
    cm = ChunkMap()
    cm.set(0, 0, 0, Cell.PASSABLE, tick=100)
    assert not cm.is_stale(0, 0, 0, now_tick=200)
    assert cm.is_stale(0, 0, 0, now_tick=100_000)


def test_render():
    cm = grid_map(["...", ".#.", "..."])
    rows = cm.render((1, 1, 0), radius=1)
    assert rows == ["...", ".#.", "..."]


# ----------------------------------------------------------------------
# Pathfinder
# ----------------------------------------------------------------------

def test_straight_path():
    cm = grid_map(["....."])
    pf = Pathfinder(cm)
    path = pf.find_path((0, 0, 0), (4, 0, 0))
    assert path == [(1, 0, 0), (2, 0, 0), (3, 0, 0), (4, 0, 0)]


def test_path_around_wall():
    cm = grid_map([
        ".....",
        ".###.",
        ".#...",
        ".#.#.",
        ".....",
    ])
    pf = Pathfinder(cm)
    path = pf.find_path((2, 2, 0), (0, 0, 0))
    assert path is not None
    assert path[-1] == (0, 0, 0)
    # Path never enters a wall
    for x, y, z in path:
        assert cm.get(x, y, z) is not Cell.WALL


def test_door_is_passable():
    cm = grid_map([
        "#####",
        ".#.#.",
        "#+#+#",
        ".....",
    ])
    pf = Pathfinder(cm)
    path = pf.find_path((0, 1, 0), (4, 1, 0))
    assert path is not None
    assert path[-1] == (4, 1, 0)
    assert any(cm.get(x, y, z) is Cell.DOOR for x, y, z in path)


def test_prefers_known_over_unknown():
    # Two routes to the goal: a longer known corridor vs straight through unknown
    cm = grid_map([
        "...",
        "???",
        "...",
    ])
    pf = Pathfinder(cm)
    path = pf.find_path((0, 0, 0), (2, 2, 0))
    assert path is not None
    # Diagonal-through-unknown costs 5*1.4=7; going around via known floor is cheaper
    unknown_steps = sum(1 for p in path if cm.get(*p) is Cell.UNKNOWN)
    assert unknown_steps <= 1


def test_paths_through_unknown_when_no_known_route():
    # Known floor at the corners, walls blocking the direct line, unknown gaps
    # the only way across — the path should route through '?' tiles.
    cm = grid_map([
        ".??",
        "?##",
        "??.",
    ])
    path = Pathfinder(cm).find_path((0, 0, 0), (2, 2, 0))
    assert path is not None
    assert path[-1] == (2, 2, 0)
    assert any(cm.get(*p) is Cell.UNKNOWN for p in path)


def test_stair_vertical_edge():
    cm = ChunkMap()
    cm.ingest({"origin": {"x": 0, "y": 0}, "z_levels": {
        "0": ["..<"],
        "1": ["?.>"],
    }}, tick=1)
    pf = Pathfinder(cm)
    path = pf.find_path((0, 0, 0), (1, 0, 1))
    assert path is not None
    assert (2, 0, 1) in path  # went up via the stair pair
    assert path[-1] == (1, 0, 1)


def test_ramp_traversal_optimistic():
    """Ramps are now traversable without confirmation — optimistic directional edges."""
    cm = ChunkMap()
    cm.ingest({"origin": {"x": 0, "y": 0}, "z_levels": {
        "0": ["..^"],
        "1": ["..."],
    }}, tick=1)
    pf = Pathfinder(cm)
    # Can reach z=1 via the ramp at (2,0,0) without prior confirmation.
    path = pf.find_path((0, 0, 0), (1, 0, 1), partial=False)
    assert path is not None
    assert path[-1] == (1, 0, 1)

    # Confirmed transition: same route still works.
    cm.confirm_vertical(2, 0, 0, 1)
    path2 = pf.find_path((0, 0, 0), (1, 0, 1), partial=False)
    assert path2 is not None
    assert path2[-1] == (1, 0, 1)


def test_partial_path_toward_unreachable_goal():
    cm = grid_map([
        "...#?",
        "...#?",
        "...#?",
    ])
    pf = Pathfinder(cm)
    # Goal is behind a full wall with unknown beyond — partial should at least
    # move toward the wall (unknown tiles beyond the wall column are unreachable)
    path = pf.find_path((0, 1, 0), (10, 1, 0), partial=True)
    assert path is not None
    assert path[-1][0] > 0  # made eastward progress


def test_stale_tiles_cost_like_unknown():
    cm = ChunkMap()
    for x in range(5):
        cm.set(x, 0, 0, Cell.PASSABLE, tick=1)
    pf = Pathfinder(cm)
    path = pf.find_path((0, 0, 0), (4, 0, 0), now_tick=1_000_000)
    assert path is not None  # stale but still traversable
    assert path[-1] == (4, 0, 0)


def test_frontier_path():
    cm = grid_map([
        ".....",
        ".....",
        ".....",
    ])
    # East edge borders unknown space (nothing ingested beyond x=4)
    pf = Pathfinder(cm)
    path = pf.frontier_path((0, 1, 0), (1, 0))
    assert path is not None
    end = path[-1]
    assert end[0] >= 3  # reached the eastern frontier


def test_frontier_path_respects_direction():
    cm = grid_map([
        ".........",
        ".........",
        ".........",
    ], origin=(0, 0))
    pf = Pathfinder(cm)
    path = pf.frontier_path((4, 1, 0), (-1, 0))
    assert path is not None
    assert path[-1][0] <= 1  # went west, not east


def test_nearest_structure_finds_reachable_door():
    # A door sits at the east end of a corridor of known floor.
    cm = grid_map([
        "......+",
        "#######",
    ])
    pf = Pathfinder(cm)
    target = pf.nearest_structure((0, 0, 0))
    assert target == (6, 0, 0)
    assert cm.get(*target) is Cell.DOOR


def test_nearest_structure_skips_unreachable_door():
    # The only door is walled off (no known walkable path) — must return None,
    # so the caller falls back to exploration instead of routing into a wall.
    cm = grid_map([
        "..#+",
        "..##",
    ])
    pf = Pathfinder(cm)
    assert pf.nearest_structure((0, 0, 0)) is None


def test_nearest_structure_ignores_door_underfoot():
    # Standing on a door must not return that same tile (min_dist guard).
    cm = grid_map([
        "+....+",
    ])
    pf = Pathfinder(cm)
    target = pf.nearest_structure((0, 0, 0))
    assert target == (5, 0, 0)


# ----------------------------------------------------------------------
# MapExtractor offset freshness (regression: stale-offset coordinate jump)
# ----------------------------------------------------------------------

def test_extractor_recomputes_offset_after_window_shift():
    """The local->absolute offset must track the live map window, not the last
    fetch. DF re-centers the loaded window as the adventurer travels, remapping
    region_x/local_x while the absolute world tile stays fixed. A stale offset
    produced a region-sized (×16) jump in adventurer_abs, which desynced the
    cached route path from the true position ("unreachable next tile")."""
    from types import SimpleNamespace

    from opendwarf.spatial.chunk_map import ChunkMap
    from opendwarf.spatial.extractor import MapExtractor

    ext = MapExtractor(lua=None, chunk_map=ChunkMap())  # type: ignore[arg-type]

    # Frame A: region offset 1000, local (50, 60) -> abs (1050, 1060).
    state_a = SimpleNamespace(
        adventurer_position=SimpleNamespace(x=50, y=60, z=138),
        adventurer_abs_position=SimpleNamespace(x=1050, y=1060, z=138),
    )
    assert ext.adventurer_abs(state_a) == (1050, 1060, 138)

    # Frame B: the window shifted by 2 regions (+32 abs). Same WORLD tile is now
    # local (18, 28) but abs is unchanged at (1050, 1060). A cached offset from
    # frame A would yield (1018, 1028) — a 32-tile phantom jump. With per-turn
    # offset refresh the absolute position stays correct.
    state_b = SimpleNamespace(
        adventurer_position=SimpleNamespace(x=18, y=28, z=138),
        adventurer_abs_position=SimpleNamespace(x=1050, y=1060, z=138),
    )
    assert ext.adventurer_abs(state_b) == (1050, 1060, 138)
    # And to_abs() for a co-located unit uses the refreshed offset.
    assert ext.to_abs(18, 28, 138) == (1050, 1060, 138)
