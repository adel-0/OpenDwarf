"""Developer probe CLI — make DFHack and the df-structures schema queryable.

This is the dev-time counterpart to the runtime agent: a fast way to "understand
the game and DFHack" without writing a throwaway script each time. Two kinds of
subcommands:

  Live (need DF + DFHack running):
    state              live GameState.summary()
    ui                 inspect_ui() snapshot (viewscreen stack, menu, travel, focus)
    keys <pattern>     enumerate df.interface_key names matching <pattern>
    screen             extract_screen_text() rows + focus

  Offline (no DFHack — read the pinned df-structures schema on disk):
    schema <pattern>   grep sources/df-structures/*.xml for a struct/enum/field name

The `schema` command is the standing wire-in for the df-structures submodule: it
keeps the version-exact struct/enum definitions one command away, so guessed field
paths and enum values get checked against ground truth instead of live-probed.

Usage:
    uv run python -m opendwarf.dev state
    uv run python -m opendwarf.dev keys TRAVEL
    uv run python -m opendwarf.dev schema adventure_game_loop_type
    uv run python -m opendwarf.dev schema 'counters2'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "sources" / "df-structures"


# ---------------------------------------------------------------------------
# Live subcommands (DFHack)
# ---------------------------------------------------------------------------

def _connect(args: argparse.Namespace):
    from opendwarf.dfhack.client import DFHackClient
    from opendwarf.dfhack.lua_executor import LuaExecutor

    client = DFHackClient(args.host, args.port, args.timeout)
    client.connect()
    lua = LuaExecutor(client, args.scripts_dir)
    lua.deploy_scripts()
    return client, lua


def cmd_state(args: argparse.Namespace) -> int:
    from opendwarf.state.game_state import GameState

    client, lua = _connect(args)
    try:
        state = GameState.from_raw(lua.extract_state())
        print(state.summary())
    finally:
        client.disconnect()
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    client, lua = _connect(args)
    try:
        print(json.dumps(lua.inspect_ui(), indent=2, default=str))
    finally:
        client.disconnect()
    return 0


def cmd_keys(args: argparse.Namespace) -> int:
    client, lua = _connect(args)
    try:
        keys = lua.find_keys(args.pattern)
        print(f"{len(keys)} interface_key(s) matching {args.pattern!r}:")
        for k in keys:
            print(f"  {k}")
    finally:
        client.disconnect()
    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    client, lua = _connect(args)
    try:
        data = lua.extract_screen_text()
        print(f"focus: {data.get('focus')}")
        for row in data.get("rows", []):
            print(f"  {row}")
    finally:
        client.disconnect()
    return 0


# ---------------------------------------------------------------------------
# Offline subcommand (df-structures schema)
# ---------------------------------------------------------------------------

def cmd_schema(args: argparse.Namespace) -> int:
    if not _SCHEMA_DIR.is_dir():
        print(
            f"ERROR: df-structures not found at {_SCHEMA_DIR}.\n"
            "Run: git submodule update --init sources/df-structures",
            file=sys.stderr,
        )
        return 2
    pattern = args.pattern.lower()
    hits = 0
    for xml in sorted(_SCHEMA_DIR.glob("*.xml")):
        try:
            lines = xml.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            if pattern in line.lower():
                print(f"{xml.name}:{n}: {line.strip()}")
                hits += 1
                if hits >= args.limit:
                    print(f"... (stopped at {args.limit} hits; raise --limit for more)")
                    return 0
    if hits == 0:
        print(f"no schema matches for {args.pattern!r} in {_SCHEMA_DIR.name}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenDwarf developer probe CLI", prog="opendwarf.dev"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--scripts-dir", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("state", help="live GameState.summary()").set_defaults(fn=cmd_state)
    sub.add_parser("ui", help="inspect_ui() snapshot").set_defaults(fn=cmd_ui)
    sub.add_parser("screen", help="extract_screen_text() rows").set_defaults(fn=cmd_screen)

    p_keys = sub.add_parser("keys", help="enumerate df.interface_key names")
    p_keys.add_argument("pattern")
    p_keys.set_defaults(fn=cmd_keys)

    p_schema = sub.add_parser("schema", help="grep df-structures XML for a name")
    p_schema.add_argument("pattern")
    p_schema.add_argument("--limit", type=int, default=60)
    p_schema.set_defaults(fn=cmd_schema)

    args = parser.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
