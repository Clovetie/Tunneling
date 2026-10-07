--!nonstrict
--[[
	NightLoop · MonsterAnimator

	Procedural animation for a Motor6D rig. No KeyframeSequences, no uploaded
	animation IDs — poses are defined in code as per-joint CFrame offsets and
	blended toward every frame, with layered noise on top so the thing never
	holds perfectly still.

		local anim = MonsterAnimator.new(model)
		anim:Play("watch")       -- blends over time
		anim:Play("recoil", true) -- true = snap, no blend
		anim:Update(dt, targetPosition)
		anim:Destroy()

	States:
		idle     slow breathing, weight shifting
		watch    head tracks the nearest player, body stays unnaturally still
		twitch   a single hard jerk, auto-returns to the previous state
		recoil   flashed: head snaps away, arms shield the face
		lunge    breaching: surges toward the glass, jaw open
		climb    CYCLE, not a pose: hauling itself up a wall. Drive it with
		         SetCycle(phase) from the distance actually climbed, or the
		         limbs slide out of time with the body.
		retreat  folds down and away
]]

local MonsterAnimator = {}
MonsterAnimator.__index = MonsterAnimator

local MonsterRig = require(script.Parent.MonsterRig)

local function ang(x, y, z)
	return CFrame.Angles(math.rad(x or 0), math.rad(y or 0), math.rad(z or 0))
end

--[[
	A pose is joint name -> CFrame offset applied on top of the rig's rest C0.
	Anything not listed returns to rest.

	AXIS CONVENTION - the sign flips depending on which side of the joint the
	part sits, which is easy to get wrong:

	  Limbs hang BELOW their joint (arms, legs):
	      +X swings them FORWARD, -X back. +Z swings the RIGHT arm out,
	      -Z the left arm out. Knees bend on -X (shin goes backward).
	  Spine sits ABOVE its joint (Waist, Spine, NeckLower, Neck):
	      -X leans FORWARD, +X leans back. The opposite of the limbs.
	  Jaw hangs below the head joint, so +X opens it.

	Rotations COMPOUND down the chain: Waist, Spine and NeckLower each at 20
	degrees folds the body 60. Keep torso values small.
]]
--[=[
	climb is not a pose, it is a CYCLE. `cycle(phase, intensity)` returns the
	joint offsets for one point in the stride, phase in [0,1). The owner drives
	that phase from the distance the body has ACTUALLY climbed
	(MonsterAnimator:SetCycle) rather than from the clock, so the hands keep
	time with the wall. A clock-driven cycle visibly slides the moment the
	climb eases in, eases out, or changes speed.

	Same axis rules as every pose above. Arms reach UP at about +166 on X
	(they hang down at rest, so +90 is straight out in front and +180 is up),
	and the knee is drawn up by hip +X with the shin folded back by knee -X.
]=]
local function climbCycle(phase, intensity)
	local TAU = math.pi * 2
	local p = (phase or 0) % 1
	local effort = 0.55 + (intensity or 0) * 0.45

	-- 0 = hand overhead gripping, 0.5 = hand back down at the hip pulling
	local upR = (math.cos(TAU * p) + 1) * 0.5
	local upL = (math.cos(TAU * (p + 0.5)) + 1) * 0.5
	-- the knee comes up on the same side as the hand that is reaching
	local kneeR = (math.cos(TAU * (p + 0.25)) + 1) * 0.5
	local kneeL = (math.cos(TAU * (p + 0.75)) + 1) * 0.5
	local sway = math.sin(TAU * p) * 5

	return {
		-- pressed to the wall: hips in tight, shoulders working overhead
		RootJoint = CFrame.new(0, -0.1 - 0.08 * upR, 0.2),
		Waist = ang(-5, 0, sway * 0.5),
		Spine = ang(-4, 0, -sway * 0.4),
		NeckLower = ang(9, 0, 0),   -- chin up, watching the sill it is making for
		Neck = ang(-5, 0, 0),
		Jaw = ang(3 + effort * 7, 0, 0),

		ShoulderR = ang(24 + 142 * upR, 0, 11),
		ElbowR = ang(8 + 38 * upR, 0, 0),
		FingerR1 = ang(14 + 34 * (1 - upR), 0, 0),
		FingerR2 = ang(18 + 38 * (1 - upR), 0, 0),
		FingerR3 = ang(14 + 34 * (1 - upR), 0, 0),

		ShoulderL = ang(24 + 142 * upL, 0, -11),
		ElbowL = ang(8 + 38 * upL, 0, 0),
		FingerL1 = ang(14 + 34 * (1 - upL), 0, 0),
		FingerL2 = ang(18 + 38 * (1 - upL), 0, 0),
		FingerL3 = ang(14 + 34 * (1 - upL), 0, 0),

		HipR = ang(10 + 56 * kneeR, 0, 0),
		KneeR = ang(-16 - 88 * kneeR, 0, 0),
		AnkleR = ang(8 + 24 * kneeR, 0, 0),

		HipL = ang(10 + 56 * kneeL, 0, 0),
		KneeL = ang(-16 - 88 * kneeL, 0, 0),
		AnkleL = ang(8 + 24 * kneeL, 0, 0),
	}
end

local POSES = {
	idle = {
		blend = 2.2,
		joints = {
			Waist = ang(-2, 0, 0),
			Spine = ang(-3, 0, 0),
			NeckLower = ang(-6, 0, 0),  -- head carried forward, vulture-ish
			Neck = ang(3, 0, 0),        -- chin levelled back up
			ShoulderL = ang(3, 0, -6),
			ShoulderR = ang(3, 0, 6),
			ElbowL = ang(11, 0, 0),
			ElbowR = ang(11, 0, 0),
			KneeL = ang(-4, 0, 0),
			KneeR = ang(-4, 0, 0),
		},
	},

	-- the stare. Body locked rigid, only the head lives.
	watch = {
		blend = 3.0,
		joints = {
			Waist = ang(-1, 0, 0),
			Spine = ang(-2, 0, 0),
			NeckLower = ang(-10, 0, 0),
			Neck = ang(4, 0, 0),
			ShoulderL = ang(2, 0, -4),
			ShoulderR = ang(2, 0, 4),
			ElbowL = ang(8, 0, 0),
			ElbowR = ang(8, 0, 0),
			Jaw = ang(3, 0, 0),
		},
	},

	twitch = {
		blend = 26,
		hold = 0.13,
		joints = {
			Spine = ang(-2, 6, 0),
			NeckLower = ang(-8, 22, 0),
			Neck = ang(9, -15, 8),
			ShoulderR = ang(12, 0, 14),
			ElbowR = ang(22, 0, 0),
			Jaw = ang(9, 0, 0),
		},
	},

	-- caught in the beam: throws itself back, forearms up across the face
	recoil = {
		blend = 18,
		hold = 0.55,
		joints = {
			RootJoint = CFrame.new(0, 0, 0.4),
			Waist = ang(9, 0, 0),
			Spine = ang(7, 0, 0),
			NeckLower = ang(12, 0, 0),
			Neck = ang(5, 0, 0),
			Jaw = ang(22, 0, 0),
			ShoulderL = ang(70, 0, -14),
			ShoulderR = ang(70, 0, 14),
			ElbowL = ang(82, 0, 0),
			ElbowR = ang(82, 0, 0),
			FingerL1 = ang(26, 0, 0),
			FingerL2 = ang(30, 0, 0),
			FingerL3 = ang(26, 0, 0),
			FingerR1 = ang(26, 0, 0),
			FingerR2 = ang(30, 0, 0),
			FingerR3 = ang(26, 0, 0),
			HipL = ang(-6, 0, 0),
			HipR = ang(-6, 0, 0),
			KneeL = ang(-9, 0, 0),
			KneeR = ang(-9, 0, 0),
		},
	},

	-- coming through the window: drives forward, arms reaching, jaw wide
	lunge = {
		blend = 20,
		hold = 0.8,
		joints = {
			RootJoint = CFrame.new(0, 0, -0.8),
			Waist = ang(-13, 0, 0),
			Spine = ang(-9, 0, 0),
			NeckLower = ang(-13, 0, 0),
			Neck = ang(-3, 0, 0),
			Jaw = ang(36, 0, 0),
			ShoulderL = ang(70, 0, -13),
			ShoulderR = ang(70, 0, 13),
			ElbowL = ang(16, 0, 0),
			ElbowR = ang(16, 0, 0),
			FingerL1 = ang(38, 0, 0),
			FingerL2 = ang(44, 0, 0),
			FingerL3 = ang(38, 0, 0),
			FingerR1 = ang(38, 0, 0),
			FingerR2 = ang(44, 0, 0),
			FingerR3 = ang(38, 0, 0),
			HipL = ang(15, 0, 0),
			HipR = ang(-12, 0, 0),
			KneeL = ang(-22, 0, 0),
			KneeR = ang(-6, 0, 0),
		},
	},

	-- climbing the outside wall to reach a window with no floor under it.
	-- blend is high because the goal moves every frame; a slow blend would
	-- smear the cycle into a wobble.
	climb = {
		blend = 18,
		cycle = climbCycle,
	},

	-- folds down and away, curling forward over itself
	retreat = {
		blend = 5,
		joints = {
			RootJoint = CFrame.new(0, -0.3, 0.45),
			Waist = ang(-14, 0, 0),
			Spine = ang(-8, 0, 0),
			NeckLower = ang(-7, 0, 0),
			Neck = ang(-5, 0, 0),
			ShoulderL = ang(18, 0, -17),
			ShoulderR = ang(18, 0, 17),
			ElbowL = ang(54, 0, 0),
			ElbowR = ang(54, 0, 0),
			HipL = ang(24, 0, 0),
			HipR = ang(24, 0, 0),
			KneeL = ang(-42, 0, 0),
			KneeR = ang(-42, 0, 0),
			AnkleL = ang(18, 0, 0),
			AnkleR = ang(18, 0, 0),
		},
	},
}

function MonsterAnimator.new(model)
	local self = setmetatable({}, MonsterAnimator)
	self.model = model
	self.joints = MonsterRig.getJoints(model)

	-- rest pose, captured before anything moves
	self.rest = {}
	for name, m in pairs(self.joints) do
		self.rest[name] = m.C0
	end

	-- current blended offset per joint
	self.current = {}
	for name in pairs(self.joints) do
		self.current[name] = CFrame.new()
	end

	self.state = "idle"
	self.previous = "idle"
	self.holdUntil = 0
	self.clock = 0
	self.nextTwitch = 4 + math.random() * 7
	self.twitchEnabled = true
	self.intensity = 0
	self.cyclePhase = 0
	self.alive = true
	return self
end

-- Joint goals for a state, resolving cycling poses on the fly.
function MonsterAnimator:_goals(stateName)
	local pose = POSES[stateName]
	if not pose then
		return {}
	end
	if pose.cycle then
		return pose.cycle(self.cyclePhase, self.intensity)
	end
	return pose.joints
end

-- Where in the stride a cycling pose (climb) is, 0..1. Anything outside is
-- wrapped, so the caller can pass total distance / stride without a modulo.
function MonsterAnimator:SetCycle(phase)
	self.cyclePhase = (tonumber(phase) or 0) % 1
	return self.cyclePhase
end

function MonsterAnimator:Play(stateName, snap)
	if not POSES[stateName] or not self.alive then
		return
	end
	-- transient states remember where to fall back to
	if POSES[stateName].hold then
		if not POSES[self.state].hold then
			self.previous = self.state
		end
		-- self.clock, not os.clock(): Update is dt-driven, so holds must be too
		self.holdUntil = self.clock + POSES[stateName].hold
	else
		self.previous = stateName
		self.holdUntil = 0
	end

	self.state = stateName

	if snap then
		local goals = self:_goals(stateName)
		for name in pairs(self.joints) do
			self.current[name] = goals[name] or CFrame.new()
		end
	end
end

function MonsterAnimator:SetIntensity(value)
	self.intensity = math.clamp(value or 0, 0, 1)
end

-- Head tracking: aim the neck at a world position, clamped to a human-ish arc.
function MonsterAnimator:_look(targetPosition)
	local root = self.model.PrimaryPart
	if not root or not targetPosition then
		return CFrame.new(), CFrame.new()
	end

	local headJoint = self.joints.Neck
	local head = headJoint and headJoint.Part1
	local origin = head and head.Position or root.Position
	local localDir = root.CFrame:VectorToObjectSpace((targetPosition - origin).Unit)

	local yaw = math.atan2(-localDir.X, -localDir.Z)
	local pitch = math.asin(math.clamp(localDir.Y, -1, 1))

	-- split the turn between the two neck joints so it bends, not snaps
	yaw = math.clamp(yaw, math.rad(-105), math.rad(105))
	pitch = math.clamp(pitch, math.rad(-40), math.rad(45))

	-- NOT -pitch. The head sits ABOVE its joint, so -X pitches it DOWN (see the
	-- axis note on POSES). `pitch` is already negative for a target below, so
	-- passing it straight through looks down; negating it looked UP at the
	-- ceiling while the player stood below, which read as "staring past you".
	return CFrame.Angles(0, yaw * 0.45, 0),
		CFrame.Angles(pitch, yaw * 0.55, 0)
end

function MonsterAnimator:Update(dt, targetPosition)
	if not self.alive or not self.model.Parent then
		return
	end
	self.clock += dt

	-- transient states expire back to whatever was playing before
	if self.holdUntil > 0 and self.clock >= self.holdUntil then
		self.holdUntil = 0
		self.state = self.previous
	end

	local pose = POSES[self.state]
	if not pose then
		return
	end
	-- cycling poses rebuild their goals every frame from the current phase
	local target = self:_goals(self.state)
	local blend = math.clamp(pose.blend * dt, 0, 1)

	-- idle twitches, more often as the night gets worse
	if self.twitchEnabled and (self.state == "idle" or self.state == "watch") then
		self.nextTwitch -= dt * (1 + self.intensity * 2)
		if self.nextTwitch <= 0 then
			self.nextTwitch = 3 + math.random() * 8
			self:Play("twitch")
		end
	end

	-- breathing, and a tremor that grows with intensity
	local breathe = math.sin(self.clock * 1.15) * 0.016
	local sway = math.sin(self.clock * 0.42) * 0.9
	local tremor = self.intensity * 0.7

	local lookNeck, lookHead = CFrame.new(), CFrame.new()
	if self.state == "watch" and targetPosition then
		lookNeck, lookHead = self:_look(targetPosition)
	end

	for name, motor in pairs(self.joints) do
		local goal = target[name] or CFrame.new()

		if name == "NeckLower" then
			goal = goal * lookNeck
		elseif name == "Neck" then
			goal = goal * lookHead
		elseif name == "Spine" then
			goal = goal * CFrame.Angles(breathe, math.rad(sway * 0.4), 0)
		elseif name == "Waist" then
			goal = goal * CFrame.Angles(0, math.rad(sway * 0.6), math.rad(sway * 0.3))
		end

		-- per-joint jitter: tiny, random, never settles
		if tremor > 0 and (name == "Neck" or name == "NeckLower" or name == "Jaw") then
			local j = tremor * 0.012
			goal = goal * CFrame.Angles(
				(math.random() - 0.5) * j,
				(math.random() - 0.5) * j,
				(math.random() - 0.5) * j)
		end

		self.current[name] = self.current[name]:Lerp(goal, blend)
		motor.C0 = self.rest[name] * self.current[name]
	end
end

function MonsterAnimator:Destroy()
	self.alive = false
	-- put the rig back to rest so a reused model is never left mid-pose
	for name, motor in pairs(self.joints or {}) do
		if motor.Parent and self.rest[name] then
			motor.C0 = self.rest[name]
		end
	end
	self.joints = nil
	self.current = nil
end

MonsterAnimator.States = POSES

return MonsterAnimator
