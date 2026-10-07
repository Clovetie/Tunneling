--!nonstrict
--[[
	NightLoop · WindowMonster
	Moves between Workspace.Spots. Catch it in a single camera flash and
	it retreats — one shot, no holding. Leave it at a window too long and it
	breaches, which costs a strike; enough strikes and the night is lost.

	A spot with a floor under it is walked to. A spot with nothing but wall
	under it is climbed to (Config.Entities.WindowMonster.Climbing): it blinks
	to the foot of the wall and hauls itself up where you can see it, which is
	what unlocked the upper-floor windows.

	Server-authoritative: subscribes to Flash.Fired, which only the server can
	raise. Detection cone is derived from SpotLight.Angle / .Range so the hitbox
	always matches the burst the player sees, and the ray punches through glass
	so flashing a monster through a window works.

	Scales with phase intensity: teleports faster, retreats for less time, and
	takes longer to repel as the night escalates.
]]

local Players = game:GetService("Players")
local ServerStorage = game:GetService("ServerStorage")
local Workspace = game:GetService("Workspace")

local EntityBase = require(script.Parent.Parent.EntityBase)
local Beam = require(script.Parent.Parent.Beam)
local MonsterRig = require(script.Parent.Parent.MonsterRig)
local MonsterAnimator = require(script.Parent.Parent.MonsterAnimator)

local WindowMonster = EntityBase.new("WindowMonster")

-- ---------------------------------------------------------------- validation
function WindowMonster:Validate(ctx)
	local world = ctx.Config.World
	local spots = Workspace:FindFirstChild(world.SpotsFolder)
	if not spots then
		return false, ("Workspace.%s not found"):format(world.SpotsFolder)
	end

	local count = 0
	for _, v in ipairs(spots:GetChildren()) do
		if v:IsA("BasePart") then
			count += 1
		end
	end
	if count == 0 then
		return false, ("Workspace.%s has no BaseParts"):format(world.SpotsFolder)
	end

	if not self.Settings.UseBuiltInRig then
		if not ServerStorage:FindFirstChild(self.Settings.Template) then
			return false, ("ServerStorage.%s not found"):format(self.Settings.Template)
		end
		self._template = ServerStorage[self.Settings.Template]
	end

	self._spotsFolder = spots

	if self.Settings.RequireGround then
		local usable = 0
		for _, v in ipairs(spots:GetChildren()) do
			if v:IsA("BasePart") and self:_classify(v) then
				usable += 1
			end
		end
		if usable == 0 then
			return false, ("no spot in %s has a floor within %d studs or anything to climb — the Watcher walks or climbs, it does not hover. Lower the spots, add a floor, or set RequireGround = false")
				:format(world.SpotsFolder, self.Settings.GroundSearch)
		end
	end
	return true
end

-- -------------------------------------------------------------------- spots
--[=[
	What holds the Watcher up at a spot?

		mode = "ground" — a floor within GroundSearch. It walks there, as before.
		mode = "climb"  — no floor, but there is something to scale: it hangs on
		                  the wall (or a sill) and hauls itself up. `pos` is
		                  where the climb starts; `wall` is { normal, dist } for
		                  the vertical surface it clings to, normal pointing
		                  away from the wall.
		nil             — nothing under it at all and climbing is off: skipped,
		                  because the Watcher walks or climbs, it does not hover.

	Cached: this is a handful of raycasts per spot and the house does not move
	during a night. Invalidated whenever a spot is added or removed.
]=]
function WindowMonster:_classify(spot)
	if not self._classifyCache then
		self._classifyCache = {}
	end
	local cached = self._classifyCache[spot]
	if cached == nil then
		cached = self:_probeSupport(spot) or false
		self._classifyCache[spot] = cached
	end
	if cached == false then
		return nil
	end
	return cached
end

local PROBE_DIRS = {
	Vector3.new(1, 0, 0), Vector3.new(-1, 0, 0),
	Vector3.new(0, 0, 1), Vector3.new(0, 0, -1),
	Vector3.new(0.7071, 0, 0.7071), Vector3.new(-0.7071, 0, 0.7071),
	Vector3.new(0.7071, 0, -0.7071), Vector3.new(-0.7071, 0, -0.7071),
}

function WindowMonster:_rayParams()
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	local ignore = {}
	if self._monster then
		table.insert(ignore, self._monster)
	end
	for _, p in ipairs(Players:GetPlayers()) do
		if p.Character then
			table.insert(ignore, p.Character)
		end
	end
	params.FilterDescendantsInstances = ignore
	params.IgnoreWater = true
	return params
end

--[=[
	Is there a vertical surface within a body-width of the spot? Cast a ring
	outward and take the closest wall-like hit. The normal points AWAY from the
	wall, back toward the spot — which is the direction the Watcher hangs, and
	therefore also the way its body faces once it gets there.
]=]
function WindowMonster:_wallNormal(spot)
	local climb = self.Settings.Climbing
	local reach = climb and climb.Probe or 2.5
	local params = self:_rayParams()
	local best, bestDist = nil, math.huge
	for _, dir in ipairs(PROBE_DIRS) do
		local hit = Workspace:Raycast(spot.Position, dir * reach, params)
		if hit and math.abs(hit.Normal.Y) < 0.5 and hit.Distance < bestDist then
			local flat = Vector3.new(hit.Normal.X, 0, hit.Normal.Z)
			if flat.Magnitude > 0.2 then
				best, bestDist = flat.Unit, hit.Distance
			end
		end
	end
	if not best then
		return nil
	end
	-- the probe direction and the surface normal are not parallel for a corner,
	-- so the measured distance is an upper bound on the true gap. Fine: Grip
	-- only ever nudges outward by a few tenths.
	return { normal = best, dist = bestDist }
end

function WindowMonster:_probeSupport(spot)
	local climb = self.Settings.Climbing
	local climbing = climb and climb.Enabled or false
	local reach = self.Settings.GroundSearch
	if climbing then
		reach = math.max(reach, climb.Search)
	end

	local params = self:_rayParams()
	local origin = spot.Position + Vector3.new(0, 2, 0)
	local down = Workspace:Raycast(origin, Vector3.new(0, -(reach + 2), 0), params)

	-- a floor within walking distance: it just stands there
	if down and down.Normal.Y > 0.5
		and (origin.Y - down.Position.Y) <= (self.Settings.GroundSearch + 2) then
		return { mode = "ground", pos = down.Position, drop = origin.Y - down.Position.Y }
	end

	if not climbing then
		return nil
	end

	-- No floor. A wall beside the spot is better evidence than whatever is far
	-- below it, and it also tells us which way to face on arrival.
	local wall = self:_wallNormal(spot)
	if not wall and down then
		local flat = Vector3.new(down.Normal.X, 0, down.Normal.Z)
		if flat.Magnitude > 0.2 then
			-- whatever it hit below is at least slanted; treat it as touching
			wall = { normal = flat.Unit, dist = 0 }
		end
	end

	-- Hanging over nothing at all: it climbs up out of the dark below. Rare,
	-- and the fog hides the cheat.
	local base = down and down.Position
		or (spot.Position - Vector3.new(0, climb.VoidRise, 0))

	return { mode = "climb", pos = base, wall = wall,
		drop = down and (origin.Y - down.Position.Y) or climb.VoidRise }
end

--[=[
	Nudge the body off the wall so it never clips the sill. The spot marks where
	the Watcher ends up; this only moves it when the spot sits closer to the
	wall than Climbing.Grip, so a sensibly placed spot is left alone.
]=]
function WindowMonster:_gripOffset(sup)
	local climb = self.Settings.Climbing
	if not (climb and sup and sup.wall) then
		return Vector3.new(0, 0, 0)
	end
	local gap = climb.Grip - sup.wall.dist
	if gap <= 0 then
		return Vector3.new(0, 0, 0)
	end
	return sup.wall.normal * gap
end

function WindowMonster:_spots()
	if self._spotCache and #self._spotCache > 0 then
		return self._spotCache
	end
	local out = {}
	local skipped = {}
	local byClimb = 0
	for _, v in ipairs(self._spotsFolder:GetChildren()) do
		if v:IsA("BasePart") then
			local sup = self:_classify(v)
			if self.Settings.RequireGround and not sup then
				table.insert(skipped, v.Name)
			else
				if sup and sup.mode == "climb" then
					byClimb += 1
				end
				table.insert(out, v)
			end
		end
	end
	self._climbSpots = byClimb
	if #skipped > 0 then
		self:Log(("ignoring %d spot(s) with nothing under them: %s")
			:format(#skipped, table.concat(skipped, ", ")))
	end
	self._spotCache = out
	return out
end

local function farthestFrom(pos, spots)
	local best, bestDist = nil, -math.huge
	for _, s in ipairs(spots) do
		local d = (s.Position - pos).Magnitude
		if d > bestDist then
			bestDist, best = d, s
		end
	end
	return best
end

-- ------------------------------------------------------------------- visuals
local function setHidden(model, hidden)
	if not model then
		return
	end
	local isRig = MonsterRig.isRig(model)
	for _, part in ipairs(model:GetDescendants()) do
		if part:IsA("BasePart") then
			-- a rig anchors ONLY its root; anchoring limbs freezes the Motor6Ds
			if isRig then
				part.Anchored = (part == model.PrimaryPart)
			else
				part.Anchored = true
			end
			-- A rig's limbs stay non-collidable even when shown: the body is
			-- dragged around by PivotTo (a climb sweeps it through space), and
			-- solid limbs would shove the player standing at the window.
			part.CanCollide = (not hidden) and (not isRig)
			local base = part:GetAttribute("BaseTransparency")
			part.Transparency = hidden and 1 or (base or 0)
		elseif part:IsA("Decal") or part:IsA("Texture") then
			part.Transparency = hidden and 1 or 0
		end
	end
end

local function setFade(model, alpha)
	if not model then
		return
	end
	for _, part in ipairs(model:GetDescendants()) do
		if part:IsA("BasePart") or part:IsA("Decal") or part:IsA("Texture") then
			part.Transparency = alpha
		end
	end
end

--[[
	Put the model at a spot, standing on the floor and turned to face whoever
	is nearest.

	The old version did `spot.CFrame + Vector3.new(0, 0.5, 0)`, which inherited
	the SPOT PART's orientation — so the Watcher faced whatever arbitrary
	direction a 1x1x1 marker happened to point, usually away from the room.
	Head tracking is clamped to a human-ish arc, so it could never turn far
	enough to recover: it ended up staring off into a wall.
]]
-- Where the model's FEET sit for a given base point. The rig's pivot is at the
-- feet already, but a template model's may not be, so measure rather than guess.
function WindowMonster:_feetAt(model, base)
	local boxCF, boxSize = model:GetBoundingBox()
	local pivot = model:GetPivot()
	local pivotToBottom = pivot.Position.Y - (boxCF.Position.Y - boxSize.Y * 0.5)
	return Vector3.new(base.X, base.Y + pivotToBottom, base.Z)
end

--[=[
	The pose the Watcher holds once it is AT a spot: standing on a floor, or
	clinging to a wall with no floor under it.

	Clinging pushes the body out along the surface normal by Climbing.Grip and
	turns it to face INTO the wall — which, at a window, means facing the room.
	That is the whole point of the climb: it arrives pressed to the glass.
]=]
function WindowMonster:_poseCFrame(model, spot, sup)
	local base = spot.Position
	local offset = self:_gripOffset(sup)

	if sup and sup.mode == "ground" then
		base = sup.pos
		offset = Vector3.new(0, 0, 0)
	end

	local pos = self:_feetAt(model, base) + offset

	if sup and sup.wall then
		-- face INTO the wall: at a window, that is facing the room
		return CFrame.lookAt(pos, pos - sup.wall.normal)
	end

	local target = self:_nearestPlayerTo(pos)
	if target then
		local flat = Vector3.new(target.X - pos.X, 0, target.Z - pos.Z)
		if flat.Magnitude > 0.25 then
			return CFrame.lookAt(pos, pos + flat.Unit)
		end
	end
	return CFrame.new(pos)
end

function WindowMonster:_placeAt(model, spot)
	if not (model and model.Parent and spot) then
		return
	end
	model:PivotTo(self:_poseCFrame(model, spot, self:_classify(spot)))
end

-- ------------------------------------------------------------------ climbing
--[=[
	Spots with no floor under them used to be skipped outright, which is why the
	upper-floor windows stayed empty all night. With climbing on, the Watcher
	scales the wall instead: it blinks out, reappears at the foot of the wall,
	and hauls itself up in plain sight. The climb is the tell — you see it
	coming and you have that long to find it with the flash.

	You can hit it mid-climb. _flashHits accepts the "climbing" state, and a
	repel drops it off the wall (see _repel).
]=]
function WindowMonster:_climbingEnabled()
	local climb = self.Settings.Climbing
	return climb ~= nil and climb.Enabled == true and self.Settings.UseBuiltInRig
end

-- The pose it starts the climb from, at the bottom, facing the wall. Same X/Z
-- as the destination (the probe fell straight down), so it scales straight up.
function WindowMonster:_climbBaseCFrame(model, spot, sup, destination)
	local pos = self:_feetAt(model, sup.pos) + self:_gripOffset(sup)
	if sup.wall then
		return CFrame.lookAt(pos, pos - sup.wall.normal)
	end
	local look = Vector3.new(destination.LookVector.X, 0, destination.LookVector.Z)
	if look.Magnitude > 0.1 then
		return CFrame.lookAt(pos, pos + look.Unit)
	end
	return CFrame.new(pos)
end

function WindowMonster:_startClimb(spot, sup, ctx)
	local cfg = self.Settings.Climbing
	local monster = self._monster
	if not (cfg and monster) then
		return
	end

	local to = self:_poseCFrame(monster, spot, sup)
	local from = self:_climbBaseCFrame(monster, spot, sup, to)

	local rise = (to.Position - from.Position).Magnitude
	local speed = math.max(0.1, cfg.Speed)
	local dur = math.clamp(rise / speed, cfg.MinTime, cfg.MaxTime)

	-- blink to the bottom of the wall, then let it be seen on the way up
	setHidden(monster, true)
	monster:PivotTo(from)
	setHidden(monster, false)

	self._currentSpot = spot
	self._state = "climbing"
	self._climb = {
		from = from,
		to = to,
		t = 0,
		dur = dur,
		rise = rise,
		climbed = 0,
		last = from.Position,
	}

	-- The siege clock is paused for the duration: a slow climb should not cost
	-- the player patience they never had a chance to spend.
	if self._breachAt and self._breachAt > 0 then
		self._breachAt += dur
	end

	if self._animator then
		self._animator:Play("climb", true)
	end

	ctx.Net.BroadcastCue({
		kind = "climb",
		entity = self.Name,
		spot = spot.Name,
		seconds = dur,
	})
	self:Log(("climbing to %s — %.1f studs in %.1fs"):format(spot.Name, rise, dur))
end

function WindowMonster:_cancelClimb()
	self._climb = nil
end

function WindowMonster:_updateClimb(dt, ctx)
	local c = self._climb
	local monster = self._monster
	if not (c and monster and monster.Parent) then
		self:_cancelClimb()
		self._state = "idle"
		return
	end

	local cfg = self.Settings.Climbing
	c.t += dt
	local alpha = math.clamp(c.t / c.dur, 0, 1)
	-- ease in and out; starting and stopping dead reads as a teleport
	local eased = alpha * alpha * (3 - 2 * alpha)

	local cf = c.from:Lerp(c.to, eased)
	monster:PivotTo(cf)

	-- drive the animation off distance actually covered, not the clock
	c.climbed += (cf.Position - c.last).Magnitude
	c.last = cf.Position
	if self._animator then
		self._animator:SetCycle(c.climbed / math.max(0.1, cfg.Stride))
	end

	if alpha >= 1 then
		self:_cancelClimb()
		self._state = "idle"
		if self._animator then
			self._animator:Play("watch", true)
		end
		-- give it a beat at the window before it moves on. NOTE: the teleport
		-- timings live on Settings, not on Settings.Climbing (cfg).
		local s = self.Settings
		local minWait = self:ByIntensity(s.TeleportMinEasy, s.TeleportMinHard, ctx.Intensity)
		local maxWait = self:ByIntensity(s.TeleportMaxEasy, s.TeleportMaxHard, ctx.Intensity)
		self._nextTeleport = os.clock() + minWait + math.random() * math.max(0, maxWait - minWait)
	end
end

-- Move to a spot: climb if it hangs, walk there if it does not.
-- RequireGround = false means "do not check, just put it there" — that is the
-- old behaviour and it stays that way, so no surprise climbs appear for it.
function WindowMonster:_goTo(spot, ctx)
	local sup = self:_classify(spot)
	if sup and sup.mode == "climb" and self.Settings.RequireGround
		and self:_climbingEnabled() and self._animator then
		self:_startClimb(spot, sup, ctx)
		return
	end
	self:_placeAt(self._monster, spot)
	self._currentSpot = spot
end

-- --------------------------------------------------------------------- beam
-- A flash counts if the cone reaches the monster. Beam.castThrough ignores
-- see-through parts, so the windows in this house no longer eat the ray.
function WindowMonster:_flashHits(player, originCFrame, originPos, spot)
	if not self._monster then
		return false
	end
	-- on the wall counts: catch it halfway up and it loses its grip
	if self._state ~= "idle" and self._state ~= "climbing" then
		return false
	end
	local halfAngle = spot.Angle / 2
	local range = spot.Range * self.Settings.RangeTolerance
	return Beam.coneHitsTarget(originCFrame, originPos, halfAngle, range,
		self._monster, { player.Character })
end

-- ------------------------------------------------------------------ breach
-- Every appearance starts a countdown. Flash it in time and the countdown
-- dies with the appearance; let it run out and the monster gets in.
-- Pressure is a siege, not a per-window timer: it survives teleports and only
-- clears when the player actually repels it.
function WindowMonster:_armBreach(ctx)
	local cfg = self.Settings
	local patience = self:ByIntensity(cfg.BreachEasy, cfg.BreachHard, ctx.Intensity)
	self._breachAt = os.clock() + patience
	self._breachWarned = false
	self._patience = patience
end

function WindowMonster:_disarmBreach()
	self._breachAt = 0
	self._breachWarned = false
end

function WindowMonster:_breach(ctx)
	local monster = self._monster
	self:_disarmBreach()

	-- it got in: show it, then pull back so the player can recover
	ctx.Net.BroadcastCue({
		kind = "breach",
		entity = self.Name,
		spot = self._currentSpot and self._currentSpot.Name or nil,
	})

	ctx.Director:AddStrike("WindowMonster", {
		spot = self._currentSpot and self._currentSpot.Name or nil,
	})

	if self._animator then
		self._animator:Play("lunge", true)
	end

	-- let the lunge read before it vanishes
	task.delay(0.55, function()
		if monster and monster.Parent then
			setHidden(monster, true)
		end
	end)
	self._state = "hidden"
	self._retreatUntil = os.clock() + self.Settings.BreachRetreat
	self:Log("breach")
end

-- ---------------------------------------------------------------- lifecycle
function WindowMonster:_spawn(spot)
	local clone
	if self.Settings.UseBuiltInRig then
		clone = MonsterRig.build({ name = "WindowMonster_Active" })
	else
		clone = self._template:Clone()
	end
	clone.Name = "WindowMonster_Active"
	clone.Parent = Workspace

	if not clone.PrimaryPart then
		clone.PrimaryPart = clone:FindFirstChild("Body")
			or clone:FindFirstChild("HumanoidRootPart")
			or clone:FindFirstChildWhichIsA("BasePart")
	end

	local isRig = MonsterRig.isRig(clone)
	for _, p in ipairs(clone:GetDescendants()) do
		if p:IsA("BasePart") then
			p.Anchored = isRig and (p == clone.PrimaryPart) or not isRig
			p.CanCollide = not isRig
		end
	end

	self:_placeAt(clone, spot)
	self:Own(clone)

	if isRig then
		if self._animator then
			self._animator:Destroy()
		end
		self._animator = MonsterAnimator.new(clone)
		self._animator:Play("watch", true)
	end
	return clone
end

-- who the thing should be staring at
function WindowMonster:_nearestPlayer()
	local monster = self._monster
	if not monster or not monster.PrimaryPart then
		return nil
	end
	return self:_nearestPlayerTo(monster.PrimaryPart.Position)
end

function WindowMonster:_nearestPlayerTo(origin)
	local best, bestDist = nil, math.huge
	for _, player in ipairs(Players:GetPlayers()) do
		local char = player.Character
		local head = char and (char:FindFirstChild("Head") or char:FindFirstChild("HumanoidRootPart"))
		if head then
			local d = (head.Position - origin).Magnitude
			if d < bestDist then
				best, bestDist = head.Position, d
			end
		end
	end
	return best
end

function WindowMonster:Start(ctx)
	self:_invalidateSpots()
	self._state = "idle"
	self._retreatUntil = 0
	self._nextTeleport = 0
	self:_cancelClimb()

	self:Track(self._spotsFolder.ChildAdded:Connect(function()
		self:_invalidateSpots()
	end))
	self:Track(self._spotsFolder.ChildRemoved:Connect(function()
		self:_invalidateSpots()
	end))

	local spots = self:_spots()
	self._currentSpot = spots[math.random(1, #spots)]
	self._monster = self:_spawn(self._currentSpot)

	self:_armBreach(ctx)

	self:Track(ctx.Flash.Fired:Connect(function(player, cf, pos, spot)
		if self:_flashHits(player, cf, pos, spot) then
			self:_repel(player, ctx)
		end
	end))

	self:Log(("active — %d spots (%d reached by climbing), flash-repel")
		:format(#spots, self._climbSpots or 0))
end

-- Spots changed: drop every cached answer about them.
function WindowMonster:_invalidateSpots()
	self._spotCache = nil
	self._classifyCache = nil
end

function WindowMonster:OnPhase(phase, ctx)
	-- re-roll the next teleport so escalation is felt immediately
	self._nextTeleport = 0
end

function WindowMonster:_repel(byPlayer, ctx)
	local wasClimbing = self._state == "climbing"
	self:_cancelClimb()
	self:_disarmBreach()
	if self._animator then
		self._animator:Play("recoil")
	end
	local cfg = self.Settings
	self._state = "hidden"
	if wasClimbing then
		self:Log("knocked off the wall")
	end

	local cooldown = self:ByIntensity(
		cfg.RetreatCooldownEasy, cfg.RetreatCooldownHard, ctx.Intensity)
	self._retreatUntil = os.clock() + cooldown

	setHidden(self._monster, true)

	local root = byPlayer and byPlayer.Character
		and byPlayer.Character:FindFirstChild("HumanoidRootPart")
	if root then
		local far = farthestFrom(root.Position, self:_spots()) or self._currentSpot
		self:_placeAt(self._monster, far)
		self._currentSpot = far
	end

	ctx.Director:Cue(byPlayer, {
		kind = "monsterRepelled",
		entity = self.Name,
		climbing = wasClimbing,
	})
end

function WindowMonster:_teleport(ctx)
	local cfg = self.Settings
	local spots = self:_spots()
	local newSpot = spots[math.random(1, #spots)]
	local tries = 0
	while newSpot == self._currentSpot and #spots > 1 and tries < 8 do
		newSpot = spots[math.random(1, #spots)]
		tries += 1
	end
	self:_goTo(newSpot, ctx)
	-- deliberately does NOT re-arm the breach timer: moving between windows is
	-- how it hunts, not a reprieve. Only a successful flash buys time back.
	-- (A climb pauses it for the duration instead — see _startClimb.)

	local minWait = self:ByIntensity(cfg.TeleportMinEasy, cfg.TeleportMinHard, ctx.Intensity)
	local maxWait = self:ByIntensity(cfg.TeleportMaxEasy, cfg.TeleportMaxHard, ctx.Intensity)
	self._nextTeleport = os.clock() + minWait + math.random() * math.max(0, maxWait - minWait)
end

function WindowMonster:Update(dt, ctx)
	local monster = self._monster
	if not monster or not monster.Parent then
		return
	end

	local watching = self:_nearestPlayer()

	if self._animator then
		self._animator:SetIntensity(ctx.Intensity)
		self._animator:Update(dt, watching)
	end

	-- on the wall: the climb owns the body until it arrives, so nothing else
	-- below gets to move it or time it out
	if self._state == "climbing" then
		self:_updateClimb(dt, ctx)
		return
	end

	--[[
		Keep the BODY turned toward whoever is nearest, not just the head.

		The head is clamped to a human-ish arc, so if the body is pointing the
		wrong way the stare can never recover. Facing is set on spawn, but at
		night start the player's Character often has not replicated yet, so
		there is nobody to face and it ends up on a default heading. This turns
		it the rest of the way, slowly, and keeps it honest as the player moves.
	]]
	if watching and self._state == "idle" and monster.PrimaryPart then
		local pos = monster.PrimaryPart.Position
		local flat = Vector3.new(watching.X - pos.X, 0, watching.Z - pos.Z)
		if flat.Magnitude > 0.5 then
			local want = CFrame.lookAt(pos, pos + flat.Unit)
			monster:PivotTo(monster.PrimaryPart.CFrame:Lerp(
				want, math.clamp(dt * (self.Settings.TurnSpeed or 2), 0, 1)))
		end
	end
	local cfg = self.Settings
	local now = os.clock()

	if self._state == "hidden" then
		if now >= self._retreatUntil then
			setHidden(monster, false)
			self._state = "idle"
			self._nextTeleport = 0
			self:_armBreach(ctx)
			if self._animator then
				self._animator:Play("watch", true)
			end
		end
		return
	end

	-- breach countdown
	if self._breachAt and self._breachAt > 0 then
		local left = self._breachAt - now
		if left <= 0 then
			self:_breach(ctx)
			return
		end
		if not self._breachWarned
			and left <= (self._patience or 0) * self.Settings.BreachWarnAt then
			self._breachWarned = true
			ctx.Net.BroadcastCue({
				kind = "breachWarning",
				entity = self.Name,
				seconds = left,
			})
		end
	end

	if self._nextTeleport == 0 then
		local minWait = self:ByIntensity(cfg.TeleportMinEasy, cfg.TeleportMinHard, ctx.Intensity)
		local maxWait = self:ByIntensity(cfg.TeleportMaxEasy, cfg.TeleportMaxHard, ctx.Intensity)
		self._nextTeleport = now + minWait + math.random() * math.max(0, maxWait - minWait)
	elseif now >= self._nextTeleport then
		self:_teleport(ctx)
	end
end

function WindowMonster:Stop(ctx)
	self:_disarmBreach()
	self:_cancelClimb()
	if self._animator then
		self._animator:Destroy()
		self._animator = nil
	end
	self._monster = nil
	self:_invalidateSpots()
end

return WindowMonster
