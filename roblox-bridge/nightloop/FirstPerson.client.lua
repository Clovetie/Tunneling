--!nonstrict
--[[
	NightLoop · forced first person   StarterPlayer > StarterPlayerScripts

	Locks the camera to first person and keeps your own body rendered, so
	looking down shows legs and torso instead of nothing.

	Three things the naive version gets wrong, all fixed here:

	1. HAIR AND HATS IN YOUR FACE.
	   Hiding only the part literally named "Head" leaves every head accessory
	   rendering a few studs in front of the camera. Accessories are found by
	   following the Handle's weld back to the body part it is attached to, so
	   anything welded to the head is hidden no matter what it is called.

	2. FIGHTING THE ENGINE EVERY FRAME.
	   Roblox's TransparencyController re-applies LocalTransparencyModifier = 1
	   to the whole character continuously in first person, which is why a
	   one-shot loop "doesn't work". Rather than re-walking GetDescendants()
	   every RenderStepped, each part gets a GetPropertyChangedSignal watcher
	   that pins the value back the instant the engine changes it.
	   See devforum 2499986 and 3395170.

	3. CAMERA INSIDE THE SKULL.
	   LockFirstPerson puts the camera at the centre of the head, so you look
	   out from inside your own face and the torso clips the near plane. The
	   camera is pushed forward to roughly where the eyes are, and pulled back
	   automatically when you walk into a wall so it never pokes through.

	Standalone on purpose: no dependency on the NightLoop package. Flip ENABLED
	to false to disable.
]]

local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local ENABLED = true
local SHOW_OWN_BODY = true

-- how far forward of the head centre the camera sits, in studs
local EYE_FORWARD = 0.9
-- never let the camera get closer than this to a wall
local WALL_SKIN = 0.35

if not ENABLED then
	return
end

local player = Players.LocalPlayer
player.CameraMode = Enum.CameraMode.LockFirstPerson

-- ------------------------------------------------------------ accessories

-- Follow an accessory's Handle weld back to the body part it is attached to.
-- Definitive, unlike guessing from attachment names.
local function accessoryAttachedTo(accessory)
	local handle = accessory:FindFirstChild("Handle")
	if not handle then
		return nil
	end
	for _, d in ipairs(handle:GetDescendants()) do
		if d:IsA("Weld") or d:IsA("WeldConstraint") or d:IsA("Motor6D") then
			local a, b = d.Part0, d.Part1
			if a == handle then
				return b
			elseif b == handle then
				return a
			end
		end
	end
	-- fallback for accessories welded by attachment name alone
	local attachment = handle:FindFirstChildWhichIsA("Attachment")
	if attachment then
		local name = string.lower(attachment.Name)
		for _, key in ipairs({ "hat", "hair", "face", "neck" }) do
			if string.find(name, key, 1, true) then
				return "HEADISH"
			end
		end
	end
	return nil
end

local function shouldHide(instance, head)
	if instance == head then
		return true
	end
	-- the face decal lives on the head
	if instance:IsA("Decal") and instance.Parent == head then
		return true
	end
	local accessory = instance:FindFirstAncestorWhichIsA("Accessory")
	if accessory then
		local attachedTo = accessoryAttachedTo(accessory)
		if attachedTo == head or attachedTo == "HEADISH" then
			return true
		end
	end
	return false
end

-- ------------------------------------------------------------ transparency

local function bind(character)
	local humanoid = character:WaitForChild("Humanoid", 10)
	local head = character:WaitForChild("Head", 10)
	if not humanoid or not head then
		return
	end

	local connections = {}
	local function cleanup()
		for _, c in ipairs(connections) do
			pcall(function() c:Disconnect() end)
		end
		table.clear(connections)
		pcall(function() RunService:UnbindFromRenderStep("NightLoopFirstPerson") end)
	end

	if SHOW_OWN_BODY then
		local function pin(instance)
			if not (instance:IsA("BasePart") or instance:IsA("Decal")) then
				return
			end
			-- the engine deliberately leaves tools alone; so do we
			if instance:FindFirstAncestorWhichIsA("Tool") then
				return
			end

			local hidden = shouldHide(instance, head)
			local function apply()
				-- not a hardcoded 0: a part that is genuinely transparent must
				-- stay transparent. LocalTransparencyModifier only ever ADDS.
				instance.LocalTransparencyModifier = hidden and 1 or instance.Transparency
			end
			apply()
			table.insert(connections,
				instance:GetPropertyChangedSignal("LocalTransparencyModifier"):Connect(apply))
			table.insert(connections,
				instance:GetPropertyChangedSignal("Transparency"):Connect(apply))
		end

		for _, d in ipairs(character:GetDescendants()) do
			pin(d)
		end
		table.insert(connections, character.DescendantAdded:Connect(function(d)
			-- accessories weld a frame after they are parented
			task.defer(pin, d)
		end))
	end

	-- --------------------------------------------------------- camera offset
	-- Push the camera out of the head to roughly eye position, and pull it
	-- back when it would clip through geometry.
	-- Unbind first: re-binding the same name throws, which would break the
	-- whole script on the first respawn.
	pcall(function() RunService:UnbindFromRenderStep("NightLoopFirstPerson") end)
	RunService:BindToRenderStep("NightLoopFirstPerson",
		Enum.RenderPriority.Camera.Value - 1, function()
		if not character.Parent or humanoid.Health <= 0 then
			return
		end
		local camera = workspace.CurrentCamera
		if not camera or not head.Parent then
			return
		end

		local forward = EYE_FORWARD
		local params = RaycastParams.new()
		params.FilterType = Enum.RaycastFilterType.Exclude
		params.FilterDescendantsInstances = { character }
		params.IgnoreWater = true

		local hit = workspace:Raycast(head.Position,
			camera.CFrame.LookVector * (EYE_FORWARD + WALL_SKIN), params)
		if hit then
			forward = math.max(0, (hit.Position - head.Position).Magnitude - WALL_SKIN)
		end

		humanoid.CameraOffset = Vector3.new(0, 0, -forward)
	end)

	table.insert(connections, humanoid.Died:Connect(cleanup))
	table.insert(connections, character.AncestryChanged:Connect(function(_, parent)
		if not parent then
			cleanup()
		end
	end))
end

if player.Character then
	task.spawn(bind, player.Character)
end
player.CharacterAdded:Connect(bind)

-- keep the lock if something else resets it
player:GetPropertyChangedSignal("CameraMode"):Connect(function()
	if player.CameraMode ~= Enum.CameraMode.LockFirstPerson then
		player.CameraMode = Enum.CameraMode.LockFirstPerson
	end
end)
