-- opendwarf--act.lua: Execute a game action via deferred input simulation
-- Usage: opendwarf--act <action_key>
-- Actions: A_MOVE_N, A_MOVE_S, etc., or conversation:<index>
--
-- Key insight: gui.simulateInput() does NOT work during RPC suspension because
-- DFHack holds the core lock while processing RPC commands. We use
-- dfhack.timeout() to schedule input for after the RPC call returns.

local args = {...}
if #args < 1 then
    print("Usage: opendwarf--act <action>")
    print("  e.g.: opendwarf--act A_MOVE_N")
    print("  e.g.: opendwarf--act conversation:0")
    return
end

local action = args[1]

-- Handle conversation selection (doRealize works during RPC — it's direct state mutation)
if action:sub(1, 13) == "conversation:" then
    local idx = tonumber(action:sub(14))
    if idx == nil then
        qerror("Invalid conversation index: " .. action)
        return
    end
    local ok, err = pcall(function()
        local adventure_ui = df.global.game.main_interface.adventure
        local choices = adventure_ui.conversation.conv_choice_info
        if idx < 0 or idx >= #choices then
            qerror("Conversation choice index out of range: " .. tostring(idx))
            return
        end
        local choice = choices[idx]
        if choice.option and choice.option.doRealize then
            choice.option:doRealize()
            print("OK: realized conversation option " .. tostring(idx))
        else
            qerror("Cannot realize conversation option " .. tostring(idx))
        end
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
