"""OpenDwarf entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from opendwarf.agent.lifecycle import check_new_life
from opendwarf.agent.loop import TacticalLoop
from opendwarf.dfhack.client import DFHackClient
from opendwarf.dfhack.lua_executor import LuaExecutor
from opendwarf.goals.manager import GoalManager
from opendwarf.goals.model import Goal, GoalStatus
from opendwarf.memory.postmortems import PostmortemBuffer
from opendwarf.memory.reflection import ReflectionEngine
from opendwarf.memory.retriever import MemoryRetriever
from opendwarf.memory.store import MemoryStore
from opendwarf.memory.writer import MemoryWriter
from opendwarf.observability import EventLogger


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="OpenDwarf — AI agent for Dwarf Fortress")
    parser.add_argument("--host", default="127.0.0.1", help="DFHack RPC host")
    parser.add_argument("--port", type=int, default=5000, help="DFHack RPC port")
    parser.add_argument("--timeout", type=float, default=10.0, help="RPC timeout in seconds")
    parser.add_argument("--scripts-dir", default=None, help="DFHack scripts directory override")
    parser.add_argument(
        "--goal", default=None,
        help="Seed an initial active goal (natural language). "
             "If omitted, GoalManager will generate goals automatically on session start.",
    )
    parser.add_argument(
        "--goals-dir", default="goals",
        help="Directory for persistent goal tree JSON (default: goals/)",
    )
    parser.add_argument(
        "--memory-dir", default="memory",
        help="Directory for persistent memory notes (default: memory/)",
    )
    parser.add_argument(
        "--logs-dir", default="logs",
        help="Directory for session observability logs (default: logs/)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--record", action="store_true",
        help="Capture every DFHack call to logs/<session>/tape.jsonl for offline "
             "replay + simulator fidelity checks.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # Avoid encoding issues on Windows
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    # Connect to DFHack
    client = DFHackClient(args.host, args.port, args.timeout)
    client.connect()

    session_name = f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    session_dir = Path(args.logs_dir) / session_name

    # Set up Lua executor and deploy scripts
    lua = LuaExecutor(client, args.scripts_dir)
    lua.deploy_scripts()

    # Optional record/replay tape: wrap the executor so every DFHack call is
    # captured to logs/<session>/tape.jsonl for offline replay + sim fidelity.
    recorder = None
    if args.record:
        from opendwarf.sim.record import RecordingLuaExecutor
        recorder = RecordingLuaExecutor(lua, session_dir / "tape.jsonl")
        lua = recorder
        logging.getLogger(__name__).info("Recording DFHack tape: %s/tape.jsonl", session_dir)

    # Set up observability
    event_logger = EventLogger(session_dir)
    logging.getLogger(__name__).info("Observability logs: %s/%s/", args.logs_dir, session_name)

    from opendwarf.llm import build_llm
    llm = build_llm(event_logger=event_logger)

    # New-life detection: do one state extraction before constructing GoalManager
    # so we can archive stale goals/scratchpad/chunks if the adventurer changed.
    goals_dir = Path(args.goals_dir)
    memory_dir_early = Path(args.memory_dir)
    try:
        state_raw_early = lua.extract_state()
        check_new_life(
            state_raw_early,
            identity_path=goals_dir / "identity.json",
            goals_file=goals_dir / "active_goals.json",
            scratchpad_path=memory_dir_early / "scratchpad.md",
            chunks_path=Path("spatial") / "chunks.json",
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "New-life check failed (non-fatal); proceeding with existing state"
        )

    # Set up goal management (Layer 3 — planning merged in)
    goal_manager = GoalManager(llm, goals_dir, event_logger=event_logger)

    # Set up memory system (Priority 4)
    memory_dir = Path(args.memory_dir)
    memory_store = MemoryStore(memory_dir)
    memory_writer = MemoryWriter(memory_store, llm, event_logger=event_logger)
    memory_retriever = MemoryRetriever(memory_store, event_logger=event_logger)
    postmortem_buffer = PostmortemBuffer(memory_dir / "postmortems.md")
    reflection_engine = ReflectionEngine(memory_store, llm, event_logger=event_logger)

    # Load static DF mechanics reference (always injected into system prompt)
    mechanics_path = memory_dir / "df_mechanics.md"
    df_mechanics = mechanics_path.read_text(encoding="utf-8").strip() if mechanics_path.exists() else ""
    if not df_mechanics:
        logging.getLogger(__name__).warning("df_mechanics.md not found at %s", mechanics_path)

    # Seed an initial goal from CLI if provided and no goals already exist
    if args.goal and not goal_manager.active_goals():
        seed = Goal.new(
            description=args.goal,
            created_tick=0,
            status=GoalStatus.ACTIVE,
        )
        goal_manager._goals.append(seed)
        goal_manager.save()
        logging.getLogger(__name__).info("Seeded initial goal: %s", args.goal)

    # Run the tactical loop
    logs_session_dir = Path(args.logs_dir) / session_name
    loop = TacticalLoop(
        lua, llm,
        goal_manager=goal_manager,
        memory_writer=memory_writer,
        memory_retriever=memory_retriever,
        postmortem_buffer=postmortem_buffer,
        reflection_engine=reflection_engine,
        df_mechanics=df_mechanics,
        logs_dir=logs_session_dir,
        spatial_dir=Path("spatial"),
        scratchpad_path=memory_dir / "scratchpad.md",
        asked_topics_path=memory_dir / "asked_topics.json",
    )
    try:
        loop.run()
    finally:
        event_logger.close()
        if recorder is not None:
            recorder.close()
        client.disconnect()


if __name__ == "__main__":
    main()
