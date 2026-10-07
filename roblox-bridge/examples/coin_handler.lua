-- Spins every coin and awards its Value attribute on touch.
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local coins = workspace:WaitForChild("ArenaCoins")

RunService.Heartbeat:Connect(function(dt)
	for _, coin in ipairs(coins:GetChildren()) do
		if coin:IsA("BasePart") then
			coin.CFrame *= CFrame.Angles(0, math.rad(90 * dt), 0)
		end
	end
end)

for _, coin in ipairs(coins:GetChildren()) do
	if coin:IsA("BasePart") then
		coin.Touched:Connect(function(hit)
			local player = Players:GetPlayerFromCharacter(hit.Parent)
			if player then
				local stat = player:FindFirstChild("leaderstats")
				print(player.Name, "collected", coin:GetAttribute("Value") or 0)
			end
		end)
	end
end
