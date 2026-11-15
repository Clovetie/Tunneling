--!nonstrict
--[[
	StateMachine.lua — Hierarchical Finite State Machine (HFSM) with state stack,
	guarded transitions, lifecycle hooks (`OnEnter`, `OnUpdate`, `OnExit`, `OnEvent`),
	and transition audit history.
]]

local StateMachine = {}
StateMachine.__index = StateMachine

function StateMachine.new(initialState, statesTable, context)
	local self = setmetatable({
		_states = statesTable or {},
		_current = nil,
		_stack = {},
		_history = {},
		_listeners = {},
		Context = context or {},
	}, StateMachine)

	if initialState and self._states[initialState] then
		self:Transition(initialState, "init")
	end
	return self
end

function StateMachine:RegisterState(name, def)
	self._states[name] = def or {}
	return self
end

function StateMachine:GetState()
	return self._current
end

function StateMachine:GetHistory()
	return self._history
end

function StateMachine:OnStateChanged(callback)
	table.insert(self._listeners, callback)
	return function()
		for i, fn in ipairs(self._listeners) do
			if fn == callback then
				table.remove(self._listeners, i)
				break
			end
		end
	end
end

function StateMachine:CanTransition(nextState)
	local targetDef = self._states[nextState]
	if not targetDef then
		return false, "unknown_state"
	end
	local curDef = self._current and self._states[self._current] or nil
	if curDef and type(curDef.CanExit) == "function" then
		local ok, reason = curDef.CanExit(self.Context, nextState)
		if not ok then
			return false, reason or "exit_blocked"
		end
	end
	if type(targetDef.CanEnter) == "function" then
		local ok, reason = targetDef.CanEnter(self.Context, self._current)
		if not ok then
			return false, reason or "enter_blocked"
		end
	end
	return true, nil
end

function StateMachine:Transition(nextState, reason)
	if self._current == nextState then
		return true
	end
	local allowed, blockReason = self:CanTransition(nextState)
	if not allowed then
		return false, blockReason
	end

	local prev = self._current
	local prevDef = prev and self._states[prev] or nil
	if prevDef and type(prevDef.OnExit) == "function" then
		prevDef.OnExit(self.Context, nextState, reason)
	end

	self._current = nextState
	table.insert(self._history, {
		from = prev,
		to = nextState,
		reason = reason or "transition",
		at = os.clock(),
	})
	if #self._history > 32 then
		table.remove(self._history, 1)
	end

	local nextDef = self._states[nextState]
	if nextDef and type(nextDef.OnEnter) == "function" then
		nextDef.OnEnter(self.Context, prev, reason)
	end

	for _, fn in ipairs(self._listeners) do
		fn(nextState, prev, reason)
	end
	return true
end

function StateMachine:PushState(interruptState, reason)
	if self._current then
		table.insert(self._stack, self._current)
	end
	return self:Transition(interruptState, reason or "push")
end

function StateMachine:PopState(reason)
	local n = #self._stack
	if n == 0 then
		return false, "empty_stack"
	end
	local restored = self._stack[n]
	table.remove(self._stack, n)
	return self:Transition(restored, reason or "pop")
end

function StateMachine:Update(dt)
	local def = self._current and self._states[self._current] or nil
	if def and type(def.OnUpdate) == "function" then
		local nextState = def.OnUpdate(self.Context, dt)
		if type(nextState) == "string" and nextState ~= self._current then
			self:Transition(nextState, "update")
		end
	end
end

function StateMachine:Dispatch(eventName, payload)
	local def = self._current and self._states[self._current] or nil
	if def and type(def.OnEvent) == "function" then
		local nextState = def.OnEvent(self.Context, eventName, payload)
		if type(nextState) == "string" and nextState ~= self._current then
			return self:Transition(nextState, eventName)
		end
	end
	return false
end

return StateMachine
