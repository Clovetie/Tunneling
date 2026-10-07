--!nonstrict
--[==[
	jobs/preview_climb.lua - run ONE real climb on the real house, in the
	viewport, so it can be judged without playing a whole night.

	It classifies your actual Workspace.Spots, picks the climb spot with the
	biggest drop (or WANTED, below), builds the rig, and drives the entity's
	own _startClimb / _updateClimb with a Heartbeat loop while you watch. The
	arrival pose is then held for HOLD seconds before the rig is destroyed.

	Read-only with respect to the game: it never starts a night, never touches
	the live entity, and the rig it builds is destroyed on the way out - even
	if something throws.

	Edit the two constants, then:
		.\ab.ps1 runfile preview_climb.lua

	Watch the viewport. The CLI blocks until the job returns (it uses `wait`
	in the job envelope, default 60s - plenty for a 7s climb plus the hold).
]==]

local H = game:GetService("HttpService")
local Workspace = game:GetService("Workspace")
local RunService = game:GetService("RunService")

-- Name of a spot to climb, e.g. "3". nil = the climb spot with the biggest
-- drop, which is usually the one you want to look at first.
local WANTED = nil
-- Seconds to hold the arrival pose so you can walk the camera around it.
local HOLD = 4

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

local okRig, MonsterRig = pcall(require, nl:FindFirstChild("MonsterRig"))
local okAnim, MonsterAnimator = pcall(require, nl:FindFirstChild("MonsterAnimator"))
local okConfig, Config = pcall(require, nl:FindFirstChild("Config"))
local okEntity, WindowMonster = pcall(require, nl.Entities and nl.Entities.WindowMonster)
if not (okRig and okAnim and okConfig and okEntity) then
	return H:JSONEncode({
		error = "a module failed to require",
		rig = okRig, animator = okAnim, config = okConfig, entity = okEntity,
	})
end

local W = Config.Entities.WindowMonster
local report = {
	wanted = WANTED,
	climbing = W.Climbing ~= nil and W.Climbing.Enabled,
}

local probe = setmetatable({ Name = "ClimbPreview", Settings = W }, { __index = WindowMonster })

-- ------------------------------------------------------------------- pick a spot
local folder = Workspace:FindFirstChild(Config.World.SpotsFolder)
if not folder then
	return H:JSONEncode({ error = ("no Workspace.%s"):format(Config.World.SpotsFolder) })
end

local climbSpots = {}
local skipped = 0
for _, v in ipairs(folder:GetChildren()) do
	if v:IsA("BasePart") then
		local ok, sup = pcall(function()
			return probe:_classify(v)
		end)
		if ok and sup and sup.mode == "climb" then
			table.insert(climbSpots, { part = v, sup = sup })
		elseif not (ok and sup) then
			skipped += 1
		end
	end
end
report.climbSpots = #climbSpots
report.skippedSpots = skipped

local chosen, sup = nil, nil
for _, entry in ipairs(climbSpots) do
	if WANTED and entry.part.Name == WANTED then
		chosen, sup = entry.part, entry.sup
	end
end
if not chosen then
	local best = -1
	for _, entry in ipairs(climbSpots) do
		if entry.sup.drop > best then
			best, chosen, sup = entry.sup.drop, entry.part, entry.sup
		end
	end
end
if not chosen then
	return H:JSONEncode({ error = "no spot classifies as a climb - nothing to preview", report = report })
end

-- ---------------------------------------------------------------------- the rig
local rig = MonsterRig.build({ name = "ClimbPreview_Rig" })
rig.Parent = Workspace
local anim = MonsterAnimator.new(rig)
anim:SetIntensity(0.35)

probe._monster = rig
probe._animator = anim

-- armed before anything below can throw: this rig must never be left behind
local cleanup = function()
	if anim then
		anim:Destroy()
	end
	if rig then
		rig:Destroy()
	end
end

local okDirector, Director = pcall(require, nl:FindFirstChild("Director"))
local okNet, Net = pcall(require, nl:FindFirstChild("Net"))
if not (okDirector and okNet) then
	cleanup()
	return H:JSONEncode({ error = "Director/Net failed to require" })
end

local intensity = 0.3
local okState, state = pcall(function()
	return Director.GetState and Director:GetState()
end)
if okState and state and state.intensity then
	intensity = state.intensity
end

local ctx = {
	Intensity = intensity,
	Net = Net,
	Director = Director,
}

local ok, err = pcall(function()
	probe:_startClimb(chosen, sup, ctx)
end)
if not ok then
	cleanup()
	return H:JSONEncode({ error = "_startClimb threw: " .. tostring(err), report = report })
end

report.spot = {
	name = chosen.Name,
	drop = n2(sup.drop),
	wall = sup.wall and n2(sup.wall.dist) or nil,
	startY = n2(rig:GetPivot().Position.Y),
}
report.planned = {
	seconds = n2(probe._climb and probe._climb.dur or 0),
	rise = n2(probe._climb and probe._climb.rise or 0),
}

-- ------------------------------------------------------------------- animate
-- Heartbeat normally fires in Edit mode, which is what makes this animate in
-- the viewport. Check it is actually alive before relying on it: a Heartbeat
-- that never fires would park the job until the bridge timed out rather than
-- falling through to the fixed-step path.
local hbFired = 0
local probeConn = RunService.Heartbeat:Connect(function()
	hbFired += 1
end)
task.wait(0.5)
probeConn:Disconnect()
local useHeartbeat = hbFired > 0
report.heartbeat = { fired = hbFired, used = useHeartbeat }

local function step()
	if useHeartbeat then
		return math.min(RunService.Heartbeat:Wait(), 0.1)
	end
	task.wait(1 / 60)
	return 1 / 60
end

local t0 = os.clock()
local frames = 0
while probe._state == "climbing" and (os.clock() - t0) < 30 do
	local dt = step()
	frames += 1
	anim:SetIntensity(intensity)
	anim:Update(dt, nil)
	probe:_updateClimb(dt, ctx)
end

local endCF = rig:GetPivot()
report.climb = {
	frames = frames,
	wallClock = n2(os.clock() - t0),
	finished = probe._state == "idle",
	endY = n2(endCF.Position.Y),
	rise = n2(endCF.Position.Y - report.spot.startY),
	cyclePhase = n2(anim.cyclePhase),
	lookX = n2(endCF.LookVector.X),
	lookZ = n2(endCF.LookVector.Z),
	pos = { n2(endCF.Position.X), n2(endCF.Position.Y), n2(endCF.Position.Z) },
}

-- -------------------------------------------------------------- hold and look
-- Keep breathing/animating at the window so it can be inspected from any angle.
local tHold = os.clock()
while (os.clock() - tHold) < HOLD do
	local dt = step()
	anim:Update(dt, nil)
end

-- ----------------------------------------------------------------- clip check
-- How much of the rig is inside the house at the arrival pose. 0 means it is
-- hanging clear; a handful of parts means it is pressed into the sill. Tune
-- Config.Entities.WindowMonster.Climbing.Grip if it is buried.
local params = OverlapParams.new()
params.FilterType = Enum.RaycastFilterType.Exclude
params.FilterDescendantsInstances = { rig }
params.MaxParts = 100
local touching, hits = 0, {}
for _, part in ipairs(rig:GetDescendants()) do
	if part:IsA("BasePart") and part.Name ~= "Root" then
		local okParts, list = pcall(function()
			return Workspace:GetPartsInPart(part, params)
		end)
		if okParts and list then
			for _, other in ipairs(list) do
				touching += 1
				if #hits < 10 then
					table.insert(hits, other.Name)
				end
			end
		end
	end
end
report.clip = { partsTouchingGeometry = touching, sampleNames = hits }

cleanup()
report.cleanedUp = Workspace:FindFirstChild("ClimbPreview_Rig") == nil
report.ok = report.climb.finished and report.cleanedUp

return H:JSONEncode(report)
