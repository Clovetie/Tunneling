--!nonstrict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local UserInputService = game:GetService("UserInputService")

local player = Players.LocalPlayer
local Shared = ReplicatedStorage:WaitForChild("Shared")
local MovementConfig = require(Shared:WaitForChild("MovementConfig"))
local remote = ReplicatedStorage:WaitForChild("StaminaStanceEvent", 15)

local stamina = MovementConfig.MaxStamina
local sprinting = false
local exhausted = false

UserInputService.InputBegan:Connect(function(input, gp)
	if gp then
		return
	end
	if input.KeyCode == Enum.KeyCode.LeftShift and not exhausted then
		sprinting = true
		if remote then
			remote:FireServer("sprint")
		end
	elseif input.KeyCode == Enum.KeyCode.LeftControl then
		sprinting = false
		if remote then
			remote:FireServer("crouch")
		end
	end
end)

UserInputService.InputEnded:Connect(function(input)
	if input.KeyCode == Enum.KeyCode.LeftShift or input.KeyCode == Enum.KeyCode.LeftControl then
		sprinting = false
		if remote then
			remote:FireServer("walk")
		end
	end
end)

RunService.Heartbeat:Connect(function(dt)
	if sprinting then
		stamina = math.max(0, stamina - MovementConfig.DrainPerSecond * dt)
		if stamina <= 0 then
			sprinting = false
			exhausted = true
			if remote then
				remote:FireServer("walk")
			end
		end
	else
		stamina = math.min(MovementConfig.MaxStamina, stamina + MovementConfig.RegenPerSecond * dt)
		if exhausted and stamina >= MovementConfig.ExhaustRecoveryThreshold then
			exhausted = false
		end
	end
end)
