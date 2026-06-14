"""Mutable in-memory world model for the OpenDwarf offline simulator.

This is component 3a of the simulator.  No DFHack, no I/O, no network —
pure Python, fully deterministic.  Action mutation (component 3b) will
add methods that advance this state; for now the world is read-only from
the executor's point of view.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SimUnit:
    """A single unit present in the simulated world."""

    id: int
    name: str
    race: str
    pos: tuple[int, int, int]
    is_hostile: bool = False
    hist_fig_id: int = -1   # -1 = non-historic (wild creature)
    is_tame: bool = False
    is_citizen: bool = False
    hp: int = 100           # unused in 3a; reserved for combat resolution


@dataclass
class SimWorld:
    """Mutable snapshot of adventure-mode state used by the offline simulator."""

    # ------------------------------------------------------------------
    # Adventurer
    # ------------------------------------------------------------------
    name: str = "Adventurer"
    pos: tuple[int, int, int] = (50, 50, 10)
    blood_count: int = 100
    blood_max: int = 100
    sneaking: bool = False

    # Physiological timers (count up; 0 = fresh)
    hunger_timer: int = 0
    thirst_timer: int = 0
    sleepiness_timer: int = 0
    exhaustion: int = 0

    # Skills — list of dicts with keys: id, level, experience
    skills: list = field(default_factory=list)

    # Wounds — list of dicts with keys: part, status
    wounds: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # Game / engine state
    # ------------------------------------------------------------------
    tick_counter: int = 0
    total_move: int = 0
    player_control_state: str = "TAKING_INPUT"
    menu_state: str = ""
    focus_state: str = "dungeonmode/Default"
    message: str = ""
    is_adventure_mode: bool = True

    # ------------------------------------------------------------------
    # World context
    # ------------------------------------------------------------------
    world_name: str = ""
    region_name: str = ""
    site_name: str = ""
    site_type: str = ""
    player_world_x: int = -1
    player_world_y: int = -1

    # ------------------------------------------------------------------
    # Units
    # ------------------------------------------------------------------
    units: list[SimUnit] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Attack menu
    # ------------------------------------------------------------------
    attack_menu_open: bool = False
    attack_menu_mode: int = -1
    attack_unit_choice: list[int] = field(default_factory=list)
    # Target chosen at attack-menu mode 0, resolved when the strike lands at mode 4.
    pending_target_id: int | None = None

    # ------------------------------------------------------------------
    # Fast travel
    # ------------------------------------------------------------------
    fast_travel_active: bool = False
    fast_travel_army_pos: tuple[int, int, int] | None = None

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------
    nearby_sites: list = field(default_factory=list)
    adventurer_dead: bool = False

    # ------------------------------------------------------------------
    # Canonical scenario factories
    # ------------------------------------------------------------------

    @classmethod
    def wolf_survival(cls) -> "SimWorld":
        """Return the canonical wolf-survival scenario starting state.

        The adventurer is at (50, 50, 10) in open wilderness (no site).
        One wild wolf stands two tiles east at (52, 50, 10).

        IMPORTANT — the wolf is ``is_hostile=False`` (matching the real DF
        behaviour where ``dfhack.units.isDanger()`` returns *false* for
        un-provoked wildlife).  It must therefore show up in
        ``GameState.huntable_units`` but NOT in ``GameState.hostile_units``.
        """
        wolf = SimUnit(
            id=1001,
            name="Wolf",
            race="WOLF",
            pos=(52, 50, 10),
            is_hostile=False,
            hist_fig_id=-1,
            is_tame=False,
            is_citizen=False,
        )
        return cls(units=[wolf])
