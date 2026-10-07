--!nonstrict
--[[
	NightLoop · Whisperer
	Audio misdirection. Places a 3D sound at a random point around a player so
	it seems to come from somewhere in the house. From phase 2 it starts
	mimicking window sounds, which is what makes the WindowMonster dangerous —
	you stop trusting what you hear.

	Needs audio IDs. Put them in Config.Entities.Whisperer.WhisperSoundIds.
	With none set the entity disables itself and says so, rather than running
	silently and looking broken.
]]

local Players = game:GetService("Players")
local Debris = game:GetService("Debris")
local Workspace = game:GetService("Workspace")

local EntityBase = require(script.Parent.Parent.EntityBase)

local Whisperer = EntityBase.new("Whisperer")

function Whisperer:Validate(ctx)
	local ids = self.Settings.WhisperSoundIds
	if type(ids) ~= "table" or #ids == 0 then
		return false,
			"no WhisperSoundIds set — add audio IDs in Config.Entities.Whisperer"
	end
	return true
end

function Whisperer:Start(ctx)
	self._next = 0
	self:Log(("active — %d whisper clips"):format(#self.Settings.WhisperSoundIds))
end

function Whisperer:_pick(list)
	if type(list) ~= "table" or #list == 0 then
		return nil
	end
	return list[math.random(1, #list)]
end

function Whisperer:_playNear(player, soundId, ctx)
	local character = player.Character
	local root = character and character:FindFirstChild("HumanoidRootPart")
	if not root then
		return
	end

	local cfg = self.Settings
	local angle = math.random() * math.pi * 2
	local distance = cfg.MinDistance
		+ math.random() * (cfg.MaxDistance - cfg.MinDistance)

	local offset = Vector3.new(
		math.cos(angle) * distance,
		math.random(-3, 4),
		math.sin(angle) * distance
	)

	local emitter = Instance.new("Part")
	emitter.Name = "WhisperEmitter"
	emitter.Anchored = true
	emitter.CanCollide = false
	emitter.CanQuery = false
	emitter.CanTouch = false
	emitter.Transparency = 1
	emitter.Size = Vector3.one
	emitter.Position = root.Position + offset
	emitter.Parent = Workspace

	local sound = Instance.new("Sound")
	sound.SoundId = soundId
	sound.Volume = cfg.Volume
	sound.RollOffMaxDistance = cfg.RollOffMax
	sound.RollOffMode = Enum.RollOffMode.InverseTapered
	sound.Parent = emitter
	sound:Play()

	-- clean up after the clip, with a ceiling so a bad ID cannot leak parts
	local lifetime = 8
	if sound.TimeLength > 0 then
		lifetime = math.min(30, sound.TimeLength + 1)
	end
	Debris:AddItem(emitter, lifetime)

	ctx.Director:Cue(player, { kind = "whisper", entity = self.Name })
end

function Whisperer:Update(dt, ctx)
	local now = os.clock()
	if self._next == 0 then
		self._next = now + self:ByIntensity(
			self.Settings.IntervalEasy, self.Settings.IntervalHard, ctx.Intensity)
		return
	end
	if now < self._next then
		return
	end

	local cfg = self.Settings
	local interval = self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
	-- jitter so it never feels metronomic
	self._next = now + interval * (0.65 + math.random() * 0.7)

	local players = Players:GetPlayers()
	if #players == 0 then
		return
	end
	local player = players[math.random(1, #players)]

	-- from phase 4 it starts faking glass to mask real window activity
	local list = cfg.WhisperSoundIds
	if ctx.PhaseIndex >= 4
		and type(cfg.FakeGlassSoundIds) == "table"
		and #cfg.FakeGlassSoundIds > 0
		and math.random() < 0.4
	then
		list = cfg.FakeGlassSoundIds
	end

	local id = self:_pick(list)
	if id then
		self:_playNear(player, id, ctx)
	end
end

function Whisperer:Stop(ctx)
	self._next = 0
end

return Whisperer
