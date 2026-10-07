--!nonstrict
-- Minimal signal. No BindableEvents, so tables pass by reference and handlers
-- run synchronously in a known order.

local Signal = {}
Signal.__index = Signal

function Signal.new()
	return setmetatable({ _handlers = {} }, Signal)
end

function Signal:Connect(fn)
	assert(type(fn) == "function", "Signal:Connect expects a function")
	table.insert(self._handlers, fn)
	local connected = true
	return {
		Disconnect = function()
			if not connected then
				return
			end
			connected = false
			local index = table.find(self._handlers, fn)
			if index then
				table.remove(self._handlers, index)
			end
		end,
	}
end

function Signal:Fire(...)
	-- iterate a copy so a handler may disconnect during dispatch
	for _, fn in ipairs(table.clone(self._handlers)) do
		local ok, err = pcall(fn, ...)
		if not ok then
			warn("[NightLoop] signal handler error: " .. tostring(err))
		end
	end
end

function Signal:DisconnectAll()
	table.clear(self._handlers)
end

return Signal
