"""OpenDwarf entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv

from opendwarf.agent.decision import AzureOpenAILLM, TacticalLoop
from opendwarf.dfhack.client import DFHackClient
from opendwarf.dfhack.lua_executor import LuaExecutor


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="OpenDwarf — AI agent for Dwarf Fortress")
    parser.add_argument("--host", default="127.0.0.1", help="DFHack RPC host")
    parser.add_argument("--port", type=int, default=5000, help="DFHack RPC port")
    parser.add_argument("--timeout", type=float, default=10.0, help="RPC timeout in seconds")
    parser.add_argument("--scripts-dir", default=None, help="DFHack scripts directory override")
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

    # Run the tactical loop
    loop = TacticalLoop(lua, llm)
    try:
        loop.run()
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
