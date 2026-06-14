"""Escape-hatch review doc generator (NORTHSTAR M4).

Clusters the L3 stall/failure signals scattered across session logs into a
single promotion queue — ``logs/REVIEW.md`` — so recurring unmodeled screens
and silent input failures become the concrete input list for new skills,
knowledge blocks, and recovery code (the L3→L1 pipeline made reviewable).

Three signal families are clustered, each by the key that identifies a
*distinct problem to fix*:

- ``escape_hatch`` — the LLM landed on an unrecognized viewscreen. Clustered by
  **focus string**: a hot focus is a candidate for a new ActionSpec/Skill or an
  ``_auto_handle`` entry.
- ``console_error`` — a deferred-input callback errored silently in DFHack's
  console (invisible to RPC). Clustered by **action**: a hot action has a
  latent bug in its key sequence.
- ``unstick_failed`` — ``UnstickSkill`` could not recover. Clustered by
  **outcome**: a recurring outcome is a recovery gap.

Run it weekly (or any time):

    uv run python -m evals.review            # writes logs/REVIEW.md
    uv run python -m evals.review --stdout   # also print to stdout
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from evals.predicates import load_events


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

@dataclass
class Cluster:
    """One distinct problem, aggregated across all sessions."""

    key: str
    count: int = 0
    sessions: set[str] = field(default_factory=set)
    # (session, turn) occurrences, newest-last; we keep a bounded sample.
    occurrences: list[tuple[str, int]] = field(default_factory=list)
    # Extra free-text samples (e.g. error messages) for the report.
    samples: list[str] = field(default_factory=list)


def _add(clusters: dict[str, Cluster], key: str, session: str, turn: int,
         sample: str | None = None) -> None:
    c = clusters.setdefault(key, Cluster(key=key))
    c.count += 1
    c.sessions.add(session)
    c.occurrences.append((session, turn))
    if sample and sample not in c.samples and len(c.samples) < 3:
        c.samples.append(sample)


@dataclass
class Review:
    escape_hatch: dict[str, Cluster] = field(default_factory=dict)
    console_error: dict[str, Cluster] = field(default_factory=dict)
    unstick_failed: dict[str, Cluster] = field(default_factory=dict)
    sessions_scanned: int = 0
    total_events: int = 0


def _focus_key(ev: dict) -> str:
    focus = ev.get("focus")
    if isinstance(focus, list):
        return ", ".join(str(f) for f in focus) if focus else "unknown"
    return str(focus) if focus else "unknown"


def collect(logs_dir: Path) -> Review:
    """Walk every ``*/decisions.jsonl`` under *logs_dir* and cluster signals."""
    review = Review()
    for decisions in sorted(logs_dir.glob("*/decisions.jsonl")):
        session = decisions.parent.name
        events = load_events(decisions)
        if not events:
            continue
        review.sessions_scanned += 1
        for ev in events:
            etype = ev.get("event")
            turn = int(ev.get("turn", -1))
            if etype == "escape_hatch":
                review.total_events += 1
                _add(review.escape_hatch, _focus_key(ev), session, turn)
            elif etype == "console_error":
                review.total_events += 1
                errs = ev.get("errors") or []
                sample = errs[0] if errs else None
                _add(review.console_error, str(ev.get("action", "unknown")),
                     session, turn, sample=sample)
            elif etype == "unstick_failed":
                review.total_events += 1
                _add(review.unstick_failed, str(ev.get("outcome", "unknown")),
                     session, turn)
    return review


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_section(title: str, key_label: str, action_hint: str,
                    clusters: dict[str, Cluster]) -> list[str]:
    lines = [f"## {title}", ""]
    if not clusters:
        lines += ["_None recorded._", ""]
        return lines
    lines.append(f"_{action_hint}_")
    lines.append("")
    lines.append(f"| count | sessions | {key_label} | recent (session · turn) |")
    lines.append("|------:|---------:|-------------|--------------------------|")
    ordered = sorted(clusters.values(), key=lambda c: (-c.count, c.key))
    for c in ordered:
        recent = c.occurrences[-3:]
        recent_str = "; ".join(f"{s} · t{t}" for s, t in recent)
        lines.append(
            f"| {c.count} | {len(c.sessions)} | `{c.key}` | {recent_str} |"
        )
        for s in c.samples:
            lines.append(f"|  |  | ↳ _{s}_ |  |")
    lines.append("")
    return lines


def render(review: Review) -> str:
    lines = [
        "# OpenDwarf — Escape-hatch / stall review",
        "",
        f"_Generated {datetime.now():%Y-%m-%d %H:%M} from "
        f"{review.sessions_scanned} session(s); "
        f"{review.total_events} signal event(s)._",
        "",
        "This is the L3→L1 promotion queue: each hot row is a recurring failure "
        "the harness should absorb into a skill, auto-handler, or knowledge block. "
        "Sort by `count`; clear a row by shipping code that handles it.",
        "",
    ]
    lines += _render_section(
        "Unrecognized screens (escape_hatch)", "focus string",
        "Hot focus → new ActionSpec/Skill or an `_auto_handle` entry.",
        review.escape_hatch,
    )
    lines += _render_section(
        "Silent input failures (console_error)", "action",
        "Hot action → latent bug in its deferred key sequence; check stderr.log.",
        review.console_error,
    )
    lines += _render_section(
        "Recovery gaps (unstick_failed)", "outcome",
        "Hot outcome → UnstickSkill can't recover this; needs a targeted path.",
        review.unstick_failed,
    )
    if review.total_events == 0:
        lines += [
            "---",
            "",
            "No stall signals recorded yet — either runs have been clean or the "
            "agent stalls in a way that isn't an unrecognized screen "
            "(e.g. conversation flail, which logs as normal decision turns).",
            "",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs-dir", default="logs", type=Path,
                        help="Directory of session_* dirs (default: logs).")
    parser.add_argument("--out", default=None, type=Path,
                        help="Output path (default: <logs-dir>/REVIEW.md).")
    parser.add_argument("--stdout", action="store_true",
                        help="Also print the report to stdout.")
    args = parser.parse_args()

    logs_dir: Path = args.logs_dir
    out: Path = args.out or (logs_dir / "REVIEW.md")

    review = collect(logs_dir)
    report = render(review)
    out.write_text(report, encoding="utf-8")
    print(f"Wrote {out} "
          f"({review.sessions_scanned} sessions, {review.total_events} signal events).")
    if args.stdout:
        print()
        print(report)


if __name__ == "__main__":
    main()
