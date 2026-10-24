-- NetGuard.lua — Server-side RemoteEvent / RemoteFunction rate-limiter
-- and payload sanitizer (prevents NaN, Inf, oversized strings, and spam).
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
	local now = os.clock()
	local win = windowSeconds or 1.0
	local limit = maxCallsPerWindow or 8
	local state = pMap[actionKey]
	if not state or (now - state.start) >= win then
		pMap[actionKey] = { start = now, count = 1 }
		return true
	end
	if state.count >= limit then
		return false
	end
	state.count = state.count + 1
	return true
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
