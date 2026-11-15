--!nonstrict
--[[
	QuestTracker.lua — Multi-objective quest & task machine progression engine.
	Supports per-player quest journals, objective counters, completion checks,
	and reward claiming without hard engine coupling.
]]

local QuestTracker = {}
QuestTracker.__index = QuestTracker

function QuestTracker.new()
	return setmetatable({
		_defs = {},
		_journals = {},
		_listeners = {},
	}, QuestTracker)
end

function QuestTracker:DefineQuest(questId, spec)
	local objectives = {}
	for i, obj in ipairs(spec.objectives or {}) do
		table.insert(objectives, {
			id = obj.id or ("obj_" .. tostring(i)),
			description = obj.description or obj.id or "Complete objective",
			target = math.max(1, tonumber(obj.target) or 1),
			kind = obj.kind or "generic",
		})
	end
	self._defs[questId] = {
		id = questId,
		title = spec.title or questId,
		description = spec.description or "",
		objectives = objectives,
		rewards = spec.rewards or {},
	}
	return self
end

function QuestTracker:OnProgress(callback)
	table.insert(self._listeners, callback)
end

function QuestTracker:_emit(playerKey, questId, eventType, snapshot)
	for _, fn in ipairs(self._listeners) do
		fn(playerKey, questId, eventType, snapshot)
	end
end

function QuestTracker:_journal(playerKey)
	local key = tostring(playerKey or "default")
	local j = self._journals[key]
	if not j then
		j = {}
		self._journals[key] = j
	end
	return j
end

function QuestTracker:StartQuest(playerKey, questId)
	local def = self._defs[questId]
	if not def then
		return false, "unknown_quest"
	end
	local journal = self:_journal(playerKey)
	if journal[questId] then
		return true, "already_started"
	end
	local progress = {}
	for _, obj in ipairs(def.objectives) do
		progress[obj.id] = 0
	end
	journal[questId] = {
		id = questId,
		status = "active",
		progress = progress,
		startedAt = os.clock(),
		claimed = false,
	}
	local snap = self:GetQuestState(playerKey, questId)
	self:_emit(playerKey, questId, "started", snap)
	return true, snap
end

function QuestTracker:IncrementObjective(playerKey, questId, objectiveId, amount)
	local def = self._defs[questId]
	local journal = self:_journal(playerKey)
	local state = journal[questId]
	if not def or not state or state.status == "claimed" then
		return false, "not_active"
	end
	local delta = tonumber(amount) or 1
	local allDone = true
	for _, obj in ipairs(def.objectives) do
		if obj.id == objectiveId then
			local cur = state.progress[obj.id] or 0
			state.progress[obj.id] = math.clamp(cur + delta, 0, obj.target)
		end
		if (state.progress[obj.id] or 0) < obj.target then
			allDone = false
		end
	end
	if allDone and state.status == "active" then
		state.status = "completed"
		state.completedAt = os.clock()
	end
	local snap = self:GetQuestState(playerKey, questId)
	self:_emit(playerKey, questId, state.status == "completed" and "completed" or "progress", snap)
	return true, snap
end

function QuestTracker:GetQuestState(playerKey, questId)
	local def = self._defs[questId]
	local state = self:_journal(playerKey)[questId]
	if not def or not state then
		return nil
	end
	local objOut = {}
	for _, obj in ipairs(def.objectives) do
		local cur = state.progress[obj.id] or 0
		table.insert(objOut, {
			id = obj.id,
			description = obj.description,
			current = cur,
			target = obj.target,
			done = cur >= obj.target,
		})
	end
	return {
		id = questId,
		title = def.title,
		status = state.status,
		claimed = state.claimed,
		objectives = objOut,
		rewards = def.rewards,
	}
end

function QuestTracker:ClaimRewards(playerKey, questId)
	local def = self._defs[questId]
	local state = self:_journal(playerKey)[questId]
	if not def or not state then
		return false, "not_started"
	end
	if state.status ~= "completed" then
		return false, "incomplete"
	end
	if state.claimed then
		return false, "already_claimed"
	end
	state.claimed = true
	state.status = "claimed"
	local snap = self:GetQuestState(playerKey, questId)
	self:_emit(playerKey, questId, "claimed", snap)
	return true, def.rewards
end

return QuestTracker
