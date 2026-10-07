--!nonstrict
--[[
	NightLoop · CrawlerRig

	A low, six-legged skitterer for the Crawl entity, built from primitives at
	runtime like MonsterRig. The design doc calls it a "skittering shadow", so
	it is almost black and reads as a silhouette rather than a creature — you
	should never get a good look at it.

	Animation here is a GAIT, not a pose set: six legs driven by phase-offset
	sine waves, which is the right model for something that scuttles. Speed
	feeds the gait, so it visibly moves faster when it flees.

		local rig = CrawlerRig.build()
		local gait = CrawlerRig.newGait(rig)
		gait:Update(dt, studsPerSecond)

	Orientation note: the rig is built legs-down (floor spider). To cling to a
	ceiling, aim its +Y along the SURFACE NORMAL — see CrawlerRig.surfaceCFrame.
]]

local CrawlerRig = {}

local SHADOW = Color3.fromRGB(16, 15, 18)
local LIMB = Color3.fromRGB(26, 24, 28)
local EYE = Color3.fromRGB(150, 36, 30)

local LEGS_PER_SIDE = 3

local function part(name, size, color, material, transparency)
	local p = Instance.new("Part")
	p.Name = name
	p.Size = size
	p.Color = color or LIMB
	p.Material = material or Enum.Material.SmoothPlastic
	p.Transparency = transparency or 0
	p.Anchored = false
	p.CanCollide = false
	p.CanTouch = false
	p.CanQuery = true
	p.Massless = true
	p.TopSurface = Enum.SurfaceType.Smooth
	p.BottomSurface = Enum.SurfaceType.Smooth
	p:SetAttribute("BaseTransparency", p.Transparency)
	return p
end

local function joint(name, parent, child, c0, c1)
	local m = Instance.new("Motor6D")
	m.Name = name
	m.Part0, m.Part1 = parent, child
	m.C0 = c0 or CFrame.new()
	m.C1 = c1 or CFrame.new()
	m.Parent = parent
	return m
end

function CrawlerRig.build(options)
	options = options or {}
	local model = Instance.new("Model")
	model.Name = options.name or "Crawler"
	model:SetAttribute("NightLoopRig", true)
	model:SetAttribute("NightLoopCrawler", true)

	local root = part("Root", Vector3.new(1.4, 0.2, 2.2), SHADOW, nil, 1)
	root.Anchored = true
	root.CanQuery = false
	root.Parent = model
	model.PrimaryPart = root

	-- flattened body, wider than tall
	local body = part("Body", Vector3.new(1.5, 0.62, 2.4), SHADOW)
	body.Parent = model
	joint("RootJoint", root, body, CFrame.new(0, 0, 0), CFrame.new())

	local head = part("Head", Vector3.new(0.9, 0.44, 0.9), SHADOW)
	head.Parent = model
	joint("Neck", body, head, CFrame.new(0, -0.02, -1.2), CFrame.new(0, 0, -0.42))

	for _, side in ipairs({ { "L", -1 }, { "R", 1 } }) do
		local tag, dir = side[1], side[2]
		local eye = part("Eye" .. tag, Vector3.new(0.12, 0.08, 0.08), EYE, Enum.Material.Neon)
		eye.CanQuery = false
		eye.Parent = model
		joint("Eye" .. tag, head, eye,
			CFrame.new(0.22 * dir, 0.08, -0.42), CFrame.new())
	end

	-- six legs: long upper segment angled out and up, lower segment reaching down
	for _, side in ipairs({ { "L", -1 }, { "R", 1 } }) do
		local tag, dir = side[1], side[2]
		for i = 1, LEGS_PER_SIDE do
			local z = 0.85 - (i - 1) * 0.85   -- front, middle, back
			local name = ("Leg%s%d"):format(tag, i)

			local upper = part(name .. "Upper", Vector3.new(0.17, 1.5, 0.17), LIMB)
			upper.Parent = model
			-- splayed outward ~55 degrees so the knee sits above the body
			joint(name .. "Hip", body, upper,
				CFrame.new(0.68 * dir, 0.1, z) * CFrame.Angles(0, 0, math.rad(55 * dir)),
				CFrame.new(0, 0.7, 0))

			local lower = part(name .. "Lower", Vector3.new(0.13, 1.6, 0.13), LIMB)
			lower.Parent = model
			joint(name .. "Knee", upper, lower,
				CFrame.new(0, -0.72, 0) * CFrame.Angles(0, 0, math.rad(-95 * dir)),
				CFrame.new(0, 0.78, 0))
		end
	end

	return model
end

function CrawlerRig.getJoints(model)
	local joints = {}
	for _, d in ipairs(model:GetDescendants()) do
		if d:IsA("Motor6D") then
			joints[d.Name] = d
		end
	end
	return joints
end

--[[
	Build a CFrame that plants the rig on a surface.
	  position  world point on the surface
	  normal    surface normal (points away from the surface, into the room)
	  forward   desired travel direction

	The rig is built legs-down, so its +Y must follow the normal: on a ceiling
	the normal points down, which flips the crawler over to hang underneath.
]]
function CrawlerRig.surfaceCFrame(position, normal, forward)
	local up = normal.Unit
	-- project the travel direction onto the surface plane
	local fwd = (forward - up * forward:Dot(up))
	if fwd.Magnitude < 1e-3 then
		fwd = up:Cross(Vector3.new(1, 0, 0))
		if fwd.Magnitude < 1e-3 then
			fwd = up:Cross(Vector3.new(0, 0, 1))
		end
	end
	fwd = fwd.Unit
	local right = fwd:Cross(up).Unit
	return CFrame.fromMatrix(position, right, up)
end

-- ------------------------------------------------------------------- gait

local Gait = {}
Gait.__index = Gait

function CrawlerRig.newGait(model)
	local self = setmetatable({}, Gait)
	self.model = model
	self.joints = CrawlerRig.getJoints(model)
	self.rest = {}
	for name, m in pairs(self.joints) do
		self.rest[name] = m.C0
	end
	self.clock = 0
	self.alive = true

	-- alternating tripod: legs 1 and 3 on one side move with leg 2 on the other
	self.phase = {}
	for _, tag in ipairs({ "L", "R" }) do
		for i = 1, LEGS_PER_SIDE do
			local tripodA = (tag == "L" and (i == 1 or i == 3)) or (tag == "R" and i == 2)
			self.phase[("Leg%s%d"):format(tag, i)] = tripodA and 0 or math.pi
		end
	end
	return self
end

function Gait:Update(dt, speed)
	if not self.alive or not self.model.Parent then
		return
	end
	speed = speed or 0
	-- step frequency rises with speed, with a floor so it twitches when still
	local freq = 2.2 + math.clamp(speed, 0, 60) * 0.26
	self.clock += dt * freq

	local swing = math.clamp(0.1 + speed / 34, 0.1, 1) -- stride size
	local body = self.joints.RootJoint

	for name, offset in pairs(self.phase) do
		local hip = self.joints[name .. "Hip"]
		local knee = self.joints[name .. "Knee"]
		if hip and knee then
			local t = self.clock + offset
			local reach = math.sin(t) * 0.55 * swing        -- fore/aft sweep
			local lift = math.max(0, math.cos(t)) * 0.5 * swing  -- lift on the forward half

			hip.C0 = self.rest[name .. "Hip"] * CFrame.Angles(reach, 0, 0)
			knee.C0 = self.rest[name .. "Knee"] * CFrame.Angles(-lift * 1.3, 0, 0)
		end
	end

	-- body bobs against the leg cycle; tiny, but it sells the scuttle
	if body then
		local bob = math.sin(self.clock * 2) * 0.035 * swing
		body.C0 = self.rest.RootJoint * CFrame.new(0, bob, 0)
	end
end

function Gait:Destroy()
	self.alive = false
	for name, motor in pairs(self.joints or {}) do
		if motor.Parent and self.rest[name] then
			motor.C0 = self.rest[name]
		end
	end
	self.joints = nil
end

return CrawlerRig
