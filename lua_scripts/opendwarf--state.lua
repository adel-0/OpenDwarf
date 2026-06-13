-- opendwarf--state.lua: Extract adventure mode game state as JSON
-- Deployed to hack/scripts/ and run as a DFHack command

local json = require("json")

-- Tile shape categories for walkability
local TILE_SHAPES = {
    [0] = "empty",    -- NONE
    [1] = "floor",    -- FLOOR
    [2] = "wall",     -- WALL
    [3] = "ramp",     -- RAMP
    [4] = "stair_up", -- STAIR_UP
    [5] = "stair_down", -- STAIR_DOWN
    [6] = "stair_updown", -- STAIR_UPDOWN
}

local function get_tile_info(x, y, z)
    local ok, ttype = pcall(dfhack.maps.getTileType, x, y, z)
    if not ok or not ttype then
        return {shape = "unknown", walkable = false}
    end
    local ok2, attrs = pcall(function()
        return df.tiletype.attrs[ttype]
    end)
    if not ok2 or not attrs then
        return {shape = "unknown", walkable = false}
    end
    local shape_val = attrs.shape
    local shape_name = "unknown"
    local ok3, sname = pcall(function()
        return df.tiletype_shape[shape_val]
    end)
    if ok3 and sname then
        shape_name = sname
    end
    local walkable = (shape_name == "FLOOR" or shape_name == "STAIR_UP"
        or shape_name == "STAIR_DOWN" or shape_name == "STAIR_UPDOWN"
        or shape_name == "RAMP")
    return {shape = shape_name, walkable = walkable}
end

local function get_state()
    local result = {}

    -- Check adventure mode
    result.game = {}
    result.game.is_adventure_mode = dfhack.world.isAdventureMode()
    if not result.game.is_adventure_mode then
        print(json.encode(result))
        return
    end

    -- Game state
    local adv_state = df.global.adventure
    -- Use cur_year_tick for stable, non-wrapping tick count
    result.game.tick_counter = df.global.cur_year_tick
    result.game.cur_year = df.global.cur_year

    -- Enum values: index into the enum table to get the string name
    local pcs = adv_state.player_control_state
    local ok_pcs, pcs_name = pcall(function()
        return df.adventure_game_loop_type[pcs]
    end)
    result.game.player_control_state = ok_pcs and pcs_name or tostring(pcs)

    local menu = adv_state.menu
    local ok_menu, menu_name = pcall(function()
        return df.ui_advmode_menu[menu]
    end)
    result.game.menu_state = ok_menu and menu_name or tostring(menu)

    local ok_msg, msg = pcall(function() return adv_state.message end)
    result.game.message = (ok_msg and msg) or ""

    -- Cumulative successful-move counter: compare before/after an action to
    -- detect blocked movement (no dedicated bump/fail flag exists)
    local ok_tm, tm = pcall(function() return adv_state.total_move end)
    result.game.total_move = (ok_tm and tm) or -1

    -- Focus state
    local ok_focus, focus_list = pcall(function() return dfhack.gui.getCurFocus() end)
    if ok_focus and focus_list and #focus_list > 0 then
        result.game.focus_state = focus_list[1]
    else
        result.game.focus_state = ""
    end

    -- World context
    result.world = {}
    pcall(function()
        -- Translate world name from word indices into language word table
        local name = df.global.world.world_data.name
        local parts = {}
        for i = 0, #name.words - 1 do
            local word_idx = name.words[i]
            if word_idx >= 0 then
                local word = df.global.world.raws.language.words[word_idx]
                if word then table.insert(parts, word.word) end
            end
        end
        result.world.world_name = table.concat(parts, " ")
    end)
    -- Site detection via global embark-tile coordinates
    -- region_x/y are embark-tile (block) coordinates; site.global_min/max_x/y are in the same system
    pcall(function()
        local map = df.global.world.map
        local adv = dfhack.world.getAdventurer()
        local ax, ay, az = dfhack.units.getPosition(adv)
        -- Player's embark-tile coordinate = region_x + floor(local_x / 16)
        local player_gx = map.region_x + math.floor(ax / 16)
        local player_gy = map.region_y + math.floor(ay / 16)
        result.world.player_world_x = player_gx
        result.world.player_world_y = player_gy

        local sites = df.global.world.world_data.sites
        for i = 0, #sites - 1 do
            local site = sites[i]
            if player_gx >= site.global_min_x and player_gx <= site.global_max_x and
               player_gy >= site.global_min_y and player_gy <= site.global_max_y then
                -- Translate site name
                local name_parts = {}
                for j = 0, #site.name.words - 1 do
                    local word_idx = site.name.words[j]
                    if word_idx >= 0 then
                        local word = df.global.world.raws.language.words[word_idx]
                        if word then table.insert(name_parts, word.word) end
                    end
                end
                local name = table.concat(name_parts, " ")
                if #name == 0 then
                    name = site.name.first_name or ""
                end
                result.world.site_name = name
                result.world.site_id = site.id
                local ok_stype, stype = pcall(function() return df.world_site_type[site.type] end)
                result.world.site_type = ok_stype and stype or tostring(site.type)
                break
            end
        end
    end)

    -- Fast travel state — detect BEFORE adventurer check (getAdventurer() returns nil during travel)
    result.fast_travel = {}
    pcall(function()
        local menu_val = adv_state.menu
        local ok_menu2, menu_name2 = pcall(function() return df.ui_advmode_menu[menu_val] end)
        local menu_str = ok_menu2 and menu_name2 or tostring(menu_val)
        result.fast_travel.active = (menu_str == "Travel")
        -- During travel, read army position and location from screen
        if result.fast_travel.active then
            -- Get army position (world coordinates used during fast travel)
            pcall(function()
                local army_id = adv_state.player_army_id
                if army_id >= 0 then
                    local army = df.army.find(army_id)
                    if army then
                        result.fast_travel.army_pos = {
                            x = army.pos.x, y = army.pos.y, z = army.pos.z
                        }
                    end
                end
            end)
            pcall(function()
                local gps = df.global.gps
                for y = gps.dimy - 3, gps.dimy - 1 do
                    local row = ""
                    for x = 0, math.min(80, gps.dimx - 1) do
                        local ok_t, t = pcall(dfhack.screen.readTile, x, y, false)
                        if ok_t and t and t.ch and t.ch >= 32 and t.ch < 128 then
                            row = row .. string.char(t.ch)
                        end
                    end
                    row = row:match("^%s*(.-)%s*$")
                    if #row > 5 then
                        result.fast_travel.location_text = row
                        break
                    end
                end
            end)
        end
    end)

    -- Adventurer info (nil during fast travel — skip to end sections)
    local adv = dfhack.world.getAdventurer()
    result.adventurer = {}

    -- Death detection (LIVE-VERIFY: exact flags/focus on the death screen pending a
    -- real death; the signals below are confirmed to be accessible on a live unit).
    -- Signal 1: adventurer unit death flags.
    --   flags2.killed — set by the game when the unit is killed (confirmed accessible)
    --   flags1.inactive — set when unit leaves active play (also catches killed state)
    --   dfhack.units.isAlive — returns false when flags2.killed or equivalent is set
    -- Signal 2: adventurer nil AND NOT in fast travel → game-over / title screen.
    result.adventurer_dead = false
    if adv then
        local ok_killed, v_killed = pcall(function() return adv.flags2.killed end)
        local ok_inactive, v_inactive = pcall(function() return adv.flags1.inactive end)
        local ok_alive, v_alive = pcall(dfhack.units.isAlive, adv)
        local killed = ok_killed and v_killed or false
        local inactive = ok_inactive and v_inactive or false
        local not_alive = ok_alive and (not v_alive) or false
        result.adventurer_dead = killed or inactive or not_alive
    end

    if not adv then
        -- Signal 2: getAdventurer() returns nil. During fast travel this is normal
        -- (fast_travel.active = true). Outside of fast travel, nil adventurer means
        -- the character is dead / game has returned to the title screen.
        if not result.fast_travel.active then
            result.adventurer_dead = true
        end
        -- Still include nearby sites during fast travel
        result.nearby_sites = {}
        pcall(function()
            -- Use army position for accurate site distances during travel
            -- Army coords are 3x embark tile coords (empirically confirmed)
            local player_gx, player_gy
            local ft = result.fast_travel or {}
            if ft.army_pos then
                player_gx = math.floor(ft.army_pos.x / 3)
                player_gy = math.floor(ft.army_pos.y / 3)
            else
                local map = df.global.world.map
                player_gx = map.region_x + 5
                player_gy = map.region_y + 5
            end
            result.world = result.world or {}
            result.world.player_world_x = player_gx
            result.world.player_world_y = player_gy
            local sites = df.global.world.world_data.sites
            local site_list = {}
            for i = 0, #sites - 1 do
                local site = sites[i]
                local cx = (site.global_min_x + site.global_max_x) / 2
                local cy = (site.global_min_y + site.global_max_y) / 2
                local dist = math.abs(cx - player_gx) + math.abs(cy - player_gy)
                if dist <= 200 then
                    table.insert(site_list, {site = site, dist = dist, player_gx = player_gx, player_gy = player_gy})
                end
            end
            table.sort(site_list, function(a, b) return a.dist < b.dist end)
            for i = 1, math.min(5, #site_list) do
                local entry = site_list[i]
                local site = entry.site
                local name = ""
                pcall(function()
                    local parts = {}
                    for j = 0, #site.name.words - 1 do
                        local widx = site.name.words[j]
                        if widx >= 0 then
                            local word = df.global.world.raws.language.words[widx]
                            if word then table.insert(parts, word.word) end
                        end
                    end
                    name = table.concat(parts, " ")
                    if #name == 0 then name = site.name.first_name or "" end
                end)
                local ok_stype, stype = pcall(function() return df.world_site_type[site.type] end)
                local cx = (site.global_min_x + site.global_max_x) / 2
                local cy = (site.global_min_y + site.global_max_y) / 2
                local dx = cx - entry.player_gx
                local dy = cy - entry.player_gy
                local dir = ""
                if dy < 0 then dir = "N" else dir = "S" end
                if math.abs(dx) > math.abs(dy) / 2 then
                    if dx > 0 then dir = dir .. "E" elseif dx < 0 then dir = dir .. "W" end
                end
                if math.abs(dy) <= math.abs(dx) / 2 then
                    if dx > 0 then dir = "E" else dir = "W" end
                end
                table.insert(result.nearby_sites, {
                    id = site.id,
                    name = name,
                    type = ok_stype and stype or "?",
                    distance = math.floor(entry.dist),
                    direction = dir,
                })
            end
        end)
        print(json.encode(result))
        return
    end

    local ok_name, name = pcall(function() return dfhack.units.getReadableName(adv) end)
    result.adventurer.name = ok_name and name or "Unknown"

    -- getPosition returns x, y, z as three separate values
    local ax, ay, az = dfhack.units.getPosition(adv)
    if ax then
        result.adventurer.position = {x = ax, y = ay, z = az}
    end

    local ok_blood, blood = pcall(function() return adv.body.blood_count end)
    result.adventurer.blood_count = ok_blood and blood or 0

    local ok_bmax, bmax = pcall(function() return adv.body.blood_max end)
    result.adventurer.blood_max = ok_bmax and bmax or 0

    -- Stealth: A_SNEAK toggles flags1.hidden_in_ambush (live-verified v0.53.14)
    local ok_sneak, sneaking = pcall(function() return adv.flags1.hidden_in_ambush end)
    result.adventurer.sneaking = ok_sneak and sneaking or false

    -- Physiological timers (count up from 0; thresholds empirical, ~v50+ values)
    -- hungry ≈ 75000, thirsty ≈ 50000, drowsy ≈ 57600
    pcall(function()
        local c2 = adv.counters2
        result.adventurer.hunger_timer = c2.hunger_timer
        result.adventurer.thirst_timer = c2.thirst_timer
        result.adventurer.sleepiness_timer = c2.sleepiness_timer
        result.adventurer.exhaustion = c2.exhaustion
    end)

    -- Body part status (wounds)
    result.adventurer.wounds = {}
    pcall(function()
        for i, wound in ipairs(adv.body.wounds) do
            for _, part_wound in ipairs(wound.parts) do
                local body_part_name = "unknown"
                pcall(function()
                    local bp = adv.body.body_plan.body_parts[part_wound.body_part_id]
                    body_part_name = bp.name_singular[0].value
                end)
                local flags = {}
                pcall(function()
                    if part_wound.flags2.bleeding then table.insert(flags, "bleeding") end
                    if part_wound.flags2.infected then table.insert(flags, "infected") end
                    if part_wound.flags2.severed then table.insert(flags, "severed") end
                    if part_wound.flags2.missing then table.insert(flags, "missing") end
                end)
                if #flags > 0 then
                    table.insert(result.adventurer.wounds, {
                        part = body_part_name,
                        status = table.concat(flags, ", "),
                    })
                end
            end
        end
    end)

    -- Skills
    result.adventurer.skills = {}
    pcall(function()
        local soul = adv.status.current_soul
        if soul then
            for _, skill in ipairs(soul.skills) do
                if skill.rating > 0 then
                    local ok_sname, sname = pcall(function()
                        return df.job_skill[skill.id]
                    end)
                    table.insert(result.adventurer.skills, {
                        id = ok_sname and sname or tostring(skill.id),
                        level = skill.rating,
                        experience = skill.experience,
                    })
                end
            end
        end
    end)

    -- Inventory (with weapon readied status, quality, and consumable flags)
    local mode_names = {
        [0] = "Hauled", [1] = "Weapon", [2] = "Worn", [3] = "Piercing",
        [4] = "Flask", [5] = "WrappedAround", [6] = "StuckIn",
        [7] = "InMouth", [8] = "Pet", [9] = "SewnInto", [10] = "Strapped",
    }
    local quality_names = {"ordinary", "well-crafted", "finely-crafted", "superior", "exceptional", "masterwork"}
    -- Item types that are food (edible): MEAT=48,FISH=49,FISH_RAW=50,VERMIN=51,SEEDS=53,
    -- PLANT=54,PLANT_GROWTH=56,CHEESE=71,FOOD=72,EGG=88
    local FOOD_TYPES = {[48]=true,[49]=true,[50]=true,[51]=true,[53]=true,
                        [54]=true,[56]=true,[71]=true,[72]=true,[88]=true}
    -- Item types that are drink: DRINK=69
    local DRINK_TYPES = {[69]=true}
    result.inventory = {}
    pcall(function()
        for _, inv_item in ipairs(adv.inventory) do
            local item = inv_item.item
            local mode = mode_names[inv_item.mode] or tostring(inv_item.mode)
            local ok_desc, desc = pcall(dfhack.items.getDescription, item, 0)
            local quality_val = 0
            pcall(function() quality_val = item:getQuality() end)
            local quality = quality_names[quality_val + 1] or "ordinary"
            local type_id = -1
            pcall(function() type_id = item:getType() end)
            table.insert(result.inventory, {
                name = ok_desc and desc or "?",
                mode = mode,
                quality = quality,
                type_id = type_id,
                is_food = FOOD_TYPES[type_id] or false,
                is_drink = DRINK_TYPES[type_id] or false,
            })
        end
    end)

    -- Floor items at adventurer's position
    result.floor_items = {}
    if ax then
        pcall(function()
            local ok_items, items_at = pcall(dfhack.items.getItemsInBox,
                ax, ay, az, ax, ay, az)
            if ok_items and items_at then
                for _, item in ipairs(items_at) do
                    if item.flags.on_ground then
                        local ok_desc, desc = pcall(dfhack.items.getDescription, item, 0)
                        local quality_val = 0
                        pcall(function() quality_val = item:getQuality() end)
                        local quality = quality_names[quality_val + 1] or "ordinary"
                        table.insert(result.floor_items, {
                            id = item.id,
                            name = ok_desc and desc or "?",
                            quality = quality,
                        })
                    end
                end
            end
        end)
    end

    -- Map tiles around adventurer (5x5 grid)
    result.map_tiles = {}
    if ax then
        local radius = 2
        for dy = -radius, radius do
            local row = {}
            for dx = -radius, radius do
                local tx, ty = ax + dx, ay + dy
                local info = get_tile_info(tx, ty, az)
                -- Compact representation: "." walkable, "#" wall, ">" stair down, etc.
                local ch = "?"
                local shape = info.shape
                if shape == "FLOOR" or shape == "RAMP" then ch = "."
                elseif shape == "WALL" or shape == "FORTIFICATION" then ch = "#"
                elseif shape == "STAIR_UP" then ch = "<"
                elseif shape == "STAIR_DOWN" then ch = ">"
                elseif shape == "STAIR_UPDOWN" then ch = "X"
                elseif shape == "EMPTY" or shape == "NONE" then ch = " "
                elseif shape == "OPEN" then ch = "."
                end
                table.insert(row, ch)
            end
            table.insert(result.map_tiles, table.concat(row))
        end
    end

    -- Nearby units
    result.nearby_units = {}
    if ax then
        local range = 30
        local ok_units, units = pcall(dfhack.units.getUnitsInBox,
            ax - range, ay - range, az,
            ax + range, ay + range, az
        )
        if ok_units and units then
            for _, unit in ipairs(units) do
                if unit ~= adv and dfhack.units.isActive(unit) then
                    local ux, uy, uz = dfhack.units.getPosition(unit)
                    local ok_uname, uname = pcall(dfhack.units.getReadableName, unit)
                    local ok_race, race = pcall(function()
                        return df.creature_raw.find(unit.race).name[0]
                    end)
                    local ok_hostile, hostile = pcall(dfhack.units.isDanger, unit)
                    local dist = 0
                    if ux then
                        dist = math.abs(ux - ax) + math.abs(uy - ay)
                    end
                    local hf_id = -1
                    pcall(function() hf_id = unit.hist_figure_id or -1 end)
                    -- Tame/citizen flags let Python tell a huntable wild creature
                    -- (wolf, deer) apart from a pet or a civ member (attacking the
                    -- latter is a crime). isDanger() does NOT flag wild predators —
                    -- they only become "danger" once provoked — so combat targeting
                    -- cannot rely on is_hostile alone (LIVE-VERIFIED v0.53.14).
                    local ok_tame, tame = pcall(dfhack.units.isTame, unit)
                    local ok_cit, citizen = pcall(dfhack.units.isCitizen, unit)
                    table.insert(result.nearby_units, {
                        id = unit.id,
                        name = ok_uname and uname or "?",
                        race = ok_race and race or "?",
                        position = ux and {x = ux, y = uy, z = uz} or {},
                        is_hostile = ok_hostile and hostile or false,
                        is_tame = ok_tame and tame or false,
                        is_citizen = ok_cit and citizen or false,
                        distance = dist,
                        hist_figure_id = hf_id,
                    })
                end
            end
        end
    end

    -- Party members
    result.party = {}
    pcall(function()
        local party_members = adv_state.interactions.party_core_members
        for i = 0, #party_members - 1 do
            local hf_id = party_members[i]
            local ok_hf, hf = pcall(function()
                return df.historical_figure.find(hf_id)
            end)
            if ok_hf and hf then
                local ok_hname, hname = pcall(function()
                    return dfhack.units.getReadableName(hf)
                end)
                table.insert(result.party, {
                    hf_id = hf_id,
                    name = ok_hname and hname or ("hf#" .. tostring(hf_id)),
                })
            end
        end
    end)

    -- Combat detection
    result.in_combat = false
    for _, u in ipairs(result.nearby_units) do
        if u.is_hostile then
            result.in_combat = true
            break
        end
    end

    -- Announcement panel state (NPC speech, combat results, etc.)
    result.showing_announcements = false
    result.announcement_text = {}
    pcall(function()
        local flags = df.global.world.status.temp_flag
        result.showing_announcements = flags.adv_showing_announcements
        if result.showing_announcements then
            -- Read the announcement text from the screen (rows 7-12 of the right panel)
            local gps = df.global.gps
            for y = 6, 14 do
                local row = ""
                for x = 30, gps.dimx - 1 do
                    local ok, tile = pcall(dfhack.screen.readTile, x, y, false)
                    if ok and tile and tile.ch and tile.ch >= 32 and tile.ch < 128 then
                        row = row .. string.char(tile.ch)
                    end
                end
                row = row:match("^%s*(.-)%s*$")  -- trim
                if #row > 0 then
                    table.insert(result.announcement_text, row)
                end
            end
        end
    end)

    -- Combat log (recent announcements)
    result.combat_log = {}
    pcall(function()
        local anns = df.global.world.status.adv_announcement
        local start = math.max(0, #anns - 5)
        for i = start, #anns - 1 do
            table.insert(result.combat_log, anns[i].text)
        end
    end)

    -- Conversation choices (two phases)
    result.conversation_choices = {}
    result.conversation_phase = "none"
    pcall(function()
        local adventure_ui = df.global.game.main_interface.adventure
        local conv = adventure_ui.conversation

        -- Phase 1: selecting who to address (list of nearby NPCs)
        if conv.selecting_conversation and #conv.select_option > 0 then
            result.conversation_phase = "select_npc"
            local adv = dfhack.world.getAdventurer()
            for i, opt in ipairs(conv.select_option) do
                local name = nil
                -- For talk_existing: find the non-self participant
                pcall(function()
                    if opt.conv_actev then
                        for _, p in ipairs(opt.conv_actev.participants) do
                            if p.unit_id ~= adv.id then
                                local u = df.unit.find(p.unit_id)
                                if u then name = dfhack.units.getReadableName(u) end
                                break
                            end
                        end
                    end
                end)
                -- For talk_new: direct unit_id field
                if not name then
                    pcall(function()
                        local u = df.unit.find(opt.unit_id)
                        if u then name = dfhack.units.getReadableName(u) end
                    end)
                end
                -- Fallback: use type name
                if not name then
                    local typename = tostring(opt):match("<(.-):")
                    name = typename or ("option_" .. tostring(i))
                end
                table.insert(result.conversation_choices, {
                    index = i,  -- 0-based (DFHack ipairs on vectors is 0-indexed)
                    text = name,
                })
            end
            return
        end

        -- Phase 2: dialogue choices (conv_choice_info)
        if #conv.conv_choice_info > 0 then
            result.conversation_phase = "dialogue"
            for i, choice in ipairs(conv.conv_choice_info) do
                local text = ""
                for _, data in ipairs(choice.title.text) do
                    text = text .. data.value
                end
                table.insert(result.conversation_choices, {
                    index = i,  -- DFHack ipairs on vectors is 0-indexed
                    text = text,
                })
            end
        end
    end)

    -- Adventure attack menu (dungeonmode/Attack). Mouse-driven, multi-step:
    -- mode 0 = pick target (unit_choice, in screen-row order), 2 = pick move
    -- (Strike/Dodge), 3 = pick body part, 4 = pick weapon/attack-type → resolves.
    -- The CombatStrikeSkill drives it by watching `mode`; unit_choice maps a target
    -- id to its on-screen row index. LIVE-VERIFIED v0.53.14. pcall-guarded — the
    -- struct is absent on non-adventure screens.
    result.attack_menu = { open = false, mode = -1, unit_choice = {} }
    pcall(function()
        local atk = df.global.game.main_interface.adventure.attack
        result.attack_menu.open = atk.open and true or false
        result.attack_menu.mode = atk.mode
        for _, u in ipairs(atk.unit_choice) do
            local id = -1
            pcall(function() id = u.id end)
            table.insert(result.attack_menu.unit_choice, id)
        end
    end)

    -- Adventurer entity/faction membership
    result.adventurer_entities = {}
    result.npc_relationships = {}
    result.quests = {}
    local adv_hf = nil
    local adv_hf_id = nil
    pcall(function()
        -- hist_figure_id is a direct field on the unit
        adv_hf_id = adv.hist_figure_id
        if not adv_hf_id or adv_hf_id < 0 then return end
        adv_hf = df.historical_figure.find(adv_hf_id)
        if not adv_hf then return end

        for _, link in ipairs(adv_hf.entity_links) do
            local ok_lt, lt = pcall(function()
                return df.histfig_entity_link_type[link:getType()]
            end)
            local ok_ent, ent = pcall(function()
                return df.historical_entity.find(link.entity_id)
            end)
            if ok_ent and ent then
                local ent_name = ""
                pcall(function()
                    local parts = {}
                    for j = 0, #ent.name.words - 1 do
                        local widx = ent.name.words[j]
                        if widx >= 0 then
                            local word = df.global.world.raws.language.words[widx]
                            if word then table.insert(parts, word.word) end
                        end
                    end
                    ent_name = table.concat(parts, " ")
                end)
                table.insert(result.adventurer_entities, {
                    name = ent_name,
                    link_type = ok_lt and lt or "MEMBER",
                })
            end
        end
    end)

    -- NPC relationships (HF-to-HF links for nearby units)
    if adv_hf then
        for _, unit_info in ipairs(result.nearby_units) do
            pcall(function()
                local unit_obj = df.unit.find(unit_info.id)
                if not unit_obj then return end
                -- Try direct field first, then general_refs
                local npc_hf_id = nil
                pcall(function()
                    local hfid = unit_obj.hist_figure_id
                    if hfid and hfid >= 0 then npc_hf_id = hfid end
                end)
                if not npc_hf_id then
                    pcall(function()
                        for _, ref in ipairs(unit_obj.general_refs) do
                            local ok_t, t = pcall(function() return ref:getType() end)
                            if ok_t and tostring(t) == "HISTFIG" then
                                local ok_id, hfid = pcall(function() return ref.hist_figure_id end)
                                if ok_id and hfid and hfid >= 0 then
                                    npc_hf_id = hfid
                                    break
                                end
                            end
                        end
                    end)
                end
                if not npc_hf_id then return end
                for _, link in ipairs(adv_hf.histfig_links) do
                    if link.target_hf == npc_hf_id then
                        local ok_lt, lt = pcall(function()
                            return df.histfig_hf_link_type[link:getType()]
                        end)
                        table.insert(result.npc_relationships, {
                            name = unit_info.name,
                            unit_id = unit_info.id,
                            relationship = ok_lt and lt or "KNOWN",
                        })
                        break
                    end
                end
            end)
        end
    end

    -- Quest log (from adventure log viewscreen if active, else world agreements)
    pcall(function()
        local log_vs = dfhack.gui.getViewscreenByType(df.viewscreen_adventure_logst, 0)
        if log_vs then
            -- Viewscreen is open — try reading quest fields
            pcall(function()
                for i, q in ipairs(log_vs.quests or {}) do
                    local ok_txt, txt = pcall(function() return q.text or tostring(q) end)
                    if ok_txt and txt and #tostring(txt) > 0 then
                        table.insert(result.quests, tostring(txt))
                    end
                end
            end)
        end
        -- Also try world agreements involving the adventurer's HF
        if adv_hf_id then
            local ok_agr, agreements = pcall(function() return df.global.world.agreements end)
            if ok_agr and agreements then
                for i = 0, #agreements - 1 do
                    local agr = agreements[i]
                    pcall(function()
                        local involved = false
                        for _, p in ipairs(agr.details.participants) do
                            if p.figure_id == adv_hf_id then
                                involved = true
                                break
                            end
                        end
                        if involved then
                            local ok_t, t = pcall(function() return agr.details.type end)
                            local ok_n, n = pcall(function() return agr.details.target_name end)
                            local desc = (ok_t and tostring(t) or "agreement")
                            if ok_n and n and #n > 0 then
                                desc = desc .. ": " .. n
                            end
                            table.insert(result.quests, desc)
                        end
                    end)
                end
            end
        end
    end)

    -- Nearby sites (for LLM context — closest 3 within range)
    result.nearby_sites = {}
    pcall(function()
        local map = df.global.world.map
        local lax, lay = dfhack.units.getPosition(adv)
        local player_gx = map.region_x + math.floor(lax / 16)
        local player_gy = map.region_y + math.floor(lay / 16)
        local sites = df.global.world.world_data.sites
        local site_list = {}
        for i = 0, #sites - 1 do
            local site = sites[i]
            local cx = (site.global_min_x + site.global_max_x) / 2
            local cy = (site.global_min_y + site.global_max_y) / 2
            local dist = math.abs(cx - player_gx) + math.abs(cy - player_gy)
            if dist <= 200 then  -- Only sites within ~200 embark tiles
                table.insert(site_list, {site = site, dist = dist})
            end
        end
        table.sort(site_list, function(a, b) return a.dist < b.dist end)
        for i = 1, math.min(5, #site_list) do
            local entry = site_list[i]
            local site = entry.site
            local name = ""
            pcall(function()
                local parts = {}
                for j = 0, #site.name.words - 1 do
                    local widx = site.name.words[j]
                    if widx >= 0 then
                        local word = df.global.world.raws.language.words[widx]
                        if word then table.insert(parts, word.word) end
                    end
                end
                name = table.concat(parts, " ")
                if #name == 0 then name = site.name.first_name or "" end
            end)
            local ok_stype, stype = pcall(function() return df.world_site_type[site.type] end)
            -- Compute direction from player
            local cx = (site.global_min_x + site.global_max_x) / 2
            local cy = (site.global_min_y + site.global_max_y) / 2
            local dx = cx - player_gx
            local dy = cy - player_gy
            local dir = ""
            if dy < 0 then dir = "N" else dir = "S" end
            if math.abs(dx) > math.abs(dy) / 2 then
                if dx > 0 then dir = dir .. "E" elseif dx < 0 then dir = dir .. "W" end
            end
            if math.abs(dy) <= math.abs(dx) / 2 then
                if dx > 0 then dir = "E" else dir = "W" end
            end
            table.insert(result.nearby_sites, {
                id = site.id,
                name = name,
                type = ok_stype and stype or "?",
                distance = math.floor(entry.dist),
                direction = dir,
                world_x = math.floor(cx),
                world_y = math.floor(cy),
            })
        end
    end)

    print(json.encode(result))
end

get_state()
