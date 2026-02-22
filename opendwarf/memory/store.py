"""MemoryStore — file-based storage for MemoryNotes.

Directory layout:
    memory/
        episodic/   mem_<id>.md
        semantic/   mem_<id>.md
        procedural/ mem_<id>.md
        postmortems.md
        df_mechanics.md
"""

from __future__ import annotations

import logging
from pathlib import Path

from opendwarf.memory.model import MemoryNote

logger = logging.getLogger(__name__)


class MemoryStore:
    """Manages the memory directory: write, read, update-in-place."""

    def __init__(self, memory_dir: Path = Path("memory")) -> None:
        self.memory_dir = memory_dir
        for sub in ("episodic", "semantic", "procedural"):
            (memory_dir / sub).mkdir(parents=True, exist_ok=True)

    def _path_for(self, note: MemoryNote) -> Path:
        subdir = note.type if note.type in ("episodic", "semantic", "procedural") else "episodic"
        return self.memory_dir / subdir / f"{note.id}.md"

    def write(self, note: MemoryNote) -> None:
        """Write a note to disk."""
        path = self._path_for(note)
        note.to_file(path)
        logger.debug("Memory written: %s [%s imp=%d]", note.id, note.type, note.importance)

    def update(self, note: MemoryNote) -> None:
        """Overwrite an existing note on disk."""
        self.write(note)
        logger.debug("Memory updated: %s", note.id)

    def load_all(self) -> list[MemoryNote]:
        """Load all non-expired memory notes from disk."""
        notes: list[MemoryNote] = []
        for subdir in ("episodic", "semantic", "procedural"):
            for path in (self.memory_dir / subdir).glob("*.md"):
                try:
                    note = MemoryNote.from_file(path)
                    notes.append(note)
                except Exception:
                    logger.exception("Failed to load memory note: %s", path)
        return notes

    def find_by_entity(self, entity_id: str) -> MemoryNote | None:
        """Find the first semantic note matching a specific entity ID."""
        for path in (self.memory_dir / "semantic").glob("*.md"):
            try:
                note = MemoryNote.from_file(path)
                if entity_id in note.entities:
                    return note
            except Exception:
                pass
        return None

    def find_by_id(self, note_id: str) -> MemoryNote | None:
        for subdir in ("episodic", "semantic", "procedural"):
            path = self.memory_dir / subdir / f"{note_id}.md"
            if path.exists():
                try:
                    return MemoryNote.from_file(path)
                except Exception:
                    pass
        return None

    def mark_expired(self, note: MemoryNote) -> None:
        note.expired = True
        self.write(note)
        logger.debug("Memory expired: %s", note.id)

    def mark_accessed(self, note: MemoryNote, current_tick: int) -> None:
        note.last_accessed_tick = current_tick
        self.write(note)
