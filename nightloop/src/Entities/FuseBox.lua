--!nonstrict
--[[
	NightLoop · FuseBox
	Phase 2+ House Power Grid & Breaker Objective Mechanic.

	Adds an interactive physical objective to the survival loop:
	  - Ensures `Workspace.FuseBox` exists (creates a self-contained breaker box
	    with a status LED and ProximityPrompt if the place does not have one).
	  - Periodically trips the house breaker into a Blackout state.
	  - Players restore power either by interacting with the FuseBox ProximityPrompt
	    OR by firing a camera Flash at the FuseBox within `FlashRestoreRange`.
	  - Leaving the house in blackout past `BlackoutGrace` lands a strike.
]]

local Lighting = game:GetService("Lighting")
local Workspace = game:GetService("Workspace")

local EntityBase = require(script.Parent.Parent.EntityBase)
local Beam = require(script.Parent.Parent.Beam)

local FuseBox = EntityBase.new("FuseBox")

local function ensureBreakerModel(boxName)
	local existing = Workspace:FindFirstChild(boxName, true)
	if existing then
		return existing, false
	end

	local model = Instance.new("Model")
	model.Name = boxName

	local cabinet = Instance.new("Part")
	cabinet.Name = "BreakerCabinet"
	cabinet.Anchored = true
	cabinet.CanCollide = true
	cabinet.Material = Enum.Material.Metal
	cabinet.Color = Color3.fromRGB(48, 54, 62)
	cabinet.Size = Vector3.new(2.4, 3.4, 0.9)
	cabinet.Position = Vector3.new(0, 4.5, -14)
	cabinet.Parent = model

	local led = Instance.new("Part")
	led.Name = "StatusLED"
	led.Shape = Enum.PartType.Ball
	led.Anchored = true
	led.CanCollide = false
	led.Material = Enum.Material.Neon
	led.Color = Color3.fromRGB(69, 224, 160)
	led.Size = Vector3.new(0.45, 0.45, 0.45)
	led.Position = cabinet.Position + Vector3.new(0, 1.1, 0.5)
	led.Parent = model

	local light = Instance.new("PointLight")
	light.Name = "Beacon"
	light.Color = Color3.fromRGB(69, 224, 160)
	light.Brightness = 1.6
	light.Range = 14
	light.Parent = led

	local prompt = Instance.new("ProximityPrompt")
	prompt.Name = "ResetBreakerPrompt"
	prompt.ActionText = "Flip Breaker"
	prompt.ObjectText = "House FuseBox"
	prompt.HoldDuration = 1.2
	prompt.MaxActivationDistance = 11
	prompt.Enabled = false
	prompt.Parent = cabinet

	model.PrimaryPart = cabinet
	model.Parent = Workspace
	return model, true
end

function FuseBox:Validate(ctx)
	return true
end

function FuseBox:Start(ctx)
	self._blackout = false
	self._blackoutAt = 0
	self._next = 0
	self._savedBrightness = nil

	local boxName = self.Settings.BoxName or "FuseBox"
	local box, created = ensureBreakerModel(boxName)
	self._box = box
	if created then
		self:Own(box)
	end

	local prompt = box:FindFirstChildWhichIsA("ProximityPrompt", true)
	self._prompt = prompt
	if prompt then
		self:Track(prompt.Triggered:Connect(function(player)
			self:RestorePower(ctx, player and player.Name or "prompt")
		end))
	end

	self:Track(ctx.Flash.Fired:Connect(function(player, originCFrame, originPos, spot)
		self:_onFlash(player, originCFrame, originPos, spot, ctx)
	end))

	self:Log("active — house breaker objective at " .. tostring(box:GetFullName()))
end

function FuseBox:_setIndicator(tripped)
	if not self._box then
		return
	end
	local led = self._box:FindFirstChild("StatusLED", true)
	local color = tripped and Color3.fromRGB(255, 75, 70) or Color3.fromRGB(69, 224, 160)
	if led and led:IsA("BasePart") then
		led.Color = color
		local beacon = led:FindFirstChildWhichIsA("PointLight")
		if beacon then
			beacon.Color = color
			beacon.Brightness = tripped and 2.5 or 1.4
		end
	end
	if self._prompt then
		self._prompt.Enabled = tripped
	end
end

function FuseBox:TripBreaker(ctx)
	if self._blackout then
		return
	end
	self._blackout = true
	self._blackoutAt = os.clock()
	self._savedBrightness = Lighting.Brightness
	Lighting.Brightness = math.min(Lighting.Brightness, 0.06)
	self:_setIndicator(true)
	ctx.Net.BroadcastCue({
		kind = "powerOutage",
		entity = self.Name,
	})
end

function FuseBox:RestorePower(ctx, source)
	if not self._blackout then
		return
	end
	self._blackout = false
	if self._savedBrightness ~= nil then
		Lighting.Brightness = self._savedBrightness
		self._savedBrightness = nil
	end
	self:_setIndicator(false)
	if ctx and ctx.Net then
		ctx.Net.BroadcastCue({
			kind = "powerRestored",
			entity = self.Name,
			by = source or "breaker",
		})
	end
	local cfg = self.Settings
	self._next = os.clock() + self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx and ctx.Intensity or 0.5)
end

function FuseBox:_onFlash(player, originCFrame, originPos, spot, ctx)
	if not self._blackout or not self._box then
		return
	end
	local maxRange = self.Settings.FlashRestoreRange or 26
	local halfAngle = spot.Angle / 2
	if Beam.coneHitsTarget(originCFrame, originPos, halfAngle, maxRange,
		self._box, { player.Character }) then
		self:RestorePower(ctx, player.Name)
	end
end

function FuseBox:OnPhase(phase, ctx)
	if self._next == 0 then
		self._next = os.clock() + 8
	end
end

function FuseBox:Update(dt, ctx)
	local cfg = self.Settings
	local now = os.clock()

	if not self._blackout then
		if self._next == 0 then
			self._next = now + self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
			return
		end
		if now >= self._next then
			self:TripBreaker(ctx)
		end
		return
	end

	local grace = self:ByIntensity(
		cfg.BlackoutGraceEasy or 28,
		cfg.BlackoutGraceHard or 14,
		ctx.Intensity
	)
	if now - self._blackoutAt >= grace then
		ctx.Director:AddStrike("FuseBox", { reason = "blackout_unfixed" })
		self:RestorePower(ctx, "overload_reset")
	end
end

function FuseBox:Stop(ctx)
	if self._blackout then
		self:RestorePower(ctx, "stopped")
	end
	self._next = 0
end

return FuseBox
