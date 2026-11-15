--!nonstrict
local AbilityDefs = {
	SolarFlare = {
		id = "SolarFlare",
		name = "Solar Flare",
		cooldown = 3.0,
		range = 55,
		baseDamage = 28,
		status = "Burn",
	},
	ArcPulse = {
		id = "ArcPulse",
		name = "Arc Pulse",
		cooldown = 5.0,
		range = 40,
		baseDamage = 36,
		status = "Shock",
	},
	AegisDash = {
		id = "AegisDash",
		name = "Aegis Dash",
		cooldown = 4.0,
		range = 24,
		baseDamage = 18,
		status = "Expose",
	},
}

return AbilityDefs
