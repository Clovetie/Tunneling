-- NetGuard.lua — Server-side RemoteEvent / RemoteFunction rate-limiter
-- and payload sanitizer (prevents NaN, Inf, oversized strings, and spam).
-- v6.1.0: the fixed-window counter moved to its sibling Throttle.lua, which NetworkGuard
-- also uses (it takes the token-bucket half). The kit has no linker, so copy both files:
-- a missing sibling is a require error whose message says what to copy.
local _throttleScript = script.Parent and script.Parent:FindFirstChild("Throttle")
assert(_throttleScript, "NetGuard needs Throttle.lua beside it - copy roblox-kit/modules/Throttle.lua into the same parent")
local Throttle = require(_throttleScript)

local Players = game:GetService("Players")

local NetGuard = {}
local buckets = {}

Players.PlayerRemoving:Connect(function(player)
	buckets[player] = nil
end)

function NetGuard.isFiniteNumber(v, minVal, maxVal)
	if type(v) ~= "number" or v ~= v or v == math.huge or v == -math.huge then
		return false
	end
	if minVal ~= nil and v < minVal then
		return false
	end
	if maxVal ~= nil and v > maxVal then
		return false
	end
	return true
end

function NetGuard.isCleanString(v, maxLen)
	return type(v) == "string" and #v <= (maxLen or 128)
end

function NetGuard.isFiniteVector3(v, maxMagnitude)
	if typeof(v) ~= "Vector3" then
		return false
	end
	if not (NetGuard.isFiniteNumber(v.X) and NetGuard.isFiniteNumber(v.Y) and NetGuard.isFiniteNumber(v.Z)) then
		return false
	end
	if maxMagnitude and v.Magnitude > maxMagnitude then
		return false
	end
	return true
end

function NetGuard.allowRate(player, actionKey, maxCallsPerWindow, windowSeconds)
	if typeof(player) ~= "Instance" or not player:IsA("Player") then
		return false
	end
	local pMap = buckets[player]
	if not pMap then
		pMap = {}
		buckets[player] = pMap
	end
	local limiter = pMap[actionKey]
	if not limiter then
		-- The first limit/window given for an actionKey is the one that governs it. Passing
		-- different numbers for the same key later was already a caller bug (the old inline
		-- counter mixed the new limit with the old window's start), so it is now ignored
		-- rather than half-applied.
		limiter = Throttle.fixedWindow(maxCallsPerWindow or 8, windowSeconds or 1.0)
		pMap[actionKey] = limiter
	end
	-- Parentheses drop the second return (retryAfter): allowRate has always returned a
	-- bare boolean and callers depend on that.
	return (limiter:attempt())
end

-- Seconds until this player/actionKey is allowed again (0 if it is allowed now).
function NetGuard.rateRetryAfter(player, actionKey)
	local pMap = buckets[player]
	local limiter = pMap and pMap[actionKey]
	if not limiter then
		return 0
	end
	local _, retryAfter = limiter:attempt()
	return retryAfter
end

function NetGuard.bindRemoteEvent(remote, opts, handler)
	local rate = (opts and opts.maxPerSecond) or 10
	return remote.OnServerEvent:Connect(function(player, ...)
		if not NetGuard.allowRate(player, remote.Name, rate, 1.0) then
			return
		end
		if opts and opts.validate and not opts.validate(player, ...) then
			return
		end
		handler(player, ...)
	end)
end

return NetGuard
