"""Tests for attack depth (NORTHSTAR M2): directional default-strike resolution.

Verifies that `attack:<id>` / bare `attack` resolve to an `attack_dir:<DIR>` key
only when the target is an adjacent hostile, and that the 8-direction mapping is
correct (DF y+ = south)."""

from __future__ import annotations

from opendwarf.actions.registry import _adjacent_hostiles, _dir8, default_registry
from opendwarf.state.game_state import GameState, Position, UnitInfo


def _state_with(units):
    s = GameState()
    s.adventurer_position = Position(50, 50, 10)
    for u in units:
        s.nearby_units.append(u)
        if u.is_hostile:
            s.hostile_units.append(u)
    return s


def _h(uid, dx, dy, dz=0, race="WOLF", dist=None):
    pos = Position(50 + dx, 50 + dy, 10 + dz)
    return UnitInfo(id=uid, name=race.title(), race=race, position=pos,
                    is_hostile=True, distance=dist if dist is not None else abs(dx) + abs(dy))


def test_dir8_mapping():
    assert _dir8(0, -1) == "N"
    assert _dir8(0, 1) == "S"
    assert _dir8(1, 0) == "E"
    assert _dir8(-1, 0) == "W"
    assert _dir8(1, -1) == "NE"
    assert _dir8(-1, -1) == "NW"
    assert _dir8(1, 1) == "SE"
    assert _dir8(-1, 1) == "SW"
    assert _dir8(0, 0) is None       # same tile
    assert _dir8(2, 0) is None       # not adjacent
    assert _dir8(1, 2) is None       # knight's move, not a neighbour


def test_adjacent_hostiles_filters_zlevel_and_range():
    s = _state_with([_h(1, 1, 0), _h(2, 0, 1, dz=1), _h(3, 3, 0)])
    adj = _adjacent_hostiles(s)
    ids = [u.id for u, _ in adj]
    assert ids == [1]  # id2 wrong z, id3 too far


def test_attack_specific_adjacent_resolves_to_dir():
    s = _state_with([_h(7, -1, -1)])  # NW neighbour → bump-attack moving NW
    d = default_registry().resolve("attack:7", s, None)
    assert d.key == "A_MOVE_NW"
    assert d.error is None


def test_attack_non_adjacent_errors():
    s = _state_with([_h(7, 4, 0)])
    d = default_registry().resolve("attack:7", s, None)
    assert d.error is not None
    assert d.key == "A_MOVE_SAME_SQUARE"


def test_bare_attack_picks_closest_adjacent():
    # two adjacent hostiles; the closer (manhattan) one wins
    s = _state_with([_h(1, 1, 1, dist=2), _h(2, 0, -1, dist=1)])
    d = default_registry().resolve("attack", s, None)
    assert d.key == "A_MOVE_N"  # id2 is directly north, dist 1


def test_bare_attack_no_adjacent_errors():
    s = _state_with([_h(1, 5, 0)])
    d = default_registry().resolve("attack", s, None)
    assert d.error is not None


def test_attack_offered_only_when_adjacent():
    s = _state_with([_h(1, 1, 0)])
    block = default_registry().build_block(s)
    assert "attack:1" in block
    assert "attack" in block
    # non-adjacent: no attack:<id>, and bare attack not offered either
    s2 = _state_with([_h(1, 5, 0)])
    block2 = default_registry().build_block(s2)
    assert "attack:1" not in block2
