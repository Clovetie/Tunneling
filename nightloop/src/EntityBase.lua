--!nonstrict
--[[
	NightLoop · EntityBase
	Shared helpers every entity module gets. Entities are plain tables created
	with EntityBase.new(name); the Registry wires them up.

	Entity lifecycle (all optional except Start):
	  entity:Validate(ctx) -> boolean, reason   -- deps present? called once
	  entity:Start(ctx)                          -- night begins
	  entity:OnPhase(phase, ctx)                 -- phase changed
	  entity:Update(dt, ctx)                     -- every heartbeat
	  entity:Stop(ctx)                           -- night ended, clean up
]]

local EntityBase = {}
EntityBase.__index = EntityBase

function EntityBase.new(name)
	return setmetatable({
		Name = name,
		_connections = {},
		_instances = {},
	}, EntityBase)
end

function EntityBase:Log(...)
	print("[NightLoop/" .. self.Name .. "]", ...)
end

function EntityBase:Warn(...)
	warn("[NightLoop/" .. self.Name .. "]", ...)
end

-- Scale a value between its easy and hard bounds by the current intensity.
function EntityBase:ByIntensity(easy, hard, intensity)
	return easy + (hard - easy) * math.clamp(intensity, 0, 1)
end

-- Track a connection so Stop() can clean it up automatically.
function EntityBase:Track(connection)
	table.insert(self._connections, connection)
	return connection
end

-- Track an Instance so Stop() destroys it.
function EntityBase:Own(instance)
	table.insert(self._instances, instance)
	return instance
end

function EntityBase:Cleanup()
	for _, connection in ipairs(self._connections) do
		pcall(function()
			connection:Disconnect()
		end)
	end
	table.clear(self._connections)

	for _, instance in ipairs(self._instances) do
		pcall(function()
			instance:Destroy()
		end)
	end
	table.clear(self._instances)
end

return EntityBase
