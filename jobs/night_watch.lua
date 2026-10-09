--!nonstrict
--[==[
	jobs/night_watch.lua - what is the Watcher actually doing right now?

	Run this DURING a playtest (it works in run mode: the server-side plugin
	instance does the polling). It samples the live monster every EVERY seconds
	for SECONDS and reports a trace you can read at a glance:

	  y      height of the model's pivot - a climb shows as a steady rise
	  knee   KneeR C0 pitch in degrees. Every pose holds it near -4..-22, EXCEPT
	         the climb, which drives it to -14..-118. knee < -25 means the climb
	         animation is playing, which is how `climbing` is decided below.
	  sh     ShoulderR pitch, 2 at watch, ~70 recoiling or lunging, sweeping
	         24..166 while climbing.
	  vis    1 = shown, 0 = hidden (retreated or blinked out)

	The entity objects live in a local inside Director.lua, so a job cannot
	reach them - that is deliberate, it keeps entities from being poked. This
	watches the model instead, which is the more honest test anyway.

	Defaults: 20 seconds at 2 Hz. The CLI prints the first 4000 characters of
	a result, so keep SECONDS / EVERY small enough to fit if you raise them.
]==]

local H = game:GetService("HttpService")
local Workspace = game:GetService("Workspace")

local SECONDS = 20
local EVERY = 0.5

local function n2(x)
	if x ~= x then
		return "NaN"
	end
	if x == math.huge then
		return "inf"
	end
	if x == -math.huge then
		return "-inf"
	end
	return math.floor(x * 100 + 0.5) / 100
end

local nl = game:GetService("ServerScriptService"):FindFirstChild("NightLoop")
if not nl then
	return H:JSONEncode({ error = "no ServerScriptService.NightLoop" })
end
local okConfig, Config = pcall(require, nl:FindFirstChild("Config"))
local okDirector, Director = pcall(require, nl:FindFirstChild("Director"))
if not (okConfig and okDirector) then
	return H:JSONEncode({ error = "Config/Director failed to require" })
end

local function motor(named)
	local model = Workspace:FindFirstChild("WindowMonster_Active")
	if not model then
		return nil
	end
	for _, d in ipairs(model:GetDescendants()) do
		if d:IsA("Motor6D") and d.Name == named then
			return d
		end
	end
	return nil
end

local function pitchDeg(cf)
	local x = cf:ToEulerAnglesXYZ()
	return math.deg(x)
end

local spotsFolder = Workspace:FindFirstChild(Config.World.SpotsFolder)

local t, ys, knees, shoulders, vis, strikes, names = {}, {}, {}, {}, {}, {}, {}
local t0 = os.clock()
local samples, climbSamples, peakRise = 0, 0, 0
local lastY, runStartY = nil, nil
local bestSpot, bestDist = nil, math.huge
local finalPos = nil

while (os.clock() - t0) < SECONDS do
	local now = os.clock() - t0
	local model = Workspace:FindFirstChild("WindowMonster_Active")

	local y, knee, sh, visN = nil, nil, nil, nil
	if model then
		local root = model.PrimaryPart
		if root then
			y = root.Position.Y
			finalPos = { n2(root.Position.X), n2(root.Position.Y), n2(root.Position.Z) }
			if spotsFolder then
				for _, s in ipairs(spotsFolder:GetChildren()) do
					if s:IsA("BasePart") then
						local d = (s.Position - root.Position).Magnitude
						if d < bestDist then
							bestDist, bestSpot = d, s.Name
						end
					end
				end
			end
		end
		local kneeM = motor("KneeR")
		local shM = motor("ShoulderR")
		if kneeM then
			knee = pitchDeg(kneeM.C0)
		end
		if shM then
			sh = pitchDeg(shM.C0)
		end
		-- 1 = visible. The rig hides by driving Transparency to 1.
		local part = model:FindFirstChild("Torso") or model:FindFirstChild("Head")
		if part and part:IsA("BasePart") then
			visN = part.Transparency < 0.9 and 1 or 0
		end

		if lastY ~= nil and y ~= nil then
			local rise = y - lastY
			if rise > 0.02 then
				if runStartY == nil then
					runStartY = lastY
				end
				peakRise = math.max(peakRise, y - runStartY)
			else
				runStartY = nil
			end
		end
		lastY = y
		if knee ~= nil and knee < -25 then
			climbSamples += 1
		end
	end

	local st = {}
	local okState, state = pcall(function()
		return Director:GetState()
	end)
	if okState and state then
		st = state
	end

	samples += 1
	table.insert(t, n2(now))
	table.insert(ys, y and n2(y) or -999)
	table.insert(knees, knee and n2(knee) or -999)
	table.insert(shoulders, sh and n2(sh) or -999)
	table.insert(vis, visN or -1)
	table.insert(strikes, st.strikes or 0)
	table.insert(names, st.phaseName or "")

	task.wait(EVERY)
end

local okState, state = pcall(function()
	return Director:GetState()
end)

return H:JSONEncode({
	seconds = SECONDS,
	every = EVERY,
	samples = samples,
	director = okState and {
		running = state.running,
		phase = state.phaseName,
		phaseIndex = state.phaseIndex,
		intensity = n2(state.intensity or 0),
		elapsed = n2(state.elapsed or 0),
		strikes = state.strikes,
		maxStrikes = state.maxStrikes,
		showPhase = state.hud and state.hud.showPhase,
	} or { error = "GetState failed" },
	trace = {
		t = t,
		y = ys,
		knee = knees,
		shoulder = shoulders,
		vis = vis,
		strikes = strikes,
		phase = names,
	},
	summary = {
		sawClimbPose = climbSamples > 0,
		climbSamples = climbSamples,
		longestRise = n2(peakRise),
		monsterPresent = finalPos ~= nil,
		finalPos = finalPos,
		nearestSpot = bestSpot,
		nearestSpotDist = bestSpot and n2(bestDist) or nil,
		-- -999 in a trace means "no monster to sample at that instant"
		sentinel = -999,
	},
})
