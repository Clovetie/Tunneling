--!nonstrict
local HorrorConfig = {
	NightDuration = 360,
	MaxStrikes = 3,
	Phases = {
		{ Name = "Unease", Start = 0, Intensity = 0.18 },
		{ Name = "Whispers", Start = 75, Intensity = 0.38 },
		{ Name = "Awakening", Start = 150, Intensity = 0.60 },
		{ Name = "Siege", Start = 225, Intensity = 0.82 },
		{ Name = "Eclipse", Start = 300, Intensity = 1.00 },
	},
	Modifiers = {
		BloodMoon = { IntensityBonus = 0.25, Title = "Blood Moon" },
		DenseFog = { IntensityBonus = 0.15, Title = "Dense Fog" },
		Ironman = { IntensityBonus = 0.20, MaxStrikes = 1, Title = "Ironman" },
	},
}

return HorrorConfig
