# Changelog

All notable changes for the Roblox Studio bridge are documented here.

## 5.4.0 — the 2026-07-28 MCP era put to work, an opt-in confirmation gate, and Studio's real playtest APIs

> v5.3 taught the bridge to *speak* the 2026-07-28 protocol. v5.4 uses the parts
> of it that change who is in charge — `tools/call` can now stop and ask a human
> (MRTR) instead of either refusing or running unchecked — and ships the three
> Studio testing APIs that the 2026 ecosystem turns on: VirtualInput, the Device
> Simulator and `StudioTestService`. Nothing here is on by default except the new
> tools; the gate needs `--require-confirm`.

### Added
- **`confirm_gate.py`** — a stdlib-only MRTR gate. `tools/call` on a gated tool
  answers `resultType: "input_required"` with an `inputRequests.confirm` elicit
  plus an HMAC-signed, single-use `requestState` that binds the *principal*, the
  method and a digest of the arguments, with a TTL and a nonce. Four modes
  (`off`, `destructive`, `write`, `all`), accept-and-remember allowances,
  `cancelled` / expiry sweeps, counters and a recent-decision ledger. A wrong or
  spent answer re-prompts (`digest_mismatch`, `replayed`, `principal_mismatch`,
  `expired`, `unknown_request`) rather than failing the call.
- **Operator control plane**: `GET|POST /api/confirmations` (status / approve /
  deny / expire), `GET /api/subscriptions`, `POST /api/subscriptions/close`,
  `GET|POST /api/mcp/events`; CLI `arena_studio.py confirmations`,
  `approve <requestId> [--note] [--remember]`, `deny <requestId> [--note]`,
  `subscriptions [--close <id>]`. An agent may always *read* the queue, but
  approving its own parked call is refused with `reason: "operator_only"`.
- **Five launch flags** on `server.py` and `mcp_server.py`:
  `--require-confirm {off,destructive,write,all}`, `--confirm-ttl` (15–900 s),
  `--confirm-allowance`, `--confirm-wait` (legacy blocking path),
  `--auto-task-ms` (promote a slow call to a task handle). Env:
  `BRIDGE_REQUIRE_CONFIRM`, `BRIDGE_CONFIRM_TTL`, `BRIDGE_CONFIRM_ALLOWANCE`,
  `BRIDGE_CONFIRM_WAIT_SECONDS`, `BRIDGE_MCP_AUTO_TASK_MS`, and
  `BRIDGE_CONFIRM_SECRET` (otherwise the bridge signs with its own token).
- **Four tools (75 registered)** backed by real engine APIs, probed before use:
  `simulate_input` (keys, typing, mouse, touch, drags and wheel through
  `UserInputService:CreateVirtualInput()`), `manage_device_simulator`
  (`StudioDeviceSimulatorService`: presets, orientation, resolution, DPI,
  scaling, custom `DeviceConfiguration` profiles), `run_studio_tests`
  (`StudioTestService` run / play / multiplayer 1–8 players with a watchdog and
  a temporary runner Script that is always destroyed), and `playtest_capabilities`
  (which of those the connected Studio build actually exposes, with the exact
  baseline version each needs).
- **Modern-era protocol behaviour**: per-request `_meta` negotiation
  (`io.modelcontextprotocol/protocolVersion|clientCapabilities|clientInfo`, which
  outranks `Mcp-Protocol-Version`), `resultType` on every result, `ttlMs` /
  `cacheScope` on list and read results, `server/discover` echoing the negotiated
  version, `subscriptions/listen` streaming `notifications/resources/updated` on
  the long-lived stdio stream or the SSE `POST /mcp` response, `-32021` when a
  client uses `tasks/*` without declaring the extension, `_meta` log level →
  real `notifications/message` (embedded as `io.modelcontextprotocol/logEntries`
  in single-shot HTTP), OTel `traceparent` / `tracestate` / `baggage` propagated
  into jobs and the audit chain, and `X-Arena-Task` / `X-Arena-Subscription`
  response headers.

### Changed
- `/api/mcp/events`, `POST /api/confirmations` and `/api/subscriptions/close` now
  live in `do_POST` (they were being looked up by `do_GET`, so POST got a 404),
  and each route reads its own body.
- An out-of-band approval may be spent by exactly **one** retry: the nonce is
  consumed when the retry takes the decision, so a second use reports `replayed`.
- `bridge_ops` gate/subscription actions are never gated (an approval is itself
  the authorization step — gating it is circular) and `confirmations` now counts
  as a read action, so an Inspector-only bridge can still show the queue.
- `manage_device_simulator` returns camelCase device rows (the engine's
  `DeviceInfo` is PascalCase), its `action` enum is derived from
  `DEVICE_SIMULATOR_ACTIONS` so schema and validator cannot drift, and `set` is
  documented as the one-job-combines-many-overrides action.
- `simulate_input` refuses instead of pretending: no playtest → per-step
  `reason: "not_playing"`, `ok: false`, and a hint naming `start_play`; a
  `target` that is a `Model` projects its `PrimaryPart` (or first Part) rather
  than erroring; `pinch` explains the one-pointer limit instead of faking it.
- `MUTATING_JOB_TYPES` covers `virtual_input`, `device_simulator` (except
  `status` / `list`) and `studio_tests`, so `--read-only` stays a true Inspector.
- The mock Studio now tracks `playtest_running` (single-world sessions report
  `isRunning` honestly), and a stopped playtest clears its simulated client peer.

### Verified
- `python3 -B roblox-bridge/test_bridge_mcp.py` → **Ran 64 tests … OK** (was 43):
  19 of them in `TestV54ConfirmationGate` (gate modes, signed state, replay,
  principal binding, task flip, subscriptions, headers, ops surface), plus the
  per-tool coverage contract — every one of the 75 tools is now called in the
  end-to-end fixture *and* recorded in the nested self-test's metrics line.
- `ArenaBridge.lua` v4.1.0 passes `run_luau_check` with no unused locals; the mock
  and the plugin implement the same four job contracts field for field.
- The operator loop was run for real: a parked legacy `write_script` + an approve
  on `POST /api/confirmations` (and via `arena_studio.py approve`, exit code 0)
  let the blocked call complete, and the second retry of a spent token reported
  `replayed`.

## 5.3.1 — make the "frozen console" and "no tunnel" cases explain themselves

> Two support reports drove this patch: a bridge started without `--tunnel`
> printed only the local block (nothing was published), and a console opened
> through the tunnel sat on stale numbers because every `/api` call was `401`.

### Added
- **Console auth banner**: when any API call returns `401`/`403` the page now
  says so and offers a token box (saved in `localStorage`) instead of silently
  keeping old numbers. The header pill switches to `token required` or
  `bridge unreachable` instead of a misleading "Waiting for Studio".
- **Token sources**: `?token=…` / `#token=…` on the dashboard URL, then
  `localStorage`, then the loopback-only `/api/local-boot` value. A token pasted
  once survives reloads and bridge restarts.
- **Stale-tab banner**: the page embeds its own version and compares it with the
  live bridge; a tab opened before an upgrade gets a **Reload** prompt instead of
  half-working UI.
- **`setup.py --dry-run`**: prints the resolved plan without starting anything —
  server command, tunnel intent (`supervised auto` / `external <url>` /
  `not requested`), dashboard URL. `python setup.py --dry-run --tunnel` is the
  fastest way to answer "why did I get no public URL?".
- **Tunnel pre-flight + failure reporting**: `--tunnel` with no `cloudflared` /
  `ngrok` on PATH now warns *before* launch with install commands, a plain run
  says **"Tunnel: not requested"**, and the watcher prints `lastError` + `logTail`
  for `state=error`/`blocked` (it previously only spoke for `unavailable`).

### Changed
- `docs/OPS.md` gained a §1 troubleshooting row pair and a new **§12 Console
  troubleshooting** table ("dashboard doesn't update", "which URL do I give
  Arena") explaining why `/api/local-boot` refuses to hand a token to a forwarded
  request.
- Version `5.3.1` across server, MCP surface, CLI, telemetry, bus, auth, tunnel
  manager and docs; dashboard badge `v5.3`.

### Verification
- `python3 -B roblox-bridge/test_bridge_mcp.py` — **43 tests pass** (2 new: the
  off-loopback dashboard contract — `/api/local-boot` withholds the token for a
  `CF-Connecting-IP` request, `/api/state` answers `401` without one and `200`
  with one, and the page ships the banner; plus `describe_launch()` covering
  supervised / external / not-requested tunnel plans).

## 5.3.0 — multi-studio fleet, dual-era MCP (2026-07-28 + tasks), animated console

> Unit-tested against the stateful mock Studio with two simulated peers. The
> modern MCP paths were verified against the published 2026-07-28 / 2025-11-25
> specifications; live Studio acceptance (G7/G8) is still operator-only.

### Added
- **Studio fleet (`/api/studios`, `studio_fleet`, `studio://studios`)** — the
  bridge now treats several Studio windows as a managed fleet, the same idea as
  Studio's built-in `list_roblox_studios` / `set_active_studio`:
  `list` returns every peer with `peerId`, place, DataModel, online age, label,
  pinned/default flags and queued job counts; `set_active` pins a default target;
  `clear_active` returns to automatic edit-first routing; `label` names a window.
  Pins and labels persist in `bridge_state/fleet.json`.
- **Pin is a preference, not a gate**: an untargeted job prefers the pinned
  Studio but falls back to `auto` routing the moment that Studio stops polling,
  so closing a window can never stall the queue (covered by a test).
- **Broadcast fan-out** (`POST /api/studios {"action":"broadcast"}`,
  `studio_fleet {action:'broadcast'}`): runs one read-only job on every online
  Studio in parallel and returns per-peer `{ok, status, durationMs, result,
  error}` with fleet-wide totals. Mutating kinds are refused with a `400` — a
  fan-out can never write to every place at once.
- **Dual-era MCP transport**: speaks the modern **2026-07-28** revision as well
  as every legacy revision back to `2024-11-05`. `server/discover` returns
  `supportedVersions` / `capabilities` / `serverInfo` / `instructions` /
  `ttlMs` / `cacheScope`; per-request version negotiation happens through
  `MCP-Protocol-Version` or `_meta`; unknown versions answer `-32022` with the
  supported list; `Mcp-Method` / `Mcp-Name` header/body mismatches answer
  `-32020`; modern results carry `resultType: "complete"` and list results carry
  cache hints. Legacy clients see byte-identical old shapes.
- **Task-augmented tool calls** (MCP tasks, SEP-1686 / SEP-2663): any tool can be
  invoked with `params.task`, returning `{resultType:"task", taskId, status,
  pollIntervalMs, ttlMs}` immediately; `tasks/get`, `tasks/result` (blocking,
  tagged with `_meta` related-task), `tasks/list` and `tasks/cancel` complete the
  lifecycle. Tools advertise `execution.taskSupport: "optional"` and
  `capabilities.tasks` declares `list` / `cancel` / `requests.tools.call`.
- **Console redesign**: sticky multi-studio strip with live chips, pin and
  broadcast controls, animated tab/pane transitions (fade + rise), glass panels
  with gradient accents and hover lift, count-up stat numbers, styled
  scrollbars and a `prefers-reduced-motion` opt-out — all in one self-contained
  document with no CDNs, web fonts or external images.
- **`/api/health`** now reports `studios {count, online, active, routing}`;
  `/api/ops` adds `fleet` and `mcp {protocolVersions, modernProtocol, tasks}`.

### Changed
- `list_studios` keeps its old shape; `studio_fleet` is the richer, pinnable
  view. Any tool already accepted `peerId`, so targeting did not need new
  argument plumbing.
- Dashboard badge `v5.3`, version `5.3.0` across server, MCP surface, CLI,
  telemetry, bus, auth, tunnel manager and docs.

### Verification
- `python3 -B roblox-bridge/test_bridge_mcp.py` — **41 tests pass** (6 new:
  fleet list/pin/routing/persistence, labels + broadcast + write refusal, the
  `studio_fleet` tool and resource, modern-era discovery/`resultType`/`-32022`/
  `-32020`, the task lifecycle, and the console's fleet strip + motion hooks).
- Modern-protocol behaviour was checked against the 2026-07-28 changelog and the
  tasks SEP; no claim is made about Studio-specific multi-instance inference
  (Roblox's built-in server infers the target from context — this bridge is
  explicit: `peerId` or a pin).

## 5.2.0 — unattended handoff: automatic sweeper, maintenance pause, job/ops resources

> Unit-tested against the stateful mock Studio. Nothing here needs a live peer to
> exercise; the G7/G8 operator evidence is still outstanding (see 5.1.0).

### Added
- **Automatic stale-agent sweeper (`server.py`)**: the reaper thread now also runs
  `sweep_stale()` every `--sweep-interval` seconds (default 300,
  `BRIDGE_SWEEP_INTERVAL`, `0` disables) against agents silent longer than
  `--sweep-offline` (default 900, `BRIDGE_SWEEP_OFFLINE`). A sweep that releases
  work emits `bus.sweep`, appends `bus.sweep` to the audit chain and bumps
  `bridge_agents_reaped_total` / `bridge_tasks_released_total`; a quiet sweep is silent and still counted in
  `bridge_sweeps_total`. Dead peers' tasks now move to a live peer without an
  operator remembering to run `bus sweep`.
- **Maintenance pause (`/api/queue/pause` / `/api/queue/resume`, admin)**: holds
  jobs for Studio while still accepting them — peers stay registered (poll
  answers `{"job":null,"paused":true}`), nothing is cancelled, resume wakes every
  waiting peer and releases the held work. Exposed as
  `bridge_ops {action:'queue_pause'|'queue_resume'}` (`reason` recorded in audit)
  and as **Pause queue / Resume queue** in the Ops Console.
- **`studio://jobs` resource**: queue depth, pause state, sweeper counters, live
  health subset, recent job summaries and per-tool latency in one document.
- **`studio://handoff` resource** (`text/markdown`): the full bus export as a
  paste-ready brief, so an agent can file its handoff with one resource read.
- **`jobLatency` in `/api/ops`**: per-tool `count/p50/p95/max/mean` over the last
  300 completed jobs, computed in-process — percentile visibility without a
  Prometheus scrape. The dashboard prints a one-line summary under the metrics box.
- **Ops Console controls**: **Sweep** on the task board (releases dead peers'
  tasks) plus the pause/resume switch and a sweeper status line.
- **`arena_studio.py queue` CLI**: `status` (pause state, sweeper counters,
  per-tool latency, five most recent jobs), `pause --reason …`, `resume` and
  `clear`, so maintenance mode needs no curl.

### Changed
- `metrics_snapshot()` exports `bridge_queue_paused` and `bridge_sweeps_total`.
- `/api/poll` responses carry `paused` so the plugin can surface maintenance mode.
- `POST /api/ops` accepts `queue_pause`, `queue_resume` and `sweep`.
- Version `5.2.0` across the server, MCP surface, CLI, telemetry, bus, auth,
  tunnel manager and docs; dashboard badge `v5.2`.

### Verification
- `python3 -B roblox-bridge/test_bridge_mcp.py` — **35 tests pass** (4 new:
  pause holds the exact job and resume reclaims it + admin-only enforcement,
  automatic sweep releases/counts/audits and stays silent when idle, `bridge_ops`
  control actions plus the two new resources, and the `queue` CLI round trip).

## 5.1.0 — partner tasks landed: validated named tunnels, sweeper, incremental briefs, MCP prompt recipes, operator scripts

> Unit-tested against the stateful mock Studio plus a fake tunnel binary.
> **Live Studio acceptance (G7) and the kill-switch drill (G8) still need a real
> peer** — the scripts for both ship here, the evidence does not.

### Added
- **Named-tunnel hardening (`tunnel_manager.py`)**: `validate_named_token()` rejects empty/whitespace/URL/short/junk tokens *before* a process spawns, the supervisor reports `state=error` with an actionable `lastError` (never a silent restart loop), and `/api/tunnel` exposes `namedTokenValid` plus display-only `name`. `setup.py --tunnel-name` passes the label through; it never reaches the cloudflared command line.
- **Stale-agent sweeper (`bus_core.py`)**: `agent_bus {action:'sweep', offlineSeconds:900}` (CLI `bus sweep`) releases `in_progress` tasks owned by agents silent past the threshold, posts one `alert`, bumps `agentsReaped`/`tasksReaped`/`lastSweepAt` in `stats()`, persists the counters, and is a no-op when called twice. Tasks held by live agents are untouched.
- **Incremental handoff briefs**: `export` accepts `since` (cursor) and `summaryOnly`; open tasks are always included, messages and task detail are filtered/omitted, and the cursor is resolved to a wall-clock cutoff from the event log.
- **MCP prompt recipes (`mcp_core.py`)**: `prompts/list` now returns **7** prompts. `multi_agent_handoff`, `bus_standup` and `task_triage` render live roster/task/cursor state through the client and fall back to an explicit "live bus state is unavailable" line when the bridge is offline.
- **Dashboard Ops tab (`server.py`)**: responsive layout at 720 px and below (two-up stat cards, stacked panels, URL wrapping, no horizontal scroll) plus a "Copy MCP config (tunnel)" button that emits a ready `mcpServers` JSON block.
- **Reconnecting event stream**: `EventSource` now retries with 1 → 2 → 4 … 30 s backoff, shows `Reconnecting (Ns)`, resets on success, and never leaks duplicate sources.
- **Operator scripts**: `jobs/measure_tunnel_latency.py` (p50/p95/max per tool, local vs tunnel, JSON out, distinct exit codes for bad token / no peer / total failure), `jobs/live_acceptance.py` (G7 checklist as a script, PASS/FAIL per step plus paste-ready evidence JSON) and `jobs/drill_studio_kill.py` (G8: enqueue, wait for the peer to claim, kill Studio, watch the requeue and `bridge_jobs_reaped_total`).
- **Docs**: `docs/OPS.md` gained §1.1 (named-tunnel runbook with a troubleshooting table), §10 (acceptance + drill procedures and how to read the results) and §11 (latency budget with an explicitly unfilled measurement table).

### Changed
- Version `5.1.0` across the server, MCP surface, CLI, telemetry, bus, auth, tunnel manager and docs.
- `arena_studio.py bus` accepts `sweep`, `--summary-only` and `--offline-seconds`.

### Verification
- `python3 -B roblox-bridge/test_bridge_mcp.py` — **31 tests pass** (6 new: sweeper + incremental export, prompt rendering/fallback, named-token validation, responsive dashboard + `node --check`, latency-script percentiles/401, operator-script failure modes).
- Live acceptance and the kill-switch drill remain **unverified without a Studio peer**; run them with the scripts above and paste the JSON into `docs/OPS.md` §10.

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
- **Handoff PDFs (`tools/build_handoff_pdfs.py`)**: `handoff/Tunneling-v5-Code.pdf` (complete v5 sources + unified diffs of every touched file, 88 pages) and `handoff/Gemini-4-Argon-Tasks.pdf` (G1–G8 as prompt-ready briefs for a text model, 12 pages). Build-time only: `reportlab` + `pygments`; the bridge runtime stays stdlib-only.

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
