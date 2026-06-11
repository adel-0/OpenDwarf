"""Death-handling sequence for the tactical loop.

Called once when adventurer_dead is first detected. Performs (in order):
  1. Generate and append a post-mortem lesson via the LLM.
  2. Flush reflection (session-end synthesis of episodic memories).
  3. Write the final behavior digest as an episodic memory note.
  4. Archive the session log directory (copy to logs/archive/<session>/).
  5. Log a structured death event to decisions.jsonl.

The sequence is best-effort: each step is wrapped so a failure in one does not
prevent the others from running. The loop exits gracefully after this returns.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opendwarf.behaviors.base import Behavior
    from opendwarf.llm.base import LLMClient
    from opendwarf.memory.postmortems import PostmortemBuffer
    from opendwarf.memory.reflection import ReflectionEngine
    from opendwarf.memory.writer import MemoryWriter
    from opendwarf.state.game_state import GameState

logger = logging.getLogger(__name__)


def handle_death(
    *,
    state: "GameState",
    cause: str,
    llm: "LLMClient",
    postmortem_buffer: "PostmortemBuffer | None",
    reflection_engine: "ReflectionEngine | None",
    memory_writer: "MemoryWriter | None",
    active_behavior: "Behavior | None",
    suspended_behavior: "Behavior | None",
    log_file,  # open file object for decisions.jsonl
    turn_count: int,
    session_log_dir: Path | None,
) -> None:
    """Execute the full death sequence. All steps are best-effort."""
    logger.info("=== ADVENTURER DEATH DETECTED (tick %d) — beginning death sequence ===",
                state.tick_counter)

    # ------------------------------------------------------------------
    # 1. Generate and append post-mortem lesson
    # ------------------------------------------------------------------
    if postmortem_buffer is not None:
        try:
            postmortem_buffer.generate_and_append(
                cause=cause,
                state_summary=state.summary(),
                llm=llm,
            )
            logger.info("Post-mortem generated and appended.")
        except Exception:
            logger.exception("Post-mortem generation failed (non-fatal)")

    # ------------------------------------------------------------------
    # 2. Flush reflection (session-end synthesis)
    # ------------------------------------------------------------------
    if reflection_engine is not None:
        try:
            notes = reflection_engine.reflect(state)
            logger.info("Session-end reflection produced %d note(s).", len(notes))
        except Exception:
            logger.exception("Session-end reflection failed (non-fatal)")

    # ------------------------------------------------------------------
    # 3. Write the final behavior digest as an episodic memory note
    # ------------------------------------------------------------------
    behavior = active_behavior or suspended_behavior
    if behavior is not None and memory_writer is not None and not behavior.digest.is_empty:
        try:
            one_line = behavior.digest.one_line(behavior_name=behavior.name)
            memory_writer.write_observation(
                f"[FINAL SESSION] Autopilot {behavior.name} active at death: {one_line}.",
                tags=["autopilot", behavior.name, "death"],
                state=state,
            )
            logger.info("Final behavior digest written as episodic note.")
        except Exception:
            logger.exception("Failed to write final behavior digest note (non-fatal)")

    # ------------------------------------------------------------------
    # 4. Archive session log directory
    # ------------------------------------------------------------------
    if session_log_dir is not None and session_log_dir.exists():
        try:
            archive_root = session_log_dir.parent / "archive"
            archive_root.mkdir(parents=True, exist_ok=True)
            dest = archive_root / session_log_dir.name
            if not dest.exists():
                shutil.copytree(str(session_log_dir), str(dest))
                logger.info("Session logs archived to %s", dest)
            else:
                logger.info("Archive already exists at %s — skipping copy", dest)
        except Exception:
            logger.exception("Session log archival failed (non-fatal)")

    # ------------------------------------------------------------------
    # 5. Log structured death event
    # ------------------------------------------------------------------
    try:
        import json
        entry = {
            "event": "adventurer_death",
            "turn": turn_count,
            "tick": state.tick_counter,
            "cause": cause,
            "health_pct": state.health_pct,
            "position": str(state.adventurer_position),
            "site": state.site_name or state.region_name,
            "active_behavior": behavior.name if behavior is not None else None,
        }
        log_file.write(json.dumps(entry) + "\n")
        log_file.flush()
    except Exception:
        logger.exception("Failed to log death event (non-fatal)")

    logger.info("=== Death sequence complete — loop will exit. ===")
