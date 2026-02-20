-- opendwarf--act.lua: Execute a game action via input simulation
-- Usage: opendwarf--act <action_key>
-- Actions: A_MOVE_N, A_MOVE_S, etc., or conversation:<index>

local args = {...}
if #args < 1 then
    print("Usage: opendwarf--act <action>")
    print("  e.g.: opendwarf--act A_MOVE_N")
    print("  e.g.: opendwarf--act conversation:0")
    return
end

local action = args[1]

-- Handle conversation selection
if action:sub(1, 13) == "conversation:" then
    local idx = tonumber(action:sub(14))
    if idx == nil then
        qerror("Invalid conversation index: " .. action)
        return
    end
    -- Select conversation option by navigating to it and pressing SELECT
    local ok, err = pcall(function()
        local adventure_ui = df.global.game.main_interface.adventure
        local choices = adventure_ui.conversation.conv_choice_info
        if idx < 0 or idx >= #choices then
            qerror("Conversation choice index out of range: " .. tostring(idx))
            return
        end
        -- Use option.doRealize() for conversation type selection (phase 1)
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

-- Handle standard input simulation via dfhack.run_command
-- Note: gui.simulateInput does NOT work during RPC suspension for movement
-- Use dfhack.run_command with keypress sending instead
local ok, err = pcall(function()
    -- Use the 'keypress' command if available, otherwise try simulateInput
    -- For now, try simulateInput as a best-effort approach
    local gui = require('gui')
    local screen = dfhack.gui.getCurViewscreen()
    gui.simulateInput(screen, action)
    print("OK: " .. action)
end)

if not ok then
    print("ERROR: " .. tostring(err))
end
