--!nonstrict
-- Leaderboard.spec.lua — the pure half of liveops/leaderboard: sanitising store rows,
-- merging a live per-server board onto a cached global one, ranking, pagination and the
-- write throttle's decision. No DataStore, no Players, no window.

return function()
	local function findShared(name)
		-- Works in the kit layout (`tests/` beside `shared/`) and in an installed game
		-- (`tests/` with `src/shared/` mapped into ReplicatedStorage).
		local sharedFolder = script.Parent.Parent:FindFirstChild("shared")
		local found = sharedFolder and sharedFolder:FindFirstChild(name)
		if found then
			return require(found)
		end
		return require(game:GetService("ReplicatedStorage"):WaitForChild(name, 10))
	end

	local Math = findShared("LeaderboardMath")
	local Config = findShared("LeaderboardConfig")

	local function row(userId, name, score)
		return { userId = userId, name = name, score = score }
	end

	local function ids(rows)
		local out = {}
		for index, entry in ipairs(rows) do
			out[index] = entry.userId
		end
		return out
	end

	local function scores(rows)
		local out = {}
		for index, entry in ipairs(rows) do
			out[index] = entry.score
		end
		return out
	end

	-- TestEZ's to.equal is reference equality on tables, so a list is checked element by
	-- element. Comparing ids(rows) against a literal table would fail; comparing it against
	-- itself would pass while asserting nothing, which is what these replace.
	local function expectIds(rows, wanted)
		expect(#rows).to.equal(#wanted)
		for index, id in ipairs(wanted) do
			expect(rows[index] ~= nil).to.equal(true)
			expect(rows[index].userId).to.equal(id)
		end
	end

	local function expectScores(rows, wanted)
		expect(#rows).to.equal(#wanted)
		for index, score in ipairs(wanted) do
			expect(rows[index] ~= nil).to.equal(true)
			expect(rows[index].score).to.equal(score)
		end
	end

	describe("toScore", function()
		it("floors, because an OrderedDataStore value has to be an integer", function()
			expect(Math.toScore(10.7)).to.equal(10)
			expect(Math.toScore(0.5)).to.equal(0)
			expect(Math.toScore(42)).to.equal(42)
			expect(Math.toScore(-3)).to.equal(-3)
		end)

		it("refuses anything that is not a finite number", function()
			expect(Math.toScore(0 / 0)).never.to.be.ok()
			expect(Math.toScore(math.huge)).never.to.be.ok()
			expect(Math.toScore(-math.huge)).never.to.be.ok()
			expect(Math.toScore("42")).never.to.be.ok()
			expect(Math.toScore(true)).never.to.be.ok()
			expect(Math.toScore(nil)).never.to.be.ok()
		end)
	end)

	describe("trimName", function()
		it("falls back for anything that is not a usable string", function()
			expect(Math.trimName("", 8)).to.equal("Player")
			expect(Math.trimName(nil, 8)).to.equal("Player")
			expect(Math.trimName(42, 8)).to.equal("Player")
		end)

		it("leaves a name inside the budget alone", function()
			expect(Math.trimName("abc", 8)).to.equal("abc")
			expect(Math.trimName("abc")).to.equal("abc")
		end)

		it("truncates to the byte budget", function()
			expect(Math.trimName("abcdefgh", 4)).to.equal("abcd")
			expect(Math.trimName("abcdef", 1)).to.equal("a")
		end)

		it("never cuts a codepoint in half, and never drops a whole one", function()
			-- "é" is two bytes. A limit of 3 fits "aé" exactly, so trimming continuation
			-- bytes would have deleted a character that was already complete.
			expect(Math.trimName("aébc", 3)).to.equal("aé")
			expect(Math.trimName("aébc", 2)).to.equal("a")
			expect(Math.trimName("aébc", 4)).to.equal("aéb")
			expect(Math.trimName("日本語の名前", 6)).to.equal("日本")
			expect(Math.trimName("日本語の名前", 7)).to.equal("日本")
		end)

		it("falls back rather than storing half a character", function()
			expect(Math.trimName("é", 1)).to.equal("Player")
		end)
	end)

	describe("sanitizeRow", function()
		it("keeps a valid row and returns a copy, not the caller's table", function()
			local original = row(7, "Alice", 100)
			local clean = Math.sanitizeRow(original, 24)
			expect(clean.userId).to.equal(7)
			expect(clean.name).to.equal("Alice")
			expect(clean.score).to.equal(100)
			expect(clean).never.to.equal(original)
			clean.score = 1
			expect(original.score).to.equal(100)
		end)

		it("floors the score and trims the name", function()
			local clean = Math.sanitizeRow(row(7, "abcdefgh", 100.9), 4)
			expect(clean.score).to.equal(100)
			expect(clean.name).to.equal("abcd")
		end)

		it("refuses rows that cannot be ranked", function()
			expect(Math.sanitizeRow("nope")).never.to.be.ok()
			expect(Math.sanitizeRow(nil)).never.to.be.ok()
			expect(Math.sanitizeRow({})).never.to.be.ok()
			expect(Math.sanitizeRow(row("7", "Alice", 10))).never.to.be.ok()
			expect(Math.sanitizeRow(row(-1, "Alice", 10))).never.to.be.ok()
			expect(Math.sanitizeRow(row(0 / 0, "Alice", 10))).never.to.be.ok()
			expect(Math.sanitizeRow(row(7, "Alice", nil))).never.to.be.ok()
			expect(Math.sanitizeRow(row(7, "Alice", "10"))).never.to.be.ok()
			expect(Math.sanitizeRow(row(7, "Alice", -5))).never.to.be.ok()
			expect(Math.sanitizeRow(row(7, "Alice", 0 / 0))).never.to.be.ok()
		end)

		it("accepts a score of zero and a missing name", function()
			expect(Math.sanitizeRow(row(7, nil, 0)).score).to.equal(0)
			expect(Math.sanitizeRow(row(7, nil, 0)).name).to.equal("Player")
			expect(Math.sanitizeRow(row(0, "Alice", 0)).userId).to.equal(0)
		end)
	end)

	describe("sortRows", function()
		it("orders by score, then name, then id, so equal scores do not reshuffle", function()
			local sorted = Math.sortRows({
				row(3, "Bob", 10),
				row(1, "Alice", 50),
				row(2, "Alice", 10),
				row(4, "Cara", 50),
			})
			expectIds(sorted, { 1, 4, 2, 3 })
		end)

		it("drops rows it cannot sanitise", function()
			local sorted = Math.sortRows({ row(1, "Alice", 5), "junk", row(2, nil, nil), row(3, "Bob", 1) })
			expect(#sorted).to.equal(2)
			expect(sorted[2].userId).to.equal(3)
		end)

		it("does not reorder the table it was given", function()
			local input = { row(1, "Alice", 5), row(2, "Bob", 50) }
			Math.sortRows(input)
			expect(input[1].userId).to.equal(1)
			expect(input[2].userId).to.equal(2)
		end)

		it("returns an empty list for anything that is not one", function()
			expect(#Math.sortRows(nil)).to.equal(0)
			expect(#Math.sortRows("rows")).to.equal(0)
			expect(#Math.sortRows({})).to.equal(0)
		end)
	end)

	describe("mergeRows", function()
		it("dedupes by id and keeps the higher score", function()
			local merged = Math.mergeRows({ row(1, "Alice", 50), row(2, "Bob", 10) }, { row(1, "Alice", 90) })
			expect(#merged).to.equal(2)
			expect(merged[1].userId).to.equal(1)
			expect(merged[1].score).to.equal(90)
			expect(merged[2].userId).to.equal(2)
		end)

		it("keeps the primary row on a tie, so the result does not depend on argument order", function()
			local merged = Math.mergeRows({ row(1, "Cached", 50) }, { row(1, "Live", 50) })
			expect(#merged).to.equal(1)
			expect(merged[1].name).to.equal("Cached")
		end)

		it("still returns the secondary rows when the primary board is missing", function()
			-- `{ nil, secondary }` has a hole at index 1, so ipairs would have skipped both.
			local merged = Math.mergeRows(nil, { row(5, "Solo", 7) })
			expect(#merged).to.equal(1)
			expect(merged[1].userId).to.equal(5)
		end)

		it("is empty when there is nothing to merge", function()
			expect(#Math.mergeRows(nil, nil)).to.equal(0)
			expect(#Math.mergeRows({}, {})).to.equal(0)
		end)

		it("drops unusable rows and sorts what is left", function()
			local merged = Math.mergeRows({ row(1, "Alice", 5), row(2, "Bob", -3) }, { row(3, "Cara", 50) })
			expectScores(merged, { 50, 5 })
			expectIds(merged, { 3, 1 })
		end)
	end)

	describe("rankOf", function()
		it("finds a player's position", function()
			local sorted = Math.sortRows({ row(1, "Alice", 50), row(2, "Bob", 30), row(3, "Cara", 10) })
			expect(Math.rankOf(sorted, 1)).to.equal(1)
			expect(Math.rankOf(sorted, 3)).to.equal(3)
		end)

		it("is nil for a player who is not on the board", function()
			local sorted = Math.sortRows({ row(1, "Alice", 50) })
			expect(Math.rankOf(sorted, 99)).never.to.be.ok()
			expect(Math.rankOf({}, 1)).never.to.be.ok()
			expect(Math.rankOf(nil, 1)).never.to.be.ok()
			expect(Math.rankOf(sorted, "1")).never.to.be.ok()
		end)
	end)

	describe("topN", function()
		it("takes the first n and tolerates an n that is too big, too small or absent", function()
			local sorted = Math.sortRows({ row(1, "A", 30), row(2, "B", 20), row(3, "C", 10) })
			expect(#Math.topN(sorted, 2)).to.equal(2)
			expect(#Math.topN(sorted, 99)).to.equal(3)
			expect(#Math.topN(sorted, 0)).to.equal(0)
			expect(#Math.topN(sorted, -1)).to.equal(0)
			expect(#Math.topN(sorted)).to.equal(3)
			expect(#Math.topN(nil, 3)).to.equal(0)
		end)
	end)

	describe("pageCount", function()
		it("is never zero, so an empty board still has a page 1", function()
			expect(Math.pageCount(0, 20)).to.equal(1)
			expect(Math.pageCount(-3, 20)).to.equal(1)
			expect(Math.pageCount(5, 0)).to.equal(1)
			expect(Math.pageCount(5, nil)).to.equal(1)
		end)

		it("rounds a partial page up", function()
			expect(Math.pageCount(20, 20)).to.equal(1)
			expect(Math.pageCount(21, 20)).to.equal(2)
			expect(Math.pageCount(100, 20)).to.equal(5)
			expect(Math.pageCount(101, 20)).to.equal(6)
		end)
	end)

	describe("clampPage", function()
		it("keeps a page inside the board", function()
			expect(Math.clampPage(3, 5)).to.equal(3)
			expect(Math.clampPage(0, 5)).to.equal(1)
			expect(Math.clampPage(-4, 5)).to.equal(1)
			expect(Math.clampPage(9, 5)).to.equal(5)
		end)

		it("treats a nonsense page as the first one", function()
			expect(Math.clampPage(nil, 5)).to.equal(1)
			expect(Math.clampPage("2", 5)).to.equal(1)
			expect(Math.clampPage(2.7, 5)).to.equal(2)
			expect(Math.clampPage(2, 0)).to.equal(1)
		end)
	end)

	describe("paginate", function()
		local function board(count)
			local list = {}
			for index = 1, count do
				list[index] = row(index, ("P%02d"):format(index), count - index + 1)
			end
			return list
		end

		it("slices a page and reports the rank its first row holds", function()
			local out = Math.paginate(board(45), 2, 20)
			expect(out.page).to.equal(2)
			expect(out.pageCount).to.equal(3)
			expect(out.total).to.equal(45)
			expect(out.firstRank).to.equal(21)
			expect(#out.rows).to.equal(20)
			expect(out.rows[1].userId).to.equal(21)
			expect(out.rows[20].userId).to.equal(40)
		end)

		it("clamps a page past the end and returns the short last page", function()
			local out = Math.paginate(board(45), 9, 20)
			expect(out.page).to.equal(3)
			expect(#out.rows).to.equal(5)
			expect(out.firstRank).to.equal(41)
			expect(out.rows[5].userId).to.equal(45)
		end)

		it("answers an empty board with one empty page rather than nothing to render", function()
			local out = Math.paginate({}, 1, 20)
			expect(out.page).to.equal(1)
			expect(out.pageCount).to.equal(1)
			expect(out.total).to.equal(0)
			expect(out.firstRank).to.equal(0)
			expect(#out.rows).to.equal(0)
		end)

		it("defaults the page size to the whole board", function()
			local out = Math.paginate(board(7), 1, nil)
			expect(#out.rows).to.equal(7)
			expect(out.pageCount).to.equal(1)
		end)

		it("does not mutate the board it was given", function()
			local list = board(5)
			Math.paginate(list, 1, 2)
			expect(#list).to.equal(5)
			expect(list[1].userId).to.equal(1)
		end)
	end)

	describe("aroundMe", function()
		local function board(count)
			local list = {}
			for index = 1, count do
				list[index] = row(index, ("P%02d"):format(index), count - index + 1)
			end
			return list
		end

		it("centres on the player", function()
			local out = Math.aroundMe(board(10), 5, 2)
			expect(out.rank).to.equal(5)
			expect(out.firstRank).to.equal(3)
			expect(#out.rows).to.equal(5)
			expect(out.rows[1].userId).to.equal(3)
			expect(out.rows[3].userId).to.equal(5)
			expect(out.rows[5].userId).to.equal(7)
		end)

		it("stops at both ends of the board", function()
			local top = Math.aroundMe(board(10), 1, 2)
			expect(top.firstRank).to.equal(1)
			expect(#top.rows).to.equal(3)
			local bottom = Math.aroundMe(board(10), 10, 2)
			expect(bottom.firstRank).to.equal(8)
			expect(#bottom.rows).to.equal(3)
			expect(bottom.rows[3].userId).to.equal(10)
		end)

		it("says plainly that an absent player has no rank", function()
			local out = Math.aroundMe(board(10), 99, 2)
			expect(out.rank).never.to.be.ok()
			expect(#out.rows).to.equal(0)
			expect(out.total).to.equal(10)
			expect(out.firstRank).to.equal(0)
		end)

		it("returns just the player with a window of zero", function()
			local out = Math.aroundMe(board(10), 4, 0)
			expect(#out.rows).to.equal(1)
			expect(out.rows[1].userId).to.equal(4)
			expect(out.firstRank).to.equal(4)
		end)
	end)

	describe("best", function()
		it("keeps the higher of two scores", function()
			expect(Math.best(5, 9)).to.equal(9)
			expect(Math.best(9, 5)).to.equal(9)
			expect(Math.best(5, 5)).to.equal(5)
		end)

		it("ignores a read that came back unusable, so a board place is never wiped", function()
			expect(Math.best(nil, 5)).to.equal(5)
			expect(Math.best(5, nil)).to.equal(5)
			expect(Math.best(5, "x")).to.equal(5)
			expect(Math.best(nil, nil)).never.to.be.ok()
		end)

		it("compares integers, because that is what the store holds", function()
			expect(Math.best(10.4, 10.6)).to.equal(10)
		end)
	end)

	describe("shouldPublish", function()
		it("publishes at or above the floor and not below it", function()
			expect(Math.shouldPublish(5, 1)).to.equal(true)
			expect(Math.shouldPublish(1, 1)).to.equal(true)
			expect(Math.shouldPublish(0, 1)).to.equal(false)
			expect(Math.shouldPublish(0, 0)).to.equal(true)
			expect(Math.shouldPublish(5)).to.equal(true)
			expect(Math.shouldPublish(0)).to.equal(false)
		end)

		it("refuses anything that is not a finite number", function()
			expect(Math.shouldPublish(nil, 1)).to.equal(false)
			expect(Math.shouldPublish("5", 1)).to.equal(false)
			expect(Math.shouldPublish(0 / 0, 1)).to.equal(false)
		end)

		it("agrees with the configured floor", function()
			expect(Math.shouldPublish(Config.MIN_SCORE, Config.MIN_SCORE)).to.equal(true)
			expect(Math.shouldPublish(Config.MIN_SCORE - 1, Config.MIN_SCORE)).to.equal(false)
		end)
	end)

	describe("writeDue", function()
		it("writes the first time and then only once the interval has passed", function()
			expect(Math.writeDue(nil, 100, 30)).to.equal(true)
			expect(Math.writeDue(100, 130, 30)).to.equal(true)
			expect(Math.writeDue(100, 129, 30)).to.equal(false)
			expect(Math.writeDue(100, 100, 30)).to.equal(false)
		end)

		it("treats a zero or negative interval as no throttle", function()
			expect(Math.writeDue(100, 100, 0)).to.equal(true)
			expect(Math.writeDue(100, 100, -5)).to.equal(true)
			expect(Math.writeDue(100, 100, nil)).to.equal(true)
		end)

		it("fails closed on a broken clock rather than writing on every sample", function()
			expect(Math.writeDue(100, 0 / 0, 30)).to.equal(false)
			expect(Math.writeDue(100, nil, 30)).to.equal(false)
		end)

		it("writes when the recorded last write is unusable", function()
			expect(Math.writeDue(0 / 0, 100, 30)).to.equal(true)
			expect(Math.writeDue("soon", 100, 30)).to.equal(true)
		end)
	end)
end
