--!nonstrict
-- CheckpointOrder.spec.lua — the pure ordering half of mechanics/checkpoint-respawn.
-- Nothing here needs a place: no checkpoints, no players, no spawning.

return function()
	local function findShared(name)
		-- Kit layout (`systems/<area>/<system>/tests/` beside `shared/`) and installed layout
		-- (`tests/` with `src/shared/` mapped into ReplicatedStorage) both have to work, or
		-- the spec only runs in one of the two places it is copied to.
		local shared = script.Parent.Parent:FindFirstChild("shared")
		local found = shared and shared:FindFirstChild(name)
		if found then
			return require(found)
		end
		return require(game:GetService("ReplicatedStorage"):WaitForChild(name, 10))
	end

	local CheckpointOrder = findShared("CheckpointOrder")
	local CheckpointConfig = findShared("CheckpointConfig")

	local ORDER = { start = 1, bridge = 2, gate = 3, keep = 4 }

	-- Three cases need a different config. Restore it through pcall so a failed assertion
	-- cannot leak the change into the cases that run after it.
	local function withConfig(key, value, fn)
		local original = CheckpointConfig[key]
		CheckpointConfig[key] = value
		local ok, err = pcall(fn)
		CheckpointConfig[key] = original
		if not ok then
			error(err, 0)
		end
	end

	describe("buildOrder", function()
		it("reads a list of { id, order } rows", function()
			local built = CheckpointOrder.buildOrder({
				{ id = "start", order = 1 },
				{ id = "keep", order = 4 },
			})
			expect(built.start).to.equal(1)
			expect(built.keep).to.equal(4)
		end)

		it("reads an id -> order map", function()
			local built = CheckpointOrder.buildOrder({ start = 1, bridge = 2 })
			expect(built.start).to.equal(1)
			expect(built.bridge).to.equal(2)
		end)

		it("uses array position as the order, deterministically", function()
			local built = CheckpointOrder.buildOrder({ "start", "bridge", "gate", "keep" })
			expect(built.start).to.equal(1)
			expect(built.bridge).to.equal(2)
			expect(built.gate).to.equal(3)
			expect(built.keep).to.equal(4)
		end)

		it("drops malformed rows instead of guessing an order for them", function()
			local built = CheckpointOrder.buildOrder({
				{ id = "start", order = 1 },
				{ id = "", order = 2 },
				{ id = "float", order = 2.5 },
				{ id = "zero", order = 0 },
				{ order = 3 },
				"plain",
			})
			expect(built.start).to.equal(1)
			expect(built[""]).never.to.be.ok()
			expect(built.float).never.to.be.ok()
			expect(built.zero).never.to.be.ok()
			expect(built.plain).to.equal(6)
		end)

		it("tolerates being handed nothing", function()
			expect(next(CheckpointOrder.buildOrder(nil))).never.to.be.ok()
			expect(next(CheckpointOrder.buildOrder("nope"))).never.to.be.ok()
		end)
	end)

	describe("audit", function()
		it("passes a well-formed order", function()
			expect(#CheckpointOrder.audit(ORDER)).to.equal(0)
		end)

		it("reports two checkpoints sharing one order", function()
			local problems = CheckpointOrder.audit({ a = 1, b = 1 })
			expect(#problems).to.equal(1)
			expect(problems[1]:find("both have order 1")).to.be.ok()
		end)

		it("reports an order that is not an integer >= 1", function()
			expect(#CheckpointOrder.audit({ a = 0 })).to.equal(1)
			expect(#CheckpointOrder.audit({ a = 1.5 })).to.equal(1)
			expect(#CheckpointOrder.audit({ a = "1" })).to.equal(1)
		end)
	end)

	describe("highestClaimed", function()
		it("picks the greatest order, not the last key pairs() visited", function()
			local id, n = CheckpointOrder.highestClaimed({ start = true, keep = true, bridge = true }, ORDER)
			expect(id).to.equal("keep")
			expect(n).to.equal(4)
		end)

		it("ignores a stale id that is no longer in the place", function()
			-- A save outliving a deleted checkpoint must not strand the player at a
			-- respawn point that does not exist any more.
			local id, n = CheckpointOrder.highestClaimed({ deleted = true, bridge = true }, ORDER)
			expect(id).to.equal("bridge")
			expect(n).to.equal(2)
		end)

		it("is nil when nothing valid is claimed", function()
			local id = CheckpointOrder.highestClaimed({}, ORDER)
			expect(id).never.to.be.ok()
			local staleOnly = CheckpointOrder.highestClaimed({ deleted = true }, ORDER)
			expect(staleOnly).never.to.be.ok()
		end)
	end)

	describe("canClaim / claim", function()
		it("allows the next checkpoint in order", function()
			local ok = CheckpointOrder.canClaim({ start = true }, ORDER, "bridge")
			expect(ok).to.equal(true)
		end)

		it("refuses to skip ahead, and says why", function()
			local ok, reason = CheckpointOrder.canClaim({ start = true }, ORDER, "keep")
			expect(ok).to.equal(false)
			expect(reason).to.equal("out_of_order")
		end)

		it("refuses an unknown checkpoint", function()
			local ok, reason = CheckpointOrder.canClaim({}, ORDER, "nowhere")
			expect(ok).to.equal(false)
			expect(reason).to.equal("unknown_checkpoint")
		end)

		it("refuses to claim the same checkpoint twice", function()
			local ok, reason = CheckpointOrder.canClaim({ start = true }, ORDER, "start")
			expect(ok).to.equal(false)
			expect(reason).to.equal("already_claimed")
		end)

		it("refuses a non-string id rather than indexing with it", function()
			expect((CheckpointOrder.canClaim({}, ORDER, nil))).to.equal(false)
			expect((CheckpointOrder.canClaim({}, ORDER, 42))).to.equal(false)
			expect((CheckpointOrder.canClaim({}, ORDER, ""))).to.equal(false)
		end)

		it("lets the first checkpoint be claimed with nothing claimed yet", function()
			local ok = CheckpointOrder.canClaim({}, ORDER, "start")
			expect(ok).to.equal(true)
		end)

		it("never mutates the caller's claimed table", function()
			local claimed = { start = true }
			local after, ok = CheckpointOrder.claim(claimed, ORDER, "bridge")
			expect(ok).to.equal(true)
			expect(after.bridge).to.equal(true)
			expect(claimed.bridge).never.to.be.ok()
			-- A refused claim must not half-apply either.
			local refused, okRefused = CheckpointOrder.claim(claimed, ORDER, "keep")
			expect(okRefused).to.equal(false)
			expect(refused.keep).never.to.be.ok()
			expect(claimed.keep).never.to.be.ok()
		end)

		it("copies the existing claims into the new table", function()
			local after = CheckpointOrder.claim({ start = true, bridge = true }, ORDER, "gate")
			expect(after.start).to.equal(true)
			expect(after.bridge).to.equal(true)
			expect(after.gate).to.equal(true)
		end)

		it("honours STRICT_ORDER = false for games that allow skipping", function()
			withConfig("STRICT_ORDER", false, function()
				local ok = CheckpointOrder.canClaim({}, ORDER, "keep")
				expect(ok).to.equal(true)
			end)
			expect(CheckpointConfig.STRICT_ORDER).to.equal(true)
		end)
	end)

	describe("respawnId", function()
		it("is the highest claimed checkpoint", function()
			expect(CheckpointOrder.respawnId({ start = true, gate = true }, ORDER)).to.equal("gate")
		end)

		it("falls back to the configured default before anything is claimed", function()
			withConfig("DEFAULT_CHECKPOINT_ID", "start", function()
				expect(CheckpointOrder.respawnId({}, ORDER)).to.equal("start")
			end)
		end)

		it("is nil with no claim and no default, so the game's own spawns are left alone", function()
			expect(CheckpointOrder.respawnId({}, ORDER)).never.to.be.ok()
		end)

		it("ignores a default that is not in the place", function()
			withConfig("DEFAULT_CHECKPOINT_ID", "demolished", function()
				expect(CheckpointOrder.respawnId({}, ORDER)).never.to.be.ok()
			end)
		end)
	end)

	describe("progress and nextId", function()
		it("counts only claims that still exist", function()
			local done, total, fraction = CheckpointOrder.progress({ start = true, deleted = true }, ORDER)
			expect(done).to.equal(1)
			expect(total).to.equal(4)
			expect(fraction).to.be.near(0.25, 1e-9)
		end)

		it("is 0/0 with no checkpoints, not a division by zero", function()
			local done, total, fraction = CheckpointOrder.progress({}, {})
			expect(done).to.equal(0)
			expect(total).to.equal(0)
			expect(fraction).to.equal(0)
		end)

		it("names the checkpoint a player is expected to reach next", function()
			local id, n = CheckpointOrder.nextId({ start = true }, ORDER)
			expect(id).to.equal("bridge")
			expect(n).to.equal(2)
			local firstId = CheckpointOrder.nextId({}, ORDER)
			expect(firstId).to.equal("start")
		end)

		it("has no next checkpoint once everything is claimed", function()
			local id = CheckpointOrder.nextId({ start = true, bridge = true, gate = true, keep = true }, ORDER)
			expect(id).never.to.be.ok()
		end)
	end)
end
