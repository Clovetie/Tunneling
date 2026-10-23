-- Maid.lua — Deterministic cleanup container for RBXScriptConnections,
-- Instances, threads, functions, and nested Destroy/Disconnect objects.
local Maid = {}
Maid.__index = Maid

function Maid.new()
	return setmetatable({ _tasks = {} }, Maid)
end

local function cleanTask(item)
	if not item then
		return
	end
	local kind = typeof(item)
	if kind == "RBXScriptConnection" then
		item:Disconnect()
	elseif kind == "Instance" then
		item:Destroy()
	elseif kind == "function" then
		item()
	elseif kind == "thread" then
		pcall(task.cancel, item)
	elseif kind == "table" then
		if type(item.Destroy) == "function" then
			item:Destroy()
		elseif type(item.Disconnect) == "function" then
			item:Disconnect()
		end
	end
end

function Maid:GiveTask(taskObj)
	local id = #self._tasks + 1
	self._tasks[id] = taskObj
	return taskObj
end

function Maid:Set(key, taskObj)
	local old = self._tasks[key]
	if old ~= taskObj then
		self._tasks[key] = taskObj
		cleanTask(old)
	end
	return taskObj
end

function Maid:DoCleaning()
	local tasks = self._tasks
	self._tasks = {}
	for _, item in pairs(tasks) do
		cleanTask(item)
	end
end

Maid.Destroy = Maid.DoCleaning

return Maid
