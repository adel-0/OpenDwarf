"""Unit tests for NORTHSTAR M2: creature tiers, tier-based policy authorization,
and the GrindCombatBehavior state machine (seek/engage/recover/until)."""

from __future__ import annotations

from opendwarf.actions.skills import SkillContext
from opendwarf.agent.loop import TacticalLoop
from opendwarf.behaviors import interrupts as I
from opendwarf.behaviors.base import BehaviorStatus
from opendwarf.behaviors.grind_combat import GrindCombatBehavior
from opendwarf.behaviors.policy import Policy
from opendwarf.behaviors.tiers import DEFAULT_TIER, tier_of
from opendwarf.state.game_state import GameState, Position, Skill, UnitInfo


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------

class _FakeLua:
    def __init__(self):
        self.actions: list[str] = []

    def execute_action(self, key):
        self.actions.append(key)


class _FakeExtractor:
    """Absolute coords = local + a fixed region offset."""
    OFF = (1000, 1000, 0)
    has_offset = True

    def adventurer_abs(self, state):
        p = state.adventurer_position
        if p is None:
            return None
        return (self.OFF[0] + p.x, self.OFF[1] + p.y, p.z)

    def to_abs(self, x, y, z):
        return (self.OFF[0] + x, self.OFF[1] + y, z)

    def ensure_fresh(self, state):
        pass


class _FakePathfinder:
    def __init__(self, path=None):
        self.path = path

    def find_path(self, cur, goal, now_tick=0, partial=False):
        return list(self.path) if self.path else []

    def frontier_path(self, cur, direction, now_tick=0):
        return []


def _ctx(lua=None, path=None):
    return SkillContext(lua or _FakeLua(), None, _FakePathfinder(path), _FakeExtractor())


def _state(*, hostiles=None, skills=None, pos=(50, 50, 10)):
    s = GameState()
    s.adventurer_position = Position(*pos)
    for u in (hostiles or []):
        s.nearby_units.append(u)
        s.hostile_units.append(u)
    for sk in (skills or []):
        s.skills.append(sk)
    return s


def _h(uid, dx, dy, dz=0, race="GOBLIN"):
    p = Position(50 + dx, 50 + dy, 10 + dz)
    return UnitInfo(id=uid, name=race.title(), race=race, position=p,
                    is_hostile=True, distance=abs(dx) + abs(dy) + abs(dz))


def _wild(uid, dx, dy, dz=0, race="WOLF"):
    """A wild, non-hostile creature (DF leaves wildlife is_hostile=False until
    provoked). hist_fig_id<0 + not tame/citizen ⇒ huntable."""
    p = Position(50 + dx, 50 + dy, 10 + dz)
    return UnitInfo(id=uid, name=race.title(), race=race, position=p,
                    is_hostile=False, distance=abs(dx) + abs(dy) + abs(dz),
                    hist_fig_id=-1)


def _state_nearby(*, units, pos=(50, 50, 10)):
    """State with units in nearby_units only (hostiles also mirrored, as the
    real extractor does)."""
    s = GameState()
    s.adventurer_position = Position(*pos)
    for u in units:
        s.nearby_units.append(u)
        if u.is_hostile:
            s.hostile_units.append(u)
    return s


# ----------------------------------------------------------------------
# Tiers
# ----------------------------------------------------------------------

def test_tier_of_known_and_unknown():
    assert tier_of("KOBOLD") == 1
    assert tier_of("goblin") == 2          # case-insensitive
    assert tier_of("DRAGON") == 4
    assert tier_of("SOME_NEW_BEAST") == DEFAULT_TIER
    assert tier_of(None) == DEFAULT_TIER


# ----------------------------------------------------------------------
# Tier-based policy authorization (single source of truth = interrupts.check)
# ----------------------------------------------------------------------

def test_tier_max_authorizes_engagement():
    policy = Policy(engage_tier_max=2, max_opponents=1, min_health_pct=0)
    s = _state(hostiles=[_h(1, 3, 0, race="WOLF")])  # WOLF is tier 2
    s.blood_count = s.blood_max = 100
    assert I.check(s, policy, None) is None


def test_tier_max_rejects_higher_tier():
    policy = Policy(engage_tier_max=2, max_opponents=1, min_health_pct=0)
    s = _state(hostiles=[_h(1, 3, 0, race="DRAGON")])  # tier 4 > 2
    s.blood_count = s.blood_max = 100
    intr = I.check(s, policy, None)
    assert intr is not None and intr.reason is I.InterruptReason.HOSTILE_UNHANDLED


def test_species_allow_and_tier_combine():
    # OGRE is tier 3 (above tier_max 2) but explicitly allowed by species.
    policy = Policy(engage_species_allow=["OGRE"], engage_tier_max=2,
                    max_opponents=2, min_health_pct=0)
    s = _state(hostiles=[_h(1, 3, 0, race="OGRE"), _h(2, 4, 0, race="WOLF")])
    s.blood_count = s.blood_max = 100
    assert I.check(s, policy, None) is None


# ----------------------------------------------------------------------
# Policy round-trip with the new field
# ----------------------------------------------------------------------

def test_policy_tier_max_roundtrip_and_bounds():
    p = Policy()
    diff = p.revise({"engage_tier_max": 3})
    assert p.engage_tier_max == 3 and "engage_tier_max" in diff
    # out of range and wrong types rejected
    assert p.revise({"engage_tier_max": 9}) == {}
    assert p.revise({"engage_tier_max": True}) == {}
    assert Policy.from_dict(p.to_dict()).engage_tier_max == 3


def test_policy_prompt_line_mentions_tier():
    p = Policy(engage_species_allow=["WOLF"], engage_tier_max=2)
    assert "tier <= 2" in p.to_prompt_line()


# ----------------------------------------------------------------------
# GrindCombatBehavior — ENGAGE
# ----------------------------------------------------------------------

def test_engage_adjacent_sends_bump_attack():
    lua = _FakeLua()
    b = GrindCombatBehavior(_ctx(lua), Policy(engage_tier_max=2))
    s = _state(hostiles=[_h(7, -1, -1, race="GOBLIN")])  # NW neighbour
    res = b.step(s)
    assert res.status is BehaviorStatus.RUNNING
    assert lua.actions == ["A_MOVE_NW"]          # bump-to-attack into the tile
    assert any("struck" in e for e in b.digest._order)


def test_engage_nonadjacent_steps_toward():
    lua = _FakeLua()
    # path: from adventurer abs (1050,1050,10) one tile east toward the goblin
    b = GrindCombatBehavior(
        _ctx(lua, path=[(1050, 1050, 10), (1051, 1050, 10)]), Policy(engage_tier_max=2))
    s = _state(hostiles=[_h(7, 5, 0, race="GOBLIN")])  # 5 tiles east, not adjacent
    res = b.step(s)
    assert res.status is BehaviorStatus.RUNNING
    assert lua.actions == ["A_MOVE_E"]           # closed distance, did not attack


def test_engage_nonadjacent_no_path_falls_back_to_straight_step():
    lua = _FakeLua()
    b = GrindCombatBehavior(_ctx(lua, path=[]), Policy(engage_tier_max=2))
    s = _state(hostiles=[_h(7, 0, 5, race="GOBLIN")])  # due south, no path
    res = b.step(s)
    assert res.status is BehaviorStatus.RUNNING
    assert lua.actions == ["A_MOVE_S"]           # straight-line fallback toward target


def test_engage_wild_target_drives_attack_menu():
    # A wild wolf (never flagged isDanger) is huntable. The grind closes distance
    # autonomously, and when adjacent it drives the attack menu (CombatStrikeSkill)
    # rather than bump (which would no-op on a neutral) or handing back to the LLM.
    lua = _FakeLua()
    b = GrindCombatBehavior(_ctx(lua), Policy(engage_tier_max=2))
    s = _state_nearby(units=[_wild(7, -1, 0, race="WOLF")])  # adjacent W
    assert s.hostile_units == []                  # danger semantics untouched
    res = b.step(s)
    assert res.status is BehaviorStatus.RUNNING
    assert b._strike is not None                   # in-flight attack-menu strike
    assert lua.actions == ["press:A_ATTACK"]       # opened the attack menu, no bump
    assert not any("struck" in e for e in b.digest._order)  # no false strike yet


def test_engage_skips_same_tile_target():
    # A creature reported on the adventurer's exact tile (seen live: a wolf at
    # distance 0) is neither steppable nor bump-attackable; pick the next one.
    lua = _FakeLua()
    b = GrindCombatBehavior(_ctx(lua), Policy(engage_tier_max=2))
    s = _state_nearby(units=[_h(1, 0, 0, race="GOBLIN"),    # same tile — skip
                             _h(2, -1, -1, race="GOBLIN")])  # NW — engage this
    res = b.step(s)
    assert res.status is BehaviorStatus.RUNNING
    assert lua.actions == ["A_MOVE_NW"]


# ----------------------------------------------------------------------
# Progress tracking: skill level-ups, kills, until predicate
# ----------------------------------------------------------------------

def test_skill_levelups_recorded_in_digest():
    b = GrindCombatBehavior(_ctx(), Policy())
    b._record_skill_levels(_state(skills=[Skill("AXE", 5)]))   # first sighting: no digest
    assert b.digest.is_empty
    b._record_skill_levels(_state(skills=[Skill("AXE", 7)]))   # +2
    assert any("+2 AXE" in e for e in b.digest._order)


def test_until_skill_level_reached():
    b = GrindCombatBehavior(_ctx(), Policy(), until={"AXE": 8})
    b._record_skill_levels(_state(skills=[Skill("AXE", 8)]))
    s = _state(skills=[Skill("AXE", 8)])
    res = b.step(s)
    assert res.status is BehaviorStatus.DONE and "AXE" in res.outcome


def test_until_max_ticks_reached():
    b = GrindCombatBehavior(_ctx(), Policy(), until={"max_ticks": 300})
    s1 = _state()
    s1.tick_counter = 1000
    b.step(s1)  # start_tick = 1000
    s2 = _state()
    s2.tick_counter = 1400  # 400 ticks elapsed >= 300
    res = b.step(s2)
    assert res.status is BehaviorStatus.DONE and "budget" in res.outcome


def test_kills_counted_when_hostile_disappears():
    b = GrindCombatBehavior(_ctx(_FakeLua()), Policy(engage_tier_max=2))
    b.step(_state(hostiles=[_h(7, -1, 0, race="GOBLIN")]))  # engage → records engaged id
    b.step(_state())                                        # hostile gone → kill noted
    assert any("defeated enemy" in e for e in b.digest._order)
    assert b._kills == 1


# ----------------------------------------------------------------------
# Intent parsing
# ----------------------------------------------------------------------

def test_parse_grind_args():
    assert TacticalLoop._parse_grind_args("grind_combat") == (12, {})
    assert TacticalLoop._parse_grind_args("grind_combat:20") == (20, {})
    assert TacticalLoop._parse_grind_args("grind_combat:16:AXE:8") == (16, {"AXE": 8})
    assert TacticalLoop._parse_grind_args("grind_combat:16:max_ticks:5000") == (16, {"max_ticks": 5000})
    # malformed radius / level fall back gracefully
    assert TacticalLoop._parse_grind_args("grind_combat:foo") == (12, {})
    assert TacticalLoop._parse_grind_args("grind_combat:3") == (4, {})  # clamped to min 4
