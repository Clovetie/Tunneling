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

return DataStoreSafe
