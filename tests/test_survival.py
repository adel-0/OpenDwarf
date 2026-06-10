"""Unit tests for survival gate evaluation."""

from opendwarf.goals.survival import SurvivalGates, evaluate
from opendwarf.state.game_state import GameState, Position, UnitInfo


def _make_state(**kwargs) -> GameState:
    s = GameState()
    s.blood_count = kwargs.get("blood_count", 100)
    s.blood_max = kwargs.get("blood_max", 100)
    s.hunger_timer = kwargs.get("hunger_timer", 0)
    s.thirst_timer = kwargs.get("thirst_timer", 0)
    s.sleepiness_timer = kwargs.get("sleepiness_timer", 0)
    s.exhaustion = kwargs.get("exhaustion", 0)
    s.hostile_units = kwargs.get("hostile_units", [])
    return s


def _hostile(dist: int) -> UnitInfo:
    return UnitInfo(id=1, name="Wolf", race="wolf",
                    position=Position(dist, 0, 0),
                    is_hostile=True, distance=dist)


class TestSurvivalGates:
    def test_safe_healthy_not_hungry(self):
        s = _make_state()
        g = evaluate(s)
        assert not g.in_danger
        assert not g.any_physio
        assert not g.any_critical
        assert g.hint() == ""

    def test_low_health_triggers_danger(self):
        s = _make_state(blood_count=20, blood_max=100)
        g = evaluate(s)
        assert g.in_danger
        assert "DANGER" in g.hint() or "low health" in g.hint().lower()

    def test_hostile_nearby_triggers_danger(self):
        s = _make_state(hostile_units=[_hostile(3)])
        g = evaluate(s)
        assert g.in_danger

    def test_hostile_far_no_danger(self):
        s = _make_state(hostile_units=[_hostile(10)])
        g = evaluate(s)
        assert not g.in_danger

    def test_hungry_flag(self):
        s = _make_state(hunger_timer=80_000)
        g = evaluate(s)
        assert g.hungry
        assert not g.hungry_critical
        assert "hungry" in g.hint().lower()

    def test_hungry_critical_flag(self):
        s = _make_state(hunger_timer=160_000)
        g = evaluate(s)
        assert g.hungry_critical
        assert "STARVING" in g.hint()

    def test_thirsty_flag(self):
        s = _make_state(thirst_timer=55_000)
        g = evaluate(s)
        assert g.thirsty
        assert not g.thirsty_critical

    def test_thirsty_critical_flag(self):
        s = _make_state(thirst_timer=105_000)
        g = evaluate(s)
        assert g.thirsty_critical
        assert "DEHYDRATED" in g.hint()

    def test_drowsy_flag(self):
        s = _make_state(sleepiness_timer=60_000)
        g = evaluate(s)
        assert g.drowsy
        assert not g.drowsy_critical

    def test_drowsy_critical_flag(self):
        s = _make_state(sleepiness_timer=120_000)
        g = evaluate(s)
        assert g.drowsy_critical
        assert "EXHAUSTED" in g.hint()

    def test_flee_trigger(self):
        s = _make_state(exhaustion=3000, hostile_units=[_hostile(3)])
        g = evaluate(s)
        assert g.flee_trigger
        assert "FLEE" in g.hint()

    def test_danger_with_physio_shows_both(self):
        s = _make_state(blood_count=20, blood_max=100, hunger_timer=80_000)
        g = evaluate(s)
        assert g.in_danger
        assert g.hungry
        hint = g.hint()
        assert "DANGER" in hint or "danger" in hint.lower()
        assert "hungry" in hint.lower()
