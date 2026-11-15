--!nonstrict
--[[
	NightLoop · Mimic
	Phase 2+ Doppelganger & Cognitive-Rule Trickster.

	Introduces rule-mastery variety alongside WindowMonster:
	  - Spawns in `"decoy"` mode (amber eyes, static silhouette): DO NOT FLASH.
	    Flashing a decoy triggers a psychic glitch that drains 1 flash charge!
	  - Escalates into `"stalk"` mode (crimson eyes, creeping toward nearest player):
	    MUST FLASH to banish before it closes within `StrikeDistance`.
]]

local Players = game:GetService("Players")
local Workspace = game:GetService("Workspace")

local EntityBase = require(script.Parent.Parent.EntityBase)
local Beam = require(script.Parent.Parent.Beam)

local Mimic = EntityBase.new("Mimic")

local function buildMimicRig(position)
	local model = Instance.new("Model")
	model.Name = "Mimic_Active"

	local body = Instance.new("Part")
	body.Name = "Torso"
	body.Anchored = true
	body.CanCollide = false
	body.CanTouch = false
	body.CanQuery = true
	body.Material = Enum.Material.SmoothPlastic
	body.Color = Color3.fromRGB(16, 18, 22)
	body.Transparency = 0.15
	body.Size = Vector3.new(2.0, 5.8, 1.1)
	body.Position = position + Vector3.new(0, 2.9, 0)
	body.Parent = model

	local eyeLeft = Instance.new("Part")
	eyeLeft.Name = "EyeLeft"
	eyeLeft.Shape = Enum.PartType.Ball
	eyeLeft.Anchored = true
	eyeLeft.CanCollide = false
	eyeLeft.Material = Enum.Material.Neon
	eyeLeft.Color = Color3.fromRGB(235, 175, 65)
	eyeLeft.Size = Vector3.new(0.28, 0.28, 0.28)
	eyeLeft.Position = body.Position + Vector3.new(-0.35, 2.1, -0.55)
	eyeLeft.Parent = model

	local eyeRight = eyeLeft:Clone()
	eyeRight.Name = "EyeRight"
	eyeRight.Position = body.Position + Vector3.new(0.35, 2.1, -0.55)
	eyeRight.Parent = model

	model.PrimaryPart = body
	model.Parent = Workspace
	return model
end

function Mimic:Validate(ctx)
	return true
end

function Mimic:Start(ctx)
	self._state = "away"
	self._next = 0
	self._rig = nil
	self._modeChangedAt = 0

	self:Track(ctx.Flash.Fired:Connect(function(player, originCFrame, originPos, spot)
		self:_onFlash(player, originCFrame, originPos, spot, ctx)
	end))

	self:Log("active — doppelganger decoy/stalker")
end

function Mimic:_despawn()
	if self._rig then
		pcall(function()
			self._rig:Destroy()
		end)
		self._rig = nil
	end
	self._state = "away"
end

function Mimic:_setEyeColor(color)
	if not self._rig then
		return
	end
	for _, name in ipairs({ "EyeLeft", "EyeRight" }) do
		local eye = self._rig:FindFirstChild(name)
		if eye and eye:IsA("BasePart") then
			eye.Color = color
		end
	end
end

function Mimic:_spawn(ctx)
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

	local angle = math.random() * math.pi * 2
	local dist = self.Settings.SpawnDistance or 22
	local pos = root.Position + Vector3.new(math.cos(angle) * dist, -2.5, math.sin(angle) * dist)

	self:_despawn()
	self._rig = buildMimicRig(pos)
	self:Own(self._rig)
	self._state = "decoy"
	self._modeChangedAt = os.clock()
	self:_setEyeColor(Color3.fromRGB(235, 175, 65))
	ctx.Net.BroadcastCue({
		kind = "mimicSpawn",
		entity = self.Name,
		mode = "decoy",
	})
	return true
end

function Mimic:_onFlash(player, originCFrame, originPos, spot, ctx)
	if not self._rig or self._state == "away" then
		return
	end
	local halfAngle = spot.Angle / 2
	local range = spot.Range * 1.05
	if not Beam.coneHitsTarget(originCFrame, originPos, halfAngle, range,
		self._rig, { player.Character }) then
		return
	end

	if self._state == "decoy" then
		-- Flashing the passive decoy triggers a cognitive penalty cue
		ctx.Director:Cue(player, {
			kind = "mimicGlitch",
			entity = self.Name,
			warning = "Flashed a passive decoy! Wait for crimson eyes.",
		})
		self._state = "stalk"
		self._modeChangedAt = os.clock()
		self:_setEyeColor(Color3.fromRGB(235, 45, 45))
	elseif self._state == "stalk" then
		self:_despawn()
		ctx.Net.BroadcastCue({
			kind = "mimicBanished",
			entity = self.Name,
			by = player.Name,
		})
		self._next = os.clock() + self:ByIntensity(
			self.Settings.IntervalEasy, self.Settings.IntervalHard, ctx.Intensity)
	end
end

function Mimic:OnPhase(phase, ctx)
	if self._next == 0 then
		self._next = os.clock() + 6
	end
end

function Mimic:Update(dt, ctx)
	local cfg = self.Settings
	local now = os.clock()

	if self._state == "away" then
		if self._next == 0 then
			local waitSec = self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
			self._next = now + waitSec * (0.75 + math.random() * 0.5)
			return
		end
		if now >= self._next then
			self._next = 0
			self:_spawn(ctx)
		end
		return
	end

	local rig = self._rig
	if not rig or not rig.PrimaryPart then
		self:_despawn()
		return
	end

	if self._state == "decoy" then
		local decoyHold = cfg.DecoyDuration or 4.5
		if now - self._modeChangedAt >= decoyHold then
			if ctx.Intensity >= 0.35 or math.random() < 0.65 then
				self._state = "stalk"
				self._modeChangedAt = now
				self:_setEyeColor(Color3.fromRGB(235, 45, 45))
				ctx.Net.BroadcastCue({
					kind = "mimicStalk",
					entity = self.Name,
				})
			else
				self:_despawn()
				self._next = now + self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
			end
		end
		return
	end

	if self._state == "stalk" then
		local nearestPlayer, nearestRoot, bestDist = nil, nil, math.huge
		for _, player in ipairs(Players:GetPlayers()) do
			local char = player.Character
			local root = char and char:FindFirstChild("HumanoidRootPart")
			if root then
				local d = (root.Position - rig.PrimaryPart.Position).Magnitude
				if d < bestDist then
					nearestPlayer, nearestRoot, bestDist = player, root, d
				end
			end
		end

		if not nearestRoot then
			self:_despawn()
			return
		end

		local speed = self:ByIntensity(cfg.StalkSpeedEasy or 7, cfg.StalkSpeedHard or 13, ctx.Intensity)
		local stepDir = (nearestRoot.Position - rig.PrimaryPart.Position)
		stepDir = Vector3.new(stepDir.X, 0, stepDir.Z)
		if stepDir.Magnitude > 0.05 then
			local nextPos = rig.PrimaryPart.Position + stepDir.Unit * (speed * dt)
			rig:PivotTo(CFrame.lookAt(nextPos, Vector3.new(nearestRoot.Position.X, nextPos.Y, nearestRoot.Position.Z)))
		end

		if bestDist <= (cfg.StrikeDistance or 5.5) then
			ctx.Director:AddStrike("Mimic", { player = nearestPlayer })
			self:_despawn()
			self._next = now + self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
			return
		end

		if now - self._modeChangedAt >= (cfg.MaxStalkTime or 12) then
			self:_despawn()
			self._next = now + self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
		end
	end
end

function Mimic:Stop(ctx)
	self:_despawn()
	self._next = 0
end

return Mimic
