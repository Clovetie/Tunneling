--!nonstrict
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local HorrorConfig = require(Shared:WaitForChild("HorrorConfig"))

local folder = ReplicatedStorage:FindFirstChild("HorrorRemotes")
if not folder then
	folder = Instance.new("Folder")
	folder.Name = "HorrorRemotes"
	folder.Parent = ReplicatedStorage
end

local stateEvent = folder:FindFirstChild("HorrorState") or Instance.new("RemoteEvent")
stateEvent.Name = "HorrorState"
stateEvent.Parent = folder

local cueEvent = folder:FindFirstChild("HorrorCue") or Instance.new("RemoteEvent")
cueEvent.Name = "HorrorCue"
cueEvent.Parent = folder

local elapsed = 0
local strikes = 0
local phaseIndex = 1

local function resolvePhase(t)
	local idx = 1
	for i, p in ipairs(HorrorConfig.Phases) do
		if t >= p.Start then
			idx = i
		end
	end
	return idx, HorrorConfig.Phases[idx]
end

local accum = 0
RunService.Heartbeat:Connect(function(dt)
	elapsed = (elapsed + dt) % HorrorConfig.NightDuration
	local idx, phase = resolvePhase(elapsed)
	if idx ~= phaseIndex then
		phaseIndex = idx
		cueEvent:FireAllClients({
			kind = "phaseChanged",
			phaseIndex = idx,
			phaseName = phase.Name,
			intensity = phase.Intensity,
		})
	end
	accum += dt
	if accum >= 1.0 then
		accum = 0
		stateEvent:FireAllClients({
			elapsed = math.floor(elapsed),
			duration = HorrorConfig.NightDuration,
			phaseIndex = idx,
			phaseName = phase.Name,
			intensity = phase.Intensity,
			strikes = strikes,
			maxStrikes = HorrorConfig.MaxStrikes,
		})
	end
end)
