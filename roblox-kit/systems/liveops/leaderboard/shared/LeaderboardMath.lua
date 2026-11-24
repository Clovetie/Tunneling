--!nonstrict
-- Pure ranking logic for liveops/leaderboard: no DataStore, no Players, no Roblox types,
-- so tests/Leaderboard.spec.lua can run without a DataModel. Rows are plain tables of
-- { userId = number, name = string, score = integer }.
--
-- Every function returns a new table and never mutates the one it was given: a board is
-- read from a cache by several callers at once, and one of them sorting in place would
-- reorder the rows under the others.

local LeaderboardMath = {}

local FALLBACK_NAME = "Player"

local function isFiniteNumber(value)
	return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
end

LeaderboardMath.isFiniteNumber = isFiniteNumber

-- OrderedDataStore values are integers. Flooring on the way in means a stored score and a
-- displayed one cannot disagree, and a fractional score cannot reshuffle the board between
-- two reads of the same data.
function LeaderboardMath.toScore(value)
	if not isFiniteNumber(value) then
		return nil
	end
	return math.floor(value)
end

-- Cutting a name at MAX_NAME_LENGTH by byte count can land mid-codepoint, which renders as
-- garbage in a TextLabel. Take whole codepoints up to the byte budget instead: trimming
-- trailing continuation bytes is not enough, because a cut that lands exactly on a
-- boundary would then delete a character that was already complete.
function LeaderboardMath.trimName(name, maxLength)
	if type(name) ~= "string" or name == "" then
		return FALLBACK_NAME
	end
	local limit = isFiniteNumber(maxLength) and math.max(1, math.floor(maxLength)) or #name
	if #name <= limit then
		return name
	end
	local ok, count = pcall(utf8.len, name)
	if not ok or type(count) ~= "number" then
		return name:sub(1, limit)
	end
	local bytes = 0
	for index = 1, count do
		local nextStart = utf8.offset(name, index + 1)
		local charEnd = (nextStart or (#name + 1)) - 1
		if charEnd > limit then
			break
		end
		bytes = charEnd
	end
	if bytes == 0 then
		return FALLBACK_NAME
	end
	return name:sub(1, bytes)
end

-- Returns a clean row, or nil if the input cannot be one. The server runs store rows
-- through this before they reach a client: a corrupt or hand-edited entry must cost one
-- row, not the whole board.
function LeaderboardMath.sanitizeRow(row, maxNameLength)
	if type(row) ~= "table" then
		return nil
	end
	local userId = row.userId
	if not isFiniteNumber(userId) or userId < 0 then
		return nil
	end
	local score = LeaderboardMath.toScore(row.score)
	if score == nil or score < 0 then
		return nil
	end
	return {
		userId = math.floor(userId),
		name = LeaderboardMath.trimName(row.name, maxNameLength),
		score = score,
	}
end

-- Score descending, then name, then id. Without the tie-breaks two players on the same
-- score swap places on every refresh, which reads as a bug even though nothing is wrong.
local function compareRows(a, b)
	if a.score ~= b.score then
		return a.score > b.score
	end
	if a.name ~= b.name then
		return a.name < b.name
	end
	return a.userId < b.userId
end

LeaderboardMath.compareRows = compareRows

function LeaderboardMath.sortRows(rows, maxNameLength)
	local out = {}
	if type(rows) ~= "table" then
		return out
	end
	for _, row in pairs(rows) do
		local clean = LeaderboardMath.sanitizeRow(row, maxNameLength)
		if clean then
			table.insert(out, clean)
		end
	end
	table.sort(out, compareRows)
	return out
end

-- Overlays one board on another (the live per-server scores on top of the cached global
-- ones) and dedupes by id. The higher score wins; on a tie the first table wins, so the
-- result does not depend on which order the caller happened to pass them in.
function LeaderboardMath.mergeRows(primary, secondary, maxNameLength)
	local byId = {}
	-- Built as a list rather than `{ primary, secondary }`: a nil primary would leave a
	-- hole at index 1 and ipairs would skip the secondary too.
	local sources = {}
	if type(primary) == "table" then
		table.insert(sources, primary)
	end
	if type(secondary) == "table" then
		table.insert(sources, secondary)
	end
	for _, source in ipairs(sources) do
		for _, row in pairs(source) do
			local clean = LeaderboardMath.sanitizeRow(row, maxNameLength)
			if clean then
				local existing = byId[clean.userId]
				if not existing or clean.score > existing.score then
					byId[clean.userId] = clean
				end
			end
		end
	end
	local out = {}
	for _, row in pairs(byId) do
		table.insert(out, row)
	end
	table.sort(out, compareRows)
	return out
end

function LeaderboardMath.rankOf(rows, userId)
	if type(rows) ~= "table" or not isFiniteNumber(userId) then
		return nil
	end
	local wanted = math.floor(userId)
	for index, row in ipairs(rows) do
		if type(row) == "table" and row.userId == wanted then
			return index
		end
	end
	return nil
end

function LeaderboardMath.topN(rows, limit)
	local out = {}
	if type(rows) ~= "table" then
		return out
	end
	local count = isFiniteNumber(limit) and math.max(0, math.floor(limit)) or #rows
	for index, row in ipairs(rows) do
		if index > count then
			break
		end
		out[index] = row
	end
	return out
end

-- Always at least one page, so a client asking for "page 1 of an empty board" gets a
-- renderable answer instead of pageCount 0 and a blank window it cannot page out of.
function LeaderboardMath.pageCount(total, pageSize)
	local size = isFiniteNumber(pageSize) and math.floor(pageSize) or 0
	local count = isFiniteNumber(total) and math.max(0, math.floor(total)) or 0
	if size <= 0 then
		return 1
	end
	return math.max(1, math.ceil(count / size))
end

function LeaderboardMath.clampPage(page, pages)
	local wanted = isFiniteNumber(page) and math.floor(page) or 1
	local limit = isFiniteNumber(pages) and math.max(1, math.floor(pages)) or 1
	return math.clamp(wanted, 1, limit)
end

function LeaderboardMath.paginate(rows, page, pageSize)
	local list = type(rows) == "table" and rows or {}
	local size = isFiniteNumber(pageSize) and math.max(1, math.floor(pageSize)) or #list
	local total = #list
	local pages = LeaderboardMath.pageCount(total, size)
	local wanted = LeaderboardMath.clampPage(page, pages)
	local slice = {}
	local first = (wanted - 1) * size
	for offset = 1, size do
		local row = list[first + offset]
		if row == nil then
			break
		end
		slice[offset] = row
	end
	return {
		rows = slice,
		page = wanted,
		pageCount = pages,
		total = total,
		firstRank = total == 0 and 0 or first + 1,
	}
end

-- The slice a player actually looks for: themselves, and a few rows either side. Returns
-- an empty list and no rank when they are not on the board at all, which the client shows
-- as "unranked" rather than as rank 0.
function LeaderboardMath.aroundMe(rows, userId, window)
	local list = type(rows) == "table" and rows or {}
	local rank = LeaderboardMath.rankOf(list, userId)
	local total = #list
	if not rank then
		return { rows = {}, rank = nil, total = total, firstRank = 0 }
	end
	local span = isFiniteNumber(window) and math.max(0, math.floor(window)) or 0
	local low = math.max(1, rank - span)
	local high = math.min(total, rank + span)
	local slice = {}
	for index = low, high do
		slice[index - low + 1] = list[index]
	end
	return { rows = slice, rank = rank, total = total, firstRank = low }
end

-- The best score seen this session. Ignores anything that is not a finite number, so a
-- source that momentarily reads nil cannot wipe a player's place on the board.
function LeaderboardMath.best(current, incoming)
	local a = LeaderboardMath.toScore(current)
	local b = LeaderboardMath.toScore(incoming)
	if a == nil then
		return b
	end
	if b == nil then
		return a
	end
	return math.max(a, b)
end

function LeaderboardMath.shouldPublish(score, minScore)
	local value = LeaderboardMath.toScore(score)
	if value == nil then
		return false
	end
	local floor = isFiniteNumber(minScore) and math.floor(minScore) or 1
	return value >= floor
end

-- The write throttle's decision, as a pure function so it can be tested without a clock or
-- a DataStore. A missing last write is due; a nonsense clock is not, because failing open
-- there would mean writing on every sample.
function LeaderboardMath.writeDue(lastWrite, now, interval)
	if not isFiniteNumber(now) then
		return false
	end
	if not isFiniteNumber(lastWrite) then
		return true
	end
	if not isFiniteNumber(interval) or interval <= 0 then
		return true
	end
	return (now - lastWrite) >= interval
end

return LeaderboardMath
