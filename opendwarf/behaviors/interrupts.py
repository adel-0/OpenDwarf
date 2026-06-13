"""The single source of interrupt truth for autopilot behaviors.

This module replaces `Skill._check_interrupts` as the one place that decides
"should code keep running, or does the LLM need to be asked?". The crucial
difference from the old skill logic: **a hostile the Policy authorizes engaging
is NOT an interrupt** — that is the whole point of the Policy/Behavior layer.

`check(state, policy, behavior)` returns an `Interrupt` (a reason + message) or
`None`. Skills that run *outside* a behavior pass `policy=None`, which restores
the old conservative behavior (any hostile / conversation / announcement stops).

The progress watchdog counters the documented LLM-agent waiting-loop pathology
(RESEARCH.md delta 2): it fires on *state-delta stagnation*, not step counting.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opendwarf.behaviors.base import Behavior
    from opendwarf.behaviors.policy import Policy
    from opendwarf.state.game_state import GameState


class InterruptReason(StrEnum):
    HOSTILE_UNHANDLED = "hostile_unhandled"   # hostile present that policy won't engage
    HEALTH_THRESHOLD = "health_threshold"     # health < policy.flee_below_health_pct
    CONVERSATION = "conversation"             # forced/any dialogue began
    ANNOUNCEMENT = "announcement"             # showing_announcements
    UNKNOWN_SCREEN = "unknown_screen"         # escape-hatch condition
    PHYSIO_CRITICAL = "physio_critical"       # hunger/thirst/drowsy critical, behavior can't self-serve
    TARGET_DONE = "target_done"               # behavior reports its goal reached
    STALLED = "stalled"                       # progress watchdog fired


@dataclass(frozen=True)
class Interrupt:
    reason: InterruptReason
    message: str

    def __str__(self) -> str:
        return f"{self.reason.value}: {self.message}"


# Focus patterns the loop handles natively without an LLM turn. Anything else is
# an unrecognized screen (escape-hatch / UNKNOWN_SCREEN). Kept here so both the
# loop and the interrupt checker agree on one definition.
KNOWN_FOCUS_PATTERNS = (
    "dungeonmode/Default",
    "dungeonmode/Conversation",
    "dungeonmode/Travel",
    "dungeonmode/Sleep",
    "dungeonmode/Look",
    "dungeonmode/ViewSheets",
    "Help",
    "DFHACK",
    "title",
)


def is_known_focus(focus: str | None) -> bool:
    if not focus:
        return True  # absent focus == normal play; never treat as unknown screen
    return any(pat in focus for pat in KNOWN_FOCUS_PATTERNS)


# ----------------------------------------------------------------------
# Progress watchdog (state-delta stagnation, not step counting)
# ----------------------------------------------------------------------

class StallWatchdog:
    """Hashes a cheap fingerprint of game state each behavior step; if the
    fingerprint is unchanged for N consecutive observations, the behavior is
    stalled (stuck in a no-progress loop) and should hand back to the LLM.
    """

    def __init__(self, threshold: int = 20) -> None:
        self.threshold = threshold
        self._last: tuple | None = None
        self._streak = 0

    @staticmethod
    def _fingerprint(state: "GameState") -> tuple:
        pos = state.adventurer_position
        pos_t = (pos.x, pos.y, pos.z) if pos is not None else None
        unit_ids = tuple(sorted(u.id for u in state.nearby_units))
        tick_bucket = state.tick_counter // 100
        return (pos_t, len(state.inventory), unit_ids, tick_bucket)

    def observe(self, state: "GameState") -> None:
        fp = self._fingerprint(state)
        if fp == self._last:
            self._streak += 1
        else:
            self._last = fp
            self._streak = 1  # this observation starts a fresh unchanged run

    def reset(self) -> None:
        self._last = None
        self._streak = 0

    @property
    def stalled(self) -> bool:
        return self._streak >= self.threshold


# ----------------------------------------------------------------------
# The checker
# ----------------------------------------------------------------------

def _hostiles_authorized(state: "GameState", policy: "Policy") -> bool:
    """True iff the Policy permits engaging the current hostile situation.

    A hostile is engageable if its race is on the species allow-list OR its danger
    tier is at/below `engage_tier_max`. The whole situation is authorized only if
    every hostile is engageable, the count is within `max_opponents`, and health is
    at/above `min_health_pct`.
    """
    hostiles = state.hostile_units
    if not hostiles:
        return True
    if len(hostiles) > policy.max_opponents:
        return False
    if state.health_pct < policy.min_health_pct:
        return False
    return all(policy.allows_engaging(u.race) for u in hostiles)


def check(
    state: "GameState",
    policy: "Policy | None",
    behavior: "Behavior | None",
) -> Interrupt | None:
    """Decide whether to interrupt autopilot and hand control to the LLM.

    `policy=None` ⇒ conservative skill-style behavior (any hostile / dialogue /
    announcement / unknown screen / critical physio interrupts).
    """
    # 1. Unknown screen — always hand back; no behavior models arbitrary menus.
    if not is_known_focus(state.focus_state) and not state.fast_travel_active:
        return Interrupt(InterruptReason.UNKNOWN_SCREEN,
                         f"unrecognized screen (focus: {state.focus_state})")

    # 2. Forced/any conversation.
    if state.conversation_phase != "none":
        return Interrupt(InterruptReason.CONVERSATION, "a conversation began")

    # 3. Pending announcements (NPC speech / events the player must page through).
    if state.showing_announcements:
        return Interrupt(InterruptReason.ANNOUNCEMENT, "an announcement is showing")

    # 4. Health below the policy flee threshold (always flee, never ask first
    #    only at the *decision* — the LLM decides how to flee).
    if policy is not None and state.health_pct < policy.flee_below_health_pct:
        return Interrupt(InterruptReason.HEALTH_THRESHOLD,
                         f"health {state.health_pct}% < flee threshold {policy.flee_below_health_pct}%")

    # 5. Hostiles. Without a policy, any hostile interrupts (old behavior).
    if state.hostile_units:
        if policy is None or not _hostiles_authorized(state, policy):
            races = ", ".join(sorted({u.race or "?" for u in state.hostile_units}))
            return Interrupt(InterruptReason.HOSTILE_UNHANDLED,
                             f"hostile(s) the policy will not engage: {races}")

    # 6. Critical physiology the running behavior cannot resolve itself.
    physio_critical = state.hungry_critical or state.thirsty_critical or state.drowsy_critical
    if physio_critical:
        if behavior is None or not behavior.handles_physio(state, policy):
            need = []
            if state.hungry_critical:
                need.append("starving")
            if state.thirsty_critical:
                need.append("dehydrated")
            if state.drowsy_critical:
                need.append("exhausted")
            return Interrupt(InterruptReason.PHYSIO_CRITICAL,
                             "critical needs autopilot can't serve: " + ", ".join(need))

    # 7. Progress watchdog.
    if behavior is not None and behavior.watchdog.stalled:
        return Interrupt(InterruptReason.STALLED,
                         f"no state change for {behavior.watchdog.threshold} steps")

    return None
