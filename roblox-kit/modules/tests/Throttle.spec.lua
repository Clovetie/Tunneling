-- Throttle.spec.lua — TestEZ spec for the kit's two rate-limiting primitives.
-- Every case drives a fake clock, so the spec is deterministic and never sleeps.

return function()
	local Throttle = require(script.Parent.Parent.Throttle)

	local function fakeClock(startAt)
		local state = { t = startAt or 0 }
		return state, function()
			return state.t
		end
	end

	describe("Throttle.new (token bucket)", function()
		it("allows the whole burst and then refuses", function()
			local clock, now = fakeClock(0)
			local limiter = Throttle.new(2, 4, { now = now })
			for _ = 1, 4 do
				local ok = limiter:attempt()
				expect(ok).to.equal(true)
			end
			local ok, retryAfter = limiter:attempt()
			expect(ok).to.equal(false)
			-- One token at 2/s takes half a second to arrive.
			expect(retryAfter).to.be.near(0.5, 1e-9)
			clock.t += 0.25
			local stillNo = limiter:attempt()
			expect(stillNo).to.equal(false)
			clock.t += 0.25
			local okAgain = limiter:attempt()
			expect(okAgain).to.equal(true)
		end)

		it("never refills past the burst, however long it idles", function()
			local clock, now = fakeClock(0)
			local limiter = Throttle.new(10, 3, { now = now })
			clock.t += 600
			expect(limiter:peek()).to.be.near(3, 1e-9)
			for _ = 1, 3 do
				expect(limiter:attempt()).to.equal(true)
			end
			expect(limiter:attempt()).to.equal(false)
		end)

		it("reports retryAfter that shrinks as time passes", function()
			local clock, now = fakeClock(0)
			local limiter = Throttle.new(4, 1, { now = now })
			expect(limiter:attempt()).to.equal(true)
			local _, first = limiter:attempt()
			expect(first).to.be.near(0.25, 1e-9)
			clock.t += 0.1
			local _, second = limiter:attempt()
			expect(second).to.be.near(0.15, 1e-9)
		end)

		it("peek consumes nothing and reset refills", function()
			local clock, now = fakeClock(0)
			local limiter = Throttle.new(1, 5, { now = now })
			expect(limiter:peek()).to.be.near(5, 1e-9)
			expect(limiter:attempt()).to.equal(true)
			expect(limiter:peek()).to.be.near(4, 1e-9)
			limiter:reset()
			expect(limiter:peek()).to.be.near(5, 1e-9)
		end)

		it("defaults a nonsense rate or burst instead of jamming open or shut", function()
			local _, now = fakeClock(0)
			-- rate 0 would mean "never refill"; NaN would poison every comparison.
			local zero = Throttle.new(0, 3, { now = now })
			for _ = 1, 3 do
				expect(zero:attempt()).to.equal(true)
			end
			expect(zero:attempt()).to.equal(false)
			local nan = Throttle.new(0 / 0, 2, { now = now })
			expect(nan:attempt()).to.equal(true)
			expect(nan:attempt()).to.equal(true)
			expect(nan:attempt()).to.equal(false)
			-- No burst given: max(3, rate * 2), matching the pre-v6.1.0 NetworkGuard default.
			local implicit = Throttle.new(10, nil, { now = now })
			expect(implicit:peek()).to.be.near(20, 1e-9)
		end)

		it("tolerates a clock that goes backwards", function()
			local clock, now = fakeClock(100)
			local limiter = Throttle.new(2, 2, { now = now })
			expect(limiter:attempt()).to.equal(true)
			expect(limiter:attempt()).to.equal(true)
			clock.t = 50
			-- Elapsed clamps at zero, so no free tokens appear out of a rewound clock.
			expect(limiter:attempt()).to.equal(false)
		end)
	end)

	describe("Throttle.fixedWindow", function()
		it("allows exactly the limit inside a window and resets on the boundary", function()
			local clock, now = fakeClock(0)
			local limiter = Throttle.fixedWindow(3, 1, { now = now })
			clock.t = 0.0
			expect(limiter:attempt()).to.equal(true)
			clock.t = 0.1
			expect(limiter:attempt()).to.equal(true)
			clock.t = 0.2
			expect(limiter:attempt()).to.equal(true)
			clock.t = 0.3
			local ok, retryAfter = limiter:attempt()
			expect(ok).to.equal(false)
			expect(retryAfter).to.be.near(0.7, 1e-9)
			-- The window rolls at start + window, not "one second after the last call".
			clock.t = 0.99
			expect(limiter:attempt()).to.equal(false)
			clock.t = 1.0
			expect(limiter:attempt()).to.equal(true)
			expect(limiter:peek()).to.be.near(2, 1e-9)
		end)

		it("does not refill gradually the way a bucket does", function()
			local clock, now = fakeClock(0)
			local limiter = Throttle.fixedWindow(2, 10, { now = now })
			expect(limiter:attempt()).to.equal(true)
			expect(limiter:attempt()).to.equal(true)
			-- Half the window has passed and a bucket would have refilled; a window has not.
			clock.t = 5
			expect(limiter:attempt()).to.equal(false)
			expect(limiter:peek()).to.equal(0)
			clock.t = 10
			expect(limiter:peek()).to.equal(2)
		end)

		it("floors a fractional limit", function()
			local _, now = fakeClock(0)
			local fractional = Throttle.fixedWindow(2.9, 1, { now = now })
			expect(fractional:attempt()).to.equal(true)
			expect(fractional:attempt()).to.equal(true)
			expect(fractional:attempt()).to.equal(false)
		end)

		it("falls back to the documented limit of 8 rather than jamming shut on 0", function()
			local _, now = fakeClock(0)
			-- Deliberate: `fixedWindow(0, 1)` is a typo far more often than it is a request
			-- for "refuse everything", and an emergency stop is FeatureFlags' job.
			local zero = Throttle.fixedWindow(0, 1, { now = now })
			expect(zero:peek()).to.equal(8)
			for _ = 1, 8 do
				expect(zero:attempt()).to.equal(true)
			end
			expect(zero:attempt()).to.equal(false)
		end)
	end)

	describe("the two primitives are interchangeable", function()
		it("expose the same four methods", function()
			local _, now = fakeClock(0)
			for _, limiter in ipairs({ Throttle.new(5, 5, { now = now }), Throttle.fixedWindow(5, 1, { now = now }) }) do
				expect(type(limiter.attempt)).to.equal("function")
				expect(type(limiter.wait)).to.equal("function")
				expect(type(limiter.peek)).to.equal("function")
				expect(type(limiter.reset)).to.equal("function")
			end
		end)
	end)
end
