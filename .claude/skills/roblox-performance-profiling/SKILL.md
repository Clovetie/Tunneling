---
name: roblox-performance-profiling
description: Measure and improve Roblox performance using repeatable Studio profiling, baseline comparisons, scene risk audits, and real device validation for CPU, rendering, memory and network bottlenecks.
---

# Roblox performance profiling

Use when players report stutter, low frame rate, memory growth, long loading or network delays. Combine with `roblox-optimization-security` and `roblox-playtest-qa`; optimize measured bottlenecks, not guesses.

## Investigation workflow
1. With permission, inspect `get_studio_state`, `survey_place(includeSource=false)` and relevant scripts. Record the place revision, Studio version, scene, player count, device, graphics settings, test duration and reproducible actions. Capture an unmodified baseline; do not change production settings or live data to create a test.
2. Run `perf_audit` for scene-count and streaming **risk estimates**; these are not measured frame times, memory, bandwidth, or proof of a bottleneck. Use `get_console_output` for existing errors. No peer means live checks are unavailable: report `no_peer`, do not claim success.
3. On an actual Studio session or appropriate test devices, use Roblox's available profiling tools to compare CPU, rendering, memory and network behavior in the same representative scenario, after warm-up and over repeated runs. Include busy scenes and transitions. Record observed values, variability and profiler/tool versions rather than inventing budgets or interpreting heuristic counts as measurements.
4. Identify one dominant cost and make the smallest reversible change consistent with server authority, accessibility and gameplay. Avoid blanket disabling collision, reducing visual quality, changing streaming or removing assets without understanding effects and getting authorization. Inspect cache/lifecycle behavior and cleanup, and use `luau_check` before script writes; persist approved edits in `dm="edit"`.
5. Repeat the same scenario after the change and compare measurements with the baseline, including regressions in appearance and correctness. Use `run_playtest_scenario` and console checks when appropriate. If user load or network traffic matters, use controlled multi-client tests; a single mock or Play Solo session cannot validate concurrency, server scale or real network conditions.

## Verification and reporting
Document baseline, change, measured before/after, device and player count, limitations, and rollback path. Test representative physical devices where available; Studio emulation and offline audits do not prove physical-device performance. Report unsupported profilers, missing peers, untested devices and scenarios explicitly; do not claim speedups without comparable measurements.
