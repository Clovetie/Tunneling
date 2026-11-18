--!strict
local ShrineDefs = {
	ArcaneHaste = {
		id = "ArcaneHaste",
		label = "HASTE +25%",
		durationSec = 45,
		cooldownSec = 30,
		speedMultiplier = 1.25,
		vfxColor = Color3.fromRGB(56, 189, 248),
	},
	GoldenAegis = {
		id = "GoldenAegis",
		label = "SHIELD +40",
		durationSec = 60,
		cooldownSec = 45,
		bonusHealth = 40,
		vfxColor = Color3.fromRGB(251, 191, 36),
	},
}

return ShrineDefs
