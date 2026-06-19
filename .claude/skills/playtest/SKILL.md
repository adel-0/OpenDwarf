---
name: playtest
description: Run a long unattended LLM play session against the live DF, recording everything, then analyze where the agent actually spent its turns and got stuck. Use when the user says "do a playtest", "let it play for N minutes and analyze", or "/playtest <duration>".
---

# Long recorded play session + analysis

Let the real LLM agent play live for a bounded time, capture a full tape, then
analyze what actually happened. This is the project's missing **validated-gameplay**
loop (see the analysis: the agent has lots of unit-tested machinery but little
proven long-run behavior). The point is to let *reality* re-prioritize the roadmap,
not to verify a single feature.

## 1. Parse the duration
The argument is a wall-clock budget like `90m`, `2h`, `45m` (default `60m` if
omitted). Convert to seconds. This is how long the agent is allowed to play before
you stop it.

## 2. Pre-flight
- Confirm DF + DFHack is up and in adventure mode: `uv run python -m opendwarf.dev state`
  should print a live GameState summary. If it errors, stop and tell the user to
  load an adventurer.
- Note the starting situation (site, health, goal) so the analysis has a baseline.

## 3. Launch the recorded run
Run the agent in the **background** with recording on, so every DFHack call lands
in a replayable tape alongside the decision log:

```
uv run python -m opendwarf.main --record --verbose
```

Capture the session dir it prints (`logs/session_<ts>/`). The run writes
`decisions.jsonl` (LLM decisions + events) and `tape.jsonl` (the DFHack I/O tape).

## 4. Let it run, then stop
Wait out the duration budget (poll occasionally; do not babysit per-turn). When the
budget is spent — or the agent exits early (death / crash) — stop the background
process cleanly (SIGINT so `_on_session_end` flushes reflection + saves maps).
If it died on its own, note that: a real death is a *finding*, not a failure.

## 5. Analyze the session
Work only from the recorded artifacts — do not re-run. Produce, in plain numbers:
- **Action distribution**: count `action` values in `decisions.jsonl`. What did it
  actually spend turns on? (The prior finding is conversation/`press`/`escape`
  dominate and `attack` never fires — confirm or refute.)
- **Turn cost**: LLM calls, median `llm_ms`, total turns vs. wall-clock. How close to
  the NORTHSTAR ">95% of actions cost zero tokens" target is it really?
- **Autopilot reach**: `behavior_suspended` / `behavior_ended` events — did any
  behavior run, for how many actions, and what interrupted it?
- **Stalls & escape hatches**: `escape_hatch`, `console_error`, `unstick_failed`,
  `STALLED` interrupts, banned-action churn. Where did it get stuck and why?
- **Progress**: did `site`/`position`/`health`/goals/skills change meaningfully, or
  did it spin in place? Did it reach any hostile, any new site, any quest?
- Run `uv run python -m evals.review` to fold the L3 signals into `logs/REVIEW.md`.

## 6. Report + reprioritize
Three parts:
1. **What happened** — a 5–8 line factual narrative of the run.
2. **The binding constraint** — the single thing that wasted the most turns. Name it.
3. **Roadmap implication** — what this says the *next* concrete build should be, and
   whether any "DONE/PARTIAL" roadmap item is contradicted by what the run showed.
Keep the tape (`tape.jsonl`) referenced by path — it is a reusable regression fixture.
Do NOT edit code in this skill; its output is findings, and it feeds `/next`.
