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
