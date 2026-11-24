--!nonstrict
local CheckpointConfig = require(script.Parent:WaitForChild("CheckpointConfig"))

local CheckpointOrder = {}

-- order is always `{ [id: string] = order: integer >= 1 }`, claimed is always
-- `{ [id: string] = true }`. Nothing here touches the DataModel, so a spec can drive it
-- and the server can trust it: every decision about progression is made in this file.

local function isId(value)
	return type(value) == "string" and value ~= ""
end

local function isOrder(value)
	return type(value) == "number" and value == math.floor(value) and value >= 1 and value < math.huge
end

-- Accepts `{ { id = "a", order = 1 }, … }`, `{ a = 1, b = 2 }` or a bare array of ids
-- (position becomes the order). Anything malformed is dropped, not guessed at.
function CheckpointOrder.buildOrder(source)
	local order = {}
	if type(source) ~= "table" then
		return order
	end
	-- A bare array of ids first, with ipairs: pairs() has no defined order, so positions
	-- taken from it would be arbitrary and a game's checkpoint order would shuffle.
	for index, value in ipairs(source) do
		if isId(value) then
			order[value] = index
		end
	end
	for key, value in pairs(source) do
		if type(value) == "table" and isId(value.id) and isOrder(value.order) then
			order[value.id] = value.order
		elseif isId(key) and isOrder(value) then
			order[key] = value
		end
	end
	return order
end

-- Duplicate or missing orders make "highest claimed" ambiguous, so they are reported
-- instead of resolved by whichever key pairs() happens to visit first.
function CheckpointOrder.audit(order)
	local problems = {}
	local byOrder = {}
	for id, value in pairs(order or {}) do
		if not isId(id) then
			table.insert(problems, "a checkpoint id is not a non-empty string")
		elseif not isOrder(value) then
			table.insert(problems, ("%s: order %s is not an integer >= 1"):format(tostring(id), tostring(value)))
		else
			local other = byOrder[value]
			if other then
				table.insert(problems, ("%s and %s both have order %d"):format(other, id, value))
			else
				byOrder[value] = id
			end
		end
	end
	table.sort(problems)
	return problems
end

-- The claimed checkpoint with the highest order that also exists in `order`. A stale id
-- left in a save after a checkpoint was deleted from the place is ignored, not honoured.
function CheckpointOrder.highestClaimed(claimed, order)
	local bestId, bestOrder = nil, 0
	for id in pairs(claimed or {}) do
		local value = order and order[id]
		if isOrder(value) and value > bestOrder then
			bestId, bestOrder = id, value
		end
	end
	return bestId, bestOrder > 0 and bestOrder or nil
end

function CheckpointOrder.canClaim(claimed, order, id)
	if not isId(id) then
		return false, "bad_id"
	end
	if not order or not isOrder(order[id]) then
		return false, "unknown_checkpoint"
	end
	if claimed and claimed[id] then
		return false, "already_claimed"
	end
	if not CheckpointConfig.STRICT_ORDER then
		return true, nil
	end
	local _, highest = CheckpointOrder.highestClaimed(claimed, order)
	local expected = (highest or 0) + 1
	if order[id] ~= expected then
		-- Saying which one is next is what makes a skipped checkpoint debuggable.
		return false, "out_of_order"
	end
	return true, nil
end

-- Returns a *new* claimed table: the caller's is never mutated, so a rejected claim cannot
-- half-apply and a save table stays safe to hand to other systems.
function CheckpointOrder.claim(claimed, order, id)
	-- `updated`, not `next`: a local named next would shadow the builtin for the rest of
	-- the function, which is the kind of thing that only bites somebody editing it later.
	local updated = {}
	for key, value in pairs(claimed or {}) do
		updated[key] = value
	end
	local ok, reason = CheckpointOrder.canClaim(claimed, order, id)
	if not ok then
		return updated, false, reason
	end
	updated[id] = true
	return updated, true, nil
end

-- Where a player respawns: the highest claimed checkpoint, or the config default when
-- nothing is claimed yet (a game with no default respawns at its own SpawnLocations).
function CheckpointOrder.respawnId(claimed, order)
	local id = CheckpointOrder.highestClaimed(claimed, order)
	if id then
		return id
	end
	-- Read live, the way STRICT_ORDER is: a game that flips its config at runtime (or a
	-- spec that sets one) gets the new value, not a copy taken at require time.
	local fallback = CheckpointConfig.DEFAULT_CHECKPOINT_ID
	if isId(fallback) and order and isOrder(order[fallback]) then
		return fallback
	end
	return nil
end

function CheckpointOrder.progress(claimed, order)
	local total = 0
	for _ in pairs(order or {}) do
		total += 1
	end
	local done = 0
	for id in pairs(claimed or {}) do
		if order and isOrder(order[id]) then
			done += 1
		end
	end
	return done, total, total > 0 and (done / total) or 0
end

-- The next id a player is expected to claim, for "Checkpoint 3 of 12" style UI.
function CheckpointOrder.nextId(claimed, order)
	local _, highest = CheckpointOrder.highestClaimed(claimed, order)
	local expected = (highest or 0) + 1
	for id, value in pairs(order or {}) do
		if value == expected then
			return id, expected
		end
	end
	return nil, nil
end

return CheckpointOrder
