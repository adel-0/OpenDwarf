# OpenDwarf - AI Agent for Dwarf Fortress

OpenDwarf is an application that autonomously plays Dwarf Fortress, using the power of LLMs.

## Architecture

OpenDwarf connects to Dwarf Fortress via DFHack's RPC interface, extracts game state, sends it to an LLM, and executes the LLM's decisions as in-game actions — creating an autonomous AI adventurer.

```
OpenDwarf (Python) <--TCP/RPC--> DFHack <--memory--> Dwarf Fortress
      |
      +--> LLM picks INTENTS; deterministic code executes them
```

Current capability status and the phased plan live in **ROADMAP.md** — this file
describes architecture and stable empirical facts, not progress.

### Intent/Skill Architecture (current)
The LLM chooses *intents*; deterministic code carries them out. This replaced the
earlier "every keystroke goes through the LLM" design, which forced ~600 lines of
compensating heuristics (ban lists, oscillation detectors, stuck counters) on top
of a blind 5×5 perception window. The LLM still reasons about *what* to do every
turn — it no longer micro-steps movement.

- **Perception** — `opendwarf/spatial/`: a persistent `ChunkMap` (16×16 chunks in
  absolute coords) fed by `opendwarf--map.lua` (wide ~40-tile extraction across
  z±2). The LLM sees a 21×21 rendered view with unit overlays; A* (`pathfinder.py`)
  consumes the full map. `extractor.py` decides when to re-fetch and converts
  local→absolute coords.
- **Actions** — `opendwarf/actions/`: a registry of `ActionSpec`s with three kinds —
  **key** (single deferred input), **skill** (multi-tick deterministic controller:
  `RouteExecutor`, `FastTravelController`, `QuestLogSkill`, `MenuSkill`), and
  **context** (conversation choice). The per-turn action list and dispatch are both
  driven by the registry, so new DF capabilities = one new `ActionSpec`/`Skill`.
- **Layer 1: Tactical loop** (`opendwarf/agent/loop.py`) — slim orchestrator:
  extract → auto-handlers → step active skill (no LLM while a skill runs) →
  else ask the LLM for an intent → dispatch. Continuity via a persisted
  `Scratchpad` (LLM rewrites it each turn) + outcome-annotated action history.
- **Layer 3: Goal Management** — Long-term goals, revised on meaningful events.
- **Layer 2: Strategic Planning** — merged into the goal manager (`revise_and_plan`).
- **LLM layer** (`opendwarf/llm/`) — provider-agnostic. `OPENDWARF_LLM_PROVIDER`
  selects `azure`, `anthropic`, or `openrouter`. `PromptBundle` keeps a stable
  cacheable prefix (base prompt + mechanics + postmortems) ahead of dynamic blocks
  so prefix caching works. Per-caller model overrides via
  `OPENDWARF_ANTHROPIC_MODEL_<CALLER>` / `OPENDWARF_OPENROUTER_MODEL_<CALLER>`.

Movement intents: `goto_site:<id>` (fast travel), `goto_unit:<id>`, `goto_stairs:<up|down>`,
`explore:<dir>` (frontier), `move_<dir>` (single step). The old wall-following
`go_*` navigator was deleted — pathfinding handles walls/doors/routing.

### Goal System (as implemented — see `opendwarf/goals/`)

Flat list of ≤3 ACTIVE `Goal` dataclasses (`model.py`) with lifecycle `ACTIVE → ACHIEVED/DROPPED/FAILED`, persisted to `goals/active_goals.json`. The top goal carries 3–6 `PlanStep`s with a machine-checkable `CompletionType`: `GOTO` (goto_* skill arrived), `REACH_SITE` (site_name changes), `TALK` (dialogue ended), `APPROACH_NPC` (unit at distance ≤ 1), `COMBAT` (combat resolved), `GET_ITEM` (inventory count up), `TRAVEL` (legacy: position delta ≥ min_tiles), `GENERIC` (15-turn timeout fallback). There is no goal-type taxonomy and no goal tree — a two-level tree was considered and deliberately not built (flat list works; don't add structure until a failure demands it).

**Survival gates** (`survival.py`): pure `evaluate(state) → SurvivalGates` checked in Python each turn — flags for danger (low health / hostile near), critical hunger/thirst/drowsiness, exhaustion, and a `flee_trigger` (exhaustion_critical + hostile). `.hint()` renders urgency text injected into the tactical prompt. Gates shape the LLM's priorities via the hint; they do not hard-filter goals.

**Revision triggers** (not per-turn — only on meaningful events): combat resolved, sub-goal achieved/failed, dialogue ended, forced dialogue started, health threshold crossed, new location discovered, session start, `wait_long`.

**Key traps**: Fast travel is a mode switch not a move sequence. Names collide — always resolve to `hist_fig_id`. Location success conditions must check z-level. Physiological urgency is danger-contextual (ignore thirst while a wolf is on you). Forced dialogue must not abort the current goal. Item goals need an acquisition method (`LOOT`/`BUY`/`TAKE`). Rumor targets may be stale — bound the search with an exploration budget.

### Memory System (as implemented — see `opendwarf/memory/`)

Episodic / semantic / procedural notes as markdown + YAML frontmatter in `memory/`; spatial memory is a separate system (`opendwarf/spatial/`, design in ROADMAP.md).

- **Retrieval** (`retriever.py`): `score = recency × importance_norm × relevance`; recency `0.99^(ticks/100)` with macro-time decay clamping; top-5 per turn, tag-filtered by context. Tactical notes (importance < 5) expire after 5000 ticks without access; semantic notes update-in-place by entity ID.
- **Writing** (`writer.py`): significance-filtered — goal-revision events or LLM-assigned importance ≥ 7. Calibration anchors: 9 = creature weakness discovery, 5 = found a sword, 2 = killed a rat.
- **Reflection** (`reflection.py`): synthesizes higher-order insight notes when accumulated episodic importance crosses threshold or at session end.
- **Post-mortems** (`postmortems.py` → `memory/postmortems.md`): 2-sentence lessons injected verbatim at session start. NOTE: generation on death is **not yet wired** — death isn't detected (ROADMAP Phase 7.1).
- **Entity IDs, not names**: always tag with `hist_fig_id`/`site_id`. Non-historic units (`hist_figure_id = -1`) use type-based tags (`unit_type:GOBLIN`). Low-confidence inferred notes are excluded from auto-injection.

For exact constants, trust the code over this summary.

## DFHack Connection Layer

### RPC Protocol
- **Message header**: `int16_t id, int16_t padding, int32_t size` (8 bytes total, NOT 12). Format string: `<hhI`.
- Reply IDs: RESULT=-1, FAIL=-2, TEXT=-3, QUIT=-4
- REPLY_TEXT payload is `CoreTextNotification` protobuf (nested fragments), not raw text
- **REPLY_TEXT fragment `.text` is bytes** in Python protobuf — decode with `.decode("utf-8", errors="replace")`
- RunCommand bind: plugin="" (empty, NOT "core"), input="dfproto.CoreRunCommandRequest", output="dfproto.EmptyMessage"
- RunLua bind: plugin="" (empty), input="dfproto.CoreRunLuaRequest", output="dfproto.StringListMessage"
- `RunLua` only works with modules named `rpc.*` — limited usefulness
- **Bug**: script errors sometimes produce REPLY_TEXT but no final REPLY_RESULT/REPLY_FAIL, causing hangs. Implement a timeout with reconnection.

### How Lua Execution Works
DFHack's `lua --unsafe` builtin does **not** route `print()` output through the RPC text channel. OpenDwarf deploys Lua scripts to DFHack's `hack/scripts/` directory (prefixed `OpenDwarf--`) and runs them as DFHack commands, which properly captures output via RPC REPLY_TEXT messages.

### Install Layout (Steam DFHack on Linux)
Steam DFHack installs as a **separate Steam app** — scripts live in
`…/steamapps/common/DFHack/hack/scripts`, NOT inside the Dwarf Fortress directory
(DF only carries `dfhooks_dfhack.ini` pointing at the DFHack `.so`). `LuaExecutor`
auto-resolves the scripts dir at runtime via `dfhack.getHackPath()`, so no manual
`--scripts-dir` is needed. The v53 API notes below are confirmed on v0.53.14 STEAM.

### v53 API Compatibility (DF v0.53.10–v0.53.14)
These functions do **not** exist — use the alternatives:
- `dfhack.to_json()` → `require("json").encode()`
- `dfhack.TranslateName()` → `dfhack.units.getReadableName()`
- `dfhack.units.getHealth()` → direct fields (`unit.body.blood_count`)
- `dfhack.units.isOpponent()` → `dfhack.units.isDanger()`
- `df.unit_inventory_item.T_mode` → does not exist; `inv_item.mode` is a raw integer (0=Hauled, 1=Weapon, 2=Worn, 4=Flask, etc.)
- `dfhack.gui.getFocusString()` → `dfhack.gui.getCurFocus()` (returns list of strings)
- `gui` module must be explicitly required: `local gui = require("gui")`
- `df.global.adventure.interactions.party_activities` may not exist — wrap in pcall

### Confirmed Working Lua Functions
- `dfhack.world.getAdventurer()` — current adventurer unit
- `dfhack.world.isAdventureMode()` — mode check
- `dfhack.units.getPosition(unit)` — returns **three separate values** `x, y, z` (NOT a table). **These are LOCAL map-relative coordinates**, not absolute world coordinates. Convert to absolute: `abs_x = df.global.world.map.region_x * 16 + x`, same for y. `z` is already the absolute z-level.
- `df.tiletype.attrs[tt].shape` — returns the `tiletype_shape` enum for a tile type integer. Key values: `6`=STAIR_UP, `7`=STAIR_DOWN, `8`=STAIR_UPDOWN, `9`=RAMP, `10`=RAMP_TOP. Use shape (not raw tile ID) to detect vertical traversal points.
- `df.global.adventure.total_move` — cumulative count of successful moves. Increments only when a move succeeds. Compare before/after an action to detect whether movement was blocked (no dedicated bump/fail flag exists).
- `df.global.adventure.travel_origin_x/y/z` — local-coordinate departure point when fast travel is active. Value `(-1, -1, 0)` = not in fast travel.
- `df.global` — all global game state
- `option.doRealize()` — works for conversation type selection (phase 1)
- `choice.title.text[i].value` — readable dialogue choice titles
- `dfhack.screen.readTile(x, y, false)` — reads screen buffer for any screen
- `dfhack.gui.matchFocusString(str)` — focus state matching
- `dfhack.gui.getViewscreenByType(type, depth)` — get specific viewscreen
- `dfhack.units.getUnitsInBox(x1,y1,z1,x2,y2,z2)` — spatial unit query
- `dfhack.units.isDanger(unit)` — hostility check
- `dfhack.maps.getTileType(x,y,z)` — tile type at position
- `dfhack.maps.isTileVisible(x,y,z)` — visibility check

## Adventure Mode Game State

### Turn Structure

The game loop is governed by `df.global.adventure.player_control_state` (enum `df.adventure_game_loop_type`):
- **`TAKING_INPUT`** — game is waiting for player action. **Only issue commands in this state.**
- Other states: animation playback, combat resolution, projectile travel, etc.

**Time**: Actions consume "instants" (not traditional roguelike turns). `.` = wait 10 instants, `,` = wait 1 instant. Movement/combat costs vary (not publicly documented — needs empirical testing via `tick_counter`).

**Key fields**:
- `df.global.adventure.tick_counter` — increments each game tick
- `df.global.adventure.game_loop_animation_timer_start` — can be advanced to skip combat animations
- `df.global.adventure.projsubloop_visible_projectile` — set to `false` to skip projectile animations

### `df.global.adventure` (core state)

| Field | Purpose |
|-------|---------|
| `player_control_state` | Current game loop phase (enum `adventure_game_loop_type`) |
| `player_id` | Nemesis record ID of the adventurer |
| `player_army_id` | Army ID for fast travel |
| `menu` | Current UI menu state (enum `ui_advmode_menu`) |
| `tick_counter` | Game tick counter |
| `message` | Current status message string |
| `interactions.party_core_members` | Vector of historical figure IDs in core party |
| `interactions.party_pets` | Vector of historical figure IDs of party pets |
| `travel_origin_x/y/z` | Fast travel origin coordinates |
| `travel_not_moved` | Whether player has moved since entering travel mode |
| `conversation` | Active conversation state |
| `rumor` | Rumor/knowledge data for conversation system |

### `df.global.game.main_interface.adventure` (UI-layer state)

| Field | Purpose |
|-------|---------|
| `look.open` | Whether look mode is active |
| `conversation.conv_choice_info` | Vector of `adventure_conversation_choice_infost` — dialogue options |
| `conversation.conv_act.events[0].menu` | Conversation state (enum `conversation_state_type`) |

### `ui_advmode_menu` enum values (confirmed)
`Default`, `Travel`, `Look`, `Inventory`. Others likely exist (Combat, Conversation) but unconfirmed.

### Reading Game State from Lua

**Getting the adventurer:**
```lua
local adv = dfhack.world.getAdventurer()
local ax, ay, az = dfhack.units.getPosition(adv)  -- returns 3 values, NOT a table
local nemesis = df.nemesis_record.find(df.global.adventure.player_id)
```

**Nearby units:**
```lua
local units = dfhack.units.getUnitsInBox(x1,y1,z1, x2,y2,z2)
-- Or iterate all:
for _, unit in ipairs(df.global.world.units.all) do
    if dfhack.units.isActive(unit) then ... end
end
```

**Detecting combat** — no single "combat state" object. Detect by:
- Hostile units nearby via `dfhack.units.isDanger(unit)`
- `df.global.world.status.temp_flag.adv_showing_announcements`
- `df.global.world.status.adv_announcement` vector for combat log text

**Inventory access:**
```lua
local adv = dfhack.world.getAdventurer()
for _, inv_item in ipairs(adv.inventory) do
    local item = inv_item.item
    local mode = tostring(inv_item.mode)  -- "Hauled", "Worn", "Weapon", etc.
end
```

**Conversation choices:**
```lua
local adventure = df.global.game.main_interface.adventure
for i, choice in ipairs(adventure.conversation.conv_choice_info) do
    local text = ""
    for _, data in ipairs(choice.title.text) do
        text = text .. data.value
    end
    print(i, text)
end
```

**Map tile access:**
```lua
local ttype = dfhack.maps.getTileType(x, y, z)
local block = dfhack.maps.getTileBlock(x, y, z)
local visible = dfhack.maps.isTileVisible(x, y, z)
```

**Safe nested field access — always use pcall:**
```lua
local ok, val = pcall(function()
    return df.global.adventure.interactions.party_core_members
end)
```

**JSON encoding (for sending data back to Python):**
```lua
local json = require("json")
print(json.encode(data_table))
```

## Action Execution & UI

### Input Simulation
Primary method — `gui.simulateInput()` sends key events to the current viewscreen:
```lua
local gui = require('gui')
local screen = dfhack.gui.getCurViewscreen()
gui.simulateInput(screen, 'A_MOVE_N')      -- move north
gui.simulateInput(screen, 'A_ATTACK')       -- attack
gui.simulateInput(screen, 'LEAVESCREEN')    -- back/escape
gui.simulateInput(screen, 'SELECT')         -- confirm/select
```

DFHack's `gui.simulateInput()` does NOT work during RPC suspension (core lock held). **Solution**: use `dfhack.timeout(1, 'frames', function() gui.simulateInput(screen, key) end)` to defer input to the next frame after the RPC call returns. This works reliably for all movement, combat, and UI actions. Conversation selection via `option:doRealize()` works directly (state mutation, not input simulation).

After sending a deferred action, the Python side must wait (~0.3-0.5s) before reading state to let the tick process.

### Movement keys (`df.interface_key` enum)
- `A_MOVE_N`, `A_MOVE_S`, `A_MOVE_E`, `A_MOVE_W`
- `A_MOVE_NE`, `A_MOVE_NW`, `A_MOVE_SE`, `A_MOVE_SW`
- `A_MOVE_SAME_SQUARE` — wait/stay in place
- `A_ATTACK`, `A_COMBAT_ATTACK` — attack actions

### Screen/Focus States

Main viewscreen: `df.viewscreen_dungeonmodest`. Focus prefix: `dungeonmode/`.

| Focus String | Context |
|-------------|---------|
| `dungeonmode/Default` | Normal gameplay, awaiting input |
| `dungeonmode/Look` | Look mode active |
| `dungeonmode/Conversation` | In dialogue with NPC |
| `dungeonmode/ViewSheets/UNIT/Skills/*` | Viewing unit skills (Labor, Combat, Social, Other) |

Other viewscreens:
- `df.viewscreen_dungeon_monsterstatusst` — unit/monster status (has `.unit` and `.inventory`)
- `df.viewscreen_adventure_logst` — quest/adventure log (has `.cursor.x/y` and `.player_region.x/y`)

```lua
-- Get the adventure mode viewscreen
local advScreen = dfhack.gui.getViewscreenByType(df.viewscreen_dungeonmodest, 0)

-- Check focus state
if dfhack.gui.matchFocusString('dungeonmode/Default') then
    -- ready for normal input
end
```

## Practical Notes & Development

- **Token budget**: DF state can be huge. Summarize before sending to LLM — send structured context, not raw data dumps.
- **Screen reading vs state reading**: Screen reading (`readTile`) works on any screen but is fragile to layout changes. State reading (`df.global.*`) is reliable but doesn't cover UI-only information. Use state reading as primary, screen reading as fallback.
- **Error recovery**: DFHack RPC can hang (see bug above). Implement timeouts on all RPC calls and reconnect on failure.

### Debugging While OpenDwarf Is Running
Use a separate Python script connecting to DFHack RPC directly:
```python
from opendwarf.dfhack.client import DFHackClient
from opendwarf.dfhack.lua_executor import LuaExecutor
from opendwarf.state.game_state import GameState

client = DFHackClient('127.0.0.1', 5000)
client.connect()
lua = LuaExecutor(client)

result = lua.extract_screen_text()     # Screen reader output
ctx = lua.extract_screen_context()     # Structured context
state_raw = lua.extract_state()        # Game state
state = GameState.from_raw(state_raw)  # Full state object
print(state.summary())                 # What the LLM sees

client.disconnect()
```
Note: use `PYTHONIOENCODING=utf-8` on Windows to avoid encoding errors from DF's Unicode.

Dwarf Fortress with DFHack will always be running when you work so you can test live.

### Contributing Guidelines
- **Document confirmed working changes** in this or another file when major features are implemented and verified.
- Test changes in DF and verify with DFHack. Commit confirmed working changes (conventional commit).
- When asked to implement something, start writing code immediately. Bias toward concrete code changes over documentation.
- For Python, prefer uv over pip.
- Do not implement fallbacks for no good reason.
- Use --verbose for debugging.

### References
- [DFHack Lua API](https://docs.dfhack.org/en/stable/docs/Lua%20API.html)
- https://dwarffortresswiki.org/index.php/Adventurer_mode_gameplay — combat, movement, companions, survival, crafting, quests
- https://dwarffortresswiki.org/index.php/Adventure_mode_quick_reference — keybindings and controls by category
