"""Simulated LuaExecutor — component 3a/3b of the OpenDwarf offline simulator.

Implements the same method surface as ``opendwarf.dfhack.lua_executor.LuaExecutor``
but operates purely against a ``SimWorld`` in memory.  No DFHack, no network,
no I/O.

Component 3b: ``execute_action`` mutates the world according to the documented
DF adventure-mode semantics (movement, bump-to-attack, attack-menu state machine).
"""

from __future__ import annotations

from opendwarf.sim.world import SimUnit, SimWorld
from opendwarf.spatial.compass import NAME_TO_DELTA, sign

# Damage dealt by a single melee strike (bump or attack-menu resolution).
_STRIKE_DAMAGE = 50

# Blood lost by the adventurer per adjacent-hostile attack during an adversary turn.
_WOLF_DAMAGE = 10

# Map A_MOVE_<DIR> key suffixes to compass direction names.
_MOVE_KEY_TO_DIR: dict[str, str] = {
    "A_MOVE_N": "n",
    "A_MOVE_S": "s",
    "A_MOVE_E": "e",
    "A_MOVE_W": "w",
    "A_MOVE_NE": "ne",
    "A_MOVE_NW": "nw",
    "A_MOVE_SE": "se",
    "A_MOVE_SW": "sw",
}


def _chebyshev(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    """Chebyshev (king-move) distance in the xy plane (same z required)."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _manhattan(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def _adjacent_huntable(world: SimWorld) -> list[int]:
    """Return the ids of huntable units adjacent (Chebyshev dist 1, same z) to the adventurer.

    Huntable = not tame AND not citizen (same definition as GameState.huntable_units).
    Order is stable: sorted by unit id.
    """
    ax, ay, az = world.pos
    ids: list[int] = []
    for u in world.units:
        ux, uy, uz = u.pos
        if uz != az:
            continue
        if max(abs(ux - ax), abs(uy - ay)) == 1:
            if not u.is_tame and not u.is_citizen:
                ids.append(u.id)
    ids.sort()
    return ids


class SimulatedLuaExecutor:
    """Drop-in simulator for ``LuaExecutor``.

    Parameters
    ----------
    world:
        The mutable ``SimWorld`` that this executor reads (and, in later
        components, mutates).
    """

    def __init__(self, world: SimWorld) -> None:
        self.world = world
        # Audit logs — populated by stub methods for test assertions.
        self.actions: list[str] = []
        self.scripts: list[str] = []

    # ------------------------------------------------------------------
    # Primary read method — the authoritative contract for component 3a
    # ------------------------------------------------------------------

    def extract_state(self) -> dict:
        """Serialise ``self.world`` into the exact nested-dict shape that
        ``GameState.from_raw()`` consumes.

        Key naming follows the Lua extractor output (e.g. ``hist_figure_id``
        not ``hist_fig_id``; ``nearby_units`` not ``units``).
        """
        w = self.world
        ax, ay, az = w.pos

        # Build the nearby_units list, computing Manhattan distance live so
        # re-extractions after a pos mutation reflect the updated distance.
        nearby_units = []
        for u in w.units:
            ux, uy, uz = u.pos
            dist = abs(ux - ax) + abs(uy - ay) + abs(uz - az)
            nearby_units.append(
                {
                    "id": u.id,
                    "name": u.name,
                    "race": u.race,
                    "position": {"x": ux, "y": uy, "z": uz},
                    "is_hostile": u.is_hostile,
                    "distance": dist,
                    "hist_figure_id": u.hist_fig_id,   # NOTE: raw key matches Lua
                    "is_tame": u.is_tame,
                    "is_citizen": u.is_citizen,
                }
            )

        # Army pos for fast travel (None → None; dict otherwise)
        army_pos: dict | None = None
        if w.fast_travel_army_pos is not None:
            fx, fy, fz = w.fast_travel_army_pos
            army_pos = {"x": fx, "y": fy, "z": fz}

        return {
            "adventurer": {
                "name": w.name,
                "position": {"x": ax, "y": ay, "z": az},
                "blood_count": w.blood_count,
                "blood_max": w.blood_max,
                "sneaking": w.sneaking,
                "hunger_timer": w.hunger_timer,
                "thirst_timer": w.thirst_timer,
                "sleepiness_timer": w.sleepiness_timer,
                "exhaustion": w.exhaustion,
                "skills": list(w.skills),   # already list[dict]
                "wounds": list(w.wounds),   # already list[dict]
            },
            "game": {
                "tick_counter": w.tick_counter,
                "total_move": w.total_move,
                "player_control_state": w.player_control_state,
                "menu_state": w.menu_state,
                "focus_state": w.focus_state,
                "message": w.message,
                "is_adventure_mode": w.is_adventure_mode,
            },
            "world": {
                "world_name": w.world_name,
                "region_name": w.region_name,
                "site_name": w.site_name,
                "site_type": w.site_type,
                "player_world_x": w.player_world_x,
                "player_world_y": w.player_world_y,
            },
            "nearby_units": nearby_units,
            "inventory": [],
            "conversation_phase": "none",
            "conversation_choices": [],
            "attack_menu": {
                "open": w.attack_menu_open,
                "mode": w.attack_menu_mode,
                "unit_choice": list(w.attack_unit_choice),
            },
            "map_tiles": [],
            "floor_items": [],
            "party": [],
            "showing_announcements": False,
            "announcement_screen": False,
            "announcement_text": [],
            "in_combat": False,
            "combat_log": [],
            "adventurer_entities": [],
            "npc_relationships": [],
            "quests": [],
            "fast_travel": {
                "active": w.fast_travel_active,
                "army_pos": army_pos,
            },
            "nearby_sites": [],
            "adventurer_dead": w.adventurer_dead,
        }

    def extract_screen_context(self) -> dict:
        """Alias for ``extract_state`` — matches the real LuaExecutor API."""
        return self.extract_state()

    # ------------------------------------------------------------------
    # Component 3b — world mutation
    # ------------------------------------------------------------------

    def execute_action(self, action: str) -> list[str]:
        """Record the requested action and mutate ``self.world`` accordingly.

        Scope: player-initiated effects only.  No adversary AI, no world tick,
        no damage to the adventurer — those are component 3c.
        """
        self.actions.append(action)
        w = self.world

        # ---- movement ---------------------------------------------------
        if action in _MOVE_KEY_TO_DIR:
            self._do_move(action)
            return []

        if action == "A_MOVE_SAME_SQUARE":
            # Wait — no movement, but time passes (adversary turn).
            self._advance_world()
            return []

        # ---- attack-menu opening via press:A_ATTACK ---------------------
        if action == "press:A_ATTACK":
            if not w.attack_menu_open:
                w.attack_menu_open = True
                w.attack_menu_mode = 0
                w.focus_state = "dungeonmode/Attack"
                w.attack_unit_choice = _adjacent_huntable(w)
            # Re-press while already open → no-op.
            return []

        # ---- attack-menu state machine ----------------------------------
        if action.startswith("attack_pick:") or action == "attack_strike":
            self._do_attack_menu(action)
            return []

        # ---- unknown / not-yet-implemented actions ----------------------
        # Just recorded above; no world effect.
        return []

    def _do_move(self, key: str) -> None:
        """Apply a directional move action against the world."""
        w = self.world
        direction = _MOVE_KEY_TO_DIR[key]
        dx, dy = NAME_TO_DELTA[direction]
        ax, ay, az = w.pos
        tx, ty = ax + dx, ay + dy

        # Look for a unit on the target tile (same z).
        target_unit: SimUnit | None = None
        for u in w.units:
            ux, uy, uz = u.pos
            if ux == tx and uy == ty and uz == az:
                target_unit = u
                break

        if target_unit is None:
            # Empty tile — move there. Time passes.
            w.pos = (tx, ty, az)
            w.total_move += 1
            self._advance_world()
        elif target_unit.is_hostile:
            # Bump-to-attack: deal damage, do NOT move. Time passes.
            self._apply_damage(target_unit)
            self._advance_world()
        else:
            # Neutral/wild unit — open the attack menu instead of moving.
            w.attack_menu_open = True
            w.attack_menu_mode = 0
            w.focus_state = "dungeonmode/Attack"
            # Populate unit_choice with all adjacent huntable units, stable order.
            w.attack_unit_choice = _adjacent_huntable(w)

    def _do_attack_menu(self, action: str) -> None:
        """Advance the attack-menu state machine."""
        w = self.world
        if not w.attack_menu_open:
            return

        mode = w.attack_menu_mode

        if action == "attack_strike":
            # Only valid at mode 2.
            if mode == 2:
                w.attack_menu_mode = 3
            return

        # action is "attack_pick:<n>"
        try:
            n = int(action.split(":", 1)[1])
        except (IndexError, ValueError):
            return

        if mode == 0:
            # Select target: record which unit we're attacking, advance to mode 2.
            if w.attack_unit_choice:
                idx = max(0, min(n, len(w.attack_unit_choice) - 1))
                w.pending_target_id = w.attack_unit_choice[idx]
            w.attack_menu_mode = 2

        elif mode == 2:
            # Unexpected pick at mode 2 (skill sends attack_strike here, not pick);
            # treat as a generic advance just in case.
            w.attack_menu_mode = 3

        elif mode == 3:
            # Body part selected → advance to mode 4.
            w.attack_menu_mode = 4

        elif mode == 4:
            # Weapon/attack selected → RESOLVE the strike and close the menu.
            target_id = w.pending_target_id
            if target_id is not None:
                for u in list(w.units):
                    if u.id == target_id:
                        self._apply_damage(u)
                        break
            # Close the menu.
            w.attack_menu_open = False
            w.attack_menu_mode = -1
            w.focus_state = "dungeonmode/Default"
            w.attack_unit_choice = []
            w.pending_target_id = None
            # The strike resolution consumes time — adversary turn runs.
            self._advance_world()

    def _apply_damage(self, unit: SimUnit) -> None:
        """Deal ``_STRIKE_DAMAGE`` to *unit*; remove it from the world if killed.

        A unit that survives a strike is aggravated — it turns hostile and will
        retaliate on the adversary turn (matching DF: attacking wildlife provokes it).
        """
        unit.hp -= _STRIKE_DAMAGE
        if unit.hp <= 0:
            self.world.units = [u for u in self.world.units if u.id != unit.id]
        else:
            unit.is_hostile = True

    def _advance_world(self) -> None:
        """Run one adversary turn: pass time, let hostiles act, check death.

        Called ONLY by time-consuming player actions (a successful move, a
        bump-attack, a wait, an attack-menu strike resolution) — never by
        attack-menu *navigation*, which is free.

        Each hostile on the adventurer's z-level either strikes (if adjacent)
        or steps one tile closer. The adventurer dies when blood runs out.
        """
        w = self.world
        if w.adventurer_dead:
            return
        w.tick_counter += 10
        ax, ay, az = w.pos
        for u in w.units:
            if not u.is_hostile:
                continue
            ux, uy, uz = u.pos
            if uz != az:
                continue
            if max(abs(ux - ax), abs(uy - ay)) <= 1:
                # Adjacent (or co-located) — strike the adventurer.
                w.blood_count = max(0, w.blood_count - _WOLF_DAMAGE)
            else:
                # Close the distance by one tile.
                sx, sy = sign(ax - ux, ay - uy)
                u.pos = (ux + sx, uy + sy, uz)
        if w.blood_count <= 0:
            w.adventurer_dead = True

    def run_script(self, name: str, args: list | None = None) -> list[str]:
        """Record the script invocation and return an empty log.

        Scripts (e.g. ``opendwarf--clickok``) have no in-world effect in the
        sim — the Help overlay is never raised, so clickok is never needed.
        """
        self.scripts.append(name)
        return []

    def deploy_scripts(self) -> None:
        """No-op — no file system in the simulator."""
        pass

    def extract_map(self, radius: int = 40) -> dict:
        """Stub — map tile extraction is not implemented in 3a."""
        return {}

    def extract_screen_text(self) -> dict:
        """Minimal screen-text stub matching the real API shape."""
        return {"focus": [self.world.focus_state], "rows": []}

    def resolve_site(self, name: str) -> list:
        """Stub — site resolution requires world-data not yet modelled."""
        return []

    def consume_action_errors(self, wait_s: float = 0.0) -> list[str]:
        """Stub — no deferred-input error channel in the simulator."""
        return []

    def inspect_ui(self) -> dict:
        """Stub — UI inspection not implemented in 3a."""
        return {}

    def find_keys(self, pattern: str) -> list[str]:
        """Stub — key enumeration not implemented in 3a."""
        return []
