--!nonstrict
--[[
	NightLoop · Breathless
	Makes one room at a time suffocating. Phase 4+.

	NOT IMPLEMENTED YET. Disabled in Config, so the Registry never requires it
	and it costs nothing. Fill in the hooks and flip Enabled = true.
]]

local EntityBase = require(script.Parent.Parent.EntityBase)

local Breathless = EntityBase.new("Breathless")

function Breathless:Validate(ctx)
	return false, "not implemented yet"
end

function Breathless:Start(ctx)
end

return Breathless
