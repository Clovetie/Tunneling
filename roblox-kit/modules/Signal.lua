-- Signal.lua — Lightweight, zero-yield pure-Luau signal class.
local Connection = {}
Connection.__index = Connection

function Connection.new(signal, fn)
	return setmetatable({
		Connected = true,
		_signal = signal,
		_fn = fn,
	}, Connection)
end

function Connection:Disconnect()
	if not self.Connected then
		return
	end
	self.Connected = false
	local list = self._signal._listeners
	for i = #list, 1, -1 do
		if list[i] == self then
			table.remove(list, i)
			break
		end
	end
end

local Signal = {}
Signal.__index = Signal

function Signal.new()
	return setmetatable({ _listeners = {} }, Signal)
end

function Signal:Connect(fn)
	assert(type(fn) == "function", "Signal:Connect expects function")
	local conn = Connection.new(self, fn)
	table.insert(self._listeners, conn)
	return conn
end

function Signal:Once(fn)
	local conn
	conn = self:Connect(function(...)
		conn:Disconnect()
		fn(...)
	end)
	return conn
end

function Signal:Wait()
	local running = coroutine.running()
	self:Once(function(...)
		task.spawn(running, ...)
	end)
	return coroutine.yield()
end

function Signal:Fire(...)
	local snapshot = table.clone(self._listeners)
	for _, conn in ipairs(snapshot) do
		if conn.Connected then
			task.spawn(conn._fn, ...)
		end
	end
end

function Signal:DisconnectAll()
	for _, conn in ipairs(self._listeners) do
		conn.Connected = false
	end
	table.clear(self._listeners)
end

Signal.Destroy = Signal.DisconnectAll

return Signal
