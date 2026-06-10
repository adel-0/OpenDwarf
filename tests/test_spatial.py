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


def test_ramp_requires_confirmation():
    cm = ChunkMap()
    cm.ingest({"origin": {"x": 0, "y": 0}, "z_levels": {
        "0": ["..^"],
        "1": ["..."],
    }}, tick=1)
    pf = Pathfinder(cm)
    path = pf.find_path((0, 0, 0), (0, 0, 1), partial=False)
    assert path is None  # unconfirmed ramp: no vertical edge

    cm.confirm_vertical(2, 0, 0, 1)
    path2 = pf.find_path((0, 0, 0), (0, 0, 1), partial=False)
    assert path2 is not None
    assert path2[-1] == (0, 0, 1)


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
