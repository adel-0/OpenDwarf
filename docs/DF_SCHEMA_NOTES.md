# DF Schema Audit Notes

Standing reference for field paths and enum values verified against `sources/df-structures`
(pinned to DF v53.10). Lookup tool: `uv run python -m opendwarf.dev schema <pattern>`.

Audit performed: 2026-06-14, against df-structures commit on branch `fix/travel-watchdog-stall`.

---

## Verified — Schema-Confirmed

| Field / Enum | Schema file : line | Confirmed value / notes |
|---|---|---|
| `unit.counters2.exhaustion` | `df.unit.xml:2940` | `uint32_t`, original-name=`exertion`. Path is correct. |
| `unit.counters2.hunger_timer` | `df.unit.xml:2942` | `uint32_t`, original-name=`hunger`. Path is correct. |
| `unit.counters2.thirst_timer` | `df.unit.xml:2943` | `uint32_t`, original-name=`thirst`. Path is correct. |
| `unit.counters2.sleepiness_timer` | `df.unit.xml:2944` | `uint32_t`, original-name=`drowsiness`. Path is correct. |
| `unit.flags1.inactive` | `df.unit.xml:1323` | `unit_flags1.DEAD` — set for dead units (and off-map critters). |
| `unit.flags2.killed` | `df.unit.xml:1375` | `unit_flags2.HAS_BEEN_KILLED` — set by the kill function. |
| `unit.flags1.hidden_in_ambush` | `df.unit.xml:1346` | `unit_flags1.AMBUSH` — stealth toggle. |
| `unit.body.blood_count` | `df.unit.xml:2832` | `uint32_t`, original-name=`blood`. |
| `unit.body.blood_max` | `df.unit.xml:2831` | `uint32_t`, original-name=`bloodmax`. |
| `adventure_game_loop_type` | `df.adventure.xml:819` | Enum: `NONE=-1, TAKING_INPUT=0, ENTER=1, INITIAL_PROCESSING=2, …` |
| `adventurest.player_control_state` | `df.adventure.xml:958` | Field name confirmed. |
| `adventurest.menu` | `df.adventure.xml:831` | `int16_t`, enum `ui_advmode_menu`. |
| `ui_advmode_menu` | `df.adventure.xml:34` | `Default=0, Look=1, …, Travel=26` (auto-increments). Key values: `Default` (MAIN), `Travel` (REGION_MAIN), `Inventory` (INVENTORY_LOOK). |
| `adventurest.total_move` | `df.adventure.xml:882` | `int32_t`. Confirmed. |
| `adventurest.travel_origin_x/y/z` | `df.adventure.xml:836-838` | Confirmed. |
| `adventurest.travel_not_moved` | `df.adventure.xml:851` | `int8_t`, original-name=`still_local`. |
| `adventurest.player_army_id` | `df.adventure.xml:890` | `int32_t`, ref-target=`army`. |
| `adventurest.interactions.party_core_members` | `df.adventure.xml:975` | `stl-vector<int32_t>`, original-name=`companion_party_hfid`. |
| `tiletype_shape` enum | `df.d_basics.xml:5867` | Integer values: `NONE=-1, EMPTY=0, FLOOR=1, BOULDER=2, PEBBLES=3, WALL=4, FORTIFICATION=5, STAIR_UP=6, STAIR_DOWN=7, STAIR_UPDOWN=8, RAMP=9, RAMP_TOP=10`. CLAUDE.md values (6–10) confirmed. |
| `df.global.game.main_interface.adventure` | `df.d_interface.xml:5505` | Path: `main_interface.adventure`, type `adventure_interfacest`. |
| `adventure_interfacest.attack` | `df.d_interface.xml:5437` | type `adventure_interface_attackst`. |
| `adventure_interface_attackst.open` | `df.d_interface.xml:5282` | `bool`. |
| `adventure_interface_attackst.mode` | `df.d_interface.xml:5283` | enum `adventure_interface_attack_mode_type`. |
| `adventure_interface_attack_mode_type` | `df.d_interface.xml:5267` | `NONE=-1, UNIT_CHOICE=0, CONFIRM=1, MOVE_CHOICE=2, AIM_TARGET=3, AIM_ATTACK=4, PARRY_CHOICE=5, BLOCK_CHOICE=6, DODGE_CHOICE=7` |
| `adventure_interface_attackst.unit_choice` | `df.d_interface.xml:5287` | `stl-vector<unit*>`. |
| `adventure_interfacest.conversation` | `df.d_interface.xml:5435` | type `adventure_interface_conversationst`. |
| `adventure_interface_conversationst.selecting_conversation` | `df.d_interface.xml:5106` | `bool`. |
| `adventure_interface_conversationst.select_option` | `df.d_interface.xml:5107` | `stl-vector<adventure_optionst*>`. |
| `adventure_interface_conversationst.conv_choice_info` | `df.d_interface.xml:5113` | `stl-vector<adventure_conversation_choice_infost*>`. |
| `adventure_interface_conversationst.choice_scroll_position` | `df.d_interface.xml:5118` | `int32_t`. |
| `adventure_interfacest.look` | `df.d_interface.xml:5450` | type `adventure_interface_lookst`. Fields: `open` (bool), `cursor` (coord). Note: CLAUDE.md previously wrote `look_cursor` — correct name is `cursor`. |
| `viewscreen_dungeonmodest` | `df.d_interface.xml:6441` | Confirmed viewscreen type. |
| `viewscreen_adventure_logst` | `df.adventure_log.xml:152` | Confirmed. |
| `viewscreen_dungeon_monsterstatusst` | `df.d_interface.xml:6571` | Confirmed. |
| `viewscreen_setupadventurest` | `df.d_interface.xml:6855` | Character creation viewscreen. |

---

## Dead Code Fixed

**`lua_scripts/opendwarf--state.lua`, lines 7-15 (original):** A `TILE_SHAPES` integer lookup
table existed with WRONG mappings (mapping index 2→"wall" when the schema says 2=BOULDER, 4=WALL).
This table was never referenced — the code below it used `df.tiletype_shape[shape_val]` for string
lookups. Removed the dead table; replaced with a comment citing the schema-confirmed values.

---

## Open — Runtime-Empirical (Schema Cannot Settle)

These items cannot be resolved from df-structures alone. They need live DF capture.

| Item | Where used | Why schema can't settle it | How to capture |
|---|---|---|---|
| Exhaustion threshold (currently 2000) | `game_state.py:exhaustion_critical` | The field path `counters2.exhaustion` is confirmed, but the numeric warning threshold is not in df-structures | Play until exhausted; watch value when the status icon appears |
| Hunger/thirst/drowsiness thresholds (75000, 50000, 57600) | `game_state.py` `_HUNGRY`, `_THIRSTY`, `_DROWSY` etc. | Same — field paths confirmed, cutoffs are from community wiki | Watch values against in-game hunger/thirst/sleep icons |
| Death-screen focus string | `game_state.py:from_raw()` Signal 3 | No death viewscreen type in df-structures v53.10 (`viewscreen_adventure_endst` does NOT exist). Schema has no adventure-end viewscreen. | Capture `dfhack.gui.getCurFocus()` at the moment of character death |
| Attack menu mode values in practice | `actions/skills.py` | Schema gives names (UNIT_CHOICE=0, etc.) but runtime ordering/skipped modes need live confirmation | Run `CombatStrikeSkill` and log `state.attack_menu_mode` at each step |

---

## Notes

- The schema is pinned to v53.10; the live game runs v0.53.14. For fields that exist in v53.10,
  field names and enum integers are stable across patch versions.
- Runtime-empirical items are NOT safe to change based on schema alone — don't update numeric
  thresholds without live capture data.
- `viewscreen_adventure_endst` (mentioned in old code comments) **does not exist** in df-structures.
  The three viewscreens covering adventure-mode lifecycle are `viewscreen_dungeonmodest` (gameplay),
  `viewscreen_setupadventurest` (character creation), and `viewscreen_titlest` (title screen).
  Death detection relies on unit flags (`flags2.killed`, `flags1.inactive`), not a viewscreen type.
