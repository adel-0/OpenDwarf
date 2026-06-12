"""Unit tests for conversation-choice enumeration: identity-trap filtering and
relabeling of internal adventure_option_* system choices (ROADMAP 3.1)."""

from opendwarf.actions.registry import _enumerate_conversation
from opendwarf.state.game_state import GameState, ConversationChoice


def _state(choices):
    s = GameState()
    s.conversation_phase = "select_npc"
    s.conversation_choices = [ConversationChoice(index=i, text=t)
                              for i, t in enumerate(choices)]
    return s


class TestIdentityTrapFiltering:
    def test_assume_identity_hidden(self):
        s = _state([
            'Zuso Sananocam "Loveteeth", Tavern Keeper',
            "adventure_option_talk_new_conversationst",
            "adventure_option_assume_identityst",
        ])
        out = _enumerate_conversation(s)
        actions = [a for a, _ in out]
        assert "conversation_2" not in actions  # assume_identity dropped
        assert "conversation_0" in actions       # named NPC kept
        assert "conversation_1" in actions       # talk_new kept

    def test_assume_identity_hidden_case_insensitive(self):
        s = _state(["adventure_option_ASSUME_IDENTITYst"])
        assert _enumerate_conversation(s) == []

    def test_named_npc_passes_through_unchanged(self):
        s = _state(['Zuso Sananocam "Loveteeth", Tavern Keeper'])
        out = _enumerate_conversation(s)
        assert out == [("conversation_0", 'Zuso Sananocam "Loveteeth", Tavern Keeper')]


class TestRelabel:
    def test_talk_new_relabeled_readable(self):
        s = _state(["adventure_option_talk_new_conversationst"])
        out = _enumerate_conversation(s)
        assert len(out) == 1
        action, label = out[0]
        assert action == "conversation_0"
        assert "adventure_option_" not in label
        assert "new conversation" in label.lower()

    def test_unknown_adventure_option_kept_with_raw_text(self):
        # An un-relabeled, non-hidden system option still surfaces (so it is never
        # silently swallowed) — just with its raw text.
        s = _state(["adventure_option_some_future_thingst"])
        out = _enumerate_conversation(s)
        assert len(out) == 1
        assert out[0][0] == "conversation_0"


class TestDialoguePhase:
    def test_indices_preserved_after_filtering(self):
        # Real dialogue choices keep their original DF indices even when an earlier
        # option is filtered, so conversation:<idx> dispatch stays correct.
        s = _state([
            "Bring up specific incident or rumor (new menu)",
            "adventure_option_assume_identityst",
            "Ask about the local ruler",
        ])
        out = _enumerate_conversation(s)
        actions = [a for a, _ in out]
        assert actions == ["conversation_0", "conversation_2"]

    def test_no_choices_returns_empty(self):
        s = _state([])
        assert _enumerate_conversation(s) == []
