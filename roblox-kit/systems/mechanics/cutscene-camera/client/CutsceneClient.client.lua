--!strict
-- CutsceneClient.client.lua — Plays keyframed camera cutscenes on the client (v6.0.0)

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local remote = ReplicatedStorage:WaitForChild("ArenaCutsceneEvent", 15) :: RemoteEvent?
local sharedFolder = ReplicatedStorage:FindFirstChild("Shared")
local defsModule = sharedFolder and sharedFolder:FindFirstChild("CutsceneDefinitions")
local defs = if defsModule and defsModule:IsA("ModuleScript") then require(defsModule) :: any else {}

local playing = false

local function playSequence(track: any)
	if playing or type(track) ~= "table" then
		return
	end
	local cam = workspace.CurrentCamera
	if not cam then
		return
	end
	playing = true
	local prevType = cam.CameraType
	local prevFov = cam.FieldOfView
	cam.CameraType = Enum.CameraType.Scriptable

	for _, kf in ipairs(track.keyframes or {}) do
		local dur = math.max(0.05, tonumber(kf.duration) or 1.2)
		local tween = TweenService:Create(
			cam,
			TweenInfo.new(dur, Enum.EasingStyle.Sine, Enum.EasingDirection.InOut),
			{ CFrame = kf.cframe, FieldOfView = tonumber(kf.fov) or prevFov }
		)
		tween:Play()
		task.wait(dur + (tonumber(kf.holdSeconds) or 0))
	end

	cam.CameraType = prevType
	cam.FieldOfView = prevFov
	playing = false
end

if remote then
	remote.OnClientEvent:Connect(function(payload: any)
		if type(payload) == "table" and payload.action == "play" then
			local track = defs[tostring(payload.id)]
			if track then
				task.spawn(playSequence, track)
			end
		end
	end)
end
