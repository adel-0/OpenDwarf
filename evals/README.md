# OpenDwarf Eval Harness

Scenario-based regression testing against real DF save-states.
Part of the flywheel loop described in NORTHSTAR.md section 5 — a skill or
prompt change ships only if the eval suite does not regress.

## How it works

1. You capture a DF save at the scenario's entry point (manual step).
2. The runner spawns an OpenDwarf session against that save with a wall-clock
   limit and evaluates success predicates over the resulting `decisions.jsonl`.
3. Exit code 0 = all predicates passed; 1 = at least one failed.

## How to capture a save

1. Launch Dwarf Fortress in Adventure Mode with DFHack running.
2. Play manually until the adventurer is positioned exactly at the scenario
   entry point (e.g. wolf on adjacent tile, standing outside a shop).
3. Open the DF save menu and save.  The save directory lives at:
   - Linux Steam: `~/.steam/steam/steamapps/common/Dwarf Fortress/data/save/<save_name>/`
   - Note the save name — it goes in the scenario YAML's `save:` field.
4. Note: the DFHack `die` command ends the session cleanly; use the DF menu
   save to capture a mid-scenario state.

Save names expected by the current scenarios:
- `wolf_encounter`    — adventurer in wilderness, wolf adjacent
- `patrol_town`       — adventurer inside a safe town, no hostiles
- `grind_wilderness`  — adventurer in wilderness near tier-1/2 hostiles
- `town_shop_adjacent`— adventurer adjacent to a shop with coins

## Running an eval

Assumes DF + DFHack is already running with the scenario save loaded.

```bash
# Run wolf-survival scenario (interactive: asks you to confirm the save is loaded)
uv run python -m evals.run wolf-survival

# Run with verbose agent logging
uv run python -m evals.run wolf-survival --verbose

# Evaluate an existing session without running the agent again
uv run python -m evals.run --judge-only logs/session_20260611_120000 wolf-survival
```

All options:

```
uv run python -m evals.run --help
```

## Predicate language

Predicates are declared in the YAML scenario spec under `success_predicate:`.
They evaluate over the session's `decisions.jsonl`.

### JSONL event shapes

Two kinds of lines appear in `decisions.jsonl`:

**Decision lines** (no `event` field) — one per LLM call:
```json
{"turn": 5, "tick": 19500, "action": "move_n", "reasoning": "...",
 "llm_ms": 4000, "health_pct": 75, "in_combat": false,
 "position": "(12,34,140)", "site": "WINDY HOLLOW",
 "active_goal": "Find food", "plan_step": null}
```

**Event lines** (have `event` field) — special state transitions:
```json
{"event": "escape_hatch", "turn": 3, "tick": 19480, "focus": ["somescreen/Unknown"], "episode": 1}
{"event": "policy_revised", "turn": 7, "tick": 19550, "diff": {"engage_tier_max": 2}}
{"event": "behavior_suspended", "turn": 20, "tick": 19700, "reason": "HOSTILE_UNHANDLED",
 "digest": "patrol: walked 45 tiles, ate x1 — interrupted by hostile"}
{"event": "behavior_ended", "turn": 45, "tick": 20100, "reason": "done",
 "digest": "grind_combat: killed wolf (2), +1 AXE +1 DODGING — done"}
```

### Leaf predicates

| Predicate | Fields | Description |
|-----------|--------|-------------|
| `survived` | `true`\|`false` | Checks `health_pct > 0` on the last decision line |
| `llm_calls` | `max: N` | Decision lines (LLM calls) <= N |
| `decision_count` | `min`, `max?` | Decision lines in range |
| `event_count` | `type`, `min`, `max?` | Named event lines in range |
| `no_event` | `type`, `max` | Named event lines <= max |
| `action_count` | `action_prefix`, `min`, `max?` | Decision lines whose `action` starts with prefix |
| `skill_level_gained` | `skill`, `min_levels` | Sums `+N SKILL` tokens in behavior digest fields; use `skill: ANY` to count all skills |

### Composition

```yaml
all_of:
  - survived: true
  - llm_calls: 50

any_of:
  - survived: true
  - skill_level_gained:
      skill: AXE
      min_levels: 1
```

Nesting is supported: `any_of` can contain an `all_of` child etc.

## Known JSONL gaps / TODOs

These predicates are limited by what decisions.jsonl currently records.
Do NOT change core code to fix these — add the event when the feature is built.

**`skill_level_gained`**: depends on `+N SKILL` tokens in the `digest` field of
`behavior_ended` / `behavior_suspended` events.  This relies on `EventDigest`
formatting those tokens consistently.  If behavior digests change format, the
predicate silently returns 0 gains.  A dedicated `skill_level_up` event from
`GrindCombatBehavior` would make this robust — tracked in ROADMAP Phase 6.3.

**`buy-item` inventory check**: there is no `inventory_gained` event today.
The `action_count(action_prefix=pickup)` proxy only detects if the agent
issued a `pickup` action, not whether it actually acquired the item.  A
dedicated `item_acquired` event would be cleaner — tracked in ROADMAP Phase 5.4.

**`survived` on timeout**: if the agent is killed by the wall-clock timeout, the
last `health_pct` in the JSONL may be nonzero (the adventurer was alive when
killed).  This is intentional — a timeout is a failure of the LLM to complete
the task, not death of the adventurer.  The `max_wallclock_seconds` limit is the
correct gate for "session too long".

## Scenario files

Located in `evals/scenarios/*.yaml`.  Add new scenarios by copying an existing
YAML and adjusting the predicate.  The runner discovers scenarios by stem name
(no `.yaml` extension needed on the CLI).

## Escape-hatch review (`logs/REVIEW.md`)

The flywheel's *input* side: the eval suite tells you when a change regresses;
the review doc tells you *what to build next*.  It clusters the L3 stall/failure
signals across every session into one promotion queue.

```
uv run python -m evals.review            # writes logs/REVIEW.md
uv run python -m evals.review --stdout   # also print to stdout
```

It scans `logs/*/decisions.jsonl` and clusters three event families, each by the
key that names a distinct problem to fix:

- `escape_hatch` by **focus string** — a hot focus is a candidate for a new
  ActionSpec/Skill or an `_auto_handle` entry (L3→L1 promotion).
- `console_error` by **action** — a hot action has a latent bug in its deferred
  key sequence (silent in RPC; check `stderr.log`).
- `unstick_failed` by **outcome** — a recurring outcome is an `UnstickSkill`
  recovery gap.

Sort by `count`; each row is cleared by shipping code that handles it.  Run it
weekly (manual command is fine).  `logs/` is gitignored, so the report is a
local artifact, not committed.  Note: conversation flail does NOT show up here —
it logs as ordinary decision turns, not stall events.
