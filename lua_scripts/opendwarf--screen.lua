-- opendwarf--screen.lua: Read current screen focus and visible text rows.
-- Output: JSON with {focus: [...], rows: [...]} where rows are trimmed non-empty lines.
-- Used by the L3 escape hatch to let the LLM navigate unmodeled viewscreens.

local json = require("json")
local gps = df.global.gps

local result = {
    focus = {},
    rows = {}
}

-- Focus string(s)
local ok_f, focus = pcall(dfhack.gui.getCurFocus)
if ok_f and focus then
    result.focus = focus
end

-- Read screen text: scan visible rows, collect non-whitespace-only lines.
-- Cap at 50 rows to stay fast; columns beyond 120 are almost never meaningful.
local max_y = math.min(gps.dimy - 1, 49)
local max_x = math.min(gps.dimx - 1, 119)

for y = 0, max_y do
    local chars = {}
    for x = 0, max_x do
        local ok, tile = pcall(dfhack.screen.readTile, x, y, false)
        if ok and tile then
            local ch = tile.ch
            if ch >= 32 and ch < 128 then
                chars[#chars + 1] = string.char(ch)
            else
                chars[#chars + 1] = " "
            end
        else
            chars[#chars + 1] = " "
        end
    end
    local row = table.concat(chars)
    -- Trim trailing spaces
    row = row:match("^(.-)%s*$") or row
    if #row > 2 then
        result.rows[#result.rows + 1] = row
    end
end

print(json.encode(result))
