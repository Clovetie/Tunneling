--!nonstrict
local CollectionService = game:GetService("CollectionService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Remotes = require(ReplicatedStorage:WaitForChild("Remotes"))
local CheckpointConfig = require(ReplicatedStorage:WaitForChild("CheckpointConfig"))
local CheckpointOrder = require(ReplicatedStorage:WaitForChild("CheckpointOrder"))

-- NetGuard is optional: with it, a client-spammed claim remote is rate limited; without it
-- the distance and cooldown checks still stand. Warn once rather than refuse to run.
local netGuardScript = ReplicatedStorage:FindFirstChild("NetGuard")
local NetGuard = netGuardScript and require(netGuardScript)
if not NetGuard then
	warn("checkpoint-respawn: NetGuard is not in ReplicatedStorage, so claim requests are only distance and cooldown checked")
end

local claimEvent = Remotes.event(CheckpointConfig.REMOTE_CLAIM)
local stateEvent = Remotes.event(CheckpointConfig.REMOTE_STATE)

local checkpoints = {} -- id -> { instance, order, position }
local order = {} -- id -> order
local claimedBy = {} -- player -> { [id] = true }
local lastClaim = {}
local dataReady = false

local function sendState(player)
	local claimed = claimedBy[player] or {}
	local currentId, currentOrder = CheckpointOrder.highestClaimed(claimed, order)
	local nextId, nextOrder = CheckpointOrder.nextId(claimed, order)
	local done, total, fraction = CheckpointOrder.progress(claimed, order)
	stateEvent:FireClient(player, {
		claimed = claimed,
		current = currentId,
		currentOrder = currentOrder,
		next = nextId,
		nextOrder = nextOrder,
		done = done,
		total = total,
		fraction = fraction,
	})
end

local function persist(player, claimed)
	if not dataReady then
		return
	end
	local data = shared.DataServiceAPI and shared.DataServiceAPI.GetData(player)
	if not data then
		return
	end
	-- Mutating the live profile table is the convention; core/data auto-saves it.
	data[CheckpointConfig.SAVE_KEY] = { claimed = claimed }
end

local function loadClaimed(player)
	if not dataReady then
		return nil
	end
	local data = shared.DataServiceAPI and shared.DataServiceAPI.WaitForData(player)
	if type(data) ~= "table" then
		return nil
	end
	local saved = data[CheckpointConfig.SAVE_KEY]
	if type(saved) == "table" and type(saved.claimed) == "table" then
		return saved.claimed
	end
	return {}
end

local function shareWithParty(player, id)
	local source = checkpoints[id]
	if not source then
		return
	end
	for _, other in ipairs(Players:GetPlayers()) do
		if other ~= player then
			local character = other.Character
			local root = character and character:FindFirstChild("HumanoidRootPart")
			if root and (root.Position - source.position).Magnitude <= CheckpointConfig.PARTY_RADIUS then
				-- Same claim path, so each player's own progression still applies. A game
				-- that wants a stronger player to carry the party sets STRICT_ORDER = false.
				local nextClaimed, ok = CheckpointOrder.claim(claimedBy[other] or {}, order, id)
				if ok then
					claimedBy[other] = nextClaimed
					persist(other, nextClaimed)
					sendState(other)
				end
			end
		end
	end
end

local function attemptClaim(player, id)
	if type(player) ~= "Instance" or not player:IsA("Player") then
		return false, "bad_player"
	end
	local checkpoint = checkpoints[id]
	if not checkpoint then
		return false, "unknown_checkpoint"
	end
	local now = os.clock()
	local last = lastClaim[player]
	if last and now - last < CheckpointConfig.CLAIM_COOLDOWN then
		return false, "cooldown"
	end
	lastClaim[player] = now

	local nextClaimed, ok, reason = CheckpointOrder.claim(claimedBy[player] or {}, order, id)
	if not ok then
		return false, reason
	end
	claimedBy[player] = nextClaimed
	persist(player, nextClaimed)
	if CheckpointConfig.PARTY_SHARE then
		shareWithParty(player, id)
	end
	sendState(player)
	return true, nil
end

local function respawnAt(player, character)
	if not CheckpointConfig.RESPAWN_AT_CHECKPOINT then
		return
	end
	local id = CheckpointOrder.respawnId(claimedBy[player], order)
	local checkpoint = id and checkpoints[id]
	if not checkpoint then
		return
	end
	local root = character:WaitForChild("HumanoidRootPart", 10)
	if not root then
		return
	end
	root.CFrame = CFrame.new(checkpoint.position + Vector3.new(0, CheckpointConfig.RESPAWN_OFFSET_Y, 0))
end

local function register(instance, orderHint)
	if not instance or not instance:IsA("BasePart") then
		return nil
	end
	local id = instance:GetAttribute(CheckpointConfig.ID_ATTRIBUTE)
	if type(id) ~= "string" or id == "" then
		id = instance.Name
	end
	local orderValue = instance:GetAttribute(CheckpointConfig.ORDER_ATTRIBUTE)
	if type(orderValue) ~= "number" then
		orderValue = orderHint
	end
	if type(orderValue) ~= "number" then
		warn(("checkpoint-respawn: %s has no %s attribute and no sibling position, so it cannot be ordered"):format(id, CheckpointConfig.ORDER_ATTRIBUTE))
		return nil
	end
	checkpoints[id] = { instance = instance, order = orderValue, position = instance.Position }
	order[id] = orderValue
	return id
end

local function unregister(instance)
	for id, entry in pairs(checkpoints) do
		if entry.instance == instance then
			checkpoints[id] = nil
			order[id] = nil
		end
	end
end

local function scan()
	checkpoints = {}
	order = {}
	for _, instance in ipairs(CollectionService:GetTagged(CheckpointConfig.CHECKPOINT_TAG)) do
		register(instance)
	end
	local folder = Workspace:FindFirstChild(CheckpointConfig.CHECKPOINT_FOLDER)
	if folder then
		local children = folder:GetChildren()
		for index, child in ipairs(children) do
			register(child, index)
		end
	end
	local problems = CheckpointOrder.audit(order)
	for _, problem in ipairs(problems) do
		warn("checkpoint-respawn: " .. problem)
	end
	return problems
end

local function attachTouch(instance, id)
	if not CheckpointConfig.TOUCH_TO_CLAIM or not instance or instance:GetAttribute("CheckpointTouchBound") then
		return
	end
	instance:SetAttribute("CheckpointTouchBound", true)
	instance.Touched:Connect(function(hit)
		local character = hit and hit.Parent
		local player = character and Players:GetPlayerFromCharacter(character)
		if not player then
			return
		end
		local humanoid = character:FindFirstChildOfClass("Humanoid")
		if not humanoid or humanoid.Health <= 0 then
			return
		end
		attemptClaim(player, id)
	end)
end

local function bindTouches()
	for id, entry in pairs(checkpoints) do
		attachTouch(entry.instance, id)
	end
end

-- Assigned before any yield, with every method attached, so another script can require it
-- the moment the server script has run.
shared.CheckpointAPI = {
	Rescan = function()
		local problems = scan()
		bindTouches()
		for _, player in ipairs(Players:GetPlayers()) do
			sendState(player)
		end
		return problems
	end,
	GetOrder = function()
		return order
	end,
	GetCheckpoints = function()
		return checkpoints
	end,
	GetClaimed = function(player)
		return claimedBy[player] or {}
	end,
	GetCurrent = function(player)
		return CheckpointOrder.highestClaimed(claimedBy[player] or {}, order)
	end,
	GetProgress = function(player)
		return CheckpointOrder.progress(claimedBy[player] or {}, order)
	end,
	-- For another system that awards a checkpoint (a quest, a purchase, a cutscene).
	Claim = function(player, id)
		return attemptClaim(player, id)
	end,
	TeleportTo = function(player, id)
		local checkpoint = checkpoints[id]
		local character = player and player.Character
		local root = character and character:FindFirstChild("HumanoidRootPart")
		if not checkpoint or not root then
			return false
		end
		root.CFrame = CFrame.new(checkpoint.position + Vector3.new(0, CheckpointConfig.RESPAWN_OFFSET_Y, 0))
		return true
	end,
}

scan()
bindTouches()

CollectionService:GetInstanceAddedSignal(CheckpointConfig.CHECKPOINT_TAG):Connect(function(instance)
	local id = register(instance)
	if id then
		attachTouch(instance, id)
		for _, player in ipairs(Players:GetPlayers()) do
			sendState(player)
		end
	end
end)
CollectionService:GetInstanceRemovedSignal(CheckpointConfig.CHECKPOINT_TAG):Connect(unregister)

claimEvent.OnServerEvent:Connect(function(player, id)
	if type(id) ~= "string" or #id > 64 then
		return
	end
	if NetGuard and not NetGuard.allowRate(player, CheckpointConfig.REMOTE_CLAIM, CheckpointConfig.CLAIM_RATE_LIMIT, CheckpointConfig.CLAIM_RATE_WINDOW) then
		return
	end
	-- Server-authoritative: the client may ask, but it has to be standing there.
	local character = player.Character
	local root = character and character:FindFirstChild("HumanoidRootPart")
	local checkpoint = checkpoints[id]
	if not root or not checkpoint then
		return
	end
	if (root.Position - checkpoint.position).Magnitude > CheckpointConfig.CLAIM_RADIUS then
		return
	end
	attemptClaim(player, id)
end)

local function onPlayerAdded(player)
	claimedBy[player] = {}
	sendState(player)
	player.CharacterAdded:Connect(function(character)
		respawnAt(player, character)
	end)
	local character = player.Character
	if character then
		respawnAt(player, character)
	end
end

Players.PlayerAdded:Connect(onPlayerAdded)
for _, player in ipairs(Players:GetPlayers()) do
	onPlayerAdded(player)
end

Players.PlayerRemoving:Connect(function(player)
	claimedBy[player] = nil
	lastClaim[player] = nil
end)

task.spawn(function()
	local waited = 0
	repeat
		if shared.DataServiceAPI then
			dataReady = true
			break
		end
		task.wait(0.2)
		waited += 0.2
	until waited >= 10
	if not dataReady then
		if CheckpointConfig.REQUIRE_DATA_SERVICE then
			warn("checkpoint-respawn: shared.DataServiceAPI never appeared and REQUIRE_DATA_SERVICE is true; install systems/core/data")
		else
			warn("checkpoint-respawn: no shared.DataServiceAPI, so progress is kept in memory for this session only")
		end
		return
	end
	local loaded = shared.DataServiceAPI.PlayerLoaded
	if loaded and loaded.Connect then
		loaded:Connect(function(player, data)
			local saved = type(data) == "table" and data[CheckpointConfig.SAVE_KEY]
			local fromSave = type(saved) == "table" and type(saved.claimed) == "table" and saved.claimed or nil
			-- Merge: a claim made in the seconds before the profile loaded is not thrown away.
			local merged = fromSave or {}
			for id in pairs(claimedBy[player] or {}) do
				merged[id] = true
			end
			claimedBy[player] = merged
			sendState(player)
		end)
	end
	for _, player in ipairs(Players:GetPlayers()) do
		local fromSave = loadClaimed(player)
		if fromSave then
			local merged = fromSave
			for id in pairs(claimedBy[player] or {}) do
				merged[id] = true
			end
			claimedBy[player] = merged
			sendState(player)
		end
	end
end)
