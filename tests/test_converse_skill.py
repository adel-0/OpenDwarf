"""Unit tests for ConverseSkill (ROADMAP 3.1 tail): the deterministic
multi-turn conversation sweep. Uses a real AskedTopics store and a real
_ConversationTracker so dedup + transcript bookkeeping are exercised end-to-end.
"""

from __future__ import annotations

from _fakes import FakeExtractor, FakePathfinder, SimulatedDF
from opendwarf.actions.skills import ConverseSkill, SkillContext, SkillStatus
from opendwarf.agent.loop import _ConversationTracker
from opendwarf.memory.asked_topics import AskedTopics
from opendwarf.state.game_state import (
    ConversationChoice,
    GameState,
    Position,
    UnitInfo,
)


# ----------------------------------------------------------------------
# Fakes — shared doubles from tests/_fakes.py. Conversation tests never set a
# position, so the identity-offset extractor's adventurer_abs returns None
# (the routing branch stays inert, as before).
# ----------------------------------------------------------------------

def _ctx(tmp_path, lua=None):
    asked = AskedTopics(tmp_path / "asked.json")
    tracker = _ConversationTracker()
    ctx = SkillContext(lua or SimulatedDF(), None, FakePathfinder(), FakeExtractor(),
                       asked_topics=asked, conv_tracker=tracker)
    return ctx, asked, tracker


def _dialogue(choices, tick=100):
    s = GameState()
    s.conversation_phase = "dialogue"
    s.conversation_choices = [ConversationChoice(index=i, text=t) for i, t in enumerate(choices)]
    s.tick_counter = tick
    return s


def _none_state(tick=100):
    s = GameState()
    s.conversation_phase = "none"
    s.tick_counter = tick
    return s


def _adjacent_state(uid=7, name="Urist", hf_id=42, tick=100):
    s = GameState()
    s.tick_counter = tick
    s.nearby_units.append(UnitInfo(
        id=uid, name=name, race="dwarf",
        position=Position(1, 1, 0), is_hostile=False, distance=1, hist_fig_id=hf_id))
    return s


def _skill(ctx, *, uid=7, name="Urist", hf_id=42, max_topics=None):
    return ConverseSkill(ctx, unit_id=uid, npc_name=name, npc_hf_id=hf_id, max_topics=max_topics)


# ----------------------------------------------------------------------
# 1. Highest-priority new topic chosen
# ----------------------------------------------------------------------

def test_pick_selects_highest_priority_new_topic(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx)
    sk._phase = "pick"
    st = _dialogue(["Tell me about yourself", "Ask about troubles"])
    res = sk.step(st)
    assert res.status is SkillStatus.RUNNING
    # "Ask about troubles" (HIGH) over "Tell me about yourself" (LOW)
    assert ctx.lua.actions == ["conversation:1"]
    assert asked.was_asked(sk._key(), "Ask about troubles")
    # tracker accumulated the chosen text
    assert tracker.has_content
    assert "troubles" in tracker.format_for_prompt().lower()


def test_pick_already_asked_topic_skipped(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx)
    # Pre-record the high-tier topic so it is skipped.
    asked.record(sk._key(), "Ask about troubles", 1)
    sk._phase = "pick"
    st = _dialogue(["Ask about troubles", "Ask about the local ruler", "Tell me about yourself"])
    res = sk.step(st)
    assert res.status is SkillStatus.RUNNING
    # Next-best unasked: "the local ruler" is MED (2) > "yourself" LOW (1)
    assert ctx.lua.actions == ["conversation:1"]


def test_choose_topic_skips_emotes_and_accusations(tmp_path):
    # Role-play emotes / accusations are not info-gathering; the sweep skips them
    # even when an avoid-keyword overlaps a HIGH keyword (e.g. "night creature").
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx)
    s = _dialogue([
        "Accuse listener of being a night creature",
        "State feelings of great exasperation when caught in a snow storm",
        "Ask about any troubles in the area",
    ])
    choice = sk._choose_topic(s)
    assert choice is not None
    assert "troubles" in choice.text.lower()


def test_choose_topic_skips_submenu_openers(tmp_path):
    # v1 never enters submenus; a "(… menu)" opener (incl. site-claiming) is
    # skipped even if it isn't caught by AskedTopics.is_topic's "(new menu)".
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx)
    s = _dialogue([
        "Claim this site for yourself (group naming menu)",
        "Ask about the local ruler",
    ])
    choice = sk._choose_topic(s)
    assert choice is not None
    assert "ruler" in choice.text.lower()


def test_choose_topic_none_when_only_emotes(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx)
    s = _dialogue([
        "Accuse listener of being a night creature",
        "Demand that they yield",
    ])
    assert sk._choose_topic(s) is None


def test_only_meta_choices_finishes_done_and_says_goodbye(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx)
    sk._phase = "pick"
    st = _dialogue(["Change the subject", "Ask for directions (new menu)", "Say goodbye"])
    res = sk.step(st)
    assert res.status is SkillStatus.DONE
    # _choose_topic returned None -> _leave clicked goodbye (index 2)
    assert "conversation:2" in ctx.lua.actions


def test_budget_cap_stops_after_max_topics(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, max_topics=2)
    sk._phase = "pick"
    sk._asked_count = 2  # already hit the budget
    st = _dialogue(["Ask about troubles", "Ask about the war"])
    res = sk.step(st)
    assert res.status is SkillStatus.DONE
    # No topic pick was sent (only possibly a goodbye, but none present here)
    assert not any(a == "conversation:0" or a == "conversation:1" for a in ctx.lua.actions)


def test_budget_cap_full_sweep(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, max_topics=2)
    sk._phase = "pick"
    choices = ["Ask about troubles", "Ask about the war", "Ask about the ruler"]
    # First pick
    sk.step(_dialogue(choices))
    assert sk._asked_count == 1
    # Simulate DF re-presenting menu -> response sees dialogue -> back to pick
    sk._phase = "response"
    sk.step(_dialogue(choices))  # response -> pick -> picks again
    assert sk._asked_count == 2
    # Third attempt should DONE on budget
    sk._phase = "response"
    res = sk.step(_dialogue(choices))
    assert res.status is SkillStatus.DONE


def test_response_none_reengages_to_open_and_talks(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, max_topics=4)
    sk._phase = "response"
    sk._asked_count = 1  # below budget
    res = sk.step(_none_state())
    assert res.status is SkillStatus.RUNNING
    assert sk._phase == "open"
    # Next step from open sends A_TALK again
    res2 = sk.step(_none_state())
    assert "A_TALK" in ctx.lua.actions
    assert sk._phase == "await"


def test_response_dialogue_returns_to_pick(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, max_topics=4)
    sk._phase = "response"
    sk._asked_count = 1
    st = _dialogue(["Ask about troubles"])
    res = sk.step(st)
    # response sees dialogue -> pick -> picks the topic (RUNNING)
    assert res.status is SkillStatus.RUNNING
    assert ctx.lua.actions == ["conversation:0"]


def test_response_none_at_budget_finishes(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, max_topics=2)
    sk._phase = "response"
    sk._asked_count = 2
    res = sk.step(_none_state())
    assert res.status is SkillStatus.DONE


def test_hostile_units_interrupt(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx)
    sk._phase = "pick"
    st = _dialogue(["Ask about troubles"])
    foe = UnitInfo(id=99, name="goblin", race="goblin",
                   position=Position(2, 2, 0), is_hostile=True, distance=2, hist_fig_id=-1)
    st.nearby_units.append(foe)
    st.hostile_units.append(foe)
    res = sk.step(st)
    assert res.status is SkillStatus.INTERRUPTED
    assert "hostile" in res.outcome.lower()


def test_route_npc_gone_interrupts(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, uid=7)
    sk._phase = "route"
    st = GameState()  # no nearby units
    res = sk.step(st)
    assert res.status is SkillStatus.INTERRUPTED
    assert "no longer visible" in res.outcome.lower()


def test_route_adjacent_advances_to_open_and_talks(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, uid=7, name="Urist", hf_id=42)
    sk._phase = "route"
    res = sk.step(_adjacent_state(uid=7, name="Urist", hf_id=42))
    # route -> open (immediate) -> sends A_TALK, tracker.start called
    assert res.status is SkillStatus.RUNNING
    assert "A_TALK" in ctx.lua.actions
    assert tracker.active
    assert tracker.npc_name == "Urist"


def test_score_tiers(tmp_path):
    # HIGH=3, MED=2, plain=1, including normalize-prefix stripping.
    assert ConverseSkill._score("Ask about troubles") == 3
    assert ConverseSkill._score("Ask about the local ruler") == 2
    assert ConverseSkill._score("Tell me about yourself") == 1
    assert ConverseSkill._score("Ask about recent news") == 3
    assert ConverseSkill._score("Ask about your family") == 2


def test_await_dialogue_advances_and_picks(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx)
    sk._phase = "await"
    st = _dialogue(["Ask about troubles"])
    res = sk.step(st)
    assert res.status is SkillStatus.RUNNING
    # await -> pick -> picked the topic
    assert ctx.lua.actions == ["conversation:0"]
    assert sk._phase == "response"


def test_await_select_npc_named_choice_picked(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, name="Urist")
    sk._phase = "await"
    s = GameState()
    s.conversation_phase = "select_npc"
    s.conversation_choices = [
        ConversationChoice(index=0, text="adventure_option_shout"),
        ConversationChoice(index=1, text="Urist"),
    ]
    res = sk.step(s)
    assert res.status is SkillStatus.RUNNING
    assert ctx.lua.actions == ["conversation:1"]


def test_await_select_npc_address_nearest_when_not_listed(tmp_path):
    # Live-verified shape: A_TALK lists ONE other named NPC (the tavern keeper)
    # plus talk_new ("address nearest") + assume_identity. Our target is not
    # named, so the skill must pick talk_new (we routed adjacent → nearest = us),
    # NEVER assume_identity.
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, name='Ricgo "Stylessupper", Crossbowman')
    sk._phase = "await"
    s = GameState()
    s.conversation_phase = "select_npc"
    s.conversation_choices = [
        ConversationChoice(index=0, text='Zuso "Loveteeth", Tavern Keeper'),
        ConversationChoice(index=1, text="adventure_option_talk_new_conversationst"),
        ConversationChoice(index=2, text="adventure_option_assume_identityst"),
    ]
    res = sk.step(s)
    assert res.status is SkillStatus.RUNNING
    assert ctx.lua.actions == ["conversation:1"]
    assert sk._npc_selected is True


def test_select_npc_talk_existing_on_reengage(tmp_path):
    # Live-verified: re-engaging an already-talked NPC offers
    # talk_existing_conversationst instead of talk_new — both are the
    # address-nearest path and must be selected.
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, name="Ricgo")
    s = GameState()
    s.conversation_phase = "select_npc"
    s.conversation_choices = [
        ConversationChoice(index=0, text='Zuso "Loveteeth", Tavern Keeper'),
        ConversationChoice(index=1, text="adventure_option_talk_existing_conversationst"),
        ConversationChoice(index=2, text="adventure_option_assume_identityst"),
    ]
    assert sk._select_npc_choice(s) == 1


def test_await_select_npc_picks_only_once(tmp_path):
    # After issuing the select, the skill must not re-fire it every tick while
    # waiting for the dialogue transition.
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, name="Urist")
    sk._phase = "await"
    s = GameState()
    s.conversation_phase = "select_npc"
    s.conversation_choices = [
        ConversationChoice(index=0, text="adventure_option_talk_new_conversationst"),
    ]
    sk.step(s)
    sk.step(s)  # still select_npc, already selected
    assert ctx.lua.actions == ["conversation:0"]  # exactly one select


def test_select_npc_never_assume_identity(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, name="Nobody")
    s = GameState()
    s.conversation_phase = "select_npc"
    s.conversation_choices = [
        ConversationChoice(index=0, text="adventure_option_assume_identityst"),
    ]
    assert sk._select_npc_choice(s) is None


def test_await_timeout_no_topics_interrupts(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx)
    sk._phase = "await"
    sk._wait = ConverseSkill._OPEN_WAIT  # one more tick exceeds
    sk._asked_count = 0
    res = sk.step(_none_state())
    assert res.status is SkillStatus.INTERRUPTED
    assert "did not start" in res.outcome.lower()


def test_await_timeout_with_topics_finishes(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx)
    sk._phase = "await"
    sk._wait = ConverseSkill._OPEN_WAIT
    sk._asked_count = 2
    res = sk.step(_none_state())
    assert res.status is SkillStatus.DONE
    assert "2 topics" in res.outcome


def test_key_scheme_name_when_hf_negative(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, name="Stranger", hf_id=-1)
    assert sk._key() == "name:Stranger"
    sk._phase = "pick"
    sk.step(_dialogue(["Ask about troubles"]))
    assert asked.was_asked("name:Stranger", "Ask about troubles")


def test_key_scheme_hf_id_when_positive(tmp_path):
    ctx, asked, tracker = _ctx(tmp_path)
    sk = _skill(ctx, name="Urist", hf_id=42)
    assert sk._key() == "42"
    sk._phase = "pick"
    sk.step(_dialogue(["Ask about troubles"]))
    assert asked.was_asked("42", "Ask about troubles")
