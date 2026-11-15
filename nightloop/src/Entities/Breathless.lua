--!nonstrict
--[[
	NightLoop · Breathless
	Phase 4+ Room Suffocation & Volumetric Miasma Hazard.

	Spawns a creeping cold miasma volume in the room/area around a player (or near
	a window spot). While a player stands inside the miasma radius, their oxygen
	drains from 1.0 -> 0.0; stepping outside recovers oxygen. Firing a camera Flash
	into the miasma core disperses it immediately. Reaching 0 oxygen lands a strike.
]]

local Players = game:GetService("Players")
local Workspace = game:GetService("Workspace")

local EntityBase = require(script.Parent.Parent.EntityBase)
local Beam = require(script.Parent.Parent.Beam)

local Breathless = EntityBase.new("Breathless")

function Breathless:Validate(ctx)
	return true
end

function Breathless:Start(ctx)
	self._next = 0
	self._activeZone = nil
	self._zoneCenter = nil
	self._zoneStarted = 0
	self._oxygenByPlayer = {}
	self._insideByPlayer = {}
	self._lastCueAt = {}

	self:Track(ctx.Flash.Fired:Connect(function(player, originCFrame, originPos, spot)
		self:_onFlash(player, originCFrame, originPos, spot, ctx)
	end))

	self:Log("active — room miasma hazard (flash or evacuate to survive)")
end

function Breathless:_clearZone(ctx, reason)
	if self._activeZone then
		pcall(function()
			self._activeZone:Destroy()
		end)
		self._activeZone = nil
	end
	self._zoneCenter = nil
	for player, wasInside in pairs(self._insideByPlayer) do
		if wasInside then
			ctx.Director:Cue(player, {
				kind = "breathlessClear",
				entity = self.Name,
				reason = reason or "cleared",
				oxygen = 1,
			})
		end
	end
	table.clear(self._insideByPlayer)
end

function Breathless:_spawnZone(ctx)
	local players = Players:GetPlayers()
	if #players == 0 then
		return false
	end
	local target = players[math.random(1, #players)]
	local char = target.Character
	local root = char and char:FindFirstChild("HumanoidRootPart")
	if not root then
		return false
	end

	local cfg = self.Settings
	local radius = cfg.Radius or 18
	local center = root.Position + Vector3.new(
		(math.random() - 0.5) * 6,
		-1.5,
		(math.random() - 0.5) * 6
	)

	local model = Instance.new("Model")
	model.Name = "Breathless_Miasma"

	local core = Instance.new("Part")
	core.Name = "MiasmaCore"
	core.Shape = Enum.PartType.Ball
	core.Anchored = true
	core.CanCollide = false
	core.CanTouch = false
	core.CanQuery = true
	core.Material = Enum.Material.ForceField
	core.Color = Color3.fromRGB(52, 130, 165)
	core.Transparency = 0.62
	core.Size = Vector3.new(radius * 1.35, 9, radius * 1.35)
	core.Position = center
	core.Parent = model

	local light = Instance.new("PointLight")
	light.Color = Color3.fromRGB(70, 165, 210)
	light.Brightness = 1.4
	light.Range = radius * 1.4
	light.Parent = core

	local emitter = Instance.new("ParticleEmitter")
	emitter.Rate = 24
	emitter.Lifetime = NumberRange.new(1.4, 2.6)
	emitter.Speed = NumberRange.new(0.6, 2.0)
	emitter.SpreadAngle = Vector2.new(180, 35)
	emitter.Color = ColorSequence.new(
		Color3.fromRGB(48, 120, 155),
		Color3.fromRGB(14, 24, 38)
	)
	emitter.Transparency = NumberSequence.new({
		NumberSequenceKeypoint.new(0, 0.85),
		NumberSequenceKeypoint.new(0.4, 0.45),
		NumberSequenceKeypoint.new(1, 1),
	})
	emitter.Size = NumberSequence.new({
		NumberSequenceKeypoint.new(0, 2.5),
		NumberSequenceKeypoint.new(1, 6.0),
	})
	emitter.Parent = core

	model.PrimaryPart = core
	model.Parent = Workspace

	self:_clearZone(ctx, "replaced")
	self._activeZone = model
	self:Own(model)
	self._zoneCenter = center
	self._zoneStarted = os.clock()
	return true
end

function Breathless:_onFlash(player, originCFrame, originPos, spot, ctx)
	if not self._activeZone or not self.Settings.FlashDisperses then
		return
	end
	local halfAngle = spot.Angle / 2
	local range = spot.Range * 1.05
	if Beam.coneHitsTarget(originCFrame, originPos, halfAngle, range,
		self._activeZone, { player.Character }) then
		self:_clearZone(ctx, "flashed")
		self._next = os.clock() + self:ByIntensity(
			self.Settings.IntervalEasy, self.Settings.IntervalHard, ctx.Intensity)
	end
end

function Breathless:OnPhase(phase, ctx)
	if self._next == 0 then
		self._next = os.clock() + 4
	end
end

function Breathless:Update(dt, ctx)
	local cfg = self.Settings
	local now = os.clock()

	if not self._activeZone then
		if self._next == 0 then
			local waitSec = self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
			self._next = now + waitSec * (0.75 + math.random() * 0.5)
			return
		end
		if now >= self._next then
			self._next = 0
			self:_spawnZone(ctx)
		end
		return
	end

	local duration = cfg.ZoneDuration or 14
	if now - self._zoneStarted >= duration then
		self:_clearZone(ctx, "expired")
		local waitSec = self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
		self._next = now + waitSec * (0.75 + math.random() * 0.5)
		return
	end

	local radius = cfg.Radius or 18
	local drainTime = math.max(2.5, self:ByIntensity(
		cfg.SuffocateTimeEasy or 7.5,
		cfg.SuffocateTimeHard or 3.8,
		ctx.Intensity
	))
	local recoveryRate = cfg.RecoveryRate or 0.45

	for _, player in ipairs(Players:GetPlayers()) do
		local char = player.Character
		local root = char and char:FindFirstChild("HumanoidRootPart")
		local ox = self._oxygenByPlayer[player]
		if ox == nil then
			ox = 1.0
		end

		local inside = false
		if root and self._zoneCenter then
			inside = (root.Position - self._zoneCenter).Magnitude <= radius
		end

		if inside then
			ox = math.max(0, ox - (dt / drainTime))
			if not self._insideByPlayer[player] then
				self._insideByPlayer[player] = true
				ctx.Director:Cue(player, {
					kind = "breathlessEnter",
					entity = self.Name,
					oxygen = ox,
				})
			elseif now - (self._lastCueAt[player] or 0) >= 0.35 then
				self._lastCueAt[player] = now
				ctx.Director:Cue(player, {
					kind = "breathlessTick",
					entity = self.Name,
					oxygen = ox,
				})
			end

			if ox <= 0 then
				self._oxygenByPlayer[player] = 1.0
				ctx.Director:AddStrike("Breathless", { player = player })
				self:_clearZone(ctx, "suffocated")
				self._next = now + self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
				return
			end
		else
			ox = math.min(1.0, ox + dt * recoveryRate)
			if self._insideByPlayer[player] then
				self._insideByPlayer[player] = false
				ctx.Director:Cue(player, {
					kind = "breathlessClear",
					entity = self.Name,
					oxygen = ox,
				})
			end
		end

		self._oxygenByPlayer[player] = ox
	end
end

function Breathless:Stop(ctx)
	self:_clearZone(ctx, "stopped")
	table.clear(self._oxygenByPlayer)
	table.clear(self._lastCueAt)
	self._next = 0
end

return Breathless
