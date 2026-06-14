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

**Recent history lives in git** (`git log`); this section tracks only what is still open.

**Currently open (as of 2026-06-12):**
- **Full-journey LIVE-VERIFY** — an observed unsupervised `JourneyBehavior` trek to a distant site; ChunkMap absolute-coord stability across the region change + topo-snap (3.4 remainder). The action primitives are live-verified; the multi-leg routing *quality* over a long real journey is not.
- ~~**Site registry + rumor extraction** (M3 steps 2-3)~~ ✅ **DONE 2026-06-12.** `spatial/sites.py` `SiteRegistry` (`spatial/sites.json`) folds observed nearby-sites (ground truth) + rumor entries; `memory/rumor_extract.py` `RumorExtractor` runs a `dialogue_ended` cheap LLM pass (`caller="rumor_extract"`) → candidates resolved to world positions via the new `opendwarf--resolve-site.lua` / `LuaExecutor.resolve_site` full-world name lookup; `journey:<rumor_id>` resolves a registry entry (incl. `world_pos`) and `JourneyBehavior` steers by absolute world bearing when the target isn't yet in the nearby-site list. LIVE-VERIFIED: world-coord export, `resolve_site`, observed fold, harvest, and bearing math (bearing/distance match DF's own). **Still LIVE-VERIFY**: the `rumor_extract` LLM call over a *real* conversation transcript, and a full `journey:<rumor_id>` trek to a distant rumored site (shares the full-journey gap below).
- ~~**Attack depth / CombatStrike** (2.1 / NORTHSTAR M2)~~ ✅ **DONE 2026-06-13.** `CombatStrikeSkill` drives the mouse-only `dungeonmode/Attack` menu deterministically (target → Strike → body part → weapon, one click per `attack.mode` transition; first-use Help overlay self-dismissed; `act.lua` `attack_pick:<n>`/`attack_strike` + `read_attack_rows`; menu state surfaced as `state.attack_menu_*`). Wired as `attack:<id>` for neutral wildlife and as `GrindCombatBehavior`'s neutral-target path (hostiles keep cheap bump-to-attack). **LIVE-VERIFIED v0.53.14**: real strikes landed on neutral wolves both standalone and via the grind (which closed distance then struck repeatedly — *"You lash the wolf in the upper body … tearing the middle spine's nervous tissue!"*). Full menu protocol documented in CLAUDE.md → "Combat targeting & the attack menu".
- **Full combat grind LIVE-VERIFY** — the *autopilot grind loop* is now LIVE-VERIFIED end-to-end against wild wolves (2026-06-14): detection (`huntable_units`) → targeting → SEEK/approach → repeated ENGAGE strikes via the attack menu → a **+1 MELEE_COMBAT level-up**, all autonomous under the live interrupt checker (one run: 104 actions, 13 strikes, 0 LLM turns). Verifying this surfaced + fixed **three latent autopilot bugs** that had made the grind un-runnable in the real loop (every prior "grind LIVE-VERIFIED" claim was standalone-only): (1) each combat-log announcement suspended the behavior to the LLM — added a `Behavior.handles_announcements` opt-in so combat behaviors page their own log (loop records it for observability first); (2) the `dungeonmode/Attack` menu read as an *unknown screen*, so the interrupt checker killed every in-flight `CombatStrikeSkill` — added it to `KNOWN_FOCUS_PATTERNS` (both copies); (3) the stall watchdog false-fired during stationary striking (position/clock barely move in menu/combat) — fingerprint now folds the digest's `notable_count` so a landed strike counts as progress. Also gated `CombatStrikeSkill`'s `A_ATTACK` press on `taking_input` (a press during a prior strike's animation was swallowed → "menu did not open"). **Remaining gap**: a wolf has not yet been killed *to death* (these wolves stay neutral — `hostile_units` empty throughout — and *flee*/reposition rather than die), and a real *hostile* encounter (danger/flee/`in_combat` semantics, the M2 ≥3-level overnight exit criterion) is still unexercised — gated on reaching a genuine hostile.
- **Death/postmortem LIVE-VERIFY** — wired; the exact DF v53 death focus string + a full e2e postmortem on a real death are unverified.
- **Eval harness** (Phase 6.3 / NORTHSTAR M4): runner (`evals/run.py`: live / `--judge-only` / `--offline` sim), 4 scenario YAMLs, predicate language, in-memory simulator, and the **escape-hatch review doc** (`evals/review.py` → `logs/REVIEW.md`, the L3→L1 promotion queue clustering `escape_hatch`/`console_error`/`unstick_failed` by focus/action/outcome) are all built + unit-tested; `review.py` LIVE-RUN against real logs surfaced a real latent bug (`press:ESCAPE` errors 4×/3 sessions — `ESCAPE` is not a valid `df.interface_key`, should be `LEAVESCREEN`). **Still open**: capturing the live DF scenario save-states (`wolf_encounter`/`patrol_town`/`grind_wilderness`/`town_shop_adjacent`) — a manual DF step — so live (non-offline) scenarios can actually run.

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
| ~~No flee execution, no yield, no armor management~~ | ✅ CLOSED (Phase 2 commit `a5d3db6`): `FleeSkill`, `yield`, `wear`/`remove_armor`. **Attack depth CLOSED 2026-06-13** (`CombatStrikeSkill` drives the `dungeonmode/Attack` menu; bump for hostiles). Combat detection/targeting/strike are now LIVE-VERIFIED against wild wolves (real blows landed) — no longer "unexercised". Still pending: a kill-to-death + a genuine *hostile* encounter (flee/danger semantics, M2 overnight grind tally). |
| One-shot conversations; no per-NPC topic memory | ✅ **CLOSED for the core loop (2026-06-12)**: reliable phase-2 choice selection (`scroll=idx*3` + deferred text-matched pixel-click, LIVE-VERIFIED) + identity-trap filtering; per-NPC asked-topics dedup (`AskedTopics` — persisted normalized topic sets, inline `[already asked]` annotations + prompt hint); and **`ConverseSkill`** — `converse:<id>` drives a whole multi-turn conversation (route→talk→sweep new topics→re-engage) deterministically, zero LLM between, LIVE-VERIFIED. **Still open (Phase 3)**: submenu diving stays LLM-driven (the 98-item "Ask for directions" list); rumor→structured-site extraction (3.3). See `MEMORY.md` → conversation-flail-bottleneck. |
| No site registry / topo graph (spatial L2/L3) | OPEN (3.2/3.3 → NORTHSTAR M3). |
| ~~No L3 escape hatch~~ | ✅ CLOSED (`078a2ca`): `press:<KEY>` + `read_screen` actions, unknown-focus detection with logged episodes (4.1/4.2). |
| Action surface covers ~10% of adventure mode | OPEN (Phase 5 / NORTHSTAR flywheel). |
| ~~Agent has almost no DF knowledge~~ | ✅ CLOSED: `df_mechanics.md` (always-on prefix) + `memory/knowledge/` situational pack (descent, demons, training, powers — wiki-verified, `[prior]` items flagged). Injection via `KnowledgePack` (NORTHSTAR II.3 item 5) — tag-matched against site_type, z-depth, hostile races, goal/behavior/scratchpad text; 1–2 files injected into the dynamic prompt section per turn; `knowledge_injected` events logged. |
| ~~`GameState.summary()` grows unboundedly~~ | ✅ CLOSED (6.1, 2026-06-14). `summary()` is now situational (`_mode()` → combat / conversation / exploration) and every list block is capped (`_CAP_*` + `_capped()` with a "(… N more)" tail). Combat drops the site list / factions / friendly bystanders; conversation drops the world-site list; the 98-item "Ask for directions" menu is capped to 25 rows. LIVE-VERIFIED on the running town adventurer (exploration→combat mode switch drops sites/factions, 40→24 lines). |
| ~~Death not detected; postmortem generation unwired~~ | ✅ CLOSED (M2 tail, 2026-06-11). Three detection signals; postmortem + reflection + digest archival wired. **LIVE-VERIFY pending**: exact focus string on DF v53 death screen; full e2e postmortem generation on a real death. |
| **No autopilot layer — every non-skill turn costs an LLM call** | PARTIAL — M1 behavior/interrupt layer + `PatrolBehavior` landed; M2 `GrindCombatBehavior` landed (seek/engage/recover/until + tier-based policy authorization; SEEK+pathing live-verified, full combat grind LIVE-VERIFY pending a hostile); M2 tail (death/postmortem) landed; **M3 `JourneyBehavior` landed** (world-map travel autopilot with multi-leg re-entry + obstacle-routing; unit-tested + live perception-checked, full-journey LIVE-VERIFY pending). **Still open in M3: site registry + rumor extraction (steps 3, `journey:<rumor_id>`).** See NORTHSTAR.md M2/M3. |

### Observed live behavior (2026-06-11 sessions — what the logs actually show)

The status column above is audited against *code*; this is audited against *runs*, and the two disagree about severity. A typical session: the agent spawns, sets a "find quests / build reputation in this town" goal, walks a little, and then **spends the bulk of its turns talking — and flailing while it talks.** Action distribution across all logged sessions is dominated by `read_screen` / `press` / `escape` / `talk` / `conversation_*`; `attack` appears **zero** times. Two consequences the plan-language ("PARTIAL", "LIVE-VERIFY pending") undersells:

1. **Conversation is the binding constraint, not combat.** The agent rarely dies — it stalls. The identity-creation-screen trap and submenu thrashing eat whole sessions before any goal progress happens. Promoting this finding here so it lives in the strategic plan, not only in `MEMORY.md`.
2. **Combat is unexercised, not just unverified.** Because the agent gets stuck in town, it never reaches a hostile, so `attack`/`flee`/`grind_combat`/death-handling have **never run end-to-end in a real encounter.** "LIVE-VERIFY pending" reads like one checkbox; it's actually an entire untested limb.

---

## Remaining Work — Phased Plan

Ordering rationale: 1–2 stop the most common premature deaths; 3 makes goal pursuit actually work; 4 delivers the "any situation" guarantee; 5 opens up the game's depth; 6 is the quality flywheel; 7 (late, optional) is unattended hardening.

**Ordering reality-check (2026-06-11)**: the rationale above was written assuming *premature death* is the limiting failure. The logs say otherwise — the agent doesn't die, it **stalls in town conversation** (see "Observed live behavior" above). That inverts the practical priority of two items:

- **Conversation robustness now comes before the rest of combat depth** (reorder applied — NORTHSTAR §8 step 2). It *was* buried in Phase 3 (3.1) / inside M3 (`journey`); it's now the next concrete build after the interrupt-loop keystone. Rationale: it's the cheapest fix to the failure mode that currently wastes every run, and combat depth can't even be *exercised* until the agent stops getting stuck in town long enough to reach a hostile. Scope: submenu/identity-trap handling + `ConverseSkill` (3.1).
- **Attack-depth (2.1 / NORTHSTAR M2) stays the combat keystone, but its LIVE-VERIFY is gated on reaching a hostile** — which in a town run means either surviving conversation or shipping `journey` (M3) to travel to a lair. So the M2 "full combat grind" verification has an implicit dependency on M3 (or on conversation robustness getting the agent out of town). Sequence accordingly: don't mark M2 done on unit tests alone while no live encounter has ever occurred.

Otherwise the ambition-order in NORTHSTAR §8 holds: the interrupt-loop keystone is done, and `journey` + eval harness remain the right large bets. The one substantive change is **pulling conversation out of the M3 bundle and treating it as a near-term blocker in its own right.**

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

3.1 **Multi-turn conversations.** ✅ **DONE 2026-06-12.** `TalkToSkill` (route + initiate single topic), reliable phase-2 choice selection + identity-trap filtering, per-NPC asked-topics dedup (`AskedTopics`, `memory/asked_topics.json` — normalized per-`hist_fig_id` topic sets, inline `[already asked]` + `_build_hint` note), and **`ConverseSkill`** (`converse:<unit_id>`): one intent → a full deterministic conversation sweep (route adjacent → `A_TALK` → address-nearest → ask highest-priority *new* topic → re-initiate when DF closes the dialogue → repeat to a 4-topic budget), zero LLM calls between, dedup + transcript shared with the LLM path. LIVE-VERIFIED end-to-end. Topic priority is keyword-tiered; emotes/accusations, `(… menu)` submenu-openers, and `assume_identity` are skipped. **Deferred to later in Phase 3**: auto-diving the "Ask for directions" 98-item submenu (stays LLM-driven for now — `converse` hands back when only submenu/meta remain); structured rumor extraction is 3.3. DF ends dialogue after single exchanges — `ConverseSkill` re-engages via `talk_existing_conversationst` automatically.

3.2 **Spatial Layers 2–3** (design below, unchanged): topological waypoint graph + rumored-site registry. Concretely: `spatial/topo_graph.json` nodes created on area-type change / named-location reveal; `spatial/sites.json` entries from quest log + conversation rumors with `estimated_pos` + `confidence`. New intent `goto_rumor:<id>`: fast travel toward `estimated_pos`, then `explore` within an `exploration_budget`, updating the entry on confirm/refute.

3.3 **Rumor pipeline glue.** Quest-log lines and conversation transcripts currently land in memory as text. Add an extraction pass (cheap LLM call on `dialogue_ended` / quest-log read) that emits structured site-registry candidates. This closes the loop: hear rumor → registry entry → goto_rumor → arrive → goal progress.

3.4 **Fast travel end-to-end** (carried from the verification checklist; quest travel depends on it): run the full enter→steer→auto-stop→exit journey live; verify ChunkMap absolute-coordinate stability across the region change; snap the exit position onto the topo graph (trap #5 below). Tune `_STOP_DISTANCE`/no-progress handling. **PARTIAL (2026-06-12)**: the army-formation blocker is FIXED + LIVE-VERIFIED (travel now engages, forms the army on the first move, and steers — see Confirmed Findings). **World-level routing landed (2026-06-12)**: `JourneyBehavior` (M3) now re-enters travel across legs and routes around terrain barriers via a collision-feedback detour-heading rotation (see Last major change). **Still open**: full-journey LIVE-VERIFY (an observed unsupervised trek to a distant site), region-change coord stability + topo-snap (un-exercised — the agent has not yet reached a distant site), and travel auto-stop tuning.

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

6.1 ✅ **Token budget management** (DONE 2026-06-14). Situational `summary()`: `_mode()` → combat (threats + map + weapons-only inventory; sites/factions/bystanders suppressed) / conversation (dialogue + relationships; world-site list + inventory suppressed) / exploration (map + sites + full inventory + factions). Every list block is capped via module `_CAP_*` constants + `_capped()` (overflow renders a "(… N more)" tail) — notably the 98-item "Ask for directions" menu (→ 25). Stable prefix (`PromptBundle`) untouched. LIVE-VERIFIED on the running adventurer; unit-tested in `tests/test_summary_budget.py`. *Remaining under 6.1*: the Phase 4 knowledge-pack injection respecting the same budget is not yet enforced.

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

*Stable DF/DFHack gotchas found in play. Items that duplicate CLAUDE.md's connection-layer / v53 API reference have been dropped — see CLAUDE.md for those.*

- Army position coords are 3× embark-tile coords; fast travel tracks via army pos.
- `getAdventurer()` returns nil during fast travel — handle gracefully.
- Entering travel **while obstructed** (site walls/rivers) wedges the UI: `menu=Travel`, `travel_origin` set, but `player_army_id=-1` and ALL travel input is rejected. Recovery is the `A_END_TRAVEL` key (LIVE-VERIFIED 2026-06-11; the old belief that exit needs an 'x'-button click is wrong — the x is a texture, invisible to `readTile`). The fast-travel *help* dialog, by contrast, needs a mouse click on its Okay button. `FastTravelController`/`UnstickSkill` detect and recover from the wedge.
- Sleep flow (LIVE-VERIFIED 2026-06-10): `A_SLEEP` opens a Help dialog the first time (auto-handler dismisses it); then `dungeonmode/Sleep`. Default is "Wait" not "Sleep" — press `A_SLEEP_SLEEP`, then `A_SLEEP_DAWN`, then SELECT. Sleep until dawn ≈ 1200 ticks.
- `A_INV_EATDRINK` is the single combined eat/drink key (no separate A_EAT/A_DRINK). Food item types: MEAT=48, FISH=49, FISH_RAW=50, SEEDS=53, PLANT=54, PLANT_GROWTH=56, CHEESE=71, FOOD=72, EGG=88; DRINK=69.
- Physiological timers live in `adv.counters2` (`hunger_timer`, `thirst_timer`, `sleepiness_timer`, `exhaustion`), all counting up from 0; above ~322,000 the game shows STARVING/DEHYDRATED.
- DFHack console log on Steam Linux: `~/.steam/debian-installation/steamapps/common/Dwarf Fortress/stderr.log`. Deferred-callback errors appear here as `opendwarf--act <action> error: <message>`; synchronous-script errors appear in the RPC reply text instead. Both channels are captured (`execute_action()` + `consume_action_errors()`).
- **Conversation phase-2 dialogue choices (LIVE-VERIFIED 2026-06-12)**: only ~12 choices render at once; `conversation.choice_scroll_position` is a fine-grained **pixel scroll (~3 units per choice)**, NOT an index — set it to `idx*3` to bring choice[idx] to the top visible row. Labels render at a fixed x-column as `lowercase-letter + NUL + UPPERCASE`. Selection is **mouse-click only** (keyboard SELECT/CURSOR don't work; `doRealize()` on a phase-2 choice HANGS the RPC); clicks need **pixel-precise** coords (`gps.precise_mouse_x = tile_x * gps.tile_pixel_x`, `tile_pixel_x/y` = 8/12). The screen buffer reflects a new scroll only AFTER a frame renders, so find+click MUST be deferred (`dfhack.timeout(2,'frames',…)`). Phase-1 (`selecting_conversation`) still uses `select_option[idx]:doRealize()` (reliable).
- **`A_TALK` select_npc menu (LIVE-VERIFIED 2026-06-12)**: lists ONE other named NPC + an *address-nearest* system option — **not** a pick-any-nearby-unit list. Route adjacent, then select address-nearest. That option is `adventure_option_talk_new_conversationst` on first contact and `adventure_option_talk_existing_conversationst` on re-engagement — match any `*_conversationst`, never `assume_identityst` (which opens the identity-creation screen, a known derail; filtered in `registry._enumerate_conversation`). After a topic DF *usually* closes the dialogue but sometimes keeps it open; a robust sweep handles both. `ConverseSkill` encodes all of this.
- The "Ask for directions (new menu)" submenu is a **98-item list of "directions to <site>" / "whereabouts of <figure>"** (rumor/quest goldmine), not a compass picker. "Change the subject (new menu)" returns to the main ~29-choice menu.
- Viewscreen stack walk: `df.global.gview.view` → `.child` chain (max 32 levels); `cur._type.name` gives `"viewscreen_dungeonmodest"` (lowercase, no brackets), fallback `tostring(cur._type)` → `"<type: viewscreen_dungeonmodest>"`.
