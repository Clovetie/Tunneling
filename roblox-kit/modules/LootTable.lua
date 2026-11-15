--!nonstrict
--[[
	LootTable.lua — Deterministic weighted loot drop engine with rarity tiers,
	luck multiplier scaling, and guaranteed pity counter.
]]

local LootTable = {}
LootTable.__index = LootTable

local RARITY_RANK = {
	Common = 1,
	Uncommon = 2,
	Rare = 3,
	Epic = 4,
	Legendary = 5,
	Mythic = 6,
}

function LootTable.new(entries, pityConfig)
	local list = {}
	for _, item in ipairs(entries or {}) do
		table.insert(list, {
			id = item.id or item.name or "item",
			name = item.name or item.id or "Item",
			weight = math.max(0.001, tonumber(item.weight) or 1),
			rarity = item.rarity or "Common",
			minQty = tonumber(item.minQty) or 1,
			maxQty = tonumber(item.maxQty) or tonumber(item.minQty) or 1,
			metadata = item.metadata or {},
		})
	end
	local pity = pityConfig or {}
	return setmetatable({
		_items = list,
		_pityThreshold = tonumber(pity.threshold) or 0,
		_pityMinRarity = pity.minRarity or "Epic",
		_rollsSincePity = {},
	}, LootTable)
end

function LootTable:_adjustedWeight(item, luckMultiplier)
	local luck = math.max(0.1, tonumber(luckMultiplier) or 1.0)
	local rank = RARITY_RANK[item.rarity] or 1
	if rank >= 3 and luck ~= 1.0 then
		return item.weight * (luck ^ ((rank - 2) * 0.65))
	end
	return item.weight
end

function LootTable:GetProbabilities(luckMultiplier)
	local total = 0
	local weights = {}
	for i, item in ipairs(self._items) do
		local w = self:_adjustedWeight(item, luckMultiplier)
		weights[i] = w
		total += w
	end
	local out = {}
	for i, item in ipairs(self._items) do
		table.insert(out, {
			id = item.id,
			name = item.name,
			rarity = item.rarity,
			probability = total > 0 and (weights[i] / total) or 0,
		})
	end
	return out
end

function LootTable:Roll(opts)
	opts = opts or {}
	local actorKey = tostring(opts.playerKey or "global")
	local luck = tonumber(opts.luck) or 1.0
	local rng = opts.random or Random.new()

	local pityCount = (self._rollsSincePity[actorKey] or 0) + 1
	local forcePity = self._pityThreshold > 0 and pityCount >= self._pityThreshold
	local minRank = RARITY_RANK[self._pityMinRarity] or 4

	local pool = {}
	local totalWeight = 0
	for _, item in ipairs(self._items) do
		local rank = RARITY_RANK[item.rarity] or 1
		if not forcePity or rank >= minRank then
			local w = self:_adjustedWeight(item, luck)
			table.insert(pool, { item = item, weight = w })
			totalWeight += w
		end
	end
	if #pool == 0 then
		for _, item in ipairs(self._items) do
			local w = self:_adjustedWeight(item, luck)
			table.insert(pool, { item = item, weight = w })
			totalWeight += w
		end
	end
	if #pool == 0 then
		return nil
	end

	local pickVal = rng:NextNumber(0, totalWeight)
	local chosen = pool[#pool].item
	local acc = 0
	for _, entry in ipairs(pool) do
		acc += entry.weight
		if pickVal <= acc then
			chosen = entry.item
			break
		end
	end

	local chosenRank = RARITY_RANK[chosen.rarity] or 1
	if chosenRank >= minRank then
		self._rollsSincePity[actorKey] = 0
	else
		self._rollsSincePity[actorKey] = pityCount
	end

	local qty = chosen.minQty
	if chosen.maxQty > chosen.minQty then
		qty = rng:NextInteger(chosen.minQty, chosen.maxQty)
	end

	return {
		id = chosen.id,
		name = chosen.name,
		rarity = chosen.rarity,
		quantity = qty,
		pityTriggered = forcePity,
		metadata = chosen.metadata,
	}
end

function LootTable:RollBatch(count, opts)
	local drops = {}
	for _ = 1, math.max(1, tonumber(count) or 1) do
		local drop = self:Roll(opts)
		if drop then
			table.insert(drops, drop)
		end
	end
	return drops
end

return LootTable
