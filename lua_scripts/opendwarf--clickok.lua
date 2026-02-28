-- opendwarf--clickok.lua: Find and click "Okay" button to dismiss help dialogs
local gps = df.global.gps
for y = 0, gps.dimy - 1 do
    for x = 0, gps.dimx - 5 do
        local ok1, t1 = pcall(dfhack.screen.readTile, x, y, false)
        local ok2, t2 = pcall(dfhack.screen.readTile, x+1, y, false)
        local ok3, t3 = pcall(dfhack.screen.readTile, x+2, y, false)
        local ok4, t4 = pcall(dfhack.screen.readTile, x+3, y, false)
        if ok1 and ok2 and ok3 and ok4 and t1 and t2 and t3 and t4 and
           t1.ch == string.byte("O") and t2.ch == string.byte("k") and
           t3.ch == string.byte("a") and t4.ch == string.byte("y") then
            dfhack.timeout(1, 'frames', function()
                gps.mouse_x = x + 1
                gps.mouse_y = y
                gps.precise_mouse_x = x + 1
                gps.precise_mouse_y = y
                local gui = require('gui')
                local screen = dfhack.gui.getCurViewscreen()
                gui.simulateInput(screen, '_MOUSE_L')
            end)
            print("OK: clicked Okay at " .. x .. "," .. y)
            return
        end
    end
end
print("Okay button not found")
