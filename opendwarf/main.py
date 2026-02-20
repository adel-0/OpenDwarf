"""OpenDwarf entry point."""

from __future__ import annotations

import argparse
import logging
import sys

from opendwarf.agent.decision import AnthropicLLM, SimulatedLLM, TacticalLoop
from opendwarf.dfhack.client import DFHackClient
from opendwarf.dfhack.lua_executor import LuaExecutor


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenDwarf — AI agent for Dwarf Fortress")
    parser.add_argument("--host", default="127.0.0.1", help="DFHack RPC host")
    parser.add_argument("--port", type=int, default=5000, help="DFHack RPC port")
    parser.add_argument("--timeout", type=float, default=10.0, help="RPC timeout in seconds")
    parser.add_argument("--simulate", action="store_true", help="Use simulated LLM instead of Anthropic API")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001", help="Anthropic model to use")
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

    # Choose LLM backend
    if args.simulate:
        llm = SimulatedLLM()
    else:
        llm = AnthropicLLM(args.model)

    # Run the tactical loop
    loop = TacticalLoop(lua, llm)
    try:
        loop.run()
    finally:
        client.disconnect()


if __name__ == "__main__":
    main()
