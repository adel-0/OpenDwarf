"""Unit tests for the NORTHSTAR M1 behavior layer: digest, watchdog, the
interrupt matrix (policy × state → reason), and behavior lifecycle."""

from __future__ import annotations

import pytest

from opendwarf.behaviors import interrupts as I
from opendwarf.behaviors.base import Behavior, BehaviorResult, BehaviorStatus
from opendwarf.behaviors.digest import EventDigest
from opendwarf.behaviors.interrupts import InterruptReason, StallWatchdog
from opendwarf.behaviors.policy import Policy
from opendwarf.state.game_state import GameState, Position, UnitInfo


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def _hostile(race="WOLF", dist=3, uid=1):
    return UnitInfo(id=uid, name=race.title(), race=race,
                    position=Position(1, 1, 0), is_hostile=True, distance=dist)


def _state(*, hostiles=None, blood=(100, 100), focus="dungeonmode/Default",
           conv="none", announce=False, travel=False,
           hunger=0, thirst=0, sleep=0):
    s = GameState()
    s.focus_state = focus
    s.conversation_phase = conv
    s.showing_announcements = announce
    s.fast_travel_active = travel
    s.blood_count, s.blood_max = blood
    s.hunger_timer, s.thirst_timer, s.sleepiness_timer = hunger, thirst, sleep
    s.adventurer_position = Position(0, 0, 0)
    for u in (hostiles or []):
        s.nearby_units.append(u)
        s.hostile_units.append(u)
    return s


# ----------------------------------------------------------------------
# EventDigest
# ----------------------------------------------------------------------

def test_digest_counts_and_order():
    d = EventDigest()
    d.add("killed bandit")
    d.add("ate plump helmet")
    d.add("killed bandit")
    assert "killed bandit (2)" in d.render()
    assert "ate plump helmet" in d.render()
    # first-seen order preserved
    assert d.render().index("killed bandit") < d.render().index("ate plump helmet")


def test_digest_empty_and_oneline():
    d = EventDigest()
    assert d.is_empty
    assert "no notable events" in d.render(behavior_name="patrol")
    d.add("reached waypoint 1")
    d.mark_action()
    assert "patrol" in d.one_line(behavior_name="patrol")
    assert "reached waypoint 1" in d.one_line(behavior_name="patrol")


def test_digest_ticks():
    d = EventDigest()
    d.note_tick(1000)
    d.note_tick(1240)
    assert d.ticks == 240


# ----------------------------------------------------------------------
# StallWatchdog
# ----------------------------------------------------------------------

def test_watchdog_fires_on_stagnation():
    w = StallWatchdog(threshold=5)
    s = _state()
    for _ in range(5):
        w.observe(s)
    assert w.stalled


def test_watchdog_resets_on_change():
    w = StallWatchdog(threshold=3)
    s = _state()
    w.observe(s); w.observe(s)
    s2 = _state()
    s2.adventurer_position = Position(5, 5, 0)
    w.observe(s2)
    assert not w.stalled
    w.observe(s2); w.observe(s2); w.observe(s2)
    assert w.stalled


def test_watchdog_travel_progress_resets_streak():
    """During fast travel getAdventurer() is nil → adventurer_position is None
    and the game tick barely advances; the only thing that moves is the travel
    army. The watchdog must track the army position or it false-fires mid-trek
    (the bug that killed every journey > 20 steps)."""
    w = StallWatchdog(threshold=3)
    for i in range(6):
        s = _state(travel=True)
        s.adventurer_position = None
        s.fast_travel_army_pos = Position(1340 + i, 1100, 0)  # army advancing
        w.observe(s)
    assert not w.stalled  # army moved every step → never stalled


def test_watchdog_travel_pinned_army_stalls():
    """A genuinely pinned travel army (no army-pos change) still stalls."""
    w = StallWatchdog(threshold=3)
    for _ in range(4):
        s = _state(travel=True)
        s.adventurer_position = None
        s.fast_travel_army_pos = Position(1340, 1100, 0)  # stuck
        w.observe(s)
    assert w.stalled


# ----------------------------------------------------------------------
# Interrupt matrix
# ----------------------------------------------------------------------

def test_no_interrupt_when_clear():
    assert I.check(_state(), Policy(), None) is None


def test_authorized_hostile_is_not_an_interrupt():
    policy = Policy(engage_species_allow=["WOLF"], max_opponents=1, min_health_pct=50)
    assert I.check(_state(hostiles=[_hostile("WOLF")]), policy, None) is None


def test_unauthorized_species_interrupts():
    policy = Policy(engage_species_allow=["WOLF"])
    intr = I.check(_state(hostiles=[_hostile("TROLL")]), policy, None)
    assert intr is not None and intr.reason is InterruptReason.HOSTILE_UNHANDLED


def test_too_many_opponents_interrupts():
    policy = Policy(engage_species_allow=["WOLF"], max_opponents=1)
    st = _state(hostiles=[_hostile("WOLF", uid=1), _hostile("WOLF", uid=2)])
    intr = I.check(st, policy, None)
    assert intr is not None and intr.reason is InterruptReason.HOSTILE_UNHANDLED


def test_low_health_to_engage_interrupts():
    policy = Policy(engage_species_allow=["WOLF"], min_health_pct=80, flee_below_health_pct=10)
    st = _state(hostiles=[_hostile("WOLF")], blood=(50, 100))  # 50% < 80%
    intr = I.check(st, policy, None)
    assert intr is not None and intr.reason is InterruptReason.HOSTILE_UNHANDLED


def test_health_threshold_interrupts_first():
    policy = Policy(flee_below_health_pct=40)
    intr = I.check(_state(blood=(30, 100)), policy, None)
    assert intr is not None and intr.reason is InterruptReason.HEALTH_THRESHOLD


def test_conversation_interrupts():
    intr = I.check(_state(conv="dialogue"), Policy(), None)
    assert intr.reason is InterruptReason.CONVERSATION


def test_announcement_interrupts():
    intr = I.check(_state(announce=True), Policy(), None)
    assert intr.reason is InterruptReason.ANNOUNCEMENT


def test_behavior_handles_announcements_suppresses_interrupt():
    class _Pager(Behavior):
        name = "pager"
        def _step(self, state):  # pragma: no cover
            return BehaviorResult.running()
        def handles_announcements(self, state):
            return True

    b = _Pager.__new__(_Pager)
    b.watchdog = StallWatchdog()
    # combat-log announcement up but behavior pages its own → no interrupt
    assert I.check(_state(announce=True), Policy(), b) is None


def test_unknown_screen_interrupts():
    intr = I.check(_state(focus="dungeonmode/SomeNewMenu"), Policy(), None)
    assert intr.reason is InterruptReason.UNKNOWN_SCREEN


def test_unknown_screen_ignored_during_fast_travel():
    assert I.check(_state(focus="weird", travel=True), Policy(), None) is None


def test_physio_critical_interrupts_without_behavior():
    intr = I.check(_state(thirst=200_000), Policy(), None)
    assert intr.reason is InterruptReason.PHYSIO_CRITICAL


def test_policy_none_any_hostile_interrupts():
    intr = I.check(_state(hostiles=[_hostile("RABBIT")]), None, None)
    assert intr.reason is InterruptReason.HOSTILE_UNHANDLED


def test_behavior_handles_physio_suppresses_interrupt():
    class _SelfServe(Behavior):
        name = "selfserve"
        def _step(self, state):  # pragma: no cover
            return BehaviorResult.running()
        def handles_physio(self, state, policy):
            return True

    b = _SelfServe.__new__(_SelfServe)
    b.watchdog = StallWatchdog()
    # critical thirst but behavior self-serves → no interrupt
    assert I.check(_state(thirst=200_000), Policy(), b) is None


def test_stall_watchdog_interrupt():
    class _Idle(Behavior):
        name = "idle"
        def _step(self, state):  # pragma: no cover
            return BehaviorResult.running()

    b = _Idle.__new__(_Idle)
    b.watchdog = StallWatchdog(threshold=2)
    s = _state()
    b.watchdog.observe(s); b.watchdog.observe(s)
    intr = I.check(s, Policy(), b)
    assert intr.reason is InterruptReason.STALLED


# ----------------------------------------------------------------------
# Interrupt precedence: unknown screen / conversation beat hostiles
# ----------------------------------------------------------------------

def test_conversation_beats_hostile():
    # A forced conversation while a hostile is present should report conversation.
    st = _state(hostiles=[_hostile("TROLL")], conv="dialogue")
    assert I.check(st, Policy(), None).reason is InterruptReason.CONVERSATION


# ----------------------------------------------------------------------
# BehaviorResult / base wrapper
# ----------------------------------------------------------------------

def test_behavior_step_wraps_watchdog():
    class _Counter(Behavior):
        name = "counter"
        def __init__(self):
            self.policy = Policy()
            self.digest = EventDigest()
            self.watchdog = StallWatchdog(threshold=3)
            self.calls = 0
        def _step(self, state):
            self.calls += 1
            return BehaviorResult.running()

    b = _Counter()
    s = _state()
    for _ in range(3):
        assert b.step(s).status is BehaviorStatus.RUNNING
    assert b.calls == 3
    assert b.watchdog.stalled  # observed unchanged state 3×
    assert b.digest.start_tick is not None


def test_watchdog_combat_progress_resets_streak():
    # Stationary striking doesn't move position/clock, but a landed strike bumps
    # the digest's notable count — that must reset the stall streak so an active
    # fight is never mistaken for a stall.
    class _Striker(Behavior):
        name = "striker"
        def __init__(self):
            self.policy = Policy()
            self.digest = EventDigest()
            self.watchdog = StallWatchdog(threshold=3)
        def _step(self, state):
            self.digest.add("struck wolf via attack menu")  # progress, same position
            return BehaviorResult.running()

    b = _Striker()
    s = _state()
    for _ in range(6):
        b.step(s)
    assert not b.watchdog.stalled  # each strike reset the streak
