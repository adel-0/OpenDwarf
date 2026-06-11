"""Situational knowledge injection — NORTHSTAR II.3 item 5.

Loads `memory/knowledge/*.md` at startup. Each file is mapped to tags + inject-when
signals via `memory/knowledge/INDEX.md`. At turn-prompt build time, the current
context is matched against tags; the 1–2 best-matching files are returned for
injection into the *dynamic* section of the turn prompt (never the cached prefix).

Signal sources:
  - site_type of current site (e.g. DarkFortress, NecromancerTower)
  - z-level / underground depth from adventurer position
  - hostile races present
  - active goal text
  - active behavior name
  - scratchpad text (first 500 chars to keep it cheap)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)

# ~4 chars per token (conservative); cap total injected content at 1500 tokens
_MAX_INJECT_TOKENS = 1500
_CHARS_PER_TOKEN = 4
_MAX_INJECT_CHARS = _MAX_INJECT_TOKENS * _CHARS_PER_TOKEN

# site_type strings from GameState that map to tag sets
_SITE_TYPE_TAGS: dict[str, list[str]] = {
    "dark_fortress": ["dark_fortress", "demon", "descent", "underworld"],
    "dark fortress": ["dark_fortress", "demon", "descent", "underworld"],
    "necromancer_tower": ["necromancy", "powers", "chargen"],
    "necromancer tower": ["necromancy", "powers", "chargen"],
    "tower": ["necromancy", "powers"],
    "cave": ["descent"],
    "cavern": ["descent", "underground"],
    "underworld": ["underworld", "demon", "descent"],
}

# Race strings that signal demon encounters
_DEMON_RACES = frozenset({
    "demon", "demon_large", "unique_demon", "pit_fiend", "balrog", "slade_beast",
    "fire_imp", "nightwing", "cave_dragon",
})

# z below this threshold → underground tags
_UNDERGROUND_DEPTH_THRESHOLD = -30


@dataclass
class TopicFile:
    """One entry from the knowledge pack."""
    path: Path
    name: str          # stem, e.g. "demons"
    tags: frozenset[str]
    content: str

    def token_estimate(self) -> int:
        return len(self.content) // _CHARS_PER_TOKEN


def _parse_index(index_path: Path) -> dict[str, frozenset[str]]:
    """Parse INDEX.md tag table: stem → frozenset of tags.

    Expects a markdown table with columns File and Tags (in either order).
    Lines that don't look like table data rows are skipped.
    """
    stem_tags: dict[str, frozenset[str]] = {}
    try:
        text = index_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("knowledge INDEX.md not found at %s", index_path)
        return stem_tags

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or "---" in line or "File" in line:
            continue
        # Strip surrounding pipes and split
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        # First cell is the filename (e.g. `descent.md` or descent.md)
        raw_file = cells[0].strip("`").strip()
        stem = Path(raw_file).stem
        if not stem:
            continue
        # Tags are in the third column (index 2) if present; else second column
        tag_cell = cells[2] if len(cells) >= 3 else cells[1]
        tags = frozenset(
            t.strip().strip("`").lower()
            for t in re.split(r"[,\s]+", tag_cell)
            if t.strip()
        )
        stem_tags[stem] = tags
    return stem_tags


class KnowledgePack:
    """Loaded knowledge pack: tag-indexed topic files ready for injection.

    Instantiate once at startup via `KnowledgePack.load(knowledge_dir)`.
    At each turn call `select(state, goal_text, behavior_name, scratchpad)` to
    get the 1–2 best-matching files for injection.
    """

    def __init__(self, topics: list[TopicFile]) -> None:
        self._topics = topics

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, knowledge_dir: Path) -> "KnowledgePack":
        """Load all .md files from *knowledge_dir* (excluding INDEX.md).

        Tags come from INDEX.md in the same directory; files not listed there
        are loaded with an empty tag set (they can still be matched by name
        appearing in goal/scratchpad text).
        """
        if not knowledge_dir.is_dir():
            logger.warning("Knowledge dir not found: %s", knowledge_dir)
            return cls([])

        index_path = knowledge_dir / "INDEX.md"
        stem_tags = _parse_index(index_path)

        topics: list[TopicFile] = []
        for md_path in sorted(knowledge_dir.glob("*.md")):
            if md_path.name.upper() == "INDEX.MD":
                continue
            stem = md_path.stem
            tags = stem_tags.get(stem, frozenset())
            try:
                content = md_path.read_text(encoding="utf-8")
            except OSError:
                logger.warning("Could not read knowledge file: %s", md_path)
                continue
            topics.append(TopicFile(path=md_path, name=stem, tags=tags, content=content))
            logger.debug("Loaded knowledge topic %r (%d tags)", stem, len(tags))

        logger.info("Knowledge pack loaded: %d topic files", len(topics))
        return cls(topics)

    # ------------------------------------------------------------------
    # Signal extraction
    # ------------------------------------------------------------------

    def _context_tags(
        self,
        state: "GameState",
        goal_text: str,
        behavior_name: str,
        scratchpad: str,
    ) -> set[str]:
        """Collect active context tags from all signal sources."""
        ctx: set[str] = set()

        # 1. Site type
        site_type_raw = (state.site_type or "").lower()
        for key, tags in _SITE_TYPE_TAGS.items():
            if key in site_type_raw:
                ctx.update(tags)

        # 2. Underground depth
        if state.adventurer_position is not None:
            z = state.adventurer_position.z
            if z <= _UNDERGROUND_DEPTH_THRESHOLD:
                ctx.update({"underground", "descent"})

        # 3. Hostile races
        hostile_races = {u.race.lower() for u in state.hostile_units}
        for race in hostile_races:
            if race in _DEMON_RACES or "demon" in race:
                ctx.update({"demon", "underworld", "combat_endgame"})

        # 4. Goal text
        goal_lower = goal_text.lower()
        if any(w in goal_lower for w in ("demon", "unique demon", "throne")):
            ctx.update({"demon", "underworld", "dark_fortress"})
        if any(w in goal_lower for w in ("underworld", "hell", "slade", "spire")):
            ctx.update({"underworld", "descent", "dark_fortress"})
        if any(w in goal_lower for w in ("descend", "descent", "cavern", "underground", "stairs")):
            ctx.update({"descent", "underground"})
        if any(w in goal_lower for w in ("slab", "necromancy", "necromancer", "immortal", "secret of life")):
            ctx.update({"necromancy", "powers", "chargen"})
        if any(w in goal_lower for w in ("train", "grind", "skill", "spar", "level", "combat training")):
            ctx.update({"training", "grind"})
        if any(w in goal_lower for w in ("dark fortress", "goblin tower", "pit", "goblin")):
            ctx.update({"dark_fortress", "descent"})

        # 5. Active behavior name
        if behavior_name:
            bname = behavior_name.lower()
            if "grind" in bname or "combat" in bname or "patrol" in bname:
                ctx.update({"training", "grind"})
            if "descend" in bname or "journey" in bname:
                ctx.update({"descent"})

        # 6. Scratchpad text (first 500 chars)
        scratch_lower = scratchpad[:500].lower()
        if any(w in scratch_lower for w in ("demon", "underworld", "slade")):
            ctx.update({"demon", "underworld"})
        if any(w in scratch_lower for w in ("descend", "dark fortress", "spire")):
            ctx.update({"descent", "dark_fortress"})
        if any(w in scratch_lower for w in ("slab", "necromancy", "tower")):
            ctx.update({"necromancy", "powers"})
        if any(w in scratch_lower for w in ("grind", "train", "spar")):
            ctx.update({"training", "grind"})

        return ctx

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def select(
        self,
        state: "GameState",
        goal_text: str = "",
        behavior_name: str = "",
        scratchpad: str = "",
    ) -> list[TopicFile]:
        """Return the 1–2 best-matching topic files for this turn's context.

        Score = number of matching tags. Files with score 0 are excluded.
        Total injected content is capped at ~1500 tokens.
        """
        if not self._topics:
            return []

        ctx = self._context_tags(state, goal_text, behavior_name, scratchpad)
        if not ctx:
            return []

        scored: list[tuple[int, TopicFile]] = []
        for topic in self._topics:
            score = len(topic.tags & ctx)
            if score > 0:
                scored.append((score, topic))

        if not scored:
            return []

        # Highest score first; stable sort by name for determinism on ties
        scored.sort(key=lambda t: (-t[0], t[1].name))

        selected: list[TopicFile] = []
        total_chars = 0
        for score, topic in scored[:2]:
            if total_chars + len(topic.content) > _MAX_INJECT_CHARS:
                break
            selected.append(topic)
            total_chars += len(topic.content)

        return selected

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    @staticmethod
    def render_for_prompt(topics: list[TopicFile]) -> str:
        """Format selected topics as a prompt block for the dynamic section."""
        if not topics:
            return ""
        parts = ["-- Situational Knowledge --"]
        for topic in topics:
            parts.append(f"\n### {topic.name.replace('_', ' ').title()}\n{topic.content.strip()}")
        return "\n".join(parts)
