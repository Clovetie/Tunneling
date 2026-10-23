-- SoundFX.lua — Production UI & 3D spatial sound effects player
-- Supports both pre-uploaded cloud `rbxassetid://` sounds (instant multiplayer)
-- and local curated CC0 Kenney `.wav` assets (`rbxasset://arena_assets/audio/...`).
local Debris = game:GetService("Debris")
local SoundService = game:GetService("SoundService")

local SoundFX = {}

SoundFX.Presets = {
	click = "rbxasset://arena_assets/audio/click_001.wav",
	hover = "rbxasset://arena_assets/audio/rollover1.wav",
	confirm = "rbxasset://arena_assets/audio/confirmation_001.wav",
	error = "rbxasset://arena_assets/audio/error_001.wav",
	open = "rbxasset://arena_assets/audio/open_001.wav",
	close = "rbxasset://arena_assets/audio/close_001.wav",
	switch = "rbxasset://arena_assets/audio/switch_001.wav",
	reward = "rbxasset://arena_assets/audio/glass_001.wav",
	pluck = "rbxasset://arena_assets/audio/pluck_001.wav",
	toggle = "rbxasset://arena_assets/audio/toggle_001.wav",
}

local function getGroup(groupName)
	local g = SoundService:FindFirstChild(groupName)
	if not g then
		g = Instance.new("SoundGroup")
		g.Name = groupName
		g.Volume = 1
		g.Parent = SoundService
	end
	return g
end

function SoundFX.play(soundOrPreset, opts)
	opts = opts or {}
	local soundId = SoundFX.Presets[soundOrPreset] or tostring(soundOrPreset)
	local s = Instance.new("Sound")
	s.Name = "SFX_" .. tostring(soundOrPreset)
	s.SoundId = soundId
	s.Volume = opts.Volume or 0.55
	local jitter = opts.PitchJitter or 0.05
	local baseSpeed = opts.PlaybackSpeed or 1.0
	s.PlaybackSpeed = baseSpeed + ((math.random() * 2 - 1) * jitter)
	s.SoundGroup = getGroup(opts.Group or "SFX")

	if opts.Position then
		local att = Instance.new("Attachment")
		att.WorldPosition = opts.Position
		att.Parent = workspace.Terrain
		s.RollOffMaxDistance = opts.MaxDistance or 90
		s.RollOffMinDistance = opts.MinDistance or 10
		s.Parent = att
		s:Play()
		Debris:AddItem(att, opts.Lifetime or 5)
	else
		s.Parent = SoundService
		s:Play()
		Debris:AddItem(s, opts.Lifetime or 5)
	end
	return s
end

function SoundFX.setGroupVolume(groupName, volume)
	getGroup(groupName).Volume = math.clamp(tonumber(volume) or 1, 0, 2)
end

return SoundFX
