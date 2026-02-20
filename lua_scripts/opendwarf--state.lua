-- opendwarf--state.lua: Extract adventure mode game state as JSON
-- Deployed to hack/scripts/ and run as a DFHack command

local json = require("json")

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
    if adv then
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

        -- Inventory
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
    end

    print(json.encode(result))
end

get_state()
