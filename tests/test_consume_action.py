"""Unit tests for the eat/drink (consume) action: availability gating on the
honest can_eat/can_drink capability flags, dispatch to ConsumeSkill, and the
ConsumeSkill menu protocol (open -> dismiss Help -> pick -> close)."""

from opendwarf.actions.registry import default_registry
from opendwarf.actions.skills import ConsumeSkill, SkillContext, SkillStatus
from opendwarf.state.game_state import GameState


def _state(**kw) -> GameState:
    s = GameState()
    s.is_adventure_mode = True
    s.can_eat = kw.get("can_eat", False)
    s.can_drink = kw.get("can_drink", False)
    s.water_adjacent = kw.get("water_adjacent", False)
    s.focus_state = kw.get("focus_state", "dungeonmode/Default")
    s.player_control_state = kw.get("player_control_state", "TAKING_INPUT")
    return s


def _actions(state: GameState) -> dict:
    """Map of available action_str -> description from the registry."""
    reg = default_registry()
    out = {}
    for spec in reg._specs:
        if spec.available(state):
            for a, d in spec.enumerate_fn(state):
                out[a] = d or ""
    return out


class _FakeLua:
    def __init__(self):
        self.calls = []

    def execute_action(self, action):
        self.calls.append(action)
        return ["OK"]

    def run_script(self, name, args=None):
        self.calls.append(f"script:{name}")
        return ["OK"]


def _ctx(lua):
    return SkillContext(lua=lua, chunk_map=None, pathfinder=None, extractor=None)


class TestConsumeAvailability:
    def test_no_consumable_no_actions(self):
        acts = _actions(_state())
        assert "drink" not in acts
        assert "eat" not in acts

    def test_can_drink_offers_drink_only(self):
        acts = _actions(_state(can_drink=True))
        assert "drink" in acts
        assert "eat" not in acts

    def test_can_eat_offers_eat_only(self):
        acts = _actions(_state(can_eat=True))
        assert "eat" in acts
        assert "drink" not in acts

    def test_adjacent_water_names_source_in_desc(self):
        acts = _actions(_state(can_drink=True, water_adjacent=True))
        assert "adjacent water" in acts["drink"].lower()

    def test_not_offered_in_conversation(self):
        s = _state(can_drink=True, can_eat=True)
        s.conversation_phase = "dialogue"
        acts = _actions(s)
        assert "drink" not in acts and "eat" not in acts

    def test_not_offered_during_combat(self):
        s = _state(can_drink=True)
        from opendwarf.state.game_state import Position, UnitInfo
        s.hostile_units = [UnitInfo(id=1, name="w", race="w",
                                    position=Position(1, 0, 0), is_hostile=True, distance=1)]
        s.nearby_units = list(s.hostile_units)
        acts = _actions(s)
        assert "drink" not in acts


class TestGotoWaterAvailability:
    """goto_water bridges the dehydration deadlock: it must be offered exactly
    when the agent has no immediate drink, and withdrawn once it does."""

    def test_offered_when_no_drink(self):
        acts = _actions(_state(can_drink=False))
        assert "goto_water" in acts
        assert "water" in acts["goto_water"].lower()

    def test_absent_when_can_drink(self):
        acts = _actions(_state(can_drink=True))
        assert "goto_water" not in acts

    def test_not_offered_during_combat(self):
        s = _state(can_drink=False)
        from opendwarf.state.game_state import Position, UnitInfo
        s.hostile_units = [UnitInfo(id=1, name="w", race="w",
                                    position=Position(1, 0, 0), is_hostile=True, distance=1)]
        s.nearby_units = list(s.hostile_units)
        acts = _actions(s)
        assert "goto_water" not in acts


class TestConsumeDispatch:
    def test_drink_dispatches_consume_skill(self):
        s = _state(can_drink=True)
        d = default_registry().resolve("drink", s, _ctx(_FakeLua()))
        assert isinstance(d.skill, ConsumeSkill)
        assert d.skill._want == "drink"

    def test_eat_dispatches_consume_skill(self):
        s = _state(can_eat=True)
        d = default_registry().resolve("eat", s, _ctx(_FakeLua()))
        assert isinstance(d.skill, ConsumeSkill)
        assert d.skill._want == "food"


class TestConsumeSkillProtocol:
    def test_full_flow_opens_picks_and_closes(self):
        lua = _FakeLua()
        skill = ConsumeSkill(_ctx(lua), want="drink", label="drink")

        # step 1: Default + TAKING_INPUT -> press A_INV_EATDRINK
        r = skill.step(_state(focus_state="dungeonmode/Default"))
        assert r.status is SkillStatus.RUNNING
        assert "A_INV_EATDRINK" in lua.calls

        # step 2: first-use Help overlay -> clickok, no menu action yet
        r = skill.step(_state(focus_state="dungeonmode/Help"))
        assert r.status is SkillStatus.RUNNING
        assert any("clickok" in c for c in lua.calls)

        # step 3: Inventory menu up -> pick the drink
        r = skill.step(_state(focus_state="dungeonmode/Inventory"))
        assert r.status is SkillStatus.RUNNING
        assert "eatdrink_pick:drink" in lua.calls

        # step 4: menu closed -> DONE
        r = skill.step(_state(focus_state="dungeonmode/Default"))
        assert r.status is SkillStatus.DONE
        assert "consumed drink" in r.outcome

    def test_waits_for_taking_input_before_opening(self):
        lua = _FakeLua()
        skill = ConsumeSkill(_ctx(lua), want="food", label="food")
        r = skill.step(_state(focus_state="dungeonmode/Default",
                              player_control_state="ANIMATION"))
        assert r.status is SkillStatus.RUNNING
        assert "A_INV_EATDRINK" not in lua.calls  # did not press while animating

    def test_menu_never_opens_interrupts(self):
        lua = _FakeLua()
        skill = ConsumeSkill(_ctx(lua), want="food", label="food")
        skill.step(_state())  # opens
        # menu never appears (focus stays Default), bail after _MAX_WAIT
        last = None
        for _ in range(ConsumeSkill._MAX_WAIT + 2):
            last = skill.step(_state(focus_state="dungeonmode/Default"))
        assert last.status is SkillStatus.INTERRUPTED
