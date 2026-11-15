--!strict
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local bossRemote = ReplicatedStorage:WaitForChild("BossStateRemote", 10)
if bossRemote and bossRemote:IsA("RemoteEvent") then
	bossRemote.OnClientEvent:Connect(function(_state: string, _hpRatio: number)
		-- Updates Boss Health Bar UDim2 scale and triggers phase transition VFX
	end)
end
