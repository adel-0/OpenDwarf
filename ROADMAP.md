# OpenDwarf — Roadmap

Tracks remaining gaps and unknowns. Completed items removed — design docs in CLAUDE.md.

Last major change: **2026-06-10 capability overhaul** — replaced the blind 5×5
perception + per-keystroke-LLM design with an intent/skill architecture: persistent
chunk map + A* pathfinding (wide perception), deterministic movement/travel/menu
skills, provider-agnostic LLM layer with prompt caching, persistent scratchpad +
outcome-annotated history, and quest-log reading. The old wall-following navigator
and ~600 lines of compensating heuristics were deleted.

✅ **Live-verified 2026-06-10 against DF v0.53.14 STEAM (Linux) + DFHack.** Map
extraction, A* routing, deterministic movement, key-action dispatch, and quest-log
read all confirmed working in-game. Only full fast-travel remains to exercise
end-to-end (its primitives are confirmed). See the checklist below for details.

---

## What Works (Python-side, unit-tested)

- `ChunkMap` ingest/merge/downgrade/persistence; A* with walls/doors/stairs/unknown
  cost, ramp-confirmation gating, frontier search, partial paths (`tests/test_spatial.py`)
- Action registry: availability matrix + dispatch for movement/travel/combat/
  conversation/item/quest intents
- Provider-agnostic LLM layer (Azure + Anthropic), cache-friendly `PromptBundle`
- Scratchpad persistence; outcome-annotated history; goal manager `GOTO` completion
- Conversation transcript capture + memory flush; trigger detection; survival hints
  (now wound-aware)

## What Carried Over

- Conversation system (NPC select, bypass greeting, transcript memory)
- Goal lifecycle + plan steps; memory (episodic/semantic/procedural, retrieval,
  reflection, postmortems); observability JSONL logs

---

## LIVE-VERIFICATION CHECKLIST (results — DF v0.53.14 STEAM Linux, 2026-06-10)

1. ✅ **`opendwarf--map.lua`** — radius 40 × 5 z-levels extracts in ~0.23s; door/
   wall/stair/water chars and absolute-coord origin (`region_x*16 + local`) all
   correct; adventurer centered in the 81×81 grid.
2. ✅ **`adventure.total_move`** — increments only on successful moves, but by a
   **variable amount** (observed +9 for one step, not +1). `RouteExecutor` and the
   snapshot diff use `!=` (any change = moved), so this is handled correctly.
3. ✅ **`RouteExecutor`** — pathed 8 tiles to an exact goal in-game, one verified
   step per tick, arrived cleanly; A* routes around walls/doors (confirmed live).
   Vertical keys `A_MOVE_UP`/`A_MOVE_DOWN` confirmed valid `interface_key` names
   (stair/ramp traversal path not yet exercised — no stairs near spawn).
4. ⏳ **`FastTravelController`** — primitives confirmed (`A_TRAVEL` key valid; army-
   pos math previously empirical), but the full enter→steer→auto-stop→exit journey
   is not yet run end-to-end (would teleport the test adventurer across the world).
5. ✅ **`QuestLogSkill`** — `A_LOG` confirmed valid; opens `adventure_log`
   viewscreen, reads quests (0 for a fresh adventurer), escapes back to Default.
6. ✅ **Coordinate offset** — `MapExtractor` local→absolute offset correct; ChunkMap
   ingest + render + pathfind agree with in-game positions. (Cross-area / post-
   fast-travel offset stability still untested — folded into item 4.)

**Setup note:** Steam DFHack on Linux installs to a *separate* Steam app
(`…/steamapps/common/DFHack/hack/scripts`), not inside the DF directory.
`LuaExecutor` now auto-resolves this via `dfhack.getHackPath()` — no `--scripts-dir`
needed.

---

## Remaining Work (priority order)

### 1. Spatial memory layers 2 & 3 (topo graph + site registry)
The chunk grid (Layer 1) is in. Add the topological waypoint graph and rumoured-site
registry (design below) for cross-region routing and dead-reckoned quest targets.

### 2. Multi-turn Conversations
DF ends dialogue after single queries; agent must re-initiate for follow-ups, with
dedup so it doesn't re-ask the same NPC the same question.

### 3. Combat tactics
Movement/attack are single-key intents; no positioning logic, no flee pathing beyond
the survival hint. Consider a combat skill (kite/approach/target-selection).

### 4. Token budget management
`GameState.summary()` + 21×21 map can grow. Situational summarization (combat→threats,
exploring→map, conversation→NPC).

### 5. Memory polish
Wire `PostmortemBuffer.generate_and_append` to death detection; procedural combat
notes; optional MemSearch vector index.

---

## Spatial Memory Design (Layer 1 DONE; Layers 2–3 pending)

### Layer 1 — Sparse Chunk Grid ✅ IMPLEMENTED
`opendwarf/spatial/chunk_map.py`. 16×16 chunks keyed `(cx,cy,z)`, absolute coords,
per-tile `last_verified_tick`, persisted to `spatial/chunks.json`. A* in
`pathfinder.py`: UNKNOWN traversable at 5× cost, stale tiles treated as UNKNOWN,
ramps need a confirmed z-transition, partial paths toward the goal on failure.

### Layer 2 — Topological Waypoint Graph (pending)
Nodes for qualitatively distinct places. Triggers: area-type change, return to a
coordinate, NPC reveals a named location. Edges carry direction/distance/terrain/
confirmed. ~200–500 nodes → `spatial/topo_graph.json`.

### Layer 3 — Site Registry (pending)
Knowledge with no tiles yet: quest targets, NPC hints, world-data sites. Each entry:
`exact_pos` (visited) or `estimated_pos` (dead-reckoned), `confidence`, source, notes.

### LLM Interface
~100–150 token structured block: current area, active route + next waypoint, nearby
sites with confidence. Never raw tiles or the full graph.

### Implementation Traps (still apply)
1. `getPosition()` is LOCAL — convert with `region_x*16 + local`. Fast travel uses a
   separate coordinate space.
2. Z-levels not auto-connected — detect portals via `tiletype_shape` (stairs/ramps);
   tag vertical edges; ramps unreliable until an observed transition.
3. PASSABLE is dynamic (rivers freeze, doors lock) — `last_verified_tick`; downgrade
   on movement failure.
4. Climbing bypasses passability — future `CLIMBABLE` cell gated on Climber skill.
5. Fast-travel exit imprecision — snap to existing topo node within ~10 tiles.

---

## Confirmed DF Empirical Findings

- Army position coords are 3× embark-tile coords; fast travel tracks via army pos.
- Fast travel help dialog + 'x' exit require mouse clicks (keyboard doesn't work).
- `getAdventurer()` returns nil during fast travel — handle gracefully.
- Use `cur_year_tick` (not `adventure.tick_counter`, which wraps at ~256).
- NPC response text appears as announcements, not in conversation data structures.
