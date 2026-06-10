# North Star Architecture — from "agent that takes turns" to "agent that lives a life"

**Ambition**: an autonomous LLM adventurer that gets strong, descends through the
caverns, and kills demons in the underworld. This document is the architecture that
makes that physically possible, and the honest accounting of why the current shape
of the harness — however correct each piece is — cannot get there by itself.

## 1. The constraint that decides everything: decision throughput

Becoming legendary in DF is a *volume* problem. Grinding combat skills to
legendary, traveling between dozens of sites, fighting hundreds of encounters,
descending three cavern layers — that is on the order of **10⁴–10⁵ game actions**.

Measured today: a turn that consults the LLM costs ~5–15s wall-clock (LLM latency +
deferred-input waits). At LLM-per-turn, 50k actions ≈ **70–200 hours of wall-clock
for one life**, most of it spent re-deciding things that were never in question
("keep sparring", "keep walking west", "keep drinking from the river").

So the architecture question is not "how does the LLM pick better actions?" but:

> **What fraction of game actions require zero LLM tokens?**

For the ambition to be reachable, the answer must be **> 95%**. Everything below
follows from that number.

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

The harness is not wrong; it is **one layer short**. We built the spinal cord and
the consciousness, and skipped the cerebellum — the thing that handles practiced
activity without thinking.

## 3. The missing layer: Behaviors under Policy

A **Behavior** is a long-running deterministic controller (minutes–hours of game
time), parameterized by a **Policy** the LLM writes. It is to a Skill what a Skill
is to a keypress.

```
keypress (ms)  <  Skill (seconds: one route, one menu)  <  Behavior (minutes-hours)
```

Behaviors needed for the north star (each composes existing skills/intents):

- `grind_combat(area, target_tiers, until: skill_levels | duration)` — pick
  fights the policy allows, attack/flee per policy, eat/drink/sleep as needed,
  loop. *This single behavior is most of "getting strong".*
- `journey(dest, provisioning_policy)` — fast travel + local navigation +
  encounter handling + forage/refill water along the way.
- `provision(食/water/ammo targets)` — hunt, butcher, fill waterskin, buy.
- `descend(until: cavern_layer | feature_found)` — systematic stair/passage
  search downward with retreat thresholds.
- `clear_site(site, rules_of_engagement)` — sweep, fight per policy, loot list.

A **Policy** is a structured standing order, written/revised by the LLM, executed
by code — *the LLM stops being the actor and becomes the author of the actor*:

```json
{
  "engage_if": {"tier_max": 2, "max_opponents": 2, "health_min": 0.6},
  "flee_if":   {"health_below": 0.4, "opponents_over": 2, "tier_over": 2},
  "physio":    {"eat_at": "hungry", "drink_at": "thirsty", "sleep": "safe_indoors_only"},
  "loot":      ["weapons_better_than_current", "food", "coins"],
  "never":     ["steal_in_sites", "fight_in_water", "sleep_outdoors_at_night"]
}
```

**Interrupts** are the contract that keeps the LLM in command: a behavior runs
silently until an *interrupt condition* fires — new hostile above policy tier,
health threshold, policy has no applicable rule, unknown screen (existing escape
hatch), named NPC encountered, behavior target achieved/impossible. Then and only
then does an LLM turn happen, with full context including "what the autopilot did
while you were away" (compressed event digest). The existing survival gates are
already interrupt conditions in embryo.

This is ~the difference between 100% and <5% of turns consuming tokens — it turns
the 200-hour life into a game-speed life with occasional thought.

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

Hand-enumerating DF's action surface is a losing game — the wiki is a thousand
pages and the game generates novelty. The escape hatch (already built) is the
sensor; what's missing is the loop that *consumes* it:

1. **Runtime**: unmodeled situation → escape-hatch episode (logged, already
   distinct in JSONL) → Tactician improvises with raw keys + screen text.
2. **Offline (Claude Code session, human-supervised at first)**: cluster the
   week's escape-hatch episodes and knowledge-gap events → for each recurring
   pattern, *write the missing `ActionSpec`/Skill/knowledge block* → live-verify
   against DF → commit behind an eval gate.
3. **Eval gate** (ROADMAP 6.3, promoted from "nice to have" to *flywheel
   prerequisite*): scenario save-states — "survives wolf", "buys item", "grinds
   spearman 5→8 unattended", "completes journey of N world-tiles". A skill/prompt
   change ships only if the eval suite doesn't regress.

The system that reaches hell is not one we fully specify in advance — it is one
whose *coverage grows from its own failure logs*, with verification keeping the
growth honest. The dev loop already works this way informally; this formalizes it.

## 6. Long-horizon continuity (lives, not sessions)

- **Campaign state**: Director's life plan + skill-level history + site knowledge
  persisted across restarts (extends `goals/` + memory; mostly exists).
- **Death loop**: wire postmortems (Phase 7.1 — promoted: it is cheap and the
  ambition needs many lives). Each death feeds the Director of the next life.
- **Digest memory**: behaviors emit compressed episode digests ("grinded 4h at
  Claw Side: +3 axe, killed 11 bandits, fled 2 trolls, ate 3×") instead of
  per-action memories — keeps token budget flat over arbitrarily long lives.

## 7. The road to hell, concretely

Feasibility notes (verify against wiki, encode in knowledge pack):
- **Strength**: spar recruited companions; fight escalating tiers (bandits →
  beasts → night creatures). `grind_combat` + `journey` + provisioning covers it.
- **Equipment**: loot bandit camps/lairs for steel; demons require the best armor
  we can loot — knowledge-pack problem plus `clear_site`.
- **The descent**: adventurers can't dig — the route is a cavern-connected
  feature: cave entrances, breached fortresses, underworld spires. Finding one is
  a Phase-3 problem (rumors, site registry, systematic exploration) plus
  `descend`. This is the rarest, most knowledge-dependent step — expect it to be
  the long pole and the best story.
- **Demons**: legendary fighter/dodger/armor-user + chokepoint tactics + the
  willingness to flee. The Director decides *when we're ready*; the eval harness
  tells us if that judgment improves.

## 8. Build order (replaces phase-order with ambition-order)

1. **Interrupt-driven loop refactor** — behaviors as first-class in `loop.py`;
   "no skill ⇒ LLM" becomes "no behavior ⇒ LLM"; event digest for post-behavior
   turns. *The keystone; everything else hangs on it.*
2. **Policy object + `grind_combat` v0** — needs attack-depth (old 2.1) and
   sparring; exit: unattended overnight run gains ≥3 combat skill levels, zero
   human input, < 500 LLM calls.
3. **`journey` + Phase 3 glue** (fast-travel e2e, site registry, rumor pipeline)
   — exit: hears of a lair in conversation, travels there, clears it, returns.
4. **Eval harness + flywheel cadence** — scenario saves, weekly escape-hatch
   review sessions. From here on, coverage compounds.
5. **Death/postmortem wiring + campaign state** — lives accumulate into progress.
6. **`descend` + cavern knowledge + the attempt.** Iterate until a demon dies.

Steps 1–2 are weeks, not months, because they reuse everything already verified.
Step 6's date is unknowable — that's what makes it a worthy north star.

---

*Relationship to ROADMAP.md*: phases 1–4 remain the capability inventory; this
document supersedes their *ordering* and adds the Behavior/Policy layer, which the
phased plan lacked. CLAUDE.md remains architecture-as-built; update it as each
piece above lands.
