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

-- Normalize whitespace/case for robust on-screen text matching.
local function conv_norm(s)
    return (s:gsub("%s+", " "):gsub("^%s+", ""):gsub("%s+$", ""):lower())
end

-- Scan the screen for the dialogue-choice label column. DF renders each choice as
-- (lowercase letter) + NUL + (UPPERCASE first char of text) + … at a fixed x column.
-- The NUL separator + uppercase-following filter rejects body-text false positives.
-- Returns a list {x=, y=, txt=} of every label row in the densest such column
-- (the choice list), sorted by y. Empty if none found.
local function read_choice_rows()
    local gps = df.global.gps
    local cols = {}
    for y = 0, gps.dimy - 1 do
        for x = 0, gps.dimx - 3 do
            local ok, t = pcall(dfhack.screen.readTile, x, y, false)
            if ok and t and t.ch >= 97 and t.ch <= 122 then  -- a-z
                local ok2, sep = pcall(dfhack.screen.readTile, x+1, y, false)
                if ok2 and sep and sep.ch == 0 then
                    local ok3, t3 = pcall(dfhack.screen.readTile, x+2, y, false)
                    if ok3 and t3 and t3.ch >= 65 and t3.ch <= 90 then  -- A-Z
                        local txt = ""
                        for k = 2, 70 do
                            local o, cc = pcall(dfhack.screen.readTile, x+k, y, false)
                            if o and cc and cc.ch >= 32 and cc.ch < 127 then txt = txt .. string.char(cc.ch)
                            elseif o and cc and cc.ch == 0 then txt = txt .. " "
                            else break end
                        end
                        cols[x] = cols[x] or {}
                        table.insert(cols[x], {x = x, y = y, txt = conv_norm(txt)})
                    end
                end
            end
        end
    end
    local best_x, best_n = nil, 0
    for x, list in pairs(cols) do if #list > best_n then best_x, best_n = x, #list end end
    if not best_x then return {} end
    table.sort(cols[best_x], function(a, b) return a.y < b.y end)
    return cols[best_x]
end

-- Like read_choice_rows but for the adventure attack menu, whose option text can
-- start lowercase ("a upper body", "b lower body") — the conversation scanner's
-- uppercase-after-NUL filter (a body-text guard) would drop those. We instead
-- require the option text to be a letter/space run of length >= 3, which rejects
-- stray glyphs while accepting both "No Quarter" (mode 0), "Strike" (mode 2) and
-- "upper body" (mode 3). Returns the densest such column, sorted by y.
local function read_attack_rows()
    local gps = df.global.gps
    local cols = {}
    for y = 0, gps.dimy - 1 do
        for x = 0, gps.dimx - 3 do
            local ok, t = pcall(dfhack.screen.readTile, x, y, false)
            if ok and t and t.ch >= 97 and t.ch <= 122 then  -- a-z hotkey letter
                local ok2, sep = pcall(dfhack.screen.readTile, x+1, y, false)
                if ok2 and sep and sep.ch == 0 then
                    local txt, letters = "", 0
                    for k = 2, 60 do
                        local o, cc = pcall(dfhack.screen.readTile, x+k, y, false)
                        if o and cc and cc.ch >= 32 and cc.ch < 127 then
                            txt = txt .. string.char(cc.ch)
                            if (cc.ch >= 65 and cc.ch <= 90) or (cc.ch >= 97 and cc.ch <= 122) then
                                letters = letters + 1
                            end
                        elseif o and cc and cc.ch == 0 then txt = txt .. " "
                        else break end
                    end
                    if letters >= 3 then
                        cols[x] = cols[x] or {}
                        table.insert(cols[x], {x = x, y = y, txt = conv_norm(txt)})
                    end
                end
            end
        end
    end
    local best_x, best_n = nil, 0
    for x, list in pairs(cols) do if #list > best_n then best_x, best_n = x, #list end end
    if not best_x then return {} end
    table.sort(cols[best_x], function(a, b) return a.y < b.y end)
    return cols[best_x]
end

-- Click a choice row (as returned by read_choice_rows) with pixel-precise coords.
local function click_row(row)
    local gps = df.global.gps
    local px = (gps.tile_pixel_x or 8)
    local py = (gps.tile_pixel_y or 12)
    gps.mouse_x = row.x
    gps.mouse_y = row.y
    gps.precise_mouse_x = row.x * px
    gps.precise_mouse_y = row.y * py
    local gui = require('gui')
    gui.simulateInput(dfhack.gui.getCurViewscreen(), '_MOUSE_L')
end

-- Pick the screen row matching target_text. The caller scrolls the target to the
-- top, so an exact/prefix match is expected; we prefer the longest shared prefix.
local function match_choice_row(rows, target_text)
    local want = conv_norm(target_text)
    -- exact match first
    for _, r in ipairs(rows) do if r.txt == want then return r end end
    -- prefix either direction (on-screen text can be truncated by panel width)
    local best, best_len = nil, 0
    for _, r in ipairs(rows) do
        local n = math.min(#r.txt, #want)
        if n >= 6 and r.txt:sub(1, n) == want:sub(1, n) and n > best_len then
            best, best_len = r, n
        end
    end
    return best
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

        -- Phase 2: selecting a dialogue choice (conv_choice_info list).
        -- Only ~12 choices render at once; conv.choice_scroll_position is a
        -- fine-grained (≈3 units per choice) pixel scroll. Setting it to idx*3
        -- reliably scrolls choice[idx] to the TOP visible row (LIVE-VERIFIED).
        -- The screen buffer only reflects the new scroll AFTER a frame renders,
        -- so we MUST defer the on-screen find+click — reading synchronously here
        -- returns the stale pre-scroll layout and clicks the wrong row (the old bug).
        -- We match by the choice's known title text (immune to ±1 pixel-row wobble)
        -- and click with pixel-precise coords (tile * tile_pixel).
        local choices = conv.conv_choice_info
        if idx < 0 or idx >= #choices then
            qerror("conv_choice_info index out of range: " .. tostring(idx) .. " (have " .. tostring(#choices) .. ")")
            return
        end

        local target_text = ""
        for _, d in ipairs(choices[idx].title.text) do target_text = target_text .. d.value end

        conv.choice_scroll_position = idx * 3
        dfhack.timeout(2, 'frames', function()
            local ok2, err2 = pcall(function()
                local gps = df.global.gps
                local rows = read_choice_rows()
                if #rows == 0 then
                    dfhack.printerr("opendwarf--act conversation:" .. idx .. " error: no choice rows on screen")
                    return
                end
                local row = match_choice_row(rows, target_text)
                if not row then
                    -- target was scrolled to the top, so the first row is the fallback
                    row = rows[1]
                end
                local px = (gps.tile_pixel_x or 8)
                local py = (gps.tile_pixel_y or 12)
                gps.mouse_x = row.x
                gps.mouse_y = row.y
                gps.precise_mouse_x = row.x * px
                gps.precise_mouse_y = row.y * py
                local gui = require('gui')
                local screen = dfhack.gui.getCurViewscreen()
                gui.simulateInput(screen, '_MOUSE_L')
            end)
            if not ok2 then
                dfhack.printerr("opendwarf--act conversation:" .. idx .. " click error: " .. tostring(err2))
            end
        end)
        print("OK: conversation:" .. idx .. " target='" .. conv_norm(target_text):sub(1, 40) .. "'")
    end)

    if not ok then
        print("ERROR: " .. tostring(err))
    end
    return
end

-- Attack-menu target pick: attack_pick:<index>
-- The adventure attack menu (dungeonmode/Attack) is mouse-driven — keyboard
-- SELECT/scroll do NOT advance it (LIVE-VERIFIED v0.53.14). Its target list and
-- move list both render as the same clickable "letter + NUL + text" choice rows
-- as conversations, so read_choice_rows finds them. mode 0 lists attack targets
-- ("a No Quarter: The wolf", "b …") in unit_choice order; clicking one confirms
-- the target and advances to mode 2. The screen buffer only reflects the menu a
-- frame after it opens, so we defer the scan+click (same reason as conversation).
if action:sub(1, 12) == "attack_pick:" then
    local idx = tonumber(action:sub(13)) or 0
    dfhack.timeout(2, 'frames', function()
        local ok, err = pcall(function()
            local rows = read_attack_rows()
            if #rows == 0 then
                dfhack.printerr("opendwarf--act attack_pick: no choice rows on screen")
                return
            end
            click_row(rows[idx + 1] or rows[1])
        end)
        if not ok then dfhack.printerr("opendwarf--act attack_pick error: " .. tostring(err)) end
    end)
    print("OK: scheduled attack_pick:" .. tostring(idx))
    return
end

-- Attack-menu strike: attack_strike — click the default "Strike" move (mode 2).
-- Falls back to the first move row if no row's text contains "strike" (e.g. the
-- only options are wrestle/charge against an unarmed target).
if action == "attack_strike" then
    dfhack.timeout(2, 'frames', function()
        local ok, err = pcall(function()
            local rows = read_attack_rows()
            if #rows == 0 then
                dfhack.printerr("opendwarf--act attack_strike: no choice rows on screen")
                return
            end
            local row
            for _, r in ipairs(rows) do
                if r.txt:find("strike", 1, true) then row = r; break end
            end
            click_row(row or rows[1])
        end)
        if not ok then dfhack.printerr("opendwarf--act attack_strike error: " .. tostring(err)) end
    end)
    print("OK: scheduled attack_strike")
    return
end

-- Eat/drink menu driver: eatdrink_pick:<food|drink|any>
-- The A_INV_EATDRINK menu (dungeonmode/Inventory, context=EAT_DRINK) is MOUSE-driven
-- like the attack menu: option_current is a flat vector of
-- adventure_option_eat_drink_itemst, each with an .item whose readable description
-- renders as the on-screen choice rows in the SAME order. We classify each option
-- by item type (drink/water vs food), find the matching on-screen row by description
-- prefix, and click it (LIVE-VERIFIED v0.53.14: one click consumes it, timer -~50000,
-- menu closes to Default). Defers the scan+click (menu renders a frame after open).
if action:sub(1, 13) == "eatdrink_pick" then
    local want = action:sub(15)  -- "food" | "drink" | "any"
    if want == "" then want = "any" end
    -- Item types (LIVE-VERIFIED enum v0.53.14): DRINK=69, LIQUID_MISC=73 (water).
    -- COIN=74 and FLASK=11 are NOT drinks (a flask is an ineffective no-op gulp).
    local FOOD = {[48]=true,[49]=true,[50]=true,[51]=true,[53]=true,[54]=true,
                  [56]=true,[71]=true,[72]=true,[88]=true}
    local DRINK = {[69]=true,[73]=true}
    local function conv_norm(s) return (s:gsub("%s+"," "):gsub("^%s+",""):gsub("%s+$","")) end

    -- Pick the option_current index whose item matches the wanted category.
    -- IMPORTANT (LIVE-VERIFIED v0.53.14): clicking a *filled flask/waterskin* (type
    -- 58) does NOT reduce the thirst timer, but clicking the actual LIQUID_MISC=74
    -- ("water [N]") or a DRINK=69 item DOES (-~50000). So drink picks an EFFECTIVE
    -- liquid/drink item only — never a flask (which would be a no-op gulp).
    local inv = df.global.game.main_interface.adventure.inventory
    local target_desc = nil
    local n = 0
    pcall(function() n = #inv.option_current end)
    for i = 0, n - 1 do
        local o = inv.option_current[i]
        local matched = false
        pcall(function()
            if o.item then
                local tid = o.item:getType()
                local is_food = FOOD[tid] or false
                local is_drink = DRINK[tid] or false  -- LIQUID_MISC / DRINK: effective
                if (want == "drink" and is_drink) or (want == "food" and is_food)
                   or (want == "any" and (is_food or is_drink)) then
                    if not target_desc then
                        target_desc = conv_norm(dfhack.items.getDescription(o.item, 0, true))
                        matched = true
                    end
                end
            end
        end)
        if matched then break end
    end
    if not target_desc then
        print("ERROR: no " .. want .. " option in eat/drink menu")
        return
    end

    dfhack.timeout(2, 'frames', function()
        local ok, err = pcall(function()
            local gps = df.global.gps
            local best, best_n = nil, 0
            for y = 0, gps.dimy - 1 do
                for x = 0, gps.dimx - 3 do
                    local okt, t = pcall(dfhack.screen.readTile, x, y, false)
                    if okt and t and t.ch >= 97 and t.ch <= 122 then
                        local ok2, sep = pcall(dfhack.screen.readTile, x+1, y, false)
                        if ok2 and sep and sep.ch == 0 then
                            local txt = ""
                            for k = 2, 60 do
                                local o, cc = pcall(dfhack.screen.readTile, x+k, y, false)
                                if o and cc and cc.ch >= 32 and cc.ch < 127 then txt = txt .. string.char(cc.ch)
                                elseif o and cc and cc.ch == 0 then txt = txt .. " "
                                else break end
                            end
                            txt = conv_norm(txt)
                            local m = math.min(#txt, #target_desc)
                            if m >= 4 and txt:sub(1,m) == target_desc:sub(1,m) and m > best_n then
                                best, best_n = {x=x, y=y}, m
                            end
                        end
                    end
                end
            end
            if best then
                click_row(best)
            else
                dfhack.printerr("opendwarf--act eatdrink_pick error: no row matched '"
                                .. target_desc .. "'")
            end
        end)
        if not ok then dfhack.printerr("opendwarf--act eatdrink_pick error: " .. tostring(err)) end
    end)
    print("OK: scheduled eatdrink_pick:" .. want .. " target='" .. target_desc:sub(1,40) .. "'")
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

-- Dismiss DFHack Lua UI screens sitting above viewscreen_dungeonmodest.
-- ONLY dismisses screens whose type name contains "dfhack" (case-insensitive)
-- or "lua" — never dismisses DF's own game screens (viewscreen_dungeonmodest,
-- viewscreen_adventure_logst, etc.). Returns OK:<count> dismissed.
if action == "dismiss_dfhack_screens" then
    local dismissed = 0
    local max_iter = 10
    for _ = 1, max_iter do
        local ok, cur = pcall(function() return dfhack.gui.getCurViewscreen() end)
        if not ok or cur == nil then break end
        local type_name = ""
        local ok2, tn = pcall(function() return cur._type.name end)
        if ok2 and tn then type_name = tn:lower() end
        -- Only dismiss DFHack's own screens (type names containing "dfhack" or
        -- screens that are lua-based script UIs whose parent is dungeonmodest).
        -- viewscreen_dungeonmodest is the floor — stop there.
        if type_name == "viewscreen_dungeonmodest" then break end
        if type_name:find("dfhack", 1, true) or type_name:find("lua", 1, true) then
            local ok3, err3 = pcall(function()
                dfhack.screen.dismiss(cur)
            end)
            if ok3 then
                dismissed = dismissed + 1
            else
                print("WARN: could not dismiss " .. type_name .. ": " .. tostring(err3))
                break
            end
        else
            -- Non-DFHack, non-dungeonmodest screen — stop; do not dismiss DF screens.
            break
        end
    end
    print("OK:" .. tostring(dismissed) .. " dfhack screens dismissed")
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

-- Handle fast travel exit. LIVE-VERIFIED 2026-06-11: A_END_TRAVEL is the
-- keyboard exit for the travel screen (the old x-button click scan was
-- unreliable — the button is a texture, invisible to readTile, when travel
-- is blocked by obstacles).
if action == "travel_exit" then
    dfhack.timeout(1, 'frames', function()
        local ok, err = pcall(function()
            local gui = require('gui')
            local screen = dfhack.gui.getCurViewscreen()
            gui.simulateInput(screen, 'A_END_TRAVEL')
        end)
        if not ok then
            dfhack.printerr("opendwarf--act travel_exit error: " .. tostring(err))
        end
    end)
    print("OK: scheduled travel_exit")
    return
end

-- Raw key injection for the L3 escape hatch: press:<INTERFACE_KEY>
-- Allows the LLM to press any validated key on unmodeled screens.
if action:sub(1, 6) == "press:" then
    local key = action:sub(7)
    if #key == 0 then
        qerror("press: missing key name")
        return
    end
    dfhack.timeout(1, 'frames', function()
        local ok, err = pcall(function()
            local gui = require('gui')
            local screen = dfhack.gui.getCurViewscreen()
            gui.simulateInput(screen, key)
        end)
        if not ok then
            dfhack.printerr("opendwarf--act press error: " .. tostring(err))
        end
    end)
    print("OK: scheduled press:" .. key)
    return
end

-- Fallthrough: the action must be a valid interface key name. Reject unknown
-- names loudly — gui.simulateInput silently ignores them inside the deferred
-- pcall, which makes typos (e.g. "key:LEAVESCREEN") look like dead keyboards.
if df.interface_key[action] == nil then
    -- print, don't qerror: script errors can hang the RPC reply (see CLAUDE.md)
    print("ERROR: unknown interface key: " .. action)
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
