--!nonstrict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local RoundConfig = require(Shared:WaitForChild("RoundConfig"))

local remote = ReplicatedStorage:FindFirstChild("RoundSurvivalState")
if not remote then
	remote = Instance.new("RemoteEvent")
	remote.Name = "RoundSurvivalState"
	remote.Parent = ReplicatedStorage
end

local completedGenerators = 0
local phase = "Intermission"

local function broadcast(extra)
	remote:FireAllClients({
		phase = phase,
		completedGenerators = completedGenerators,
		requiredGenerators = RoundConfig.GeneratorsRequired,
		note = extra,
	})
end

local function bindPrompt(prompt)
	if prompt:GetAttribute("BoundRoundTask") then
		return
	end
	prompt:SetAttribute("BoundRoundTask", true)
	prompt.Triggered:Connect(function(player)
		if prompt:GetAttribute("Repaired") then
			return
		end
		prompt:SetAttribute("Repaired", true)
		prompt.Enabled = false
		completedGenerators += 1
		local curThreat = tonumber(player:GetAttribute("ThreatLevel")) or 0
		player:SetAttribute("ThreatLevel", curThreat + RoundConfig.RepairThreatGain)
		if completedGenerators >= RoundConfig.GeneratorsRequired then
			phase = "ExtractionOpen"
			broadcast("All generators online — reach the extraction exit!")
		else
			broadcast(player.Name .. " repaired a generator!")
		end
	end)
end

for _, inst in ipairs(Workspace:GetDescendants()) do
	if inst:IsA("ProximityPrompt") and inst.Name == "GeneratorPrompt" then
		bindPrompt(inst)
	end
end

Players.PlayerAdded:Connect(function(player)
	player:SetAttribute("ThreatLevel", 0)
	broadcast("Survivor joined")
end)
