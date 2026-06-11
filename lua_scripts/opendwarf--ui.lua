-- opendwarf--ui.lua: UI introspection and key discovery
-- Usage:
--   opendwarf--ui            → JSON: viewscreen stack, focus strings, adventure state,
--                              travel fields, gps dims, current message.
--   opendwarf--ui keys <pat> → space-separated df.interface_key names containing <pat>
--                              (case-insensitive comparison against name string)
--
-- All fields are wrapped in pcall; null is emitted on any failure.
-- Read-only, side-effect-free.

local args = {...}
local json = require("json")

-- -----------------------------------------------------------------------
-- Helper: safe pcall wrapper that returns nil on failure
-- -----------------------------------------------------------------------
local function safe(fn)
    local ok, val = pcall(fn)
    if ok then return val end
    return nil
end

-- -----------------------------------------------------------------------
-- Mode: keys <pattern>
-- -----------------------------------------------------------------------
if args[1] == "keys" then
    local pattern = (args[2] or ""):upper()
    local matches = {}
    -- Iterate df.interface_key enum by walking integer values until we hit nil
    -- names. The enum is dense from 0 upward; stop after a long run of gaps.
    local consecutive_gaps = 0
    for i = 0, 20000 do
        local ok, name = pcall(function()
            return df.interface_key[i]
        end)
        if ok and name and type(name) == "string" then
            consecutive_gaps = 0
            if pattern == "" or name:upper():find(pattern, 1, true) then
                matches[#matches + 1] = name
            end
        else
            consecutive_gaps = consecutive_gaps + 1
            if consecutive_gaps > 500 then
                break
            end
        end
    end
    print(table.concat(matches, " "))
    return
end

-- -----------------------------------------------------------------------
-- Mode: inspect (default, no args)
-- -----------------------------------------------------------------------

-- Walk the viewscreen stack (gview.view -> child -> child ...)
local function viewscreen_stack()
    local stack = {}
    local ok, view = pcall(function() return df.global.gview.view end)
    if not ok or not view then return stack end
    local cur = view
    local depth = 0
    while cur and depth < 32 do
        local name = safe(function() return cur._type.name end)
            or safe(function() return tostring(cur._type) end)
            or "unknown"
        stack[#stack + 1] = name
        cur = safe(function() return cur.child end)
        depth = depth + 1
        if cur == nil then break end
    end
    return stack
end

-- Get focus strings from DFHack
local function focus_strings()
    local ok, flist = pcall(dfhack.gui.getCurFocus)
    if not ok or not flist then return nil end
    -- getCurFocus returns a list of strings
    local out = {}
    for _, f in ipairs(flist) do
        out[#out + 1] = f
    end
    return out
end

-- adventure.menu name + number
local function menu_info()
    local num = safe(function() return df.global.adventure.menu end)
    if num == nil then return nil end
    local name = safe(function()
        return df["ui_advmode_menu"][num]
    end) or tostring(num)
    return { name = name, value = num }
end

-- player_control_state
local function control_state()
    local num = safe(function() return df.global.adventure.player_control_state end)
    if num == nil then return nil end
    local name = safe(function()
        return df["adventure_game_loop_type"][num]
    end) or tostring(num)
    return { name = name, value = num }
end

-- Travel fields
local function travel_info()
    local origin_x = safe(function() return df.global.adventure.travel_origin_x end)
    local origin_y = safe(function() return df.global.adventure.travel_origin_y end)
    local origin_z = safe(function() return df.global.adventure.travel_origin_z end)
    local not_moved = safe(function() return df.global.adventure.travel_not_moved end)
    local army_id = safe(function() return df.global.adventure.player_army_id end)

    -- Army position (3x embark coords) if the army record exists
    local army_x, army_y, army_z = nil, nil, nil
    if army_id and army_id >= 0 then
        local army = safe(function() return df.army.find(army_id) end)
        if army then
            army_x = safe(function() return army.pos.x end)
            army_y = safe(function() return army.pos.y end)
            army_z = safe(function() return army.pos.z end)
        end
    end

    return {
        origin_x = origin_x,
        origin_y = origin_y,
        origin_z = origin_z,
        not_moved = not_moved,
        player_army_id = army_id,
        army_pos_x = army_x,
        army_pos_y = army_y,
        army_pos_z = army_z,
    }
end

-- GPS dimensions
local function gps_dims()
    local w = safe(function() return df.global.gps.dimx end)
    local h = safe(function() return df.global.gps.dimy end)
    return { width = w, height = h }
end

-- Current adventure message
local function current_message()
    return safe(function()
        local msg = df.global.adventure.message
        if msg and type(msg) == "userdata" then
            return tostring(msg)
        end
        return msg
    end)
end

-- Assemble and emit the JSON payload
local result = {
    viewscreen_stack = viewscreen_stack(),
    focus_strings    = focus_strings(),
    menu             = menu_info(),
    player_control_state = control_state(),
    travel           = travel_info(),
    gps              = gps_dims(),
    message          = current_message(),
}

print(json.encode(result))
