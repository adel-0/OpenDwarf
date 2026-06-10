-- opendwarf--map.lua: Extract a wide tile grid around the adventurer as JSON
-- Usage: opendwarf--map [radius]   (default radius 40, z range az-2..az+2)
--
-- Output: {"origin": {"x","y","z"}, "adventurer": {"x","y","z"}, "z_levels": {"<abs_z>": [rows]}}
-- All coordinates are ABSOLUTE world tiles: abs = region_{x,y} * 16 + local.
-- Row chars: . floor  # wall  < > X stairs  ^ ramp  ~ water  + door  (space) open air  ? unknown
--
-- NEEDS LIVE VERIFICATION: payload size/latency, door detection, water flags.

local json = require("json")

local args = {...}
local RADIUS = tonumber(args[1]) or 40
local Z_BELOW, Z_ABOVE = 2, 2

local adv = dfhack.world.getAdventurer()
if not adv then
    -- During fast travel there is no local adventurer unit; map extraction is meaningless
    print(json.encode({error = "no adventurer (fast travel?)"}))
    return
end

local ax, ay, az = dfhack.units.getPosition(adv)
if not ax then
    print(json.encode({error = "no position"}))
    return
end

local map = df.global.world.map
local abs_ox = map.region_x * 16 + (ax - RADIUS)
local abs_oy = map.region_y * 16 + (ay - RADIUS)

-- Shape -> char mapping (df.tiletype_shape enum names)
local SHAPE_CHAR = {
    FLOOR = ".", BOULDER = ".", PEBBLES = ".", SAPLING = ".", SHRUB = ".",
    BROOK_TOP = ".", GRASS = ".",
    WALL = "#", FORTIFICATION = "#", TRUNK_BRANCH = "#",
    STAIR_UP = "<", STAIR_DOWN = ">", STAIR_UPDOWN = "X",
    RAMP = "^",
    RAMP_TOP = " ", EMPTY = " ", NONE = " ", BRANCH = " ", TWIG = " ",
    BROOK_BED = "~", ENDLESS_PIT = " ",
}

-- Collect door positions from the buildings vector (one pass, bbox-filtered)
local door_pos = {}
pcall(function()
    local lo_x, hi_x = ax - RADIUS, ax + RADIUS
    local lo_y, hi_y = ay - RADIUS, ay + RADIUS
    for _, bld in ipairs(df.global.world.buildings.all) do
        if df.building_doorst:is_instance(bld)
            and bld.x1 >= lo_x and bld.x1 <= hi_x
            and bld.y1 >= lo_y and bld.y1 <= hi_y
            and bld.z >= az - Z_BELOW and bld.z <= az + Z_ABOVE then
            door_pos[bld.z .. ":" .. bld.x1 .. ":" .. bld.y1] = true
        end
    end
end)

local function tile_char(x, y, z, block)
    if not block then return "?" end
    local lx, ly = x % 16, y % 16
    local tt = block.tiletype[lx][ly]
    if not tt then return "?" end
    local ok, attrs = pcall(function() return df.tiletype.attrs[tt] end)
    if not ok or not attrs then return "?" end
    local ok2, shape_name = pcall(function() return df.tiletype_shape[attrs.shape] end)
    local ch = SHAPE_CHAR[ok2 and shape_name or ""] or "?"
    -- Water override: deep liquid makes the tile hazardous
    local okd, des = pcall(function() return block.designation[lx][ly] end)
    if okd and des then
        local flow = des.flow_size or 0
        if flow >= 4 then
            if des.liquid_type then
                ch = "#"  -- magma: treat as impassable
            else
                ch = "~"
            end
        end
    end
    -- Door override
    if door_pos[z .. ":" .. x .. ":" .. y] then
        ch = "+"
    end
    return ch
end

local result = {
    origin = {x = abs_ox, y = abs_oy, z = az - Z_BELOW},
    adventurer = {x = map.region_x * 16 + ax, y = map.region_y * 16 + ay, z = az},
    z_levels = {},
}

for z = az - Z_BELOW, az + Z_ABOVE do
    local rows = {}
    local block_cache = {}
    for y = ay - RADIUS, ay + RADIUS do
        local row = {}
        for x = ax - RADIUS, ax + RADIUS do
            local bkey = math.floor(x / 16) .. ":" .. math.floor(y / 16)
            local block = block_cache[bkey]
            if block == nil then
                local okb, b = pcall(dfhack.maps.getTileBlock, x, y, z)
                block = (okb and b) or false
                block_cache[bkey] = block
            end
            if block == false then
                table.insert(row, "?")
            else
                table.insert(row, tile_char(x, y, z, block))
            end
        end
        table.insert(rows, table.concat(row))
    end
    result.z_levels[tostring(z)] = rows
end

print(json.encode(result))
