--!nonstrict
--[[
	NightLoop · Crawl

	From the design doc:
	  Phase 3  skittering shadows across ceilings/walls, paranoia peaks
	  Phase 4  drops into a room briefly - retreat or risk being hit
	  Phase 5  appears more boldly, skitters through hallways

	Needs nothing named in your place. It looks for a ceiling above the player
	first, and falls back to the nearest WALL — which matters here, because
	this house has no roof: an interior survey found 0 of 25 columns with
	anything overhead, but walls in 8 of 8 directions. A ceiling-only crawler
	would never have appeared once.

	Flash interaction is consistent with the Window Monster: catch it in the
	beam and it bolts. Unlike the Window Monster it cannot be killed off, only
	driven away — the flash buys you distance, not safety.
]]

local Players = game:GetService("Players")
local Workspace = game:GetService("Workspace")

local EntityBase = require(script.Parent.Parent.EntityBase)
local CrawlerRig = require(script.Parent.Parent.CrawlerRig)
local Beam = require(script.Parent.Parent.Beam)

local Crawl = EntityBase.new("Crawl")

function Crawl:Validate(ctx)
	-- nothing place-specific to check; the ceiling probe is done per-appearance
	return true
end

function Crawl:Start(ctx)
	self._next = 0
	self._state = "away"
	self._rig = nil
	self._gait = nil
	self._path = nil
	self._dropAt = 0
	self._contact = 0

	self:Track(ctx.Flash.Fired:Connect(function(player, cf, pos, spot)
		self:_onFlash(player, cf, pos, spot, ctx)
	end))

	self:Log("active — ceiling skitter, drops from phase "
		.. tostring(self.Settings.DropFromPhase))
end

-- ------------------------------------------------------------------ helpers

function Crawl:_players()
	local list = {}
	for _, player in ipairs(Players:GetPlayers()) do
		local char = player.Character
		local root = char and char:FindFirstChild("HumanoidRootPart")
		if root then
			table.insert(list, { player = player, root = root })
		end
	end
	return list
end

-- look for a ceiling above a point. Returns position + normal, or nil.
function Crawl:_ceilingAbove(position, ignore)
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	params.FilterDescendantsInstances = ignore or {}
	params.IgnoreWater = true

	local hit = Workspace:Raycast(position + Vector3.new(0, 2, 0),
		Vector3.new(0, self.Settings.CeilingSearch, 0), params)
	if not hit then
		return nil
	end
	-- a ceiling faces downward; anything else is a shelf or a prop
	if hit.Normal.Y > -0.5 then
		return nil
	end
	return hit.Position, hit.Normal
end

-- nearest wall around a point, scanning horizontally. Returns pos, normal.
function Crawl:_wallNear(position, ignore)
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	params.FilterDescendantsInstances = ignore or {}
	params.IgnoreWater = true

	local best, bestNormal, bestDist = nil, nil, math.huge
	for i = 0, 7 do
		local a = i * math.pi / 4
		local dir = Vector3.new(math.cos(a), 0, math.sin(a))
		local hit = Workspace:Raycast(position, dir * self.Settings.WallSearch, params)
		-- a wall is near-vertical; floors and shelves are not
		if hit and math.abs(hit.Normal.Y) < 0.5 then
			local d = (hit.Position - position).Magnitude
			if d < bestDist and d > 2 then
				best, bestNormal, bestDist = hit.Position, hit.Normal, d
			end
		end
	end
	return best, bestNormal
end

-- Re-attach to the surface each frame so the crawler hugs geometry instead of
-- sliding through it. Casts back INTO the surface from just off it.
function Crawl:_reattach(point, normal, ignore)
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	params.FilterDescendantsInstances = ignore or {}
	params.IgnoreWater = true
	local hit = Workspace:Raycast(point + normal * 3, -normal * 6, params)
	if hit then
		return hit.Position, hit.Normal
	end
	return nil
end

function Crawl:_despawn()
	if self._gait then
		self._gait:Destroy()
		self._gait = nil
	end
	if self._rig then
		self._rig:Destroy()
		self._rig = nil
	end
	self._path = nil
	self._state = "away"
end

function Crawl:_spawnRun(ctx)
	local targets = self:_players()
	if #targets == 0 then
		return false
	end
	local pick = targets[math.random(1, #targets)]
	local centre = pick.root.Position
	local cfg = self.Settings
	local half = cfg.MinRunDistance * 0.5

	local startPos, normal, dir, surface

	-- 1. ceiling, if this place has one
	local angle = math.random() * math.pi * 2
	local flatDir = Vector3.new(math.cos(angle), 0, math.sin(angle))
	local ceilA, ceilNormal = self:_ceilingAbove(centre - flatDir * half)
	local ceilB = self:_ceilingAbove(centre + flatDir * half)
	if ceilA and ceilB then
		startPos, normal, dir, surface = ceilA, ceilNormal, flatDir, "ceiling"
	else
		-- 2. wall. Run along its horizontal tangent at roughly head height.
		local wallPos, wallNormal = self:_wallNear(centre + Vector3.new(0, cfg.WallHeight, 0))
		if not wallPos then
			return false
		end
		local tangent = wallNormal:Cross(Vector3.yAxis)
		if tangent.Magnitude < 1e-3 then
			return false
		end
		tangent = tangent.Unit
		startPos = wallPos - tangent * half
		normal, dir, surface = wallNormal, tangent, "wall"
	end

	local rig = CrawlerRig.build({ name = "Crawl_Active" })
	rig.Parent = Workspace
	for _, d in ipairs(rig:GetDescendants()) do
		if d:IsA("BasePart") then
			d.Anchored = (d == rig.PrimaryPart)
			d.CanCollide = false
		end
	end
	-- sit just off the surface so the body does not clip into it
	rig:PivotTo(CrawlerRig.surfaceCFrame(
		startPos + normal * cfg.SurfaceOffset, normal, dir))

	self._rig = rig
	self:Own(rig)
	self._gait = CrawlerRig.newGait(rig)
	self._path = {
		from = startPos,
		to = startPos + dir * cfg.MinRunDistance,
		dir = dir,
		normal = normal,
		surface = surface,
		t = 0,
		length = cfg.MinRunDistance,
		target = pick.player,
	}
	self._state = "skitter"

	ctx.Net.BroadcastCue({ kind = "crawl", entity = self.Name, surface = surface })
	return true
end

-- drop off the surface into the room
function Crawl:_drop(ctx)
	if not self._rig or not self._rig.PrimaryPart then
		return
	end
	local from = self._rig.PrimaryPart.Position
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	params.FilterDescendantsInstances = { self._rig }
	local hit = Workspace:Raycast(from, Vector3.new(0, -40, 0), params)
	if not hit then
		return
	end

	self._rig:PivotTo(CrawlerRig.surfaceCFrame(
		hit.Position + hit.Normal * 0.9, hit.Normal, self._path and self._path.dir or Vector3.zAxis))
	self._state = "dropped"
	self._dropAt = os.clock()
	self._contact = 0

	ctx.Net.BroadcastCue({ kind = "crawlDrop", entity = self.Name })
	self:Log("dropped")
end

function Crawl:_flee(ctx, reason)
	if self._state == "flee" or self._state == "away" then
		return
	end
	self._state = "flee"
	self._fleeUntil = os.clock() + 1.6
	ctx.Net.BroadcastCue({ kind = "crawlFlee", entity = self.Name, reason = reason })
end

function Crawl:_onFlash(player, originCFrame, originPos, spot, ctx)
	if not self._rig or self._state == "away" or self._state == "flee" then
		return
	end
	local halfAngle = spot.Angle / 2
	local range = spot.Range * (self.Settings.RangeTolerance or 1)
	if Beam.coneHitsTarget(originCFrame, originPos, halfAngle, range,
		self._rig, { player.Character }) then
		self:_flee(ctx, "flashed")
	end
end

-- ---------------------------------------------------------------- lifecycle

function Crawl:OnPhase(phase, ctx)
	self._next = 0
end

function Crawl:Update(dt, ctx)
	local cfg = self.Settings
	local now = os.clock()

	if self._state == "away" then
		if self._next == 0 then
			local wait = self:ByIntensity(cfg.IntervalEasy, cfg.IntervalHard, ctx.Intensity)
			self._next = now + wait * (0.7 + math.random() * 0.6)
			return
		end
		if now >= self._next then
			self._next = 0
			self:_spawnRun(ctx)
		end
		return
	end

	local rig = self._rig
	if not rig or not rig.Parent or not rig.PrimaryPart then
		self:_despawn()
		return
	end

	if self._state == "skitter" then
		local path = self._path
		local speed = cfg.Speed * (0.75 + ctx.Intensity * 0.5)
		path.t += (dt * speed) / math.max(1, path.length)

		if path.t >= 1 then
			-- reached the far side: drop in, or vanish into the dark
			local canDrop = ctx.PhaseIndex >= cfg.DropFromPhase
				and math.random() < cfg.DropChance
			if canDrop then
				self:_drop(ctx)
			else
				self:_despawn()
			end
			return
		end

		local point = path.from:Lerp(path.to, path.t)
		local surfacePos, surfaceNormal = self:_reattach(point, path.normal, { rig })
		if surfacePos then
			path.normal = surfaceNormal
			rig:PivotTo(CrawlerRig.surfaceCFrame(
				surfacePos + surfaceNormal * cfg.SurfaceOffset, surfaceNormal, path.dir))
		else
			-- lost the surface (doorway, gap): keep going on the last known normal
			rig:PivotTo(CrawlerRig.surfaceCFrame(
				point + path.normal * cfg.SurfaceOffset, path.normal, path.dir))
		end
		self._gait:Update(dt, speed)
		return
	end

	if self._state == "dropped" then
		self._gait:Update(dt, 2)

		-- stay too close for too long and it lands a hit
		local nearest, dist = nil, math.huge
		for _, entry in ipairs(self:_players()) do
			local d = (entry.root.Position - rig.PrimaryPart.Position).Magnitude
			if d < dist then
				nearest, dist = entry, d
			end
		end

		if nearest and dist <= cfg.HitRadius then
			self._contact += dt
			if self._contact >= cfg.HitDelay then
				ctx.Director:AddStrike("Crawl", { player = nearest.player })
				ctx.Net.BroadcastCue({ kind = "crawlHit", entity = self.Name })
				self:_flee(ctx, "struck")
			end
		else
			self._contact = math.max(0, self._contact - dt * 0.5)
		end

		if now - self._dropAt > cfg.Lifetime then
			self:_flee(ctx, "timeout")
		end
		return
	end

	if self._state == "flee" then
		self._gait:Update(dt, cfg.FleeSpeed)
		local dir = self._path and self._path.dir or Vector3.new(0, 0, 1)
		rig:PivotTo(rig:GetPivot() + dir * cfg.FleeSpeed * dt)
		if now >= (self._fleeUntil or 0) then
			self:_despawn()
		end
		return
	end
end

function Crawl:Stop(ctx)
	self:_despawn()
	self._next = 0
end

return Crawl
