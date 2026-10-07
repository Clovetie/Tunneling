--!nonstrict
--[[
	NightLoop · MonsterRig

	Builds "The Pale Watcher" from primitives at runtime — no uploaded assets,
	no asset IDs, nothing to import. Every part is joined with a Motor6D so the
	rig can be posed by code, which is what MonsterAnimator does.

	Why parts and not a mesh: animating a skinned mesh needs an uploaded
	KeyframeSequence. A Motor6D rig can be animated procedurally from a script,
	so the whole thing ships as source.

	Proportions are deliberately wrong — too tall, arms too long, head too
	narrow. The silhouette is what reads through a window at night.

		local rig = MonsterRig.build()        -- returns a Model
		rig.PrimaryPart                       -- "Root", the only anchored part

	Joints are named and reachable at rig.Joints (a Folder of ObjectValues) or
	via MonsterRig.getJoints(rig) -> { [name] = Motor6D }.
]]

local MonsterRig = {}

local PALE = Color3.fromRGB(176, 170, 158)
local DARK = Color3.fromRGB(104, 100, 94)
local EYE = Color3.fromRGB(236, 214, 150)

-- scale: the player is ~5 studs, this thing is ~8.6
local S = 1

local function part(name, size, color, material, transparency)
	local p = Instance.new("Part")
	p.Name = name
	p.Size = size * S
	p.Color = color or PALE
	p.Material = material or Enum.Material.SmoothPlastic
	p.Transparency = transparency or 0
	p.Anchored = false
	p.CanCollide = false
	p.CanQuery = true
	p.CanTouch = false
	p.TopSurface = Enum.SurfaceType.Smooth
	p.BottomSurface = Enum.SurfaceType.Smooth
	p.Massless = true
	-- remember how it should look, so hide/show never guesses
	p:SetAttribute("BaseTransparency", p.Transparency)
	return p
end

-- joint a child part to a parent part. c0/c1 are offsets from each part's centre.
local function joint(name, parent, child, c0, c1)
	local m = Instance.new("Motor6D")
	m.Name = name
	m.Part0 = parent
	m.Part1 = child
	m.C0 = c0 or CFrame.new()
	m.C1 = c1 or CFrame.new()
	m.Parent = parent
	return m
end

function MonsterRig.build(options)
	options = options or {}
	local model = Instance.new("Model")
	model.Name = options.name or "PaleWatcher"
	model:SetAttribute("NightLoopRig", true)

	-- ---------------------------------------------------------------- core
	-- Root sits at the FEET, so placing the model at a spot puts it on the
	-- floor rather than buried to the waist.
	local root = part("Root", Vector3.new(1.6, 0.2, 1), PALE, nil, 1)
	root.Name = "Root"
	root.Anchored = true
	root.CanQuery = false
	root.Parent = model
	model.PrimaryPart = root

	local pelvis = part("Pelvis", Vector3.new(1.5, 0.9, 0.85), DARK)
	pelvis.Parent = model
	joint("RootJoint", root, pelvis, CFrame.new(0, 3.9 * S, 0), CFrame.new())

	local torso = part("Torso", Vector3.new(1.7, 1.5, 0.9), PALE)
	torso.Parent = model
	joint("Waist", pelvis, torso, CFrame.new(0, 0.45 * S, 0), CFrame.new(0, -0.75 * S, 0))

	-- narrow, high chest: the ribcage reads as starved
	local chest = part("Chest", Vector3.new(1.9, 1.3, 0.95), PALE)
	chest.Parent = model
	joint("Spine", torso, chest, CFrame.new(0, 0.75 * S, 0), CFrame.new(0, -0.65 * S, 0))

	local neck = part("Neck", Vector3.new(0.42, 1.25, 0.42), DARK)
	neck.Parent = model
	joint("NeckLower", chest, neck, CFrame.new(0, 0.65 * S, 0), CFrame.new(0, -0.62 * S, 0))

	-- ---------------------------------------------------------------- head
	local head = part("Head", Vector3.new(0.86, 1.35, 1.02), PALE)
	head.Parent = model
	joint("Neck", neck, head, CFrame.new(0, 0.62 * S, 0), CFrame.new(0, -0.6 * S, 0))

	local jaw = part("Jaw", Vector3.new(0.72, 0.42, 0.8), DARK)
	jaw.Parent = model
	joint("Jaw", head, jaw, CFrame.new(0, -0.5 * S, 0.08 * S), CFrame.new(0, 0.12 * S, 0))

	for _, side in ipairs({ { "L", -1 }, { "R", 1 } }) do
		local tag, dir = side[1], side[2]
		local eye = part("Eye" .. tag, Vector3.new(0.17, 0.1, 0.1), EYE, Enum.Material.Neon)
		eye.CanQuery = false
		eye.Parent = model
		joint("Eye" .. tag, head, eye,
			CFrame.new(0.2 * dir * S, 0.2 * S, -0.48 * S), CFrame.new())
	end

	-- ---------------------------------------------------------------- arms
	for _, side in ipairs({ { "L", -1 }, { "R", 1 } }) do
		local tag, dir = side[1], side[2]

		local upper = part("UpperArm" .. tag, Vector3.new(0.38, 1.7, 0.38), PALE)
		upper.Parent = model
		joint("Shoulder" .. tag, chest, upper,
			CFrame.new(0.98 * dir * S, 0.45 * S, 0), CFrame.new(0, 0.8 * S, 0))

		local lower = part("LowerArm" .. tag, Vector3.new(0.32, 1.9, 0.32), PALE)
		lower.Parent = model
		joint("Elbow" .. tag, upper, lower,
			CFrame.new(0, -0.85 * S, 0), CFrame.new(0, 0.9 * S, 0))

		local hand = part("Hand" .. tag, Vector3.new(0.34, 0.5, 0.3), DARK)
		hand.Parent = model
		joint("Wrist" .. tag, lower, hand,
			CFrame.new(0, -0.95 * S, 0), CFrame.new(0, 0.22 * S, 0))

		-- long fingers; they catch the light against glass
		for f = 1, 3 do
			local finger = part(("Finger%s%d"):format(tag, f),
				Vector3.new(0.09, 0.75, 0.09), DARK)
			finger.CanQuery = false
			finger.Parent = model
			joint(("Finger%s%d"):format(tag, f), hand, finger,
				CFrame.new((f - 2) * 0.11 * S, -0.24 * S, 0), CFrame.new(0, 0.36 * S, 0))
		end
	end

	-- ---------------------------------------------------------------- legs
	for _, side in ipairs({ { "L", -1 }, { "R", 1 } }) do
		local tag, dir = side[1], side[2]

		local upper = part("UpperLeg" .. tag, Vector3.new(0.46, 1.8, 0.46), PALE)
		upper.Parent = model
		joint("Hip" .. tag, pelvis, upper,
			CFrame.new(0.42 * dir * S, -0.4 * S, 0), CFrame.new(0, 0.85 * S, 0))

		local lower = part("LowerLeg" .. tag, Vector3.new(0.4, 1.8, 0.4), PALE)
		lower.Parent = model
		joint("Knee" .. tag, upper, lower,
			CFrame.new(0, -0.9 * S, 0), CFrame.new(0, 0.9 * S, 0))

		local foot = part("Foot" .. tag, Vector3.new(0.42, 0.28, 0.85), DARK)
		foot.Parent = model
		joint("Ankle" .. tag, lower, foot,
			CFrame.new(0, -0.9 * S, 0), CFrame.new(0, 0, 0.22 * S))
	end

	return model
end

-- Collect every Motor6D by name. Cheap enough to call once per spawn.
function MonsterRig.getJoints(model)
	local joints = {}
	for _, d in ipairs(model:GetDescendants()) do
		if d:IsA("Motor6D") then
			joints[d.Name] = d
		end
	end
	return joints
end

function MonsterRig.isRig(model)
	return model and model:GetAttribute("NightLoopRig") == true
end

return MonsterRig
