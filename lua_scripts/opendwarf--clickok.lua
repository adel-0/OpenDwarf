-- opendwarf--clickok.lua: Detect and click an "Okay" button to dismiss modal
-- dialogs. v50+ draws quest/divination popups OVER dungeonmode/Default with no
-- separate viewscreen and no main_interface widget flagged open, so screen-scan
-- is the only reliable detection. Prints JSON: {"found":true,"x":..,"y":..} or
-- {"found":false}.
local json = require("json")
local gps = df.global.gps

local function letter(t)
    if not t or not t.ch then return false end
    local c = t.ch
    return (c >= string.byte("A") and c <= string.byte("Z"))
        or (c >= string.byte("a") and c <= string.byte("z"))
end

for y = 0, gps.dimy - 1 do
    for x = 1, gps.dimx - 5 do
        local ok1, t1 = pcall(dfhack.screen.readTile, x, y, false)
        if ok1 and t1 and t1.ch == string.byte("O") then
            local ok2, t2 = pcall(dfhack.screen.readTile, x + 1, y, false)
            local ok3, t3 = pcall(dfhack.screen.readTile, x + 2, y, false)
            local ok4, t4 = pcall(dfhack.screen.readTile, x + 3, y, false)
            if ok2 and ok3 and ok4 and t2 and t3 and t4 and
               t2.ch == string.byte("k") and
               t3.ch == string.byte("a") and
               t4.ch == string.byte("y") then
                -- word-boundary guard: don't click "Okayama" in flowing text
                local okb, tb = pcall(dfhack.screen.readTile, x - 1, y, false)
                local oka, ta = pcall(dfhack.screen.readTile, x + 4, y, false)
                if not (okb and letter(tb)) and not (oka and letter(ta)) then
                    dfhack.timeout(1, 'frames', function()
                        gps.mouse_x = x + 1
                        gps.mouse_y = y
                        gps.precise_mouse_x = x + 1
                        gps.precise_mouse_y = y
                        local gui = require('gui')
                        local screen = dfhack.gui.getCurViewscreen()
                        gui.simulateInput(screen, '_MOUSE_L')
                    end)
                    print(json.encode({found = true, x = x, y = y}))
                    return
                end
            end
        end
    end
end
print(json.encode({found = false}))
