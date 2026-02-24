-- Check unit id vs hist_fig_id for nearby units
local adv = dfhack.world.getAdventurer()
if not adv then print("No adventurer"); return end
local ax, ay, az = dfhack.units.getPosition(adv)
local units = dfhack.units.getUnitsInBox(ax-20, ay-20, az-2, ax+20, ay+20, az+2)
local total = 0
local has_histfig = 0
local no_histfig = 0
for _, u in ipairs(units) do
    if dfhack.units.isActive(u) then
        total = total + 1
        if u.hist_figure_id and u.hist_figure_id >= 0 then
            has_histfig = has_histfig + 1
            print("HAS histfig: unit.id=" .. u.id .. " hist_fig_id=" .. u.hist_figure_id .. " name=" .. dfhack.units.getReadableName(u))
        else
            no_histfig = no_histfig + 1
            print("NO histfig:  unit.id=" .. u.id .. " name=" .. dfhack.units.getReadableName(u))
        end
    end
end
print("Total active: " .. total .. " | has hist_fig_id: " .. has_histfig .. " | no hist_fig_id: " .. no_histfig)
