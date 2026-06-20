#!/usr/bin/env python3
"""Block until OpenDwarf posts a decision request, then print it.

Used by the external brain agent. Prints a header line ``TURN <n> CALLER <c>``
followed by the full system+user prompt the game wants a decision for. Exits 0
on a request, exit 2 if a stop sentinel (``logs/bridge/STOP``) appears first,
exit 3 on overall timeout (no request for --idle seconds).

Usage: python scripts/bridge_wait.py [--idle 120]
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

BRIDGE = Path(os.getenv("OPENDWARF_BRIDGE_DIR", "logs/bridge"))


def pending() -> Path | None:
    reqs = sorted(BRIDGE.glob("req_*.json"), key=lambda p: int(p.stem.split("_")[1]))
    for r in reqs:
        if not (BRIDGE / f"resp_{r.stem.split('_')[1]}.json").exists():
            return r
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--idle", type=float, default=180.0,
                    help="exit 3 if no request appears within this many seconds")
    ap.add_argument("--dynamic-only", action="store_true",
                    help="print only the per-turn prompt (skip the static system prefix)")
    args = ap.parse_args()
    BRIDGE.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.idle
    while time.monotonic() < deadline:
        if (BRIDGE / "STOP").exists():
            print("STOP", file=sys.stderr)
            return 2
        r = pending()
        if r:
            try:
                req = json.loads(r.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                time.sleep(0.2)
                continue
            print(f"TURN {req['turn']} CALLER {req['caller']}")
            if not args.dynamic_only:
                print(req["system"])
                print("\n===== TURN PROMPT =====\n")
            print(req["user"])
            return 0
        time.sleep(0.4)
    print("IDLE_TIMEOUT", file=sys.stderr)
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
