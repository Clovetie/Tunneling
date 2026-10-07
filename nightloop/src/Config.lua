--!nonstrict
--[[
	NightLoop · Config
	Every tunable in the package lives here. Nothing else should hold magic numbers.

	Porting to another game? This file plus Entities/ is usually all you touch.
]]

local Config = {}

-- ======== RUN MODE ========
-- "OneNight" : run a single night, fire NightEnded, stop.   <- reusable mode
-- "Endless"  : chain nights, scaling difficulty each time.
Config.Mode = "OneNight"

-- Start automatically when the server boots. Set false if your own code calls
-- Director:StartNight() (lobby, round system, another game, etc).
Config.AutoStart = true

-- Seconds. The design doc is 5 phases over 15 minutes.
Config.NightDuration = 900

-- Endless mode only: each subsequent night multiplies intensity by this.
Config.EndlessIntensityStep = 0.15
Config.EndlessMaxIntensity = 2.0

-- ======== PHASES ========
-- Start = seconds into the night. Intensity 0..1 drives entity aggression.
Config.Phases = {
	{ Name = "Unease",         Start = 0,   Intensity = 0.15 },
	{ Name = "Tension Builds", Start = 180, Intensity = 0.35 },
	{ Name = "House Awake",    Start = 360, Intensity = 0.55 },
	{ Name = "Overload",       Start = 540, Intensity = 0.78 },
	{ Name = "Breaking Point", Start = 720, Intensity = 1.00 },
}

-- ======== SHARED WORLD REFERENCES ========
-- Everything the package touches in YOUR place is named here, so porting means
-- editing strings rather than hunting through code.
Config.World = {
	SpotsFolder = "Spots",            -- Workspace.<name>, BaseParts
	FlashlightToolName = "Flashlight", -- Tool in the character
	FlashlightLightPart = "Light",     -- part inside the tool
	FlashlightAttachment = "LightOrigin",
	FlashlightBeamName = "Light",      -- the SpotLight inside the attachment
}

-- ======== SURVIVAL ========
-- How the night is lost. Per the design doc: the Window Monster breaching too
-- many times, or another entity landing a hit. Everything that can hurt the
-- player goes through Director:AddStrike, so the budget is shared.
Config.Survival = {
	MaxStrikes = 3,
	StrikeGrace = 2.5,  -- seconds of immunity after a strike, stops double hits
}

-- ======== ATMOSPHERE ========
-- Optional lighting control. Turn ControlLighting off and NightLoop will not
-- touch Lighting at all, which is what you want if your place already has its
-- own day/night system.
Config.Atmosphere = {
	ControlLighting = true,
	NightClockTime = 0,
	DawnClockTime = 6.6,
	DawnDuration = 8,     -- the payoff tween when the player survives
	NightBrightness = 0.35,
	DawnBrightness = 2,
	FogEasy = 320,        -- fog creeps in as intensity rises
	FogHard = 110,
}

-- ======== FLASH ========
-- The flashlight is a camera-style flash, not a held beam. Activating spends a
-- charge and fires one burst; charges refill on a timer.
Config.Flash = {
	Charges = 3,
	RechargeTime = 7,     -- seconds per charge
	BurstDuration = 0.14, -- how long the light stays on
	Cooldown = 0.5,       -- minimum seconds between flashes
	ZoomKick = 14,        -- degrees of FOV punch on the client

	-- A flash is brighter and reaches further than the resting torch. These
	-- override the SpotLight for the length of the burst and are restored
	-- after, so the hit cone always matches the light the player actually saw.
	BurstRange = 90,
	BurstBrightness = 8,
	BurstAngle = 0,       -- 0 = keep the light's own angle
}

-- ======== ENTITIES ========
-- Enabled=false entities are never required or ticked — zero cost.
-- FirstPhase is 1-based index into Config.Phases.
Config.Entities = {
	WindowMonster = {
		Enabled = true,
		FirstPhase = 1,
		-- Built-in procedural rig: no asset IDs, no imports, animated by code.
		-- Set false to go back to cloning ServerStorage.<Template> instead.
		UseBuiltInRig = true,
		TurnSpeed = 2.0,        -- how fast the body swivels to keep facing you
		RequireGround = true,   -- never spawn at a spot with no floor under it
		GroundSearch = 14,      -- how far below a spot to look for that floor
		Template = "jumpscare",        -- ServerStorage.<name>, used when UseBuiltInRig = false
		-- beam repel
		RepelTimeEasy = 0.8,           -- seconds of light at intensity 0
		RepelTimeHard = 1.6,           -- ... at intensity 1
		DecayRate = 1.5,               -- exposure lost per second off target
		CheckInterval = 0.08,
		FadeMax = 0.65,
		RetreatCooldownEasy = 20,
		RetreatCooldownHard = 7,
		TeleportMinEasy = 9,
		TeleportMaxEasy = 16,
		TeleportMinHard = 3,
		TeleportMaxHard = 7,
		-- How long the monster sits at a window before it breaks in. This is the
		-- actual fail pressure: ignore it and you take a strike.
		BreachEasy = 34,       -- seconds of patience at intensity 0
		BreachHard = 9,        -- ... at intensity 1 ("glass shatters in seconds")
		BreachWarnAt = 0.45,   -- warn the player with this fraction left
		BreachRetreat = 6,     -- it drops back for this long after breaking in

		RangeTolerance = 1.05,
	},

	Whisperer = {
		Enabled = true,
		FirstPhase = 1,
		-- PASTE YOUR AUDIO IDS HERE. Without them the entity self-disables
		-- and tells you so in the output.
		WhisperSoundIds = {},          -- e.g. { "rbxassetid://123", ... }
		FakeGlassSoundIds = {},        -- phase 4+ misdirection
		IntervalEasy = 45,             -- seconds between cues at intensity 0
		IntervalHard = 9,              -- ... at intensity 1
		MinDistance = 12,              -- studs from the player
		MaxDistance = 34,
		Volume = 0.5,
		RollOffMax = 40,
	},

	Knocker = {
		Enabled = true,
		FirstPhase = 2,
		DoorPath = "Door",          -- Workspace.<name>, a BasePart
		IntervalEasy = 50,          -- seconds between knock sets at intensity 0
		IntervalHard = 14,          -- ... at intensity 1
		KnocksMin = 2,
		KnocksMax = 5,
		RattleStuds = 0.12,         -- how far the door jolts per knock
		RattleTime = 0.07,
		ScareRadius = 14,           -- stand this close during a knock and it reacts
		SlamFromPhase = 3,          -- phase at which knocks escalate to slams
		KnockSoundIds = {},         -- optional; rattle works without audio
		SlamSoundIds = {},
		Volume = 0.8,
		RollOffMax = 70,
	},
	Crawl = {
		Enabled = true,
		FirstPhase = 3,
		IntervalEasy = 55,      -- seconds between appearances at intensity 0
		IntervalHard = 18,      -- ... at intensity 1
		Speed = 18,             -- studs/sec across the surface (~1.4s per crossing)
		CeilingSearch = 30,     -- how far up to look for something to cling to
		WallSearch = 26,        -- fallback: how far sideways to look for a wall
		WallHeight = 5,         -- how high up the wall it runs
		SurfaceOffset = 0.45,   -- keeps the body from clipping into the surface
		MinRunDistance = 26,    -- length of a crossing
		DropFromPhase = 4,      -- phase at which it starts dropping into rooms
		DropChance = 0.45,
		HitRadius = 7,          -- stay this close after a drop...
		HitDelay = 1.6,         -- ...for this long, and it lands a strike
		Lifetime = 14,          -- max seconds on the floor before it leaves
		FleeSpeed = 48,
		RangeTolerance = 1.1,
	},
	Breathless  = { Enabled = false, FirstPhase = 4 },
	TickingMan  = { Enabled = false, FirstPhase = 5 },
}

-- ======== HUD ========
Config.Hud = {
	Enabled = true,
	ShowClock = true,
	ShowPhase = true,
	-- in-fiction clock: night runs from 12am to 6am across NightDuration
	ClockStartHour = 0,
	ClockEndHour = 6,
}

Config.Debug = false

return Config
