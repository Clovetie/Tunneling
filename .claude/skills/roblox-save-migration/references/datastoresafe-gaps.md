# `DataStoreSafe` — the gaps that defeat a migration

`roblox-kit/modules/DataStoreSafe.lua` is a session-cached profile manager. It is **not** a
migration system, and it has no version field of its own. If your game uses it (rather than
`systems/core/data`, which has a real engine), read this before bumping anything: three of its
behaviours will silently defeat a migration that looks correct.

Read the module before believing any summary of it, including this one.

## What it actually offers

```lua
local DataStoreSafe = require(script.Parent.DataStoreSafe)

local profiles = DataStoreSafe.createProfileStore("PlayerData", {
	currency = 0,
	inventory = {},
}, {
	{ key = "currency", name = "Coins" },
})

local data = profiles:Get(player)            -- nil until loaded
profiles:Update(player, function(d)          -- mutates in memory, does NOT save
	d.currency += 10
end)
local saved = profiles:Save(player)          -- see gap 2
```

- Keys are `"Player_" .. tostring(player.UserId)`.
- `loadPlayer` retries a failed `GetAsync` three times with `task.wait(0.5 * attempt)`.
- `Update` runs your mutator on the cached table, re-syncs leaderstats, and returns the table
  (`nil` if the profile is not loaded). It does not persist; `Save` does.
- `PlayerRemoving` saves then forgets the profile, and `game:BindToClose` saves every profile.
  Neither callback is guaranteed to finish — a shutdown save is a best effort, not a promise.
- `leaderstatKeys` entries are `{ key = ..., name = ... }` and are written as `IntValue`s with
  `math.floor(tonumber(data[key]) or 0)`, so a fractional or non-numeric field displays as 0.

## Gap 1 — `reconcile` runs before you can see the row

```lua
local function reconcile(target, template)
	for k, v in pairs(template) do
		if target[k] == nil then
			target[k] = type(v) == "table" and table.clone(v) or v
		elseif type(v) == "table" and type(target[k]) == "table" then
			reconcile(target[k], v)
		end
	end
	return target
end
```

It iterates the **template**, so two things follow:

- Fields present in the loaded row but absent from the template **survive**. Good, and the
  reason you should never "clean up" a row by rebuilding it from the template.
- Fields absent from the loaded row are **filled from the template before `Get` ever returns
  it**. So if `defaultProfile` contains `dataVersion = 3`, an old row with no version arrives
  stamped `3`, and `if data.dataVersion < 3 then migrate() end` never fires. The migration
  silently never runs, and every old row looks current.

This is exactly why `systems/core/data` migrates **before** `Reconcile()` and says so in a
comment. `DataStoreSafe` gives you no hook at that point, so you have two options:

1. **Keep the version field out of `defaultProfile`.** Then reconcile cannot stamp it, a
   missing version means "at most v1", and you run every step — each of which must be a no-op
   on a fresh default row (which is why steps have to be idempotent and default-safe anyway).
2. **Read the raw row yourself** with `GetAsync` before the session starts. Now there are two
   readers of one key and no session lock between them, so this needs care and a reason.

Prefer 1, and prefer migrating to `systems/core/data` if the game is going to keep changing
shape.

## Gap 2 — `Save()` returns `true` after three failed writes

```lua
if cloudAvailable and store then
	for attempt = 1, 3 do
		local ok = pcall(function()
			store:SetAsync(key, data)
		end)
		if ok then
			return true
		end
		task.wait(0.5 * attempt)
	end
end
return true    -- <- reached when every attempt failed
```

A `true` from `Save` is **not** evidence the data was persisted. So:

- Do not stamp `dataVersion` because `Save` returned true. Stamp it inside the migration, only
  after the transform itself succeeded, and treat persistence as unproven until a reload shows
  the new row.
- Do not report "migration applied to N players" from `Save` return values. There is no counter
  here that means what you want it to mean.

## Gap 3 — the memory copy is written first, so `Get` lies after a failed save

```lua
memoryFallback[key] = table.clone(data)   -- before any cloud attempt
```

After a failed cloud write, `Get` returns the **new** data while the store still holds the
**old** row. The process looks healthy; the next session — or the next server — loads the old
row and migrates it again. That is not a bug you can fix from outside the module, and it is
the reason guarantee 6 (idempotent, and safe on a fresh row) is mandatory rather than tidy.

Related: in Studio, the first failed `GetAsync` sets `cloudAvailable = false` for the rest of
the process and switches to memory immediately. One transient failure therefore converts the
whole session to memory-only, and a migration test that passes in Studio may never have met a
DataStore. Say which one you tested against.

## Ordered stores

`DataStoreSafe.createOrderedStore(storeName, options)` exists because OrderedDataStore values
must be **integers** and display metadata does not belong in them: it keeps scores in the
ordered store and names in a plain companion store, throttles writes per key, and degrades to
memory with a single warning when the DataStore is unavailable instead of erroring on every
call.

Migrating a leaderboard therefore means migrating **two stores that must stay consistent**, and
a schema change that moves display data into the ordered value cannot be done at all. If the
companion name store and the ordered store disagree, the rank list renders names against the
wrong scores — so a migration here needs a plan for which store is authoritative while the
other catches up.
