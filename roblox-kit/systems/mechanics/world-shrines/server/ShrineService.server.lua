--!strict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local remote = ReplicatedStorage:FindFirstChild("ShrineActivatedRemote") :: RemoteEvent?
if not remote then
	local r = Instance.new("RemoteEvent")
	r.Name = "ShrineActivatedRemote"
	r.Parent = ReplicatedStorage
	remote = r
end

local lastActivated: { [Player]: number } = {}

Players.PlayerRemoving:Connect(function(player: Player)
	lastActivated[player] = nil
end)
