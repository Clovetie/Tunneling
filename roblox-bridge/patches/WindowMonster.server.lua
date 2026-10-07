-- WindowMonster / JumpscareController  (Script, server)  ServerScriptService
--
-- CONTINUOUS BEAM MODEL
-- Hold the flashlight on the monster for BEAM_REPEL_TIME seconds of cumulative
-- exposure and it retreats. Look away and the exposure bleeds off at
-- BEAM_DECAY_RATE, so sweeping a room works but a stray flick does not.
--
-- Fully server-authoritative. The server reads the SpotLight's own .Enabled and
-- raycasts from the tool's LightOrigin attachment, so there is no RemoteEvent to
-- fire and nothing for an exploiter to spoof. (The old FlashEvent was never fired
-- by anything in the place, which is why the mechanic had never worked.)

local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local ServerStorage = game:GetService("ServerStorage")
local Workspace = game:GetService("Workspace")

local SPOTS_FOLDER = Workspace:WaitForChild("Spots")
local MONSTER_TEMPLATE = ServerStorage:WaitForChild("jumpscare")

-- ======== CONFIG ========
local RETREAT_COOLDOWN = 17      -- seconds hidden after being repelled
local TELEPORT_INTERVAL_MIN = 6
local TELEPORT_INTERVAL_MAX = 13

-- beam shape is READ FROM THE SPOTLIGHT ITSELF (Angle / Range) so the hitbox can
-- never drift away from the cone the player can actually see. Tune the light in
-- Studio and the gameplay follows. These are only fallbacks if the light is gone.
local FALLBACK_HALF_ANGLE = 36   -- degrees
local FALLBACK_RANGE = 15        -- studs
local RANGE_TOLERANCE = 1.05     -- allow 5% past the visible falloff

-- beam timing
local BEAM_REPEL_TIME = 1.0      -- seconds of light needed to repel
local BEAM_DECAY_RATE = 1.5      -- exposure lost per second once off target
local BEAM_CHECK_INTERVAL = 0.08 -- seconds between beam tests
local FADE_MAX = 0.65            -- how transparent it gets at full exposure
-- ========================

local spotsCache = {}

local function getSpots()
	if #spotsCache > 0 then
		return spotsCache
	end
	for _, v in ipairs(SPOTS_FOLDER:GetChildren()) do
		if v:IsA("BasePart") then
			table.insert(spotsCache, v)
		end
	end
	return spotsCache
end

SPOTS_FOLDER.ChildAdded:Connect(function()
	table.clear(spotsCache)
end)
SPOTS_FOLDER.ChildRemoved:Connect(function()
	table.clear(spotsCache)
end)

local function farthestSpotFrom(pos, spots)
	local best, bestDist = nil, -math.huge
	for _, s in ipairs(spots) do
		local d = (s.Position - pos).Magnitude
		if d > bestDist then
			bestDist = d
			best = s
		end
	end
	return best
end

local function placeAt(model, spot)
	if not model or not model.Parent or not spot then
		return
	end
	-- PivotTo replaces the deprecated SetPrimaryPartCFrame
	model:PivotTo(spot.CFrame + Vector3.new(0, 0.5, 0))
end

local function setModelHidden(model, hidden)
	if not model then
		return
	end
	for _, part in ipairs(model:GetDescendants()) do
		if part:IsA("BasePart") then
			part.Anchored = true
			part.CanCollide = not hidden
			part.Transparency = hidden and 1 or 0
		elseif part:IsA("Decal") or part:IsA("Texture") then
			part.Transparency = hidden and 1 or 0
		end
	end
end

-- visual feedback while the beam is burning it away
local function setModelFade(model, alpha)
	if not model then
		return
	end
	for _, part in ipairs(model:GetDescendants()) do
		if part:IsA("BasePart") then
			part.Transparency = alpha
		elseif part:IsA("Decal") or part:IsA("Texture") then
			part.Transparency = alpha
		end
	end
end

-- Returns origin CFrame + position for a player's LIT, EQUIPPED flashlight.
-- Equipped means the Tool is parented to the Character, so a torch sitting in
-- the Backpack cannot repel anything.
local function getActiveBeam(player)
	local character = player.Character
	if not character then
		return nil
	end

	local tool = character:FindFirstChild("Flashlight")
	if not tool or not tool:IsA("Tool") then
		return nil
	end

	local lightPart = tool:FindFirstChild("Light")
	if not lightPart then
		return nil
	end

	local origin = lightPart:FindFirstChild("LightOrigin")
	if not origin or not origin:IsA("Attachment") then
		return nil
	end

	-- the actual beam, by name — the old getToolLightColor() grabbed whichever
	-- of Light/Shadow/SurfaceLight enumerated first and never checked Enabled
	local spot = origin:FindFirstChild("Light")
	if not spot or not spot:IsA("Light") or not spot.Enabled then
		return nil
	end

	return origin.WorldCFrame, origin.WorldPosition, spot
end

-- centre ray + two concentric rings (4 rays each) that genuinely reach the cone
-- edge. The previous version scaled every sample by 0.4/0.6, so a nominal 20 deg
-- cone only ever tested 12.3 deg and the monster could be visibly lit without
-- registering a hit.
local RING_RADII = { 0.55, 0.95 }
local RING_SAMPLES = 4

local function buildConeDirections(centerCFrame, halfAngleDeg)
	local dir = centerCFrame.LookVector
	local right = centerCFrame.RightVector
	local up = centerCFrame.UpVector
	local spread = math.tan(math.rad(halfAngleDeg))

	local dirs = { dir }
	for ringIndex, radius in ipairs(RING_RADII) do
		-- offset alternate rings so the samples interleave instead of stacking
		local phase = (ringIndex - 1) * (math.pi / RING_SAMPLES)
		for i = 0, RING_SAMPLES - 1 do
			local t = phase + (i / RING_SAMPLES) * math.pi * 2
			local x = math.cos(t) * spread * radius
			local y = math.sin(t) * spread * radius
			table.insert(dirs, (dir + right * x + up * y).Unit)
		end
	end
	return dirs
end

local function beamHitsMonster(player, monster)
	local originCFrame, originPos, spot = getActiveBeam(player)
	if not originCFrame then
		return false
	end

	local halfAngle = spot and (spot.Angle / 2) or FALLBACK_HALF_ANGLE
	local range = (spot and spot.Range or FALLBACK_RANGE) * RANGE_TOLERANCE

	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude -- Blacklist is deprecated
	params.IgnoreWater = true
	params.FilterDescendantsInstances = { player.Character }

	for _, dir in ipairs(buildConeDirections(originCFrame, halfAngle)) do
		local result = Workspace:Raycast(originPos, dir * range, params)
		if result and result.Instance and result.Instance:IsDescendantOf(monster) then
			return true
		end
	end
	return false
end

local function spawnMonsterAtSpot(spot)
	local clone = MONSTER_TEMPLATE:Clone()
	clone.Parent = Workspace

	if not clone.PrimaryPart then
		clone.PrimaryPart = clone:FindFirstChild("Body")
			or clone:FindFirstChild("HumanoidRootPart")
			or clone:FindFirstChildWhichIsA("BasePart")
	end

	for _, p in ipairs(clone:GetDescendants()) do
		if p:IsA("BasePart") then
			p.Anchored = true
			p.CanCollide = true
		end
	end

	placeAt(clone, spot)
	return clone
end

-- ======== main ========
task.spawn(function()
	task.wait(0.5)

	local spots = getSpots()
	if #spots == 0 then
		warn("[WindowMonster] No Spots found under Workspace.Spots")
		return
	end

	local currentSpot = spots[math.random(1, #spots)]
	local monster = spawnMonsterAtSpot(currentSpot)
	local state = "idle" -- "idle" | "hidden"
	local retreatUntil = 0
	local exposure = 0

	local function repel(byPlayer)
		state = "hidden"
		exposure = 0
		retreatUntil = os.clock() + RETREAT_COOLDOWN
		setModelHidden(monster, true)

		local spotList = getSpots()
		local root = byPlayer
			and byPlayer.Character
			and byPlayer.Character:FindFirstChild("HumanoidRootPart")
		if root then
			-- old code used Character.PrimaryPart, which is often nil
			local far = farthestSpotFrom(root.Position, spotList) or currentSpot
			placeAt(monster, far)
			currentSpot = far
		end
	end

	-- wandering
	task.spawn(function()
		while monster and monster.Parent do
			if state == "hidden" then
				if os.clock() >= retreatUntil then
					setModelHidden(monster, false)
					state = "idle"
				end
				task.wait(0.25)
			else
				task.wait(math.random(TELEPORT_INTERVAL_MIN, TELEPORT_INTERVAL_MAX))
				if not monster or not monster.Parent then
					break
				end
				if state == "idle" then
					local spotList = getSpots()
					local newSpot = spotList[math.random(1, #spotList)]
					local tries = 0
					while newSpot == currentSpot and #spotList > 1 and tries < 8 do
						newSpot = spotList[math.random(1, #spotList)]
						tries += 1
					end
					placeAt(monster, newSpot)
					currentSpot = newSpot
					exposure = 0
				end
			end
		end
	end)

	-- beam exposure
	local accum = 0
	RunService.Heartbeat:Connect(function(dt)
		if not monster or not monster.Parent or state ~= "idle" then
			return
		end

		accum += dt
		if accum < BEAM_CHECK_INTERVAL then
			return
		end
		local step = accum
		accum = 0

		local litBy = nil
		for _, player in ipairs(Players:GetPlayers()) do
			if beamHitsMonster(player, monster) then
				litBy = player
				break
			end
		end

		if litBy then
			exposure = math.min(BEAM_REPEL_TIME, exposure + step)
		else
			exposure = math.max(0, exposure - step * BEAM_DECAY_RATE)
		end

		setModelFade(monster, (exposure / BEAM_REPEL_TIME) * FADE_MAX)

		if exposure >= BEAM_REPEL_TIME then
			repel(litBy)
		end
	end)

	-- respawn if something destroys it
	monster.AncestryChanged:Connect(function(_, parent)
		if not parent then
			task.delay(2 + math.random() * 3, function()
				local spotList = getSpots()
				if #spotList == 0 then
					return
				end
				currentSpot = spotList[math.random(1, #spotList)]
				monster = spawnMonsterAtSpot(currentSpot)
				state = "idle"
				retreatUntil = 0
				exposure = 0
			end)
		end
	end)

	print("[WindowMonster] ready — beam model, "
		.. BEAM_REPEL_TIME .. "s to repel, " .. #spots .. " spots")
end)
