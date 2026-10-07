--!nonstrict
--[[
	NightLoop · Net
	Owns the RemoteEvents. Creates them on demand so the package carries no
	pre-built instances and can be dropped into any place.
]]

local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Net = {}

local FOLDER_NAME = "NightLoopRemotes"

local function ensureFolder()
	local folder = ReplicatedStorage:FindFirstChild(FOLDER_NAME)
	if not folder then
		folder = Instance.new("Folder")
		folder.Name = FOLDER_NAME
		folder.Parent = ReplicatedStorage
	end
	return folder
end

local function ensureRemote(name)
	local folder = ensureFolder()
	local remote = folder:FindFirstChild(name)
	if not remote then
		remote = Instance.new("RemoteEvent")
		remote.Name = name
		remote.Parent = folder
	end
	return remote
end

-- server -> client, ~4/sec: the authoritative night state for the HUD
function Net.State()
	return ensureRemote("NightState")
end

-- server -> client, one-off cues (a whisper, a scare, a phase sting)
function Net.Cue()
	return ensureRemote("NightCue")
end

-- client -> server, "I pressed the flash button". The server decides if it fires.
function Net.FlashRequest()
	return ensureRemote("FlashRequest")
end

function Net.BroadcastState(state)
	Net.State():FireAllClients(state)
end

function Net.BroadcastCue(cue)
	Net.Cue():FireAllClients(cue)
end

function Net.SendCue(player, cue)
	if player then
		Net.Cue():FireClient(player, cue)
	else
		Net.Cue():FireAllClients(cue)
	end
end

return Net
