--!strict
-- Throttle.lua — the two rate-limiting primitives the kit shares.
--
-- A token bucket refills continuously (good for "N calls per second, allow a short
-- burst"); a fixed window counts calls inside a rolling window (good for "at most N
-- per window, hard stop"). They are different guarantees, so both live here instead of
-- being re-derived inside each caller: `NetGuard` and `NetworkGuard` each hand-rolled
-- one before v6.1.0 and neither was reusable.
--
--   local Throttle = require(path.Throttle)
--   local limiter = Throttle.new(10, 20)          -- 10/s, burst 20
--   local ok, retryAfter = limiter:attempt()      -- never yields
--   limiter:wait()                                -- yields until allowed
--   limiter:peek()                                -- capacity left, consumes nothing
--   limiter:reset()
--
--   local window = Throttle.fixedWindow(8, 1.0)   -- at most 8 calls per 1s window
--
-- Both return the same four methods, so a caller can swap one for the other. Pass
-- `{ now = fn }` to drive either from a test clock: the specs do, which is why neither
-- needs to sleep to be verified.
--
-- Defaults, used whenever an argument is nil, zero, negative, NaN or infinite: rate 10/s,
-- burst `max(3, rate * 2)`, window limit 8 calls, window 1s. A limiter that refuses
-- everything because someone passed 0 is a worse accident than one that runs at the
-- documented default — an emergency stop belongs in FeatureFlags, not here.

export type Limiter = {
	attempt: () -> (boolean, number),
	wait: () -> number,
	peek: () -> number,
	reset: () -> (),
}

export type Options = {
	now: (() -> number)?,
}

local Throttle = {}

local function clockNow(): number
	return os.clock()
end

-- NaN, infinities, zero and negatives would all produce a limiter that either never
-- refills or refills instantly, so they fall back to the documented default.
local function sane(value: any, fallback: number): number
	if type(value) ~= "number" then
		return fallback
	end
	local n = value :: number
	if n ~= n or n <= 0 or n == math.huge then
		return fallback
	end
	return n
end

local function nowOf(opts: Options?): () -> number
	local fn = opts and opts.now
	if type(fn) == "function" then
		return fn
	end
	return clockNow
end

function Throttle.new(rate: number?, burst: number?, opts: Options?): Limiter
	local refill = sane(rate, 10)
	local capacity = sane(burst, math.max(3, refill * 2))
	local now = nowOf(opts)
	local tokens = capacity
	local last = now()

	local self = {} :: Limiter

	self.attempt = function(): (boolean, number)
		local t = now()
		-- Mirror the pre-v6.1.0 NetworkGuard arithmetic exactly: clamp the elapsed time at
		-- zero, refill proportionally, cap at the burst, and move the stamp even when the
		-- clock went backwards.
		local elapsed = math.max(0, t - last)
		tokens = math.min(capacity, tokens + elapsed * refill)
		last = t
		if tokens < 1 then
			return false, (1 - tokens) / refill
		end
		tokens -= 1
		return true, 0
	end

	self.peek = function(): number
		local t = now()
		return math.min(capacity, tokens + math.max(0, t - last) * refill)
	end

	self.reset = function()
		tokens = capacity
		last = now()
	end

	self.wait = function(): number
		local started = now()
		while true do
			local ok, retryAfter = self.attempt()
			if ok then
				return now() - started
			end
			-- Never sleep longer than a second: a caller who set rate = 0.001 should still
			-- get control back often enough to notice, and task.wait clamps at 1/60 anyway.
			task.wait(math.min(math.max(retryAfter, 1 / 60), 1))
		end
	end

	return self
end

function Throttle.fixedWindow(limit: number?, windowSeconds: number?, opts: Options?): Limiter
	local maxCalls = math.max(1, math.floor(sane(limit, 8)))
	local window = sane(windowSeconds, 1)
	local now = nowOf(opts)
	local startedAt: number? = nil
	local count = 0

	local self = {} :: Limiter

	self.attempt = function(): (boolean, number)
		local t = now()
		local start = startedAt
		if start == nil or (t - start) >= window then
			startedAt = t
			count = 1
			return true, 0
		end
		if count >= maxCalls then
			return false, (start + window) - t
		end
		count += 1
		return true, 0
	end

	self.peek = function(): number
		local start = startedAt
		if start == nil or (now() - start) >= window then
			return maxCalls
		end
		return math.max(0, maxCalls - count)
	end

	self.reset = function()
		startedAt = nil
		count = 0
	end

	self.wait = function(): number
		local began = now()
		while true do
			local ok, retryAfter = self.attempt()
			if ok then
				return now() - began
			end
			task.wait(math.min(math.max(retryAfter, 1 / 60), window))
		end
	end

	return self
end

return Throttle
