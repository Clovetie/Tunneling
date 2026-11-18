--!strict
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local bossRemote = ReplicatedStorage:FindFirstChild("BossStateRemote") :: RemoteEvent?
if not bossRemote then
	local r = Instance.new("RemoteEvent")
	r.Name = "BossStateRemote"
	r.Parent = ReplicatedStorage
	bossRemote = r
end
