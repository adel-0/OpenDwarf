-- opendwarf--act.lua: Execute a game action via deferred input simulation
-- Usage: opendwarf--act <action_key>
-- Actions: A_MOVE_N, A_MOVE_S, etc., or conversation:<index>
--
-- Key insight: gui.simulateInput() does NOT work during RPC suspension because
-- DFHack holds the core lock while processing RPC commands. We use
-- dfhack.timeout() to schedule input for after the RPC call returns.
--
-- Conversation insight: conversation choices require MOUSE CLICKS.
-- Keyboard SELECT/CURSOR keys do not work in the native DF v53 conversation UI.

local args = {...}
if #args < 1 then
    print("Usage: opendwarf--act <action>")
    print("  e.g.: opendwarf--act A_MOVE_N")
    print("  e.g.: opendwarf--act conversation:0")
    return
end

local action = args[1]

-- Find the screen column and first-choice row for the conversation choice list.
-- Returns x, y of the first 'a' choice row, or nil if not found.
local function find_choice_a_pos()
    local gps = df.global.gps
    -- Choices are in the right panel. Scan x from 30 to 80.
    for y = 0, gps.dimy - 1 do
        for x = 30, 80 do
            local ok, tile = pcall(dfhack.screen.readTile, x, y, false)
            if ok and tile and tile.ch == string.byte('a') then
                local ok2, next = pcall(dfhack.screen.readTile, x+1, y, false)
                if ok2 and next and next.ch == string.byte(' ') then
                    local ok3, t3 = pcall(dfhack.screen.readTile, x+2, y, false)
                    if ok3 and t3 and t3.ch >= string.byte('A') and t3.ch <= string.byte('Z') then
                        return x, y
                    end
                end
            end
        end
    end
    return nil, nil
end

-- Handle conversation selection
if action:sub(1, 13) == "conversation:" then
    local idx = tonumber(action:sub(14))
    if idx == nil then
        qerror("Invalid conversation index: " .. action)
        return
    end

    local ok, err = pcall(function()
        local adventure_ui = df.global.game.main_interface.adventure
        local conv = adventure_ui.conversation

        -- Phase 1: selecting who to talk to (select_option list)
        if conv.selecting_conversation then
            local opts = conv.select_option
            if idx < 0 or idx >= #opts then
                qerror("select_option index out of range: " .. tostring(idx) .. " (have " .. tostring(#opts) .. ")")
                return
            end
            local opt = opts[idx]
            if opt and opt.doRealize then
                opt:doRealize()
                print("OK: realized select_option " .. tostring(idx))
            else
                qerror("select_option[" .. tostring(idx) .. "] has no doRealize")
            end
            return
        end

        -- Phase 2: selecting dialogue choice (conv_choice_info list)
        -- Strategy: set choice_scroll_position = idx so that choice[idx] becomes the 'a' item,
        -- then wait 2 frames for re-render, then mouse-click the 'a' position on screen.
        local choices = conv.conv_choice_info
        if idx < 0 or idx >= #choices then
            qerror("conv_choice_info index out of range: " .. tostring(idx) .. " (have " .. tostring(#choices) .. ")")
            return
        end

        conv.choice_scroll_position = idx
        dfhack.timeout(2, 'frames', function()
            local ok2, err2 = pcall(function()
                -- Find where 'a' choice appears on screen after scroll
                local cx, cy = find_choice_a_pos()
                if not cx then
                    dfhack.printerr("opendwarf--act: could not find choice 'a' on screen for idx=" .. tostring(idx))
                    return
                end
                df.global.gps.mouse_x = cx
                df.global.gps.mouse_y = cy
                df.global.gps.precise_mouse_x = cx
                df.global.gps.precise_mouse_y = cy
                local gui = require('gui')
                local screen = dfhack.gui.getCurViewscreen()
                gui.simulateInput(screen, '_MOUSE_L')
            end)
            if not ok2 then
                dfhack.printerr("opendwarf--act conversation click error: " .. tostring(err2))
            end
        end)
        print("OK: scrolled to " .. tostring(idx) .. ", click queued")
    end)

    if not ok then
        print("ERROR: " .. tostring(err))
    end
    return
end

-- Helper: open a letter-based selection menu then navigate to index N and SELECT.
-- open_key: DFHack interface key to open the menu (e.g. 'A_PICKUP', 'A_DROP')
-- idx: 0-based item index (cursor starts at 0, we move down idx times)
local function open_and_select(open_key, idx)
    -- Step 1: open menu (deferred 1 frame after RPC lock releases)
    dfhack.timeout(1, 'frames', function()
        local ok, err = pcall(function()
            local gui = require('gui')
            local screen = dfhack.gui.getCurViewscreen()
            gui.simulateInput(screen, open_key)
        end)
        if not ok then
            dfhack.printerr("opendwarf--act open_menu error: " .. tostring(err))
            return
        end
        -- Step 2: navigate to item idx then confirm (deferred 3 frames for menu to render)
        dfhack.timeout(3, 'frames', function()
            local ok2, err2 = pcall(function()
                local gui = require('gui')
                local screen = dfhack.gui.getCurViewscreen()
                for _ = 1, idx do
                    gui.simulateInput(screen, 'CURSOR_DOWN')
                end
                gui.simulateInput(screen, 'SELECT')
            end)
            if not ok2 then
                dfhack.printerr("opendwarf--act select_item error: " .. tostring(err2))
            end
        end)
    end)
end

-- Item pickup: pickup:<index>  (A_GROUND = pick up from floor)
if action:sub(1, 7) == "pickup:" then
    local idx = tonumber(action:sub(8)) or 0
    open_and_select('A_GROUND', idx)
    print("OK: scheduled pickup:" .. tostring(idx))
    return
end

-- Item drop: drop:<index>  (A_INV_DROP = drop from inventory)
if action:sub(1, 5) == "drop:" then
    local idx = tonumber(action:sub(6)) or 0
    open_and_select('A_INV_DROP', idx)
    print("OK: scheduled drop:" .. tostring(idx))
    return
end

-- Wield item from inventory: wield:<index>  (A_INV_DRAW_WEAPON = draw/wield weapon)
if action:sub(1, 6) == "wield:" then
    local idx = tonumber(action:sub(7)) or 0
    open_and_select('A_INV_DRAW_WEAPON', idx)
    print("OK: scheduled wield:" .. tostring(idx))
    return
end

-- Eat or drink from inventory: eatdrink:<index>  (A_INV_EATDRINK = eat/drink menu)
if action:sub(1, 9) == "eatdrink:" then
    local idx = tonumber(action:sub(10)) or 0
    open_and_select('A_INV_EATDRINK', idx)
    print("OK: scheduled eatdrink:" .. tostring(idx))
    return
end

-- Wear armor from inventory: wear:<index>  (A_INV_WEAR)
if action:sub(1, 5) == "wear:" then
    local idx = tonumber(action:sub(6)) or 0
    open_and_select('A_INV_WEAR', idx)
    print("OK: scheduled wear:" .. tostring(idx))
    return
end

-- Remove armor from inventory: remove:<index>  (A_INV_REMOVE)
if action:sub(1, 7) == "remove:" then
    local idx = tonumber(action:sub(8)) or 0
    open_and_select('A_INV_REMOVE', idx)
    print("OK: scheduled remove:" .. tostring(idx))
    return
end

-- Handle fast travel enter
if action == "travel_enter" then
    dfhack.timeout(1, 'frames', function()
        local ok, err = pcall(function()
            local gui = require('gui')
            local screen = dfhack.gui.getCurViewscreen()
            gui.simulateInput(screen, 'A_TRAVEL')
        end)
        if not ok then
            dfhack.printerr("opendwarf--act travel_enter error: " .. tostring(err))
        end
        -- After entering travel, auto-dismiss help dialog if it appears
        -- Use longer delay (10 frames) as the dialog may take time to render
        dfhack.timeout(10, 'frames', function()
            pcall(function()
                local focus = dfhack.gui.getCurFocus()
                if focus and #focus > 0 and focus[1]:find("Help") then
                    -- Find and click the "Okay" button
                    local gps = df.global.gps
                    for y = 0, gps.dimy - 1 do
                        for x = 0, gps.dimx - 5 do
                            local ok1, t1 = pcall(dfhack.screen.readTile, x, y, false)
                            local ok2, t2 = pcall(dfhack.screen.readTile, x+1, y, false)
                            local ok3, t3 = pcall(dfhack.screen.readTile, x+2, y, false)
                            local ok4, t4 = pcall(dfhack.screen.readTile, x+3, y, false)
                            if ok1 and ok2 and ok3 and ok4 and t1 and t2 and t3 and t4 and
                               t1.ch == string.byte('O') and t2.ch == string.byte('k') and
                               t3.ch == string.byte('a') and t4.ch == string.byte('y') then
                                dfhack.timeout(1, 'frames', function()
                                    gps.mouse_x = x + 1
                                    gps.mouse_y = y
                                    gps.precise_mouse_x = x + 1
                                    gps.precise_mouse_y = y
                                    local gui2 = require('gui')
                                    local screen2 = dfhack.gui.getCurViewscreen()
                                    gui2.simulateInput(screen2, '_MOUSE_L')
                                end)
                                return
                            end
                        end
                    end
                end
            end)
        end)
    end)
    print("OK: scheduled travel_enter")
    return
end

-- Handle fast travel exit (click the 'x' stop-travel button)
if action == "travel_exit" then
    dfhack.timeout(1, 'frames', function()
        local ok, err = pcall(function()
            local gps = df.global.gps
            local gui = require('gui')
            -- Scan bottom rows for standalone 'x' button
            for y = gps.dimy - 5, gps.dimy - 1 do
                for x = 0, gps.dimx - 1 do
                    local ok_t, t = pcall(dfhack.screen.readTile, x, y, false)
                    if ok_t and t and t.ch == string.byte('x') then
                        local ok_prev, prev = pcall(dfhack.screen.readTile, x-1, y, false)
                        -- Check it's a standalone 'x' (space before it)
                        if ok_prev and prev and (prev.ch == 32 or prev.ch < 32) then
                            gps.mouse_x = x
                            gps.mouse_y = y
                            gps.precise_mouse_x = x
                            gps.precise_mouse_y = y
                            local screen = dfhack.gui.getCurViewscreen()
                            gui.simulateInput(screen, '_MOUSE_L')
                            return
                        end
                    end
                end
            end
            dfhack.printerr("opendwarf--act: could not find 'x' stop-travel button")
        end)
        if not ok then
            dfhack.printerr("opendwarf--act travel_exit error: " .. tostring(err))
        end
    end)
    print("OK: scheduled travel_exit")
    return
end

-- Defer input simulation using dfhack.timeout so it fires AFTER the RPC lock releases.
-- 1 tick delay is enough — the callback runs on the next DF frame when the core is unlocked.
dfhack.timeout(1, 'frames', function()
    local ok, err = pcall(function()
        local gui = require('gui')
        local screen = dfhack.gui.getCurViewscreen()
        gui.simulateInput(screen, action)
    end)
    if not ok then
        dfhack.printerr("opendwarf--act deferred error: " .. tostring(err))
    end
end)

print("OK: scheduled " .. action)
