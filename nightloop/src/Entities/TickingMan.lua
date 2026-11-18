--!nonstrict
--[[
	NightLoop · TickingMan
	Phase 5 Temporal Anomaly & Movement-Freeze Enforcer.

	Manifests a tall clockwork silhouette that ticks down a rhythmic windup
	before freezing time across the house for `FreezeDuration` seconds.
	During the freeze window:
	  - Players who HOLD STILL (velocity below `MoveThreshold`) survive unscathed.
	  - Players who sprint/move during the freeze shatter the temporal lock and
	    take a strike.
	  - Firing a camera Flash directly at TickingMan during windup or freeze
	    banishes the anomaly early.
]]

local Players = game:GetService("Players")
local Workspace = game:GetService("Workspace")

local EntityBase = require(script.Parent.Parent.EntityBase)
local Beam = require(script.Parent.Parent.Beam)

local TickingMan = EntityBase.new("TickingMan")

local function buildClockworkRig(position)
	local model = Instance.new("Model")
	model.Name = "TickingMan_Active"

	local root = Instance.new("Part")
	root.Name = "HumanoidRootPart"
	root.Anchored = true
	root.CanCollide = false
	root.CanTouch = false
	root.CanQuery = true
	root.Material = Enum.Material.Metal
	root.Color = Color3.fromRGB(28, 25, 22)
	root.Size = Vector3.new(2.2, 7.6, 1.2)
	root.Position = position + Vector3.new(0, 3.8, 0)
	root.Parent = model

	local dial = Instance.new("Part")
	dial.Name = "ClockDial"
	dial.Shape = Enum.PartType.Cylinder
	dial.Anchored = true
	dial.CanCollide = false
	dial.CanTouch = false
	dial.Material = Enum.Material.Neon
	dial.Color = Color3.fromRGB(235, 195, 110)
	dial.Size = Vector3.new(0.35, 2.4, 2.4)
	dial.CFrame = root.CFrame * CFrame.new(0, 2.6, -0.55) * CFrame.Angles(0, math.rad(90), 0)
	dial.Parent = model

	local hand = Instance.new("Part")
	hand.Name = "MinuteHand"
	hand.Anchored = true
	hand.CanCollide = false
	hand.CanTouch = false
	hand.Material = Enum.Material.Metal
	hand.Color = Color3.fromRGB(20, 16, 14)
	hand.Size = Vector3.new(0.18, 1.05, 0.12)
	hand.CFrame = root.CFrame * CFrame.new(0, 2.9, -0.75)
	hand.Parent = model

	local glow = Instance.new("PointLight")
	glow.Color = Color3.fromRGB(240, 190, 95)
	glow.Brightness = 2.0
	glow.Range = 22
	glow.Parent = dial

	model.PrimaryPart = root
	model.Parent = Workspace
	return model
end

function TickingMan:Validate(ctx)
	return true
end

function TickingMan:Start(ctx)
	self._state = "dormant"
	self._next = 0
	self._rig = nil
	self._ticksLeft = 0
	self._nextTickAt = 0
	self._freezeEndsAt = 0
	self._anchorPositions = {}

	self:Track(ctx.Flash.Fired:Connect(function(player, originCFrame, originPos, spot)
		self:_onFlash(player, originCFrame, originPos, spot, ctx)
	end))

	self:Log("active — temporal freeze anomaly")
end

function TickingMan:_despawn()
	if self._rig then
		pcall(function()
			self._rig:Destroy()
		end)
		self._rig = nil
	end
	table.clear(self._anchorPositions)
	self._state = "dormant"
end

function TickingMan:_beginWindup(ctx)
	local players = Players:GetPlayers()
	if #players == 0 then
		return false
	end
	local pick = players[math.random(1, #players)]
	local char = pick.Character
	local root = char and char:FindFirstChild("HumanoidRootPart")
	if not root then
		return false
	end

	local angle = math.random() * math.pi * 2
	local dist = self.Settings.SpawnDistance or 18
	local spawnPos = root.Position + Vector3.new(math.cos(angle) * dist, -2.5, math.sin(angle) * dist)

	self:_despawn()
	self._rig = buildClockworkRig(spawnPos)
	self:Own(self._rig)
	self._state = "windup"
	self._ticksLeft = self.Settings.WindupTicks or 4
	self._nextTickAt = os.clock()
	return true
end

function TickingMan:_onFlash(player, originCFrame, originPos, spot, ctx)
	if not self._rig or self._state == "dormant" then
		return
	end
	local halfAngle = spot.Angle / 2
	local range = spot.Range * 1.1
	if Beam.coneHitsTarget(originCFrame, originPos, halfAngle, range,
		self._rig, { player.Character }) then
		self:_despawn()
		ctx.Net.BroadcastCue({
			kind = "tickingBanished",
			entity = self.Name,
			by = player.Name,
		})
		self._next = os.clock() + self:ByIntensity(
			self.Settings.IntervalEasy, self.Settings.IntervalHard, ctx.Intensity)
	end
end

function TickingMan:OnPhase(phase, ctx)
	if self._next == 0 then
		self._next = os.clock() + 5
	end
end

function TickingMan:Update(dt, ctx)
	local cfg = self.Settings
	local now = os.clock()

	if self._state == "dormant" then
		if self._next == 0 then
			local waitSec = self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
			self._next = now + waitSec * (0.75 + math.random() * 0.5)
			return
		end
		if now >= self._next then
			self._next = 0
			self:_beginWindup(ctx)
		end
		return
	end

	if self._state == "windup" then
		if now >= self._nextTickAt then
			if self._ticksLeft > 0 then
				ctx.Net.BroadcastCue({
					kind = "tickingWindup",
					entity = self.Name,
					ticksLeft = self._ticksLeft,
				})
				if self._rig then
					local hand = self._rig:FindFirstChild("MinuteHand")
					if hand and hand:IsA("BasePart") then
						hand.CFrame = hand.CFrame * CFrame.Angles(0, 0, math.rad(90))
					end
				end
				self._ticksLeft -= 1
				self._nextTickAt = now + (cfg.TickInterval or 0.65)
			else
				-- Enter the Time Freeze window: record every player's position
				self._state = "frozen"
				local freezeDur = self:ByIntensity(
					cfg.FreezeDurationEasy or 2.0,
					cfg.FreezeDurationHard or 2.8,
					ctx.Intensity
				)
				self._freezeEndsAt = now + freezeDur
				table.clear(self._anchorPositions)
				for _, player in ipairs(Players:GetPlayers()) do
					local char = player.Character
					local root = char and char:FindFirstChild("HumanoidRootPart")
					if root then
						self._anchorPositions[player] = root.Position
					end
				end
				ctx.Net.BroadcastCue({
					kind = "timeFreeze",
					entity = self.Name,
					duration = freezeDur,
				})
			end
		end
		return
	end

	if self._state == "frozen" then
		local maxDrift = cfg.MoveThreshold or 3.2
		for _, player in ipairs(Players:GetPlayers()) do
			local char = player.Character
			local root = char and char:FindFirstChild("HumanoidRootPart")
			local anchor = self._anchorPositions[player]
			if root and anchor then
				local moved = (root.Position - anchor).Magnitude
				if moved > maxDrift then
					ctx.Director:AddStrike("TickingMan", { player = player, moved = moved })
					ctx.Net.BroadcastCue({
						kind = "timeShatter",
						entity = self.Name,
						player = player.Name,
					})
					self:_despawn()
					self._next = now + self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
					return
				end
			end
		end

		if now >= self._freezeEndsAt then
			ctx.Net.BroadcastCue({
				kind = "timeResume",
				entity = self.Name,
			})
			self:_despawn()
			self._next = now + self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
		end
	end
end

function TickingMan:Stop(ctx)
	self:_despawn()
	self._next = 0
end

return TickingMan
