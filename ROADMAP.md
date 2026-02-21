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

### Multi-Step Planning
- The `--goal` CLI flag is a single string with no decomposition
- **Fix**: Add a `StrategicPlanner` that decomposes goals into ordered subgoals, injected into the tactical turn prompt

### Goal Completion Detection
- No detection of whether a goal/subgoal has been achieved
- **Fix**: Check state against active subgoal criteria after each action; trigger replanning when complete or blocked

### Session Persistence
- Goal and plan exist only in memory for one run
- **Fix**: Serialize goal + subgoal stack to disk; reload on startup

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
- **Fix**: Maintain a separate graph/grid of known regions/sites/routes (purpose-built for pathfinding, not MemSearch)

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
