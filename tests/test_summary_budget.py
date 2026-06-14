"""Tests for situational, bounded GameState.summary() (ROADMAP 6.1).

summary() must (a) pick its heavy blocks by context mode — combat vs
conversation vs exploration — and (b) cap every list block so a large site
registry or a 98-item "Ask for directions" menu can't blow the token budget."""

from __future__ import annotations

from opendwarf.state import game_state as gs
from opendwarf.state.game_state import (
    ConversationChoice,
    EntityLink,
    GameState,
    NearbySite,
    Position,
    UnitInfo,
)


def _base() -> GameState:
    s = GameState()
    s.adventurer_name = "Olo"
    s.adventurer_position = Position(50, 50, 10)
    s.site_name = "LEAP TEMPLE"
    s.site_type = "Town"
    return s


def _hostile(uid: int) -> UnitInfo:
    return UnitInfo(id=uid, name="Wolf", race="WOLF", position=Position(51, 50, 10),
                    is_hostile=True, distance=1)


def _friendly(uid: int) -> UnitInfo:
    return UnitInfo(id=uid, name=f"Bystander{uid}", race="HUMAN",
                    position=Position(52, 50, 10), is_hostile=False, distance=2)


# --- mode detection -------------------------------------------------------

def test_mode_combat_when_hostiles():
    s = _base()
    s.hostile_units.append(_hostile(1))
    assert s._mode() == "combat"


def test_mode_conversation_when_choices():
    s = _base()
    s.conversation_choices.append(ConversationChoice(0, "Ask about news"))
    assert s._mode() == "conversation"


def test_mode_exploration_default():
    assert _base()._mode() == "exploration"


def test_combat_outranks_conversation():
    s = _base()
    s.hostile_units.append(_hostile(1))
    s.conversation_choices.append(ConversationChoice(0, "Yield?"))
    assert s._mode() == "combat"


# --- situational suppression ---------------------------------------------

def test_sites_and_factions_hidden_in_combat():
    s = _base()
    s.hostile_units.append(_hostile(1))
    s.nearby_sites.append(NearbySite(1, "TOME MOUTH", "Town", 17, "S"))
    s.adventurer_entities.append(EntityLink("ROOMY UNION", "MEMBER"))
    s.nearby_units.append(_friendly(2))
    out = s.summary()
    assert "Nearby Sites" not in out
    assert "Factions" not in out
    assert "Bystander" not in out          # friendly bystanders suppressed mid-fight
    assert "Hostile Units" in out


def test_sites_hidden_in_conversation():
    s = _base()
    s.conversation_choices.append(ConversationChoice(0, "Ask about news"))
    s.nearby_sites.append(NearbySite(1, "TOME MOUTH", "Town", 17, "S"))
    out = s.summary()
    assert "Nearby Sites" not in out
    assert "Conversation" in out


def test_sites_and_factions_shown_in_exploration():
    s = _base()
    s.nearby_sites.append(NearbySite(1, "TOME MOUTH", "Town", 17, "S"))
    s.adventurer_entities.append(EntityLink("ROOMY UNION", "MEMBER"))
    out = s.summary()
    assert "Nearby Sites" in out
    assert "TOME MOUTH" in out
    assert "Factions" in out


# --- bounding -------------------------------------------------------------

def test_conversation_choices_capped():
    s = _base()
    s.conversation_phase = "dialogue"
    for i in range(98):  # the real "Ask for directions" list size
        s.conversation_choices.append(ConversationChoice(i, f"directions to site {i}"))
    out = s.summary()
    # Only _CAP_CHOICES rows render, plus a truncation tail.
    rendered = [ln for ln in out.splitlines() if ln.strip().startswith("[")]
    assert len(rendered) == gs._CAP_CHOICES
    assert f"(… {98 - gs._CAP_CHOICES} more" in out


def test_nearby_sites_capped():
    s = _base()
    for i in range(20):
        s.nearby_sites.append(NearbySite(i, f"SITE {i}", "Town", 10 + i, "S"))
    out = s.summary()
    assert f"(… {20 - gs._CAP_SITES} more)" in out


def test_no_truncation_tail_when_under_cap():
    s = _base()
    s.nearby_sites.append(NearbySite(1, "TOME MOUTH", "Town", 17, "S"))
    assert "more)" not in s.summary()
