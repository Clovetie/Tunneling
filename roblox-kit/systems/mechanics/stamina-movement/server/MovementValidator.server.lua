--!nonstrict
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local MovementConfig = require(Shared:WaitForChild("MovementConfig"))

local remote = ReplicatedStorage:FindFirstChild("StaminaStanceEvent")
if not remote then
	remote = Instance.new("RemoteEvent")
	remote.Name = "StaminaStanceEvent"
	remote.Parent = ReplicatedStorage
end

remote.OnServerEvent:Connect(function(player, stance)
	local char = player.Character
	local hum = char and char:FindFirstChildOfClass("Humanoid")
	if not hum then
		return
	end
	if stance == "sprint" then
		hum.WalkSpeed = MovementConfig.SprintSpeed
		player:SetAttribute("NoiseRadius", 42)
	elseif stance == "crouch" then
		hum.WalkSpeed = MovementConfig.CrouchSpeed
		player:SetAttribute("NoiseRadius", 6)
	else
		hum.WalkSpeed = MovementConfig.WalkSpeed
		player:SetAttribute("NoiseRadius", 18)
	end
end)
