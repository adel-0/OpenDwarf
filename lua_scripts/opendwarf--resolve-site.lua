-- opendwarf--resolve-site.lua
-- Resolve a (possibly distant) site by name substring against the FULL world
-- site list — unlike the nearby-site scan in opendwarf--state.lua this is not
-- distance-capped, so a rumored site mentioned in conversation can be turned
-- into a concrete world position + site id for journey:<rumor_id>.
--
-- Usage:  opendwarf--resolve-site <name words...>
-- Output: JSON { "matches": [ {id,name,type,world_x,world_y,distance}, ... ] }
--   sorted by distance from the player; up to 8 matches.

local json = require("json")

local function site_name(site)
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
    return name
end

local function resolve(query)
    local result = { matches = {} }
    query = (query or ""):lower()
    if #query == 0 then
        print(json.encode(result))
        return
    end

    -- Player embark-tile position (best effort; falls back to map region origin).
    local player_gx, player_gy = 0, 0
    pcall(function()
        local map = df.global.world.map
        local adv = dfhack.world.getAdventurer()
        if adv then
            local ax, ay = dfhack.units.getPosition(adv)
            player_gx = map.region_x + math.floor(ax / 16)
            player_gy = map.region_y + math.floor(ay / 16)
        else
            local army_id = df.global.adventure.player_army_id
            local army = army_id >= 0 and df.army.find(army_id) or nil
            if army then
                player_gx = math.floor(army.pos.x / 3)
                player_gy = math.floor(army.pos.y / 3)
            else
                player_gx = map.region_x + 5
                player_gy = map.region_y + 5
            end
        end
    end)

    pcall(function()
        local sites = df.global.world.world_data.sites
        local hits = {}
        for i = 0, #sites - 1 do
            local site = sites[i]
            local name = site_name(site)
            if #name > 0 and name:lower():find(query, 1, true) then
                local cx = math.floor((site.global_min_x + site.global_max_x) / 2)
                local cy = math.floor((site.global_min_y + site.global_max_y) / 2)
                local ok_stype, stype = pcall(function() return df.world_site_type[site.type] end)
                table.insert(hits, {
                    id = site.id,
                    name = name,
                    type = ok_stype and stype or "?",
                    world_x = cx,
                    world_y = cy,
                    distance = math.abs(cx - player_gx) + math.abs(cy - player_gy),
                })
            end
        end
        table.sort(hits, function(a, b) return a.distance < b.distance end)
        for i = 1, math.min(8, #hits) do
            table.insert(result.matches, hits[i])
        end
    end)

    print(json.encode(result))
end

resolve(table.concat({...}, " "))
