--!nonstrict
--[[
	NightLoop · Director   —   THE PUBLIC API

		local Director = require(game.ServerScriptService.NightLoop.Director)

		Director:StartNight()            -- uses Config.Mode
		Director:StartNight({ mode = "OneNight", duration = 300 })
		Director:StopNight("aborted")
		Director:FailNight("caught", player)

		Director.NightStarted:Connect(function(night) end)
		Director.PhaseChanged:Connect(function(phase, index) end)
		Director.NightEnded:Connect(function(result, detail) end)
		          -- result: "survived" | "failed" | "aborted"

		Director:GetState()  -> { running, night, phaseIndex, phaseName,
		                          intensity, elapsed, remaining }

	One-night mode is the default: a single night runs, NightEnded fires, and
	nothing restarts. Drop the folder into another game, call StartNight() from
	your round system, and listen for NightEnded.
]]

local RunService = game:GetService("RunService")
local Players = game:GetService("Players")

local package = script.Parent
local Config = require(package.Config)
local Signal = require(package.Signal)
local Registry = require(package.Registry)
local Net = require(package.Net)
local Flash = require(package.Flash)

local Director = {}

Director.NightStarted = Signal.new()
Director.PhaseChanged = Signal.new()
Director.NightEnded = Signal.new()
Director.StrikeAdded = Signal.new()

local state = {
	running = false,
	night = 0,
	elapsed = 0,
	phaseIndex = 0,
	intensityBonus = 0,
	duration = Config.NightDuration,
	mode = Config.Mode,
	strikes = 0,
	lastStrike = 0,
}

local registry = Registry.new(Config, package.Entities)
local heartbeat = nil
local broadcastAccum = 0
local ctx = nil

local function phaseFor(elapsed)
	local index = 1
	for i, phase in ipairs(Config.Phases) do
		if elapsed >= phase.Start then
			index = i
		else
			break
		end
	end
	return index, Config.Phases[index]
end

local function buildContext()
	return {
		Config = Config,
		Director = Director,
		Net = Net,
		Flash = Flash,
		Beam = require(package.Beam),
		Random = Random.new(),
		Night = state.night,
		Elapsed = 0,
		Remaining = state.duration,
		PhaseIndex = 1,
		Phase = Config.Phases[1],
		Intensity = Config.Phases[1].Intensity,
	}
end

local function refreshContext()
	local index, phase = phaseFor(state.elapsed)
	ctx.Night = state.night
	ctx.Elapsed = state.elapsed
	ctx.Remaining = math.max(0, state.duration - state.elapsed)
	ctx.PhaseIndex = index
	ctx.Phase = phase
	ctx.Intensity = math.clamp(phase.Intensity + state.intensityBonus, 0, Config.EndlessMaxIntensity)
	return index, phase
end

function Director:GetState()
	local phase = Config.Phases[math.max(1, state.phaseIndex)]
	return {
		running = state.running,
		night = state.night,
		mode = state.mode,
		phaseIndex = state.phaseIndex,
		phaseName = phase and phase.Name or "",
		phaseCount = #Config.Phases,
		intensity = ctx and ctx.Intensity or 0,
		elapsed = state.elapsed,
		remaining = math.max(0, state.duration - state.elapsed),
		duration = state.duration,
		strikes = state.strikes or 0,
		maxStrikes = Config.Survival.MaxStrikes,
		hud = {
			showClock = Config.Hud.ShowClock,
			showPhase = Config.Hud.ShowPhase,
			enabled = Config.Hud.Enabled,
		},
	}
end

local function broadcast()
	Net.BroadcastState(Director:GetState())
end

local function callEntities(method, ...)
	for _, entity in ipairs(registry:Active(state.phaseIndex)) do
		local fn = entity[method]
		if type(fn) == "function" then
			local ok, err = pcall(fn, entity, ...)
			if not ok then
				warn(("[NightLoop] %s:%s() errored: %s")
					:format(entity.Name, method, tostring(err)))
			end
		end
	end
end

local function stopInternal(result, detail)
	if not state.running then
		return
	end
	state.running = false

	if heartbeat then
		heartbeat:Disconnect()
		heartbeat = nil
	end

	for _, entity in ipairs(registry:All()) do
		if type(entity.Stop) == "function" then
			pcall(entity.Stop, entity, ctx)
		end
		if type(entity.Cleanup) == "function" then
			pcall(entity.Cleanup, entity)
		end
	end

	broadcast()
	Net.BroadcastCue({
		kind = "nightEnded",
		result = result,
		reason = detail and detail.reason or nil,
		strikes = state.strikes,
		maxStrikes = Config.Survival.MaxStrikes,
	})
	Director.NightEnded:Fire(result, detail)

	if Config.Debug then
		print(("[NightLoop] night %d ended: %s"):format(state.night, result))
	end

	if state.mode == "Endless" and result == "survived" then
		task.delay(3, function()
			Director:StartNight({ mode = "Endless" })
		end)
	end
end

function Director:StartNight(opts)
	opts = opts or {}

	if state.running then
		warn("[NightLoop] StartNight called while a night is already running")
		return false
	end

	state.mode = opts.mode or Config.Mode
	state.duration = opts.duration or Config.NightDuration
	state.elapsed = 0
	state.night += 1
	state.phaseIndex = 1
	state.strikes = 0
	state.lastStrike = 0

	if state.mode == "Endless" then
		state.intensityBonus = (state.night - 1) * Config.EndlessIntensityStep
	else
		state.intensityBonus = opts.intensityBonus or 0
	end

	ctx = buildContext()
	refreshContext()

	local loaded = registry:Load(ctx)
	if #loaded == 0 then
		warn("[NightLoop] no entities active — check Config.Entities")
	end

	state.running = true
	Director.NightStarted:Fire(state.night)
	Director.PhaseChanged:Fire(Config.Phases[1], 1)
	callEntities("Start", ctx)
	callEntities("OnPhase", Config.Phases[1], ctx)
	broadcast()

	if Config.Debug then
		print(("[NightLoop] night %d started (%s, %ds, %d entities)")
			:format(state.night, state.mode, state.duration, #loaded))
	end

	heartbeat = RunService.Heartbeat:Connect(function(dt)
		if not state.running then
			return
		end

		state.elapsed += dt
		local newIndex = refreshContext()

		if newIndex ~= state.phaseIndex then
			state.phaseIndex = newIndex
			local phase = Config.Phases[newIndex]
			Director.PhaseChanged:Fire(phase, newIndex)
			callEntities("OnPhase", phase, ctx)
			broadcast()
			if Config.Debug then
				print(("[NightLoop] phase %d: %s (intensity %.2f)")
					:format(newIndex, phase.Name, ctx.Intensity))
			end
		end

		callEntities("Update", dt, ctx)

		broadcastAccum += dt
		if broadcastAccum >= 0.25 then
			broadcastAccum = 0
			broadcast()
		end

		if state.elapsed >= state.duration then
			stopInternal("survived")
		end
	end)

	return true
end

--[[
	Anything that can hurt the player calls this — a window breach, a Crawl
	hit, suffocation. The strike budget is shared, so entities never need to
	know about each other or about the fail condition.

	Returns true if the strike landed, false if it was swallowed by the grace
	window (stops one event double-hitting across frames).
]]
function Director:AddStrike(source, detail)
	if not state.running then
		return false
	end

	local now = os.clock()
	if now - state.lastStrike < Config.Survival.StrikeGrace then
		return false
	end
	state.lastStrike = now
	state.strikes += 1

	local remaining = math.max(0, Config.Survival.MaxStrikes - state.strikes)
	Director.StrikeAdded:Fire(source, state.strikes, remaining)

	Net.BroadcastCue({
		kind = "strike",
		source = source,
		strikes = state.strikes,
		maxStrikes = Config.Survival.MaxStrikes,
		remaining = remaining,
		detail = detail,
	})
	broadcast()

	if Config.Debug then
		print(("[NightLoop] strike %d/%d from %s")
			:format(state.strikes, Config.Survival.MaxStrikes, tostring(source)))
	end

	if state.strikes >= Config.Survival.MaxStrikes then
		stopInternal("failed", { reason = source, strikes = state.strikes })
	end
	return true
end

function Director:GetStrikes()
	return state.strikes, Config.Survival.MaxStrikes
end

function Director:StopNight(reason)
	stopInternal("aborted", reason)
end

function Director:FailNight(reason, player)
	stopInternal("failed", { reason = reason, player = player })
end

function Director:IsRunning()
	return state.running
end

-- Let entities request a client-side cue without importing Net themselves.
function Director:Cue(player, cue)
	Net.SendCue(player, cue)
end

Players.PlayerRemoving:Connect(function()
	if state.running and #Players:GetPlayers() <= 1 and Config.Mode == "OneNight" then
		-- last player left; nothing to run the night for
		stopInternal("aborted", "no players")
	end
end)

return Director
