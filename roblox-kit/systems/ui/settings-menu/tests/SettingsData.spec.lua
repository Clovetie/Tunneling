--!nonstrict
-- SettingsData.spec.lua — the pure half of ui/settings-menu: clamping, snapping,
-- validation, keybind conflicts and the "what differs from default" question that drives
-- the Restore button. No window, no SoundService, no saving.

return function()
	local function findShared(name)
		-- Works in the kit layout (`tests/` beside `shared/`) and in an installed game
		-- (`tests/` with `src/shared/` mapped into ReplicatedStorage).
		local shared = script.Parent.Parent:FindFirstChild("shared")
		local found = shared and shared:FindFirstChild(name)
		if found then
			return require(found)
		end
		return require(game:GetService("ReplicatedStorage"):WaitForChild(name, 10))
	end

	local SettingsData = findShared("SettingsData")
	local SettingsConfig = findShared("SettingsConfig")

	-- Adds a key to the schema for one case and removes it again even if the case fails, so
	-- a failure cannot leak a synthetic setting into the cases after it.
	local function withSchemaKey(key, rule, fn)
		local original = SettingsData.SCHEMA[key]
		SettingsData.SCHEMA[key] = rule
		local ok, err = pcall(fn)
		SettingsData.SCHEMA[key] = original
		if not ok then
			error(err, 0)
		end
	end

	describe("defaults", function()
		it("has a value for every schema key and a copy of every keybind", function()
			local defaults = SettingsData.defaults()
			for key, rule in pairs(SettingsData.SCHEMA) do
				expect(defaults[key]).to.equal(rule.default)
			end
			for action, name in pairs(SettingsConfig.KEYBINDS) do
				expect(defaults.Keybinds[action]).to.equal(name)
			end
		end)

		it("reports itself as unchanged from default", function()
			expect(SettingsData.isDefault(SettingsData.defaults())).to.equal(true)
			expect(#SettingsData.diffFromDefaults(SettingsData.defaults())).to.equal(0)
		end)

		it("hands out a fresh table each time, so one caller cannot dirty another's", function()
			local a = SettingsData.defaults()
			a.MasterVolume = 0
			expect(SettingsData.defaults().MasterVolume).to.equal(1)
		end)
	end)

	describe("snap", function()
		it("clamps outside the range", function()
			expect(SettingsData.snap("MasterVolume", 1.4)).to.equal(1)
			expect(SettingsData.snap("MasterVolume", -0.2)).to.equal(0)
			expect(SettingsData.snap("CameraSensitivity", 0.05)).to.equal(0.2)
			expect(SettingsData.snap("CameraSensitivity", 99)).to.equal(3)
		end)

		it("snaps to the step", function()
			expect(SettingsData.snap("MasterVolume", 0.17)).to.equal(0.15)
			expect(SettingsData.snap("MasterVolume", 0.18)).to.equal(0.2)
			expect(SettingsData.snap("CameraSensitivity", 1.06)).to.equal(1.1)
		end)

		it("leaves exact values alone, with no float dust", function()
			expect(SettingsData.snap("MasterVolume", 0.8)).to.equal(0.8)
			expect(SettingsData.snap("MasterVolume", 0.15)).to.equal(0.15)
			expect(SettingsData.snap("CameraSensitivity", 2.99)).to.equal(3)
		end)

		it("keeps max reachable when the step does not divide the range", function()
			-- A 0..1 slider with step 0.3 must still be able to reach 1.0, or a game that
			-- picks an awkward step ships a volume control that cannot be maxed.
			withSchemaKey("__SpecOdd", { kind = "number", min = 0, max = 1, step = 0.3, default = 0 }, function()
				expect(SettingsData.snap("__SpecOdd", 1)).to.equal(1)
				expect(SettingsData.snap("__SpecOdd", 0.9)).to.equal(0.9)
				expect(SettingsData.snap("__SpecOdd", 0.5)).to.equal(0.6)
				expect(SettingsData.snap("__SpecOdd", 0.4)).to.equal(0.3)
				expect(SettingsData.snap("__SpecOdd", 0.1)).to.equal(0)
			end)
			expect(SettingsData.SCHEMA.__SpecOdd).never.to.be.ok()
		end)

		it("refuses NaN, infinities and non-numbers", function()
			expect(SettingsData.snap("MasterVolume", 0 / 0)).never.to.be.ok()
			expect(SettingsData.snap("MasterVolume", math.huge)).never.to.be.ok()
			expect(SettingsData.snap("MasterVolume", "1")).never.to.be.ok()
			expect(SettingsData.snap("MasterVolume", true)).never.to.be.ok()
		end)

		it("is nil for a key that is not a number, or not a setting at all", function()
			expect(SettingsData.snap("InvertY", 1)).never.to.be.ok()
			expect(SettingsData.snap("__SpecNope", 1)).never.to.be.ok()
		end)
	end)

	describe("normalizeValue", function()
		it("rejects an unknown setting", function()
			local value, reason = SettingsData.normalizeValue("__SpecNope", 1)
			expect(value).never.to.be.ok()
			expect(reason).to.equal("unknown_setting")
		end)

		it("refuses to coerce a non-boolean into a toggle", function()
			-- "true" and 1 are both truthy in Lua; accepting them would let a client turn a
			-- setting on by sending a string.
			expect((SettingsData.normalizeValue("InvertY", "true"))).never.to.be.ok()
			expect((SettingsData.normalizeValue("InvertY", 1))).never.to.be.ok()
			expect(SettingsData.normalizeValue("InvertY", false)).to.equal(false)
			expect(SettingsData.normalizeValue("InvertY", true)).to.equal(true)
		end)

		it("checks an enum against its option list", function()
			expect(SettingsData.normalizeValue("ColorBlindPalette", "Protanopia")).to.equal("Protanopia")
			local _, reason = SettingsData.normalizeValue("ColorBlindPalette", "Rainbow")
			expect(reason).to.equal("not_an_option")
			local _, typeReason = SettingsData.normalizeValue("ColorBlindPalette", 2)
			expect(typeReason).to.equal("not_a_string")
		end)

		it("clamps and snaps a number rather than storing what it was given", function()
			expect(SettingsData.normalizeValue("MasterVolume", 5)).to.equal(1)
			expect(SettingsData.normalizeValue("MasterVolume", 0.17)).to.equal(0.15)
		end)
	end)

	describe("normalize", function()
		it("returns the defaults for anything that is not a table", function()
			expect(SettingsData.isDefault(SettingsData.normalize(nil))).to.equal(true)
			expect(SettingsData.isDefault(SettingsData.normalize("nope"))).to.equal(true)
			expect(SettingsData.isDefault(SettingsData.normalize(42))).to.equal(true)
		end)

		it("fills missing keys and drops unknown ones", function()
			local out = SettingsData.normalize({ MasterVolume = 0.5, __SpecNope = "x" })
			expect(out.MasterVolume).to.equal(0.5)
			expect(out.__SpecNope).never.to.be.ok()
			expect(out.MusicVolume).to.equal(SettingsData.SCHEMA.MusicVolume.default)
		end)

		it("repairs a corrupt save instead of propagating it", function()
			local out = SettingsData.normalize({
				MasterVolume = "loud",
				InvertY = "yes",
				ColorBlindPalette = "Rainbow",
				CameraSensitivity = 9000,
			})
			expect(out.MasterVolume).to.equal(1)
			expect(out.InvertY).to.equal(false)
			expect(out.ColorBlindPalette).to.equal("None")
			expect(out.CameraSensitivity).to.equal(3)
		end)

		it("only accepts keybinds for known actions with plausible key names", function()
			local out = SettingsData.normalize({
				Keybinds = {
					Sprint = "LeftControl",
					NotAnAction = "Q",
					Interact = "a very long key name that is not a keycode",
					Jump = 42,
				},
			})
			expect(out.Keybinds.Sprint).to.equal("LeftControl")
			expect(out.Keybinds.NotAnAction).never.to.be.ok()
			expect(out.Keybinds.Interact).to.equal(SettingsConfig.KEYBINDS.Interact)
			expect(out.Keybinds.Jump).to.equal(SettingsConfig.KEYBINDS.Jump)
		end)
	end)

	describe("isKeyName", function()
		it("accepts KeyCode-shaped names", function()
			expect(SettingsData.isKeyName("LeftShift")).to.equal(true)
			expect(SettingsData.isKeyName("E")).to.equal(true)
			expect(SettingsData.isKeyName("F1")).to.equal(true)
			expect(SettingsData.isKeyName("KeypadEnter")).to.equal(true)
		end)

		it("refuses everything else", function()
			expect(SettingsData.isKeyName("")).to.equal(false)
			expect(SettingsData.isKeyName("left shift")).to.equal(false)
			expect(SettingsData.isKeyName("1")).to.equal(false)
			expect(SettingsData.isKeyName(string.rep("A", 33))).to.equal(false)
			expect(SettingsData.isKeyName(42)).to.equal(false)
			expect(SettingsData.isKeyName(nil)).to.equal(false)
		end)
	end)

	describe("set", function()
		it("returns a new table and never mutates the caller's", function()
			local before = SettingsData.defaults()
			local after, ok = SettingsData.set(before, "MasterVolume", 0.4)
			expect(ok).to.equal(true)
			expect(after.MasterVolume).to.equal(0.4)
			expect(before.MasterVolume).to.equal(1)
		end)

		it("refuses an unknown key or a bad value, and still returns usable settings", function()
			local before = SettingsData.defaults()
			local after, ok, reason = SettingsData.set(before, "__SpecNope", 1)
			expect(ok).to.equal(false)
			expect(reason).to.equal("unknown_setting")
			expect(SettingsData.isDefault(after)).to.equal(true)
			local _, ok2 = SettingsData.set(before, "InvertY", "yes")
			expect(ok2).to.equal(false)
		end)
	end)

	describe("step", function()
		it("moves one step in each direction", function()
			expect(SettingsData.step("MasterVolume", 0.5, 1)).to.equal(0.55)
			expect(SettingsData.step("MasterVolume", 0.5, -1)).to.equal(0.45)
			expect(SettingsData.step("CameraSensitivity", 1, 1)).to.equal(1.1)
		end)

		it("stops at the ends of the range", function()
			expect(SettingsData.step("MasterVolume", 1, 1)).to.equal(1)
			expect(SettingsData.step("MasterVolume", 0, -1)).to.equal(0)
		end)

		it("snaps a stray value onto the grid before stepping", function()
			expect(SettingsData.step("MasterVolume", 0.17, 1)).to.equal(0.2)
		end)

		it("falls back to the default when handed nothing usable", function()
			expect(SettingsData.step("MasterVolume", nil, 1)).to.equal(1)
			expect(SettingsData.step("InvertY", true, 1)).never.to.be.ok()
		end)
	end)

	describe("diffFromDefaults", function()
		it("lists changed settings, sorted, and nothing else", function()
			local changed = SettingsData.diffFromDefaults({ MasterVolume = 0.5, ReduceMotion = true })
			expect(#changed).to.equal(2)
			expect(changed[1]).to.equal("MasterVolume")
			expect(changed[2]).to.equal("ReduceMotion")
		end)

		it("includes a remapped key, named by action", function()
			local changed = SettingsData.diffFromDefaults({ Keybinds = { Sprint = "LeftControl" } })
			expect(changed[1]).to.equal("Keybinds.Sprint")
		end)

		it("is empty once restored", function()
			expect(SettingsData.isDefault(SettingsData.normalize({ MasterVolume = 0.5 }))).to.equal(false)
			expect(SettingsData.isDefault(SettingsData.defaults())).to.equal(true)
		end)
	end)

	describe("bindKey", function()
		it("rebinds an action", function()
			local after, ok = SettingsData.bindKey(SettingsData.defaults(), "Sprint", "LeftControl")
			expect(ok).to.equal(true)
			expect(after.Keybinds.Sprint).to.equal("LeftControl")
		end)

		it("refuses a key another action already holds, and names the other action", function()
			local defaults = SettingsData.defaults()
			local after, ok, reason = SettingsData.bindKey(defaults, "Sprint", defaults.Keybinds.Interact)
			expect(ok).to.equal(false)
			expect(reason).to.equal("conflict:Interact")
			expect(after.Keybinds.Sprint).to.equal(defaults.Keybinds.Sprint)
		end)

		it("clears a binding with nil, which can never conflict", function()
			local after, ok = SettingsData.bindKey(SettingsData.defaults(), "Sprint", nil)
			expect(ok).to.equal(true)
			expect(after.Keybinds.Sprint).never.to.be.ok()
		end)

		it("refuses an unknown action and an implausible key name", function()
			expect((SettingsData.bindKey(SettingsData.defaults(), "__SpecNope", "Q"))).to.equal(false)
			expect((SettingsData.bindKey(SettingsData.defaults(), "Sprint", "not a keycode"))).to.equal(false)
		end)

		it("never mutates the caller's settings", function()
			local before = SettingsData.defaults()
			SettingsData.bindKey(before, "Sprint", "LeftControl")
			expect(before.Keybinds.Sprint).to.equal(SettingsConfig.KEYBINDS.Sprint)
		end)
	end)
end
