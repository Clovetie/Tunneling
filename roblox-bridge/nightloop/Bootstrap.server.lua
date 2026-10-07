--!nonstrict
--[[
	NightLoop · Bootstrap
	The ONLY Script in the package. Everything else is a ModuleScript.

	If Config.AutoStart is false this does nothing, and you drive the night
	yourself:

		local Director = require(game.ServerScriptService.NightLoop.Director)
		Director:StartNight({ mode = "OneNight" })
]]

local Players = game:GetService("Players")

local package = script.Parent
local Config = require(package.Config)
local Director = require(package.Director)
local Flash = require(package.Flash)
local Atmosphere = require(package.Atmosphere)

-- lighting reacts to the night; no-op if Config.Atmosphere.ControlLighting = false
Atmosphere:Bind(Director)

-- charges tick independently of the night so the tool always works
Flash:Start()

Director.NightEnded:Connect(function(result, detail)
	print(("[NightLoop] night ended: %s%s"):format(
		result,
		type(detail) == "table" and (" (" .. tostring(detail.reason) .. ")")
			or (detail and (" (" .. tostring(detail) .. ")") or "")
	))
end)

if not Config.AutoStart then
	print("[NightLoop] loaded, AutoStart off — call Director:StartNight() yourself")
	return
end

-- wait for a player before starting, otherwise the night burns down in an
-- empty server
task.spawn(function()
	while #Players:GetPlayers() == 0 do
		task.wait(1)
	end
	task.wait(2)
	Director:StartNight()
end)
