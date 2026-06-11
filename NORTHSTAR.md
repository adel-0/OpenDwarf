# North Star Architecture — from "agent that takes turns" to "agent that lives a life"

**Ambition**: an autonomous LLM adventurer that gets strong, descends through the caverns, and kills demons in the underworld. This document is the architecture that makes that physically possible, and the honest accounting of why the current shape of the harness — however correct each piece is — cannot get there by itself.

*Research grounding*: **RESEARCH.md** (2026-06-10) audits this plan against the published state of the art (BALROG/NetHack, Voyager, Cradle, Pokémon agents) and first principles. Verdict: architecture confirmed; four deltas adopted and folded into Part II below (Tactician→Director escalation, state-delta progress watchdog, postmortems moved into M2, world-selection lever).

## 1. The constraint that decides everything: decision throughput

Becoming legendary in DF is a *volume* problem. Grinding combat skills to legendary, traveling between dozens of sites, fighting hundreds of encounters, descending three cavern layers — that is on the order of **10⁴–10⁵ game actions**.

Measured today: a turn that consults the LLM costs ~5–15s wall-clock (LLM latency + deferred-input waits). At LLM-per-turn, 50k actions ≈ **70–200 hours of wall-clock for one life**, most of it spent re-deciding things that were never in question ("keep sparring", "keep walking west", "keep drinking from the river").

So the architecture question is not "how does the LLM pick better actions?" but:

> **What fraction of game actions require zero LLM tokens?**

For the ambition to be reachable, the answer must be **> 95%**. Everything below follows from that number.

## 2. What survives from the current harness (most of it)

No sunk-cost fallacy — but also no novelty bias. Audit of what we have:

| Layer | Verdict |
|-------|---------|
| DFHack RPC + Lua deployment (`dfhack/`) | **Keep.** Months of empirical traps encoded (header format, deferred simulateInput, v53 API). Any rewrite re-pays this cost for zero gain. |
| ChunkMap + A* + wide extraction (`spatial/`) | **Keep.** Exactly what an autopilot needs. |
| ActionSpec registry + skills (`actions/`) | **Keep as the instruction set.** Becomes the target that behaviors compile down to. |
| PromptBundle / providers / observability / memory | **Keep.** |
| Tactical loop (`agent/loop.py`) | **Restructure.** Its core assumption — *no active skill ⇒ ask the LLM* — is the throughput killer. |
| GoalManager | **Absorb** into the Director (below). Goals/plan-steps survive as data model. |

The harness is not wrong; it is **one layer short**. We built the spinal cord and the consciousness, and skipped the cerebellum — the thing that handles practiced activity without thinking.

## 3. The missing layer: Behaviors under Policy

A **Behavior** is a long-running deterministic controller (minutes–hours of game time), parameterized by a **Policy** the LLM writes. It is to a Skill what a Skill is to a keypress.

```
keypress (ms)  <  Skill (seconds: one route, one menu)  <  Behavior (minutes-hours)
```

Behaviors needed for the north star (each composes existing skills/intents):

- `grind_combat(area, target_tiers, until: skill_levels | duration)` — pick fights the policy allows, attack/flee per policy, eat/drink/sleep as needed, loop. *This single behavior is most of "getting strong".*
- `journey(dest, provisioning_policy)` — fast travel + local navigation + encounter handling + forage/refill water along the way.
- `provision(food/water/ammo targets)` — hunt, butcher, fill waterskin, buy.
- `descend(until: cavern_layer | feature_found)` — systematic stair/passage search downward with retreat thresholds.
- `clear_site(site, rules_of_engagement)` — sweep, fight per policy, loot list.

A **Policy** is a structured standing order, written/revised by the LLM, executed by code — *the LLM stops being the actor and becomes the author of the actor*:

```json
{
  "engage_if": {"tier_max": 2, "max_opponents": 2, "health_min": 0.6},
  "flee_if":   {"health_below": 0.4, "opponents_over": 2, "tier_over": 2},
  "physio":    {"eat_at": "hungry", "drink_at": "thirsty", "sleep": "safe_indoors_only"},
  "loot":      ["weapons_better_than_current", "food", "coins"],
  "never":     ["steal_in_sites", "fight_in_water", "sleep_outdoors_at_night"]
}
```

**Interrupts** are the contract that keeps the LLM in command: a behavior runs silently until an *interrupt condition* fires — new hostile above policy tier, health threshold, policy has no applicable rule, unknown screen (existing escape hatch), named NPC encountered, behavior target achieved/impossible. Then and only then does an LLM turn happen, with full context including "what the autopilot did while you were away" (compressed event digest). The existing survival gates are already interrupt conditions in embryo.

This is ~the difference between 100% and <5% of turns consuming tokens — it turns the 200-hour life into a game-speed life with occasional thought.

## 4. Control hierarchy (revised, with explicit time scales)

| Level | Cadence | Model | Role |
|-------|---------|-------|------|
| **Director** | Hours of game time / major events | strongest available | Campaign: "become legendary axe + shield", "find cavern entrance", "descend". Owns the life plan, postmortems, what to grind and why. Absorbs today's GoalManager. |
| **Tactician** | Interrupts only | mid/cheap (deepseek-flash class) | Resolve the interrupt: pick fight/flee/divert, revise the active policy, choose conversation lines, drive the escape hatch. Today's tactical LLM turn, demoted from "every turn" to "exceptions". |
| **Behaviors** | Continuous | none | Autopilot under policy (section 3). |
| **Skills / keys / auto-handlers** | Sub-second | none | Exactly today's L0/L1. |

Model tiering matters for cost as much as latency: the Director can be Opus-class
because it speaks rarely; the Tactician must be fast-and-cheap because interrupts
cluster (today's OpenRouter provider exists for exactly this).

## 5. The flywheel: the harness that grows itself

Hand-enumerating DF's action surface is a losing game — the wiki is a thousand pages and the game generates novelty. The escape hatch (already built) is the sensor; what's missing is the loop that *consumes* it:

1. **Runtime**: unmodeled situation → escape-hatch episode (logged, already
   distinct in JSONL) → Tactician improvises with raw keys + screen text.
2. **Offline (Claude Code session, human-supervised at first)**: cluster the week's escape-hatch episodes and knowledge-gap events → for each recurring pattern, *write the missing `ActionSpec`/Skill/knowledge block* → live-verify against DF → commit behind an eval gate.
3. **Eval gate** (ROADMAP 6.3, promoted from "nice to have" to *flywheel prerequisite*): scenario save-states — "survives wolf", "buys item", "grinds spearman 5→8 unattended", "completes journey of N world-tiles". A skill/prompt change ships only if the eval suite doesn't regress.

The system that reaches hell is not one we fully specify in advance — it is one whose *coverage grows from its own failure logs*, with verification keeping the growth honest. The dev loop already works this way informally; this formalizes it.

## 6. Long-horizon continuity (lives, not sessions)

- **Campaign state**: Director's life plan + skill-level history + site knowledge
  persisted across restarts (extends `goals/` + memory; mostly exists).
- **Death loop**: wire postmortems (Phase 7.1 — promoted: it is cheap and the
  ambition needs many lives). Each death feeds the Director of the next life.
- **Digest memory**: behaviors emit compressed episode digests ("grinded 4h at Claw Side: +3 axe, killed 11 bandits, fled 2 trolls, ate 3×") instead of per-action memories — keeps token budget flat over arbitrarily long lives.

## 7. The road to hell, concretely

Feasibility notes (verify against wiki, encode in knowledge pack):
- **Strength**: spar recruited companions; fight escalating tiers (bandits →
  beasts → night creatures). `grind_combat` + `journey` + provisioning covers it.
- **Equipment**: loot bandit camps/lairs for steel; demons require the best armor
  we can loot — knowledge-pack problem plus `clear_site`.
- **The descent** (wiki-verified 2026-06-10, details in `memory/knowledge/descent.md`): adventurers can't dig, and ordinary caves/caverns do NOT reach the underworld. Exactly two routes exist: the slade spire inside an **initial dark fortress** (Π — sites whose unique demon rose from below), or a **player-made fortress** that breached hell in fortress mode (a legal prepared-route option). Better still: the *unique demon itself rules the dark-fortress throne room* — "kill a demon" is achievable at the top of the tower without entering hell, making it the natural first demon milestone before an underworld expedition.
  
  **World selection is a legitimate lever**: choose a generated world whose goblin civs are demon-ruled (guaranteeing Π sites), with cave/savagery dials to taste — player prerogative, not cheating; converts much of this knowledge problem into configuration.
- **Demons** (`memory/knowledge/demons.md`): pain-immune, fire-immune, ~167× dwarf size, Accomplished+ skills — only structural damage stops them, webbing demons must be fled, and the underworld respawns them endlessly (the objective is *enter, kill one, leave*, never "clear"). Legendary fighter/dodger/armor-user + chokepoint tactics + the willingness to flee. The optional necromancy path (`memory/knowledge/powers.md`) removes provisioning entirely — at the cost of frozen attributes, so body first, slab later. The Director decides *when we're ready*; the eval harness tells us if that judgment improves.

## 8. Build order (replaces phase-order with ambition-order)

1. **Interrupt-driven loop refactor** — behaviors as first-class in `loop.py`; "no skill ⇒ LLM" becomes "no behavior ⇒ LLM"; event digest for post-behavior turns. *The keystone; everything else hangs on it.*
2. **Policy object + `grind_combat` v0** — needs attack-depth (old 2.1) and sparring; exit: unattended overnight run gains ≥3 combat skill levels, zero human input, < 500 LLM calls.
3. **`journey` + Phase 3 glue** (fast-travel e2e, site registry, rumor pipeline) — exit: hears of a lair in conversation, travels there, clears it, returns.
4. **Eval harness + flywheel cadence** — scenario saves, weekly escape-hatch
   review sessions. From here on, coverage compounds.
5. **Death/postmortem wiring + campaign state** — lives accumulate into progress.
6. **`descend` + cavern knowledge + the attempt.** Iterate until a demon dies.

Steps 1–2 are weeks, not months, because they reuse everything already verified.
Step 6's date is unknowable — that's what makes it a worthy north star.

---

*Relationship to ROADMAP.md*: phases 1–4 remain the capability inventory; this document supersedes their *ordering* and adds the Behavior/Policy layer, which the phased plan lacked. CLAUDE.md remains architecture-as-built; update it as each piece above lands.

---
---

# Part II — Implementation Spec (hand-off)

Written for the implementing model (Sonnet-class). Design decisions are made; do not re-litigate them. Every named symbol below exists in the codebase today unless marked NEW. Follow CLAUDE.md contributing rules: live-verify against the running DF, conventional commits, uv, no gratuitous fallbacks.

## II.0 Current seams you will build on

- `opendwarf/agent/loop.py` — `TacticalLoop._tick()` order: stale-state guard → `_auto_handle` → `extractor.ensure_fresh` → **active-skill stepping** → goal revision → prompt build → `llm.decide(caller="tactical")` → `_dispatch`.
- `opendwarf/actions/skills.py` — `Skill.step(state) -> SkillResult` with `SkillStatus.{RUNNING,DONE,INTERRUPTED}`; shared `Skill._check_interrupts()` hard-codes: any hostile / conversation / announcement ⇒ interrupt.
- `SkillContext(lua, chunk_map, pathfinder, extractor)` — handles for sub-skills.
- Decision JSON from the LLM: `{"action": str, "reasoning": str, "scratchpad": str?}`.
- Per-caller model override env vars already work:
  `OPENDWARF_{ANTHROPIC,OPENROUTER}_MODEL_<CALLER>`.

## II.1 Milestone M1 — Behavior layer + interrupt-driven loop

New package `opendwarf/behaviors/` with:

### `policy.py` (NEW)
```python
@dataclass
class Policy:
    # v0 deliberately small. Extend only when a behavior needs it.
    engage_species_allow: list[str]   # creature race strings the autopilot MAY fight
    max_opponents: int = 1            # engage only if hostiles ≤ this
    min_health_pct: int = 60          # engage only if health ≥ this
    flee_below_health_pct: int = 40   # autopilot flees without asking
    eat_when_hungry: bool = True
    drink_when_thirsty: bool = True
    sleep_indoors_only: bool = True
    never: list[str] = field(...)     # free-text hard rules, shown to Tactician
```
- `to_prompt_line()` — one-line summary for the turn prompt.
- Persisted at `goals/policy.json`; loaded by the loop at start.
- The LLM revises it via a new optional decision key `"policy": {…}` (same pattern as `"scratchpad"`); validate fields, ignore unknown keys, log the diff as a `policy_revised` event in decisions.jsonl.

### `interrupts.py` (NEW)
```python
class InterruptReason(StrEnum):
    HOSTILE_UNHANDLED   # hostile present that policy does not authorize engaging
    HEALTH_THRESHOLD    # health < policy.flee_below_health_pct
    CONVERSATION        # forced/any dialogue began
    ANNOUNCEMENT        # showing_announcements
    UNKNOWN_SCREEN      # existing escape-hatch condition
    PHYSIO_CRITICAL     # hunger/thirst/drowsy critical AND behavior can't self-serve
    TARGET_DONE         # behavior reports goal reached
    STALLED             # progress watchdog fired (see below)

def check(state: GameState, policy: Policy, behavior: Behavior | None) -> Interrupt | None
```
This **replaces** `Skill._check_interrupts` as the single source of interrupt truth. Crucial change vs today: *a hostile the policy authorizes is NOT an interrupt* — that's the whole point. Skills keep their method as a fallback when run outside a behavior (pass `policy=None` ⇒ today's behavior exactly).

**Progress watchdog** (RESEARCH.md delta 2 — counters the documented LLM-agent waiting-loop pathology): `STALLED` fires on *state-delta stagnation*, not step counting. Hash `(adventurer pos, inventory count, nearby-unit ids, tick bucket)` each behavior step; unchanged for N=20 consecutive steps ⇒ STALLED. Cheap, in `interrupts.check`.

**Tactician→Director escalation** (RESEARCH.md delta 1): if the interrupt-resolution call fails to parse, returns `"escalate": true` (new optional decision key), or the same `InterruptReason` re-fires within 3 turns of being "resolved", re-ask with `caller="tactical_escalated"` — which model tiering maps to the strongest model. One retry; no loops.

### `digest.py` (NEW)
`EventDigest`: `add(event: str, **counters)`, `render(max_lines=12) -> str`.
Behaviors append factual events ("killed bandit (2)", "ate plump helmet", "fled troll", "+1 MACE"). Rendered into the post-interrupt turn prompt as `-- While on autopilot ({behavior.name}, {n} actions, {ticks} ticks) --`. Also: on behavior end, write ONE episodic memory note from the digest (importance from outcomes), not per-action notes.

### `base.py` (NEW)
```python
class Behavior:
    name: str
    def __init__(self, ctx: SkillContext, policy: Policy): ...
    def step(self, state: GameState) -> BehaviorResult: ...
    # BehaviorResult = RUNNING | DONE(outcome) | NEEDS_LLM(reason)  — mirrors SkillResult
    digest: EventDigest
```
Behaviors run child Skills by holding them and forwarding `step()` (exactly how `TalkToSkill` already wraps `RouteExecutor` — copy that pattern). They send keys the same way skills do (via `ctx.lua`).

### Loop changes (`loop.py`)
- Add `self._active_behavior` slot. New `_tick` order: stale guard → `_auto_handle` → `ensure_fresh` → **`interrupts.check(...)`** → if interrupt: suspend behavior (keep it), build LLM turn with digest + reason → else if active behavior: `behavior.step(state)`, return → else if active skill: as today → else: LLM turn as today.
- Post-interrupt decision options (new ActionSpecs, group="autopilot"): `resume` (continue suspended behavior), `abort_behavior`, plus all normal actions. If the LLM picks a normal action, the behavior stays suspended; `resume` re-arms it.
- `_history` entries for behavior episodes use the digest one-liner, not raw
  actions.

**M1 exit criterion (testable without combat):** a NEW `PatrolBehavior` (walk a loop of waypoints, re-pathing, eating/drinking from inventory per policy) runs 30+ minutes unattended in a safe town, < 20 LLM calls total, digest and resume verified live. Unit tests: interrupt matrix (policy × state → reason), digest rendering, policy JSON round-trip.

## II.2 Milestone M2 — `grind_combat` v0

Prerequisite inside this milestone: **attack depth** (old ROADMAP 2.1). LIVE-VERIFY what `A_ATTACK` opens in v0.53; expose `attack:<unit_id>` choosing target by id with the quick/default strike. Screen-read fallback only if state structs are insufficient. Keep strike choice deterministic (closest authorized hostile, default attack); LLM strike-choice is a later upgrade.

`GrindCombatBehavior(area_center, radius, until: dict)` state machine:
```
SEEK    — pathfind toward nearest policy-authorized hostile in radius; none found → widen search ring, then STALLED after N empty sweeps
ENGAGE  — attack:<id> until target down or flee condition → policy flee
RECOVER — post-combat: eat/drink per policy; sleep if drowsy & sleep rule allows
CHECK   — read skills (extend opendwarf--state.lua to emit adventurer skill levels — they're already shown in summary; expose raw ids+levels); `until` met (e.g. {"MACE": 8} or {"max_ticks": N}) → DONE
```
New ActionSpec `grind_combat` (available when policy non-empty), params from
intent string: `grind_combat:<radius>`.
Tier data: `behaviors/tiers.py` (NEW) — starter table mapping race string → tier 1–4 from `memory/df_mechanics.md`'s danger tiers; policy authorizes via species list OR `tier_max`. Unknown race ⇒ treat as tier 3 (interrupt).

**Also in M2 — postmortem wiring** (moved here from M4, RESEARCH.md delta 3: deaths start when grinding starts): detect death (LIVE-VERIFY the focus string on the death screen), call existing `PostmortemBuffer.generate_and_append`, flush reflection, archive `logs/<session>` + the final digest. Each death must feed the next life before overnight runs begin.

**M2 exit criterion:** overnight unattended run in wilderness: ≥3 combat skill level-ups, zero human input, < 500 LLM calls, alive (or a postmortem-worthy death with the digest explaining why). Prerequisite: the wolf-survival eval scenario (II.4) exists and passes — do not run unattended ungated.

## II.3 Milestone M3 — `journey` + quest glue

1. Fast-travel end-to-end LIVE-VERIFY (ROADMAP 3.4) — do this FIRST; everything
   here depends on it.
2. `JourneyBehavior(dest_site_id)`: fast travel toward dest → on forced exit (encounter/night), local-handle via policy (fight/flee/sleep) → re-enter travel → arrive → DONE. Provisioning: interrupt PHYSIO_CRITICAL only if inventory can't satisfy need.
3. Site registry `spatial/sites.json` + rumor extraction pass on `dialogue_ended` (cheap LLM call, caller="rumor_extract") → entries with `estimated_pos`/`confidence` (ROADMAP 3.2/3.3 as specced there).
4. Intent `journey:<site_id|rumor_id>`.
5. **Situational knowledge injection** — `opendwarf/memory/knowledge.py` (NEW): load `memory/knowledge/*.md` at startup; `INDEX.md`'s table maps each file to tags + inject-when signals. At turn-prompt build, match context (site type, underground depth, hostile races present, active goal/behavior text) against tags; inject the 1–2 best-matching topic files into the *dynamic* section of `PromptBundle` (never the cached prefix — `df_mechanics.md` alone stays in the prefix). Log injections as `knowledge_injected` events. Facts marked `[prior]` in the pack are LIVE-VERIFY items: when one is confirmed or refuted in play, update the file (flywheel applies to knowledge, not just skills).

**M3 exit criterion:** hears of a location in conversation → travels there across fast-travel distance → acts on it (clear/loot/talk), autonomously.

## II.4 Milestone M4 — flywheel & lives (parallelizable after M1)

- **Eval harness** `evals/`: named DF save dirs + YAML scenario specs (start save, max wall-clock, success predicate over decisions.jsonl/state). Runner: `uv run python -m evals.run <scenario>`. First four scenarios: wolf-survival, buy-item, patrol-overnight, grind-3-levels. The wolf-survival scenario is an M2 prerequisite — build it first.
- ~~Postmortem wiring~~ — moved into M2 (deaths begin there).
- **Escape-hatch review doc**: `logs/REVIEW.md` generated weekly (manual command is fine): cluster escape_hatch + knowledge-gap events by focus string with counts — the input queue for new skills/knowledge blocks.

## II.5 Caller/model tiering (config, no new code)

| Caller | Role | Suggested env |
|--------|------|----------------|
| `tactical` | interrupt resolution | cheap+fast (e.g. deepseek-flash via openrouter) |
| `tactical_escalated` | hard interrupts punted by the Tactician | strongest available |
| `goal_revision` | Director | strongest available |
| `rumor_extract`, `memory_*` | extraction | cheap |

## II.6 Out of scope for these milestones

Sparring companions, ranged combat, crafting/chopping, sneaking, `descend` — all follow the same Behavior pattern later. Do not generalize early for them.
