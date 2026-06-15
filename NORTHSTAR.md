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

**Breadth update (2026-06-15)**: the Director now drives *breadth* with an autotelic
learning-progress curriculum (`goals/curriculum.py`), not a hand-curated goal list.
A `CompetenceLedger` tracks per-capability competence (7 families) from signals the
loop already emits; `select_focus()` picks the family to emphasise next by absolute
learning progress + optimism − mastery (MAGELLAN/ALP), and the existing event-gated
goal-revision call turns that family into a concrete world-grounded goal (LMA3). No
new LLM call (AEL "less is more"). This is the §8-step-toward-Phase-5 mechanism for
generating goals like "earn fame as a performer" without enumerating the wiki.

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

1. ✅ **Interrupt-driven loop refactor** — behaviors as first-class in `loop.py`; "no skill ⇒ LLM" becomes "no behavior ⇒ LLM"; event digest for post-behavior turns. *The keystone; everything else hangs on it.* (M1 landed.)
2. **Conversation robustness** (was buried in step 3 / old ROADMAP 3.1) — submenu/identity-trap handling + `ConverseSkill` (re-initiate with same NPC by `hist_fig_id`, asked-topics dedup). *Reordered to the front 2026-06-11:* the logs show the agent **stalls in town conversation** before it ever reaches the hostiles steps 3–4 assume, so this — not combat — is the binding near-term blocker, and it's a precondition for ever LIVE-VERIFYing the combat grind. Cheapest fix to the failure mode that wastes every current run. Exit: the agent works a single NPC rumor → details → directions without re-asking or getting trapped in a submenu, across a full town visit.
3. **Policy object + `grind_combat` v0** — needs attack-depth (old 2.1) and sparring; exit: unattended overnight run gains ≥3 combat skill levels, zero human input, < 500 LLM calls. *Note: "full grind" cannot be marked done on unit tests alone — its LIVE-VERIFY is gated on reaching a live hostile, which depends on step 2 (out of town) or step 4 (`journey` to a lair). No live encounter has occurred yet.*
4. **`journey` + Phase 3 glue** (fast-travel e2e, site registry, rumor pipeline) — exit: hears of a lair in conversation, travels there, clears it, returns. (Conversation moved out to step 2; this is now fast-travel + spatial L2/L3 + rumor extraction only.)
5. **Eval harness + flywheel cadence** — scenario saves, weekly escape-hatch
   review sessions. From here on, coverage compounds.
6. **Death/postmortem wiring + campaign state** — lives accumulate into progress.
7. **`descend` + cavern knowledge + the attempt.** Iterate until a demon dies.

Steps 2–3 are weeks, not months, because they reuse everything already verified.
Step 7's date is unknowable — that's what makes it a worthy north star.

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

**STATUS: DONE — M1 landed.** As-built in `opendwarf/behaviors/`: `policy.py` (`Policy` dataclass, persisted `goals/policy.json`, LLM revises it via an optional `"policy"` decision key), `interrupts.py` (the single interrupt checker that replaced `Skill._check_interrupts` — a *policy-authorized* hostile is NOT an interrupt; `STALLED` fires on state-delta stagnation, not step count; `tactical_escalated` re-ask on parse-fail / repeat-interrupt), `digest.py` (`EventDigest`, one episodic note per behavior end), `base.py` (`Behavior` / `BehaviorResult`), and `PatrolBehavior`. The loop carries an `_active_behavior` slot with suspend-on-interrupt / `resume` / `abort_behavior`. Design rationale is Part I §3–4; the code is the source of truth for detail.

## II.2 Milestone M2 — `grind_combat` v0

**STATUS: PARTIAL.** `GrindCombatBehavior` (SEEK→ENGAGE→RECOVER→CHECK; `behaviors/tiers.py` danger table; `Policy.engage_tier_max`) landed and is live-verified for SEEK + A* pathing; **a real hostile encounter has never run** (combat is unexercised — see ROADMAP "Observed live behavior"). Postmortem/death wiring landed (`agent/death_handler.py`; LIVE-VERIFY of the v53 death focus string pending).

**STILL OPEN — attack depth** (old ROADMAP 2.1, the combat keystone): `A_ATTACK` is still blind. LIVE-VERIFY what it opens in v0.53; expose `attack:<unit_id>` choosing target by id with the quick/default strike (screen-read fallback only if state structs are insufficient). Keep strike choice deterministic (closest authorized hostile, default attack); LLM strike-choice is a later upgrade. Its full LIVE-VERIFY is gated on reaching a live hostile (depends on M3 `journey` or conversation getting the agent out of town).

**M2 exit criterion:** overnight unattended wilderness run: ≥3 combat skill level-ups, zero human input, < 500 LLM calls, alive (or a postmortem-worthy death whose digest explains why). Prerequisite: the wolf-survival eval scenario (II.4) exists and passes — do not run unattended ungated.

## II.3 Milestone M3 — `journey` + quest glue

**STATUS: PARTIAL.** `JourneyBehavior` (`opendwarf/behaviors/journey.py`, intent `journey:<site_id|name>`) landed — multi-leg travel with army-formation handling and a collision-feedback detour router around terrain barriers; unit-tested + live perception-checked, **full-journey LIVE-VERIFY pending** (no observed unsupervised trek to a distant site yet). Fast-travel army formation is fixed + live-verified (ROADMAP 3.4). Situational knowledge injection (old item 5) landed as `memory/knowledge.py` `KnowledgePack` — tag-matched topic files injected into the *dynamic* prompt section (never the cached prefix), `knowledge_injected` events logged.

**STILL OPEN:**
1. Fast-travel **end-to-end** LIVE-VERIFY: an observed trek across region changes; ChunkMap absolute-coord stability across the region change; snap exit position onto the topo graph.
2. **Site registry** `spatial/sites.json` + **rumor extraction** pass on `dialogue_ended` (cheap LLM call, `caller="rumor_extract"`) → entries with `estimated_pos`/`confidence` (ROADMAP 3.2/3.3).
3. Intent `journey:<rumor_id>` (currently `journey` targets known `site_id`s only).

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

## II.7 Milestone M5 — seamless DFHack interface (introspection, error feedback, self-recovery)

*Motivation (2026-06-11 incident)*: an obstructed fast-travel attempt wedged the UI; recovery took ~30 manual tool calls because knowledge that already existed (the `A_END_TRAVEL` key, the viewscreen stack, DFHack's console log) had no path into the harness.

**STATUS: DONE — M5 landed (live-verified DF v0.53.14).**
- **Runtime introspection** — `lua_scripts/opendwarf--ui.lua` (+`LuaExecutor.inspect_ui()` / `find_keys()`): viewscreen-stack types, focus strings, `adventure.menu`, `player_control_state`, travel fields, GPS dims, current message; `keys <pattern>` enumerates `df.interface_key` names from the live enum.
- **Error feedback channel** — `LuaExecutor.execute_action()` records the DFHack console-log (`stderr.log`) byte offset before the deferred callback; `consume_action_errors()` surfaces new ERROR/printerr lines so silent deferred-input failures become visible; `console_error` JSONL events emitted.
- **Self-recovery** — `UnstickSkill`: inspect → dismiss DFHack Lua screens above `dungeonmodest` → `LEAVESCREEN`×2 with focus checks → focus-token key candidates via `find_keys()` → escape-hatch turn enriched with the inspect snapshot + candidates. Travel-wedge recovery is live-verified (zero LLM calls).

**Knowledge integration rule (load-bearing, no new code)**: the installed DFHack tree (`…/steamapps/common/DFHack/hack/lua/`, `hack/scripts/`) is the version-exact API source of truth, greppable on disk — *by the development-time agent only*. The runtime agent has no filesystem; it sees only the prompt. Every discovery must be delivered in-band, in priority order: (1) compiled into an ActionSpec/Skill so the runtime agent never needs the raw key, (2) surfaced live by the introspection layer in escape-hatch prompts, (3) written to `memory/knowledge/` for situational injection. Recorded in CLAUDE.md.
