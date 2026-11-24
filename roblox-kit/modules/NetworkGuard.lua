--!strict
-- NetworkGuard.lua — Server-Authoritative Remote Schema Validator & Token-Bucket Rate Limiter (v6.0.0)
-- Protects RemoteEvents and RemoteFunctions from NaN/Inf injection, type spoofing, and packet floods.

export type FieldRule = {
	kind: string, -- "number" | "string" | "boolean" | "Vector3" | "CFrame" | "table" | "Instance"
	optional: boolean?,
	min: number?,
	max: number?,
	maxLength: number?,
	enum: { any }?,
}

export type EndpointConfig = {
	name: string,
	ratePerSecond: number?,
	burst: number?,
	schema: { [string]: FieldRule }?,
}

-- v6.1.0: the rate limiter moved to its sibling Throttle.lua, which NetGuard also uses.
-- Copy both files; the kit has no linker, so a missing sibling is a require error and the
-- message has to say what to copy.
local _throttleScript = script.Parent and script.Parent:FindFirstChild("Throttle")
assert(_throttleScript, "NetworkGuard needs Throttle.lua beside it - copy roblox-kit/modules/Throttle.lua into the same parent")
local Throttle = require(_throttleScript)

local NetworkGuard = {}
NetworkGuard.__index = NetworkGuard

-- opts is optional and additive: `NetworkGuard.new()` still works, and `new({ now = fn })`
-- lets a spec drive the bucket from a fake clock instead of sleeping.
function NetworkGuard.new(opts: { now: (() -> number)? }?)
	local self = setmetatable({}, NetworkGuard)
	self._endpoints = {} :: { [string]: EndpointConfig }
	self._buckets = {} :: { [string]: any } -- key -> { limiter: Throttle.Limiter, violations: number }
	self._anomalyLog = {} :: { { player: string, endpoint: string, reason: string, timestamp: number } }
	self._clock = opts
	return self
end

function NetworkGuard:RegisterEndpoint(config: EndpointConfig)
	assert(type(config) == "table" and type(config.name) == "string", "EndpointConfig requires a name")
	self._endpoints[config.name] = config
end

function NetworkGuard:_recordAnomaly(playerKey: string, endpoint: string, reason: string)
	table.insert(self._anomalyLog, {
		player = playerKey,
		endpoint = endpoint,
		reason = reason,
		timestamp = os.clock(),
	})
	if #self._anomalyLog > 250 then
		table.remove(self._anomalyLog, 1)
	end
end

function NetworkGuard:GetAnomalies(): { { player: string, endpoint: string, reason: string, timestamp: number } }
	return self._anomalyLog
end

function NetworkGuard.IsFiniteNumber(val: any): boolean
	return type(val) == "number" and val == val and val > -math.huge and val < math.huge
end

function NetworkGuard.IsSafeVector3(val: any, maxMagnitude: number?): boolean
	if typeof(val) ~= "Vector3" then
		return false
	end
	if not (NetworkGuard.IsFiniteNumber(val.X) and NetworkGuard.IsFiniteNumber(val.Y) and NetworkGuard.IsFiniteNumber(val.Z)) then
		return false
	end
	if maxMagnitude and val.Magnitude > maxMagnitude then
		return false
	end
	return true
end

function NetworkGuard:CheckRateLimit(playerKey: string, endpointName: string): (boolean, string?)
	local cfg = self._endpoints[endpointName]
	local rate = (cfg and cfg.ratePerSecond) or 10
	local burst = (cfg and cfg.burst) or math.max(3, rate * 2)
	local key = playerKey .. "::" .. endpointName
	local bucket = self._buckets[key]
	if not bucket then
		-- Throttle.new starts full and consumes one on the first attempt, which is the same
		-- decision the inline bucket made here before v6.1.0 (tokens = burst - 1, allowed).
		bucket = { limiter = Throttle.new(rate, burst, self._clock), violations = 0 }
		self._buckets[key] = bucket
	end

	local allowed = bucket.limiter:attempt()
	if not allowed then
		bucket.violations += 1
		self:_recordAnomaly(playerKey, endpointName, "rate_limit_exceeded")
		return false, "rate_limit_exceeded"
	end
	return true, nil
end

-- How long until this player/endpoint would be allowed again, in seconds (0 if now).
function NetworkGuard:RetryAfter(playerKey: string, endpointName: string): number
	local bucket = self._buckets[playerKey .. "::" .. endpointName]
	if not bucket then
		return 0
	end
	local _, retryAfter = bucket.limiter:attempt()
	return retryAfter
end

function NetworkGuard:ValidatePayload(playerKey: string, endpointName: string, payload: any): (boolean, string?)
	local okRate, rateReason = self:CheckRateLimit(playerKey, endpointName)
	if not okRate then
		return false, rateReason
	end

	local cfg = self._endpoints[endpointName]
	if not cfg or not cfg.schema then
		return true, nil
	end
	if type(payload) ~= "table" then
		self:_recordAnomaly(playerKey, endpointName, "payload_not_table")
		return false, "payload_not_table"
	end

	for fieldName, rule in pairs(cfg.schema) do
		local val = payload[fieldName]
		if val == nil then
			if not rule.optional then
				self:_recordAnomaly(playerKey, endpointName, "missing_field:" .. fieldName)
				return false, "missing_field:" .. fieldName
			end
		else
			local actualType = typeof(val)
			if actualType ~= rule.kind then
				self:_recordAnomaly(playerKey, endpointName, "type_mismatch:" .. fieldName)
				return false, "type_mismatch:" .. fieldName
			end
			if rule.kind == "number" then
				if not NetworkGuard.IsFiniteNumber(val) then
					self:_recordAnomaly(playerKey, endpointName, "non_finite_number:" .. fieldName)
					return false, "non_finite_number:" .. fieldName
				end
				if rule.min and val < rule.min then
					self:_recordAnomaly(playerKey, endpointName, "below_min:" .. fieldName)
					return false, "below_min:" .. fieldName
				end
				if rule.max and val > rule.max then
					self:_recordAnomaly(playerKey, endpointName, "above_max:" .. fieldName)
					return false, "above_max:" .. fieldName
				end
			elseif rule.kind == "string" then
				local maxLen = rule.maxLength or 256
				if #val > maxLen then
					self:_recordAnomaly(playerKey, endpointName, "string_too_long:" .. fieldName)
					return false, "string_too_long:" .. fieldName
				end
			elseif rule.kind == "Vector3" then
				if not NetworkGuard.IsSafeVector3(val, rule.max or 10000) then
					self:_recordAnomaly(playerKey, endpointName, "unsafe_vector3:" .. fieldName)
					return false, "unsafe_vector3:" .. fieldName
				end
			end
			if rule.enum then
				local matched = false
				for _, allowed in ipairs(rule.enum) do
					if val == allowed then
						matched = true
						break
					end
				end
				if not matched then
					self:_recordAnomaly(playerKey, endpointName, "invalid_enum:" .. fieldName)
					return false, "invalid_enum:" .. fieldName
				end
			end
		end
	end

	return true, nil
end

return NetworkGuard
