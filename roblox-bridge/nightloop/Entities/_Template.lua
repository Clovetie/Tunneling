--!nonstrict
--[[
	NightLoop · entity template

	Copy this file, rename it, and add a matching block to Config.Entities.
	Modules whose name starts with "_" are ignored by the Registry.

	Every hook except Start is optional.
]]

local EntityBase = require(script.Parent.Parent.EntityBase)

local Entity = EntityBase.new("Template")

-- Return false plus a reason if the place is missing something you need.
-- The night still runs; your entity is just skipped with a clear warning.
function Entity:Validate(ctx)
	return true
end

-- Night has begun. self.Settings is your Config.Entities block.
function Entity:Start(ctx)
	self:Log("started")
end

-- Phase changed. ctx.Intensity is 0..1 (higher in Endless mode).
function Entity:OnPhase(phase, ctx)
end

-- Every heartbeat. Keep it cheap; gate heavy work behind your own timer.
function Entity:Update(dt, ctx)
	-- local value = self:ByIntensity(easyValue, hardValue, ctx.Intensity)
end

-- Night over. Connections made with self:Track() and instances passed to
-- self:Own() are cleaned up for you.
function Entity:Stop(ctx)
end

return Entity
