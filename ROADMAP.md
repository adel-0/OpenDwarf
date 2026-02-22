# OpenDwarf — Roadmap

Tracks known gaps and unknowns on the path to a fully autonomous DF adventurer.

---

## Priority 1 — Wider State Coverage

### ✓ Skills & XP (DONE)
- **Implemented**: Read `adv.status.current_soul.skills`, filters non-zero skills, shows top 8 in summary

### ✓ Equipment Quality & Value (DONE)
- **Implemented**: Added `item:getQuality()` to inventory extraction, maps 0-5 → quality names

### ✓ World & Region Context (DONE)
- **Implemented**: Extracts world name via language word lookup. Site detection via `rgn_min/max` bounds (working when at site).

### Quest Log & Objectives
- `df.viewscreen_adventure_logst` is never read; world agreements tried but no active quests to verify
- **Fix**: Open/read the adventure log viewscreen to extract quest text (requires navigating to it mid-loop, or detecting when it's the current screen)

### ✓ NPC Reputation & Relationships (DONE)
- **Implemented**: Reads `unit.hist_figure_id` → `df.historical_figure`, extracts `entity_links` (faction/civilization membership) and `histfig_links` (personal HF-to-HF relationships like FRIEND/SPOUSE/ENEMY). Shown in summary as "Factions" and "Known NPCs nearby". Confirmed working fields: `hist_figure_id`, `entity_links[i].entity_id`, `histfig_links[i].target_hf`, enums `histfig_entity_link_type` / `histfig_hf_link_type`.

---

## Priority 2 — Item Interaction

### ✓ Item Pickup & Drop (DONE)
- **Implemented**: `pickup_N` / `drop_N` / `wield_N` actions. `opendwarf--act.lua` opens the relevant menu (`A_GROUND` / `A_INV_DROP` / `A_INV_DRAW_WEAPON`, deferred 1 frame), navigates CURSOR_DOWN N times and presses SELECT (3 more frames). Floor items shown in state summary with 0-based indices. Hauled items shown with indices. `cursor_up`/`cursor_down` also added. All key names empirically verified present in df.interface_key.

### ✓ Equip / Wield (DONE)
- **Implemented**: `wield_N` (`A_INV_DRAW_WEAPON`), `wear` (`A_INV_WEAR`), `remove_item` (`A_INV_REMOVE`) all verified working key names.

### ✓ wait_long fixed (DONE)
- **Implemented**: `wait_long` now uses `A_WAIT` (10 instants, the '.' key). Previously it incorrectly used `A_MOVE_SAME_SQUARE` same as `wait`. Empirically verified: tick advances by 10 per `wait_long`.

### ✓ Rest / Sleep (DONE)
- **Implemented**: `rest` action maps to `A_SLEEP` (opens rest/sleep menu). Verified key exists. Sub-menu choices (`A_SLEEP_SLEEP`, `A_SLEEP_WAIT`, `A_SLEEP_DAWN`) can be selected via conversation_N or cursor+select after opening.

---

## ✓ Priority 3 — Strategic / Goal Management Layer (Layer 3) (DONE)

Design informed by BDI agent theory, Voyager's automatic curriculum, Generative Agents' trigger-based reflection, and LLM-on-NetHack failure analysis. Key lesson from the NetHack paper: zero goal hierarchy = local optima trap — the agent makes locally reasonable moves that never accumulate toward anything.

---

### 3.1 Goal Data Model

Goals are structured records, not free-text strings. Free-text makes lifecycle tracking unreliable — the LLM can't consistently detect its own goal transitions.

```python
@dataclass
class Goal:
    id: str
    description: str            # natural language, injected into LLM prompts
    type: GoalType              # SURVIVAL | PHYSIOLOGICAL | SOCIAL | EXPLORATION | RENOWN | NARRATIVE
    status: GoalStatus          # CANDIDATE | ACTIVE | ACHIEVED | DROPPED | FAILED
    priority: float             # 0.0–1.0, recomputed at each revision
    parent_id: str | None       # sub-goal of this parent, if any
    sub_goal_ids: list[str]     # decomposed children
    created_tick: int
    target_hf_id: int | None    # resolved hist_fig id for person-targeting goals; None = unresolved
    acquisition_method: str | None  # "LOOT" | "BUY" | "TAKE" — for item goals
    exploration_budget: int | None  # ticks before unresolved location goal auto-fails; None = unlimited
    success_condition: dict | None  # structured check: {type, z_range, site_type, ...}
```

**Goal lifecycle** — transitions triggered by Python checking `GameState`, not inferred from LLM output:
```
CANDIDATE ──adopt──▶ ACTIVE ──achieve──▶ ACHIEVED
                       │
                       ├──drop──────▶ DROPPED
                       └──fail──────▶ FAILED ──replan──▶ CANDIDATE
```

Goals form a two-level tree in practice: one long-term goal (e.g. "gain renown") decomposed into ordered sub-goals (e.g. "complete Thane Ulfgar's quest"). The active leaf sub-goal drives Layer 2 planning.

---

### 3.2 Conflict Resolution — Survival Gates

Permadeath means survival isn't just a high-priority goal — it's a hard gate. These checks happen in Python *before* the goal manager LLM call, not inside it.

- `health < 25%` OR hostile unit within 5 tiles → only SURVIVAL goals eligible
- `exhaustion_critical AND hostile_nearby` → treat as SURVIVAL flee trigger (not just a goal filter)
- `exhaustion_critical AND safe` → only PHYSIOLOGICAL goals eligible
- `hunger/thirst_critical AND hostile_nearby` → ignore physiological, SURVIVAL goals only
- `hunger/thirst_critical AND safe` → PHYSIOLOGICAL eligible alongside SURVIVAL

Within the eligible set, the LLM reasons freely about priority. The goal manager prompt explicitly asks for a risk assessment alongside the priority ranking — the LLM should justify why it's worth pursuing a risky goal, not just silently assign it high priority.

---

### 3.3 Revision — Trigger-Based, Not Per-Turn

Goal management LLM calls run only on meaningful events, not every tick. Continuous reconsideration is expensive and causes indecisiveness.

**Revision triggers:**
- Combat resolved (victory or retreat)
- Sub-goal ACHIEVED or FAILED
- NPC dialogue ended (voluntary or forced)
- Forced dialogue started unexpectedly (villain parley, ambush, etc.)
- Health threshold crossed (25%, 10%)
- New named location discovered
- Session start

`wait_long` is also a natural revision moment — the adventurer is resting, a good narrative beat for reflection.

On trigger: LLM receives the current goal tree, triggering event, and world context. Output: revised priorities, new CANDIDATE goals, drops, reasoning trace. Python applies changes and persists to disk.

---

### 3.4 Goal Generation for Open-Ended Play

DF has no win condition. When the active goal tree is shallow (< 2 leaf goals), the goal manager prompt includes a generation phase: propose 3–5 candidate goals that are feasible given current skills/equipment/world knowledge, then rank the full pool. This is the only mechanism for injecting new long-term direction — no separate curriculum system needed.

---

### 3.5 Implementation Traps

**1. Fast travel is a mode switch, not a move sequence**
A goal like "travel to Goblin Pits" cannot be executed as a sequence of local MOVE actions — the agent will walk to the loaded map edge and stall. The StrategicPlanner must decompose travel goals into sub-goals: `[enter_fast_travel, navigate_overworld, exit_at_site]`. After exiting, verify the agent is actually inside the site bounds (check tile/area type), not spawned in generic wilderness a tile away. Re-enter travel and adjust if not.

**2. Names are not unique — always resolve to `hist_fig_id`**
Procedurally generated names collide. Goals targeting a person must resolve the target to a `historical_figure_id` integer at creation time. An unresolved target forces `type = INVESTIGATE` — the agent must find and identify the person before any direct-action goal becomes eligible. Never act on a string name alone.

**3. `success_condition` for location goals must check z-level**
2D coordinate proximity marks a dungeon goal ACHIEVED when the agent stands on the mountain above it. Success conditions for any underground site must require the agent is actually inside: verify `current_z < surface_z` or check that the current tile's area type matches the target site type. The `success_condition` field is a structured dict for this reason — not a string.

**4. Physiological gates are danger-contextual, not flat**
"Exhausted" mid-combat is a flee trigger. "Hungry" mid-combat is ignorable. A flat "exhaustion critical → only PHYSIOLOGICAL eligible" rule will make the agent attempt to sleep in the middle of a fight. See §3.2 for the full context matrix.

**5. Forced dialogue must not silently abort the current goal**
NPCs can force a conversation screen open during movement or combat. The action executor must detect `ui_advmode_menu == Conversation` unexpectedly and surface it as a `forced_dialogue_started` revision trigger rather than treating it as a movement failure. The goal manager then provides context for the conversation; the current goal resumes after dialogue ends.

**6. Item goals need an `acquisition_method`**
Picking up a shop item without trading triggers immediate town hostility. Item acquisition goals must specify `LOOT` (dungeon/ruin, unclaimed), `BUY` (requires merchant interaction), or `TAKE` (explicitly hostile context). The goal generator infers this from site type and item ownership flags. The tactical layer must refuse a pickup that would violate the goal's declared method.

**7. Rumors can be stale — goals need `exploration_budget`**
A quest target may have died decades ago. After searching for `exploration_budget` ticks without finding the target, the goal auto-transitions to `FAILED` with reason `TARGET_NOT_FOUND`. The `replan` path then lets the goal manager generate an INVESTIGATE sub-goal ("verify whether the target still exists") rather than looping forever.

---

### 3.6 Implementation Tasks

**Core:**
- [x] Define `Goal`, `GoalStatus`, `GoalType` dataclasses in `opendwarf/goals/model.py`
- [x] Build `GoalManager`: in-memory goal tree, lifecycle transitions, serialize/load to `goals/active_goals.json`
- [x] Replace `--goal` CLI string with goal tree initialization
- [x] Trigger detection in main loop: combat end, sub-goal transitions, health thresholds, dialogue end

**Goal management LLM call:**
- [x] `GoalManagerPrompt`: packages goal tree + triggering event + world context
- [x] Parse output: priority updates, new candidates, drops, reasoning trace
- [x] Survival gate check in Python before prompt is built

**Strategic planner (Layer 2):**
- [x] `StrategicPlanner`: active leaf goal → LLM call → ordered step list with contingencies
- [x] Inject active sub-goal + current plan step into every tactical turn prompt

**Sub-goal lifecycle:**
- [x] On sub-goal ACHIEVED: activate next sibling or mark parent complete
- [x] On sub-goal FAILED: propagate upward, trigger revision cycle

---

## Priority 4 — Memory System

**Storage**: All non-spatial memories are MemSearch markdown notes. Markdown files are source of truth; MemSearch maintains the vector index on top. No separate database.

**Design informed by**: Generative Agents (retrieval scoring), MemoryBank (Ebbinghaus-inspired decay tiers), Reflexion (failure post-mortems), SPRING (static mechanics injection).

**Core principles**:
- No LLM calls for every store/retrieve — only for importance scoring at write time, reflection/consolidation, and post-mortems
- Significance filter gates every write — most observations are discarded
- Retrieval scores on recency × importance × relevance, not raw similarity
- Semantic notes update in place when the same entity is observed again (no duplication)

---

### 4.1 Memory Types & Cross-Session Persistence

| Type | What it stores | Cross-session | Notes |
|------|---------------|---------------|-------|
| Episodic | Combat outcomes, discoveries, deaths, major NPC interactions | Major events only (importance ≥ 8) | Tactical observations (enemy at position X) expire within-session only |
| Semantic | Named facts: locations, NPCs, factions, quest targets | Yes — all | Update-in-place when same entity observed again |
| Procedural | Combat tactics, negotiation patterns, item strategies | Verified only (≥ 2 confirmed successes) | Structured records, not prose |
| Spatial | Tiles, sites, routes | Yes — all | Separate system — see Spatial Memory section below |

Working memory (current turn scratchpad, recent decisions, active goal context) is not persisted to MemSearch.

---

### 4.2 Significance Filter

Every candidate memory passes a gate before storage. Write only if:
- Triggered by a goal-revision event (same set as §3.3 revision triggers), **or**
- LLM-assigned importance score ≥ 7

Tactical observations below threshold are discarded. The LLM assigns importance as one additional field in the write call — not a separate round-trip. Calibration: frame importance as "how much would forgetting this hurt a future decision?" Reference scale: 9 = first discovery of a creature weakness; 5 = found a sword in a dungeon; 2 = killed a rat. Without this anchor the LLM inflates scores and the filter becomes useless.

**Distinguish mechanic from flavor**: DF generates verbose, atmospheric descriptions ("The goblin seems annoyed by the flies," "The merchant eyes you nervously"). These are flavor — they carry no actionable game-mechanic information and must not be stored as semantic facts. The importance-scoring prompt must include explicit examples of DF flavor text and instruct the LLM to score them 1–2 regardless of surface plausibility. The primary defence is that most memory writes are triggered by structured game-state events (combat resolution, dialogue end), not raw text parsing — flavor text is only a risk when the LLM synthesizes observations into notes during reflection.

---

### 4.3 Storage Format

Each memory is a markdown file with YAML frontmatter:

```markdown
---
id: mem_00412
type: episodic          # episodic | semantic | procedural
tick: 18450
importance: 8
tags: [combat, undead, victory]
entities: [hist_fig_1234]    # resolved hist_fig_id or site_id — never name strings
links: [mem_00398]           # related memory IDs
source: observed             # observed | inferred | reflection
confidence: 1.0              # 1.0 = direct observation; <0.5 = LLM inference, not auto-injected
cross_session: true
---

Defeated a wight near the Tomb of Ul at tick 18450. It ignored slashing weapons —
only blunt attacks landed. Took severe arm damage before the kill.
```

**Entity IDs, not name strings**: Always tag with `hist_fig_id` / `site_id`. DF names collide. Semantic update-in-place queries by entity ID, not text similarity.

**Non-historic units have no `hist_fig_id`**: Most active units (random wolves, generic goblins, common merchants) have `hist_figure_id = -1` or unset. Only named/notable historic figures carry a real `hist_fig_id`. For non-historic units, the `entities` field must use a type-based tag instead (e.g., `unit_type:GOBLIN`) — the `unit.id` is transient (only valid while the unit is loaded) and must never be stored as a cross-session entity key. Non-historic encounters cannot produce cross-session semantic notes about individuals; they can only produce type-level notes ("goblins in this area carry crossbows").

---

### 4.4 Retrieval Scoring

Generative Agents formula, adapted for DF tick time:

```
score = recency × importance_norm × relevance
```

- **Recency**: `0.99 ^ (ticks_elapsed / 100)` — a memory from 1,000 ticks ago scores ~0.90; from 10,000 ticks ~0.37
- **Importance_norm**: raw score / 10 → [0.0, 1.0]
- **Relevance**: cosine similarity from MemSearch query

All three multiply — a zero on any dimension means no retrieval. Top-5 results, split by task context:
- Combat → pre-filter on tag `combat` or `threat`
- Exploration → pre-filter on tag `location` or `site`
- Conversation → pre-filter on tag `npc` or `faction`

Hard limit: never inject more than 5 memories per turn regardless of query results. More degrades performance (empirically observed in NetHack agent research — context pollution is the primary RAG failure mode).

**Macro-time decay clamping**: Raw DF ticks are a poor decay clock because macro-time events (fast travel, sleep) advance ticks by tens of thousands in a single action. A single overland hop of 50,000 ticks yields `0.99^500 ≈ 0.007` — effectively zeroing all tactical memories. Clamp tick delta for decay purposes: no single action may contribute more than 1,000 ticks to the decay counter, regardless of how many real ticks elapsed. Sleep and fast travel fire this cap. Only per-action ticks (combat, movement, dialogue) accumulate normally.

---

### 4.5 Decay & Eviction

Two half-life tiers, enforced lazily at retrieval time (no scheduled sweep):

- **Tactical notes** (importance < 5): expire after 5,000 ticks without retrieval. If `ticks_since_last_access > 5000` at retrieval time → mark `expired`, exclude from results.
- **Strategic notes** (importance ≥ 7): never expire by time alone. Evicted only when contradicted by a newer observation of the same entity.
- **Procedural notes**: evicted if `success_rate < 0.3` after ≥ 5 total attempts. Success rate is updated in frontmatter each time the tactic is attempted.

**Update-in-place for semantic notes**: Before writing a new semantic note about a known entity, query MemSearch by entity ID. If an existing note is found, update it (revise tick and content) rather than create a new one. This is the primary mechanism preventing semantic fact duplication.

---

### 4.6 Reflexion Post-Mortem Buffer

On adventurer death or FAILED root goal: a single LLM call produces a ~2-sentence post-mortem:
- What went wrong
- What to do differently

Appended to `memory/postmortems.md` — flat file, not in MemSearch. Max 10 entries; oldest dropped when full. Before appending, check for semantic similarity > 0.85 with existing entries (MemSearch query on postmortems content) — update the existing entry rather than duplicate.

The **entire file is injected at every session start**, before any retrieval. Zero retrieval latency; always present for all runs.

Example entry:
```
[tick 18900, death] Engaged two goblins simultaneously without checking HP first.
Never fight multiple opponents when below 60% health without a clear retreat path.
```

---

### 4.7 Reflection / Consolidation

A separate, explicitly triggered LLM call that synthesizes recent episodic memories into semantic or procedural notes. **Not automatic per-turn.**

Triggers:
- Sum of importance scores of the last 20 episodic memories exceeds 120
- Session end (always run before shutdown)

The reflection prompt receives the recent episodic batch and outputs 1–3 higher-order insight notes (e.g. "Eastern ruins consistently spawn hostile undead — likely a burial site"). These are stored as semantic notes with `source: reflection`, `importance: 7–8`. This is the only mechanism that converts episodic → semantic automatically; all other semantic writes are from direct observation.

---

### 4.8 Static Mechanics Injection (SPRING-style)

`memory/df_mechanics.md` — hand-authored ~500-token guide covering:
- Creature danger tiers (kobolds → trolls → megabeasts)
- Combat basics (anatomy targeting, weapon type effectiveness, size/skill disadvantage)
- Physiological needs (hunger/thirst/exhaustion thresholds and game consequences)
- Economy (trading, theft hostility triggers, quest reward types)
- Site types and what to expect (fortresses, ruins, lairs, towns)

Injected verbatim into every session system prompt. Never retrieved — always present. Written once, zero runtime cost.

---

### Spatial Memory
- No persistent map — agent re-explores already-visited areas

**Why a pure node-edge graph fails**: Knowing "Oaktown connects to Stonehall" is useless when the agent must navigate *between* known nodes — it has no tile-level knowledge of that space, can't detect obstacles, and can't recognise when it's been somewhere before. One step off a known edge and it's lost.

**Design: three co-existing layers** (never merged — each solves a different problem)

#### Layer 1 — Sparse Chunk Grid (tile-level, exact knowledge)

A `dict` keyed on `(chunk_x, chunk_y, z)` where chunks are 16×16 world tiles. Cell values: `UNKNOWN | PASSABLE | WALL | WATER`. Only visited chunks exist in memory — no allocation for unexplored world. World coordinates used directly (DF exposes them via `dfhack.units.getPosition`).

The existing 5×5 `map_tiles` extracted each turn feeds directly into this. On area transitions, scan a wider radius with `dfhack.maps.getTileType`.

Pathfinding: A* on the chunk grid. Key rule: `UNKNOWN` tiles get high traversal cost, not infinite — the agent will path through unknown space when no known route exists, recording tiles as it goes. No LLM involvement.

Persistence: serialise the chunk dict to `spatial/chunks.msgpack` each session.

#### Layer 2 — Topological Waypoint Graph (site-to-site, coarse)

Nodes for *qualitatively distinct places* only — not every tile. Creation triggers:
- Area type changes (wilderness → town, open → dungeon)
- Agent deliberately returns to a coordinate (it's become a recognised place)
- NPC dialogue reveals a named location

Edges carry `direction` (compass bearing), `distance_tiles`, `terrain`, and `confirmed` flag. Unconfirmed edges come from NPC rumours or world data — the agent hasn't walked them.

Node count for a full playthrough: 200–500. Serialised to `spatial/topo_graph.json` (~50KB).

#### Layer 3 — Site Registry (rumoured + visited locations)

Handles knowledge that has no tiles yet: quest targets, NPC hints ("there's a goblin fort northeast"), world-data sites. Each entry stores:
- `exact_pos` (world coords, set on visit) or `estimated_pos` (dead-reckoned from NPC bearing + distance hint)
- `confidence` (0.0–1.0): 1.0 = visited, 0.4 = NPC rumour, 0.2 = vague overheard hint
- `source` and `notes` (armorer here, hostile encounter, etc.)

When the agent visits an estimated site, `exact_pos` is set and a topo node is created or linked.

#### Navigation across layers

High-level route: A\* on the topo graph → ordered list of waypoints. Low-level execution: A\* on chunk grid toward current waypoint, replanning each turn as new tiles are recorded. When the grid has no complete path, the agent explores toward the waypoint direction — the frontier-following strategy used in robotics. No LLM needed for any of this.

#### LLM interface — what the LLM actually sees

Never send raw tiles, coordinates, or the full graph. Generate a structured text block from Python each turn (~100–150 tokens):

```
-- Spatial Context --
Current area: Wilderness (8 chunks explored nearby)
Active route to Goblin Pits: waypoint 2/4 — "Crossroads at Blackwood" ~80 tiles NE
Unexplored frontiers: N (12 tiles), NE (8 tiles)
Nearby sites:
  - Ironhold (fortress) 240 tiles NE [visited — armorer, safe]
  - Abandoned shrine 12 tiles SE [unlooted]
  - "Goblin stronghold" ~1 day N (unverified, heard from merchant)
```

The LLM decides *direction* (continue route / explore frontier / detour). Python handles step-by-step execution.

#### Implementation traps (empirically verified)

**1. `getPosition()` returns LOCAL coordinates, not absolute** *(verified)*
`dfhack.units.getPosition()` returns tile coords relative to the currently loaded map area, not the world. The loaded map origin is `(map.region_x * 16, map.region_y * 16)`. The chunk grid must convert to absolute before storing: `abs_x = df.global.world.map.region_x * 16 + local_x`. Without this, positions become garbage when the loaded map region shifts during inter-region travel.

Fast travel uses its own coarser coordinate space. `df.global.adventure.travel_origin_x/y/z` holds the local-coordinate departure point (`-1, -1, 0` = not in fast travel). The overworld (fast travel) grid is a completely separate layer — don't mix it with the local chunk grid.

**2. Z-levels are not automatically connected** *(verified)*
The chunk grid treats `(cx, cy, z)` and `(cx, cy, z+1)` as disconnected planes. A* will never cross z-levels unless vertical connections are explicitly modelled. When recording tiles, detect vertical portal shapes via `df.tiletype.attrs[tt].shape`:
- `6` = STAIR_UP, `7` = STAIR_DOWN, `8` = STAIR_UPDOWN, `9` = RAMP, `10` = RAMP_TOP

Tag these tiles as vertical edges in the topo graph (Layer 2 nodes), linking `(x,y,z)` to `(x,y,z±1)`. Without this, a dungeon's floors are disconnected islands.

**3. PASSABLE is dynamic** *(confirmed valid, no fix mechanism verified)*
Rivers freeze (WATER → PASSABLE), then melt. Doors get locked. A tile marked PASSABLE may not be passable next turn. Two mitigations:
- Store `last_verified_tick` on each passable cell; treat stale entries (many ticks since last visit) as UNKNOWN rather than PASSABLE
- On movement failure: no dedicated bump flag exists. Detect via position delta — if `pos_before == pos_after` after an intended move, the move failed. Immediately downgrade the blocking tile from PASSABLE to UNKNOWN. This is the primary invalidation mechanism.

**4. Climbing bypasses the passability model** *(valid, not yet empirically tested)*
WALL tiles are not always impassable in adventure mode — rough stone, trees, and cliff faces can be climbed given the Climber skill. A pure PASSABLE/WALL model will incorrectly block emergency escape routes. Future enhancement: add `CLIMBABLE` as a cell type, and allow A* to use climbing edges with a high cost modifier gated on the agent's Climber skill level.

**5. Fast-travel exit imprecision — fuzzy node snap required**
Exiting fast travel does not always land the agent on the exact targeted tile — DF spawns you near the destination. If node linkage uses exact coordinate match, the agent will create a duplicate topo node a few tiles from an existing one and fragment the graph. Fix: on area arrival, check for any known topo node within a 10-tile radius. If one exists, snap to it rather than creating a new node.

**6. Natural ramps are not reliably traversable**
A RAMP tile at Z=N is only usable if there is open space at Z=N+1 directly above. Natural terrain generates ramps with walls above them — they appear as RAMP shape in `df.tiletype.attrs` but are impassable. Do not add vertical edges to the topo graph based on tile type detection alone. Record vertical edges only after a successful Z-level transition has been observed empirically (position z changes after movement toward a ramp/stair tile). When pathfinding, prefer confirmed STAIR shapes (6/7/8) over RAMP shapes (9/10).

**Fix**: Implement the three-layer spatial memory as described (purpose-built for pathfinding, not MemSearch)

### 4.9 Retrieval Integration — What the LLM Sees Each Turn

Three memory blocks injected into the turn prompt (total budget ~300 tokens):

```
-- Session lessons --
[postmortems.md contents, if non-empty]

-- Retrieved memories (top 5) --
[recency × importance × relevance scored, context-filtered by task type]

-- Spatial context --
[generated from spatial memory system — see §4.5]
```

Working memory (last 3 decisions, active sub-goal, current plan step) is already in the tactical prompt and is not duplicated here.

---

### 4.10 Implementation Traps

**1. Context pollution is the primary RAG failure mode** *(NetHack agent finding)*
More retrieved context is not better. Hard cap at 5 memories, always tag-filtered to task type. A generic top-5 query without context filtering injects exploration memories during combat and vice versa — measured to hurt performance.

**2. Importance inflation**
Without a calibration anchor in the prompt, LLMs score everything 8–10 "just in case." Include explicit reference examples in the importance-scoring prompt (see §4.2). If the distribution of stored importances skews above 7, the significance filter is broken.

**3. Entity resolution required for update-in-place — entity ID is the only valid key**
Update-in-place MUST use the actual `hist_fig_id` or `site_id` integer as the lookup key — never name strings, never vector similarity. "This note looks similar to the same entity" is not a valid match condition: if `hist_fig_id` matches, it is the same entity; if not, it isn't. For non-historic units (no `hist_fig_id`), update-in-place keys on `unit_type` tag — producing type-level notes only, never individual notes. Resolve entity IDs at write time using the same logic as goal target resolution (§3.5 trap #2).

**4. Cross-session memory poisoning**
An LLM-inferred fact stored cross-session can corrupt future runs. Any note with `source: inferred` or `source: reflection` carries a `confidence` field. Notes with `confidence < 0.5` are excluded from automatic injection — they are only retrieved on direct explicit query.

**5. Reflexion buffer drift**
If the agent repeats the same mistake, the buffer fills with similar entries. Deduplicate before appending (see §4.6). A saturated buffer of near-identical post-mortems gives less signal than a single well-maintained entry.

---

### 4.11 Implementation Tasks

**Core storage:**
- [ ] Define memory note schema (YAML frontmatter fields, content format per type)
- [ ] Build `MemoryWriter`: significance filter → importance scoring → MemSearch write
- [ ] Build `MemoryRetriever`: recency × importance × relevance scoring on MemSearch results with tag pre-filtering
- [ ] Implement update-in-place for semantic notes (entity ID lookup before write)

**Decay & eviction:**
- [ ] Add `last_accessed_tick` to frontmatter, updated on every retrieval hit
- [ ] Lazy eviction check at retrieval time (tactical tier: 5,000-tick TTL)
- [ ] Procedural success-rate tracking: update frontmatter on tactic attempt; evict if rate < 0.3

**Session integration:**
- [ ] Inject `postmortems.md` at session start (before tactical prompt)
- [ ] Inject `df_mechanics.md` into system prompt (static, always present)
- [ ] Wire top-5 retrieval into tactical turn prompt (§4.9 format)
- [ ] Trigger memory writes on goal-revision events (hook into existing §3.3 triggers)
- [ ] Author `memory/df_mechanics.md` initial content

**Reflexion:**
- [ ] Write post-mortem LLM call on death / FAILED root goal
- [ ] Enforce 10-entry cap + similarity dedup on `postmortems.md`

**Reflection/consolidation:**
- [ ] Implement importance-sum threshold check after each episodic write
- [ ] Build reflection prompt + output parser (1–3 insight notes → semantic/procedural writes)
- [ ] Run reflection at session end

---

## Priority 5 — Prompt & Context Quality

### ✓ Action Validation (DONE)
- **Implemented**: Pre-execution validation of move actions against 5x5 map grid. Invalid moves (walls/unknowns) silently substitute `wait` with warning log.

### Token Budget Management
- `GameState.summary()` can grow large with no intelligent filtering
- **Fix**: Situational summarization — prioritize by context (combat→threats, exploring→map, conversation→NPC)

### Richer Turn Context
- Turn prompt lacks current subgoal, relevant memories, recent decision history
- **Fix**: Inject active subgoal, top retrieved memories, and last 3 decisions

---

## Unknowns Requiring Empirical Testing

- Exact instant costs per action (movement, combat, etc.)
- Full `ui_advmode_menu` and `adventure_game_loop_type` enum values
- Programmatic combat targeting (body part / attack type selection via menu navigation)
- Item pickup/drop key sequences
- Full `df.global.adventure` field list (discover via `df.global.adventure._fields` in live DFHack console)

---

## Feature Dependency Map

```
Memory System
  └─ requires: wider state coverage (what to remember)
  └─ requires: session persistence (where to store it)

Strategic Layer
  └─ requires: quest log visibility (what goals exist)
  └─ requires: session persistence (plan survives restart)
  └─ enhanced by: memory system (informed planning)

Item Interaction
  └─ requires: equipment quality in state (informed decisions)
  └─ enhances: strategic layer (gear-up subgoals become executable)

Richer Turn Context
  └─ requires: strategic layer (active subgoal)
  └─ requires: memory system (retrieved context)
```

---

## Quick Wins (Low Effort, High Impact)

### Completed ✓
| Gap | Effort | Impact |
|-----|--------|--------|
| ✓ Extract skill levels in state Lua | Small | High — enables training decisions |
| ✓ Log decisions to JSONL file | Small | High — enables debugging |
| ✓ Extract world/site name in state | Small | Medium — grounds agent spatially |
| ✓ Validate move actions pre-execution | Small | Medium — reduces wasted turns |

### Remaining
| Gap | Effort | Impact |
|-----|--------|--------|
| Add `rest` / sleep action | Small | Medium — enables recovery (key unknown, needs testing) |
| ✓ Add item pickup/drop/wield actions | Small | High — enables inventory management |
| ✓ Extract NPC relationships/reputation | Small | High — informs social decisions |
