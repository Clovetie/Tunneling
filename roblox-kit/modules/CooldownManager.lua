--!nonstrict
--[[
	CooldownManager.lua — Multi-charge ability cooldown, global cooldown (GCD),
	haste multiplier, and per-entity rate limiter for Roblox gameplay systems.
]]

local CooldownManager = {}
CooldownManager.__index = CooldownManager

function CooldownManager.new(globalCooldownSeconds)
	return setmetatable({
		_gcd = tonumber(globalCooldownSeconds) or 0,
		_gcdReadyAt = 0,
		_haste = 1.0,
		_actions = {},
	}, CooldownManager)
end

function CooldownManager:SetHaste(multiplier)
	self._haste = math.max(0.1, tonumber(multiplier) or 1.0)
end

function CooldownManager:Define(actionId, cooldownSeconds, maxCharges)
	local maxC = math.max(1, math.floor(tonumber(maxCharges) or 1))
	self._actions[actionId] = {
		cooldown = math.max(0.05, tonumber(cooldownSeconds) or 1.0),
		maxCharges = maxC,
		charges = maxC,
		rechargeStart = 0,
	}
	return self
end

function CooldownManager:_sync(actionId, now)
	local entry = self._actions[actionId]
	if not entry then
		return nil
	end
	if entry.charges >= entry.maxCharges then
		entry.rechargeStart = 0
		return entry
	end
	local effectiveCd = entry.cooldown / self._haste
	if entry.rechargeStart <= 0 then
		entry.rechargeStart = now
	end
	local elapsed = now - entry.rechargeStart
	if elapsed >= effectiveCd then
		local gained = math.floor(elapsed / effectiveCd)
		entry.charges = math.min(entry.maxCharges, entry.charges + gained)
		if entry.charges >= entry.maxCharges then
			entry.rechargeStart = 0
		else
			entry.rechargeStart += gained * effectiveCd
		end
	end
	return entry
end

function CooldownManager:GetStatus(actionId)
	local now = os.clock()
	local entry = self:_sync(actionId, now)
	if not entry then
		return { ready = true, charges = 1, maxCharges = 1, remaining = 0 }
	end
	local gcdRem = math.max(0, self._gcdReadyAt - now)
	local effectiveCd = entry.cooldown / self._haste
	local chargeRem = 0
	if entry.charges < entry.maxCharges and entry.rechargeStart > 0 then
		chargeRem = math.max(0, effectiveCd - (now - entry.rechargeStart))
	end
	local ready = (entry.charges > 0) and (gcdRem <= 0)
	return {
		ready = ready,
		charges = entry.charges,
		maxCharges = entry.maxCharges,
		remaining = entry.charges > 0 and gcdRem or math.max(gcdRem, chargeRem),
		nextChargeIn = chargeRem,
	}
end

function CooldownManager:TryConsume(actionId, fallbackCooldown)
	if not self._actions[actionId] then
		self:Define(actionId, fallbackCooldown or 1.0, 1)
	end
	local now = os.clock()
	local entry = self:_sync(actionId, now)
	if now < self._gcdReadyAt then
		return false, "gcd", self._gcdReadyAt - now
	end
	if entry.charges <= 0 then
		local effectiveCd = entry.cooldown / self._haste
		local rem = math.max(0, effectiveCd - (now - entry.rechargeStart))
		return false, "cooldown", rem
	end
	if entry.charges == entry.maxCharges then
		entry.rechargeStart = now
	end
	entry.charges -= 1
	if self._gcd > 0 then
		self._gcdReadyAt = now + (self._gcd / self._haste)
	end
	return true, "ok", 0
end

function CooldownManager:Reset(actionId)
	if actionId then
		local entry = self._actions[actionId]
		if entry then
			entry.charges = entry.maxCharges
			entry.rechargeStart = 0
		end
		return
	end
	for _, entry in pairs(self._actions) do
		entry.charges = entry.maxCharges
		entry.rechargeStart = 0
	end
	self._gcdReadyAt = 0
end

return CooldownManager
