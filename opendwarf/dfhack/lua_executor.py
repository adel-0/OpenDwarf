"""Deploy and execute Lua scripts via DFHack RPC."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from opendwarf.dfhack.client import DFHackClient

logger = logging.getLogger(__name__)

SCRIPT_PREFIX = "opendwarf--"
LUA_SCRIPTS_DIR = Path(__file__).parent.parent.parent / "lua_scripts"

# Default DFHack console log path on Steam Linux.
# Resolved once at import time for performance; override via DFHACK_CONSOLE_LOG env var.
_DF_STEAM_DIR = Path.home() / ".steam/debian-installation/steamapps/common/Dwarf Fortress"
DFHACK_CONSOLE_LOG = os.environ.get(
    "DFHACK_CONSOLE_LOG",
    str(_DF_STEAM_DIR / "stderr.log"),
)


class LuaExecutor:
    """Manages Lua script deployment and execution."""

    def __init__(self, client: DFHackClient, dfhack_scripts_dir: str | Path | None = None,
                 console_log: str | None = None):
        self.client = client
        # If not overridden, resolve from the live DFHack install (works across
        # platforms / Steam vs. classic layouts). Falls back to project-local dir.
        self.scripts_dir = Path(dfhack_scripts_dir) if dfhack_scripts_dir else None
        # Console log for capturing DFHack printerr / deferred callback errors.
        self.console_log = console_log or DFHACK_CONSOLE_LOG
        # Offset holder: [byte_offset]. Initialised to the end of the log so we
        # only capture *new* errors from this session onward.
        self._console_offset: list[int] = self.console_log_offset(self.console_log)

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

    @staticmethod
    def _extract_json(lines: list[str]) -> dict | None:
        """Parse the JSON object embedded in script output (from the first '{').

        DFHack may emit non-JSON preamble before the payload. Returns None if no
        JSON object is present or it fails to parse, so soft-failing callers can
        substitute a default and hard-failing callers can raise.
        """
        text = "\n".join(lines)
        start = text.find("{")
        if start == -1:
            return None
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            return None

    def extract_state(self) -> dict:
        """Run the state extraction script and parse JSON output."""
        lines = self.run_script(f"{SCRIPT_PREFIX}state")
        data = self._extract_json(lines)
        if data is None:
            raise ValueError(f"No JSON found in state output: {' '.join(lines)[:200]}")
        return data

    def extract_map(self, radius: int = 40) -> dict:
        """Run the wide map extraction script and parse JSON output."""
        lines = self.run_script(f"{SCRIPT_PREFIX}map", [str(radius)])
        data = self._extract_json(lines)
        if data is None:
            raise ValueError(f"No JSON found in map output: {' '.join(lines)[:200]}")
        return data

    def resolve_site(self, name: str) -> list[dict]:
        """Look up sites whose name contains `name` against the full world site
        list. Returns match dicts (id, name, type, world_x, world_y, distance),
        nearest first. Used to turn a rumored site name into a journey target.
        Returns [] on any failure (never raises into the tactical loop)."""
        if not name or not name.strip():
            return []
        try:
            lines = self.run_script(f"{SCRIPT_PREFIX}resolve-site", name.strip().split())
            return (self._extract_json(lines) or {}).get("matches", [])
        except Exception:
            logger.exception("resolve_site(%r) failed", name)
            return []

    def execute_action(self, action: str) -> list[str]:
        """Run the action execution script with an action string.

        Records the console log offset *before* scheduling the deferred input so
        that a subsequent call to ``consume_action_errors()`` can return only the
        errors produced by this action.
        """
        # Snapshot offset before scheduling the deferred callback.
        try:
            self._console_offset = self.console_log_offset(self.console_log)
        except Exception:  # noqa: BLE001
            pass
        result = self.run_script(f"{SCRIPT_PREFIX}act", [action])
        # Eagerly check for synchronous errors (e.g. unknown key) reported in the
        # script's own print() output — these are not in the console log.
        for line in result:
            if line.startswith("ERROR:"):
                logger.warning("execute_action(%s): %s", action, line)
        return result

    def consume_action_errors(self, wait_s: float = 0.0) -> list[str]:
        """Return ERROR/printerr lines written to the DFHack console log since the
        last ``execute_action`` call.

        Caller should invoke this *after* the post-action wait so the deferred
        callback has had time to run and flush its output.

        Returns a (possibly empty) list of stripped error strings.
        """
        if wait_s > 0:
            import time
            time.sleep(wait_s)
        return LuaExecutor.consume_console_errors(self.console_log, self._console_offset)

    def extract_screen_text(self) -> dict:
        """Read the current screen focus strings and visible text rows."""
        lines = self.run_script(f"{SCRIPT_PREFIX}screen")
        return self._extract_json(lines) or {"focus": [], "rows": []}

    def extract_screen_context(self) -> dict:
        """Alias for extract_state — structured context."""
        return self.extract_state()

    def inspect_ui(self) -> dict:
        """Return a structured snapshot of the current UI state.

        Includes: viewscreen stack types, focus strings, adventure menu,
        player_control_state, travel fields, gps dims, current message.
        All fields are wrapped in pcall on the Lua side; missing fields are null.
        Read-only, side-effect-free, <0.1s.
        """
        lines = self.run_script(f"{SCRIPT_PREFIX}ui")
        data = self._extract_json(lines)
        if data is None:
            logger.warning("inspect_ui: no/invalid JSON in output: %s", "\n".join(lines)[:200])
            return {}
        return data

    def find_keys(self, pattern: str) -> list[str]:
        """Return df.interface_key names (from the live DFHack enum) containing pattern.

        Comparison is case-insensitive. Returns an empty list on any failure.
        Read-only, side-effect-free.
        """
        lines = self.run_script(f"{SCRIPT_PREFIX}ui", ["keys", pattern])
        text = " ".join(lines).strip()
        if not text:
            return []
        return [k for k in text.split() if k]

    @staticmethod
    def consume_console_errors(log_path: str, offset_holder: list[int]) -> list[str]:
        """Return new ERROR/printerr lines from DFHack's console log since the last call.

        ``offset_holder`` is a one-element list holding the byte offset from which
        to start reading on the next call (pass-by-reference idiom for a mutable
        int).  Callers must initialise it as ``[0]`` or, better, call
        ``console_log_offset(log_path)`` to start at the current end of the file.

        Returns a (possibly empty) list of stripped error lines.
        """
        try:
            with open(log_path, "rb") as fh:
                fh.seek(offset_holder[0])
                chunk = fh.read()
                offset_holder[0] = offset_holder[0] + len(chunk)
            text = chunk.decode("utf-8", errors="replace")
            errors: list[str] = []
            for line in text.splitlines():
                stripped = line.strip()
                if stripped and (
                    "error" in stripped.lower()
                    or "printerr" in stripped.lower()
                    or stripped.startswith("opendwarf--")
                ):
                    errors.append(stripped)
            return errors
        except OSError:
            return []

    @staticmethod
    def console_log_offset(log_path: str) -> list[int]:
        """Return a fresh offset_holder initialised to the current end of log_path.

        Use this to start capturing *new* errors only (ignore historical content).
        """
        try:
            import os
            return [os.path.getsize(log_path)]
        except OSError:
            return [0]
