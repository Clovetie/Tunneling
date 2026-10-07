--!nonstrict
--[[
	NightLoop · WindowMonster
	Teleports between Workspace.Spots. Catch it in a single camera flash and
	it retreats — one shot, no holding. Leave it at a window too long and it
	breaches, which costs a strike; enough strikes and the night is lost.

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
		local grounded = 0
		for _, v in ipairs(spots:GetChildren()) do
			if v:IsA("BasePart") and self:_groundUnder(v) then
				grounded += 1
			end
		end
		if grounded == 0 then
			return false, ("no spot in %s has ground within %d studs — the Watcher walks, it does not hover. Lower the spots, add a floor, or set RequireGround = false")
				:format(world.SpotsFolder, self.Settings.GroundSearch)
		end
	end
	return true
end

-- -------------------------------------------------------------------- spots
-- Is there a floor under this spot? The Watcher walks; it does not hover.
function WindowMonster:_groundUnder(spot)
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	local ignore = { self._monster }
	for _, p in ipairs(Players:GetPlayers()) do
		if p.Character then
			table.insert(ignore, p.Character)
		end
	end
	params.FilterDescendantsInstances = ignore
	params.IgnoreWater = true

	local hit = Workspace:Raycast(spot.Position + Vector3.new(0, 2, 0),
		Vector3.new(0, -(self.Settings.GroundSearch + 2), 0), params)
	-- a floor faces up; a wall or a windowsill edge does not
	if hit and hit.Normal.Y > 0.5 then
		return hit.Position
	end
	return nil
end

function WindowMonster:_spots()
	if self._spotCache and #self._spotCache > 0 then
		return self._spotCache
	end
	local out = {}
	local skipped = {}
	for _, v in ipairs(self._spotsFolder:GetChildren()) do
		if v:IsA("BasePart") then
			if self.Settings.RequireGround and not self:_groundUnder(v) then
				table.insert(skipped, v.Name)
			else
				table.insert(out, v)
			end
		end
	end
	if #skipped > 0 then
		self:Log(("ignoring %d ungrounded spot(s): %s"):format(#skipped, table.concat(skipped, ", ")))
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
			part.CanCollide = not hidden
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
function WindowMonster:_placeAt(model, spot)
	if not (model and model.Parent and spot) then
		return
	end

	local ground = self:_groundUnder(spot)
	local base = ground or (spot.Position + Vector3.new(0, 0.5, 0))

	-- stand it ON the surface: offset the pivot by however far the model
	-- extends below its own pivot. Works for the rig and for a template model.
	local boxCF, boxSize = model:GetBoundingBox()
	local pivot = model:GetPivot()
	local pivotToBottom = pivot.Position.Y - (boxCF.Position.Y - boxSize.Y * 0.5)
	local pos = Vector3.new(base.X, base.Y + pivotToBottom, base.Z)

	local target = self:_nearestPlayerTo(pos)
	if target then
		local flat = Vector3.new(target.X - pos.X, 0, target.Z - pos.Z)
		if flat.Magnitude > 0.25 then
			model:PivotTo(CFrame.lookAt(pos, pos + flat.Unit))
			return
		end
	end
	model:PivotTo(CFrame.new(pos))
end

-- --------------------------------------------------------------------- beam
-- A flash counts if the cone reaches the monster. Beam.castThrough ignores
-- see-through parts, so the windows in this house no longer eat the ray.
function WindowMonster:_flashHits(player, originCFrame, originPos, spot)
	if not self._monster or self._state ~= "idle" then
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
	self._spotCache = nil
	self._state = "idle"
	self._retreatUntil = 0
	self._nextTeleport = 0

	self:Track(self._spotsFolder.ChildAdded:Connect(function()
		self._spotCache = nil
	end))
	self:Track(self._spotsFolder.ChildRemoved:Connect(function()
		self._spotCache = nil
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

	self:Log(("active — %d spots, flash-repel"):format(#spots))
end

function WindowMonster:OnPhase(phase, ctx)
	-- re-roll the next teleport so escalation is felt immediately
	self._nextTeleport = 0
end

function WindowMonster:_repel(byPlayer, ctx)
	self:_disarmBreach()
	if self._animator then
		self._animator:Play("recoil")
	end
	local cfg = self.Settings
	self._state = "hidden"

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

	ctx.Director:Cue(byPlayer, { kind = "monsterRepelled", entity = self.Name })
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
	self:_placeAt(self._monster, newSpot)
	self._currentSpot = newSpot
	-- deliberately does NOT re-arm the breach timer: moving between windows is
	-- how it hunts, not a reprieve. Only a successful flash buys time back.

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
	if self._animator then
		self._animator:Destroy()
		self._animator = nil
	end
	self._monster = nil
	self._spotCache = nil
end

return WindowMonster
