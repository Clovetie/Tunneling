---
name: roblox-observability-and-telemetry
description: Instrument a Roblox game for observability — client and server logging, FPS and memory budget checks, crash detection, and telemetry — and know exactly what Studio's output window can and cannot tell you.
---

# Roblox observability and telemetry

Use when you have to *see* what a game did: diagnose a report you cannot reproduce, prove a budget is being held, or find out why a session ended. This skill is about instrumentation and evidence. Turning a measurement into a fix is `roblox-performance-profiling`; auditing scene structure is `perf_audit`; securing the data you collect is `roblox-optimization-security`.

There are **two independent halves** of observability here, and a confusing report usually comes from reading only one:

- **Studio-side** — what the game did: logs via `get_console_output`, memory and physics FPS via `get_studio_state`.
- **Bridge-side** — what the connector did: `/api/metrics`, `studio://jobs`, `/api/events`, `/api/audit?verify=1`.

A tool call that "took 12 seconds" can be a slow script *or* a job queued behind a paused queue (`bridge_queue_paused`, `bridge_queue_depth`). Quote both halves or quote neither.

## Logging discipline
1. Log with a stable prefix per system (`[Checkpoint]`, `[Economy]`) so `get_console_output`'s `search` filter can isolate one system, and use severity honestly: `warn` for degraded-but-working, `error` only for something a player would notice. A game whose normal path emits errors has no error signal left.
2. Log the **decision and its inputs**, not the outcome alone: `claim rejected reason=out_of_order id=7 lastClaimed=9` is actionable; `invalid claim` is not. Reason strings from the kit (`bad_id`, `unknown_checkpoint`, `already_claimed`, `out_of_order`, `cooldown`) exist precisely so a log line names a branch.
3. Never log a secret, token, full user payload, or anything you would not want in an audit trail that outlives the session. The bridge records log traffic and serves it to any attached dashboard.
4. Read logs incrementally: `get_console_output {sinceIndex: <previous nextIndex>}` returns only new entries, so a long playtest does not have to be re-read from the top. Filter `source: "game"` (the default) to keep ArenaBridge's own diagnostics out of the picture; use `"bridge"` when the connector itself is the suspect.

## FPS and memory budget checks
5. `get_studio_state` returns `memoryMb` (from `Stats:GetTotalMemoryUsageMb()`) and `physicsFps` (from `workspace:GetRealPhysicsFPS()`), each rounded to 0.1. **Both are `nil` when the read failed** — `nil` means unmeasured, never zero, and reporting `0 FPS` because a field was absent is the single most common way this skill gets misused.
6. The same payload carries `dm`, `isEdit`, `isRunning` and `playtestActive`. Check them before quoting a number: a sample from an `edit` peer is the place as authored, not a running session, and `physicsFps` only means something while physics is stepping.
7. One sample is noise. Budgets are held or broken over time, so sample on a cadence through the same scenario, after warm-up, and report the spread — not the best value you happened to catch.
8. Budget checks need a stated budget. If nobody wrote one down, say so and measure anyway; do not invent a target and then report a pass against it.

## Crash reporting
9. **Studio's output window cannot report a crash**: the process that would have logged it is the one that died. The only crash signal available here is bridge-side, and it is a real one — a peer that claims a job and never returns leaves the job orphaned; the reaper requeues it, and after repeated deaths fails it as `peer_crash_loop`, emits a `job.crash_loop` event, writes an audit row with `actor="reaper"`, appends `[crash loop: peer <id> died N×]` to the job note, and increments `bridge_jobs_crash_loop_total`.
10. Expect roughly a 30-second window: the reaper only treats a peer as gone after it stops heartbeating (20 s grace). `bridge_peers_online` and `bridge_studio_connected` dropping to 0 is the liveness signal; `/api/audit?verify=1` is the timeline.
11. A hard crash inside an `execute_luau` probe (submitted as a `run_luau` job — a job *kind*, not a tool name, and the one kind the confirm gate covers that no tool shares its name with) is a *self-inflicted* one. If you are probing for a crash, say so in the report, because the crash-loop counter cannot tell your experiment from a defect.

## What `perf_audit` will not tell you
12. `perf_audit` is a structural estimate computed from one `inspect` job bounded by `depth: 20` and `maxChildren: 500`. It always returns `triangleCount: null` and `textureMemoryEstimateMB: null` — its own `warnings` field says the plugin does not expose that metadata. Quoting triangles or texture memory from it is inventing data.
13. `uniqueMeshCount` counts `MeshPart` **instances**, not distinct mesh assets, so 500 copies of one mesh reads as 500. `mobileVerdict` is a threshold on the bounded part count (`low` < 1000, `moderate` ≥ 1000, `elevated` ≥ 3000 parts or ≥ 1500 meshes, `high` ≥ 8000), so on a deep or very wide place a `low` verdict can be an artifact of the 500-child cap rather than a measured calm. Cross-check against `survey_place` counts before believing a low verdict.
14. It audits the **edit** DataModel: the authored place, not what a streaming client actually holds in memory. With `StreamingEnabled` on, those two numbers differ by design.

## Verification
State which half of the evidence you actually have, and mark the rest unverified with the command that would settle it.

- **Provable from this repository alone:** nothing about a running game. There is no Luau runtime here — kit Lua can be parsed, type-checked and global-checked (`luau_check`, `tools/check_globals.sh`) and pure arithmetic replayed outside Studio, but no log line, FPS sample, memory figure or crash event in this repo was produced by a real client. A mock Studio peer answers the same calls as a real one and proves the *plumbing*, never the *game*.
- **Needs an attached Studio peer:** `get_console_output`, `get_studio_state` memory/FPS, `perf_audit`, `survey_place`. No peer means report `no_peer` and stop; a `nil` or absent field is not a zero and not a pass.
- **Needs an operator, with the exact command:** live FPS/memory over time (`get_studio_state` sampled during a playtest, `dm="server"` and `dm="client"`, with `isRunning`/`playtestActive` true and recorded); real device behaviour, because Studio emulation is not a phone; the crash path (`bash roblox-bridge/tools/...` orphan-requeue drill in OPS §10.2, or stop Studio mid-job and watch `bridge_jobs_crash_loop_total` and `job.crash_loop`); and anything about a *player's* crash, which no API here exposes at all.
- **Report honestly:** the sample cadence and count, the DataModel each number came from, the `sinceIndex` range each log read covered, any field that was `nil`, and every claim you could not evidence. An observability report that cannot say what it did not see is not observability.
