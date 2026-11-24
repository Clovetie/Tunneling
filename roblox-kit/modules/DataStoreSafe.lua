-- DataStoreSafe.lua — Session-cached player profile manager with automatic
-- in-memory Studio fallback when API Services are disabled, retries, auto-save,
-- leaderstats syncing, and BindToClose shutdown flush.
local DataStoreService = game:GetService("DataStoreService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local DataStoreSafe = {}

function DataStoreSafe.createProfileStore(storeName, defaultProfile, leaderstatKeys)
	local store = nil
	local cloudAvailable = true
	local okStore, storeOrErr = pcall(function()
		return DataStoreService:GetDataStore(storeName)
	end)
	if okStore then
		store = storeOrErr
	else
		cloudAvailable = false
		warn("[DataStoreSafe] Running in local memory fallback mode: " .. tostring(storeOrErr))
	end

	local profiles = {}
	local memoryFallback = {}

	local function reconcile(target, template)
		for k, v in pairs(template) do
			if target[k] == nil then
				target[k] = type(v) == "table" and table.clone(v) or v
			elseif type(v) == "table" and type(target[k]) == "table" then
				reconcile(target[k], v)
			end
		end
		return target
	end

	local function syncLeaderstats(player, data)
		if not leaderstatKeys then
			return
		end
		local ls = player:FindFirstChild("leaderstats")
		if not ls then
			ls = Instance.new("Folder")
			ls.Name = "leaderstats"
			ls.Parent = player
		end
		for _, spec in ipairs(leaderstatKeys) do
			local key = spec.key
			local label = spec.name or key
			local valObj = ls:FindFirstChild(label)
			if not valObj then
				valObj = Instance.new("IntValue")
				valObj.Name = label
				valObj.Parent = ls
			end
			valObj.Value = math.floor(tonumber(data[key]) or 0)
		end
	end

	local function loadPlayer(player)
		local key = "Player_" .. tostring(player.UserId)
		local loaded = nil
		if cloudAvailable and store then
			for attempt = 1, 3 do
				local ok, res = pcall(function()
					return store:GetAsync(key)
				end)
				if ok then
					loaded = res
					break
				end
				if RunService:IsStudio() then
					cloudAvailable = false
					loaded = memoryFallback[key]
					break
				end
				task.wait(0.5 * attempt)
			end
		else
			loaded = memoryFallback[key]
		end

		local data = type(loaded) == "table" and loaded or {}
		reconcile(data, defaultProfile)
		profiles[player] = data
		syncLeaderstats(player, data)
		return data
	end

	local function savePlayer(player)
		local data = profiles[player]
		if not data then
			return false
		end
		local key = "Player_" .. tostring(player.UserId)
		memoryFallback[key] = table.clone(data)
		if cloudAvailable and store then
			for attempt = 1, 3 do
				local ok = pcall(function()
					store:SetAsync(key, data)
				end)
				if ok then
					return true
				end
				task.wait(0.5 * attempt)
			end
		end
		return true
	end

	Players.PlayerAdded:Connect(loadPlayer)
	Players.PlayerRemoving:Connect(function(player)
		savePlayer(player)
		profiles[player] = nil
	end)
	for _, p in ipairs(Players:GetPlayers()) do
		task.spawn(loadPlayer, p)
	end

	game:BindToClose(function()
		for player in pairs(profiles) do
			savePlayer(player)
		end
	end)

	return {
		Get = function(_, player)
			return profiles[player]
		end,
		Update = function(_, player, mutator)
			local data = profiles[player]
			if not data then
				return nil
			end
			mutator(data)
			syncLeaderstats(player, data)
			return data
		end,
		Save = function(_, player)
			return savePlayer(player)
		end,
	}
end

-- Ordered-store half: a global leaderboard. OrderedDataStore values must be integers and
-- display metadata does not belong in them, so this keeps scores in the ordered store and
-- names in a plain companion store, throttles writes per key, and degrades to memory with a
-- single warning when the DataStore is unavailable (Studio with API access off) instead of
-- erroring on every call.
function DataStoreSafe.createOrderedStore(storeName, options)
	options = type(options) == "table" and options or {}
	local minWriteInterval = tonumber(options.minWriteInterval) or 6
	local nameStoreName = options.nameStore or (tostring(storeName) .. "_Names")
	local nameTtl = tonumber(options.nameTtl) or 300
	local maxTop = 100

	local ordered, names = nil, nil
	local cloudAvailable = true
	local cloudReason = nil
	local warned = false

	local function disable(reason)
		if cloudAvailable then
			cloudAvailable = false
			cloudReason = reason
		end
		if not warned then
			warned = true
			warn(("[DataStoreSafe] Ordered store %q is in memory-fallback mode: %s"):format(tostring(storeName), tostring(reason)))
		end
	end

	local okOrdered, orderedOrErr = pcall(function()
		return DataStoreService:GetOrderedDataStore(storeName)
	end)
	local okNames, namesOrErr = pcall(function()
		return DataStoreService:GetDataStore(nameStoreName)
	end)
	if okOrdered and okNames then
		ordered, names = orderedOrErr, namesOrErr
	else
		disable(orderedOrErr or namesOrErr)
	end

	local memoryScores = {}
	local memoryNames = {}
	local pending = {}
	local lastWrite = {}
	local writtenNames = {}
	local nameCache = {}

	local function keyOf(userId)
		return "Player_" .. tostring(userId)
	end

	local function idOf(key)
		return tonumber(tostring(key):match("^Player_(%-?%d+)$"))
	end

	-- Getting a store object succeeds even with API access off; the first call is what
	-- fails, so a failed call downgrades the whole store rather than retrying forever.
	local function attempt(fn)
		if not cloudAvailable then
			return false, cloudReason
		end
		for try = 1, 3 do
			local ok, result = pcall(fn)
			if ok then
				return true, result
			end
			if RunService:IsStudio() then
				disable(result)
				return false, result
			end
			if try == 3 then
				return false, result
			end
			task.wait(0.5 * try)
		end
		return false, "exhausted"
	end

	local function writeNow(key, score, name)
		local wroteScore = true
		if score ~= nil then
			wroteScore = attempt(function()
				ordered:SetAsync(key, score)
			end)
			if wroteScore then
				lastWrite[key] = os.clock()
				pending[key] = nil
			end
		end
		-- Names change rarely; rewriting one on every score write would double the calls.
		if name ~= nil and name ~= writtenNames[key] then
			local wroteName = attempt(function()
				names:SetAsync(key, name)
			end)
			if wroteName then
				writtenNames[key] = name
			end
		end
		return wroteScore
	end

	local function topFromMemory(limit)
		local rows = {}
		for key, score in pairs(memoryScores) do
			local userId = idOf(key)
			if userId then
				table.insert(rows, { userId = userId, score = score, name = memoryNames[key] })
			end
		end
		table.sort(rows, function(a, b)
			if a.score ~= b.score then
				return a.score > b.score
			end
			return a.userId < b.userId
		end)
		local count = math.clamp(math.floor(limit or maxTop), 1, maxTop)
		local out = {}
		for index = 1, math.min(count, #rows) do
			out[index] = rows[index]
		end
		return out
	end

	local store = {}

	function store.Available()
		return cloudAvailable, cloudReason
	end

	-- Records a score, writing it now when it is due (or forced) and queueing it otherwise.
	-- Returns false only for input that cannot be stored.
	function store.Set(userId, score, name, force)
		if type(userId) ~= "number" or userId ~= userId or userId < 0 then
			return false
		end
		if type(score) ~= "number" or score ~= score or score == math.huge or score == -math.huge then
			return false
		end
		local key = keyOf(math.floor(userId))
		local value = math.floor(score)
		memoryScores[key] = value
		if type(name) == "string" and name ~= "" then
			memoryNames[key] = name
		end
		if not cloudAvailable then
			return true
		end
		pending[key] = { score = value, name = memoryNames[key] }
		local last = lastWrite[key]
		local now = os.clock()
		if force or last == nil or (now - last) >= minWriteInterval then
			writeNow(key, value, memoryNames[key])
		end
		return true
	end

	function store.Remove(userId)
		if type(userId) ~= "number" or userId ~= userId or userId < 0 then
			return false
		end
		local key = keyOf(math.floor(userId))
		memoryScores[key] = nil
		memoryNames[key] = nil
		pending[key] = nil
		lastWrite[key] = nil
		writtenNames[key] = nil
		nameCache[key] = nil
		attempt(function()
			ordered:RemoveAsync(key)
		end)
		attempt(function()
			names:RemoveAsync(key)
		end)
		return true
	end

	function store.GetTop(limit)
		local count = math.clamp(math.floor(limit or maxTop), 1, maxTop)
		if not cloudAvailable then
			return topFromMemory(count), "datastore_unavailable"
		end
		local ok, entries = attempt(function()
			return ordered:GetSortedAsync(false, count):GetCurrentPage()
		end)
		if not ok then
			return topFromMemory(count), "datastore_unavailable"
		end
		local rows = {}
		for index, entry in ipairs(entries or {}) do
			local key = entry:GetKey()
			local userId = idOf(key)
			local value = tonumber(entry:GetValue())
			if userId and value then
				rows[index] = { userId = userId, score = math.floor(value), name = memoryNames[key] }
			end
		end
		return rows, nil
	end

	-- One name per row the caller is about to show, cached, so a page read costs a page of
	-- lookups at most once per nameTtl rather than on every refresh.
	function store.GetName(userId)
		if type(userId) ~= "number" or userId ~= userId then
			return nil
		end
		local key = keyOf(math.floor(userId))
		if memoryNames[key] then
			return memoryNames[key]
		end
		local cached = nameCache[key]
		local now = os.clock()
		if cached and (now - cached.at) < nameTtl then
			return cached.name
		end
		local ok, name = attempt(function()
			return names:GetAsync(key)
		end)
		local resolved = ok and type(name) == "string" and name ~= "" and name or nil
		nameCache[key] = { name = resolved, at = now }
		if resolved then
			memoryNames[key] = resolved
		end
		return resolved
	end

	function store.Flush()
		local wrote = 0
		for key, entry in pairs(pending) do
			if writeNow(key, entry.score, entry.name) then
				wrote += 1
			end
		end
		return wrote
	end

	function store.Pending()
		local count = 0
		for _ in pairs(pending) do
			count += 1
		end
		return count
	end

	game:BindToClose(function()
		store.Flush()
	end)

	return store
end

return DataStoreSafe
