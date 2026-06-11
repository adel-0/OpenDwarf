"""New-life detection: reset stale goals/scratchpad/chunks on adventurer change.

Compares the current adventurer identity (name) against a persisted identity
file. On mismatch (or absent file with stale artifacts), archives the stale
files and writes the new identity.

Note: chunks are per-world-absolute coordinates. Resetting them on adventurer
change within the same world loses map knowledge, but correctness (no stale
goals from a previous life) beats reuse for now. Same-world detection is a
later refinement.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_IDENTITY_FILENAME = "identity.json"


def _extract_identity(state_raw: dict) -> dict | None:
    """Pull adventurer identity fields from the raw state dict.

    Uses adventurer name as the primary key. Returns None if the identity
    cannot be determined (e.g. state extraction failed or no adventurer).
    """
    adv = state_raw.get("adventurer", {})
    if isinstance(adv, list):
        adv = {}  # Lua empty table encodes as []
    name = adv.get("name", "")
    if not name or name == "Unknown":
        return None
    identity: dict = {"adventurer_name": name}
    # Include player_id from game dict if present (may be added to Lua in future)
    game = state_raw.get("game", {})
    if isinstance(game, dict):
        player_id = game.get("player_id")
        if player_id is not None:
            identity["player_id"] = player_id
    return identity


def _identities_match(old: dict, new: dict) -> bool:
    """Return True if the two identity dicts refer to the same adventurer."""
    # If player_id is available in both, use it as the primary key.
    if "player_id" in old and "player_id" in new:
        return old["player_id"] == new["player_id"]
    # Fall back to name comparison.
    return old.get("adventurer_name") == new.get("adventurer_name")


def _archive_files(
    timestamp: str,
    *,
    goals_file: Path,
    scratchpad_path: Path,
    chunks_path: Path,
) -> None:
    """Move existing stale artifacts into memory/archive/life_<timestamp>/."""
    archive_dir = goals_file.parent.parent / "memory" / "archive" / f"life_{timestamp}"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for src in (goals_file, scratchpad_path, chunks_path):
        if src.exists():
            dest = archive_dir / src.name
            shutil.move(str(src), dest)
            logger.info("Archived %s → %s", src, dest)


def check_new_life(
    state_raw: dict,
    identity_path: Path,
    *,
    goals_file: Path,
    scratchpad_path: Path,
    chunks_path: Path,
) -> bool:
    """Check if the current adventurer is a new life; archive stale files if so.

    Returns True if a new life was detected (files may have been archived).
    Returns False if the identity matches or could not be determined.

    Conservative: if identity can't be determined from state_raw, does nothing.
    """
    new_identity = _extract_identity(state_raw)
    if new_identity is None:
        logger.debug("lifecycle: could not determine adventurer identity; doing nothing")
        return False

    # Load persisted identity.
    old_identity: dict | None = None
    if identity_path.exists():
        try:
            old_identity = json.loads(identity_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("lifecycle: failed to read %s; treating as absent", identity_path)
            old_identity = None

    if old_identity is not None and _identities_match(old_identity, new_identity):
        logger.debug("lifecycle: same adventurer (%s); no action", new_identity.get("adventurer_name"))
        return False

    # New adventurer (or first run with stale artifacts).
    old_name = old_identity.get("adventurer_name", "?") if old_identity else "(none)"
    new_name = new_identity.get("adventurer_name", "?")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stale_exist = any(p.exists() for p in (goals_file, scratchpad_path, chunks_path))

    if stale_exist:
        logger.info(
            "New adventurer detected (%s → %s): archiving goals/scratchpad/map",
            old_name, new_name,
        )
        _archive_files(
            timestamp,
            goals_file=goals_file,
            scratchpad_path=scratchpad_path,
            chunks_path=chunks_path,
        )
    elif old_identity is None:
        logger.info("lifecycle: first run, writing identity for %s", new_name)
    else:
        logger.info(
            "New adventurer detected (%s → %s): no stale artifacts to archive",
            old_name, new_name,
        )

    # Write new identity.
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_text(json.dumps(new_identity, indent=2), encoding="utf-8")
    return True
