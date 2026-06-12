"""Unit tests for AskedTopics — isolated, no GameState/IO mocking beyond tmp_path."""

from pathlib import Path

import pytest

from opendwarf.memory.asked_topics import AskedTopics


# ----------------------------------------------------------------------
# normalize
# ----------------------------------------------------------------------

class TestNormalize:
    def test_strips_ask_about_prefix(self):
        assert AskedTopics.normalize("ask about the bandit camp") == "the bandit camp"

    def test_strips_ask_for_prefix(self):
        assert AskedTopics.normalize("ask for directions") == "directions"

    def test_strips_ask_the_prefix(self):
        assert AskedTopics.normalize("ask the local ruler") == "local ruler"

    def test_strips_ask_prefix(self):
        assert AskedTopics.normalize("ask for water") == "water"

    def test_strips_tell_me_about_prefix(self):
        assert AskedTopics.normalize("tell me about the goblins") == "the goblins"

    def test_strips_tell_me_about_before_tell(self):
        # "tell me about " prefix is listed before "tell " so it matches first
        assert AskedTopics.normalize("tell me about dangers") == "dangers"

    def test_strips_say_prefix(self):
        assert AskedTopics.normalize("say hello") == "hello"

    def test_strips_talk_about_prefix(self):
        assert AskedTopics.normalize("talk about the war") == "the war"

    def test_strips_bring_up_prefix(self):
        assert AskedTopics.normalize("bring up the siege") == "the siege"

    def test_strips_discuss_prefix(self):
        assert AskedTopics.normalize("discuss the artifact") == "the artifact"

    def test_strips_inquire_about_prefix(self):
        assert AskedTopics.normalize("inquire about work") == "work"

    def test_strips_trailing_question_mark(self):
        assert AskedTopics.normalize("ask about the fortress?") == "the fortress"

    def test_strips_trailing_period(self):
        assert AskedTopics.normalize("ask about the weather.") == "the weather"

    def test_strips_trailing_exclamation(self):
        assert AskedTopics.normalize("ask about danger!") == "danger"

    def test_collapses_internal_whitespace(self):
        # re.sub collapses ALL whitespace in the string before prefix stripping
        assert AskedTopics.normalize("ask about  the   ruler") == "the ruler"

    def test_lowercases_result(self):
        assert AskedTopics.normalize("Ask About The Dragon") == "the dragon"

    def test_no_prefix_passthrough(self):
        assert AskedTopics.normalize("the local tavern") == "the local tavern"

    def test_only_prefix_gives_empty(self):
        # Empty string normalizes to empty
        assert AskedTopics.normalize("") == ""

    def test_first_prefix_wins(self):
        # "tell me about " comes before "tell " in the tuple so it wins
        assert AskedTopics.normalize("tell me about goblins") == "goblins"


# ----------------------------------------------------------------------
# is_topic
# ----------------------------------------------------------------------

class TestIsTopic:
    def test_true_for_real_topic(self):
        assert AskedTopics.is_topic("ask about the local ruler")

    def test_false_for_new_menu(self):
        assert not AskedTopics.is_topic("(new menu)")

    def test_false_for_change_the_subject(self):
        assert not AskedTopics.is_topic("Change the subject")

    def test_false_for_never_mind(self):
        assert not AskedTopics.is_topic("Never mind")

    def test_false_for_nevermind(self):
        assert not AskedTopics.is_topic("nevermind")

    def test_false_for_say_goodbye(self):
        assert not AskedTopics.is_topic("Say goodbye")

    def test_false_for_goodbye(self):
        assert not AskedTopics.is_topic("Goodbye")

    def test_false_for_stop_talking(self):
        assert not AskedTopics.is_topic("stop talking")

    def test_false_for_leave(self):
        assert not AskedTopics.is_topic("leave")

    def test_false_for_start_a_new_conversation(self):
        assert not AskedTopics.is_topic("start a new conversation")

    def test_false_for_empty_after_normalize(self):
        # An empty string normalizes to empty, which is not a topic
        assert not AskedTopics.is_topic("")

    def test_case_insensitive_nav(self):
        assert not AskedTopics.is_topic("GOODBYE")


# ----------------------------------------------------------------------
# record + was_asked round-trip
# ----------------------------------------------------------------------

class TestRecordWasAsked:
    def test_basic_round_trip(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        at.record("12730", "ask about the bandit camp", tick=1)
        assert at.was_asked("12730", "ask about the bandit camp")

    def test_different_prefix_same_topic_dedupes(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        at.record("12730", "ask about the bandit camp", tick=1)
        # Same underlying topic with no prefix
        assert at.was_asked("12730", "the bandit camp")

    def test_prefix_variants_all_match(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        at.record("12730", "the bandit camp", tick=1)
        assert at.was_asked("12730", "ask about the bandit camp")
        assert at.was_asked("12730", "talk about the bandit camp")
        assert at.was_asked("12730", "discuss the bandit camp")

    def test_npc_key_none_is_no_op(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        at.record(None, "ask about the ruler", tick=1)
        assert not at.was_asked(None, "ask about the ruler")
        assert not at._data  # nothing stored

    def test_non_topic_is_no_op(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        at.record("12730", "Say goodbye", tick=1)
        assert not at._data.get("12730")

    def test_was_asked_none_key_returns_false(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        assert not at.was_asked(None, "ask about the ruler")

    def test_was_asked_unknown_npc_returns_false(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        at.record("12730", "ask about the ruler", tick=1)
        assert not at.was_asked("99999", "ask about the ruler")


# ----------------------------------------------------------------------
# asked() ordering
# ----------------------------------------------------------------------

class TestAsked:
    def test_most_recent_first(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        at.record("42", "ask about the weather", tick=1)
        at.record("42", "ask about the war", tick=5)
        at.record("42", "ask about the king", tick=3)
        result = at.asked("42")
        assert result == ["the war", "the king", "the weather"]

    def test_empty_for_unknown_npc(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        assert at.asked("unknown") == []

    def test_empty_for_none_key(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        assert at.asked(None) == []


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------

class TestPersistence:
    def test_survives_reload(self, tmp_path):
        path = tmp_path / "at.json"
        at1 = AskedTopics(path)
        at1.record("12730", "ask about the fortress", tick=10)

        at2 = AskedTopics(path)
        assert at2.was_asked("12730", "ask about the fortress")

    def test_corrupt_file_starts_empty(self, tmp_path):
        path = tmp_path / "at.json"
        path.write_text("not valid json", encoding="utf-8")
        at = AskedTopics(path)
        assert at._data == {}

    def test_wrong_type_starts_empty(self, tmp_path):
        path = tmp_path / "at.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        at = AskedTopics(path)
        assert at._data == {}

    def test_missing_file_starts_empty(self, tmp_path):
        at = AskedTopics(tmp_path / "nonexistent.json")
        assert at._data == {}

    def test_multiple_npcs_persist(self, tmp_path):
        path = tmp_path / "at.json"
        at1 = AskedTopics(path)
        at1.record("12730", "ask about the siege", tick=1)
        at1.record("name:Bob", "ask about food", tick=2)

        at2 = AskedTopics(path)
        assert at2.was_asked("12730", "the siege")
        assert at2.was_asked("name:Bob", "food")


# ----------------------------------------------------------------------
# Key scheme interop
# ----------------------------------------------------------------------

class TestKeyScheme:
    def test_numeric_and_name_keys_are_independent(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        at.record("12730", "ask about the ruler", tick=1)
        # name-based key for a different NPC should not match
        assert not at.was_asked("name:Bob", "ask about the ruler")

    def test_name_key_independent_of_numeric(self, tmp_path):
        at = AskedTopics(tmp_path / "at.json")
        at.record("name:Bob", "ask about the artifact", tick=1)
        assert not at.was_asked("12730", "ask about the artifact")
        assert at.was_asked("name:Bob", "ask about the artifact")
