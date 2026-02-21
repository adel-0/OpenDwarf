"""OpenDwarf entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from opendwarf.agent.decision import AzureOpenAILLM, TacticalLoop
from opendwarf.dfhack.client import DFHackClient
from opendwarf.dfhack.lua_executor import LuaExecutor
from opendwarf.goals.manager import GoalManager
from opendwarf.goals.model import Goal, GoalStatus, GoalType
from opendwarf.planning.strategic import StrategicPlanner


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
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
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

    # Set up Lua executor and deploy scripts
    lua = LuaExecutor(client, args.scripts_dir)
    lua.deploy_scripts()

    llm = AzureOpenAILLM()

    # Set up goal management (Layer 3) and strategic planning (Layer 2)
    goals_dir = Path(args.goals_dir)
    goal_manager = GoalManager(llm, goals_dir)
    strategic_planner = StrategicPlanner(llm)

    # Seed an initial goal from CLI if provided and no goals already exist
    if args.goal and not goal_manager.active_goals() and not goal_manager.candidate_goals():
        seed = Goal.new(
            description=args.goal,
            type=GoalType.NARRATIVE,
            priority=0.7,
            created_tick=0,
            status=GoalStatus.ACTIVE,
        )
        goal_manager.add(seed)
        goal_manager.save()
        logging.getLogger(__name__).info("Seeded initial goal: %s", args.goal)

    # Run the tactical loop
    loop = TacticalLoop(
        lua, llm,
        goal_manager=goal_manager,
        strategic_planner=strategic_planner,
    )
    try:
        loop.run()
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
