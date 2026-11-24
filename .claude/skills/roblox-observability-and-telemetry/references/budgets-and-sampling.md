# Budgets, sampling, and the two telemetry halves

A budget check has three parts people skip: the number, where the number came from, and how
many numbers there were. This file is about parts two and three, plus the bridge-side half
that most reports forget exists.

## Where FPS and memory actually come from

`get_studio_state` is the only surface here that returns them. The plugin reads:

| Field | Source in the plugin | Shape |
|---|---|---|
| `memoryMb` | `Stats:GetTotalMemoryUsageMb()` | number rounded to 0.1, **or `nil` if the `pcall` failed** |
| `physicsFps` | `workspace:GetRealPhysicsFPS()` | number rounded to 0.1, **or `nil` if the `pcall` failed** |

Read the fields that sit next to them in the same payload before quoting either number:

| Field | Why it changes the meaning of the sample |
|---|---|
| `dm` | which DataModel answered — `edit`, `server` or `client` |
| `isEdit` | `true` means the place as authored, not a running session |
| `isRunning` | `false` means physics is not stepping, so `physicsFps` is not a frame rate you can compare to a budget |
| `playtestActive` | whether a playtest was live when the sample was taken |
| `playerCount` | a one-player sample is not a concurrency sample |
| `peerId` | which Studio window answered, when several are open |

**`nil` is not `0`.** `memoryMb: nil` means the read failed; reporting it as "0 MB" or as a
pass is a false negative in the exact place a budget check is supposed to catch one. Say
"unmeasured" and say why.

## Sampling

One sample is noise; two is a trend you cannot trust; a cadence over the same scenario is a
measurement.

```lua
-- Client or server side: sample on a fixed interval, keep the spread, not the best value.
local samples = {}
local started = os.clock()

task.spawn(function()
	while os.clock() - started < 30 do
		local stats = game:GetService("Stats")
		table.insert(samples, {
			t = os.clock() - started,
			memMb = stats:GetTotalMemoryUsageMb(),
		})
		task.wait(1)
	end
	local total, peak = 0, 0
	for _, s in ipairs(samples) do
		total += s.memMb
		peak = math.max(peak, s.memMb)
	end
	print(string.format("[Telemetry] %d samples over %.0fs: mean %.1fMB peak %.1fMB",
		#samples, os.clock() - started, total / math.max(1, #samples), peak))
end)
```

Rules:

- **Warm up first.** The first seconds of a session include load and streaming; a budget
  measured there is a loading measurement.
- **Same scenario, same order.** If the run before the change and the run after it did
  different things, the comparison is meaningless.
- **Report mean and peak, and the sample count.** Peak is what breaks a device; mean is what
  hides it. A report with only one of the two is half a measurement.
- **Through the bridge, sample by calling `get_studio_state` repeatedly** and record the
  timestamps yourself. The bridge does not keep a history of those values, so if you do not
  write them down there is no series to analyse later.

## The bridge half

Everything above describes the game. The connector has its own telemetry, and it is the half
that explains a "slow tool" that was never a slow script.

`GET /api/metrics` renders Prometheus text — `# TYPE` lines, counters, gauges, and histograms
with `_bucket{le="..."}`, `_sum`, `_count` and quantile series:

| Series | What it answers |
|---|---|
| `bridge_job_duration_ms{type,dm,quantile="0.95"}` | the p95 wall time for one job kind — the whole round trip, queueing included |
| `bridge_jobs_total{type,dm,outcome}` | how many jobs of a kind finished, and how many of them errored |
| `bridge_queue_depth`, `bridge_queue_paused` | whether the work was waiting rather than running |
| `bridge_peers_online`, `bridge_studio_connected` | liveness — the first thing to check when numbers stop arriving |
| `bridge_jobs_reaped_total`, `bridge_jobs_crash_loop_total` | orphans recovered, and peers that died holding work |
| `bridge_auth_rejections_total`, `bridge_origin_rejections_total`, `bridge_http_rejected_total` | refusals, which look like "the tool is broken" from the caller's side |
| `bridge_confirmations_pending` | a human is being waited on — a stalled call may be a gate, not a hang |

Also available: `studio://jobs` (queue depth, pause state, live peers, recent job summaries,
p50/p95 wall time per tool), `/api/events` (SSE, or `?once=1` for a snapshot), and
`/api/audit?verify=1` — the hash-chained log, which is the only timeline you can prove was
not edited afterwards.

**Use the two halves together.** A 12-second `execute_luau` is either a slow script
(Studio-side: your own `os.clock()` around the work) or a job that sat in the queue
(bridge-side: `bridge_queue_depth`, `bridge_queue_paused`, the `duration_ms` on the job
record). Guessing which one is how a performance investigation goes down the wrong path for
an afternoon.

## Crash detection

There is no API here that reports a client crash, because the process that would report it is
gone. What exists is inference on the bridge side, and it is reliable enough to page on:

1. A peer claims a job. The peer then stops heartbeating.
2. After the grace period (the reaper only treats a peer as gone once it has stopped
   heartbeating for 20 s, so allow a ~30 s window) the reaper requeues the orphan: `status`
   back to `queued`, `attempts` incremented, `bridge_jobs_reaped_total` moving.
3. If the same peer keeps dying on the same work, the job is failed as `peer_crash_loop`, a
   `job.crash_loop` event is emitted, an audit row is written with `actor="reaper"`, the job's
   note gains `[crash loop: peer <id> died N×]`, and `bridge_jobs_crash_loop_total` increments.

So the evidence set for "Studio crashed while doing X" is: a `peer_crash_loop` job, its audit
rows, the counter, and the moment `bridge_peers_online` fell to 0. Record all four. And if the
crashing job was a `run_luau` or `execute_luau` probe you wrote, say so — the counter cannot
distinguish your experiment from a defect, and a report that hides that turns a self-inflicted
crash into a mystery.

## What a budget check must never claim

- That a device held the budget. Studio on a workstation is not a phone; say which machine
  produced each number.
- That a fix worked, from one sample either side.
- That `perf_audit`'s `mobileVerdict` is a device verdict. It is a threshold on a part count
  read from a bounded `inspect` (`depth: 20`, `maxChildren: 500`) of the **edit** DataModel,
  and its `triangleCount` and `textureMemoryEstimateMB` are always `null`.
- That anything was observed at all when the peer was absent. No peer means `no_peer`, and the
  honest report is a list of commands for an operator to run.
