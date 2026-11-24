# Logging patterns that survive `get_console_output`

The output window is a shared, bounded, per-session buffer. These patterns exist so that a
log line written by a game can still be *found* later through the bridge's LogService reader.

## A prefix per system

`get_console_output {search: "..."}` is a case-insensitive substring filter over the whole
line, so one stable prefix per system is what makes a 2,000-entry buffer searchable.

```lua
--!nonstrict
local TAG = "[Checkpoint]"

local function log(fmt, ...)
	-- string.format keeps the shape of the line predictable; concatenation drifts.
	print(TAG .. " " .. string.format(fmt, ...))
end

log("claim accepted id=%d player=%s", index, player.Name)
log("claim rejected reason=%s id=%d", reason, tostring(index))
```

Rules that make the prefix useful:

- One tag per system, and the same tag in every file of that system. Two tags for one system
  means two searches and half the story each time.
- `reason=<string>` for every rejection path. The kit's systems already use enumerable
  reasons (`bad_id`, `unknown_checkpoint`, `already_claimed`, `out_of_order`, `cooldown`;
  `unknown_setting`, `not_a_number`, `not_a_boolean`, `not_a_string`, `not_an_option`,
  `unsupported_kind`) — log the reason you returned, not a paraphrase of it.
- Log the **inputs that decided the branch**, because that is the part you cannot recover
  afterwards. `out_of_order id=7 lastClaimed=9` tells you everything; `invalid` tells you
  nothing you did not already know.

## Severity, honestly

| Use | For | Effect on the reader |
|---|---|---|
| `print` | normal-path decisions you may need once | `level: "Output"` — filtered out when you ask for problems |
| `warn` | degraded but still working (fallback engaged, retry succeeded, memory mode) | `level: "Warning"` — the useful middle band |
| `error` | something a player would notice, or a state you cannot recover from | `level: "Error"` — must stay rare enough to mean something |

The kit's convention is **warn, don't error, when degrading**: `DataStoreSafe` warns once and
switches to memory rather than erroring on every call, and `createOrderedStore` degrades with a
single warning when the DataStore is unavailable. Follow that — an `error` per call in a retry
loop produces thousands of identical lines and buries the one that matters.

```lua
local warned = false
local function degradeOnce(message)
	if not warned then
		warned = true
		warn("[Economy] " .. message .. " (further occurrences suppressed)")
	end
end
```

## Timing without a profiler

`os.clock()` is what this repository uses for cooldowns and elapsed measurements (31 files),
and it is the right tool for "how long did this call take" inside one script:

```lua
local started = os.clock()
local ok, result = pcall(function()
	return store:UpdateAsync(key, transform)
end)
local elapsedMs = math.floor((os.clock() - started) * 1000)

if not ok then
	warn(string.format("[Economy] UpdateAsync failed after %dms: %s", elapsedMs, tostring(result)))
else
	print(string.format("[Economy] UpdateAsync ok in %dms", elapsedMs))
end
```

Two limits to keep in the report:

- This measures **your call**, in **one** DataModel. It is not a frame time, not a server
  tick, and not comparable to the bridge's `bridge_job_duration_ms`, which measures the whole
  round trip including queueing.
- `pcall` around the call is what makes the failure path loggable at all. An unprotected call
  that errors produces a traceback and no elapsed time.

## What you cannot recover from the buffer

- **Anything from a crashed session.** LogService history belongs to the process that wrote
  it. A crashed client took its lines with it; the bridge's reaper is the only witness.
- **Anything before the buffer's own retention.** The reader is bounded by `limit` (max 2000)
  and by what LogService still holds, so a long playtest read from the top can silently start
  partway in. Use `sinceIndex` from the first read onward and say which range you covered.
- **The other DataModel.** A `print` from a playtest *client* is not in the *server* log. Pass
  `dm: "client"` or `dm: "server"` explicitly instead of relying on `auto`, and note in the
  report which one each block of lines came from.
- **Bridge chatter, unless you asked for it.** `source` defaults to `"game"`, which excludes
  ArenaBridge's own diagnostics. When the connector is the suspect, re-read with
  `source: "bridge"` or `"all"` — do not conclude "nothing was logged" from a game-filtered
  read.
