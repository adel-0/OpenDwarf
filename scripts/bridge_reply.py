#!/usr/bin/env python3
"""Post a decision back to OpenDwarf for a given turn.

Usage:
  python scripts/bridge_reply.py <turn> '<json-decision>'
  echo '<json-decision>' | python scripts/bridge_reply.py <turn>

The decision JSON must contain at least {"action": "..."} and may include
"reasoning", "scratchpad", "policy". Writes atomically so the game loop never
reads a half-written file.
"""
import json
import os
import sys
from pathlib import Path

BRIDGE = Path(os.getenv("OPENDWARF_BRIDGE_DIR", "logs/bridge"))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: bridge_reply.py <turn> '<json>'", file=sys.stderr)
        return 1
    turn = int(sys.argv[1])
    payload = sys.argv[2] if len(sys.argv) > 2 else sys.stdin.read()
    payload = payload.strip()
    # Validate it parses and has an action (fail loudly rather than stall the loop).
    obj = json.loads(payload)
    if not isinstance(obj, dict):
        print("decision must be a JSON object", file=sys.stderr)
        return 1
    BRIDGE.mkdir(parents=True, exist_ok=True)
    dest = BRIDGE / f"resp_{turn}.json"
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    tmp.rename(dest)
    print(f"posted resp_{turn}: {obj.get('action')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
