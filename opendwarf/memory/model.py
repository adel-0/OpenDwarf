"""MemoryNote — the atomic unit of the memory system.

Each note is a markdown file with YAML frontmatter:

    ---
    id: mem_00412
    type: episodic
    tick: 18450
    importance: 8
    tags: [combat, undead, victory]
    entities: [hist_fig_1234]
    links: [mem_00398]
    source: observed
    confidence: 1.0
    cross_session: true
    last_accessed_tick: 0
    ---

    Defeated a wight near the Tomb of Ul at tick 18450...
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class MemoryNote:
    id: str
    type: str           # episodic | semantic | procedural
    tick: int           # game tick when the note was created
    importance: int     # 1–10
    tags: list[str]
    entities: list[str] # hist_fig_id (int as str) or "unit_type:GOBLIN"
    links: list[str]    # related memory IDs
    source: str         # observed | inferred | reflection
    confidence: float   # 1.0 = direct observation; <0.5 = LLM inference
    cross_session: bool
    content: str
    last_accessed_tick: int = 0
    expired: bool = False
    # Procedural-only fields
    success_count: int = 0
    attempt_count: int = 0

    @staticmethod
    def new(
        type: str,
        tick: int,
        importance: int,
        tags: list[str],
        content: str,
        *,
        entities: list[str] | None = None,
        links: list[str] | None = None,
        source: str = "observed",
        confidence: float = 1.0,
        cross_session: bool | None = None,
    ) -> MemoryNote:
        # Auto-determine cross_session based on importance threshold
        if cross_session is None:
            cross_session = importance >= 7 or type == "semantic"
        return MemoryNote(
            id=f"mem_{uuid.uuid4().hex[:8]}",
            type=type,
            tick=tick,
            importance=importance,
            tags=tags,
            entities=entities or [],
            links=links or [],
            source=source,
            confidence=confidence,
            cross_session=cross_session,
            content=content,
        )

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def to_file(self, path: Path) -> None:
        """Write note as markdown with YAML frontmatter."""
        front: dict = {
            "id": self.id,
            "type": self.type,
            "tick": self.tick,
            "importance": self.importance,
            "tags": self.tags,
            "entities": self.entities,
            "links": self.links,
            "source": self.source,
            "confidence": self.confidence,
            "cross_session": self.cross_session,
            "last_accessed_tick": self.last_accessed_tick,
            "expired": self.expired,
        }
        if self.type == "procedural":
            front["success_count"] = self.success_count
            front["attempt_count"] = self.attempt_count
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "---\n" + yaml.dump(front, default_flow_style=False) + "---\n\n" + self.content + "\n"
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def from_file(path: Path) -> MemoryNote:
        """Parse a markdown file with YAML frontmatter."""
        text = path.read_text(encoding="utf-8")
        if text.startswith("---"):
            parts = text.split("---", 2)
            # parts[0] = "" | parts[1] = yaml | parts[2] = content
            front = yaml.safe_load(parts[1]) or {}
            content = parts[2].strip() if len(parts) > 2 else ""
        else:
            front = {}
            content = text.strip()
        return MemoryNote(
            id=str(front.get("id", f"mem_{path.stem}")),
            type=str(front.get("type", "episodic")),
            tick=int(front.get("tick", 0)),
            importance=int(front.get("importance", 5)),
            tags=list(front.get("tags", [])),
            entities=list(front.get("entities", [])),
            links=list(front.get("links", [])),
            source=str(front.get("source", "observed")),
            confidence=float(front.get("confidence", 1.0)),
            cross_session=bool(front.get("cross_session", False)),
            content=content,
            last_accessed_tick=int(front.get("last_accessed_tick", 0)),
            expired=bool(front.get("expired", False)),
            success_count=int(front.get("success_count", 0)),
            attempt_count=int(front.get("attempt_count", 0)),
        )

    # ------------------------------------------------------------------
    # Retrieval scoring
    # ------------------------------------------------------------------

    def score(self, query_words: set[str], current_tick: int) -> float:
        """Score = recency × importance_norm × relevance."""
        # Recency with macro-time clamping (max 1000 ticks per action already applied upstream)
        ticks_elapsed = max(0, current_tick - self.tick)
        recency = 0.99 ** (ticks_elapsed / 100)

        importance_norm = self.importance / 10.0

        # Relevance: word overlap between query and content+tags
        note_words = set(self.content.lower().split()) | {t.lower() for t in self.tags}
        if query_words:
            overlap = len(query_words & note_words)
            relevance = overlap / len(query_words)
        else:
            relevance = 0.5

        return recency * importance_norm * relevance

    @property
    def success_rate(self) -> float:
        if self.attempt_count == 0:
            return 1.0
        return self.success_count / self.attempt_count
