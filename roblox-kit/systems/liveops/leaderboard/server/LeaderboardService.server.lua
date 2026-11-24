--!nonstrict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Remotes = require(ReplicatedStorage:WaitForChild("Remotes"))
local LeaderboardConfig = require(ReplicatedStorage:WaitForChild("LeaderboardConfig"))
local LeaderboardMath = require(ReplicatedStorage:WaitForChild("LeaderboardMath"))

local cfg = LeaderboardConfig

-- NetGuard is optional, exactly as in mechanics/checkpoint-respawn: with it a client that
-- spams the fetch remote is rate limited, without it the per-player cooldown still applies.
local netGuardScript = ReplicatedStorage:FindFirstChild("NetGuard")
local NetGuard = netGuardScript and require(netGuardScript)
if not NetGuard then
	warn("leaderboard: NetGuard is not in ReplicatedStorage, so fetches are only cooldown checked")
end

-- Enabled boards, in the order the client shows them: BOARD_ORDER first (that is what a
-- game edits to arrange its tabs), then any board it forgot to list.
local boards = {}
local boardsByKey = {}
do
	-- table.sort is not stable, so a board missing from BOARD_ORDER would swap places with
	-- its neighbours between runs. One precomputed position each keeps the tab order fixed:
	-- listed boards in BOARD_ORDER, then the rest in the order BOARDS declares them.
	local order = type(cfg.BOARD_ORDER) == "table" and cfg.BOARD_ORDER or {}
	local declared = type(cfg.BOARDS) == "table" and cfg.BOARDS or {}
	local position = {}
	for index, board in ipairs(declared) do
		if type(board) == "table" and type(board.key) == "string" and board.key ~= "" and board.enabled ~= false then
			position[board.key] = position[board.key] or (#order + index)
		end
	end
	for index, key in ipairs(order) do
		if type(key) == "string" and position[key] then
			position[key] = index
		end
	end
	for _, board in ipairs(declared) do
		local key = type(board) == "table" and board.key or nil
		if key and position[key] and not boardsByKey[key] and board.enabled ~= false then
			table.insert(boards, board)
			boardsByKey[key] = board
		end
	end
	table.sort(boards, function(a, b)
		return position[a.key] < position[b.key]
	end)
end

-- One ordered store per board, created through DataStoreSafe so writes are throttled and a
-- Studio session with API access off degrades to memory with one warning instead of an
-- error on every call. Without the module the per-server board still works.
local stores = {}
local cloudReason = nil
do
	local safeScript = ReplicatedStorage:FindFirstChild("DataStoreSafe")
	local DataStoreSafe = nil
	if safeScript then
		local ok, module = pcall(require, safeScript)
		DataStoreSafe = ok and module or nil
	end
	if type(DataStoreSafe) ~= "table" or type(DataStoreSafe.createOrderedStore) ~= "function" then
		cloudReason = "missing_module"
		warn("leaderboard: DataStoreSafe is not in ReplicatedStorage, so only the per-server board is available")
	else
		for _, board in ipairs(boards) do
			local storeName = ("%s_%s_%s"):format(cfg.STORE_PREFIX, board.key, cfg.STORE_VERSION)
			local ok, store = pcall(DataStoreSafe.createOrderedStore, storeName, {
				minWriteInterval = cfg.WRITE_INTERVAL,
				nameStore = cfg.NAME_STORE_NAME,
			})
			if ok and type(store) == "table" then
				stores[board.key] = store
			else
				cloudReason = cloudReason or tostring(store)
				warn(("leaderboard: no store for board %q (%s)"):format(board.key, tostring(store)))
			end
		end
	end
end

local requestEvent = Remotes.event(cfg.REMOTE_REQUEST)
local stateEvent = Remotes.event(cfg.REMOTE_STATE)
local optOutEvent = Remotes.event(cfg.REMOTE_OPT_OUT)

local bests = {}
local optedOut = {}
local lastRequest = {}
local cache = {}

local function dataService()
	return shared.DataServiceAPI
end

local function profile(player)
	local service = dataService()
	local data = service and service.GetData(player)
	return type(data) == "table" and data or nil
end

local function displayName(player)
	local name = player.DisplayName
	if type(name) ~= "string" or name == "" then
		name = player.Name
	end
	return LeaderboardMath.trimName(name, cfg.MAX_NAME_LENGTH)
end

local function isOptedOut(player)
	if optedOut[player] ~= nil then
		return optedOut[player]
	end
	local flag = player:GetAttribute(cfg.OPT_OUT_ATTRIBUTE)
	if type(flag) == "boolean" then
		optedOut[player] = flag
		return flag
	end
	local data = profile(player)
	if data and type(data[cfg.SAVE_KEY]) == "boolean" then
		optedOut[player] = data[cfg.SAVE_KEY]
		return data[cfg.SAVE_KEY]
	end
	return false
end

local function storeFor(boardKey)
	return stores[boardKey]
end

local function publish(player, boardKey, score)
	if not LeaderboardMath.shouldPublish(score, cfg.MIN_SCORE) then
		return false
	end
	if isOptedOut(player) then
		return false
	end
	local store = storeFor(boardKey)
	if not store then
		return false
	end
	return store.Set(player.UserId, score, displayName(player), false)
end

local function readValue(player, board)
	local source = type(board.source) == "table" and board.source or {}
	local key = source.key
	if source.kind == "leaderstat" then
		local stats = player:FindFirstChild("leaderstats")
		local value = stats and type(key) == "string" and stats:FindFirstChild(key) or nil
		return value and tonumber(value.Value) or nil
	elseif source.kind == "attribute" then
		return type(key) == "string" and tonumber(player:GetAttribute(key)) or nil
	elseif source.kind == "data" then
		local data = profile(player)
		return data and type(key) == "string" and tonumber(data[key]) or nil
	elseif source.kind == "economy" then
		local economy = shared.EconomyAPI
		if type(economy) == "table" and type(economy.GetBalance) == "function" then
			local ok, balance = pcall(economy.GetBalance, player)
			return ok and tonumber(balance) or nil
		end
	end
	return nil
end

local function sample(player, force)
	local ledger = bests[player]
	if not ledger then
		return
	end
	for _, board in ipairs(boards) do
		local best = LeaderboardMath.best(ledger[board.key], readValue(player, board))
		ledger[board.key] = best
		if best and LeaderboardMath.shouldPublish(best, cfg.MIN_SCORE) and not isOptedOut(player) then
			local store = storeFor(board.key)
			if store then
				store.Set(player.UserId, best, displayName(player), force and true or false)
			end
		end
	end
end

local function serverRows(boardKey)
	local rows = {}
	for player, ledger in pairs(bests) do
		local score = ledger[boardKey]
		if not isOptedOut(player) and LeaderboardMath.shouldPublish(score, cfg.MIN_SCORE) then
			table.insert(rows, { userId = player.UserId, name = displayName(player), score = score })
		end
	end
	return LeaderboardMath.sortRows(rows, cfg.MAX_NAME_LENGTH)
end

local function globalRows(boardKey)
	local now = os.clock()
	local entry = cache[boardKey]
	if entry and (now - entry.at) < cfg.CACHE_TTL then
		return entry.rows, entry.warning
	end
	local store = storeFor(boardKey)
	if not store then
		return {}, cloudReason or "datastore_unavailable"
	end
	local fetched, warning = store.GetTop(math.clamp(math.floor(cfg.MAX_FETCH or 100), 1, 100))
	local rows = LeaderboardMath.sortRows(fetched, cfg.MAX_NAME_LENGTH)
	cache[boardKey] = { rows = rows, at = now, warning = warning }
	return rows, warning
end

-- Names live in their own store, so they are resolved only for the rows about to be sent:
-- a page of twenty costs twenty lookups, not a hundred per refresh. The rows belong to the
-- cache (sortRows copies), so filling a name in here also saves the next reader the lookup.
local function resolveNames(rows, boardKey)
	local store = storeFor(boardKey)
	if not store then
		return rows
	end
	for _, row in ipairs(rows) do
		if type(row.name) ~= "string" or row.name == "" then
			row.name = LeaderboardMath.trimName(store.GetName(row.userId), cfg.MAX_NAME_LENGTH)
		end
	end
	return rows
end

local function boardList()
	local out = {}
	for _, board in ipairs(boards) do
		table.insert(out, { key = board.key, label = board.label or board.key })
	end
	return out
end

local function setOptOut(player, on)
	on = on and true or false
	optedOut[player] = on
	player:SetAttribute(cfg.OPT_OUT_ATTRIBUTE, on)
	local data = profile(player)
	if data then
		data[cfg.SAVE_KEY] = on
	end
	if on then
		for _, board in ipairs(boards) do
			local store = storeFor(board.key)
			if store then
				store.Remove(player.UserId)
			end
		end
		cache = {}
	end
	return on
end

shared.LeaderboardAPI = {
	Boards = function()
		return boardList()
	end,
	GetTop = function(boardKey, limit)
		if not boardsByKey[boardKey] then
			return {}
		end
		return LeaderboardMath.topN(globalRows(boardKey), limit or cfg.PAGE_SIZE)
	end,
	GetServerTop = function(boardKey, limit)
		if not boardsByKey[boardKey] then
			return {}
		end
		return LeaderboardMath.topN(serverRows(boardKey), limit or cfg.PAGE_SIZE)
	end,
	GetScore = function(player, boardKey)
		local ledger = bests[player]
		return ledger and ledger[boardKey] or nil
	end,
	GetRank = function(player, boardKey)
		if not boardsByKey[boardKey] or type(player) ~= "table" then
			return nil
		end
		return LeaderboardMath.rankOf(globalRows(boardKey), player.UserId)
	end,
	-- For games whose scores come from events rather than a leaderstat.
	Submit = function(player, boardKey, value)
		if not boardsByKey[boardKey] or type(player) ~= "table" or not bests[player] then
			return false
		end
		local ledger = bests[player]
		ledger[boardKey] = LeaderboardMath.best(ledger[boardKey], value)
		cache[boardKey] = nil
		return publish(player, boardKey, ledger[boardKey]) and true or false
	end,
	SetOptOut = function(player, on)
		if type(player) ~= "table" or not bests[player] then
			return false
		end
		return setOptOut(player, on)
	end,
	IsOptOut = function(player)
		return isOptedOut(player) and true or false
	end,
	Refresh = function(boardKey)
		if boardKey then
			cache[boardKey] = nil
		else
			cache = {}
		end
	end,
	Status = function()
		local pending = 0
		local available = 0
		for _, board in ipairs(boards) do
			local store = storeFor(board.key)
			if store then
				available += 1
				pending += store.Pending()
			end
		end
		return {
			cloud = available > 0,
			cloudReason = available > 0 and nil or (cloudReason or "datastore_unavailable"),
			boards = boardList(),
			stores = available,
			pendingWrites = pending,
			inServer = #Players:GetPlayers(),
		}
	end,
}

local function answer(player, boardKey, scope, page)
	local rows, warning
	if scope == "server" then
		rows = serverRows(boardKey)
		warning = nil
	else
		rows, warning = globalRows(boardKey)
	end
	local slice = LeaderboardMath.paginate(rows, page, cfg.PAGE_SIZE)
	resolveNames(slice.rows, boardKey)
	local around = LeaderboardMath.aroundMe(rows, player.UserId, cfg.AROUND_WINDOW)
	resolveNames(around.rows, boardKey)
	stateEvent:FireClient(player, {
		board = boardKey,
		scope = scope,
		page = slice.page,
		pageCount = slice.pageCount,
		total = slice.total,
		firstRank = slice.firstRank,
		rows = slice.rows,
		around = around.rows,
		me = {
			rank = around.rank,
			score = (bests[player] or {})[boardKey],
			optOut = isOptedOut(player) and true or false,
			aroundFirstRank = around.firstRank,
		},
		warning = warning,
		boards = boardList(),
		cachedAt = os.clock(),
	})
end

requestEvent.OnServerEvent:Connect(function(player, payload)
	if type(payload) ~= "table" then
		return
	end
	local boardKey = payload.board
	if type(boardKey) ~= "string" or #boardKey > 32 or not boardsByKey[boardKey] then
		return
	end
	local scope = payload.scope == "server" and "server" or "global"
	local now = os.clock()
	local last = lastRequest[player]
	if last and (now - last) < cfg.REQUEST_COOLDOWN then
		return
	end
	if NetGuard and not NetGuard.allowRate(player, cfg.REMOTE_REQUEST, cfg.REQUEST_RATE_LIMIT, cfg.REQUEST_RATE_WINDOW) then
		return
	end
	lastRequest[player] = now
	answer(player, boardKey, scope, tonumber(payload.page))
end)

-- Opt-out is its own remote rather than a flag on the fetch request, so a state change
-- cannot ride along on a read that the rate limiter may drop. The client sends the view it
-- is looking at so the reply does not throw it back to page 1, and the toggle shares the
-- fetch cooldown: otherwise it would be a way to force uncached global reads.
optOutEvent.OnServerEvent:Connect(function(player, payload)
	if type(payload) ~= "table" or type(payload.on) ~= "boolean" or not bests[player] then
		return
	end
	local now = os.clock()
	local last = lastRequest[player]
	if last and (now - last) < cfg.REQUEST_COOLDOWN then
		return
	end
	if NetGuard and not NetGuard.allowRate(player, cfg.REMOTE_OPT_OUT, cfg.REQUEST_RATE_LIMIT, cfg.REQUEST_RATE_WINDOW) then
		return
	end
	lastRequest[player] = now
	setOptOut(player, payload.on)
	local boardKey = type(payload.board) == "string" and boardsByKey[payload.board] and payload.board
		or (boards[1] and boards[1].key)
	if not boardKey then
		return
	end
	answer(player, boardKey, payload.scope == "server" and "server" or "global", tonumber(payload.page))
end)

local function onPlayerAdded(player)
	bests[player] = {}
	optedOut[player] = nil
	local opted = isOptedOut(player)
	if opted then
		-- A save that says "hide me" is honoured before the first sample, and the store is
		-- cleaned in case an earlier session published them.
		for _, board in ipairs(boards) do
			local store = storeFor(board.key)
			if store then
				store.Remove(player.UserId)
			end
		end
	end
	sample(player, false)

	local service = dataService()
	if service and service.PlayerLoaded and service.PlayerLoaded.Connect then
		service.PlayerLoaded:Connect(function(loaded, data)
			if loaded ~= player or optedOut[player] ~= nil then
				return
			end
			if type(data) == "table" and type(data[cfg.SAVE_KEY]) == "boolean" then
				optedOut[player] = data[cfg.SAVE_KEY]
				if data[cfg.SAVE_KEY] then
					setOptOut(player, true)
				end
			end
		end)
	end
end

Players.PlayerAdded:Connect(onPlayerAdded)
for _, player in ipairs(Players:GetPlayers()) do
	task.spawn(onPlayerAdded, player)
end

Players.PlayerRemoving:Connect(function(player)
	if cfg.LEAVE_WRITE and not isOptedOut(player) then
		sample(player, true)
	end
	bests[player] = nil
	optedOut[player] = nil
	lastRequest[player] = nil
end)

game:BindToClose(function()
	for _, player in ipairs(Players:GetPlayers()) do
		if cfg.LEAVE_WRITE and not isOptedOut(player) then
			sample(player, true)
		end
	end
	for _, board in ipairs(boards) do
		local store = storeFor(board.key)
		if store then
			store.Flush()
		end
	end
end)

if math.floor(cfg.SAMPLE_INTERVAL or 0) > 0 then
	task.spawn(function()
		while true do
			task.wait(cfg.SAMPLE_INTERVAL)
			for _, player in ipairs(Players:GetPlayers()) do
				if bests[player] then
					sample(player, false)
				end
			end
		end
	end)
end
