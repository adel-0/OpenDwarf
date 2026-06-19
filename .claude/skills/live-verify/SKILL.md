---
name: live-verify
description: Drive one LIVE-VERIFY backlog item to a verified-or-refuted state against the running DF — pick the item, confirm DFHack is connected, run the right exercise (scratch script / eval scenario / agent loop), capture real evidence, and record the outcome honestly. Use when the user says "live-verify X", "verify against DF", "run the wolf scenario", or "/live-verify".
---

# Live-verify an OpenDwarf capability against the running game

Most of ROADMAP's "Currently open" block is **built but unverified** — code + unit
tests exist, but the limb has never run end-to-end in real DF. This skill closes
one such gap. It is verify-only: `/next` is build→commit (live-verify is one step
of seven); this drives an already-built capability to a live verdict.

DF is not always on the dev laptop. If it isn't reachable, prepare the exercise
(scratch script, scenario yaml, save-capture notes) and say clearly that the live
run is deferred — do **not** mark anything verified.

## 1. Pick the item
Read `ROADMAP.md` → "Currently open" and the gap table; take the first item tagged
**LIVE-VERIFY** unless the user named one (e.g. full combat grind on a hostile,
full journey to a distant site, death/postmortem focus string, eval save-states).
If `NORTHSTAR.md` Part II specs it (M1–M5), read that section.

## 2. Confirm DF + DFHack is reachable
Before anything else, prove the connection — don't assume:
```python
from opendwarf.dfhack.client import DFHackClient
from opendwarf.dfhack.lua_executor import LuaExecutor
c = DFHackClient("127.0.0.1", 5000); c.connect()
lua = LuaExecutor(c); print(lua.inspect_ui())   # focus, menu, viewscreen stack
```
If this fails or hangs, STOP: report that DF is unreachable and the verification is
deferred. (RPC can hang on script errors — every call needs a timeout; reconnect on
failure. See CLAUDE.md → RPC Protocol.)

## 3. Choose the exercise mechanism (cheapest that proves it)
- **Scratch script via `LuaExecutor`** — for protocol/API/empirical facts (a focus
  string, a key name, a menu's field layout). Probe live; enumerate `df.interface_key`
  with `lua.find_keys(<pattern>)` rather than trusting the wiki.
- **Eval scenario** — for a behavior/skill end-to-end:
  `uv run python -m evals.run <scenario>` (live) or
  `uv run python -m evals.run --judge-only <session_dir> <scenario>` (re-judge a run).
  Scenarios live in `evals/scenarios/*.yaml`; predicates score `decisions.jsonl`.
- **Full agent loop** — `uv run python -m opendwarf.main --verbose` — when the gap is
  emergent (does the agent get *out of town*, does the curriculum shift focus over a
  session). Watch `logs/<session>/decisions.jsonl` + `goal_events.jsonl`.

## 4. Capture the save-state if the scenario needs one (the standing ops gap)
Eval scenarios name a DF save (e.g. `wolf_encounter`) that must exist on the DF box;
none are committed. To capture one: in DF, set up the situation (adventurer beside a
wolf / in a town / adjacent to a shop), `quicksave` or region-save via DFHack, note
the save folder name, and set it as `save:` in the scenario yaml. Build
`wolf-survival` first — it gates the M2 overnight grind. Record which saves now exist.

## 5. Run and capture real evidence
Run the exercise. Capture the **actual** DF output: the combat-log line, the focus
string, the session JSONL counts (LLM calls, strikes, level-ups), the behavior
digest. Quote it. If the RPC hung or the run died, say so and what you saw before it.

## 6. Record the verdict honestly
- Be explicit about what ran live and what did **not** (e.g. "SEEK + approach + 13
  strikes verified on a neutral wolf; no *hostile* encounter, so danger/flee/`in_combat`
  semantics remain unexercised"). Never claim a live encounter that didn't happen.
- Update `ROADMAP.md` "Currently open": amend or remove the item; note any new
  remaining gap discovered.
- A stable empirical DF fact (focus string, menu protocol, key name) goes in
  **CLAUDE.md** (architecture-as-built), not as a progress note in ROADMAP.
- If the run surfaced a bug, fix it (small) or log it for `/next`.

## 7. Report
Three lines: what is now LIVE-VERIFIED (with the evidence), what is still pending and
why, and the next LIVE-VERIFY item in the queue.
