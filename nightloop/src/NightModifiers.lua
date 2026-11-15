--!nonstrict
--[[
	NightLoop · NightModifiers
	Composable run mutators for replayable variety (inspired by DOORS modifiers
	and co-op survival moon conditions).

	Can be passed via `Director:StartNight({ modifiers = { "BloodMoon", "Overcharged" } })`
	or configured in `Config.Modifiers`.
]]

local NightModifiers = {}

NightModifiers.Catalog = {
	BloodMoon = {
		Title = "Blood Moon",
		Description = "Entity aggression +25%, crimson fog tint, +1 extra Flash charge.",
		IntensityBonus = 0.25,
		ExtraFlashCharges = 1,
		FogScale = 0.78,
	},
	DenseFog = {
		Title = "Dense Fog",
		Description = "Visibility cut in half; audio and proximity cues intensify.",
		IntensityBonus = 0.12,
		FogScale = 0.50,
	},
	Overcharged = {
		Title = "Overcharged Capacitor",
		Description = "Flashlight recharges 40% faster, but entities recover 20% quicker.",
		IntensityBonus = 0.15,
		RechargeMultiplier = 0.60,
	},
	GlassHouse = {
		Title = "Glass House",
		Description = "WindowMonster, Knocker, and Mimic are hyper-active from Phase 1.",
		IntensityBonus = 0.18,
		ForceEarlyEntities = { "WindowMonster", "Knocker", "Mimic" },
	},
	ChronosNight = {
		Title = "Chronos Anomaly",
		Description = "TickingMan and Breathless awaken early in the night.",
		IntensityBonus = 0.20,
		ForceEarlyEntities = { "Breathless", "TickingMan" },
	},
	Ironman = {
		Title = "Ironman (One Strike)",
		Description = "A single mistake ends the night (MaxStrikes = 1).",
		IntensityBonus = 0.22,
		MaxStrikesOverride = 1,
	},
}

function NightModifiers.Resolve(activeNames, baseConfig)
	local resolved = {
		names = {},
		titles = {},
		intensityBonus = 0,
		maxStrikes = baseConfig and baseConfig.Survival and baseConfig.Survival.MaxStrikes or 3,
		extraFlashCharges = 0,
		rechargeMultiplier = 1.0,
		fogScale = 1.0,
		forceEarly = {},
	}
	if type(activeNames) ~= "table" then
		return resolved
	end
	for _, name in ipairs(activeNames) do
		local spec = NightModifiers.Catalog[name]
		if spec then
			table.insert(resolved.names, name)
			table.insert(resolved.titles, spec.Title)
			resolved.intensityBonus += (spec.IntensityBonus or 0)
			resolved.extraFlashCharges += (spec.ExtraFlashCharges or 0)
			if spec.RechargeMultiplier then
				resolved.rechargeMultiplier *= spec.RechargeMultiplier
			end
			if spec.FogScale then
				resolved.fogScale *= spec.FogScale
			end
			if spec.MaxStrikesOverride then
				resolved.maxStrikes = math.min(resolved.maxStrikes, spec.MaxStrikesOverride)
			end
			if type(spec.ForceEarlyEntities) == "table" then
				for _, entName in ipairs(spec.ForceEarlyEntities) do
					resolved.forceEarly[entName] = true
				end
			end
		end
	end
	return resolved
end

return NightModifiers
