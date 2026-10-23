-- TagBinder.lua — Clean CollectionService component lifecycle binder.
-- Automatically attaches `Class.new(instance)` to existing and future tagged
-- instances and calls `:Destroy()` when untagged or removed from DataModel.
local CollectionService = game:GetService("CollectionService")

local TagBinder = {}

function TagBinder.bind(tagName, classTable)
	assert(type(tagName) == "string", "tagName must be a string")
	assert(type(classTable) == "table" and type(classTable.new) == "function", "classTable.new required")

	local active = {}
	local connections = {}

	local function attach(inst)
		if active[inst] ~= nil then
			return
		end
		local ok, obj = pcall(classTable.new, inst)
		if ok and obj ~= nil then
			active[inst] = obj
		else
			warn(string.format("[TagBinder:%s] failed on %s: %s", tagName, inst:GetFullName(), tostring(obj)))
		end
	end

	local function detach(inst)
		local obj = active[inst]
		if obj ~= nil then
			active[inst] = nil
			if type(obj) == "table" and type(obj.Destroy) == "function" then
				pcall(obj.Destroy, obj)
			end
		end
	end

	table.insert(connections, CollectionService:GetInstanceAddedSignal(tagName):Connect(attach))
	table.insert(connections, CollectionService:GetInstanceRemovedSignal(tagName):Connect(detach))

	for _, inst in ipairs(CollectionService:GetTagged(tagName)) do
		task.spawn(attach, inst)
	end

	return {
		Get = function(_, inst)
			return active[inst]
		end,
		GetAll = function()
			return active
		end,
		Destroy = function()
			for _, c in ipairs(connections) do
				c:Disconnect()
			end
			table.clear(connections)
			for inst in pairs(active) do
				detach(inst)
			end
		end,
	}
end

return TagBinder
