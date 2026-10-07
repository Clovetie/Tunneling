--!nonstrict
--[[
	Flashlight input + arm aiming.   LocalScript, lives inside the Tool.
	Replaces the old "Turn" script, which started a `while wait()` loop on every
	equip that never exited — they stacked up and fought over Shoulder.C0.

	This one holds exactly one RenderStepped connection and drops it on unequip.
	Firing is a request only; the server owns charges and the burst.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

local tool = script.Parent
local player = Players.LocalPlayer
local camera = workspace.CurrentCamera

local remotes = ReplicatedStorage:WaitForChild("NightLoopRemotes", 30)
local flashRequest = remotes and remotes:WaitForChild("FlashRequest", 10)

local aimConnection = nil
local shoulder = nil
local basePosition = nil

local function findShoulder(character)
	-- R15
	local upper = character:FindFirstChild("RightUpperArm")
	if upper then
		local joint = upper:FindFirstChild("RightShoulder")
		if joint and joint:IsA("Motor6D") then
			return joint
		end
	end
	-- R6
	local torso = character:FindFirstChild("Torso")
	if torso then
		local joint = torso:FindFirstChild("Right Shoulder")
		if joint and joint:IsA("Motor6D") then
			return joint
		end
	end
	return nil
end

local function stopAiming()
	if aimConnection then
		aimConnection:Disconnect()
		aimConnection = nil
	end
	if shoulder and basePosition then
		pcall(function()
			shoulder.C0 = CFrame.new(basePosition)
		end)
	end
	shoulder = nil
	basePosition = nil
end

tool.Equipped:Connect(function()
	local character = tool.Parent
	if not character or not character:FindFirstChild("Humanoid") then
		return
	end

	shoulder = findShoulder(character)
	if not shoulder then
		return
	end
	basePosition = shoulder.C0.Position

	if aimConnection then
		aimConnection:Disconnect()
	end

	aimConnection = RunService.RenderStepped:Connect(function()
		if not shoulder or not shoulder.Parent then
			stopAiming()
			return
		end
		local root = character:FindFirstChild("HumanoidRootPart")
		if not root then
			return
		end

		-- where the camera looks, in the character's own space
		local look = root.CFrame:ToObjectSpace(camera.CFrame).LookVector
		local pitch = math.asin(math.clamp(look.Y, -1, 1))
		local yaw = math.asin(math.clamp(look.X, -1, 1))

		shoulder.C0 = CFrame.new(basePosition) * CFrame.Angles(pitch, -yaw, 0)
	end)
end)

tool.Unequipped:Connect(stopAiming)

player.CharacterRemoving:Connect(stopAiming)

tool.Activated:Connect(function()
	if flashRequest then
		flashRequest:FireServer()
	end
end)
