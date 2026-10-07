--!nonstrict
--[==[
	jobs/set_config_flag.lua - flip one top-level flag in Config.lua, in place,
	without shipping a whole push.

	Built for the standing TODO "set Config.Hud.ShowPhase = false when testing
	ends, keep the bar" - but it works for any flag sitting on its own indented
	line inside a table, e.g. ShowClock, or Enabled / UseBuiltInRig under an
	Entities block. It does NOT match `Config.AutoStart = ...` style top-level
	lines; change the pattern if you ever need those.

	    local FLAG  = "ShowPhase"
	    local VALUE = "false"

	HOW IT WORKS, AND THE ONE TRAP: it edits the ModuleScript's .Source in
	place. Studio caches require() per Instance (AGENTS.md pitfall #1), so the
	current Studio session keeps using the old value until the DataModel
	reloads - which a new playtest does. So: run this, then start a fresh
	playtest. Do not expect a running night to notice.

	It reports the before and after text so you can see exactly what changed,
	and refuses to guess: if the flag is not found exactly once, nothing is
	written.
]==]

local H = game:GetService("HttpService")

local FLAG = "ShowPhase"
local VALUE = "false"

local nl = game:GetService("ServerScriptService"):FindFirstChild("NightLoop")
if not nl then
	return H:JSONEncode({ error = "no ServerScriptService.NightLoop" })
end
local config = nl:FindFirstChild("Config")
if not config or not config:IsA("LuaSourceContainer") then
	return H:JSONEncode({ error = "no ServerScriptService.NightLoop.Config" })
end

local src = config.Source
local pattern = "(\n\t" .. FLAG .. " = )([%w_%.]+)"

-- count first: a flag that matches twice is a guess, not an edit
local _, matches = src:gsub(pattern, "")
if matches ~= 1 then
	return H:JSONEncode({
		error = ("flag %s matched %d times (expected exactly 1) - nothing changed")
			:format(FLAG, matches),
	})
end

local before = src:match(pattern)
local newSrc = src:gsub(pattern, "%1" .. VALUE, 1)
config.Source = newSrc

local after = config.Source:match(pattern)

return H:JSONEncode({
	flag = FLAG,
	value = VALUE,
	before = before,
	after = after,
	written = after == ("\n\t" .. FLAG .. " = " .. VALUE),
	bytes = #config.Source,
	-- remember: the running session keeps the cached module until reload
	note = "takes effect on the next playtest / place reload, not in a running night",
})
