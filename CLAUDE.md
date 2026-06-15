# OpenDwarf - AI Agent for Dwarf Fortress

OpenDwarf is an application that autonomously plays Dwarf Fortress, using the power of LLMs. DF + DFHack is almost always running for live testing.

## Architecture

OpenDwarf connects to Dwarf Fortress via DFHack's RPC interface, extracts game state, sends it to an LLM, and executes the LLM's decisions as in-game actions — creating an autonomous AI adventurer.

```
OpenDwarf (Python) <--TCP/RPC--> DFHack <--memory--> Dwarf Fortress
      |
      +--> LLM picks INTENTS; deterministic code executes them
```

Current capability status lives in **ROADMAP.md**; the target architecture
(Behavior/Policy autopilot layer, Director/Tactician model tiering) and the
implementation spec for it live in **NORTHSTAR.md**; **RESEARCH.md** grounds that
plan in the literature (BALROG, Voyager, Cradle) and holds the risk register —
this file describes architecture-as-built and stable empirical facts, not
progress or plans.

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

Episodic / semantic / procedural notes as markdown + YAML frontmatter in `memory/`; spatial memory is a separate system (`opendwarf/spatial/`, design in ROADMAP.md). Curated DF knowledge: `memory/df_mechanics.md` (always-on prompt prefix). A situational, tag-matched knowledge-injection layer (`KnowledgePack` + `memory/knowledge/` topic files) was tried and removed — hand-curating an open-ended DF encyclopedia and push-injecting it by tag-match duplicated the model's own DF prior, risked stale overrides, and was never actually wired into `main.py`. Future DF knowledge should be *pulled* on a demonstrated gap (escape-hatch episode), not pushed speculatively; the few non-obvious OpenDwarf-discovered facts belong in `df_mechanics.md`.

- **Retrieval** (`retriever.py`): `score = recency × importance_norm × relevance`; recency `0.99^(ticks/100)` with macro-time decay clamping; top-5 per turn, tag-filtered by context. Tactical notes (importance < 5) expire after 5000 ticks without access; semantic notes update-in-place by entity ID.
- **Writing** (`writer.py`): significance-filtered — goal-revision events or LLM-assigned importance ≥ 7. Calibration anchors: 9 = creature weakness discovery, 5 = found a sword, 2 = killed a rat.
- **Reflection** (`reflection.py`): synthesizes higher-order insight notes when accumulated episodic importance crosses threshold or at session end.
- **Post-mortems** (`postmortems.py` → `memory/postmortems.md`): 2-sentence lessons injected verbatim at session start. Generation on death is wired (`agent/death_handler.py`, M2 tail) — death is detected via three signals (unit flags / nil adventurer / death focus pattern); the exact DF v53 death focus string is the one LIVE-VERIFY gap.
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
- `df.global.adventure.total_move` — cumulative count of successful moves. Increments by a **variable** amount per successful move (observed +9 for one step), only when a move succeeds. Compare before/after with `!=` (never assume +1) to detect whether movement was blocked — no dedicated bump/fail flag exists.
- `df.global.adventure.travel_origin_x/y/z` — local-coordinate departure point when fast travel is active. Value `(-1, -1, 0)` = not in fast travel.
- **The travel army is created only AFTER the first travel-map move** (LIVE-VERIFIED v0.53.14): immediately after entering travel `player_army_id=-1` / `adventure.travel.not_moved=1` and no `df.army` exists; issuing one `A_MOVE_*` forms the army (`player_army_id` set, `army.pos` valid, `not_moved=0`) and advances it ~1 embark-tile/move. So a None army right after `travel_enter` is NORMAL — you must MOVE to form it (do NOT treat it as an obstruction wedge). Whether the formation move is accepted is position-dependent (some tiles/z-levels/map-edges reject it). The genuine obstruction wedge is different: `menu=Travel` with `player_army_id=-1` AND repeated moves never form an army (recover with `A_END_TRAVEL`). Straight-line `A_MOVE_*` steering moves the army one tile/press but cannot route around terrain barriers (mountains/oceans/site edges pin `army_pos`) — world-level routing belongs to a future `JourneyBehavior`.
- `df.global.world.world_data.sites` — every site in the world (NOT distance-capped), each with `.id`, `.name` (word-index translated), `.type` (→ `df.world_site_type[...]`), and `.global_min/max_x/y` embark-tile bounds (centre = midpoint). Player embark-tile pos = `region_x + local_x//16` (or `army.pos.x//3` during travel). A rumored site name can be resolved to a concrete id + world centre by substring-scanning this list (LIVE-VERIFIED v0.53.14 — `opendwarf--resolve-site.lua` returns distant matches the 200-tile nearby-site scan omits). Site bearing computed from `(centre - player)` embark-tile deltas matches DF's own reported direction/distance.
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
- `df.global.adventure.tick_counter` — increments each game tick, but **wraps at ~256** — for elapsed-time math use `df.global.cur_year_tick` instead.
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

**NPC dialogue responses** appear as game *announcements* (`df.global.world.status.adv_announcement`), NOT inside the `conversation` structs — read them there, not from `conv_act`.

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

### Adventure verb keys (empirically confirmed v0.53.14)
Probed live via `find_keys` + before/after state diffs. **Implemented**:
- `A_SNEAK` — toggles stealth. Flips `adventurer.flags1.hidden_in_ambush`
  (focus stays `dungeonmode/Default`); a clean single-key toggle. Surfaced as
  the `sneak` action + `state.sneaking` + a "SNEAKING (hidden)" status line.

**Confirmed flows, not yet implemented** (each opens a multi-step UI whose
trailing target/aim step needs a live target to verify — left for a future
ActionSpec/Skill; do NOT ship blind):
- `A_LOOK` → `dungeonmode/Look`, `main_interface.adventure.look.open=true`,
  cursor = `look.look_cursor` (a coord); move with `CURSOR_*`, `LEAVESCREEN`
  closes. Description renders in the right panel (not in `extract_screen_text`
  lines — needs a targeted `readTile` scan to capture).
- `A_THROW` and `A_INTERACT` → open a Help overlay (auto-dismissable via the
  clickok auto-handler) **plus** `main_interface.adventure.inventory` (focus
  `dungeonmode/Inventory`); item list is `inventory.option` /
  `option_current` / `scroll_position` (navigable like conversation choices),
  followed by a target/aim cursor. Throwing can target any tile, not only
  hostiles. **Caution**: iterating `inventory.option` in a probe script that
  errors will trigger the RPC script-error-hang bug — guard every field.
- Other present-but-unimplemented adventure keys: `A_JUMP`, `A_SHOOT`,
  `A_COMPANIONS` (party roster), `A_HOLD`/`A_WRESTLE` (grapple), `A_YIELD`
  (wired as `yield`), attack variants `QUICK_ATTACK`/`HEAVY_ATTACK`/
  `WILD_ATTACK`/`PRECISE_ATTACK`/`CHARGE_ATTACK`/`MULTI_ATTACK`.

### Combat targeting & the attack menu (LIVE-VERIFIED v0.53.14)
- **Wild creatures are NOT flagged `isDanger` — even when adjacent.** `dfhack.units.isDanger(unit)` returns false for un-provoked wildlife (wolves, deer, turkeys all read false at distance 1). So `state.hostile_units` (which keys on `isDanger`) is *empty* in the presence of huntable wildlife, and any combat path gated solely on it (the old `attack` action, `GrindCombatBehavior`) was unreachable. This is the root cause that kept combat unexercised in town/wilderness runs. **Fix**: `GameState.huntable_units` = `hostile_units` (active dangers) ∪ wild creatures (`hist_fig_id < 0`, not tame, not citizen). The `attack` action + grind target selection key on this; danger/flee/`in_combat`/interrupt semantics still key on `hostile_units` alone (passive wildlife must never trigger a flee). Per-unit `is_tame`/`is_citizen` come from `dfhack.units.isTame/isCitizen`.
- **Bump-to-attack auto-strikes ONLY genuine hostiles.** Moving (`A_MOVE_<dir>`) into a *hostile's* tile delivers the default strike. Moving into a **neutral/wild** creature's tile instead **opens the `dungeonmode/Attack` menu and deals no damage** (verified: 8 consecutive bumps into an adjacent wild wolf left wounds/blood unchanged, `total_move` frozen, `combat_log` empty, focus flipped to `dungeonmode/Attack`). So neutral wildlife cannot be killed by bumping — it needs the attack menu (the deferred `CombatStrike` skill, ROADMAP 2.1). `GrindCombatBehavior` auto-bumps only `is_hostile` targets; for a neutral target it hands back to the LLM rather than spamming no-op bumps.
- **The `A_ATTACK` menu** (`df.global.game.main_interface.adventure.attack`, focus `dungeonmode/Attack`) is a MOUSE-DRIVEN multi-step menu — keyboard `SELECT`/scroll/`A_MOVE_*` do NOT advance it. It is driven deterministically by `CombatStrikeSkill` (`actions/skills.py`), wired in as `attack:<id>` for neutral wildlife and as the `GrindCombatBehavior` neutral-target path. **Verified protocol (LIVE-VERIFIED v0.53.14)** — `adventure.attack.mode` (surfaced as `state.attack_menu_mode`) progresses through these stages, one mouse-click per transition, each click landing on a conversation-style `letter+NUL+text` choice row:
  - `A_ATTACK` → `mode 0` ("Who will you attack?") + a **first-use `dungeonmode/Help` overlay** stacked on top (dismiss via `opendwarf--clickok` — the same Okay-button screen-scan click; the skill does this itself since the loop's Help auto-handler is bypassed while a Behavior runs). `attack.unit_choice` (vector of `df.unit`, surfaced as `state.attack_unit_choice`) lists attackable targets in the SAME order as the on-screen rows; map a target id to its row index via that vector.
  - click target row (`attack_pick:<n>`) → `mode 2` (move list "Strike"/"Dodge"); click "Strike" (`attack_strike`) →
  - `mode 3` ("What do you want to aim for?" — body parts "upper body"/"lower body"/"neck"/"head"/…; first row = upper body, a solid "Normal strike, square"); click a body part (`attack_pick:0`) →
  - `mode 4` ("Attack upper body:" — weapon/attack-type "lash/iron whip"/"strike/copper buckler"/"punch/…", + modifiers Quick/Heavy/Wild/Precise/Multi); click the first row = primary weapon (`attack_pick:0`) → **strike resolves, menu closes to Default**, the blow is dealt (e.g. *"You lash the wolf in the upper body … tearing the middle spine's nervous tissue!"*).
  - Body-part rows start LOWERCASE after the NUL, so the conversation `read_choice_rows` scanner (uppercase-after filter, a body-text guard) drops them — `act.lua` uses a looser `read_attack_rows` (letter+NUL + ≥3-letter run) for `attack_pick`/`attack_strike`. The descriptive right panel does not render to `extract_screen_text`; the left/center choice rows DO (via `readTile`). Strike choice is deterministic (nearest target, plain Strike, upper body, primary weapon); LLM-driven strike choice (aim a wound, wrestle, charge) is a later upgrade.
- **Combat barely advances the game clock; press `A_ATTACK` only while `TAKING_INPUT`** (LIVE-VERIFIED v0.53.14). Across a 104-action autopilot grind only ~1 `cur_year_tick` elapsed — driving the (paused) attack menu and resolving strikes consumes almost no game time. So any progress/stall detection keyed on `tick_counter` (= `cur_year_tick`) will read a stationary fight as frozen; use a behavior-level event signal (a landed strike) instead (`StallWatchdog` folds `EventDigest.notable_count`). A back-to-back strike must also wait for `player_control_state == TAKING_INPUT` before pressing `A_ATTACK` — a press during the prior blow's animation is swallowed and the menu never opens.
- **Autopilot interrupt traps for combat behaviors** (LIVE-VERIFIED v0.53.14, the reason every earlier grind "live-verify" was standalone-only): under the real loop's interrupt checker a grind is suspended to the LLM by two things that are *normal* during combat — (a) each strike emits a combat-log **announcement** (`showing_announcements`); a combat `Behavior` must opt into paging its own log (`handles_announcements`) or it hands back after every blow, and (b) the `dungeonmode/Attack` menu is a non-`Default` focus that reads as an **unknown screen** unless listed in `KNOWN_FOCUS_PATTERNS`, which otherwise kills the in-flight `CombatStrikeSkill` mid-sequence.

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

**v50 modal dialogs are invisible to state reading** (confirmed live v0.53.14):
quest/divination popups with an "Okay" button (e.g. "Do you feel the pull?…")
draw OVER `dungeonmode/Default` — focus unchanged, viewscreen stack normal,
state `TAKING_INPUT`, every `main_interface` widget `open=false`, no
`dialog`/`prompt` field exists. They swallow ALL input (movement, ESCAPE,
SELECT do nothing) and also block fast travel. Only reliable handling:
screen-scan for the "Okay" text via `dfhack.screen.readTile` and mouse-click
it (`gps.mouse_x/y` + `_MOUSE_L` inside `dfhack.timeout`) — implemented in
`lua_scripts/opendwarf--clickok.lua`, run each tick by the tactical loop's
auto-handler. If the agent's moves are blocked with no obstacle in sight,
suspect one of these dialogs first.

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

### Finding DFHack/DF API knowledge (dev-time only — do this before improvising)
**Audience note**: everything in this section is for the *development-time* agent (Claude Code, with shell access). The *runtime* OpenDwarf agent has no computer access — it sees only the prompt the harness builds. Any knowledge discovered here is worthless at runtime until it is delivered in-band: compiled into an ActionSpec/Skill (preferred — the agent shouldn't need key names at all), surfaced by introspection in the escape-hatch prompt (NORTHSTAR II.7), or — for genuinely non-obvious, stable facts — added to the always-on `memory/df_mechanics.md` prefix.
- **Search the live enums first**: key names, viewscreen types, focus strings are all enumerable at runtime (e.g. pattern-search `df.interface_key` — this is how `A_END_TRAVEL` was found after the wiki and memory both had it wrong). Use the introspection scripts (`inspect_ui`, `find_keys`) once M5 lands; until then, a deployed scratch script.
- **The installed DFHack tree is on disk and version-exact**: grep `~/.steam/debian-installation/steamapps/common/DFHack/hack/lua/` (API modules like `gui.lua`) and `hack/scripts/` before trusting wiki/docs — the wiki describes classic keybindings and older APIs.
- **Deferred-input errors are invisible to RPC**: `gui.simulateInput` failures inside `dfhack.timeout` callbacks go to DFHack's console log, not the RPC reply. An action that "did nothing" usually errored there. Check the console log (error-feedback channel in NORTHSTAR II.7) before concluding keys are dead.

### References
- [DFHack Lua API](https://docs.dfhack.org/en/stable/docs/Lua%20API.html)
- https://dwarffortresswiki.org/index.php/Adventurer_mode_gameplay — combat, movement, companions, survival, crafting, quests
- https://dwarffortresswiki.org/index.php/Adventure_mode_quick_reference — keybindings and controls by category
