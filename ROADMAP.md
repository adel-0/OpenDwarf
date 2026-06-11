# OpenDwarf — Roadmap

**Vision**: an LLM intelligently playing DF Adventure Mode — handling any situation the game throws at it (within game balance, no cheating), demonstrating multi-step decision-making in pursuit of broad goals, on a lightweight but powerful harness.

> **NORTHSTAR.md supersedes the *ordering* below** (2026-06-10): it adds the Behavior/Policy autopilot layer and re-sequences work toward "legendary adventurer reaches the underworld". NORTHSTAR.md Part II is the implementation spec for milestones M1–M4. The phases below remain the capability inventory; status marks audited against code 2026-06-10. **RESEARCH.md** grounds the plan in the published state of the art and holds the risk register.

## Design principles (hold for every item below)

1. **No cheating.** We read game state and simulate inputs — never mutate game state (no teleports, no stat/item edits). Skipping animations is the one allowed exception.

2. **The harness is a gradient, not a wall.** Every capability sits at the lowest level that works, and gets promoted only when failures demand it:
   - **L0 — Deterministic, no LLM**: auto-handlers (popups, announcement paging, help dialogs), RPC plumbing, map extraction. The LLM never sees these.
   - **L1 — Skills**: the LLM picks an *intent*; code executes the multi-step *how* (A* routing, fast travel, menu sequences). One `ActionSpec` per capability.
   - **L2 — Engineered context**: the LLM decides, and the harness's job is to make that decision well-informed — action lists with availability, the map view, memories, and injected DF knowledge (mechanics, consequences, social rules). Much of DF's depth needs *no new code*, just the right knowledge at the right time.
   - **L3 — Escape hatch**: when no skill or action fits (novel menu, weird prompt, unmodeled mechanic), the LLM takes over directly: raw key input + screen-text reading. Recurring L3 patterns get promoted down to L1/L2.
   
   The goal is not to script the game — it's to let the LLM *play* it, with code absorbing only the parts where LLM-per-keystroke is wasteful or unreliable.

3. **Everything observable.** Every decision, goal change, and memory event lands in session JSONL — keep that true.

4. **Verify live.** DF + DFHack are always running during development. Anything marked LIVE-VERIFY must be exercised in-game before being marked done. Note: most wiki documentation describes *classic* keybindings; v50+ premium UI differs — always resolve actions via `df.interface_key` names and confirm in-game.

Last major change: **2026-06-11 NORTHSTAR M2 tail — death detection + postmortem wiring**: adventurer death is now detected via three independent signals (unit `flags2.killed` / `flags1.inactive` / `not isAlive` from Lua extractor; `getAdventurer()=nil` outside fast-travel; focus-string patterns). On death the loop calls `PostmortemBuffer.generate_and_append`, flushes session-end reflection, writes the active behavior digest as an episodic memory note, archives the session log directory to `logs/archive/`, and exits gracefully. Death loop is guarded by `_death_handled` to prevent double-firing. New module `opendwarf/agent/death_handler.py`; `GameState.adventurer_dead` flag; Lua `opendwarf--state.lua` updated. 23 new unit tests; 106 total passing. **LIVE-VERIFY pending**: exact focus string on DF v53 death screen (cannot die on demand); full end-to-end postmortem generation (requires a real death with LLM connected).

Previous change: **2026-06-11 NORTHSTAR M2 — `GrindCombatBehavior`**: autopilot that hunts and bump-attacks policy-authorized hostiles to train combat skills (seek ring-search → engage closest → recover via eat/drink → `until` skill level / tick budget), plus `behaviors/tiers.py` danger-tier table and a new `Policy.engage_tier_max` so the LLM can authorize fights by difficulty class rather than enumerating races. Tier authorization is enforced in the single interrupt checker. New `grind_combat[:radius[:SKILL:level]]` autopilot intent. 14 new unit tests (tiers, tier authorization, engage/seek/until, intent parsing); 83 passing. SEEK + live A* pathing + real-state stepping live-verified 2026-06-11; full combat grind needs LIVE-VERIFY against a hostile encounter. Not in this change: postmortem/death wiring (M2 tail) remains open.

Previous change: **2026-06-10 Phase 1 — Survival completeness**: physiological state extraction (hunger/thirst/sleep timers + derived flags), `SleepSkill` (4-phase: A_SLEEP→A_SLEEP_SLEEP→A_SLEEP_DAWN→SELECT), eat/drink ActionSpecs with item-type filtering, survival gates pure function + unit tests, hint injection into the LLM prompt.
✅ Live-verified 2026-06-10 on DF v0.53.14 STEAM (Linux): physiological timers, SleepSkill end-to-end, action block shows eat/drink/sleep correctly.
Eat/drink with actual food items needs LIVE-VERIFY (current character has no food — pending Phase 1 exit-criterion run with a fresh adventurer).

Previous change (2026-06-10): intent/skill architecture — persistent chunk map + A*,
RouteExecutor, key dispatch, quest-log. Full fast-travel end-to-end → Phase 3.

---

## Where We Are (honest gap analysis)

**Works today** (Python unit-tested; core movement live-verified):
- Perception: `ChunkMap` + `MapExtractor` (~81×81 extraction, z±2), 21×21 rendered view with unit overlays, A* with unknown-cost/stairs/partial paths.
- Actions: registry-driven list + dispatch; `RouteExecutor`, `FastTravelController`, `QuestLogSkill`, `MenuSkill` (pickup/drop/wield), conversation choices, talk/attack/wait/rest keys.
- Cognition: tactical loop with auto-handlers; `GoalManager` (flat ≤3 goals + structured plan steps with machine-checkable completion); revision triggers; scratchpad; outcome-annotated history; memory system (episodic/semantic/procedural, retrieval, reflection, postmortems); observability JSONL.

**Gaps that block the vision** (status audited against code 2026-06-10):

| Gap | Status |
|-----|--------|
| ~~No hunger/thirst/exhaustion; no eat/drink/sleep~~ | ✅ CLOSED (Phase 1). Eat/drink with real food still LIVE-VERIFY. |
| ~~No flee execution, no yield, no armor management~~ | ✅ CLOSED (Phase 2 commit `a5d3db6`): `FleeSkill`, `yield`, `wear`/`remove_armor`. **Still open: blind `attack` key — no target/strike selection (2.1 → NORTHSTAR M2).** |
| One-shot conversations; no per-NPC topic memory | PARTIAL: `TalkToSkill` routes to a chosen NPC and opens dialogue (`078a2ca`). Asked-topics dedup (3.1) still missing. |
| No site registry / topo graph (spatial L2/L3) | OPEN (3.2/3.3 → NORTHSTAR M3). |
| ~~No L3 escape hatch~~ | ✅ CLOSED (`078a2ca`): `press:<KEY>` + `read_screen` actions, unknown-focus detection with logged episodes (4.1/4.2). |
| Action surface covers ~10% of adventure mode | OPEN (Phase 5 / NORTHSTAR flywheel). |
| ~~Agent has almost no DF knowledge~~ | PARTIAL: `df_mechanics.md` (15 sections, always-on) + `memory/knowledge/` situational pack added 2026-06-10 (descent routes, demons, training meta, necromancy — wiki-verified, `[prior]` items flagged LIVE-VERIFY). Injection mechanism specced in NORTHSTAR II.3 item 5; not yet implemented. |
| `GameState.summary()` grows unboundedly | OPEN (6.1). |
| ~~Death not detected; postmortem generation unwired~~ | ✅ CLOSED (M2 tail, 2026-06-11). Three detection signals; postmortem + reflection + digest archival wired. **LIVE-VERIFY pending**: exact focus string on DF v53 death screen; full e2e postmortem generation on a real death. |
| **No autopilot layer — every non-skill turn costs an LLM call** | PARTIAL — M1 behavior/interrupt layer + `PatrolBehavior` landed; M2 `GrindCombatBehavior` landed (seek/engage/recover/until + tier-based policy authorization; SEEK+pathing live-verified, full combat grind LIVE-VERIFY pending a hostile); M2 tail (death/postmortem) landed. **Still open: `journey` (M3).** See NORTHSTAR.md M2/M3. |

---

## Remaining Work — Phased Plan

Ordering rationale: 1–2 stop the most common premature deaths; 3 makes goal pursuit actually work; 4 delivers the "any situation" guarantee; 5 opens up the game's depth; 6 is the quality flywheel; 7 (late, optional) is unattended hardening.

### Phase 1 — Survival completeness ✅ IMPLEMENTED (eat/drink LIVE-VERIFY pending)

1.1 ✅ **Physiological state extraction.** `opendwarf--state.lua` reads
`adv.counters2.hunger_timer / thirst_timer / sleepiness_timer / exhaustion`.
`GameState` has raw timers + derived `hungry/thirsty/drowsy/critical` flags.
Thresholds (empirical, LIVE-VERIFIED values observed):
  hungry ≈ 75000, hungry_critical ≈ 150000
  thirsty ≈ 50000, thirsty_critical ≈ 100000
  drowsy ≈ 57600, drowsy_critical ≈ 115200
Summary shows physio line only when non-normal.

1.2 ✅ **Eat / drink actions.** `eat_N` / `drink_N` ActionSpecs available when food/drink items are in inventory (item type filtering: MEAT/FISH/FOOD/PLANT/CHEESE/EGG/SEEDS=food, DRINK=drink). `A_INV_EATDRINK` is the single combined eat/drink key; `eatdrink:N` in act.lua uses the same `open_and_select` mechanism as pickup/drop. LIVE-VERIFY with actual food items still needed. `drink_adjacent` (from water tile) deferred — implement when a water tile is reachable in testing.

1.3 ✅ **SleepSkill.** 4-phase (LIVE-VERIFIED 2026-06-10):
  A_SLEEP → opens sleep menu (first time shows Help dialog, auto-handler dismisses it)
  A_SLEEP_SLEEP → selects 's Sleep' (default is 'w Wait')
  A_SLEEP_DAWN → selects 'd Until dawn'
  SELECT → confirms; game fast-forwards to dawn (~1200 ticks)
L2 note in skill docstring: outdoors at night = bogeymen.

1.4 ✅ **Survival gates** (`opendwarf/goals/survival.py`): pure `evaluate(state)`
function → `SurvivalGates` dataclass; `.hint()` generates LLM-readable urgency text.
Wired into `_build_hint` in the tactical loop. 12 unit tests pass.

**Exit criterion:** a fresh adventurer running overnight in a peaceful area is still alive in the morning (fed, watered, slept). — Pending full-run verification.

### Phase 2 — Combat competence ✅ MOSTLY DONE (commit `a5d3db6`; 2.1 attack depth remains → NORTHSTAR M2)

2.1 **Attack execution depth.** Today `attack` sends `A_ATTACK` blind. In v50 this opens target/attack selection UI. Build a `CombatStrike` skill: read the attack screen (LIVE-VERIFY which viewscreen/fields expose target and attack lists — screen-read fallback if state structs don't cover it), pick target by unit id from the intent (`attack:<unit_id>`); attack *choice* (body part, weapon vs wrestle) can start deterministic (quick/high-chance default) and graduate to an LLM choice when the context block can present hit chances — the gradient in action.

2.2 **Flee as a skill.** `flee` intent: A* toward the nearest known safe target (site, stairs away from threat, or maximizing distance past line of sight), re-planning each step, terminating when no hostile within ~15 tiles. The survival hint currently *suggests* fleeing but nothing *executes* it.

2.3 **Equipment management.** `wear_N` (armor — `MenuSkill`, LIVE-VERIFY keys), `sheathe` (frees hands for climbing/grappling), plus a prompt-visible note of empty equipment slots. Ranged (`aim/fire`, `throw_N`) included if the attack-screen work in 2.1 makes it cheap, else deferred to Phase 5.

2.4 **Combat context & memory.** Verify the post-interrupt LLM turn gets a crisp picture: hostile list with direction/distance (exists), combat log (exists), the new attack/flee intents. Write procedural memory notes on `combat_resolved` so "wolves are dangerous at level 0" survives across sessions. L2 knowledge: yield/surrender mechanics, jumping-tackle, height advantage, wrestling basics.

**Exit criterion:** agent survives (or deliberately flees) a single-wolf encounter most of the time; multi-hostile encounters produce coherent target choices.

### Phase 3 — Quest depth & world model

3.1 **Multi-turn conversations.** (PARTIAL: `TalkToSkill` exists — route + initiate; topic dedup missing.) DF ends dialogue after single exchanges. Add a `ConverseSkill` that re-initiates `talk` with the *same* NPC (resolved by `hist_fig_id`) for follow-ups, with an asked-topics set per NPC persisted as a semantic memory note, so the agent works through rumor → details → directions without re-asking. The dedup data also feeds the prompt ("you already asked X about the bandit camp").

3.2 **Spatial Layers 2–3** (design below, unchanged): topological waypoint graph + rumored-site registry. Concretely: `spatial/topo_graph.json` nodes created on area-type change / named-location reveal; `spatial/sites.json` entries from quest log + conversation rumors with `estimated_pos` + `confidence`. New intent `goto_rumor:<id>`: fast travel toward `estimated_pos`, then `explore` within an `exploration_budget`, updating the entry on confirm/refute.

3.3 **Rumor pipeline glue.** Quest-log lines and conversation transcripts currently land in memory as text. Add an extraction pass (cheap LLM call on `dialogue_ended` / quest-log read) that emits structured site-registry candidates. This closes the loop: hear rumor → registry entry → goto_rumor → arrive → goal progress.

3.4 **Fast travel end-to-end** (carried from the verification checklist; quest travel depends on it): run the full enter→steer→auto-stop→exit journey live; verify ChunkMap absolute-coordinate stability across the region change; snap the exit position onto the topo graph (trap #5 below). Tune `_STOP_DISTANCE`/no-progress handling.

**Exit criterion:** the agent hears about a location in conversation, travels there across fast-travel distance, and acts on it — fully autonomously.

### Phase 4 — Generality: the L3 escape hatch + knowledge pack (4.1 ✅, 4.2 ✅ in `078a2ca`; 4.3 partial, 4.4 open)

This is what makes "handle *any* situation" honest instead of aspirational.

4.1 **Raw-input escape hatch.** A `press:<INTERFACE_KEY>` action (validated against the `df.interface_key` enum, dangerous keys excluded) always available to the LLM, plus a `read_screen` intent that returns the current screen text via `dfhack.screen.readTile` (the screen-reader exists in `LuaExecutor`). When the agent lands on an unmodeled viewscreen, the loop should *not* blind-escape: present the focus string + screen text + raw keys and let the LLM navigate. Auto-handlers (L0) still cover the known-trivial screens first.

4.2 **Unknown-screen flow.** Promote `_auto_handle` to a registry keyed on focus-string patterns. Unrecognized focus → escape-hatch turn (4.1) instead of a silent stall. Log every escape-hatch episode distinctly — each recurring one is a candidate for promotion to a skill (the L3→L1 pipeline made concrete).

4.3 **DF knowledge pack.** Expand `memory/df_mechanics.md` into a curated, *situational* knowledge base sourced from the wiki (adventure gameplay + quick reference pages, see References in CLAUDE.md): combat mechanics, social rules (theft → exile/death, crime scoped to the civ; fame and recruitment; performance reputation), survival lore (bogeymen, freezing, swimming), crafting recipes, night-creature/secret mechanics. Inject by context: a small always-on core (in the cached prefix) + per-situation blocks selected like memories (combat → combat lore; in a shop → trade rules). This is pure L2 — the cheapest depth we can buy, no new actions required.

4.4 **Knowledge-gap feedback.** When the LLM flails (no-effect actions, repeated escape-hatch turns), log it as a knowledge-gap event; review these to grow the pack. The scratchpad already lets the agent note "I don't know how X works" — make those notes greppable.

**Exit criterion:** dropped into an unmodeled situation (a shop menu, a lever, a performance prompt), the agent makes meaningful progress via the escape hatch instead of stalling — without any situation-specific code.

### Phase 5 — Adventure-mode breadth (engage the game's actual depth)

DF adventure mode is far more than fight-and-survive: performances, authorship, crafting, companions, commerce, religion, secrets, site claiming. Work through these capability families, each at its cheapest harness level. Most are *one `ActionSpec` + a `MenuSkill` + a knowledge block*; some are L2-only (knowledge, no code); a few start as documented L3 patterns and get promoted when used often.
All keybindings below are classic-era wiki references — LIVE-VERIFY v50 equivalents.

5.1 **Movement breadth** (mostly L1 keys + state flags): `sneak` (toggle; expose "sneaking" in summary; Ambusher-skill dependent), `climb`/`jump` (combat & terrain escape; pathfinder may *suggest* but never auto-use until confirmed reliable), swimming awareness (danger knowledge + skill gate), careful movement, gait/speed selection (run when fleeing, walk by default), `wait_until_dawn`.

5.2 **Wilderness craft & camping** (L1 `MenuSkill`s over the crafting menu): butcher corpse (sharp tool required), knap stone, carve helve + assemble stone axe, start campfire, chop trees. Combined with Phase 1 this makes the agent wilderness-self-sufficient: kill → butcher → eat, knap → armed.

5.3 **Social breadth**: recruit companions (conversation-based; fame-gated — companions also guard your sleep), party awareness in the prompt (exists minimally), ask permission to sleep, demand yield / accept surrender, **performances** (tell story / recite poetry / sing — reputation engine and fame source; conversation-menu driven, so mostly L2 over the existing conversation system).

5.4 **Commerce**: shop trading skill (enter shop → trade menu → select/confirm), coin awareness in inventory, L2 knowledge: prices, currency denominations, and the hard rule that unpaid taking is theft with exile/death consequences.

5.5 **Knowledge & power**: read books/slabs (libraries, towers — path to secrets, including necromancy: an emergent-content goldmine if the agent chooses it), write compositions (late; fame source), pray at temples (L1 key + L2 lore), artifact quests (mostly covered by Phase 3 pipeline + quest log).

5.6 **Site interaction**: claim structures / become lord (conversation-driven), found a camp (`b`-equivalent), assign hearthpersons. Late in the phase — this is end-game content that gives long-running agents a renown arc.

**Exit criterion:** the goal manager can legitimately generate — and the agent can execute — goals like "earn fame as a performer in this town", "recruit two companions and clear the bandit camp", "find a library and learn a secret", not just "survive and kill".

### Phase 6 — Quality flywheel (ongoing)

6.1 **Token budget management.** Situational `summary()`: combat → threats + map core; conversation → dialogue + relationships; exploration → map + sites. Cap each block; keep the stable prefix (`PromptBundle`) untouched for caching. The Phase 4 knowledge pack injection must respect the same budget.

6.2 **Memory polish.** Procedural combat notes (started in 2.4), contradiction-driven semantic updates, optional MemSearch vector index if keyword retrieval misses.

6.3 **Eval harness.** Scripted scenario checks against a save: "fresh adventurer reaches a town within N turns", "gets a quest within M conversations", "survives a wolf encounter", "buys an item in a shop". Score from decision JSONL (it already has everything needed). Without this we're guessing whether a prompt/skill change helped.

### Phase 7 — Unattended robustness (late stage, optional)

Not needed while a human is around to restart things. Do last.

7.1 **Death handling** ✅ IMPLEMENTED (M2 tail, 2026-06-11): `adventurer_dead` flag on `GameState` (three signals: `flags2.killed`/`flags1.inactive`/`not isAlive` from Lua extractor; nil adventurer outside fast-travel; death focus patterns). `handle_death()` in `opendwarf/agent/death_handler.py` calls `PostmortemBuffer.generate_and_append`, flushes `ReflectionEngine`, writes final behavior digest as episodic note, archives `logs/<session>/` to `logs/archive/`. Loop exits gracefully after death. **LIVE-VERIFY pending**: exact DF v53 death screen focus string; full e2e test on a real death with LLM connected.

7.2 **New-character flow**: a `CharacterCreationSkill` driving post-death menus to a new adventurer (escape-hatch-first, promote to skill if it works).

7.3 **Process supervision**: session supervisor in `main.py` (retry with backoff, wait out a dead DF), stall watchdog (N no-change ticks with no active skill → escalate). The RPC client already reconnects on timeout.

---

## Spatial Memory Design (Layer 1 DONE; Layers 2–3 → Phase 3.2)

### Layer 1 — Sparse Chunk Grid ✅ IMPLEMENTED
`opendwarf/spatial/chunk_map.py`. 16×16 chunks keyed `(cx,cy,z)`, absolute coords, per-tile `last_verified_tick`, persisted to `spatial/chunks.json`. A* in `pathfinder.py`: UNKNOWN traversable at 5× cost, stale tiles treated as UNKNOWN, ramps need a confirmed z-transition, partial paths toward the goal on failure.

### Layer 2 — Topological Waypoint Graph (pending)
Nodes for qualitatively distinct places. Triggers: area-type change, return to a coordinate, NPC reveals a named location. Edges carry direction/distance/terrain/confirmed. ~200–500 nodes → `spatial/topo_graph.json`.

### Layer 3 — Site Registry (pending)
Knowledge with no tiles yet: quest targets, NPC hints, world-data sites. Each entry: `exact_pos` (visited) or `estimated_pos` (dead-reckoned), `confidence`, source, notes.

### LLM Interface
~100–150 token structured block: current area, active route + next waypoint, nearby
sites with confidence. Never raw tiles or the full graph.

### Implementation Traps (still apply)
1. `getPosition()` is LOCAL — convert with `region_x*16 + local`. Fast travel uses a separate coordinate space.
2. Z-levels not auto-connected — detect portals via `tiletype_shape` (stairs/ramps); tag vertical edges; ramps unreliable until an observed transition.
3. PASSABLE is dynamic (rivers freeze, doors lock) — `last_verified_tick`; downgrade on movement failure.
4. Climbing bypasses passability — future `CLIMBABLE` cell gated on Climber skill.

5. Fast-travel exit imprecision — snap to existing topo node within ~10 tiles.

---

## Confirmed DF Empirical Findings

- Army position coords are 3× embark-tile coords; fast travel tracks via army pos.
- Fast travel help dialog + 'x' exit require mouse clicks (keyboard doesn't work).
- `getAdventurer()` returns nil during fast travel — handle gracefully.
- Use `cur_year_tick` (not `adventure.tick_counter`, which wraps at ~256).
- NPC response text appears as announcements, not in conversation data structures.
- `adventure.total_move` increments by a *variable* amount per successful move (observed +9 for one step) — compare with `!=`, never `+1`.
- Steam DFHack on Linux is a separate Steam app (`…/steamapps/common/DFHack/hack/scripts`); `LuaExecutor` auto-resolves via `dfhack.getHackPath()`.
- Live-verified working (2026-06-10): `opendwarf--map.lua` wide extraction (~0.23s), RouteExecutor pathing, `A_MOVE_UP`/`A_MOVE_DOWN` key names, `A_LOG` quest log open/read/escape, local→absolute coordinate offset.
- Sleep flow (LIVE-VERIFIED 2026-06-10): `A_SLEEP` opens a Help dialog the first time (auto-handler dismisses it); subsequent presses go to `dungeonmode/Sleep`. Default is "Wait" not "Sleep" — must press `A_SLEEP_SLEEP` first, then `A_SLEEP_DAWN`, then SELECT to confirm. Sleep until dawn ≈ 1200 ticks (~1 in-game day).
- `A_INV_EATDRINK` is the single combined eat/drink key (no separate A_EAT/A_DRINK). Food item types confirmed: MEAT=48, FISH=49, FISH_RAW=50, SEEDS=53, PLANT=54, PLANT_GROWTH=56, CHEESE=71, FOOD=72, EGG=88. Drink: DRINK=69.
- Physiological timers confirmed in `adv.counters2` under `hunger_timer`, `thirst_timer`, `sleepiness_timer`, `exhaustion`. All count up from 0. Empirically observed: timers above 322,000 show STARVING/DEHYDRATED in-game.
