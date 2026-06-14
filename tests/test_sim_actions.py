"""Tests for component 3b: SimulatedLuaExecutor world mutation.

Covers:
  1. Movement into an empty tile
  2. Bump-attack against a hostile unit
  3. Bumping a neutral/wild unit opens the attack menu
  4. Killing via the manual attack-menu action sequence
  5. End-to-end: real CombatStrikeSkill driving the sim to kill a wolf
"""

from __future__ import annotations

import pytest

from opendwarf.actions.skills import CombatStrikeSkill, SkillContext, SkillStatus
from opendwarf.sim import SimulatedLuaExecutor, SimUnit, SimWorld
from opendwarf.sim.executor import _STRIKE_DAMAGE, _WOLF_DAMAGE
from opendwarf.state.game_state import GameState, Position


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(sim: SimulatedLuaExecutor) -> GameState:
    """Extract and parse state from the sim."""
    return GameState.from_raw(sim.extract_state())


# ---------------------------------------------------------------------------
# Test 1 — movement into an empty tile
# ---------------------------------------------------------------------------

class TestMovementEmptyTile:
    """A_MOVE_E on an empty tile advances the adventurer and increments total_move."""

    def setup_method(self):
        # Adventurer at (50,50,10); wolf at (52,50,10) — tile (51,50,10) is empty.
        self.world = SimWorld.wolf_survival()
        self.sim = SimulatedLuaExecutor(self.world)

    def test_position_advances_east(self):
        self.sim.execute_action("A_MOVE_E")
        assert self.world.pos == (51, 50, 10)

    def test_total_move_increments(self):
        before = self.world.total_move
        self.sim.execute_action("A_MOVE_E")
        assert self.world.total_move > before

    def test_state_reflects_new_position(self):
        self.sim.execute_action("A_MOVE_E")
        state = _state(self.sim)
        assert state.adventurer_position == Position(51, 50, 10)

    def test_action_recorded(self):
        self.sim.execute_action("A_MOVE_E")
        assert "A_MOVE_E" in self.sim.actions

    def test_no_move_on_same_square(self):
        original = self.world.pos
        self.sim.execute_action("A_MOVE_SAME_SQUARE")
        assert self.world.pos == original


# ---------------------------------------------------------------------------
# Test 2 — bump a hostile unit
# ---------------------------------------------------------------------------

class TestBumpHostile:
    """Bumping a hostile unit deals _STRIKE_DAMAGE and does NOT move the adventurer."""

    def setup_method(self):
        # Place adventurer at (50,50,10) and a hostile unit one tile east.
        hostile = SimUnit(
            id=2001,
            name="Goblin",
            race="GOBLIN",
            pos=(51, 50, 10),
            is_hostile=True,
            hist_fig_id=99,
            hp=100,
        )
        self.world = SimWorld(pos=(50, 50, 10), units=[hostile])
        self.sim = SimulatedLuaExecutor(self.world)
        self.goblin = hostile

    def test_damage_dealt(self):
        before_hp = self.goblin.hp
        self.sim.execute_action("A_MOVE_E")
        assert self.goblin.hp == before_hp - _STRIKE_DAMAGE

    def test_adventurer_does_not_move(self):
        self.sim.execute_action("A_MOVE_E")
        assert self.world.pos == (50, 50, 10)

    def test_total_move_unchanged(self):
        before = self.world.total_move
        self.sim.execute_action("A_MOVE_E")
        assert self.world.total_move == before

    def test_hostile_killed_when_hp_low(self):
        self.goblin.hp = _STRIKE_DAMAGE  # exactly lethal
        self.sim.execute_action("A_MOVE_E")
        assert all(u.id != self.goblin.id for u in self.world.units)


# ---------------------------------------------------------------------------
# Test 3 — bumping a neutral/wild unit opens the attack menu
# ---------------------------------------------------------------------------

class TestBumpNeutralOpensMenu:
    """Bumping a non-hostile unit opens the Attack menu, deals no damage, and doesn't move."""

    def setup_method(self):
        # Wolf-survival scenario: adventurer at (50,50,10), wolf at (52,50,10).
        # Move adventurer one step east first so the wolf is adjacent (51 → 52).
        self.world = SimWorld.wolf_survival()
        self.world.pos = (51, 50, 10)   # wolf is now one tile east
        self.sim = SimulatedLuaExecutor(self.world)
        self.wolf = self.world.units[0]  # Wolf at (52,50,10)

    def test_attack_menu_opens(self):
        self.sim.execute_action("A_MOVE_E")
        assert self.world.attack_menu_open is True

    def test_attack_menu_mode_zero(self):
        self.sim.execute_action("A_MOVE_E")
        assert self.world.attack_menu_mode == 0

    def test_focus_set_to_attack(self):
        self.sim.execute_action("A_MOVE_E")
        assert self.world.focus_state == "dungeonmode/Attack"

    def test_no_damage_dealt(self):
        hp_before = self.wolf.hp
        self.sim.execute_action("A_MOVE_E")
        assert self.wolf.hp == hp_before

    def test_adventurer_does_not_move(self):
        self.sim.execute_action("A_MOVE_E")
        assert self.world.pos == (51, 50, 10)

    def test_wolf_in_unit_choice(self):
        self.sim.execute_action("A_MOVE_E")
        assert self.wolf.id in self.world.attack_unit_choice

    def test_state_reflects_open_menu(self):
        self.sim.execute_action("A_MOVE_E")
        state = _state(self.sim)
        assert state.attack_menu_open is True
        assert state.attack_menu_mode == 0


# ---------------------------------------------------------------------------
# Test 4 — kill a wolf via the manual attack-menu action sequence
# ---------------------------------------------------------------------------

class TestKillViaAttackMenu:
    """Drive the attack-menu protocol manually and verify the wolf is killed."""

    def setup_method(self):
        # Wolf adjacent to adventurer, hp at most _STRIKE_DAMAGE so one strike kills.
        wolf = SimUnit(
            id=1001,
            name="Wolf",
            race="WOLF",
            pos=(51, 50, 10),
            is_hostile=False,
            hist_fig_id=-1,
            is_tame=False,
            is_citizen=False,
            hp=_STRIKE_DAMAGE,  # exactly lethal
        )
        self.world = SimWorld(pos=(50, 50, 10), units=[wolf])
        self.sim = SimulatedLuaExecutor(self.world)

    def _exec(self, action: str) -> None:
        self.sim.execute_action(action)

    def test_full_sequence_kills_wolf_and_closes_menu(self):
        # Step 1: open the attack menu
        self._exec("press:A_ATTACK")
        assert self.world.attack_menu_open is True
        assert self.world.attack_menu_mode == 0

        # Step 2: pick target (wolf is at index 0)
        self._exec("attack_pick:0")
        assert self.world.attack_menu_mode == 2

        # Step 3: pick Strike
        self._exec("attack_strike")
        assert self.world.attack_menu_mode == 3

        # Step 4: pick body part
        self._exec("attack_pick:0")
        assert self.world.attack_menu_mode == 4

        # Step 5: pick weapon — resolves the strike and closes the menu
        self._exec("attack_pick:0")
        assert self.world.attack_menu_open is False
        assert self.world.attack_menu_mode == -1
        assert self.world.focus_state == "dungeonmode/Default"
        assert self.world.attack_unit_choice == []

        # Wolf is dead (removed from world.units)
        assert all(u.id != 1001 for u in self.world.units)

    def test_mode_progression_0_2_3_4_closed(self):
        """Assert the exact mode sequence mandated by the CLAUDE.md protocol."""
        modes: list[int] = []
        self._exec("press:A_ATTACK")
        modes.append(self.world.attack_menu_mode)   # 0
        self._exec("attack_pick:0")
        modes.append(self.world.attack_menu_mode)   # 2
        self._exec("attack_strike")
        modes.append(self.world.attack_menu_mode)   # 3
        self._exec("attack_pick:0")
        modes.append(self.world.attack_menu_mode)   # 4
        self._exec("attack_pick:0")
        assert modes == [0, 2, 3, 4]
        assert self.world.attack_menu_open is False


# ---------------------------------------------------------------------------
# Test 5 — real CombatStrikeSkill driving the sim end-to-end
# ---------------------------------------------------------------------------

class TestCombatStrikeSkillEndToEnd:
    """Drive a real CombatStrikeSkill against SimulatedLuaExecutor until DONE.

    This proves that the sim's attack-menu state machine is compatible with the
    actual deterministic skill that runs in production against DF.
    """

    def setup_method(self):
        # Adventurer at (50,50,10); wolf one tile east at (51,50,10).
        # Wolf hp = _STRIKE_DAMAGE so it dies from a single strike.
        wolf = SimUnit(
            id=1001,
            name="Wolf",
            race="WOLF",
            pos=(51, 50, 10),
            is_hostile=False,
            hist_fig_id=-1,
            is_tame=False,
            is_citizen=False,
            hp=_STRIKE_DAMAGE,
        )
        self.world = SimWorld(pos=(50, 50, 10), units=[wolf])
        self.sim = SimulatedLuaExecutor(self.world)
        ctx = SkillContext(self.sim, None, None, None)
        self.skill = CombatStrikeSkill(ctx, unit_id=1001, target_name="wolf")

    def test_skill_reaches_done(self):
        """The skill must terminate DONE within a bounded number of steps."""
        result = None
        for _ in range(20):
            state = GameState.from_raw(self.sim.extract_state())
            result = self.skill.step(state)
            if result.status is SkillStatus.DONE:
                break
        assert result is not None
        assert result.status is SkillStatus.DONE, (
            f"Expected DONE but got {result.status}: {result.outcome}\n"
            f"Actions issued: {self.sim.actions}"
        )

    def test_wolf_killed(self):
        """After the skill finishes, the wolf must be removed from world.units."""
        for _ in range(20):
            state = GameState.from_raw(self.sim.extract_state())
            res = self.skill.step(state)
            if res.status is SkillStatus.DONE:
                break
        assert all(u.id != 1001 for u in self.world.units), (
            "Wolf still alive after skill completed"
        )

    def test_outcome_mentions_wolf(self):
        """The DONE outcome string should mention the target name."""
        outcome = ""
        for _ in range(20):
            state = GameState.from_raw(self.sim.extract_state())
            res = self.skill.step(state)
            if res.status is SkillStatus.DONE:
                outcome = res.outcome
                break
        assert "wolf" in outcome.lower(), f"Unexpected outcome: {outcome!r}"

    def test_correct_action_sequence_issued(self):
        """The skill must issue exactly the documented attack-menu actions."""
        for _ in range(20):
            state = GameState.from_raw(self.sim.extract_state())
            res = self.skill.step(state)
            if res.status is SkillStatus.DONE:
                break
        assert self.sim.actions == [
            "press:A_ATTACK",
            "attack_pick:0",
            "attack_strike",
            "attack_pick:0",
            "attack_pick:0",
        ], f"Unexpected action sequence: {self.sim.actions}"


# ---------------------------------------------------------------------------
# Test 6 — component 3c: the adversary turn (_advance_world)
# ---------------------------------------------------------------------------

class TestAggravationOnStrike:
    """A wild creature that SURVIVES a strike turns hostile (DF: attacking provokes)."""

    def setup_method(self):
        # Wild wolf adjacent, hp survives a single strike (100 > _STRIKE_DAMAGE=50).
        wolf = SimUnit(
            id=1001, name="Wolf", race="WOLF", pos=(51, 50, 10),
            is_hostile=False, hist_fig_id=-1, hp=100,
        )
        self.world = SimWorld(pos=(50, 50, 10), units=[wolf])
        self.sim = SimulatedLuaExecutor(self.world)
        self.wolf = wolf

    def test_surviving_wolf_becomes_hostile(self):
        # Drive the menu to land one strike (50 dmg, wolf survives at 50 hp).
        for a in ("press:A_ATTACK", "attack_pick:0", "attack_strike",
                  "attack_pick:0", "attack_pick:0"):
            self.sim.execute_action(a)
        assert self.wolf.hp == 100 - _STRIKE_DAMAGE
        assert self.wolf.is_hostile is True


class TestAdversaryTurn:
    """Hostiles act on time-consuming player actions: approach, then attack."""

    def setup_method(self):
        # Hostile wolf three tiles east on the same z-level.
        wolf = SimUnit(
            id=2001, name="Wolf", race="WOLF", pos=(53, 50, 10),
            is_hostile=True, hist_fig_id=-1, hp=100,
        )
        self.world = SimWorld(pos=(50, 50, 10), units=[wolf])
        self.sim = SimulatedLuaExecutor(self.world)
        self.wolf = wolf

    def test_wolf_steps_closer_on_wait(self):
        self.sim.execute_action("A_MOVE_SAME_SQUARE")
        assert self.wolf.pos == (52, 50, 10)   # stepped one tile toward (50,50)

    def test_tick_advances_on_wait(self):
        before = self.world.tick_counter
        self.sim.execute_action("A_MOVE_SAME_SQUARE")
        assert self.world.tick_counter > before

    def test_adjacent_wolf_draws_blood(self):
        self.wolf.pos = (51, 50, 10)            # adjacent
        before = self.world.blood_count
        self.sim.execute_action("A_MOVE_SAME_SQUARE")
        assert self.world.blood_count == before - _WOLF_DAMAGE

    def test_wolf_on_other_zlevel_ignored(self):
        self.wolf.pos = (51, 50, 11)            # adjacent in xy but wrong z
        before = self.world.blood_count
        self.sim.execute_action("A_MOVE_SAME_SQUARE")
        assert self.world.blood_count == before
        assert self.wolf.pos == (51, 50, 11)    # did not move

    def test_adventurer_dies_when_blood_runs_out(self):
        self.wolf.pos = (51, 50, 10)            # adjacent
        self.world.blood_count = _WOLF_DAMAGE   # one hit is lethal
        self.sim.execute_action("A_MOVE_SAME_SQUARE")
        assert self.world.blood_count == 0
        assert self.world.adventurer_dead is True

    def test_dead_adventurer_stops_world(self):
        self.world.adventurer_dead = True
        before_tick = self.world.tick_counter
        before_pos = self.wolf.pos
        self.sim.execute_action("A_MOVE_SAME_SQUARE")
        assert self.world.tick_counter == before_tick
        assert self.wolf.pos == before_pos


class TestMenuNavigationIsFree:
    """Attack-menu navigation costs no time — the adversary does NOT act."""

    def setup_method(self):
        # Wild wolf adjacent (the intended target) plus a second hostile two tiles
        # away to prove the adversary stays frozen during menu nav.
        target = SimUnit(
            id=1001, name="Wolf", race="WOLF", pos=(51, 50, 10),
            is_hostile=False, hist_fig_id=-1, hp=100,
        )
        bystander = SimUnit(
            id=3001, name="Boar", race="BOAR", pos=(52, 50, 10),
            is_hostile=True, hist_fig_id=-1, hp=100,
        )
        self.world = SimWorld(pos=(50, 50, 10), units=[target, bystander])
        self.sim = SimulatedLuaExecutor(self.world)
        self.bystander = bystander

    def test_navigation_does_not_advance_time(self):
        before_tick = self.world.tick_counter
        before_blood = self.world.blood_count
        before_pos = self.bystander.pos
        # Open + navigate the menu WITHOUT resolving the strike.
        for a in ("press:A_ATTACK", "attack_pick:0", "attack_strike", "attack_pick:0"):
            self.sim.execute_action(a)
        assert self.world.tick_counter == before_tick
        assert self.world.blood_count == before_blood
        assert self.bystander.pos == before_pos        # hostile stayed put

    def test_strike_resolution_does_advance_time(self):
        before_tick = self.world.tick_counter
        for a in ("press:A_ATTACK", "attack_pick:0", "attack_strike",
                  "attack_pick:0", "attack_pick:0"):   # final pick resolves
            self.sim.execute_action(a)
        assert self.world.tick_counter > before_tick   # the resolving click cost time


class TestClosedCombatLoopWon:
    """The payoff: a real CombatStrikeSkill kills the wolf and the adventurer lives."""

    def setup_method(self):
        # Wolf needs two strikes to die (hp = 1.5× strike). First strike aggravates
        # it; it then retaliates each adversary turn — but the adventurer has plenty
        # of blood and wins the exchange.
        wolf = SimUnit(
            id=1001, name="Wolf", race="WOLF", pos=(51, 50, 10),
            is_hostile=False, hist_fig_id=-1, hp=_STRIKE_DAMAGE + 1,
        )
        self.world = SimWorld(pos=(50, 50, 10), units=[wolf])
        self.sim = SimulatedLuaExecutor(self.world)

    def _run_one_strike(self) -> None:
        """Drive a fresh CombatStrikeSkill to DONE (one full strike)."""
        ctx = SkillContext(self.sim, None, None, None)
        skill = CombatStrikeSkill(ctx, unit_id=1001, target_name="wolf")
        for _ in range(20):
            state = GameState.from_raw(self.sim.extract_state())
            res = skill.step(state)
            if res.status is SkillStatus.DONE:
                return
        raise AssertionError("CombatStrikeSkill did not finish")

    def test_adventurer_wins_the_fight(self):
        # Strike until the wolf is gone (re-issuing the skill per strike, as the
        # tactical loop would once the menu closes).
        for _ in range(5):
            if all(u.id != 1001 for u in self.world.units):
                break
            self._run_one_strike()
        assert all(u.id != 1001 for u in self.world.units), "wolf survived the fight"
        assert self.world.adventurer_dead is False
        assert self.world.blood_count > 0
