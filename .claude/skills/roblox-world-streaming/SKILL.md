---
name: roblox-world-streaming
description: Design and debug streaming-aware Roblox worlds, client instance lifetimes, distant traversal, replication focus, and multi-client streaming tests.
---

# Large worlds and instance streaming

Use for streamed-out targets, distant interactions, teleport/loading transitions or memory problems in large places. Combine with `roblox-multiplayer-interactions`, `roblox-performance-profiling` and `roblox-playtest-qa`. This skill is guidance, not a change to Studio or a persistent agent modification.

## Inspect before changing
1. Get permission before edits or production changes. Use `diagnose_connection`, `get_studio_state`, `survey_place(includeSource=false)` and targeted reads to identify the place, relevant scripts, current `Workspace` streaming settings, models, remotes, spawn and traversal routes. Record the existing configuration and a rollback plan. No peer means inspection of live Studio is unavailable; work from supplied material and report the gap.
2. Map each important client feature to its server authority, spatial targets and state needed when those targets are absent. A client may not have a world instance yet or may lose it later; a remote carrying an instance or path does not guarantee that it is currently resolvable on the receiver. Use stable identifiers and server-owned state where appropriate; do not treat a stale client reference or property as authority for rewards, movement or proximity.
3. Inspect the project's lifecycle patterns. Handle streamed-in and removed objects, reconnect/rebind safely and clean up listeners so repeated streaming does not duplicate effects. Check for missing or stale targets and provide bounded waiting, timeout/fallback and recovery; avoid an unbounded `WaitForChild` on a spatial object as a critical loading gate. Stream-out is not necessarily destruction, and local-only changes may be lost on stream-out.

## Design and safety
- Choose model boundaries around actual behavior. Roblox documents `Atomic` initial replication for a model and its initial descendants, not for descendants added later. Persistent models remain loaded and use memory; do not make a whole world persistent merely to hide lifecycle bugs. Review the current Creator Docs and measure the experience before changing streaming properties or model modes.
- For authorized long-distance movement, validate the destination, player eligibility and request on the server. Consider the documented server-side `Player:RequestStreamAroundAsync()` for anticipated destinations or limited replication foci when justified; handle delay/failure, departure and cleanup, and test readiness rather than assuming a prefetch makes every object immediately available. Do not let clients choose arbitrary destinations or unbounded replication foci.
- Loading UI must remain responsive and recover when an object cannot arrive. Keep player-visible state and essential communication available without requiring the whole map to replicate first. Treat `perf_audit` as scene-risk estimation, not proof of streaming, frame time or device performance.

## Verification
See `references/streaming-test-matrix.md` for a scenario matrix. Run `luau_check` and inspect its coverage before authorized edits; preserve rollback. In an isolated test place with streaming enabled, exercise join, traversal out and back, delayed or missing targets, respawn, long-distance movement and repeated rebinds. Verify server state and each client's display/interaction independently, and inspect console output. Vary settings only in the approved test environment, document them and restore them afterwards. Use multiple real Studio clients for independent streaming and concurrency, and representative physical devices/network conditions for memory and responsiveness. Offline tools, mocks, a single client and screenshots cannot establish these results. Report untested conditions and do not claim a streaming fix without observed behavior.
