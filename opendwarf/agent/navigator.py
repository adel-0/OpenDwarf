"""Autopilot navigator — executes multi-step movement without LLM calls."""

from __future__ import annotations

import enum
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opendwarf.dfhack.lua_executor import LuaExecutor
    from opendwarf.state.game_state import GameState, UnitInfo

logger = logging.getLogger(__name__)

# Direction vectors: (dx, dy) where +x=East, +y=South
DIRECTION_DELTAS: dict[str, tuple[int, int]] = {
    "n": (0, -1),
    "s": (0, 1),
    "e": (1, 0),
    "w": (-1, 0),
    "ne": (1, -1),
    "nw": (-1, -1),
    "se": (1, 1),
    "sw": (-1, 1),
}

# Ordered list for rotation (clockwise)
_DIRECTIONS_CW = ["n", "ne", "e", "se", "s", "sw", "w", "nw"]

# DFHack input keys for each direction
_MOVE_KEYS: dict[str, str] = {
    "n": "A_MOVE_N",
    "s": "A_MOVE_S",
    "e": "A_MOVE_E",
    "w": "A_MOVE_W",
    "ne": "A_MOVE_NE",
    "nw": "A_MOVE_NW",
    "se": "A_MOVE_SE",
    "sw": "A_MOVE_SW",
}

_WALKABLE_CHARS = set(".@<>X")


class NavigatorResult(enum.Enum):
    MOVED = "moved"
    DONE = "done"
    INTERRUPTED = "interrupted"


class Navigator:
    """Executes multi-step movement autonomously, returning control to the LLM on events."""

    def __init__(self, lua: LuaExecutor):
        self.lua = lua
        self._active: bool = False
        self._direction: str | None = None  # compass direction name
        self._target_unit_id: int | None = None
        self._visited: dict[tuple[int, int, int], int] = {}  # pos -> visit count
        self._steps_taken: int = 0
        self._max_steps: int = 15
        self._follow_hand: int = 1  # 1 = clockwise (right-hand), -1 = counter-clockwise (left-hand)
        self._reason: str = ""  # human-readable reason for last deactivation

    @property
    def active(self) -> bool:
        return self._active

    @property
    def deactivation_reason(self) -> str:
        return self._reason

    def activate_direction(self, direction: str, map_tiles: list[str]) -> None:
        """Start navigating in a compass direction."""
        self._active = True
        self._direction = direction
        self._target_unit_id = None
        self._visited.clear()
        self._steps_taken = 0
        self._max_steps = 15
        self._follow_hand = self._choose_hand(direction, map_tiles)
        self._reason = ""
        logger.info("Navigator activated: direction=%s, hand=%s",
                     direction, "right" if self._follow_hand == 1 else "left")

    def activate_approach(self, unit_id: int, initial_distance: int = 15) -> None:
        """Start navigating toward a specific unit."""
        self._active = True
        self._direction = None
        self._target_unit_id = unit_id
        self._visited.clear()
        self._steps_taken = 0
        # Allow more steps for distant units, but cap at 30
        self._max_steps = min(max(initial_distance * 2, 15), 30)
        self._follow_hand = 1
        self._reason = ""
        logger.info("Navigator activated: approach unit %d (max_steps=%d)", unit_id, self._max_steps)

    def deactivate(self) -> None:
        self._active = False
        self._direction = None
        self._target_unit_id = None

    def step(self, state: GameState) -> NavigatorResult:
        """Execute one navigation step. Returns result indicating what happened."""
        if not self._active:
            return NavigatorResult.DONE

        # --- Interrupt checks ---
        if state.hostile_units:
            self._reason = "hostile unit detected nearby"
            self._active = False
            return NavigatorResult.INTERRUPTED

        if state.conversation_phase != "none":
            self._reason = "conversation forced"
            self._active = False
            return NavigatorResult.INTERRUPTED

        if state.showing_announcements:
            self._reason = "announcement appeared"
            self._active = False
            return NavigatorResult.INTERRUPTED

        # Max steps check
        if self._steps_taken >= self._max_steps:
            self._reason = f"moved {self._steps_taken} tiles, returning control"
            self._active = False
            return NavigatorResult.DONE

        # --- Determine target direction ---
        if self._target_unit_id is not None:
            direction = self._direction_to_unit(state)
            if direction is None:
                self._reason = "target unit not found or reached"
                self._active = False
                return NavigatorResult.DONE
        else:
            direction = self._direction

        if direction is None:
            self._reason = "no direction set"
            self._active = False
            return NavigatorResult.DONE

        # --- Try to move ---
        chosen = self._find_passable_direction(direction, state.map_tiles)
        if chosen is None:
            self._reason = f"path blocked after {self._steps_taken} tiles"
            self._active = False
            return NavigatorResult.DONE

        # --- Loop detection ---
        pos = state.adventurer_position
        if pos:
            key = (pos.x, pos.y, pos.z)
            self._visited[key] = self._visited.get(key, 0) + 1
            if self._visited[key] >= 3:
                self._reason = f"stuck in loop after {self._steps_taken} tiles"
                self._active = False
                return NavigatorResult.DONE
            # Bounding box check: if we've taken 10+ steps but stayed within a small area, stuck
            if self._steps_taken >= 10 and len(self._visited) >= 5:
                xs = [p[0] for p in self._visited]
                ys = [p[1] for p in self._visited]
                bbox = max(max(xs) - min(xs), max(ys) - min(ys))
                # If bbox is tiny relative to steps, we're circling
                if bbox < self._steps_taken // 3:
                    self._reason = f"stuck in loop after {self._steps_taken} tiles (bbox={bbox})"
                    self._active = False
                    return NavigatorResult.DONE

        # Execute the move
        move_key = _MOVE_KEYS[chosen]
        self.lua.execute_action(move_key)
        self._steps_taken += 1
        logger.debug("Navigator step %d: moved %s", self._steps_taken, chosen)
        return NavigatorResult.MOVED

    def _direction_to_unit(self, state: GameState) -> str | None:
        """Compute compass direction from adventurer to target unit."""
        if state.adventurer_position is None:
            return None
        target: UnitInfo | None = None
        for u in state.nearby_units:
            if u.id == self._target_unit_id:
                target = u
                break
        if target is None:
            return None
        # If adjacent (distance <= 1), we're done
        if target.distance <= 1:
            self._reason = f"reached unit {target.name}"
            return None
        dx = target.position.x - state.adventurer_position.x
        dy = target.position.y - state.adventurer_position.y
        # Snap to nearest compass direction
        return self._snap_to_compass(dx, dy)

    @staticmethod
    def _snap_to_compass(dx: int, dy: int) -> str:
        """Snap a delta vector to the nearest 8-direction compass name."""
        if dx == 0 and dy == 0:
            return "n"  # fallback
        sx = (1 if dx > 0 else -1 if dx < 0 else 0)
        sy = (1 if dy > 0 else -1 if dy < 0 else 0)
        # Use diagonal when both components are significant
        if abs(dx) > 0 and abs(dy) > 0 and abs(dx) * 2 >= abs(dy) and abs(dy) * 2 >= abs(dx):
            key = (sx, sy)
        elif abs(dx) > abs(dy):
            key = (sx, 0)
        elif abs(dy) > abs(dx):
            key = (0, sy)
        else:
            key = (sx, sy)
        _delta_to_name = {v: k for k, v in DIRECTION_DELTAS.items()}
        return _delta_to_name.get(key, "n")

    def _find_passable_direction(self, primary: str, map_tiles: list[str]) -> str | None:
        """Try primary direction, then wall-follow rotations within forward hemisphere."""
        if self._is_passable(primary, map_tiles):
            return primary

        # Rotate from primary direction, alternating sides, max 90° each way (forward hemisphere)
        idx = _DIRECTIONS_CW.index(primary)
        for offset in range(1, 4):  # 45°, 90°, 135° — stop before 180°
            # Try one side
            candidate_idx = (idx + offset * self._follow_hand) % 8
            candidate = _DIRECTIONS_CW[candidate_idx]
            if self._is_passable(candidate, map_tiles):
                return candidate
            # Try other side
            candidate_idx = (idx - offset * self._follow_hand) % 8
            candidate = _DIRECTIONS_CW[candidate_idx]
            if self._is_passable(candidate, map_tiles):
                return candidate

        return None  # Nothing passable in forward hemisphere

    @staticmethod
    def _is_passable(direction: str, map_tiles: list[str]) -> bool:
        """Check if a direction leads to a walkable tile in the 5x5 map."""
        if not map_tiles:
            return True  # Can't check, assume passable
        dx, dy = DIRECTION_DELTAS[direction]
        row_idx = 2 + dy
        col_idx = 2 + dx
        if 0 <= row_idx < len(map_tiles):
            row = map_tiles[row_idx]
            if 0 <= col_idx < len(row):
                return row[col_idx] in _WALKABLE_CHARS
        return False

    def _choose_hand(self, direction: str, map_tiles: list[str]) -> int:
        """Choose wall-following hand based on which side has more open tiles."""
        if not map_tiles:
            return 1
        idx = _DIRECTIONS_CW.index(direction)
        left_open = 0
        right_open = 0
        for offset in range(1, 4):
            # Right side (clockwise)
            r_idx = (idx + offset) % 8
            if self._is_passable(_DIRECTIONS_CW[r_idx], map_tiles):
                right_open += 1
            # Left side (counter-clockwise)
            l_idx = (idx - offset) % 8
            if self._is_passable(_DIRECTIONS_CW[l_idx], map_tiles):
                left_open += 1
        # Prefer the side with more open tiles; default to right-hand (clockwise)
        if left_open > right_open:
            return -1  # counter-clockwise (left-hand rule)
        return 1  # clockwise (right-hand rule)
