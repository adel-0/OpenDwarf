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
    result.game.tick_counter = adv_state.tick_counter

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

    -- Focus state
    local ok_focus, focus_list = pcall(function() return dfhack.gui.getCurFocus() end)
    if ok_focus and focus_list and #focus_list > 0 then
        result.game.focus_state = focus_list[1]
    else
        result.game.focus_state = ""
    end

    -- Adventurer info
    local adv = dfhack.world.getAdventurer()
    result.adventurer = {}
    if not adv then
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

    -- Inventory (with weapon readied status)
    local mode_names = {
        [0] = "Hauled", [1] = "Weapon", [2] = "Worn", [3] = "Piercing",
        [4] = "Flask", [5] = "WrappedAround", [6] = "StuckIn",
        [7] = "InMouth", [8] = "Pet", [9] = "SewnInto", [10] = "Strapped",
    }
    result.inventory = {}
    pcall(function()
        for _, inv_item in ipairs(adv.inventory) do
            local item = inv_item.item
            local mode = mode_names[inv_item.mode] or tostring(inv_item.mode)
            local ok_desc, desc = pcall(dfhack.items.getDescription, item, 0)
            table.insert(result.inventory, {
                name = ok_desc and desc or "?",
                mode = mode,
            })
        end
    end)

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
        local range = 15
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
                    table.insert(result.nearby_units, {
                        id = unit.id,
                        name = ok_uname and uname or "?",
                        race = ok_race and race or "?",
                        position = ux and {x = ux, y = uy, z = uz} or {},
                        is_hostile = ok_hostile and hostile or false,
                        distance = dist,
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

    -- Combat log (recent announcements)
    result.combat_log = {}
    pcall(function()
        local anns = df.global.world.status.adv_announcement
        local start = math.max(0, #anns - 5)
        for i = start, #anns - 1 do
            table.insert(result.combat_log, anns[i].text)
        end
    end)

    -- Conversation choices
    result.conversation_choices = {}
    pcall(function()
        local adventure_ui = df.global.game.main_interface.adventure
        for i, choice in ipairs(adventure_ui.conversation.conv_choice_info) do
            local text = ""
            for _, data in ipairs(choice.title.text) do
                text = text .. data.value
            end
            table.insert(result.conversation_choices, {
                index = i - 1,  -- 0-indexed for Python
                text = text,
            })
        end
    end)

    print(json.encode(result))
end

get_state()
