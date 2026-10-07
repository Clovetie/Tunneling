--!nonstrict
--[[
	NightLoop · TickingMan
	Clocks tick violently; freezes player time briefly. Phase 5.

	NOT IMPLEMENTED YET. Disabled in Config, so the Registry never requires it
	and it costs nothing. Fill in the hooks and flip Enabled = true.
]]

local EntityBase = require(script.Parent.Parent.EntityBase)

local TickingMan = EntityBase.new("TickingMan")

function TickingMan:Validate(ctx)
	return false, "not implemented yet"
end

function TickingMan:Start(ctx)
end

return TickingMan
