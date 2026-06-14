---
name: next
description: Implement the next open OpenDwarf roadmap item end-to-end — design, build, unit-test, live-verify against the running DF, commit, and update ROADMAP. Use when the user says "do the next roadmap item", "implement the next thing", or "/next".
---

# Implement the next OpenDwarf roadmap item

Drive one roadmap item from open to committed. DF + DFHack are always running for live testing.

## 1. Pick the item
Read `ROADMAP.md` → the **"Currently open"** block (top) and the gap-analysis table. Take the first item unless the user named one. If `NORTHSTAR.md` has a spec for it (Part II milestones M1–M5), read that section too. If the item depends on an unfinished prerequisite (e.g. combat grind needs a live hostile), say so and pick the unblocking item instead.

## 2. State scope before building
In one or two lines: what you're implementing and **what you are not touching**. Honor the project's standing constraints — the runtime agent has only the harness prompt (no filesystem); no streaming/Twitch features; no fallbacks without a concrete failure mode to guard. If the scope is genuinely ambiguous, ask; otherwise proceed.

## 3. Build it
Write the code to shippable completion — no half-finished limbs. Match the surrounding code's idiom. New DF capability = one new `ActionSpec`/`Skill`/`Behavior` in the registry, not a special case in the loop. Prefer the simplest level that works (L0 auto-handler < L1 skill < L2 knowledge < L3 escape hatch). You may spawn a Sonnet 4.6 agent to build to save on tokens, but guide it well.

## 4. Unit-test
Add or extend tests. Run the full suite (`uv run pytest -q`) and report the passing count. Don't proceed if it regresses.

## 5. Live-verify
Exercise the change against the running DF via DFHack (a scratch script through `DFHackClient`/`LuaExecutor`, or the agent loop). Anything marked **LIVE-VERIFY** must actually run in-game. Be explicit about what was and was NOT exercised live (e.g. "SEEK + pathing verified; no hostile was present, so ENGAGE is still unexercised"). Don't claim a live encounter happened if it didn't.

## 6. Commit + record
Conventional commit (`feat(...)`/`fix(...)`). Append the co-author line. Update `ROADMAP.md`'s "Currently open" block (remove or amend the item); if a stable empirical DF fact was discovered, add it to CLAUDE.md (architecture-as-built) — not a progress note in ROADMAP.

## 7. Report
Three lines: what landed, what is still pending LIVE-VERIFY, and what the next open item is.
