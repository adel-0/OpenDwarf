# OpenDwarf — Roadmap

Tracks remaining gaps and unknowns. Completed items removed — design docs in CLAUDE.md.

Last evaluation: 2026-03-25 (52-turn run). Agent travels from town to camp via fast travel (14 tiles NE), auto-stops at destination, initiates conversations. Improved: origin-site tracking prevents ping-pong, direction hints steer toward destinations, unreachable NPCs get banned after 3 failed approaches.

---

## What Works

- Fast travel enter/exit, army position tracking, site distance display
- Conversation system: NPC selection, bypass greeting, topic navigation, transcript memory
- Busy NPC detection (NPC-to-NPC conversations) with wait/relocate guidance
- Goal lifecycle: CANDIDATE → ACTIVE → ACHIEVED/DROPPED/FAILED, with plan steps
- Plan step completion types: TRAVEL, TALK, APPROACH_NPC, REACH_SITE, COMBAT, GET_ITEM, GENERIC
- Stuck detection: nav failure counter, area-stuck detection, forced fast travel escalation, site-aware (no travel when at a named site)
- Fast travel: auto-stop at destination, origin site tracking, direction hints, monotonic travel warning
- Unreachable NPC banning: after 3 failed approach_unit attempts, unit is excluded from actions
- Low health survival hints (< 30% HP triggers rest/flee guidance)
- Memory: episodic/semantic/procedural write, retrieval (top-5), reflection, postmortem buffer
- Observability: decisions.jsonl, llm_calls.jsonl, goal_events.jsonl, memory_events.jsonl

---

## Remaining Work (priority order)

### 1. Multi-turn Conversations
DF ends dialogue after single direction queries. The agent needs to re-initiate conversation to ask follow-up questions. Also needs deduplication — agent may re-ask same NPC same question.

### 2. ~~Site Discovery During Fast Travel~~ ✅ DONE
Auto-stop implemented when nearby site distance ≤ 1, excluding origin site.

### 3. Navigator Wall-Following Loops (partially improved)
Local autopilot gets stuck in wall-following loops around buildings. Improved: max_steps reduced 30→15, bbox loop detection added, but fundamental issue remains — need spatial memory or pathfinding for reliable town navigation.

### 4. APPROACH_NPC Completion Check
Currently completes when ANY non-hostile NPC is adjacent — should check for the specific target NPC (by hist_fig_id or name).

### 5. Spatial Memory
No persistent map — agent re-explores already-visited areas. Three-layer design documented below (chunk grid + topo graph + site registry). Would eliminate navigator loops and enable informed pathfinding.

### 6. Quest Log Reading
`df.viewscreen_adventure_logst` is never read. Opening/reading the adventure log would provide quest objectives.

### 7. Token Budget Management
`GameState.summary()` can grow large. Needs situational summarization — prioritize by context (combat → threats, exploring → map, conversation → NPC).

### 8. Memory System Polish
- Wire `PostmortemBuffer.generate_and_append` to death detection
- Procedural notes for combat outcomes
- MemSearch vector index (optional)

---

## Spatial Memory Design (Not Yet Implemented)

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
