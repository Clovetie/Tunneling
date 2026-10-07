--!nonstrict
--[[
	NightLoop · Beam
	Shared cone-raycast maths.

	The important bit is castThrough(): a plain Workspace:Raycast stops on the
	first hit, and this game's windows are parts at Transparency 0.6 with
	CanCollide true. The beam hit the glass and died, so you had to physically
	push the flashlight through a window to register. castThrough() keeps going
	through anything see-through and only stops on something solid.
]]

local Workspace = game:GetService("Workspace")

local Beam = {}

Beam.RING_RADII = { 0.55, 0.95 }
Beam.RING_SAMPLES = 4
Beam.MAX_PENETRATIONS = 6
Beam.GLASS_TRANSPARENCY = 0.35 -- at or above this a part counts as see-through

function Beam.coneDirections(cf, halfAngleDeg)
	local dir, right, up = cf.LookVector, cf.RightVector, cf.UpVector
	local spread = math.tan(math.rad(halfAngleDeg))
	local dirs = { dir }
	for ringIndex, radius in ipairs(Beam.RING_RADII) do
		local phase = (ringIndex - 1) * (math.pi / Beam.RING_SAMPLES)
		for i = 0, Beam.RING_SAMPLES - 1 do
			local t = phase + (i / Beam.RING_SAMPLES) * math.pi * 2
			table.insert(dirs, (dir
				+ right * math.cos(t) * spread * radius
				+ up * math.sin(t) * spread * radius).Unit)
		end
	end
	return dirs
end

local function seeThrough(part)
	return part.Transparency >= Beam.GLASS_TRANSPARENCY
		or not part.CanCollide
end

-- Cast from origin, passing through glass, returning the first SOLID hit.
-- Returns the RaycastResult plus every see-through part crossed.
function Beam.castThrough(origin, direction, range, ignoreList)
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	params.IgnoreWater = true

	local ignore = table.clone(ignoreList or {})
	params.FilterDescendantsInstances = ignore

	local travelled = 0
	local crossed = {}
	local from = origin

	for _ = 1, Beam.MAX_PENETRATIONS do
		local remaining = range - travelled
		if remaining <= 0 then
			return nil, crossed
		end

		local result = Workspace:Raycast(from, direction * remaining, params)
		if not result then
			return nil, crossed
		end

		if not seeThrough(result.Instance) then
			return result, crossed
		end

		-- punch through the glass and keep going
		table.insert(crossed, result.Instance)
		table.insert(ignore, result.Instance)
		params.FilterDescendantsInstances = ignore
		travelled += (result.Position - from).Magnitude + 0.05
		from = result.Position + direction * 0.05
	end

	return nil, crossed
end

-- Does a cone from this origin reach the target model?
--[[
	Did the cone reach the target?

	Checks BOTH the part the ray stopped on and every part it passed through.
	The pass-through list matters: castThrough deliberately penetrates
	non-collidable parts, and a Motor6D rig is built entirely from
	non-collidable parts — so a monster would be silently punched through and
	never register as lit. Counting `crossed` means "the beam reached it" is
	what decides a hit, which is what the player actually sees.
]]
function Beam.coneHitsTarget(originCFrame, originPos, halfAngleDeg, range, target, ignoreList)
	if not target or not target.Parent then
		return false
	end
	for _, dir in ipairs(Beam.coneDirections(originCFrame, halfAngleDeg)) do
		local hit, crossed = Beam.castThrough(originPos, dir, range, ignoreList)
		if hit and hit.Instance and hit.Instance:IsDescendantOf(target) then
			return true
		end
		for _, part in ipairs(crossed or {}) do
			if part:IsDescendantOf(target) then
				return true
			end
		end
	end
	return false
end

return Beam
