--!strict
-- DialogueClient.client.lua — Client listener for NPC dialogue updates & flag sync (v6.0.0)

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local remote = ReplicatedStorage:WaitForChild("ArenaDialogueEvent", 15) :: RemoteEvent?
local syncedFlags: { [string]: boolean } = {}

if remote then
	remote.OnClientEvent:Connect(function(payload: any)
		if type(payload) == "table" and payload.action == "sync_flags" and type(payload.flags) == "table" then
			syncedFlags = payload.flags
		end
	end)
end
