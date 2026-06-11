"""Unit tests for NORTHSTAR II.3 item 5 — situational knowledge injection.

Tests cover:
- Tag matching: synthetic GameState + goal/behavior/scratchpad fixtures → expected files
- Token cap: files over the limit are excluded
- No-match → no injection → empty string
- Prompt block: prefix (cached) is untouched; knowledge appears only in the dynamic section
- INDEX.md parsing
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from opendwarf.memory.knowledge import (
    KnowledgePack,
    TopicFile,
    _MAX_INJECT_CHARS,
    _parse_index,
)
from opendwarf.state.game_state import GameState, Position, UnitInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(
    *,
    site_type: str = "",
    z: int = 0,
    hostile_races: list[str] | None = None,
) -> GameState:
    s = GameState()
    s.site_type = site_type
    s.adventurer_position = Position(0, 0, z)
    for i, race in enumerate(hostile_races or []):
        u = UnitInfo(id=i, name=race.title(), race=race,
                     position=Position(1, 1, 0), is_hostile=True, distance=2)
        s.hostile_units.append(u)
        s.nearby_units.append(u)
    return s


def _topic(name: str, tags: frozenset[str], content: str = "dummy content") -> TopicFile:
    return TopicFile(
        path=Path(f"/fake/{name}.md"),
        name=name,
        tags=tags,
        content=content,
    )


def _pack(*topics: TopicFile) -> KnowledgePack:
    return KnowledgePack(list(topics))


# ---------------------------------------------------------------------------
# Tag-matching tests
# ---------------------------------------------------------------------------

class TestTagMatching:
    """Each test fixes one signal source and checks which files are selected."""

    def test_dark_fortress_site_selects_descent_and_demons(self):
        pack = _pack(
            _topic("descent", frozenset({"descent", "dark_fortress", "underworld"})),
            _topic("demons", frozenset({"demon", "underworld", "combat_endgame"})),
            _topic("training", frozenset({"training", "grind"})),
        )
        state = _state(site_type="dark_fortress")
        selected = pack.select(state)
        names = {t.name for t in selected}
        assert "descent" in names or "demons" in names, "dark_fortress site must pull descent or demons"

    def test_goal_mentions_demon_selects_demons(self):
        pack = _pack(
            _topic("demons", frozenset({"demon", "underworld", "combat_endgame", "dark_fortress"})),
            _topic("training", frozenset({"training", "grind"})),
        )
        state = _state()
        selected = pack.select(state, goal_text="kill the unique demon in the throne room")
        names = {t.name for t in selected}
        assert "demons" in names

    def test_goal_mentions_grind_selects_training(self):
        pack = _pack(
            _topic("training", frozenset({"training", "grind", "skill"})),
            _topic("demons", frozenset({"demon", "underworld"})),
        )
        state = _state()
        selected = pack.select(state, goal_text="grind combat skills to legendary")
        names = {t.name for t in selected}
        assert "training" in names

    def test_goal_mentions_necromancy_selects_powers(self):
        pack = _pack(
            _topic("powers", frozenset({"necromancy", "powers", "chargen", "slab"})),
            _topic("training", frozenset({"training", "grind"})),
        )
        state = _state()
        selected = pack.select(state, goal_text="read the slab and learn necromancy")
        names = {t.name for t in selected}
        assert "powers" in names

    def test_goal_mentions_descent_selects_descent(self):
        pack = _pack(
            _topic("descent", frozenset({"descent", "dark_fortress", "underworld"})),
            _topic("training", frozenset({"training", "grind"})),
        )
        state = _state()
        selected = pack.select(state, goal_text="descend into the underworld via the spire")
        names = {t.name for t in selected}
        assert "descent" in names

    def test_behavior_grind_combat_selects_training(self):
        pack = _pack(
            _topic("training", frozenset({"training", "grind"})),
            _topic("descent", frozenset({"descent", "dark_fortress"})),
        )
        state = _state()
        selected = pack.select(state, behavior_name="grind_combat")
        names = {t.name for t in selected}
        assert "training" in names

    def test_scratchpad_demon_selects_demons(self):
        pack = _pack(
            _topic("demons", frozenset({"demon", "underworld", "combat_endgame"})),
            _topic("training", frozenset({"training", "grind"})),
        )
        state = _state()
        selected = pack.select(state, scratchpad="heading to fight the demon in the slade spire")
        names = {t.name for t in selected}
        assert "demons" in names

    def test_scratchpad_grind_selects_training(self):
        pack = _pack(
            _topic("training", frozenset({"training", "grind"})),
            _topic("demons", frozenset({"demon", "underworld"})),
        )
        state = _state()
        selected = pack.select(state, scratchpad="need to grind weapon skills before attempting the fortress")
        names = {t.name for t in selected}
        assert "training" in names

    def test_hostile_demon_race_selects_demons(self):
        pack = _pack(
            _topic("demons", frozenset({"demon", "underworld", "combat_endgame"})),
            _topic("training", frozenset({"training", "grind"})),
        )
        state = _state(hostile_races=["demon"])
        selected = pack.select(state)
        names = {t.name for t in selected}
        assert "demons" in names

    def test_deep_z_selects_descent(self):
        pack = _pack(
            _topic("descent", frozenset({"descent", "underground"})),
            _topic("training", frozenset({"training", "grind"})),
        )
        state = _state(z=-50)
        selected = pack.select(state)
        names = {t.name for t in selected}
        assert "descent" in names

    def test_shallow_z_no_underground_tag(self):
        pack = _pack(
            _topic("descent", frozenset({"descent", "underground"})),
        )
        state = _state(z=10)
        # Only underground tag in "descent" — no other signal. No match expected.
        selected = pack.select(state)
        # At z=10 (above threshold of -30), no underground signal fires
        assert selected == []

    def test_necromancer_tower_site_selects_powers(self):
        pack = _pack(
            _topic("powers", frozenset({"necromancy", "powers", "chargen", "tower"})),
            _topic("descent", frozenset({"descent", "dark_fortress"})),
        )
        state = _state(site_type="necromancer_tower")
        selected = pack.select(state)
        names = {t.name for t in selected}
        assert "powers" in names


# ---------------------------------------------------------------------------
# No-match tests
# ---------------------------------------------------------------------------

class TestNoMatch:
    def test_no_signals_returns_empty(self):
        pack = _pack(
            _topic("demons", frozenset({"demon", "underworld"})),
            _topic("training", frozenset({"training", "grind"})),
        )
        state = _state()
        result = pack.select(state)
        assert result == []

    def test_empty_pack_returns_empty(self):
        pack = KnowledgePack([])
        state = _state()
        assert pack.select(state, goal_text="kill the demon") == []

    def test_no_tag_overlap_returns_empty(self):
        # Topic has tags that don't match any signal
        pack = _pack(_topic("obscure", frozenset({"xyzzy", "plugh"})))
        state = _state(site_type="dark_fortress")
        assert pack.select(state) == []


# ---------------------------------------------------------------------------
# Token cap tests
# ---------------------------------------------------------------------------

class TestTokenCap:
    def test_oversized_single_file_excluded(self):
        big_content = "x" * (_MAX_INJECT_CHARS + 100)
        pack = _pack(_topic("demons", frozenset({"demon", "underworld"}), content=big_content))
        state = _state(hostile_races=["demon"])
        selected = pack.select(state)
        assert selected == [], "File exceeding token cap must be excluded"

    def test_second_file_excluded_when_cap_reached(self):
        # First file almost fills the cap; second file has the same tag but won't fit
        near_cap = "x" * (_MAX_INJECT_CHARS - 10)
        pack = _pack(
            _topic("demons", frozenset({"demon", "underworld", "combat_endgame"}), content=near_cap),
            _topic("descent", frozenset({"descent", "underworld"}), content="small content"),
        )
        state = _state(hostile_races=["demon"])
        selected = pack.select(state, goal_text="descend into the underworld")
        total_chars = sum(len(t.content) for t in selected)
        assert total_chars <= _MAX_INJECT_CHARS

    def test_two_small_files_both_included(self):
        pack = _pack(
            _topic("demons", frozenset({"demon", "underworld", "combat_endgame"}), content="demon content"),
            _topic("descent", frozenset({"descent", "underworld", "dark_fortress"}), content="descent content"),
        )
        state = _state(hostile_races=["demon"])
        selected = pack.select(state, goal_text="descend to the underworld to fight demons")
        assert len(selected) == 2

    def test_at_most_two_files_selected(self):
        # Even with 3+ matching files, at most 2 are returned
        pack = _pack(
            _topic("demons", frozenset({"demon", "underworld"}), content="d"),
            _topic("descent", frozenset({"descent", "underworld"}), content="d"),
            _topic("training", frozenset({"training", "underworld"}), content="d"),
        )
        state = _state()
        selected = pack.select(state, goal_text="train and descend to fight demons in the underworld")
        assert len(selected) <= 2


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

class TestRenderForPrompt:
    def test_empty_topics_returns_empty_string(self):
        assert KnowledgePack.render_for_prompt([]) == ""

    def test_single_topic_rendered(self):
        topic = _topic("demons", frozenset({"demon"}), content="Demon facts here.")
        result = KnowledgePack.render_for_prompt([topic])
        assert "Situational Knowledge" in result
        assert "Demon facts here." in result

    def test_two_topics_both_in_output(self):
        t1 = _topic("demons", frozenset({"demon"}), content="Demon content.")
        t2 = _topic("training", frozenset({"training"}), content="Training content.")
        result = KnowledgePack.render_for_prompt([t1, t2])
        assert "Demon content." in result
        assert "Training content." in result

    def test_knowledge_block_not_in_system_prefix(self):
        """The cached system prefix must not contain knowledge content.

        build_system_bundle is called with goal/mechanics/postmortems only;
        knowledge goes into the user turn prompt via build_turn_prompt.
        """
        from opendwarf.agent.prompts import build_system_bundle, build_turn_prompt

        bundle = build_system_bundle(
            goal_summary="kill a demon",
            df_mechanics="some mechanics",
            postmortems="",
        )
        # System prefix must NOT contain topic content
        for block in bundle.system_blocks:
            assert "Demon facts" not in block.text

        # knowledge_block is in the user turn prompt
        knowledge = "-- Situational Knowledge --\nDemon facts."
        user_prompt = build_turn_prompt(
            state_summary="state here",
            knowledge_block=knowledge,
        )
        assert "Demon facts." in user_prompt

    def test_no_knowledge_prompt_unchanged(self):
        """Prompt with no knowledge_block is identical to the pre-feature baseline."""
        from opendwarf.agent.prompts import build_turn_prompt

        baseline = build_turn_prompt(state_summary="state")
        with_empty = build_turn_prompt(state_summary="state", knowledge_block="")
        assert baseline == with_empty


# ---------------------------------------------------------------------------
# INDEX.md parsing
# ---------------------------------------------------------------------------

class TestParseIndex:
    def test_parses_real_index(self, tmp_path: Path):
        index = tmp_path / "INDEX.md"
        index.write_text(
            "# Index\n\n"
            "| File | Inject when | Tags |\n"
            "|---|---|---|\n"
            "| `descent.md` | goal mentions descent | `descent`, `dark_fortress`, `underworld` |\n"
            "| `demons.md` | demon present | `demon`, `underworld`, `combat_endgame` |\n",
            encoding="utf-8",
        )
        result = _parse_index(index)
        assert "descent" in result
        assert "dark_fortress" in result["descent"]
        assert "underworld" in result["descent"]
        assert "demons" in result
        assert "combat_endgame" in result["demons"]

    def test_missing_index_returns_empty(self, tmp_path: Path):
        result = _parse_index(tmp_path / "NOFILE.md")
        assert result == {}

    def test_load_from_real_knowledge_dir(self):
        """Integration: load from the actual memory/knowledge/ directory."""
        knowledge_dir = Path(__file__).parent.parent / "memory" / "knowledge"
        if not knowledge_dir.is_dir():
            pytest.skip("memory/knowledge/ not present")
        pack = KnowledgePack.load(knowledge_dir)
        # All four topic files should load
        names = {t.name for t in pack._topics}
        assert "demons" in names
        assert "descent" in names
        assert "training" in names
        assert "powers" in names

    def test_load_integration_tag_matching(self):
        """Integration: real pack + real INDEX.md → expected selections."""
        knowledge_dir = Path(__file__).parent.parent / "memory" / "knowledge"
        if not knowledge_dir.is_dir():
            pytest.skip("memory/knowledge/ not present")
        pack = KnowledgePack.load(knowledge_dir)

        # GrindCombat behavior → training.md
        state = _state()
        selected = pack.select(state, behavior_name="grind_combat")
        assert any(t.name == "training" for t in selected)

        # Demon hostile → demons.md
        state2 = _state(hostile_races=["demon"])
        selected2 = pack.select(state2)
        assert any(t.name == "demons" for t in selected2)

        # Dark fortress site → descent.md or demons.md
        state3 = _state(site_type="dark_fortress")
        selected3 = pack.select(state3)
        names3 = {t.name for t in selected3}
        assert names3 & {"descent", "demons"}

        # Necromancer tower site → powers.md
        state4 = _state(site_type="necromancer_tower")
        selected4 = pack.select(state4)
        assert any(t.name == "powers" for t in selected4)
