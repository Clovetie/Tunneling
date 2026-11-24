--!nonstrict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Remotes = require(ReplicatedStorage:WaitForChild("Remotes"))
local SettingsConfig = require(ReplicatedStorage:WaitForChild("SettingsConfig"))
local SettingsData = require(ReplicatedStorage:WaitForChild("SettingsData"))

-- NetworkGuard is optional: with it, a save request is schema-checked and token-bucket
-- limited; without it the cooldown below still applies and SettingsData still refuses
-- anything malformed. Warn once rather than refuse to run.
local guardScript = ReplicatedStorage:FindFirstChild("NetworkGuard")
local guard = guardScript and require(guardScript).new()
if guard then
	guard:RegisterEndpoint({
		name = SettingsConfig.REMOTE_SAVE,
		ratePerSecond = 1,
		burst = 4,
		schema = { settings = { kind = "table" } },
	})
else
	warn("settings-menu: NetworkGuard is not in ReplicatedStorage, so saves are cooldown and schema-normalised only")
end

local saveEvent = Remotes.event(SettingsConfig.REMOTE_SAVE)
local loadEvent = Remotes.event(SettingsConfig.REMOTE_LOAD)

local lastSave = {}
local SAVE_COOLDOWN = 0.5

local function dataService()
	return shared.DataServiceAPI
end

local function readSaved(player)
	local service = dataService()
	local data = service and service.GetData(player)
	if type(data) ~= "table" then
		return nil
	end
	-- Mutating the live profile table is the convention; core/data auto-saves it. A raw
	-- DataStore call would bypass its session lock, retries and BindToClose flush.
	return data[SettingsConfig.SAVE_KEY]
end

local function writeSaved(player, settings)
	local service = dataService()
	local data = service and service.GetData(player)
	if type(data) ~= "table" then
		return false
	end
	data[SettingsConfig.SAVE_KEY] = settings
	return true
end

local function send(player, settings)
	loadEvent:FireClient(player, settings)
end

local function current(player)
	return SettingsData.normalize(readSaved(player))
end

shared.SettingsAPI = {
	Get = function(player)
		return current(player)
	end,
	Set = function(player, key, value)
		local settings, ok, reason = SettingsData.set(current(player), key, value)
		if not ok then
			return false, reason
		end
		writeSaved(player, settings)
		send(player, settings)
		return true, nil
	end,
	Reset = function(player)
		local settings = SettingsData.defaults()
		writeSaved(player, settings)
		send(player, settings)
		return settings
	end,
	GetKeybind = function(player, action)
		return current(player).Keybinds[action]
	end,
}

saveEvent.OnServerEvent:Connect(function(player, incoming)
	if type(incoming) ~= "table" then
		return
	end
	if guard then
		local ok, reason = guard:ValidatePayload(player.Name, SettingsConfig.REMOTE_SAVE, { settings = incoming })
		if not ok then
			return
		end
	end
	local now = os.clock()
	local last = lastSave[player]
	if last and now - last < SAVE_COOLDOWN then
		return
	end
	lastSave[player] = now

	-- The server normalises rather than trusts: unknown keys are dropped, numbers are
	-- clamped and snapped to their step, enums are checked against their option list, and
	-- key names have to look like an Enum.KeyCode name.
	local settings = SettingsData.normalize(incoming)
	writeSaved(player, settings)
	-- Reply with the normalised table so a client that sent 5 for a 0..1 slider sees the
	-- value that was actually stored instead of the one it asked for.
	send(player, settings)
end)

local function onPlayerAdded(player)
	local service = dataService()
	if service and service.PlayerLoaded and service.PlayerLoaded.Connect then
		service.PlayerLoaded:Connect(function(loaded, data)
			if loaded == player then
				send(player, SettingsData.normalize(type(data) == "table" and data[SettingsConfig.SAVE_KEY] or nil))
			end
		end)
	end
	send(player, current(player))
end

Players.PlayerAdded:Connect(onPlayerAdded)
for _, player in ipairs(Players:GetPlayers()) do
	onPlayerAdded(player)
end

Players.PlayerRemoving:Connect(function(player)
	lastSave[player] = nil
end)
