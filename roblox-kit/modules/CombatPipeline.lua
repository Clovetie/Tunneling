--!nonstrict
--[[
	CombatPipeline.lua — Deterministic damage, shield absorption, armor mitigation,
	critical hits, combo chains, status effects (`Burn`, `Slow`, `Stun`, `Expose`),
	and aggro/threat table calculation.
]]

local CombatPipeline = {}
CombatPipeline.__index = CombatPipeline

function CombatPipeline.new()
	return setmetatable({
		_entities = {},
	}, CombatPipeline)
end

function CombatPipeline:RegisterEntity(entityId, stats)
	stats = stats or {}
	local maxHp = math.max(1, tonumber(stats.maxHealth) or 100)
	self._entities[entityId] = {
		id = entityId,
		maxHealth = maxHp,
		health = tonumber(stats.health) or maxHp,
		shield = math.max(0, tonumber(stats.shield) or 0),
		armor = math.max(0, tonumber(stats.armor) or 0),
		critChance = math.clamp(tonumber(stats.critChance) or 0.1, 0, 1),
		critMultiplier = math.max(1.0, tonumber(stats.critMultiplier) or 1.5),
		combo = 0,
		lastHitAt = 0,
		statuses = {},
		threat = {},
	}
	return self._entities[entityId]
end

function CombatPipeline:GetEntity(entityId)
	return self._entities[entityId]
end

function CombatPipeline:ApplyStatus(targetId, statusName, duration, stacks)
	local ent = self._entities[targetId]
	if not ent then
		return false
	end
	local now = os.clock()
	local existing = ent.statuses[statusName]
	local newStacks = math.max(1, tonumber(stacks) or 1)
	if existing and existing.expiresAt > now then
		existing.stacks = math.min(10, existing.stacks + newStacks)
		existing.expiresAt = math.max(existing.expiresAt, now + (tonumber(duration) or 4))
	else
		ent.statuses[statusName] = {
			name = statusName,
			stacks = newStacks,
			expiresAt = now + (tonumber(duration) or 4),
		}
	end
	return true
end

function CombatPipeline:HasStatus(targetId, statusName)
	local ent = self._entities[targetId]
	if not ent then
		return false, 0
	end
	local st = ent.statuses[statusName]
	if st and st.expiresAt > os.clock() then
		return true, st.stacks
	end
	return false, 0
end

function CombatPipeline:ResolveHit(attackerId, targetId, hitSpec)
	hitSpec = hitSpec or {}
	local attacker = self._entities[attackerId]
	local target = self._entities[targetId]
	if not target or target.health <= 0 then
		return { ok = false, reason = "invalid_or_defeated_target" }
	end

	local now = os.clock()
	local baseDamage = math.max(0, tonumber(hitSpec.baseDamage) or 10)
	local comboMult = 1.0
	if attacker then
		if now - attacker.lastHitAt <= 2.5 then
			attacker.combo = math.min(10, attacker.combo + 1)
		else
			attacker.combo = 1
		end
		attacker.lastHitAt = now
		comboMult = 1.0 + (attacker.combo - 1) * 0.05
	end

	local isCrit = hitSpec.forceCrit == true
	if not isCrit and attacker and not hitSpec.disableCrit then
		local roll = (hitSpec.critRoll ~= nil) and hitSpec.critRoll or math.random()
		isCrit = roll < attacker.critChance
	end
	local critMult = isCrit and (attacker and attacker.critMultiplier or 1.5) or 1.0

	-- Armor mitigation formula: 100 / (100 + armor)
	local effectiveArmor = hitSpec.trueDamage and 0 or target.armor
	local armorMult = 100 / (100 + math.max(0, effectiveArmor))

	-- Expose status increases damage taken by 15% per stack
	local exposed, exposeStacks = self:HasStatus(targetId, "Expose")
	local statusMult = exposed and (1.0 + exposeStacks * 0.15) or 1.0

	local finalDamage = math.floor(baseDamage * comboMult * critMult * armorMult * statusMult + 0.5)
	local shieldAbsorbed = math.min(target.shield, finalDamage)
	target.shield -= shieldAbsorbed
	local hpDamage = finalDamage - shieldAbsorbed
	target.health = math.max(0, target.health - hpDamage)

	if attackerId then
		local threatWeight = tonumber(hitSpec.threatMultiplier) or 1.0
		target.threat[attackerId] = (target.threat[attackerId] or 0) + finalDamage * threatWeight
	end

	if hitSpec.applyStatus then
		self:ApplyStatus(
			targetId,
			hitSpec.applyStatus.name,
			hitSpec.applyStatus.duration,
			hitSpec.applyStatus.stacks
		)
	end

	return {
		ok = true,
		damage = finalDamage,
		hpDamage = hpDamage,
		shieldAbsorbed = shieldAbsorbed,
		critical = isCrit,
		combo = attacker and attacker.combo or 1,
		remainingHealth = target.health,
		defeated = target.health <= 0,
	}
end

function CombatPipeline:GetHighestThreatTarget(entityId)
	local ent = self._entities[entityId]
	if not ent then
		return nil, 0
	end
	local bestId, bestVal = nil, -1
	for actorId, val in pairs(ent.threat) do
		if val > bestVal then
			bestId, bestVal = actorId, val
		end
	end
	return bestId, math.max(0, bestVal)
end

return CombatPipeline
