"""Deploy and execute Lua scripts via DFHack RPC."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from opendwarf.dfhack.client import DFHackClient

logger = logging.getLogger(__name__)

SCRIPT_PREFIX = "opendwarf--"
LUA_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "lua_scripts"


class LuaExecutor:
    """Manages Lua script deployment and execution."""

    def __init__(self, client: DFHackClient, dfhack_scripts_dir: str | Path | None = None):
        self.client = client
        # If not overridden, resolve from the live DFHack install (works across
        # platforms / Steam vs. classic layouts). Falls back to project-local dir.
        self.scripts_dir = Path(dfhack_scripts_dir) if dfhack_scripts_dir else None

    def _resolve_scripts_dir(self) -> Path | None:
        if self.scripts_dir is not None:
            return self.scripts_dir
        try:
            out = self.client.run_command("lua", ["print(dfhack.getHackPath())"])
            hack_path = "".join(out).strip()
            if hack_path:
                self.scripts_dir = Path(hack_path) / "scripts"
                logger.info("Resolved DFHack scripts dir: %s", self.scripts_dir)
                return self.scripts_dir
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not query DFHack for scripts path: %s", exc)
        fallback = Path(__file__).parent.parent.parent / "game" / "hack" / "scripts"
        self.scripts_dir = fallback
        return fallback

    def deploy_scripts(self) -> None:
        """Copy all Lua scripts from lua_scripts/ to DFHack's scripts dir."""
        if not LUA_SCRIPTS_DIR.exists():
            logger.warning("Lua scripts directory not found: %s", LUA_SCRIPTS_DIR)
            return
        self._resolve_scripts_dir()
        for src in LUA_SCRIPTS_DIR.glob("*.lua"):
            dst = self.scripts_dir / src.name
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("Deployed %s -> %s", src.name, dst)

    def run_script(self, script_name: str, args: list[str] | None = None) -> list[str]:
        """Run a deployed script as a DFHack command. Returns text output lines."""
        # Strip .lua suffix and prefix for the command name
        cmd = script_name.removesuffix(".lua")
        return self.client.run_command(cmd, args)

    def extract_state(self) -> dict:
        """Run the state extraction script and parse JSON output."""
        lines = self.run_script(f"{SCRIPT_PREFIX}state")
        text = "\n".join(lines)
        # Find the JSON object in the output (skip any non-JSON preamble)
        start = text.find("{")
        if start == -1:
            raise ValueError(f"No JSON found in state output: {text[:200]}")
        return json.loads(text[start:])

    def extract_map(self, radius: int = 40) -> dict:
        """Run the wide map extraction script and parse JSON output."""
        lines = self.run_script(f"{SCRIPT_PREFIX}map", [str(radius)])
        text = "\n".join(lines)
        start = text.find("{")
        if start == -1:
            raise ValueError(f"No JSON found in map output: {text[:200]}")
        return json.loads(text[start:])

    def execute_action(self, action: str) -> list[str]:
        """Run the action execution script with an action string."""
        return self.run_script(f"{SCRIPT_PREFIX}act", [action])

    def extract_screen_context(self) -> dict:
        """Alias for extract_state — structured context."""
        return self.extract_state()
