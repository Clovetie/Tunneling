# Changelog

All notable changes for the Roblox Studio bridge are documented here.

## 6.1.0 — Security hardening, REST/MCP gate parity, dashboard extraction, and NightLoop removal

### Security (P0)
- **Confirmation-gate parity across transports (`P0-S1`).** The `--require-confirm`
  human-in-the-loop gate used to wrap only the MCP `tools/call` path. The decision now
  lives in `BridgeState.enqueue_job()`, the single chokepoint both transports feed, so a
  write-scoped token can no longer enqueue `run_luau` over `POST /api/jobs` with the gate
  on. `POST /api/jobs` answers `403` with the pending `requestId` and accepts
  `{"confirmation": {"requestId"|"stateToken"}}` on retry. `run_luau`, `execute_luau` and
  `probe` joined `CONFIRM_GATED_TOOLS`, and `mcp_core.job_kind_is_read_only()` routes every
  REST job kind through `is_tool_call_read_only()` — the same argument-aware oracle the MCP
  path uses — instead of a job-type string list. A client can no longer answer its own
  confirmation either: `elicitation_capable` is read from per-request `_meta`, which the
  *caller* writes, so accepting `inputResponses` from it let a prompt-injected agent approve
  its own `execute_luau`. Client answers are now refused by default
  (`reason: client_answers_disabled`, with the operator command in the message) and an
  operator who runs a host that genuinely elicits can opt back in with
  `BRIDGE_TRUST_CLIENT_ELICITATION=1`; `ConfirmGate.status()` reports which policy is in
  force. `gateCleared` is likewise never read from a request body — it is set only from the
  in-process MCP call context — so a REST caller cannot claim the dispatcher already cleared
  the gate.
- **No more owner-token disclosure (`P0-S2`).** `GET /api/plugin/download` is now
  `admin`-only and bakes a short-TTL (`BRIDGE_PLUGIN_TOKEN_TTL`, default 24 h) write-scoped
  token minted for that download, recorded in the audit log — never `bridge.token`.
  `GET /api/local-boot` still returns the boot payload but withholds the credential unless
  the caller is admin, and says so via `tokenWithheld`. `?token=` is accepted only on
  `/api/poll` and `/api/result` (the two routes the Studio plugin calls, where
  `HttpService:RequestAsync` cannot be relied on to send headers); every other route,
  including `arena_studio.py`, is header-only. The dashboard's `EventSource` now redeems a
  one-shot 30 s ticket from `GET /api/sse-ticket` instead of putting a token in a URL.
- **Dashboard XSS closed (`P0-S3`).** `esc()` escaped only `& < >` and was interpolated
  straight into inline `onclick` attributes, so a peer id containing `'` escaped the
  attribute and executed attacker JS against the admin token in `localStorage`. `esc()` now
  escapes `& < > " ' \``, the dashboard emits **zero** inline event handlers (133 static and
  33 generated sites converted to `data-act` / `data-args` resolved by one delegated
  listener), and every HTML/JS/CSS response carries
  `Content-Security-Policy: … script-src 'self' … frame-ancestors 'none'`,
  `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and `Cross-Origin-Opener-Policy`.
- **Path and URL confinement (`P0-S4`).** New `mcp_core.safe_local_path()`,
  `safe_import_path()` and `safe_url()` are now the only way a tool argument reaches the
  filesystem or the network. `import_image` no longer accepts `repoAsset: "../registry.json"`
  or `imageUrl: "file:///etc/hostname"`, and `saveFile` / `outputFile` on `render_offline`,
  `capture_viewport`, `screenshot_gui`, `export_rbxmx` and `export_place_rbxlx` cannot write
  outside an allowed import root (`BRIDGE_IMPORT_ROOTS` extends it). `safe_url` also refuses
  loopback/link-local/private/metadata hosts, closing SSRF against the bridge itself.
  `safe_local_path` *refuses* an absolute path (`/etc/passwd`, `\\server\share`,
  `C:\Windows\win.ini`) instead of silently rebasing it under the root, which used to hand
  back a contained path that was not the path the caller asked for. A sweep of all 100 tool
  schemas for file-shaped arguments found six more unconfined sinks, each a different
  primitive, and all now go through the same helpers: `agent_bus {action:"export", path}`
  (write anywhere), `drift_and_sync {localDir}` in pull mode (write anywhere),
  `luau_check {path}` and `audit_network_security {localDir}` (read anywhere and quote it
  back), `import_images_batch {directory,files}` (read anywhere), and
  `open_cloud_action {placeFile}` (read anywhere, then upload it to Roblox with the
  operator's API key). `image_pipeline`'s generated `Assets.lua` now escapes every
  interpolated key and value as a Luau string literal, so a filename containing `"` cannot
  inject code into a module Studio `require`s — and a lock-file row with no `relPath` no
  longer aborts the import with `KeyError`.
- **Safe defaults (`P0-S5`).** `--host` defaults to `127.0.0.1`; binding a non-loopback
  address prints a warning. A first run with no credential anywhere now generates
  `bridge.token` at mode `0600` instead of coming up in open-admin mode; open mode requires
  an explicit `--no-auth` (or `--no-generate-token`) and is written to the audit chain. The
  demo literal `arena-demo-7f3a` is defined once (`auth_core.PLACEHOLDER_TOKEN`), refused by
  `TokenStore.authenticate()`, and `setup.py` refuses to bake a plugin with it.
- **Kit source is no longer public (`P0-S6`).** `GET /api/kit/catalog` bypassed `_require`
  entirely, so an unauthenticated caller could read full module source
  (`?item=NetworkGuard`). Kit *metadata* stays public (the kit is CC0 and the dashboard
  gallery renders without a token); only `item=` and `section=` — the two queries that return
  source — require `read`. A category listing returns name/path/bytes even at
  `format=full`, so gating it would have broken the gallery without protecting anything.
  `auth_core.READ_ONLY_PATHS` / `ADMIN_PATHS` were dead code and are now the written-down
  route policy: `server.py` raises every request's required scope to the declared floor, so a
  new `/api/*` route that forgets `_require(...)` still gets its declared scope enforced.
  The tables are also honest now — a public prefix wins over a `read` entry (`/api/health`
  was declared read-only while its handler never called `_require`), three declarations for
  routes the server does not dispatch were dropped (`/api/fleet`, `/api/audit/verify`,
  `/api/kit/model`), and routes whose scope depends on the query or the peer
  (`/api/kit/catalog`, `/api/local-boot`, the kit binary routes, `/mcp`) are listed in
  `ROUTE_DECIDES_PATHS` instead of being given a floor they do not honour. A test walks every
  route as an anonymous *cross-site* caller — the header
  `_is_local_convenience_request()` uses to spot a hostile web page talking to localhost — and
  fails if anything outside those lists answers without a credential.

### Changed
- **The dashboard moved out of Python (`P4-D1`).** `dashboard_html()` was ~3,000 lines of
  HTML/CSS/JS inside a Python raw string. It is now `roblox-bridge/static/{index.html,
  dashboard.css,dashboard.js}`, served from `/static/` through a filename whitelist, with
  Python substituting only the version stamp. This is what makes the strict
  `script-src 'self'` CSP possible and cuts ~168 KB from `server.py`.
- `arena_studio.py` sends `X-Arena-Token` only; the redundant `?token=` append is gone.
- The dashboard's six kit calls (`/api/kit/catalog`, `/api/kit/cloud-icons`,
  `/api/kit/viewport-preview`) go through `apiFetch`, so they carry the token and surface the
  auth banner instead of rendering an empty gallery; `downloadPluginFile()` fetches the plugin
  as a `Blob` rather than opening `/api/plugin/download?token=…` in a new tab.
- New `roblox-bridge/tools/dashboard_harness.js`: a dependency-free Node harness that loads
  `static/dashboard.js` into a stub DOM and checks the XSS contract (`esc()` coverage, hostile
  peer data rendered inert, every `data-act` resolving, no token in a URL). Run
  `node tools/dashboard_harness.js`; it exits non-zero, and restoring the pre-6.1.0 `esc()`
  plus one inline `onclick` makes it fail with the original exploit string.
- `test_bridge_mcp.py` gained `TestV61SecurityHardening` (25 tests) covering each P0 item
  end-to-end against a live server: gate parity across both transports, the credential sweep
  over every route, the path/URL confinement sweep over every file-shaped tool argument, the
  CSP and static-asset policy, and the declared route policy — plus `TestV61ManifestIntegrity`
  and `TestV61SnapshotRestore` for the P1 reliability work below. Suite total 117 → 152.
- Plugin version matrix gained a `6.1.0` "current" row; `6.0.0` moved to `legacy_supported`.
- **Reliability surface (`P1`).** `reap_jobs()` now returns `{recovered, timedOut,
  crashLoops}`; `fleet_snapshot()` gained `fleetLoadWarning`; `read_scripts` results gained
  `name` and `parent` (plugin *and* mock peer); `manage_snapshots(action="restore")` gained
  `stopOnError`/`dryRun` and returns `results`/`failedCount`/`notAttempted`;
  `update_repo_asset_manifest()` gained `check=True` and returns `checked`/`drift`/`ok`/
  `written`; new metric counters `bridge_jobs_crash_loop_total` and
  `bridge_http_rejected_total`; new env knob `BRIDGE_MAX_CONCURRENT_REQUESTS`.

### Fixed
- `BridgeState._gate_principal_ok()` did `from confirm_gate import same_principal`, but
  `same_principal` is a `ConfirmGate` **staticmethod** — so the branch that compares
  principals on a `confirmation.stateToken` retry raised `ImportError` instead of enforcing
  the self-approval ban.
- `update_repo_asset_manifest()` raised `KeyError: 'relPath'` on a lock-file row written
  before that field existed, which aborted the whole image import; such rows are now emitted
  with an empty `relPath`, and rows with neither a cloud nor a local URI are left out of
  `Assets.lua` instead of being emitted as `key = ""`.

### Reliability (P1)
- **A crashing peer could hold a job hostage forever (`P1-B1`).** `reap_jobs()` requeued an
  orphaned job every time it saw one, with no ceiling: a Studio that claims a write and dies
  on it — a place that crashes the plugin, a script that kills the peer — was requeued
  forever, burning a claim cycle each time and never telling the caller anything. Orphan
  recovery is now capped at `max(2, maxRetries + 2)` *claims*. Past the cap the job fails with
  `peer_crash_loop after N attempts: peer X took the job and stopped responding each time
  (cap M)`, its waiter is woken with that error instead of hanging until the client gives up,
  and the outcome is emitted (`job.crash_loop`, level `error`), audited (actor `reaper`) and
  counted (`bridge_jobs_crash_loop_total`). `reap_jobs()` returns
  `{recovered, timedOut, crashLoops}`. The two job counters also have one owner each now:
  `attempts` is incremented only on the claim path (`complete_job` had a dead
  `attempts = int(job.get("attempts") or 0)` read that never wrote anything back) and
  `requeues` counts recoveries only, so `requeues == attempts - 1` instead of drifting.
- **A truncated `fleet.json` silently reset every pin and label (`P1-B4`).** `_save_fleet()`
  wrote the live file in place, so a kill or a full disk mid-write left a partial file that
  `_load_fleet()` swallowed with a bare `except: pass` — the next start came up with default
  pins and no hint that an operator's routing choices had been lost. Saves now go to
  `fleet.json.tmp` + `os.replace()`, loads distinguish "no file yet" from "unreadable", and an
  unreadable file is quarantined as `fleet.json.corrupt-<unix ts>` rather than overwritten, so
  the evidence survives. The condition is recorded on `fleet_load_warning`, returned as
  `fleetLoadWarning` by `fleet_snapshot()`/`GET /api/fleet`, and emitted as `fleet.load_failed`
  / `fleet.save_failed` warnings instead of disappearing.
- **A chunked or oversized body was read as `b""` and dispatched as a no-op (`P1-B5`).**
  `_read_body_raw()` returned empty bytes whenever `Content-Length` was absent, so a
  `Transfer-Encoding: chunked` POST reached the routes as `{}`: `POST /api/jobs` answered a
  confusing `400` about a missing `type`, and `POST /api/confirmations` without a `requestId`
  silently did nothing at all — an approval that looks accepted and never happens. Chunked
  bodies are now refused with `411` and a message that names the fix. `Expect:
  100-continue` is answered only *after* the declared size is checked, so an oversized body is
  rejected before it is uploaded instead of after, and a malformed `Content-Length` on such a
  request is a `400` rather than a guess. Finally, `ThreadingHTTPServer` spawned a thread per
  connection with no ceiling (and several SSE streams could be held open indefinitely), so
  `BoundedThreadingHTTPServer` caps in-flight requests — default 64, override with
  `BRIDGE_MAX_CONCURRENT_REQUESTS` or `create_http_server(max_concurrent_requests=…)` — and
  refuses the excess immediately with `503` + `Retry-After: 1`, counting
  `bridge_http_rejected_total` and emitting at most one `http.saturated` warning per second so
  a flood cannot drown the event stream it is being reported to.
- **The asset manifest was written in place, and only after the bytes it indexes (`P1-B3`).**
  `asset-lock.json` and `modules/Assets.lua` were rewritten directly, so a crash between the
  two — or the `KeyError` above — left a truncated manifest that the next import parsed as
  empty and Studio `require`d as broken Luau; repo images and audio were written the same way,
  so a half-written PNG could be indexed with a sha that did not match its bytes. Both manifest
  files and every repo asset write now go through a sibling temp file plus `os.replace()`, the
  asset bytes land before the manifest that describes them, and regeneration stays
  write-only-on-change (an unchanged file is not even touched, so its mtime is stable).
  `update_repo_asset_manifest(check=True)` computes both documents and reports
  `drift`/`ok`/`written` without writing or creating anything; `python3 image_pipeline.py
  --check` is that call as a CI gate and exits non-zero on drift.
- **`manage_snapshots(action="restore")` mangled dotted names and swallowed partial failure
  (`P1-B2`).** Restore rebuilt every target by splitting the snapshot's dot-joined path, so a
  script whose *own name* contains a dot (`A.B`, `Part 1.5`, `UI.Shell`) was written under an
  invented parent with a truncated name — and since each write was a separate job in a bare
  loop, the first failure raised out of the tool with the place half-restored and no record of
  which half. `read_scripts` now reports `name` and `parent` alongside `path` (in
  `ArenaBridge.lua` and the mock peer), snapshots capture them, and restore writes to exactly
  those; the dot-split survives only as a fallback for snapshots taken before 6.1.0 and is
  flagged per row as `legacySplit` plus a warning that says so. Both peers' path `resolve()`
  also re-joins segments when a single segment does not match, which is what makes a dotted
  name addressable at all — a path that resolved before resolves identically. Every attempt is
  collected into `results: [{path, parent, name, ok, error, legacySplit}]` with
  `restoredCount`/`failedCount`/`notAttemptedCount`/`notAttempted`, `stopOnError` (default
  `true`) halts at the first failure instead of grinding through 200 × 30 s timeouts against a
  dead peer, and `dryRun` resolves every target and reports the plan without touching Studio
  (and is classified read-only, like `find_and_replace_scripts(dryRun=true)`).

### Developer tooling (P1-B6)
- **There is a lint gate now.** `pyproject.toml` configures `ruff` over
  `roblox-bridge/`, `jobs/` and `tools/` with `select = ["E", "F", "B", "BLE"]`, and
  `bash tools/check_lint.sh` is the single command that runs it — the same string
  locally and in CI. It exits `0` clean, `1` on a violation, `2` when ruff is not
  installed (with the two install commands printed), so a missing dev dependency is
  never silently a pass.
  - Two rules are ignored, both on purpose and both documented in the config:
    `E501` (3,994 hits — the tree embeds Luau source, dense report tables and prose
    docstrings; wrapping them changes strings the tools emit verbatim) and `BLE001`,
    the 63 blind `except Exception` handlers that P1-B6 was really about.
  - `BLE001` is **ratcheted, not forgiven**: gate 2 counts the handlers and compares
    against `tools/lint_ble_baseline.txt` (63 today). Adding one fails the build;
    removing one prints a reminder to lower the baseline so the gain sticks. That is
    the honest shape of this debt — rewriting 63 handlers in one pass is a larger
    regression risk than the handlers themselves, and a silently-ignored rule is how
    the count got to 63.
  - The 50 non-`E501`/non-`BLE001` findings were fixed rather than ignored, and one
    was a genuine latent bug: `tools/fetch_assets.py` defined `get_json()` inside a
    loop closing over that iteration's `headers` dict (`B023`) — now bound as a
    default argument. Also: 10 unused imports removed (including a dead
    `mode_from_env` re-import in `server.py`; 5 more stay and are annotated — see
    below), 4 unused-assignment findings deleted as 6 dead lines
    (`existing_agents` was fetched twice and never read, so the seeding tool now
    makes one fewer request), 15 compound `if x: y` / `a; b` lines split (each
    rewrite AST-checked against the original before being written), 3 `raise`
    statements given `from None` / `from exc` so the original cause is either
    explicitly suppressed or preserved, 4 `zip()` calls given `strict=False` to pin
    today's truncating behaviour, 6 unused loop variables `_`-prefixed, and 2
    placeholder-free f-strings de-`f`'d. `build_handoff_pdfs.py`'s five
    reportlab/pygments imports carry `# noqa: F401` — they are availability probes
    for the optional PDF toolchain, not dead code.
  - ruff is a **lint-time** dependency only: nothing in `pyproject.toml` is read at
    runtime and the stdlib-only import audit in CI still applies to every module the
    bridge loads (AGENTS.md rule 1).

### Removed (P2 — NightLoop)
- **`nightloop/` is gone**, with its in-repo copies and every reference: 125
  tracked files, −37,441 lines. That is the reference horror-survival experience
  (8 entities + 6 night modifiers), its static Luau analysis workbench
  (`nightloop/tools/audit.sh`; the equivalent check that stays is
  `bash roblox-bridge/tools/check_globals.sh <dir>`), the `roblox-bridge/nightloop/`
  export mirror, `roblox-bridge/patches/` (four Place1 patch scripts plus their
  `ORIGINAL_*` backups), and the twelve NightLoop-specific operator jobs in `jobs/`
  (`baseline_survey`, `climb_check`, `drift_audit`, `hash_diagnostic`,
  `night_watch`, `preview_climb`, `push_climb`, `set_config_flag`,
  `set_whisperer_ids`, `whisperer_check`, `make_push.py`,
  `repo_hashes_2026-10-07.txt`). The three general drills stay:
  `jobs/{live_acceptance,drill_studio_kill,measure_tunnel_latency}.py`.
  `roblox-bridge/tools/{render_posesheet.py,capture_poses.lua}` went with it — the
  NightLoop monster rig was their only subject.
  - 12,985 lines of that Python were a *third* divergent copy of the bridge
    (v3.4.0 against 6.1.0), which is the reason to delete rather than archive:
    three copies of `server.py` are three places to miss a security fix — the P0
    work in this same release would otherwise have had to be applied twice more.
    `roblox-bridge/` is now the only bridge.
  - Kit content is untouched: `roblox-kit/systems/mechanics/horror-director/`, the
    `horror_*` prefabs and `ui_horror_sanity_vitals_hud.json` are kit material, not
    NightLoop. The horror showcase still renders — it was always hardcoded geometry
    plus those kit prefabs, never a read of that tree — under its new canonical key
    `showcase:horror_cabin`, labelled "Horror Cabin, FuseBox, SafeRoom &
    Generator"; `showcase:horror_nightloop` stays as a deprecated alias so a saved
    command keeps working.
  - References were repointed, not just deleted: the Luau tool-binary search path
    lost `nightloop/.tools/bin`; `drift_and_sync`'s examples, the completion
    candidates, the `arena_studio.py` and `ab.ps1` usage lines now say
    `roblox-kit/modules` and `ServerScriptService.GameSystems` (the container the
    mock world actually builds) instead of `nightloop/src` and
    `ServerScriptService.NightLoop`; `README.md` lost the NightLoop feature row, the
    layout row and the `audit.sh` command, and the rows that stayed had their stale
    counts corrected to what the tree holds — 100 tools, 33 modules, 24 systems, 36
    prefabs, 20 `.rbxmx` models.
- **`SESSION.md` is deleted** (P4-D5). It was a *tracked* file titled "live
  connector credentials" holding machine-local paths, dead tunnel hostnames and a
  NightLoop-era session journal — a credential-shaped file in Git history is a leak
  surface whether or not a secret survived in it, and its own header claimed it was
  gitignored while `CONNECT.md` recorded that it was not. `CONNECT.md` now points at
  the canonical source, `roblox-bridge/bridge.token` (mode 0600, gitignored), and no
  longer asks anyone to record session state in the repo. **If a token was ever
  pasted into `SESSION.md`, run `python setup.py --rotate`** — deleting the file
  does not remove it from history. `roblox-bridge/CHANGES.md` is kept, because
  history is history, with a note that the `patches/ORIGINAL_*.lua` backups it cites
  were removed and now live only in Git history.

## 6.0.0 — Studio Plugin v6 (`ArenaBridge.lua` DockWidget UI + Bulletproof Playtest Stop), 90 MCP Tools, Network Security Auditor, Cutscene Sequencer, and V6 Control Center Overhaul

### Added
- **Roblox Studio Plugin v6 (`ArenaBridge.lua v6.0.0`)**:
  - **Built-in Roblox Studio `DockWidgetPluginGui` Dashboard (`ArenaBridgeDashboardV6`)** + `Arena v6 UI` toolbar toggle button showing live connection status, DataModel role (`edit` / `server` / `client`), Place name/ID, jobs processed/failed counters, last job latency, and 3 in-Studio action buttons (`Force Stop Playtest`, `Clean Temp Runners`, `Reconnect Now`).
  - **Bulletproof Playtest Closing (`handlers["playtest"]`)**:
    - Supports `stop`, `force_stop`, and `cleanup` actions.
    - Attempts `StudioTestService:EndTest(...)` and falls back to `StudioTestService:CanLeaveTest()` + `StudioTestService:LeaveTest()` when Studio is inside a manual F5/F8 playtest or server DataModel.
    - Treats "No test is currently running" / already-stopped states as idempotent success (`stopped = true, alreadyStopped = true`) instead of failing.
    - Self-heals stale `activePlaytestRunning` locks by cross-checking `RunService:IsEdit()` and automatically purges leftover `__ArenaTestRunner_*` temporary `ServerScriptService` scripts via `cleanupTempRunners()`.
  - **Instant Play Peer Eviction (`plugin.Unloading` + `/api/poll?goodbye=1` + `evict_playtest_peers()`)**:
    - When Studio closes a Play Solo / Run session, `plugin.Unloading` sends a `/api/poll?goodbye=1` beacon; `server.py` immediately marks `server`/`client` peers offline (`lastPoll = 0`) and cancels any queued runtime jobs so `get_studio_state` / `list_studios` never show ghost playtest peers.
  - **`plugin_info` Job Type**: Returns live in-Studio telemetry (`pluginVersion`, `jobsProcessed`, `jobsFailed`, `lastJobType`, `lastJobMs`, `dashboardAvailable`, `capabilities`).
- **4 New V6 MCP Tools (bringing `TOOL_SPECS` to 90 tools)**:
  - `manage_plugin_and_playtest`: Inspect connected Studio peer plugin versions (`v6.0.0` vs older `v4.0.0`/`v4.1.0`), query live in-Studio `plugin_info`, install `ArenaBridge.lua v6.0.0` locally, or run `force_stop_playtest` / `evict_stale_peers`.
  - `audit_network_security`: Scan Studio or local Luau scripts for unvalidated `.OnServerEvent` / `.OnServerInvoke` handlers, client-trusted authority parameters (`price`, `currency`, `damage`, `admin`), and missing rate limits, returning a 0–100 `securityScore` and `NetworkGuard` remediations.
  - `build_cutscene_sequence`: Generate keyframed 3D camera cutscene tracks (`boss_awakening`, `safe_room_unlock`, `horror_corridor_reveal`, `victory_dawn_flyover`, `arena_overview`) with 3D waypoint rigs and `CutsceneDirector` Luau modules.
  - `generate_ui_theme_pack`: Generate multi-device responsive `ScreenGui` theme packs (`HorrorBloodMoon`, `CyberpunkNeon`, `FantasyGold`, `MinimalDark`, `StudRetro`) + `UIThemeTokens` Luau modules.
- **Expanded `roblox-kit/` Library (28 Modules, 22 Systems, 30 Prefabs)**:
  - Added 3 new Luau modules (`CutsceneDirector.lua`, `NetworkGuard.lua`, `DialogueSystem.lua`), 2 new drop-in mechanics systems (`mechanics/cutscene-camera`, `mechanics/dialogue-npc`), and 4 new 3D/UI prefabs (`horror_security_camera_rig`, `world_cutscene_stage_rig`, `world_npc_dialogue_kiosk`, `ui_dialogue_cinematic_box`).
- **V6 Control Center UI Overhaul (`server.py`)**:
  - Added `GET|POST /api/plugin-status`, `GET /api/plugin/download` (serves pre-baked `ArenaBridge.lua v6.0.0` with active bridge URL and token), and `POST /api/playtest/force-stop`.
  - Added the **Studio Plugin & Playtest v6 (`#tab-plugin`)** tab, top-bar **⏹ Force Stop Playtest** & **⬇ Plugin v6.0.0** buttons, and automatic per-peer plugin version badges (`v6.0.0 ✓` vs `v4.0.0 ⚠`).

## 5.5.0 — MCP 2026-07-28 & `.well-known` discovery completeness, Cloudflare QUIC→HTTP/2 tunnel resilience, and HTTP security hardening

### Added
- **Zero-auth `.well-known` discovery endpoints**:
  - **SEP-1649 MCP Server Cards** at `GET /.well-known/mcp/server-card.json` and `GET /.well-known/mcp.json`, plus the `mcp://server-card.json` MCP resource (12th registered resource).
  - **RFC 9728 OAuth 2.0 Protected Resource Metadata** at `GET /.well-known/oauth-protected-resource/mcp` and `GET /.well-known/oauth-protected-resource`, paired with RFC 9728 `WWW-Authenticate: Bearer ... resource_metadata="..."` headers on `401` and `error="insufficient_scope"` on `403`.
- **MCP protocol & ergonomics upgrades (`mcp_core.py`, `server.py`)**:
  - **`completion/complete` + `completions` capability** across modern and legacy eras for prompt arguments (`task_id`, `agent_id`, `focus`, `mode`, `root`) and resource templates (`skill://{name}`).
  - **SEP-2243 Base64 header codec (`=?base64?...?=`)** (`encode_mcp_header_value` / `decode_mcp_header_value`) and strict Streamable HTTP validation (`HTTP 400` + `-32020`) for `Mcp-Method`, `Mcp-Name`, and `MCP-Protocol-Version` vs `_meta["io.modelcontextprotocol/protocolVersion"]` mismatches; `HTTP 400` on `-32022` unsupported protocol versions; `405 Method Not Allowed` for `DELETE /mcp` and `GET /mcp` SSE in stateless `2026-07-28`.
  - **Cursor pagination** (`cursor` / `nextCursor`) on `tools/list`, `resources/list`, `resources/templates/list`, and `prompts/list`; human-readable `title` fields across tools, resources, resource templates, and prompts; `resource_link` blocks appended to `apply_skill`, `skill_references`, and `manage_snapshots` responses.
- **Cloudflare Tunnel transport resilience (`tunnel_manager.py`, `setup.py`, `arena_studio.py`)**:
  - `--tunnel-protocol auto|http2|quic` (`BRIDGE_TUNNEL_PROTOCOL`) with automatic `quic` → `http2` fallback when outbound UDP `:7844` is blocked.
  - Named Cloudflare tunnel edge-registration detection (`Registered tunnel connection` / `Connection ... registered`) when `--public-url` is supplied alongside `--tunnel-token`.
  - Strict public `/api/health` verification (`HTTP 200` + JSON `{"ok": true}`), on-demand `probe` (`POST /api/tunnel {"action":"probe"}`, `arena_studio.py tunnel probe`, `bridge_ops(action="tunnel_probe")`), and separation of active `publicUrl` from persisted `lastKnownUrl` while stopped.
  - Immediate `: connected\n\n` SSE byte flush and 15-second `: keepalive\n\n` heartbeats on `/mcp` subscriptions and `/api/events` (plus `Last-Event-ID` resume and `Cache-Control: no-cache, no-transform`) so Cloudflare proxies never buffer or idle-drop streams.
- **HTTP security guards**:
  - Browser `Origin` validation (`403` on untrusted cross-origin requests; configurable allowlist via `BRIDGE_ALLOWED_ORIGINS`), safe `Content-Length` parsing with `BRIDGE_MAX_BODY_BYTES` (`400` / `413`), `X-Content-Type-Options: nosniff`, expanded `Access-Control-Expose-Headers`, and `0o600` file permissions on `bridge.token` across all creation/recovery paths in `setup.py` plus `--mcp-transport http|stdio`.
- **NightLoop horror survival expansion (`nightloop/src/`, `nightloop/client/`) & static analysis library (`nightloop/tools/lib/`)**:
  - Implemented 4 new full horror survival entities (`Breathless.lua` miasma/oxygen suffocation zone, `TickingMan.lua` temporal freeze/movement detector, `Mimic.lua` shapeshifting shadow doppelganger, and `FuseBox.lua` interactive house breaker blackout system) plus `NightModifiers.lua` (`BloodMoon`, `DenseFog`, `Overcharged`, `GlassHouse`, `ChronosNight`, `Ironman`) wired into `Director.lua`, `Config.lua`, and `NightLoopClient.client.lua`.
  - Added `paths.js`, `luau.js`, `luau-transform.js`, and `evaluate.js` modules and `luau-compile` fallback in `check-syntax.js` so `bash nightloop/tools/audit.sh` runs cleanly out of the box across all 23 server + 3 client/tool Luau files.
- **Gameplay Mechanics, Procedural World & Audio Synthesis expansion (`mcp_core.py`, `image_pipeline.py`, `roblox-kit/`)**:
  - Added 4 new MCP tools (bringing `TOOL_SPECS` to **86** tools): `generate_dungeon_layout` (seeded 3D room & corridor layout via Prim's MST + escape loops), `build_gameplay_mechanic` (10 ready-to-install gameplay mechanic bundles), `configure_atmosphere_fx` (8 multi-layer Lighting + post-processing presets), and `synthesize_sound_effect` (12 procedural 16-bit mono PCM `.wav` horror & gameplay SFX).
  - Expanded `roblox-kit/` with 6 new Luau gameplay modules (`StateMachine`, `CooldownManager`, `LootTable`, `QuestTracker`, `CombatPipeline`, `DungeonGenerator` -> **25** modules), 6 new drop-in gameplay systems under `roblox-kit/systems/mechanics/` (`horror-director`, `round-survival`, `stamina-movement`, `inventory-loot`, `combat-abilities`, `weather-cycle` -> **20** systems), 6 new prefabs (`horror_fuse_breaker_station`, `horror_task_generator_machine`, `world_safe_room_shelter`, `world_loot_chest_shrine`, `lighting_horror_blood_moon`, `ui_horror_sanity_vitals_hud` -> **26** prefabs), and 12 new procedural PNG shader presets (**38** total).
- **Web Dashboard & Localhost Asset Library Overhaul (`server.py`)**:
  - Added `GET /api/kit/catalog`, `GET /api/kit/assets`, `GET /api/kit/cloud-icons`, `GET /api/kit/thumbnail/<rel>`, `GET /api/kit/procedural/<preset>.png`, and `GET /api/kit/synth-audio/<preset>.wav`.
  - Fixed `loadKitGallery()` (which previously called `browse_kit` with default `format="summary"`, returning empty arrays) and wired `saveToken()` / `applyHeaderToken()` to reload the Asset Library immediately; added interactive Gameplay Mechanics Builder, HTML5 Canvas 3D Dungeon Map Generator, Atmosphere FX Studio, Procedural WAV Soundboard, and Unified 4,320-Asset Registry Explorer with pagination and thumbnails.

### Fixed
- Fixed `UnboundLocalError` (`header_version`) when `POST /mcp` received malformed JSON (`-32700`).
- Fixed `modern` (`2026-07-28`) `server/discover` and `tasks/*` envelopes (`resultType: "complete"`, `_meta["io.modelcontextprotocol/serverInfo"]`, `MCP-Protocol-Version` response header) and hardcoded `v4.0`/`v5.4.0` strings in the Web Dashboard.
- Fixed `jobs/drill_studio_kill.py` (`reaped_total()` crash on list-typed `/api/metrics` `counters`, and `execute_luau` → `run_luau` job type) and `jobs/measure_tunnel_latency.py` (mapped MCP tool names to valid Studio job types and added `--transport mcp`).
- Removed 12 untracked-experiment scratch JSON/Lua files from `roblox-bridge/`.

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

- **CI, staged one directory out:** `.github/ci.yml` runs the suite on Python
  3.10 / 3.12 / 3.13, audits that runtime modules stay stdlib-only (a guarded
  optional import with a documented fallback, like Pillow in `image_pipeline`,
  passes), scans every `.lua` for undefined globals, re-checks the kit licence
  manifest, and fails if a doc claims a bridge version other than the one in
  `server.py`. Move it into `.github/workflows/` to activate — that push needs
  the GitHub App to hold the `workflows` permission, which is why it waits a
  directory up. Every step is also a plain local command.

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
