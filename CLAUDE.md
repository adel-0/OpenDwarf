# OpenDwarf - AI Agent for Dwarf Fortress

OpenDwarf is an application that autonomously plays Dwarf Fortress, using the power of LLMs.

## Architecture

OpenDwarf connects to Dwarf Fortress via DFHack's RPC interface, extracts game state, sends it to an LLM, and executes the LLM's decisions as in-game actions — creating an autonomous AI adventurer.

```
OpenDwarf (Python) <--TCP/RPC--> DFHack <--memory--> Dwarf Fortress
      |
      +--> LLM for tactical decisions
```

### Layered Decision Architecture
Every decision goes through the LLM. DF is too complex for hardcoded rules — a "flee when low HP" heuristic fails when fleeing through a goblin horde is certain death. The LLM reasons about every situation in context.

- **Layer 3: Goal Management** — Long-term goals, revised after major events. Generates objectives.
- **Layer 2: Strategic Planning** — Decomposes objectives into plans. Runs on goal transitions.
- **Layer 1: Tactical Decisions** — Every-turn reasoning: movement, combat, dialogue, inventory. Always LLM-driven.
- **Perception & Action** — DFHack Lua scripts for state extraction and action execution.

### Memory System (Planned)
See ROADMAP.md for full design. Summary:

**What goes where:**

| Type | Example | Storage |
|------|---------|---------|
| Episodic | "Fought a troll at the bridge, lost my left arm" | MemSearch markdown + vector index |
| Semantic | "Oaktown has an armorer" | MemSearch markdown + vector index |
| Procedural | "Targeting the neck works on unarmored enemies" | MemSearch markdown + vector index (evolves on new evidence) |
| Spatial | Towns, lairs, routes, dangerous areas | Separate purpose-built graph/grid with pathfinding — not in MemSearch |

**Design principles:**
- LLM calls only for memory evolution and periodic summarization, not for every store/retrieve
- Not every observation becomes a memory — filter for significance before storing
- Retrieval via MemSearch's hybrid search (fast, no LLM needed)
- Spatial memory is separate — similarity search is wrong for pathfinding

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

### v53 API Compatibility (DF v0.53.10)
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
- `dfhack.units.getPosition(unit)` — returns **three separate values** `x, y, z` (NOT a table)
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
- Until a real LLM API is implemented, simulate the calls yourself.
- Do not implement fallbacks for no good reason.

### References
- [DFHack Lua API](https://docs.dfhack.org/en/stable/docs/Lua%20API.html)
- https://dwarffortresswiki.org/index.php/Adventurer_mode_gameplay — combat, movement, companions, survival, crafting, quests
- https://dwarffortresswiki.org/index.php/Adventure_mode_quick_reference — keybindings and controls by category
