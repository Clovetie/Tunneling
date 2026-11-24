--!nonstrict
-- Every tunable for ui/settings-menu. No Roblox types in here on purpose: SettingsData
-- requires this file, and keeping it plain means the validation logic (and its spec) needs
-- no DataModel. Key codes are names ("LeftShift"), not Enum.KeyCode, for the same reason.

local SettingsConfig = {
	SAVE_KEY = "settings",
	REMOTE_SAVE = "SaveSettings",
	REMOTE_LOAD = "SettingsLoaded",
	WINDOW_KEY = "Settings",
	HUD_ORDER = 90,

	-- The whole settings surface in one table: what the menu renders, what the server
	-- accepts and what a save may contain are all derived from this, so they cannot drift.
	SCHEMA = {
		MasterVolume = { kind = "number", min = 0, max = 1, step = 0.05, default = 1, label = "Master volume" },
		MusicVolume = { kind = "number", min = 0, max = 1, step = 0.05, default = 0.8, label = "Music volume" },
		SfxVolume = { kind = "number", min = 0, max = 1, step = 0.05, default = 1, label = "Sound effects" },
		CameraSensitivity = { kind = "number", min = 0.2, max = 3, step = 0.1, default = 1, label = "Camera sensitivity" },
		InvertY = { kind = "boolean", default = false, label = "Invert camera Y" },
		ReduceMotion = { kind = "boolean", default = false, label = "Reduce motion" },
		ShowHints = { kind = "boolean", default = true, label = "Show on-screen hints" },
		ColorBlindPalette = {
			kind = "enum",
			options = { "None", "Protanopia", "Deuteranopia", "Tritanopia" },
			default = "None",
			label = "Colour-blind palette",
		},
	},

	-- Render order of the menu. A game reorders or drops rows without touching the client.
	ORDER = {
		"MasterVolume", "MusicVolume", "SfxVolume", "CameraSensitivity",
		"InvertY", "ReduceMotion", "ShowHints", "ColorBlindPalette",
	},

	-- Action -> key name. Remapping is client-side by nature (the game's own input code
	-- reads it), but it is saved and validated like everything else.
	KEYBINDS = {
		Sprint = "LeftShift",
		Interact = "E",
		Jump = "Space",
		-- Not Escape: that opens Roblox's own core menu, which this window cannot draw
		-- over, so the player would press it and see nothing happen.
		Menu = "F1",
	},
	KEYBIND_ORDER = { "Sprint", "Interact", "Jump", "Menu" },

	-- SoundGroup names to apply the volume sliders to, when the game has them. This is the
	-- Roblox-idiomatic way to do master volume: parent the game's sounds to a "Master"
	-- SoundGroup and the slider drives it. A game with no SoundGroups gets a one-time
	-- Studio warning and the settings still save and apply to everything else.
	SOUND_GROUPS = { Master = "Master", Music = "Music", Sfx = "SFX" },

	-- Colour-blind palette, applied through one ColorCorrectionEffect in Lighting. These
	-- are a coarse saturation/tint aid, NOT a simulation of the deficiency: Roblox has no
	-- stock LUT effect, and shipping a fake simulation would be worse than saying so. A
	-- game with real LUTs should replace these values or drive its own effect from
	-- SettingsAPI.onChanged("ColorBlindPalette", fn). Plain numbers, so this file stays
	-- free of Roblox types.
	PALETTE_ADJUSTMENTS = {
		Protanopia = { saturation = -0.15, tint = { 0.93, 1.0, 1.0 } },
		Deuteranopia = { saturation = -0.15, tint = { 1.0, 0.94, 1.0 } },
		Tritanopia = { saturation = -0.15, tint = { 1.0, 1.0, 0.93 } },
	},
	PALETTE_EFFECT_NAME = "SettingsPalette",

	-- Menu geometry and wording, as plain numbers.
	UI_TITLE = "Settings",
	UI_WIDTH = 420,
	UI_ROW_HEIGHT = 34,
	UI_ANCHOR_X = 0.5,
	UI_ANCHOR_Y = 0.5,
	UI_RESTORE_LABEL = "Restore defaults",
	UI_CLOSE_LABEL = "Close",
	UI_KEYBIND_HINT = "Press a key, or Backspace to clear",
}

return SettingsConfig
