"""Unit tests for _ConversationGuard — isolated, no GameState/IO."""

from opendwarf.agent.loop import _ConversationGuard, _NPC_EXHAUST_COOLDOWN, _NPC_TALK_LIMIT


class TestKey:
    def test_historic_id_positive(self):
        assert _ConversationGuard.key(12730, "Bob") == "12730"

    def test_negative_id_falls_back_to_name(self):
        assert _ConversationGuard.key(-1, "Bob") == "name:Bob"

    def test_none_id_falls_back_to_name(self):
        assert _ConversationGuard.key(None, "Bob") == "name:Bob"

    def test_both_none_returns_none(self):
        assert _ConversationGuard.key(None, None) is None

    def test_zero_id_is_valid(self):
        assert _ConversationGuard.key(0, "Bob") == "0"


class TestReEngagementBans:
    def test_not_exhausted_below_limit(self):
        g = _ConversationGuard()
        key = "12730"
        for t in range(_NPC_TALK_LIMIT - 1):
            g.note_conversation(key, t)
        assert not g.is_exhausted(key, _NPC_TALK_LIMIT - 1)

    def test_exhausted_at_limit(self):
        g = _ConversationGuard()
        key = "12730"
        for t in range(_NPC_TALK_LIMIT):
            g.note_conversation(key, t)
        assert g.is_exhausted(key, _NPC_TALK_LIMIT)

    def test_stays_exhausted_within_cooldown(self):
        g = _ConversationGuard()
        key = "12730"
        exhaust_turn = 10
        for i in range(_NPC_TALK_LIMIT):
            g.note_conversation(key, exhaust_turn + i)
        # One turn before cooldown expires
        assert g.is_exhausted(key, exhaust_turn + _NPC_EXHAUST_COOLDOWN - 1)

    def test_expires_after_cooldown(self):
        g = _ConversationGuard()
        key = "12730"
        exhaust_turn = 10
        for i in range(_NPC_TALK_LIMIT):
            g.note_conversation(key, exhaust_turn)
        assert not g.is_exhausted(key, exhaust_turn + _NPC_EXHAUST_COOLDOWN)

    def test_entry_purged_after_expiry(self):
        g = _ConversationGuard()
        key = "12730"
        exhaust_turn = 10
        for i in range(_NPC_TALK_LIMIT):
            g.note_conversation(key, exhaust_turn)
        # Trigger expiry
        g.is_exhausted(key, exhaust_turn + _NPC_EXHAUST_COOLDOWN)
        assert key not in g._exhausted


class TestNoteOtherAction:
    def test_resets_streak_so_not_exhausted(self):
        g = _ConversationGuard()
        key = "99"
        for t in range(_NPC_TALK_LIMIT - 1):
            g.note_conversation(key, t)
        g.note_other_action()
        # 4 more conversations after reset — total streak since reset is 4, never hits 5
        for t in range(_NPC_TALK_LIMIT - 1):
            g.note_conversation(key, _NPC_TALK_LIMIT + t)
        assert not g.is_exhausted(key, _NPC_TALK_LIMIT * 2)

    def test_streak_cleared(self):
        g = _ConversationGuard()
        key = "99"
        for t in range(3):
            g.note_conversation(key, t)
        g.note_other_action()
        assert g.streak_for(key) == 0


class TestNoteProductive:
    def test_resets_streak_so_not_exhausted(self):
        g = _ConversationGuard()
        key = "77"
        for t in range(_NPC_TALK_LIMIT - 1):
            g.note_conversation(key, t)
        g.note_productive()
        # 4 more — still never hit 5 consecutively
        for t in range(_NPC_TALK_LIMIT - 1):
            g.note_conversation(key, _NPC_TALK_LIMIT + t)
        assert not g.is_exhausted(key, _NPC_TALK_LIMIT * 2)

    def test_does_not_clear_exhausted_mark(self):
        g = _ConversationGuard()
        key = "77"
        exhaust_turn = 5
        for i in range(_NPC_TALK_LIMIT):
            g.note_conversation(key, exhaust_turn)
        assert g.is_exhausted(key, exhaust_turn)
        g.note_productive()
        # Should still be exhausted within the cooldown window
        assert g.is_exhausted(key, exhaust_turn + 1)


class TestSwitchingTarget:
    def test_switching_target_resets_streak(self):
        g = _ConversationGuard()
        key_a = "A"
        key_b = "B"
        for t in range(_NPC_TALK_LIMIT - 1):
            g.note_conversation(key_a, t)
        # Switch to B — A's streak is abandoned
        g.note_conversation(key_b, _NPC_TALK_LIMIT)
        assert not g.is_exhausted(key_a, _NPC_TALK_LIMIT)
        assert not g.is_exhausted(key_b, _NPC_TALK_LIMIT)

    def test_streak_attributed_to_new_target(self):
        g = _ConversationGuard()
        g.note_conversation("A", 0)
        g.note_conversation("B", 1)
        assert g.streak_for("B") == 1
        assert g.streak_for("A") == 0


class TestCooldownExpiry:
    def test_expiry_at_exact_cooldown_boundary(self):
        g = _ConversationGuard()
        key = "555"
        exhaust_turn = 10
        for i in range(_NPC_TALK_LIMIT):
            g.note_conversation(key, exhaust_turn)
        # Exactly at cooldown — should expire
        assert not g.is_exhausted(key, exhaust_turn + _NPC_EXHAUST_COOLDOWN)

    def test_still_exhausted_one_before_boundary(self):
        g = _ConversationGuard()
        key = "555"
        exhaust_turn = 10
        for i in range(_NPC_TALK_LIMIT):
            g.note_conversation(key, exhaust_turn)
        assert g.is_exhausted(key, exhaust_turn + _NPC_EXHAUST_COOLDOWN - 1)
