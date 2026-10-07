-- JumpscareController (ServerScriptService)
-- Uses an actual light raycast (multi-ray cone) and checks light color.
-- Monster is a Model whose PrimaryPart is a BasePart.

local ServerStorage = game:GetService("ServerStorage")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local SPOTS_FOLDER = Workspace:WaitForChild("Spots")
local MONSTER_TEMPLATE = ServerStorage:WaitForChild("jumpscare")
local FLASH_EVENT_NAME = "FlashEvent"

local FLASH_RANGE = 40         -- max distance for rays
local RETREAT_COOLDOWN = 17     -- seconds hidden before reappearing
local TELEPORT_INTERVAL_MIN = 6
local TELEPORT_INTERVAL_MAX = 13

-- Light raycast parameters
local CONE_ANGLE_DEGREES = 20  -- half-angle of the cone (flashlight spread)
local RAY_SAMPLES = 9          -- number of rays (1 center + others around)
local COLOR_TOLERANCE = 0.12   -- how close colors must be (0..1)

-- Acceptable color (change to taste). Example: white flash required.
local REQUIRED_COLOR = Color3.fromRGB(255, 255, 255)

-- Create RemoteEvent if missing
local flashEvent = ReplicatedStorage:FindFirstChild(FLASH_EVENT_NAME)
if not flashEvent then
	flashEvent = Instance.new("RemoteEvent")
	flashEvent.Name = FLASH_EVENT_NAME
	flashEvent.Parent = ReplicatedStorage
end

local function getSpots()
	local spots = {}
	for _, v in ipairs(SPOTS_FOLDER:GetChildren()) do
		if v:IsA("BasePart") then
			table.insert(spots, v)
		end
	end
	return spots
end

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

local function teleportModelToSpot(model, spot)
	if not model or not model.Parent or not model.PrimaryPart or not spot then return end
	model:SetPrimaryPartCFrame(spot.CFrame + Vector3.new(0, 0.5, 0))
end

local function setModelHidden(model, hidden)
	if not model then return end
	for _, part in ipairs(model:GetDescendants()) do
		if part:IsA("BasePart") then
			part.CanCollide = not hidden
			if hidden then
				part.Transparency = 1
			else
				part.Transparency = 0
			end
			part.Anchored = true
		elseif part:IsA("Decal") or part:IsA("Texture") then
			part.Transparency = hidden and 1 or 0
		end
	end
end

-- color distance helper (returns 0 = identical, larger = more different)
local function colorDistance(a, b)
	return math.abs(a.R - b.R) + math.abs(a.G - b.G) + math.abs(a.B - b.B)
end

-- Attempts to find a gameplay flashlight origin & direction for the player:
-- Expected setup on player: a Tool named "Flashlight" with an Attachment "LightOrigin" under its Handle,
-- and a SpotLight/PointLight/SurfaceLight as a child (used to read Color).
local function getPlayerLightOriginAndCFrame(player)
	if not player.Character then return end
	-- look for a Tool named "Flashlight" in character or Backpack
	local tool = player.Character:FindFirstChild("Flashlight") or player:FindFirstChild("Backpack") and player.Backpack:FindFirstChild("Flashlight")
	if not tool then
		-- fallback: use Head CFrame
		local head = player.Character:FindFirstChild("Head")
		if head then
			return head.CFrame, head.Position
		end
		return nil
	end

	-- origin attachment
	local originAttachment = nil
	local handle = tool:FindFirstChild("Light")
	if handle then
		originAttachment = handle:FindFirstChild("LightOrigin") -- Attachment
		if originAttachment then
			return originAttachment.WorldCFrame, originAttachment.WorldPosition
		else
			-- fallback to handle CFrame
			return handle.CFrame, handle.Position
		end
	end

	return nil
end

-- Get the light instance and its Color property (if present)
local function getToolLightColor(tool)
	if not tool then return nil end
	for _, child in ipairs(tool:GetDescendants()) do
		if child:IsA("SpotLight") or child:IsA("PointLight") or child:IsA("SurfaceLight") then
			return child.Color, child.Parent -- return color and parent (to get origin)
		end
	end
	return nil
end

-- Build sampled ray directions in a cone around centerDir.
local function buildConeDirections(centerCFrame, coneAngleDeg, samples)
	local dir = centerCFrame.LookVector
	local right = centerCFrame.RightVector
	local up = centerCFrame.UpVector
	local angleRad = math.rad(coneAngleDeg)

	local dirs = {}
	-- center ray
	table.insert(dirs, dir)

	-- ring samples (evenly spaced)
	for i = 1, samples - 1 do
		-- polar coordinates on unit circle
		local t = (i - 1) / (samples - 1) * math.pi * 2
		-- produce an offset within cone: use sin of angle to distribute inside
		local u = (i % 3 == 0) and 0.6 or 0.4 -- small subtle variation to cover cone
		local x = math.cos(t) * math.tan(angleRad) * u
		local y = math.sin(t) * math.tan(angleRad) * u
		local sample = (dir + right * x + up * y).Unit
		table.insert(dirs, sample)
	end
	return dirs
end

-- raycast from origin along dir * range, returns raycast result
local function doRay(origin, dir, range, ignoreList)
	local rayParams = RaycastParams.new()
	rayParams.FilterDescendantsInstances = ignoreList or {}
	rayParams.FilterType = Enum.RaycastFilterType.Blacklist
	rayParams.IgnoreWater = true
	local result = Workspace:Raycast(origin, dir * range, rayParams)
	return result
end

-- SERVER-SIDE VALIDATION: simulate a flashlight beam by casting multiple rays from the tool/light origin.
-- Requirements:
--  - One of the rays must hit a descendant of the monster within range.
--  - The flashlight's actual Color (SpotLight/PointLight/SurfaceLight) must match REQUIRED_COLOR within tolerance.
local function validateFlash(player, monster)
	if not player or not player.Character or not monster or not monster.PrimaryPart then return false end

	-- find player's flashlight tool and light color (if present)
	local tool = player.Character:FindFirstChild("Flashlight") or (player:FindFirstChild("Backpack") and player.Backpack:FindFirstChild("Flashlight"))
	local color, colorParent = nil, nil
	if tool then
		color, colorParent = getToolLightColor(tool)
	end

	-- if color exists, compare to REQUIRED_COLOR
	if color then
		if colorDistance(color, REQUIRED_COLOR) > COLOR_TOLERANCE then
			-- color doesn't match required color
			return false
		end
	end
	-- if no light color found, we still allow if player has a "Flashlight" tool but no light object (optional).
	-- if you want to require a light instance, uncomment the next lines:
	-- if not color then return false end

	-- determine origin and central CFrame (direction)
	local originCFrame, originPos = nil, nil
	if tool and colorParent then
		-- prefer light's parent (like handle) as origin if available
		originCFrame = (colorParent:IsA("BasePart") and colorParent.CFrame) or getPlayerLightOriginAndCFrame(player)
		originPos = (colorParent:IsA("BasePart") and colorParent.Position) or (originCFrame and originCFrame.Position)
	else
		local got = getPlayerLightOriginAndCFrame(player)
		if got then
			originCFrame, originPos = got, (got and got.Position)
		end
	end

	-- as a final fallback, use Head
	if not originCFrame then
		local head = player.Character:FindFirstChild("Head")
		if head then
			originCFrame = head.CFrame
			originPos = head.Position
		else
			return false
		end
	end

	-- build directions and raycast
	local dirs = buildConeDirections(originCFrame, CONE_ANGLE_DEGREES, RAY_SAMPLES)
	local ignoreList = {player.Character}
	for _, d in ipairs(dirs) do
		local result = doRay(originPos, d, FLASH_RANGE, ignoreList)
		if result and result.Instance then
			-- valid only if hit monster or its descendants
			if result.Instance:IsDescendantOf(monster) then
				return true
			end
		end
	end

	return false
end

-- create one monster at a given spot
local function spawnMonsterAtSpot(spot)
	local clone = MONSTER_TEMPLATE:Clone()
	clone.Parent = Workspace

	if not clone.PrimaryPart then
		clone.PrimaryPart = clone:FindFirstChild("Body") or clone:FindFirstChild("HumanoidRootPart") or clone:FindFirstChildWhichIsA("BasePart")
	end

	if clone.PrimaryPart and spot then
		clone:SetPrimaryPartCFrame(spot.CFrame + Vector3.new(0, 0.5, 0))
	end

	for _, p in ipairs(clone:GetDescendants()) do
		if p:IsA("BasePart") then
			p.Anchored = true
			p.CanCollide = true
		end
	end

	return clone
end

-- main manager
spawn(function()
	wait(0.5)
	local spots = getSpots()
	if #spots == 0 then
		warn("[JumpscareController] No Spots found under Workspace.Spots")
		return
	end

	local currentSpot = spots[math.random(1, #spots)]
	local monster = spawnMonsterAtSpot(currentSpot)
	local state = "idle" -- "idle", "hidden"
	local retreatUntil = 0

	-- teleport cycle loop (no movement) — randomly teleport to another spot occasionally
	spawn(function()
		while monster and monster.Parent do
			if state == "hidden" then
				-- stay hidden until cooldown
				if tick() >= retreatUntil then
					-- unhide and resume idle
					setModelHidden(monster, false)
					state = "idle"
				end
			else
				local waitTime = math.random(TELEPORT_INTERVAL_MIN, TELEPORT_INTERVAL_MAX)
				wait(waitTime)
				if not monster or not monster.Parent then break end

				local newSpot = spots[math.random(1, #spots)]
				if #spots > 1 then
					local tries = 0
					while newSpot == currentSpot and tries < 8 do
						newSpot = spots[math.random(1, #spots)]
						tries = tries + 1
					end
				end

				teleportModelToSpot(monster, newSpot)
				currentSpot = newSpot
				wait(0.6 + math.random()*1.8)
			end
			wait()
		end
	end)

	-- Flash handling: validate then retreat (hide + teleport to far spot)
	flashEvent.OnServerEvent:Connect(function(player)
		if not monster or not monster.Parent then return end
		if state == "hidden" then return end -- already hidden, ignore
		if validateFlash(player, monster) then
			-- hide immediately
			setModelHidden(monster, true)
			state = "hidden"
			retreatUntil = tick() + RETREAT_COOLDOWN

			-- teleport to farthest spot while hidden
			local playerRoot = player.Character and player.Character.PrimaryPart
			if playerRoot then
				local far = farthestSpotFrom(playerRoot.Position, spots) or currentSpot
				teleportModelToSpot(monster, far)
				currentSpot = far
			end

			-- schedule unhide (redundant safety)
			delay(RETREAT_COOLDOWN + 0.1, function()
				if monster and monster.Parent and state == "hidden" and tick() >= retreatUntil then
					setModelHidden(monster, false)
					state = "idle"
				end
			end)
		end
	end)

	-- respawn if destroyed
	monster.AncestryChanged:Connect(function(child, parent)
		if not parent then
			-- small delay then respawn at random spot
			delay(2 + math.random()*3, function()
				local s = getSpots()
				if #s == 0 then return end
				currentSpot = s[math.random(1, #s)]
				monster = spawnMonsterAtSpot(currentSpot)
				state = "idle"
				retreatUntil = 0
			end)
		end
	end)
end)

