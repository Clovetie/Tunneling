-- StateStore.lua — Lightweight observable key-value state store for server
-- session data or client UI state subscriptions.
local StateStore = {}
StateStore.__index = StateStore

function StateStore.new(initialState)
	return setmetatable({
		_data = table.clone(initialState or {}),
		_subs = {},
	}, StateStore)
end

function StateStore:Get(key)
	return self._data[key]
end

function StateStore:GetSnapshot()
	return table.clone(self._data)
end

function StateStore:Set(key, newValue)
	local prev = self._data[key]
	if prev == newValue then
		return
	end
	self._data[key] = newValue
	local list = self._subs[key]
	if list then
		for _, fn in ipairs(table.clone(list)) do
			task.spawn(fn, newValue, prev)
		end
	end
end

function StateStore:Increment(key, delta)
	local cur = tonumber(self._data[key]) or 0
	local nextVal = cur + (delta or 1)
	self:Set(key, nextVal)
	return nextVal
end

function StateStore:Observe(key, callback)
	if not self._subs[key] then
		self._subs[key] = {}
	end
	table.insert(self._subs[key], callback)
	task.spawn(callback, self._data[key], nil)
	return function()
		local list = self._subs[key]
		if not list then
			return
		end
		for i = #list, 1, -1 do
			if list[i] == callback then
				table.remove(list, i)
				break
			end
		end
	end
end

return StateStore
