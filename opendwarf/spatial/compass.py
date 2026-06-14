"""Canonical 8-direction compass tables and helpers.

Single home for the delta ↔ direction-name ↔ movement-key mappings that were
previously duplicated across ``actions/skills.py``, ``actions/registry.py`` and
``behaviors/journey.py`` (the behaviors imported skills' private ``_``-names
across package boundaries). DF convention: ``+x`` = East, ``+y`` = South.

Dependency-free by design (no ``opendwarf`` imports) so any layer — state,
actions, behaviors — can use it without risking an import cycle.
"""

from __future__ import annotations

# Compass ring, clockwise from north. Used for rotational steering (e.g.
# JourneyBehavior rotates a base heading by N 45° steps indexing into this).
RING: tuple[str, ...] = ("n", "ne", "e", "se", "s", "sw", "w", "nw")

# Direction name -> unit (dx, dy) step.
NAME_TO_DELTA: dict[str, tuple[int, int]] = {
    "n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0),
    "ne": (1, -1), "nw": (-1, -1), "se": (1, 1), "sw": (-1, 1),
}

# Unit (dx, dy) step -> DF movement interface key.
DELTA_TO_KEY: dict[tuple[int, int], str] = {
    (0, -1): "A_MOVE_N", (0, 1): "A_MOVE_S", (1, 0): "A_MOVE_E", (-1, 0): "A_MOVE_W",
    (1, -1): "A_MOVE_NE", (-1, -1): "A_MOVE_NW", (1, 1): "A_MOVE_SE", (-1, 1): "A_MOVE_SW",
}


def sign(dx: int, dy: int) -> tuple[int, int]:
    """Reduce a delta to a unit step vector: each component mapped to -1/0/+1."""
    sx = 1 if dx > 0 else -1 if dx < 0 else 0
    sy = 1 if dy > 0 else -1 if dy < 0 else 0
    return sx, sy


def delta_to_key(dx: int, dy: int) -> str | None:
    """Movement key for a unit delta, or None if it isn't one of the 8 steps."""
    return DELTA_TO_KEY.get((dx, dy))


def dir8(dx: int, dy: int) -> str | None:
    """Uppercase 8-direction name for an *adjacent* delta (Chebyshev distance 1),
    or None for the same tile / a non-adjacent delta. Used to label the
    direction of an adjacent unit."""
    if max(abs(dx), abs(dy)) != 1:
        return None
    vert = "N" if dy < 0 else "S" if dy > 0 else ""
    horz = "E" if dx > 0 else "W" if dx < 0 else ""
    return (vert + horz) or None
