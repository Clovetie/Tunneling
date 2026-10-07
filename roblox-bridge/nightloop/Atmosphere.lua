--!nonstrict
--[[
	NightLoop · Atmosphere

	Drives Lighting across the night and plays the dawn payoff on survival —
	"dawn light breaks through the 14 windows" from the design doc.

	Entirely optional. Set Config.Atmosphere.ControlLighting = false and this
	module touches nothing, which is what you want if the host game already
	runs its own day/night cycle. Everything it changes is captured on start
	and restored on stop, so it never leaves your place edited.
]]

local Lighting = game:GetService("Lighting")
local TweenService = game:GetService("TweenService")

local Config = require(script.Parent.Config)

local Atmosphere = {}

local saved = nil
local activeTweens = {}

local function settings()
	return Config.Atmosphere
end

local function capture()
	if saved then
		return
	end
	saved = {
		ClockTime = Lighting.ClockTime,
		Brightness = Lighting.Brightness,
		FogEnd = Lighting.FogEnd,
		FogStart = Lighting.FogStart,
		Ambient = Lighting.Ambient,
		OutdoorAmbient = Lighting.OutdoorAmbient,
	}
end

local function cancelTweens()
	for _, tween in ipairs(activeTweens) do
		pcall(function()
			tween:Cancel()
		end)
	end
	table.clear(activeTweens)
end

local function tween(duration, goal, style)
	local info = TweenInfo.new(duration,
		style or Enum.EasingStyle.Sine, Enum.EasingDirection.Out)
	local t = TweenService:Create(Lighting, info, goal)
	table.insert(activeTweens, t)
	t:Play()
	return t
end

function Atmosphere:Begin()
	local cfg = settings()
	if not cfg.ControlLighting then
		return
	end
	capture()
	cancelTweens()

	Lighting.ClockTime = cfg.NightClockTime
	Lighting.Brightness = cfg.NightBrightness
	Lighting.FogEnd = cfg.FogEasy
end

-- fog tightens as the night gets worse
function Atmosphere:SetIntensity(intensity)
	local cfg = settings()
	if not cfg.ControlLighting or not saved then
		return
	end
	local fog = cfg.FogEasy + (cfg.FogHard - cfg.FogEasy) * math.clamp(intensity, 0, 1)
	tween(2.5, { FogEnd = fog })
end

function Atmosphere:Dawn()
	local cfg = settings()
	if not cfg.ControlLighting or not saved then
		return
	end
	cancelTweens()
	tween(cfg.DawnDuration, {
		ClockTime = cfg.DawnClockTime,
		Brightness = cfg.DawnBrightness,
		FogEnd = math.max(cfg.FogEasy, 500),
	}, Enum.EasingStyle.Quad)
end

function Atmosphere:Restore(delay)
	local cfg = settings()
	if not saved then
		return
	end
	local snapshot = saved
	task.delay(delay or 0, function()
		cancelTweens()
		for property, value in pairs(snapshot) do
			pcall(function()
				Lighting[property] = value
			end)
		end
	end)
	saved = nil
end

-- Hook straight onto the Director's signals. Called once from Bootstrap.
function Atmosphere:Bind(Director)
	if not settings().ControlLighting then
		return
	end

	Director.NightStarted:Connect(function()
		Atmosphere:Begin()
	end)

	Director.PhaseChanged:Connect(function(phase)
		Atmosphere:SetIntensity(phase.Intensity or 0)
	end)

	Director.NightEnded:Connect(function(result)
		if result == "survived" then
			Atmosphere:Dawn()
			-- hold the sunrise on screen before handing Lighting back
			Atmosphere:Restore(settings().DawnDuration + 6)
		else
			Atmosphere:Restore(4)
		end
	end)
end

return Atmosphere
