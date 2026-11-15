--!strict
-- CutsceneService.server.lua — Broadcasts cutscene triggers to one or all players (v6.0.0)

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local remote = ReplicatedStorage:FindFirstChild("ArenaCutsceneEvent") :: RemoteEvent?
if not remote then
	local created = Instance.new("RemoteEvent")
	created.Name = "ArenaCutsceneEvent"
	created.Parent = ReplicatedStorage
	remote = created
end

local CutsceneService = {}

function CutsceneService.PlayForAll(cutsceneId: string)
	if remote then
		remote:FireAllClients({ action = "play", id = cutsceneId, timestamp = os.clock() })
	end
end

function CutsceneService.PlayForPlayer(player: Player, cutsceneId: string)
	if remote then
		remote:FireClient(player, { action = "play", id = cutsceneId, timestamp = os.clock() })
	end
end

return CutsceneService
