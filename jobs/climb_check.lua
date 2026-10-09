--!nonstrict
--[==[
	jobs/climb_check.lua - verification for the Watcher's climbing animation.

	READ ONLY, apart from a scaffold at y=2000 that it destroys before it
	returns. Four things get checked:

	  1. config  - the live Config really carries the Climbing block
	  2. spots   - how every child of Workspace.Spots classifies now
	               (ground / climb / none), and how far it is to the ground
	  3. anim    - the climb cycle moves the limbs, and SetCycle reaches the
	               motors instead of getting lost in the blend
	  4. climb   - a real climb driven by the entity's own _startClimb /
	               _updateClimb: monotonic rise, sane duration, arrives at the
	               spot facing into the wall

	`checks` holds self-check vectors. There is no local Luau interpreter, so a
	job that computes values has to assert its own arithmetic (CONNECT.md
	section 8, item 6 - the double-rounding trap that faked a drift report).

	Run it after jobs/push_climb.lua:
		.\ab.ps1 runfile climb_check.lua
]==]

local H = game:GetService("HttpService")
local Workspace = game:GetService("Workspace")

-- JSONEncode throws on NaN and infinity, and Studio rounds through double, so
-- every number the job reports goes through here first.
local function n2(x)
	if x ~= x then
		return "NaN"
	end
	if x == math.huge then
		return "inf"
	end
	if x == -math.huge then
		return "-inf"
	end
	return math.floor(x * 100 + 0.5) / 100
end

local nl = game:GetService("ServerScriptService"):FindFirstChild("NightLoop")
if not nl then
	return H:JSONEncode({ error = "no ServerScriptService.NightLoop" })
end

local okRig, MonsterRig = pcall(require, nl:FindFirstChild("MonsterRig"))
local okAnim, MonsterAnimator = pcall(require, nl:FindFirstChild("MonsterAnimator"))
local okConfig, Config = pcall(require, nl:FindFirstChild("Config"))
local okEntity, WindowMonster = pcall(require, nl.Entities and nl.Entities.WindowMonster)

local report = {
	require = {
		rig = okRig,
		animator = okAnim,
		config = okConfig,
		entity = okEntity,
		rigError = okRig and nil or tostring(MonsterRig),
		animatorError = okAnim and nil or tostring(MonsterAnimator),
		configError = okConfig and nil or tostring(Config),
		entityError = okEntity and nil or tostring(WindowMonster),
	},
}
if not (okRig and okAnim and okConfig and okEntity) then
	report.error = "a module failed to require on the plugin thread"
	return H:JSONEncode(report)
end

-- ------------------------------------------------------------------- config
local W = Config.Entities.WindowMonster
local Climb = W.Climbing
report.config = {
	climbingPresent = Climb ~= nil,
	enabled = Climb and Climb.Enabled,
	speed = Climb and Climb.Speed,
	stride = Climb and Climb.Stride,
	minTime = Climb and Climb.MinTime,
	maxTime = Climb and Climb.MaxTime,
	search = Climb and Climb.Search,
	probe = Climb and Climb.Probe,
	grip = Climb and Climb.Grip,
	voidRise = Climb and Climb.VoidRise,
	requireGround = W.RequireGround,
	useBuiltInRig = W.UseBuiltInRig,
	turnSpeed = W.TurnSpeed,
}

-- -------------------------------------------------------------------- spots
-- A throwaway entity. The classifier only needs Settings plus the raycast
-- helpers it gets from WindowMonster through __index - no Director, no night.
local probe = setmetatable({
	Name = "ClimbCheck",
	Settings = W,
}, { __index = WindowMonster })

local folder = Workspace:FindFirstChild(Config.World.SpotsFolder)
local spots = {}
if folder then
	for _, v in ipairs(folder:GetChildren()) do
		if v:IsA("BasePart") then
			local ok, sup = pcall(function()
				return probe:_classify(v)
			end)
			table.insert(spots, {
				name = v.Name,
				mode = (ok and sup) and sup.mode or (ok and "none" or "error"),
				drop = (ok and sup) and n2(sup.drop) or nil,
				wall = (ok and sup and sup.wall) and n2(sup.wall.dist) or nil,
				spotY = n2(v.Position.Y),
				baseY = (ok and sup) and n2(sup.pos.Y) or nil,
			})
		end
	end
end
report.spots = spots

-- ---------------------------------------------------------------- animation
local rig = MonsterRig.build({ name = "ClimbCheck_Rig" })
rig.Parent = Workspace
rig:PivotTo(CFrame.new(0, 2050, -3))

local anim = MonsterAnimator.new(rig)
anim:SetIntensity(0) -- no tremor, so a settled pose can be compared exactly

local climbPose = MonsterAnimator.States and MonsterAnimator.States.climb
local cycle = climbPose and climbPose.cycle
-- X is the reach/pull axis for the limbs; the torso sway rides on Z.
local KEYS = {
	"ShoulderR", "ShoulderL", "ElbowR",
	"HipR", "KneeR", "HipL", "KneeL",
	"Waist", "Spine", "NeckLower", "Jaw",
}

local function degXYZ(cf)
	local x, y, z = cf:ToEulerAnglesXYZ()
	return math.deg(x), math.deg(y), math.deg(z)
end

local shape = {}
if cycle then
	local lo, hi = {}, {}
	for _, k in ipairs(KEYS) do
		lo[k] = { x = math.huge, z = math.huge }
		hi[k] = { x = -math.huge, z = -math.huge }
	end
	for i = 0, 32 do
		local pose = cycle(i / 32, 0)
		for _, k in ipairs(KEYS) do
			local cf = pose[k]
			if cf then
				local x, _, z = degXYZ(cf)
				if x < lo[k].x then
					lo[k].x = x
				end
				if x > hi[k].x then
					hi[k].x = x
				end
				if z < lo[k].z then
					lo[k].z = z
				end
				if z > hi[k].z then
					hi[k].z = z
				end
			end
		end
	end
	for _, k in ipairs(KEYS) do
		table.insert(shape, {
			joint = k,
			minX = n2(lo[k].x),
			maxX = n2(hi[k].x),
			spanX = n2(hi[k].x - lo[k].x),
			spanZ = n2(hi[k].z - lo[k].z),
		})
	end
end

-- Does SetCycle actually reach the motors, or does the blend eat it? Hold one
-- phase until it settles, then the opposite phase, and compare with the goal.
local settle = {}
if cycle then
	local function hold(phase)
		anim:SetCycle(phase)
		for _ = 1, 90 do
			anim:Update(1 / 60, nil)
		end
		local joints = MonsterRig.getJoints(rig)
		local want = cycle(phase, 0)
		local out = {}
		for _, k in ipairs({ "ShoulderR", "ShoulderL", "HipR", "NeckLower" }) do
			local got = degXYZ(joints[k].C0)
			local goal = degXYZ(want[k])
			out[k] = { got = n2(got), want = n2(goal), off = n2(got - goal) }
		end
		return out
	end
	settle = { up = hold(0), down = hold(0.5) }
	settle.armTravel = n2(math.abs(settle.up.ShoulderR.got - settle.down.ShoulderR.got))
	settle.worstOff = n2(math.max(
		math.abs(settle.up.ShoulderR.off), math.abs(settle.down.ShoulderR.off),
		math.abs(settle.up.HipR.off), math.abs(settle.down.HipR.off)))
end
report.anim = { cycleShape = shape, settle = settle }

-- ---------------------------------------------------------------- the climb
--[==[
	A scaffold at y=2000 - a floor, a wall, and a spot 22 studs up with no
	floor under it: what an upper-floor window looks like to the classifier.
	Everything built here is destroyed before the job returns.
]==]
local scaffold = {}
local function block(name, size, cf)
	local p = Instance.new("Part")
	p.Name = name
	p.Size = size
	p.Anchored = true
	p.CanCollide = true
	p.Material = Enum.Material.SmoothPlastic
	p.CFrame = cf
	p.Parent = Workspace
	table.insert(scaffold, p)
	return p
end

-- wall face at z = 0, floor top at y = 2000, spot 0.8 out from the wall
block("ClimbCheck_Ground", Vector3.new(40, 1, 40), CFrame.new(0, 1999.5, 0))
block("ClimbCheck_Wall", Vector3.new(12, 40, 2), CFrame.new(0, 2020, 1))
local spot = block("ClimbCheck_Spot", Vector3.new(1, 1, 1), CFrame.new(0, 2022, -0.8))

local sup = probe:_classify(spot)
report.climbSpot = {
	mode = sup and sup.mode,
	drop = sup and n2(sup.drop),
	wall = (sup and sup.wall) and n2(sup.wall.dist) or nil,
	baseY = sup and n2(sup.pos.Y) or nil,
	spotY = n2(spot.Position.Y),
}

-- the entity drives its own climb; we only supply a stand-in context
probe._monster = rig
probe._animator = anim
local ctx = {
	Intensity = 0.5,
	Net = { BroadcastCue = function() end },
}

probe:_startClimb(spot, sup, ctx)

local startCF = rig:GetPivot()
local dt = 1 / 60
local steps, lastY, monotonic = 0, startCF.Position.Y, true
local trace = {}
while probe._state == "climbing" and steps < 1200 do
	probe:_updateClimb(dt, ctx)
	steps += 1
	local y = rig:GetPivot().Position.Y
	if y < lastY - 0.001 then
		monotonic = false
	end
	lastY = y
	if steps % 60 == 0 then
		table.insert(trace, n2(y))
	end
end

local endCF = rig:GetPivot()
report.climb = {
	startY = n2(startCF.Position.Y),
	endY = n2(endCF.Position.Y),
	rise = n2(endCF.Position.Y - startCF.Position.Y),
	seconds = n2(steps * dt),
	steps = steps,
	monotonic = monotonic,
	finished = probe._state == "idle",
	endZ = n2(endCF.Position.Z),
	lookZ = n2(endCF.LookVector.Z),
	lookY = n2(endCF.LookVector.Y),
	cyclePhase = n2(anim.cyclePhase),
	traceY = trace,
}

-- ------------------------------------------------------------------- checks
-- Self-check vectors: the job's own arithmetic, recomputed here. If these do
-- not match the constants in the source, the numbers above mean nothing.
report.checks = {
	ease05 = n2((function(a)
		return a * a * (3 - 2 * a)
	end)(0.5)),
	upPhase0 = n2((math.cos(0) + 1) * 0.5),
	upPhase25 = n2((math.cos(math.pi / 2) + 1) * 0.5),
	upPhase50 = n2((math.cos(math.pi) + 1) * 0.5),
	shoulderTop = 24 + 142,
	shoulderBottom = 24,
	scaffoldRise = 2022 - 2000,
	expectedSeconds = Climb and n2(math.clamp(22 / Climb.Speed, Climb.MinTime, Climb.MaxTime)) or nil,
	expectedCycles = Climb and n2(22 / Climb.Stride) or nil,
}

-- ------------------------------------------------------------------ cleanup
anim:Destroy()
for _, p in ipairs(scaffold) do
	p:Destroy()
end
rig:Destroy()

local worstOff = report.anim.settle.worstOff
report.ok = report.climb.finished == true
	and report.climb.monotonic == true
	and worstOff ~= nil
	and worstOff < 1

return H:JSONEncode(report)
