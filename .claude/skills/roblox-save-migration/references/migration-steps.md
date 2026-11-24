# Writing migration steps, and the spec that guards them

## The engine's contract

`SaveMigrations.Migrate(data, targetVersion, steps?)` returns a status and either data or
`nil`. `steps` defaults to `SaveMigrations.Steps`, which is `DataMigrations` — the game-owned
table. Passing `steps` explicitly is what makes the engine testable without touching the real
step table.

| Status | Returned when | `DataService` does |
|---|---|---|
| `current` | `data.dataVersion == targetVersion` | loads it, then `Reconcile()` |
| `migrated` | every step from `version` to `targetVersion - 1` ran | replaces `profile.Data` with the deep copy, then `Reconcile()` |
| `reset` | no numeric `dataVersion`, NaN, or below `BASELINE` | `profile.Data = newData()` — a fresh template row |
| `newer` | `dataVersion > targetVersion` | ends the session, save untouched, kicks with the "newer version" message, **no warn** |
| `missingStep` | `steps[version]` is not a function | ends the session, save untouched, kicks, warns |
| `failed` | a step raised | ends the session, save untouched, kicks, warns |

Three details that are easy to get wrong:

- The walk is `while version < targetVersion`, taking `steps[version]` and then incrementing —
  so **`steps` is keyed by the version you are migrating *from***. `[1]` upgrades 1 → 2.
- `working.dataVersion = version` is stamped **after** each successful step, so a step never
  has to set the version itself, and a step that errors leaves the version at the last good one
  (which is then discarded, because `failed` returns `nil`).
- Steps are called through `pcall`, so an error becomes `failed` rather than propagating. That
  is the guarantee that a broken step cannot corrupt a row — and the reason a step must not
  rely on raising to signal "this row is special". Return normally; let the status do the work.

## Step shape

A step mutates its argument in place and returns nothing. It is pure: no `game`, no services,
no yields, no `print` in the normal path.

```lua
-- DataMigrations.lua
return {
	-- v1 -> v2: `cash` became `currency`, and inventory items gained a `uid`.
	[1] = function(data)
		if data.currency == nil and type(data.cash) == "number" then
			data.currency = data.cash
		end
		data.cash = nil

		local inventory = data.inventory
		if type(inventory) == "table" then
			for index, item in ipairs(inventory) do
				if type(item) == "table" and item.uid == nil then
					item.uid = string.format("%d-%d", os.time(), index)
				end
			end
		end
	end,

	-- v2 -> v3: only adds `settings`. Reconcile would fill it, but the version
	-- bump still needs a step, or every v2 save becomes `missingStep`.
	[2] = function(_data)
	end,
}
```

Rules the example is following:

- **Idempotent.** `if data.currency == nil and type(data.cash) == "number"` — running it twice
  is a no-op the second time, because the source field is gone. A step written as
  `data.currency = data.cash` unconditionally would erase a real balance on a re-run, and
  re-runs happen (failed cloud write, second server, rollback then re-rollout).
- **Safe on a fresh default row.** After a `reset`, the row is a clean template. Every step
  from `BASELINE` upward still runs on a first-ever join if you structure it that way, so a
  step must not assume the old field exists.
- **Tolerates a wrong type instead of raising.** `type(...) == "table"` guards. A `failed`
  status kicks the player, so a defensive check is worth more than a clean traceback.
- **Preserves unknown fields.** Only the fields being changed are touched. Never rebuild the
  row from the template: that erases every field written by a system you did not read.
- **No `os.time()`-style nondeterminism unless it is idempotent.** The `uid` above is generated
  only when missing, so a re-run keeps the first value.

## Rename, retype, split — the three that go wrong

| Change | What the step must do | Trap |
|---|---|---|
| Rename (`cash` → `currency`) | copy only when the destination is absent, then clear the source | copying unconditionally overwrites a newer value on a re-run |
| Retype (string → number, or a scalar → a table) | convert with an explicit fallback for values you cannot convert, and keep the old value somewhere if losing it is unacceptable | `tonumber("1,200")` is `nil`; a converted-away original cannot be recovered by a rollback |
| Split into two stores | write both, make one authoritative while the other catches up, and plan the window where they disagree | an ordered store's values must be integers, so display data cannot move into it at all — see `datastoresafe-gaps.md` |

For a retype or a destructive drop, the backup has to exist **before** the step runs, and
writing one needs explicit authorisation. A step cannot undo a change it already persisted.

## The spec the README promises

`systems/core/data/README.md` says `tests/SaveMigrations.spec.lua` fails until every version
from `MIGRATION_BASELINE` to `DATA_VERSION - 1` has a step. **That file is not in the kit** —
the system has no `tests/` directory — so the guard does not currently run. This is the shape
it should have:

```lua
--!nonstrict
-- SaveMigrations.spec.lua — the engine's status table, and the "every version has a step"
-- guard. Pure: no DataModel, no DataStore, no yields.

return function()
	local function findShared(name)
		-- Works in the kit layout (`tests/` beside `shared/`) and in an installed game
		-- (`shared/` mapped into ReplicatedStorage). Both, or the spec only runs in one
		-- of the two places it is copied to.
		local shared = script.Parent.Parent:FindFirstChild("shared")
		local found = shared and shared:FindFirstChild(name)
		if found then
			return require(found)
		end
		return require(game:GetService("ReplicatedStorage"):WaitForChild(name, 10))
	end

	local Config = findShared("DataConfig")
	local Steps = findShared("DataMigrations")
	local SaveMigrations = findShared("SaveMigrations")

	describe("SaveMigrations.Migrate", function()
		it("returns current when the version already matches", function()
			local data = { dataVersion = Config.DATA_VERSION, currency = 5 }
			local status, out = SaveMigrations.Migrate(data, Config.DATA_VERSION, {})
			expect(status).to.equal("current")
			expect(out.currency).to.equal(5)
		end)

		it("never modifies its input", function()
			local data = { dataVersion = 1, cash = 5 }
			SaveMigrations.Migrate(data, 2, { [1] = function(d) d.currency = d.cash end })
			expect(data.currency).to.equal(nil)
		end)

		it("applies every step in order and stamps each version", function()
			local steps = {
				[1] = function(d) d.a = (d.a or 0) + 1 end,
				[2] = function(d) d.b = (d.b or 0) + 10 end,
			}
			local status, out = SaveMigrations.Migrate({ dataVersion = 1 }, 3, steps)
			expect(status).to.equal("migrated")
			expect(out.a).to.equal(1)
			expect(out.b).to.equal(10)
			expect(out.dataVersion).to.equal(3)
		end)

		it("resets a row with no version, a NaN version, or one below the baseline", function()
			for _, data in ipairs({ {}, { dataVersion = 0 / 0 }, { dataVersion = Config.MIGRATION_BASELINE - 1 } }) do
				expect(SaveMigrations.Migrate(data, Config.DATA_VERSION, {})).to.equal("reset")
			end
		end)

		it("refuses a save from a newer build, and returns no data", function()
			local status, out = SaveMigrations.Migrate({ dataVersion = Config.DATA_VERSION + 1 }, Config.DATA_VERSION, {})
			expect(status).to.equal("newer")
			expect(out).to.equal(nil)
		end)

		it("refuses when a step is missing or raises", function()
			expect(SaveMigrations.Migrate({ dataVersion = 1 }, 2, {})).to.equal("missingStep")
			expect(SaveMigrations.Migrate({ dataVersion = 1 }, 2, {
				[1] = function() error("boom") end,
			})).to.equal("failed")
		end)
	end)

	describe("DataMigrations", function()
		it("has a step for every version from the baseline up to DATA_VERSION - 1", function()
			-- This is the guard the README promises: bump DATA_VERSION without adding the
			-- step and this fails before any save is at risk.
			for version = Config.MIGRATION_BASELINE, Config.DATA_VERSION - 1 do
				expect(type(Steps[version])).to.equal("function")
			end
		end)
	end)
end
```

Notes on running it:

- `expect(x).to.equal({ ... })` compares table **references** in TestEZ, so compare field by
  field, as above. `to.equal(nil)` and `to.equal("string")` are fine.
- `run_testez_suite` builds a scratch container holding **one ModuleScript per `*.spec.lua`
  plus a runner** — it does not copy the modules under test. So inside the container
  `script.Parent.Parent` is not the system's `shared/` directory, and the
  `ReplicatedStorage:WaitForChild(name, 10)` branch of `findShared` is the one that resolves.
  Install `DataConfig`, `DataMigrations` and `SaveMigrations` into the place's
  ReplicatedStorage first, or the spec errors on require rather than failing an assertion.
- Read `runner` in the report before believing a green result: `bundled-shim` means the place
  has no TestEZ and the run used the subset shim, which is not the same verdict. Check
  `shimGaps` too — a construct the shim lacks means those assertions never ran.
- Use the `testez_first_workflow` prompt for the whole loop: spec first, static-check with
  `luau_check` and `tools/check_globals.sh`, then the feature, then execute.
