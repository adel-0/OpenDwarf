"""Tests for new-life detection (WP3)."""

from __future__ import annotations

import json
from pathlib import Path

from opendwarf.agent.lifecycle import check_new_life


def _raw_state(name: str, player_id: int | None = None) -> dict:
    """Build a minimal raw state dict for testing."""
    state: dict = {"adventurer": {"name": name}}
    if player_id is not None:
        state["game"] = {"player_id": player_id}
    return state


def _setup(tmp_path: Path) -> dict:
    """Return a kwargs dict for check_new_life with tmp dirs."""
    goals_dir = tmp_path / "goals"
    goals_dir.mkdir()
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    spatial_dir = tmp_path / "spatial"
    spatial_dir.mkdir()
    return {
        "identity_path": goals_dir / "identity.json",
        "goals_file": goals_dir / "active_goals.json",
        "scratchpad_path": memory_dir / "scratchpad.md",
        "chunks_path": spatial_dir / "chunks.json",
    }


def test_first_call_writes_identity(tmp_path: Path) -> None:
    """First call with no prior identity file writes identity, no archive created."""
    kwargs = _setup(tmp_path)
    result = check_new_life(_raw_state("Urist McTest"), **kwargs)
    assert result is True
    identity = json.loads(kwargs["identity_path"].read_text())
    assert identity["adventurer_name"] == "Urist McTest"
    # No archive dir created because there were no stale files.
    archive_base = tmp_path / "memory" / "archive"
    assert not archive_base.exists() or not any(archive_base.iterdir())


def test_same_identity_is_noop(tmp_path: Path) -> None:
    """Second call with the same adventurer is a no-op."""
    kwargs = _setup(tmp_path)
    check_new_life(_raw_state("Urist McTest"), **kwargs)
    result = check_new_life(_raw_state("Urist McTest"), **kwargs)
    assert result is False


def test_changed_identity_archives_files(tmp_path: Path) -> None:
    """Changed adventurer name triggers file archival."""
    kwargs = _setup(tmp_path)

    # First life: write identity and create stale artifacts.
    check_new_life(_raw_state("Urist McTest"), **kwargs)
    goals_file: Path = kwargs["goals_file"]
    scratchpad: Path = kwargs["scratchpad_path"]
    chunks: Path = kwargs["chunks_path"]
    goals_file.write_text('{"goals": []}')
    scratchpad.write_text("old scratchpad")
    chunks.write_text('{"chunks": {}}')

    # Second life: different adventurer.
    result = check_new_life(_raw_state("Bomrek McSword"), **kwargs)
    assert result is True

    # Stale files should be gone from their original locations.
    assert not goals_file.exists(), "goals file should have been moved"
    assert not scratchpad.exists(), "scratchpad should have been moved"
    assert not chunks.exists(), "chunks.json should have been moved"

    # They should be in the archive.
    archive_base = tmp_path / "memory" / "archive"
    life_dirs = list(archive_base.iterdir())
    assert len(life_dirs) == 1
    archived = {p.name for p in life_dirs[0].iterdir()}
    assert "active_goals.json" in archived
    assert "scratchpad.md" in archived
    assert "chunks.json" in archived

    # New identity was written.
    identity = json.loads(kwargs["identity_path"].read_text())
    assert identity["adventurer_name"] == "Bomrek McSword"


def test_unknown_identity_is_skipped(tmp_path: Path) -> None:
    """If adventurer name is Unknown/empty, nothing happens."""
    kwargs = _setup(tmp_path)
    result = check_new_life(_raw_state("Unknown"), **kwargs)
    assert result is False
    assert not kwargs["identity_path"].exists()


def test_player_id_used_when_available(tmp_path: Path) -> None:
    """If player_id is in state, it's used for identity matching."""
    kwargs = _setup(tmp_path)
    # Same player_id but different name (e.g. renamed — treated as same life).
    check_new_life(_raw_state("Urist McTest", player_id=42), **kwargs)
    result = check_new_life(_raw_state("Different Name", player_id=42), **kwargs)
    assert result is False  # same player_id → same adventurer


def test_partial_stale_files_ok(tmp_path: Path) -> None:
    """Archive works even if only some stale files exist."""
    kwargs = _setup(tmp_path)
    check_new_life(_raw_state("Urist McTest"), **kwargs)

    # Only create one stale file.
    kwargs["goals_file"].write_text('{"goals": []}')

    result = check_new_life(_raw_state("Bomrek McSword"), **kwargs)
    assert result is True
    assert not kwargs["goals_file"].exists()

    archive_base = tmp_path / "memory" / "archive"
    life_dirs = list(archive_base.iterdir())
    assert len(life_dirs) == 1
    archived = {p.name for p in life_dirs[0].iterdir()}
    assert "active_goals.json" in archived
    # scratchpad and chunks weren't created so they're not in the archive either.
    assert "scratchpad.md" not in archived
    assert "chunks.json" not in archived
