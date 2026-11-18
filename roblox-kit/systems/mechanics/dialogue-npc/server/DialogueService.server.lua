--!strict
-- DialogueService.server.lua — Server-authoritative NPC dialogue session & flag handler (v6.0.0)

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local remote = ReplicatedStorage:FindFirstChild("ArenaDialogueEvent") :: RemoteEvent?
if not remote then
	local created = Instance.new("RemoteEvent")
	created.Name = "ArenaDialogueEvent"
	created.Parent = ReplicatedStorage
	remote = created
end

local playerFlags: { [number]: { [string]: boolean } } = {}

if remote then
	remote.OnServerEvent:Connect(function(player: Player, payload: any)
		if type(payload) ~= "table" then
			return
		end
		local flags = playerFlags[player.UserId]
		if not flags then
			flags = {}
			playerFlags[player.UserId] = flags
		end
		if type(payload.setFlag) == "string" and #payload.setFlag <= 64 then
			flags[payload.setFlag] = true
		end
		remote:FireClient(player, {
			action = "sync_flags",
			flags = flags,
			lastTree = payload.treeId,
		})
	end)
end
