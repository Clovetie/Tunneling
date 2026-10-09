--!nonstrict
--[==[
	jobs/set_whisperer_ids.lua - drop your audio IDs into Config.lua.

	Whisperer is written and enabled; it has been sitting disabled only because
	WhisperSoundIds / FakeGlassSoundIds are empty. Fill in the two lists below
	with whatever the user supplies - IDs are NEVER invented - then:

		.\ab.ps1 runfile set_whisperer_ids.lua
		.\ab.ps1 runfile whisperer_check.lua     (proves every ID loads)
		.\ab.ps1 runfile night_watch.lua         (optional: watch it work)

	Format: full rbxassetid:// strings, or bare numbers (they get prefixed).

	Same trap as set_config_flag.lua: it edits .Source in place, and Studio
	caches require() per Instance, so it takes effect on the NEXT playtest or
	place reload, not in a night that is already running.
]==]

local H = game:GetService("HttpService")

local WHISPERS = {
	-- "rbxassetid://000000000",
}

local FAKE_GLASS = {
	-- these fire from phase 4 as misdirection: a whisper with nothing there
}

local function normalise(list)
	local out = {}
	for _, v in ipairs(list) do
		local s = tostring(v)
		if not s:match("^rbxassetid://") then
			s = "rbxassetid://" .. s:gsub("^%s*(.-)%s*$", "%1")
		end
		table.insert(out, s)
	end
	return out
end

local function render(list)
	if #list == 0 then
		return "{}"
	end
	local parts = {}
	for _, v in ipairs(list) do
		table.insert(parts, ('"%s"'):format(v))
	end
	return "{ " .. table.concat(parts, ", ") .. " }"
end

local nl = game:GetService("ServerScriptService"):FindFirstChild("NightLoop")
if not nl then
	return H:JSONEncode({ error = "no ServerScriptService.NightLoop" })
end
local config = nl:FindFirstChild("Config")
if not config or not config:IsA("LuaSourceContainer") then
	return H:JSONEncode({ error = "no ServerScriptService.NightLoop.Config" })
end

local whisperStr = render(normalise(WHISPERS))
local glassStr = render(normalise(FAKE_GLASS))

local src = config.Source
local edits = {
	{ name = "WhisperSoundIds", value = whisperStr, pattern = "(\n\t\tWhisperSoundIds = )(%b{})" },
	{ name = "FakeGlassSoundIds", value = glassStr, pattern = "(\n\t\tFakeGlassSoundIds = )(%b{})" },
}

-- count both before touching anything: a pattern that matches twice is a
-- guess, not an edit, and a half-written Config is worse than none
for _, edit in ipairs(edits) do
	local _, n = src:gsub(edit.pattern, "")
	if n ~= 1 then
		return H:JSONEncode({
			error = ("%s matched %d times (expected 1) - nothing changed"):format(edit.name, n),
		})
	end
end

local before = {}
for _, edit in ipairs(edits) do
	before[edit.name] = src:match(edit.pattern)
	local newSrc = src:gsub(edit.pattern, "%1" .. edit.value, 1)
	src = newSrc
end

config.Source = src

local readback = {}
for _, edit in ipairs(edits) do
	readback[edit.name] = config.Source:match(edit.pattern)
end

return H:JSONEncode({
	before = before,
	after = readback,
	counts = { whispers = #WHISPERS, fakeGlass = #FAKE_GLASS },
	written = readback.WhisperSoundIds == "\n\t\tWhisperSoundIds = " .. whisperStr
		and readback.FakeGlassSoundIds == "\n\t\tFakeGlassSoundIds = " .. glassStr,
	bytes = #config.Source,
	note = "takes effect on the next playtest / place reload",
	next = "run whisperer_check.lua to prove every ID loads before playing a night",
})
