-- opendwarf--drainpopups.lua: Drain df.global.world.status.popups.
--
-- These are the centered "mega" world/agreement popups ("You are now in
-- control of X", divination/quest notices) that DF draws OVER
-- dungeonmode/Default with a "More" pager. They are NOT a viewscreen and NOT a
-- main_interface widget (focus stays dungeonmode/Default, every widget
-- open=false), so neither focus checks nor the Okay-button screen-scan
-- (opendwarf--clickok) detect them. They swallow ALL input — movement, fast
-- travel, conversation — and a start-of-adventure cascade queues many at once
-- and regenerates them as world events fire, so the loop drains every tick.
--
-- Acknowledging a popup in-game pops it off the front of the vector; mouse
-- clicks and keys do NOT reliably advance it via simulateInput, so we erase the
-- entries directly (LIVE-VERIFIED v0.53.14: erasing unwedges movement). We
-- erase only (no :delete()) — DF owns these objects and frees them on its own
-- acknowledge path; double-freeing would crash. Returns the deduped text of
-- what was drained so the loop can surface notable notices to the agent.
local json = require("json")
local drained = {}
local seen = {}
local n = 0
pcall(function()
    local pops = df.global.world.status.popups
    n = #pops
    for i = 0, n - 1 do
        local ok, txt = pcall(function() return pops[i].text end)
        local s = ok and tostring(txt) or ""
        if #s > 0 and not seen[s] then
            seen[s] = true
            drained[#drained + 1] = s
        end
    end
    -- erase from the back so indices stay valid
    for i = n - 1, 0, -1 do
        pcall(function() pops:erase(i) end)
    end
end)
print(json.encode({drained = n, texts = drained}))
