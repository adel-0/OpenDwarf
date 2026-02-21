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

## Priority 3 — Strategic / Goal Management Layer (Layer 3)

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

**Design**: Built on [MemSearch](https://github.com/zilliztech/memsearch) with A-MEM-inspired structured notes. Each memory is a markdown file with frontmatter (tags, keywords, category, context, links to related memories). Memory evolution: new experience that contradicts/refines an existing memory updates the old note rather than appending.

**Type → Storage**: Episodic/semantic/procedural → MemSearch markdown + vector index. Spatial → separate purpose-built graph/grid with pathfinding (not MemSearch — similarity search is wrong for pathfinding).

**Design principles**: No LLM calls for every store/retrieve (only for evolution and periodic summarization). Filter for significance before storing. Retrieval via MemSearch hybrid search (fast, no LLM needed).

### Episodic Memory
- No record of past events
- **Fix**: After significant events, write a structured memory note

### Semantic Memory
- Information gathered in conversation is lost after session
- **Fix**: Extract named facts from NPC dialogue; store in searchable notes

### Procedural Memory
- No learned tactics from trial and error
- **Fix**: Record what worked/didn't after combat; allow LLM to update existing notes on new evidence

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

**Fix**: Implement the three-layer spatial memory as described (purpose-built for pathfinding, not MemSearch)

### Retrieval
- No retrieval mechanism even if notes existed
- **Fix**: Integrate MemSearch hybrid search; inject top-K relevant memories into each turn prompt

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
