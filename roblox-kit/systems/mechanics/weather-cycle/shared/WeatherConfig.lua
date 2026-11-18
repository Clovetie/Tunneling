--!nonstrict
local WeatherConfig = {
	CycleDuration = 90,
	States = {
		Clear = { Brightness = 2.0, FogEnd = 650, ClockTime = 14.0, Ambient = Color3.fromRGB(95, 105, 120) },
		Rain = { Brightness = 1.1, FogEnd = 260, ClockTime = 16.5, Ambient = Color3.fromRGB(60, 72, 90) },
		Thunderstorm = { Brightness = 0.65, FogEnd = 170, ClockTime = 19.0, Ambient = Color3.fromRGB(42, 50, 68) },
		BloodMoon = { Brightness = 0.45, FogEnd = 140, ClockTime = 0.0, Ambient = Color3.fromRGB(95, 24, 28) },
		DenseFog = { Brightness = 0.80, FogEnd = 95, ClockTime = 6.2, Ambient = Color3.fromRGB(78, 86, 92) },
		Blizzard = { Brightness = 1.35, FogEnd = 110, ClockTime = 11.0, Ambient = Color3.fromRGB(130, 155, 180) },
	},
	Order = { "Clear", "Rain", "Thunderstorm", "DenseFog", "BloodMoon", "Blizzard" },
}

return WeatherConfig
