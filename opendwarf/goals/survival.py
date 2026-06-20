"""Survival gate evaluation — pure function, no side effects.

Called before each LLM turn to determine which constraints and hints to inject.
These gates run in Python before any LLM call so the LLM sees a prioritised
context and never needs to infer urgency from raw numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opendwarf.state.game_state import GameState

# Tile distance at which a hostile unit triggers the danger gate
_HOSTILE_DANGER_RADIUS = 5


@dataclass(frozen=True)
class SurvivalGates:
    """Which survival conditions are active this turn."""

    in_danger: bool          # low health or hostile nearby — survival goals only
    physio_critical: bool    # hunger/thirst/drowsiness critical — physio priority
    flee_trigger: bool       # exhaustion critical + hostile — flee immediately
    hungry_critical: bool
    thirsty_critical: bool
    drowsy_critical: bool
    hungry: bool
    thirsty: bool
    drowsy: bool
    # Honest capability flags (computed in state.lua from DF's full consumable set:
    # carried food/drink incl. flask + container contents, plus an adjacent water
    # tile). These — not top-level inventory classification — decide whether an
    # `eat`/`drink` action actually exists this turn.
    can_eat: bool = True
    can_drink: bool = True
    water_adjacent: bool = False

    @property
    def any_critical(self) -> bool:
        return self.hungry_critical or self.thirsty_critical or self.drowsy_critical

    @property
    def any_physio(self) -> bool:
        return self.hungry or self.thirsty or self.drowsy

    def hint(self) -> str:
        """Build a human-readable priority hint for the LLM prompt."""
        parts: list[str] = []

        if self.flee_trigger:
            parts.append(
                "CRITICAL: exhaustion is severe AND enemies are nearby — FLEE NOW. "
                "Use goto_site to the nearest town or break line of sight."
            )
        elif self.in_danger:
            parts.append(
                "DANGER: low health or hostile unit nearby. "
                "Prioritise survival: flee, fight, or seek safety."
            )

        physio_msgs = []
        # Tell the model to eat/drink ONLY when an executable action actually exists
        # this turn (can_eat/can_drink — see field docstrings). Otherwise redirect to
        # ACQUIRING a consumable; never promise "drink immediately" when no drink
        # action is available (the run-ending failure mode — the model looped on a
        # no-op autopilot it couldn't serve).
        if self.hungry_critical:
            physio_msgs.append("STARVING — use 'eat' now" if self.can_eat
                               else "STARVING and NO food reachable — acquire food: hunt nearby "
                                    "wildlife (attack), loot a corpse, or travel to a town to "
                                    "buy/take it (no eat action is available right now)")
        elif self.hungry:
            physio_msgs.append("hungry — use 'eat'" if self.can_eat else "hungry — find food soon")
        if self.thirsty_critical:
            if self.can_drink:
                src = ("the adjacent water" if self.water_adjacent
                       else "your waterskin/flask")
                physio_msgs.append(f"DEHYDRATED — use 'drink' now ({src}); repeat until rehydrated")
            else:
                physio_msgs.append("DEHYDRATED and NO drink reachable — use 'goto_water' to "
                                   "path to a mapped water tile (river/pool), then 'drink'; "
                                   "if no water is mapped, 'explore' to find one or travel to "
                                   "a town with drink (no drink action is available right now)")
        elif self.thirsty:
            physio_msgs.append("thirsty — use 'drink'" if self.can_drink
                               else "thirsty — find water soon")
        if self.drowsy_critical:
            physio_msgs.append("EXHAUSTED — sleep immediately (safe location only)")
        elif self.drowsy:
            physio_msgs.append("drowsy — sleep when safe")

        if physio_msgs:
            urgency = "URGENT" if self.any_critical else "NOTE"
            parts.append(f"{urgency}: {'; '.join(physio_msgs)}.")

        return "\n".join(parts)


def evaluate(state: "GameState") -> SurvivalGates:
    """Evaluate all survival gates from the current game state."""
    hostile_close = any(
        u.distance <= _HOSTILE_DANGER_RADIUS for u in state.hostile_units
    )
    low_health = state.health_pct < 25

    in_danger = low_health or hostile_close
    flee_trigger = state.exhaustion_critical and bool(state.hostile_units)
    physio_critical = (state.hungry_critical or state.thirsty_critical
                       or state.drowsy_critical) and not in_danger

    return SurvivalGates(
        in_danger=in_danger,
        physio_critical=physio_critical,
        flee_trigger=flee_trigger,
        hungry_critical=state.hungry_critical,
        thirsty_critical=state.thirsty_critical,
        drowsy_critical=state.drowsy_critical,
        hungry=state.hungry,
        thirsty=state.thirsty,
        drowsy=state.drowsy,
        can_eat=state.can_eat,
        can_drink=state.can_drink,
        water_adjacent=state.water_adjacent,
    )
