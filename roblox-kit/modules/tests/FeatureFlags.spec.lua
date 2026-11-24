-- FeatureFlags.spec.lua — TestEZ spec for the flag layer.
--
-- Deliberately limited to what does not depend on a running game: `resolve` is pure, and
-- `configure`/`get` are checked through flags whose names no real game would use, so the
-- spec neither creates `ReplicatedStorage.FeatureFlags` nor mutates a place. Setting a
-- flag (and the attribute-replication path that carries it to clients) needs a live
-- server, so it is verified by `run_testez_suite` against a real Studio peer rather than
-- asserted here.

return function()
	local Flags = require(script.Parent.Parent.FeatureFlags)

	local A = "__SpecFlag_Alpha"
	local B = "__SpecFlag_Beta"
	local C = "__SpecFlag_Gamma"

	describe("Flags.resolve (pure)", function()
		it("returns the defaults when nothing has been set", function()
			local out = Flags.resolve({ [A] = true, [B] = false }, {})
			expect(out[A]).to.equal(true)
			expect(out[B]).to.equal(false)
		end)

		it("lets a set attribute win over the default", function()
			local out = Flags.resolve({ [A] = true }, { [A] = false })
			expect(out[A]).to.equal(false)
			local back = Flags.resolve({ [A] = true }, { [A] = true })
			expect(back[A]).to.equal(true)
		end)

		it("surfaces an attribute that has no default at all", function()
			local out = Flags.resolve({}, { [C] = true })
			expect(out[C]).to.equal(true)
		end)

		it("ignores a non-boolean attribute instead of coercing it", function()
			-- The failure this prevents: a string "false" that reads as truthy in Lua and
			-- silently switches a feature on.
			local out = Flags.resolve({ [A] = false }, { [A] = "false", [B] = 1, [C] = nil })
			expect(out[A]).to.equal(false)
			expect(out[B]).to.equal(nil)
			expect(out[C]).to.equal(nil)
		end)

		it("ignores junk in the default table too", function()
			local out = Flags.resolve({ [A] = "yes", [B] = true, [""] = true }, {})
			expect(out[A]).to.equal(nil)
			expect(out[B]).to.equal(true)
			expect(out[""]).to.equal(nil)
		end)

		it("tolerates being handed nothing", function()
			expect(next(Flags.resolve(nil, nil))).to.equal(nil)
			expect(next(Flags.resolve())).to.equal(nil)
		end)
	end)

	describe("Flags.configure / Flags.get", function()
		it("reports how many defaults it applied and skips the junk", function()
			local applied = Flags.configure({ [A] = true, [B] = "not a boolean", [""] = true })
			expect(applied).to.equal(1)
			expect(Flags.get(A)).to.equal(true)
		end)

		it("is additive across calls, later value winning per key", function()
			Flags.configure({ [A] = true, [C] = false })
			Flags.configure({ [A] = false })
			expect(Flags.get(A)).to.equal(false)
			expect(Flags.get(C)).to.equal(false)
		end)

		it("returns false for a flag nobody ever mentioned", function()
			expect(Flags.get("__SpecFlag_NeverConfigured")).to.equal(false)
		end)

		it("refuses a non-string or empty name rather than indexing with it", function()
			expect(Flags.get("")).to.equal(false)
			expect(Flags.get(nil :: any)).to.equal(false)
			expect(Flags.get(42 :: any)).to.equal(false)
		end)

		it("reflects configured defaults in all()", function()
			Flags.configure({ [B] = true })
			local effective = Flags.all()
			expect(effective[B]).to.equal(true)
		end)
	end)

	describe("Flags.onChanged", function()
		it("rejects a missing name or callback", function()
			expect(function()
				Flags.onChanged("", function() end)
			end).to.throw()
			expect(function()
				Flags.onChanged(A, nil :: any)
			end).to.throw()
		end)

		it("returns a connection whose Disconnect is idempotent", function()
			local conn = Flags.onChanged(A, function() end)
			expect(type(conn.Disconnect)).to.equal("function")
			conn:Disconnect()
			conn:Disconnect()
		end)
	end)

	describe("Flags.rootName", function()
		it("is the documented ReplicatedStorage folder", function()
			expect(Flags.rootName()).to.equal("FeatureFlags")
		end)
	end)
end
