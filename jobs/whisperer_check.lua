--!nonstrict
--[==[
	jobs/whisperer_check.lua - prove every Whisperer audio ID actually loads.

	Run AFTER set_whisperer_ids.lua. For each ID in
	Config.Entities.Whisperer.{WhisperSoundIds, FakeGlassSoundIds} it builds a
	Sound, asks ContentProvider to preload it, and reports the resulting
	TimeLength. A duration above 0 means the asset exists and is audible; a
	failure means the ID is wrong, private, or not yet approved - better to
	find that out here than to hear silence at 3am in the game.

	Read-only apart from a scratch folder in ServerStorage, which it destroys.
]==]

local H = game:GetService("HttpService")
local ContentProvider = game:GetService("ContentProvider")
local ServerStorage = game:GetService("ServerStorage")

local function n2(x)
	if x ~= x or x == math.huge or x == -math.huge then
		return 0
	end
	return math.floor(x * 100 + 0.5) / 100
end

local nl = game:GetService("ServerScriptService"):FindFirstChild("NightLoop")
if not nl then
	return H:JSONEncode({ error = "no ServerScriptService.NightLoop" })
end
local okConfig, Config = pcall(require, nl:FindFirstChild("Config"))
if not okConfig then
	return H:JSONEncode({ error = "Config failed to require: " .. tostring(Config) })
end

local settings = Config.Entities and Config.Entities.Whisperer
if not settings then
	return H:JSONEncode({ error = "no Config.Entities.Whisperer" })
end

local scratch = Instance.new("Folder")
scratch.Name = "WhispererIdCheck"
scratch.Parent = ServerStorage

local function checkList(list)
	local out = {}
	for _, id in ipairs(list or {}) do
		local sound = Instance.new("Sound")
		sound.Name = "check"
		sound.SoundId = tostring(id)
		sound.Volume = 0
		sound.Parent = scratch

		local ok, err = pcall(function()
			ContentProvider:PreloadAsync({ sound })
		end)
		local length = 0
		if ok then
			local okLen, len = pcall(function()
				return sound.TimeLength
			end)
			if okLen and type(len) == "number" then
				length = len
			end
		end

		table.insert(out, {
			id = tostring(id),
			loaded = ok,
			seconds = n2(length),
			error = ok and nil or tostring(err),
		})
	end
	return out
end

local whispers = checkList(settings.WhisperSoundIds)
local fakes = checkList(settings.FakeGlassSoundIds)

scratch:Destroy()

local function tally(list)
	local ok, bad = 0, 0
	for _, entry in ipairs(list) do
		if entry.loaded and entry.seconds > 0 then
			ok += 1
		else
			bad += 1
		end
	end
	return ok, bad
end

local wOk, wBad = tally(whispers)
local fOk, fBad = tally(fakes)

return H:JSONEncode({
	whispers = whispers,
	fakeGlass = fakes,
	summary = {
		whisperTotal = #whispers,
		whisperGood = wOk,
		whisperBad = wBad,
		fakeGlassTotal = #fakes,
		fakeGlassGood = fOk,
		fakeGlassBad = fBad,
		empty = (#whispers == 0),
	},
	enabled = settings.Enabled,
	ok = (#whispers > 0) and (wBad == 0) and (fBad == 0),
	hint = (#whispers == 0)
		and "no IDs set - Whisperer stays disabled until the user supplies them"
		or nil,
})
