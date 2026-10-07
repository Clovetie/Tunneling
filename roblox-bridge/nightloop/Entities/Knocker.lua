--!nonstrict
--[[
	NightLoop · Knocker
	Knocks on the front door, then escalates to slams. Works with a plain
	anchored door Part — it jolts the door along its own local axis rather than
	swinging it, so no hinge is required.

	Audio is optional. With no KnockSoundIds set you still get the physical
	rattle, which is enough to read in-game; drop IDs into Config to add sound.
]]

local Players = game:GetService("Players")
local Debris = game:GetService("Debris")
local Workspace = game:GetService("Workspace")

local EntityBase = require(script.Parent.Parent.EntityBase)

local Knocker = EntityBase.new("Knocker")

function Knocker:Validate(ctx)
	local door = Workspace:FindFirstChild(self.Settings.DoorPath, true)
	if not door then
		return false, ("Workspace.%s not found"):format(self.Settings.DoorPath)
	end
	if not door:IsA("BasePart") then
		return false, ("%s is a %s, expected a BasePart")
			:format(self.Settings.DoorPath, door.ClassName)
	end
	self._door = door
	return true
end

function Knocker:Start(ctx)
	self._next = 0
	self._busy = false
	self._restCFrame = self._door.CFrame
	self:Log(("active — door at %s"):format(tostring(self._door:GetFullName())))
end

function Knocker:_sound(ids, ctx)
	if type(ids) ~= "table" or #ids == 0 then
		return
	end
	local emitter = Instance.new("Part")
	emitter.Anchored = true
	emitter.CanCollide = false
	emitter.CanQuery = false
	emitter.Transparency = 1
	emitter.Size = Vector3.one
	emitter.CFrame = self._restCFrame
	emitter.Parent = Workspace

	local sound = Instance.new("Sound")
	sound.SoundId = ids[math.random(1, #ids)]
	sound.Volume = self.Settings.Volume
	sound.RollOffMaxDistance = self.Settings.RollOffMax
	sound.RollOffMode = Enum.RollOffMode.InverseTapered
	sound.Parent = emitter
	sound:Play()
	Debris:AddItem(emitter, 6)
end

-- one physical knock: jolt the door along its thin axis and settle back
function Knocker:_jolt(distance, duration)
	local door = self._door
	if not door or not door.Parent then
		return
	end
	-- the door's thinnest axis is the one it should rattle along
	local size = door.Size
	local axis
	if size.X <= size.Y and size.X <= size.Z then
		axis = self._restCFrame.RightVector
	elseif size.Z <= size.Y then
		axis = self._restCFrame.LookVector
	else
		axis = self._restCFrame.UpVector
	end

	door.CFrame = self._restCFrame + axis * distance
	task.wait(duration)
	if door.Parent then
		door.CFrame = self._restCFrame
	end
end

function Knocker:_nearbyPlayer()
	local best, bestDist = nil, self.Settings.ScareRadius
	for _, player in ipairs(Players:GetPlayers()) do
		local character = player.Character
		local root = character and character:FindFirstChild("HumanoidRootPart")
		if root then
			local d = (root.Position - self._restCFrame.Position).Magnitude
			if d <= bestDist then
				best, bestDist = player, d
			end
		end
	end
	return best
end

function Knocker:_knockSet(ctx)
	if self._busy then
		return
	end
	self._busy = true

	local cfg = self.Settings
	local slamming = ctx.PhaseIndex >= cfg.SlamFromPhase
	local count = math.random(cfg.KnocksMin, cfg.KnocksMax)

	task.spawn(function()
		for i = 1, count do
			if not self._door or not self._door.Parent then
				break
			end

			local scale = slamming and 2.2 or 1
			self:_jolt(cfg.RattleStuds * scale, cfg.RattleTime)
			self:_sound(slamming and cfg.SlamSoundIds or cfg.KnockSoundIds, ctx)

			-- someone standing at the door when it goes gets a jolt of their own
			local near = self:_nearbyPlayer()
			if near then
				ctx.Director:Cue(near, {
					kind = "knock",
					entity = self.Name,
					close = true,
					slam = slamming,
				})
			end

			task.wait(0.14 + math.random() * 0.1)
		end
		self._busy = false
	end)
end

function Knocker:OnPhase(phase, ctx)
	self._next = 0
end

function Knocker:Update(dt, ctx)
	local cfg = self.Settings
	local now = os.clock()

	if self._next == 0 then
		self._next = now + self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
		return
	end
	if now < self._next then
		return
	end

	local interval = self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
	self._next = now + interval * (0.7 + math.random() * 0.6)
	self:_knockSet(ctx)
end

function Knocker:Stop(ctx)
	if self._door and self._door.Parent and self._restCFrame then
		self._door.CFrame = self._restCFrame
	end
	self._busy = false
	self._next = 0
end

return Knocker
