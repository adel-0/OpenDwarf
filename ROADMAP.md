# OpenDwarf — Roadmap

Tracks remaining gaps and unknowns. Completed items removed — design docs in CLAUDE.md.

Last evaluation: 2026-02-28 (41-turn run). **Agent is functional for basic exploration.** Successfully navigates between sites via fast travel, detects site boundaries, reads nearby site distances during travel, enters/exits fast travel mode, and breaks navigation loops. Traveled from wilderness to GARLIC GLEAM (23+ embark tiles) autonomously. Remaining gaps: conversation content not yet verified as visible to LLM, no conversation memory, spatial memory not implemented.

---

## P0 — Critical Blockers — ALL RESOLVED

### 1. Fast Travel — DONE
- Enter/exit fast travel via `travel_enter`/`travel_exit` actions in act.lua
- Help dialog auto-dismissed via mouse-click on "Okay" button (clickok.lua)
- Army position tracked during travel via `df.army.find(player_army_id).pos`
- Army coords are 3× embark tile coords — converted for site distance calculation
- Nearby sites with distances/directions shown during travel for navigation
- Stuck detection (3 consecutive nav failures) bans local movement and forces `travel`
- `go_*` → `move_*` conversion during fast travel prevents navigator activation

### 2. Conversation Content Extraction — DONE
- Announcement text buffered before auto-dismiss (NPC responses appear as announcements)
- Buffer injected into LLM turn prompt as "Recent Announcements" block
- Conversation transcript tracked separately, cleared on dialogue end

### 3. Navigation Loop Breaking — DONE
- Position history tracking (last 30 positions, 8-position window for stuck detection)
- Navigator failure counter (3 consecutive stuck/loop events → force fast travel)
- Area-stuck detection (3 turns in ≤10 tile bounding box → force fast travel)
- When stuck: all `go_*` directions banned from action block, strong hint injected

---

## P1 — Important (Agent Functions But Poorly)

### 4. Site Detection — DONE
- Fixed coordinate system: uses `global_min/max_x/y` (embark tiles) with player position in embark tiles (`region_x + floor(local_x/16)`)
- Verified working: correctly identifies MASSIVE DABBLE, GARLIC GLEAM when at site

### 5. Conversation Memory & Deduplication
The agent can now see NPC responses (via announcement buffer), but doesn't write memory notes about conversations. It may still re-ask the same NPC the same question.

**What's needed:**
- After a conversation ends, write a memory note summarizing who was talked to and what was discussed
- Before initiating `talk`, check memory for recent conversations with that NPC
- Inject conversation history into the LLM's turn context

### 6. Tick Counter Accuracy — DONE
- Switched from `adventure.tick_counter` (wraps at ~256) to `df.global.cur_year_tick` (stable)

### 7. Announcement/Combat Log Reading — DONE
- Announcement text captured and buffered for LLM context
- Combat log injected into turn prompt when present

---

## P2 — Enhancement (Improves Quality)

### 8. Spatial Memory
No persistent map — agent re-explores already-visited areas. See design below.

### 9. Quest Log Reading
- `df.viewscreen_adventure_logst` is never read; world agreements tried but no active quests to verify
- **Fix**: Open/read the adventure log viewscreen to extract quest text

### 10. Token Budget Management
- `GameState.summary()` can grow large with no intelligent filtering
- **Fix**: Situational summarization — prioritize by context (combat→threats, exploring→map, conversation→NPC)

### 11. Richer Turn Context
- Turn prompt lacks relevant memories and recent decision history
- **Fix**: Inject top retrieved memories and last 3 decisions to avoid repetition

### 12. Memory System — Remaining Tasks
- [ ] Wire `PostmortemBuffer.generate_and_append` to adventurer death detection
- [ ] Procedural note creation for combat outcomes
- [ ] MemSearch vector index integration (optional)

---

## Spatial Memory Design (Not Yet Implemented)

No persistent map — agent re-explores already-visited areas.

**Why a pure node-edge graph fails**: Knowing "Oaktown connects to Stonehall" is useless when the agent must navigate *between* known nodes — it has no tile-level knowledge of that space, can't detect obstacles, and can't recognise when it's been somewhere before.

### Three Co-Existing Layers

#### Layer 1 — Sparse Chunk Grid (tile-level, exact knowledge)

`dict` keyed on `(chunk_x, chunk_y, z)` where chunks are 16×16 world tiles. Cell values: `UNKNOWN | PASSABLE | WALL | WATER`. Only visited chunks exist. The existing 5×5 `map_tiles` extracted each turn feeds into this. On area transitions, scan wider radius with `dfhack.maps.getTileType`.

Pathfinding: A* on the chunk grid. `UNKNOWN` tiles get high traversal cost, not infinite — the agent paths through unknown space when no known route exists. Persistence: `spatial/chunks.msgpack`.

#### Layer 2 — Topological Waypoint Graph (site-to-site, coarse)

Nodes for qualitatively distinct places only. Creation triggers: area type change (wilderness → town), agent returns to a coordinate, NPC reveals a named location. Edges carry direction, distance, terrain, confirmed flag. Node count ~200–500. Serialised to `spatial/topo_graph.json`.

#### Layer 3 — Site Registry (rumoured + visited locations)

Handles knowledge with no tiles yet: quest targets, NPC hints, world-data sites. Each entry: `exact_pos` (set on visit) or `estimated_pos` (dead-reckoned from NPC hints), `confidence` (1.0=visited, 0.4=rumor, 0.2=vague), source, notes.

### Navigation Across Layers

High-level: A* on topo graph → waypoint list. Low-level: A* on chunk grid toward current waypoint, replanning each turn. When grid has no complete path, explore toward waypoint direction (frontier-following). No LLM needed.

### LLM Interface — What It Sees

Never send raw tiles or the full graph. Generate ~100–150 token structured text block:
```
-- Spatial Context --
Current area: Wilderness (8 chunks explored nearby)
Active route to Goblin Pits: waypoint 2/4 — "Crossroads at Blackwood" ~80 tiles NE
Nearby sites:
  - Ironhold (fortress) 240 tiles NE [visited — armorer, safe]
  - "Goblin stronghold" ~1 day N (unverified, heard from merchant)
```

### Implementation Traps

1. **`getPosition()` returns LOCAL coordinates** — convert to absolute: `abs_x = region_x * 16 + local_x`. Fast travel uses separate coordinate space.
2. **Z-levels not automatically connected** — detect vertical portals via `df.tiletype.attrs[tt].shape` (6=STAIR_UP, 7=STAIR_DOWN, 8=STAIR_UPDOWN, 9=RAMP, 10=RAMP_TOP). Tag as vertical edges.
3. **PASSABLE is dynamic** — rivers freeze/melt, doors lock. Store `last_verified_tick`; treat stale entries as UNKNOWN. On movement failure (pos unchanged), downgrade blocking tile.
4. **Climbing bypasses passability** — WALL tiles can be climbed. Future: add `CLIMBABLE` cell type gated on Climber skill.
5. **Fast-travel exit imprecision** — DF spawns near destination, not exact. Snap to existing topo node within 10-tile radius to avoid graph fragmentation.
6. **Natural ramps unreliable** — RAMP shape doesn't guarantee traversability (may have wall above). Record vertical edges only after observed successful z-transition. Prefer STAIR shapes over RAMP.

---

## Confirmed DF Empirical Findings

- **Army position coordinates**: `df.army.find(player_army_id).pos` uses coords that are 3× embark tile coordinates
- **Fast travel movement**: `A_MOVE_*` keys work during travel mode; position tracked via army pos, not adventurer unit
- **Fast travel help dialog**: Appears on first entry per session, requires mouse-click on "Okay" button (keyboard SELECT/LEAVESCREEN don't work)
- **Fast travel exit**: Click the `x` button on screen bottom (keyboard shortcuts don't work)
- **`getAdventurer()` returns nil during fast travel** — state extraction must handle this gracefully
- **`adventure.tick_counter`** wraps at ~256; use `cur_year_tick` instead
- **NPC response text** appears as announcements, not in conversation data structures
