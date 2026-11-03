# Changelog

All notable changes for the Roblox Studio bridge are documented here.

## 5.0.0 — ops platform: supervised tunnel, agent bus, telemetry, scoped auth

> Implemented and unit-tested against the stateful mock Studio plus a fake tunnel
> binary. Live Roblox Studio acceptance for v5 (real peer, real public tunnel,
> kill-switch drill) is still pending — see `docs/OPS.md` §10 and
> `handoff/GEMINI-4-ARGON.md` tasks G7/G8.

### Added
- **Tunnel supervisor (`tunnel_manager.py`)**: auto-detects cloudflared/ngrok, scrapes the public URL, health-probes `<url>/api/health`, restarts with exponential backoff after exits or repeated probe failures, supports named tunnels (`--tunnel-token`) and externally managed URLs (`--public-url`), reports LAN URLs, and exposes a state machine (`stopped/starting/online/degraded/reconnecting/unavailable/error`) through `GET /api/tunnel` and the Ops tab.
- **Agent Bus (`bus_core.py`, MCP tool `agent_bus`)**: presence registry with heartbeats, direct/broadcast mailbox with cursor inbox and read receipts, and a lease-based task board (priorities, tags, dependencies, artifact handoff, auto-expiring leases) plus a markdown handoff export. HTTP `/api/bus`, CLI `arena_studio.py bus …`, dashboard roster/task UI.
- **Scoped tokens (`auth_core.py`)**: hashed `read`/`write`/`admin` tokens with expiry, revocation and per-token token-bucket rate limits (`429` + `Retry-After`). `bridge.token` remains the admin owner credential. Read tokens cannot call mutating MCP tools (`insufficient_scope`). Admin endpoints `GET/POST /api/tokens`.
- **Resilient job queue**: idempotency keys / `Idempotency-Key`, 0–9 priorities with priority-then-FIFO selection, bounded retries on transient errors, per-job timeouts, and a background reaper that requeues jobs whose claiming peer disappeared.
- **Telemetry (`telemetry.py`)**: counters/gauges/latency histograms with Prometheus exposition (`/api/metrics?format=prometheus`), a sequence-numbered event stream with SSE (`/api/events`, JSON snapshot via `?once=1`), and a SHA-256 hash-chained audit log (`/api/audit`, `?verify=1`) with OS file locking so concurrent writers cannot fork the chain.
- **Ops report & console**: `GET /api/ops`, MCP `bridge_ops`, CLI `ops/metrics/audit/tunnel/tokens`, new dashboard tab 7 (tunnel controls, agent roster, task board, live event stream, metrics, audit verification).
- **MCP surface**: 2 new tools (`agent_bus`, `bridge_ops`) → 70 total, and 3 new resources (`studio://bus`, `studio://ops`, `studio://audit`).
- **Setup/CLI**: `setup.py --tunnel-flavor/--tunnel-token/--public-url` with a URL watcher that prints the tunnel URL once the supervisor reports it; `arena_studio.py` gained `ops`, `metrics`, `audit`, `tunnel`, `tokens` and `bus` commands plus a raw-text request helper.
- **Partner handoff**: `handoff/GEMINI-4-ARGON.md`, `handoff/bus_seed.json` and `tools/seed_agent_bus.py` (idempotent bus seeding, online or offline).

### Changed
- `GET /api/health` stays public and now reports version, uptime, auth mode, tunnel state and agent counts.
- `/api/state`, `/api/peers`, `/api/jobs*` and the new read endpoints require a read-scoped token; loopback read convenience is preserved only for `/api/agent-guide` and job polling reads.
- Auth is still disabled entirely when no token is configured (zero-config localhost), but a configured token is now required on `/mcp` and every write path with no anonymous loopback bypass.
- Runtime state (audit, tokens, bus, last tunnel URL) lives in gitignored `roblox-bridge/bridge_state/`.

### Verification
- `python3 -B roblox-bridge/test_bridge_mcp.py` — **25 tests pass**, including 6 new v5 tests: role enforcement over REST and MCP, rate-limit 429s, bus presence/mailbox/lease-expiry/handoff export, idempotency + priority + retry + orphan/timeout reaping, Prometheus/SSE/audit-chain tamper detection, and tunnel URL scraping plus the missing-binary path.
- Dashboard JS passes `node --check`; `/api/health`, `/api/ops`, `/api/metrics`, `/api/events` (SSE), `/api/audit?verify=1` and `/api/tunnel` smoke-tested against a live local server.

## 4.0.0 — upgrade in progress

> This entry records implemented work, not a claim of full release acceptance. Live Roblox Studio play-mode, capture, peer, and scratch-place validation remains unverified when no Studio peer is connected. Inventory targets are met; 268 bundled Kenney assets still use explicit pack-level license attestations rather than exact source-file hash matches.

### Added
- Added `build_biome`, which lays out a seeded, undoable part-based biome tile field with named palettes and optional simple decorations; its response explicitly states that it does not edit Terrain voxels.
- Added `build_skybox` with required six-face content inputs and validated optional Atmosphere properties.
- Added `scatter_props` with deterministic disk sampling, spacing checks, safe script stripping, and rollback on placement errors.
- Added the `self_test` MCP tool and `GET /api/self-test` endpoint with an isolated mock regression run, per-tool pass/fail/call-count/timing matrix, human-readable summary, and optional Studio scratch write/read/delete cycle. Studio-dependent work reports `no_peer` as skipped.
- Expanded end-to-end mock coverage for the registered Studio tools, Luau checks/execution, script lifecycle, generated-image import paths, playtest state, peer waits, snapshots, geometry, viewport rendering, UI layout, and scene auditing.
- Added a Windows Luau 0.744 compiler/analyzer installer that verifies the pinned archive SHA-256 before installation; added its MIT license attribution.
- Added `mesh_capabilities`, a Studio-peer runtime probe for MeshPart construction and transient EditableMesh creation/cleanup. It reports probe evidence and never infers support from the Studio version string.
- Added `search_assets`, a peer-free paginated search over the licensed local registry with source, SPDX, provenance, type, and category filters.
- Added a pinned per-file CC0 asset registry, preserved upstream license evidence, generated attribution, SHA-256 validation, resumable codeload fetching with Git-blob verification, and a `verify_licences` CI workflow.
- Expanded the licensed kit to 4,320 registered files across 226 categories: 3,000 fetched images, 500 pinned Kenney GLB models, 50 deterministic 8-bit LDR sky panoramas with six cube-face PNGs each, existing CC0 assets/audio, and repository-authored `.rbxmx` examples. GLB thumbnails are explicitly neutral placeholders, not model renders.
- Added deterministic sky panorama/cube-face generation, waveform and `.rbxmx` preview generation, local contact sheets, and collision-safe Studio `Assets.lua` indexing for repeated image basenames.
- Added `roblox-kit/assets/screenshots/self-test-viewport.png` with a machine-readable sidecar. It demonstrates the peer-free software viewport and HUD regression path; it is explicitly not a live Studio screenshot.

### Fixed and improved
- `luau_check` now supports inline/file/directory inputs, reports binary and Studio validation backends, provides fallback diagnostics, and treats unavailable optional syntax backends as warnings for otherwise valid writes. Invalid source continues to be rejected before Studio mutation.
- Playtest scenarios now fail when runtime peers are missing, confirm client/player routing before client probes, report session survival/stop status, accept a bounded console slice/cursor, and pass the selected peer ID through all scenario jobs. Regression tests cover keep-alive and clean-stop outcomes.
- Property coercion now rejects malformed tagged colors, enums, instance references, and unknown tags rather than silently substituting a default. Build nodes clean up on failure, build transactions roll back created roots, and set/mass-set results report partial failures with `ok:false`.
- Playtest status and stop handling route through edit-mode peers, and playtest death reasons are surfaced. Missing or stale peers fail fast rather than waiting for an unavailable route.
- Fixed the viewport HUD audit call, made HUD inclusion opt-in by default, and added rasterizer/render regression coverage. Fractional UI rectangles and scaled text positions are now safely rasterized instead of raising float-index errors. Native desktop capture is guarded against unsafe execution.
- Merged typed structured UI properties with legacy property data for layout, visibility, sizing, and styling audits. Nested inspections can now retain runtime `AbsolutePosition`/`AbsoluteSize` and `ScreenInsets` values; UI audit and GUI screenshots identify `measuredFrom` as `client` or `derived` and report safe-area policy/insets when available.
- Tightened Z-fighting checks to require actual overlap and collidable surface conditions.
- Made snapshot diff read-only and verified snapshot persistence/reload behavior.
- Added opt-in `saveToRepo` control for generated procedural images so transient image generation need not mutate the repository.
- Made asset lock/Assets.lua regeneration deterministic and write-only-on-change, preventing no-op image imports from touching unchanged repository assets; duplicate basenames now receive stable path-derived keys rather than silently overwriting one another.
- Self-test subprocesses now run with a recursion guard, and their tool matrix flags missing end-to-end fixtures instead of reporting unconditional passes.

### Migration notes
- `capture_viewport.includeHud` now defaults to `false`.
- Syntax validation is best-effort when all validation backends are unavailable: the operation reports warnings and continues, while detected syntax errors still block writes.

### Verification
- `python3 -B roblox-bridge/test_bridge_mcp.py` — 18 tests passed, including the authenticated `GET /api/self-test` endpoint, fractional viewport rasterization, offline no-peer render path, and nested isolated `self_test` run. The registry now exposes 68 tools.
- `python3 -B tools/verify_licences.py` and `python3 -B tools/verify_licences.py --online` — 4,320 assets passed offline checks and live pinned-tree verification: 3,705 raster images, 50 skies, 520 models, and 45 audio files across 226 categories.
- Online pinned-tree audit: 3,656 exact Git-blob matches; 268 Kenney files use pack-level CC0 attestations; 396 items are first-party generated. Asset inventory thresholds (3,000 assets, 8 categories, 500 models, 50 skies) are met. This does not replace live Studio acceptance.
- `./roblox-bridge/tools/check_globals.sh roblox-bridge/ArenaBridge.lua` — no undefined globals (verified earlier in this work).
- Studio scratch-cycle status: skipped when no peer is available. This is not a live Studio acceptance result.
