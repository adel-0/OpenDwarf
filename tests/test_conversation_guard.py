"""Unit tests for _ConversationGuard — isolated, no GameState/IO.

The guard counts conversation engagements PER NPC (additive across target
switches, surviving "productive" chats) and only clears on genuine departure.
See the 2026-06-16 playtest: a single shared streak was defeated by every
converse sweep looking productive, by alternating two NPCs, and by goto_unit
between chats. These tests pin the per-NPC semantics that fix all three.
"""

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

    def test_none_key_is_ignored(self):
        g = _ConversationGuard()
        for t in range(_NPC_TALK_LIMIT * 2):
            g.note_conversation(None, t)
        assert not g.is_exhausted(None, _NPC_TALK_LIMIT * 2)

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


class TestPerNpcAccumulation:
    """The regressions the 2026-06-16 fix targets — counts must survive switches
    and 'productive' chats; only genuine departure clears them."""

    def test_alternating_two_npcs_both_exhaust(self):
        # Live failure: agent rotated converse:A / converse:B and neither tripped
        # because each switch reset the single shared streak.
        g = _ConversationGuard()
        turn = 0
        for _ in range(_NPC_TALK_LIMIT):
            g.note_conversation("A", turn); turn += 1
            g.note_conversation("B", turn); turn += 1
        assert g.is_exhausted("A", turn)
        assert g.is_exhausted("B", turn)

    def test_productive_chat_does_not_forgive_reengagement(self):
        # Every converse sweep writes a transcript; that must NOT reset the count.
        # (note_productive is gone — repeated engagements simply accumulate.)
        g = _ConversationGuard()
        for t in range(_NPC_TALK_LIMIT):
            g.note_conversation("77", t)
        assert g.is_exhausted("77", _NPC_TALK_LIMIT)


class TestNoteOtherAction:
    def test_departure_clears_counts_so_not_exhausted(self):
        g = _ConversationGuard()
        key = "99"
        for t in range(_NPC_TALK_LIMIT - 1):
            g.note_conversation(key, t)
        g.note_other_action()
        # Fresh budget after departure — limit-1 more never reaches the limit.
        for t in range(_NPC_TALK_LIMIT - 1):
            g.note_conversation(key, _NPC_TALK_LIMIT + t)
        assert not g.is_exhausted(key, _NPC_TALK_LIMIT * 2)

    def test_count_cleared(self):
        g = _ConversationGuard()
        key = "99"
        for t in range(_NPC_TALK_LIMIT - 1):
            g.note_conversation(key, t)
        g.note_other_action()
        assert g.streak_for(key) == 0

    def test_departure_does_not_clear_exhausted_mark(self):
        g = _ConversationGuard()
        key = "77"
        for i in range(_NPC_TALK_LIMIT):
            g.note_conversation(key, 5)
        assert g.is_exhausted(key, 5)
        g.note_other_action()
        # Cooldown still applies even after leaving and coming back.
        assert g.is_exhausted(key, 6)


class TestSwitchingTarget:
    def test_switching_target_keeps_both_counts(self):
        g = _ConversationGuard()
        for t in range(_NPC_TALK_LIMIT - 1):
            g.note_conversation("A", t)
        g.note_conversation("B", _NPC_TALK_LIMIT)
        # A retains its accumulated count; switching away did not abandon it.
        assert g.streak_for("A") == _NPC_TALK_LIMIT - 1
        assert g.streak_for("B") == 1

    def test_counts_are_independent(self):
        g = _ConversationGuard()
        g.note_conversation("A", 0)
        g.note_conversation("B", 1)
        g.note_conversation("B", 2)
        assert g.streak_for("A") == 1
        assert g.streak_for("B") == 2


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
