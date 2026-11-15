--!nonstrict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local UserInputService = game:GetService("UserInputService")

local player = Players.LocalPlayer
local castRemote = ReplicatedStorage:WaitForChild("CastAbilityEvent", 15)
if not castRemote then
	return
end

local KEY_MAP = {
	[Enum.KeyCode.Q] = "SolarFlare",
	[Enum.KeyCode.E] = "ArcPulse",
	[Enum.KeyCode.R] = "AegisDash",
}

UserInputService.InputBegan:Connect(function(input, gp)
	if gp then
		return
	end
	local abilityId = KEY_MAP[input.KeyCode]
	if abilityId then
		local cam = workspace.CurrentCamera
		local dir = cam and cam.CFrame.LookVector or Vector3.new(0, 0, -1)
		castRemote:FireServer(abilityId, dir)
	end
end)
