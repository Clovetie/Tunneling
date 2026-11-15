# Arena ↔ Roblox Studio MCP Server & Bridge (v6.0.0)

Zero-dependency **Model Context Protocol (MCP)** server (`stdio` + `Streamable HTTP`), multi-DataModel Studio local plugin (`ArenaBridge.lua` v6.0.0), live web dashboard, and CLI (`arena_studio.py` / `ab.ps1`) for connecting AI coding agents to Roblox Studio.

Works simultaneously across **every connection mode**:

| Mode | Transport | Clients Supported | Best For |
|---|---|---|---|
| **1. Local MCP (`stdio`)** | `python mcp_server.py` | Claude Code, Claude Desktop, Cursor, Codex CLI, VS Code Copilot, Windsurf, Cline, Zed | Direct local agent control — auto-attaches to a running bridge or starts an embedded one automatically |
| **2. Remote / Local MCP (`Streamable HTTP`)** | `POST http://127.0.0.1:8077/mcp` or `https://<the-tunnel>.trycloudflare.com/mcp` | Any MCP 2026-07-28 / 2025-06-18 / 2024-11-05 Streamable HTTP client | Shared multi-agent sessions or remote MCP clients over a tunnel |
| **3. Read-Only Inspector MCP** | `--read-only` flag on `mcp_server.py` or `server.py` | Any MCP client or REST caller | Restricted place auditing, architecture exploration, and live log debugging |
| **4. Arena REST + HANDS MODE CLI** | `POST /api/jobs` · `.\ab.ps1 <cmd>` | Arena cloud agents & PowerShell / Bash | Cloud sandboxes with restricted egress or direct terminal scripting |
| **5. Supervised Public Tunnel** | `python setup.py --tunnel` · `POST /api/tunnel` | Cloud agents, phone/tablet testing | Auto-detects cloudflared/ngrok, scrapes and re-learns the URL, health-probes the public endpoint, restarts with backoff, supports named tunnels |
| **6. Coexistence with Studio's Built-in MCP** | Parallel sidecar | Studio Built-in MCP (`StudioMCP`) + `arena-roblox-studio-mcp` | Combines Roblox's cloud AI mesh/material tools with repo FNV-1a sync, `luau_check`, snapshots, terrain, and `require_fresh` |

---

## v5 Ops Platform (tunnels, multi-agent bus, telemetry, scoped auth)

`docs/OPS.md` is the full reference. Highlights:

- **Supervised tunnel** — `setup.py --tunnel` (or `server.py --tunnel`) keeps a
  cloudflared/ngrok tunnel alive: URL scraping, health probes, backoff restarts,
  named-tunnel tokens, LAN URLs, and a live state machine in `GET /api/tunnel`.
- **Agent Bus** — presence, direct/broadcast mailbox, cursor inbox and a
  lease-based task board so several agents (Claude, Gemini, humans) can share one
  Studio session. `agent_bus` MCP tool + `arena_studio.py bus …` + Ops tab UI.
- **Scoped tokens** — `read` / `write` / `admin` roles, expiry, revocation and
  per-token rate limits on top of the existing `bridge.token` owner credential.
- **Resilient jobs** — idempotency keys, priorities, bounded retries, per-job
  timeouts and orphan requeue when a peer disappears.
- **Observability** — Prometheus metrics (`/api/metrics`), SSE event stream
  (`/api/events`), and a SHA-256 hash-chained audit log (`/api/audit?verify=1`).
- **Ops Console** — dashboard tab 7 renders tunnel, roster, task board, live
  events, metrics and audit verification.

---

## Ecosystem Research Summary (2026 Best Practices Applied)

This v6.0.0 architecture incorporates verified patterns across the 2026 Roblox MCP ecosystem:

1. **Roblox Official Built-in Studio MCP (`create.roblox.com/docs/studio/mcp`)** *(which superseded `Roblox/studio-rust-mcp-server`, archived April 2026)*:
   - Focused, low-token tool taxonomy (`get_studio_state`, `list_studios`, `search_game_tree`, `inspect_instance`, `script_read` with line ranges, atomic `multi_edit`, `script_search`, `script_grep`, DataModel-targeted `execute_luau`, `start_stop_play`, `get_console_output`) rather than 150+ overlapping micro-tools that bloat prompt context.
2. **Multi-DataModel Routing & Silent Client RPC (`Chrrxs/robloxstudio-mcp`, `Dvitash/robloxstudio-mcp`)**:
   - In Studio playtests, both the Edit DataModel and Play Server DataModel stay alive simultaneously, while the Play Client DataModel cannot call `HttpService:RequestAsync`.
   - **v4.0.0 fix**: Every peer identifies its DataModel (`edit` vs `server`), eliminating Edit/Server poll races. In Play mode, the `server` peer creates a transient `Archivable = false` `RemoteFunction` (`ReplicatedStorage.__ArenaBridgeClientRPC`) and the `client` peer attaches silently without ever calling HTTP — enabling live `dm="client"` inspection/eval with zero Output window errors.
3. **Editor-Safe Script Editing & Cache Busting (`EL4CTEO/rbx-studio-mcp`, `boshyxd/robloxstudio-mcp`, `aaronaalmendarez/roblox-mcp`)**:
   - Reads active open editor buffers via `ScriptEditorService:GetEditorSource()` and writes via `ScriptEditorService:UpdateSourceAsync()` with CRLF→LF normalization (avoiding the 199,999-byte `Script.Source` limit, open-tab clobbering, and Live Scripting `\r\n` errors).
   - Solves Luau's `ModuleScript` instance `require()` caching via `replaceInstance: true` on writes/edits plus `require_fresh` sandbox evaluation.
   - Adds optimistic concurrency (`expectedHash` exact 53-bit-safe 32-bit FNV-1a) + server-side multi-script snapshots (`manage_snapshots`) + single `Ctrl+Z` `ChangeHistoryService` undo recordings.
4. **Sidecar Safeguards (`gjlmotea/studio-sidecar-mcp` + NightLoop `AGENTS.md`)**:
   - Offline pre-push `luau_check` for available local syntax and global checks; inspect `checksPerformed` and warnings for missing analysis.
   - One-command `drift_and_sync` (`check` / `push` / `pull`) between local Git folders and Studio containers.
   - Automatic embedded-script scanning & stripping (`stripScripts: true`) in `insert_asset` to prevent free-model backdoors.
   - Automatic token recovery in `setup.py` and `ab.ps1` from installed `%LOCALAPPDATA%\Roblox\Plugins\ArenaBridge.lua`, eliminating `HTTP 401` token mismatches across fresh branch ZIP folders.

---

For the curated source ZIP and clean-install steps, see `docs/RELEASE.md`. Rebuild from the working Git checkout with `python3 roblox-bridge/tools/build_release.py`; the generated archive and checksum are placed under ignored `release-artifacts/`. It is a source distribution, not a signed native installer.

## Quick Start

### 1. One-Command Install & Launch

```powershell
cd roblox-bridge
python setup.py --write-mcp
```

- Installs `ArenaBridge.lua` v6.0.0 into `%LOCALAPPDATA%\Roblox\Plugins` (Windows) or `~/Documents/Roblox/Plugins` (macOS) with your token pre-baked.
- Writes `.mcp.json` ready for Claude Code / Cursor / VS Code.
- Starts `server.py` v6.0.0 on `http://127.0.0.1:8077` (Web Dashboard + REST API + `POST /mcp` Streamable HTTP).
- Add `--tunnel` if exposing to a remote cloud agent via Cloudflare Quick Tunnel, or `--install-only` if letting `mcp_server.py` self-host the embedded bridge on demand.

### Safe script edits

MCP `multi_edit` rejects malformed edit entries before confirmation, task creation, or Studio dispatch. Use either `oldText`/`newText` (optionally `replaceAll`) or integer `startLine`/`endLine`/`newText`; do not mix forms. `endLine = startLine - 1` inserts before the start line. These checks apply to MCP calls, not direct plugin/REST jobs, and do not prove the target or source is correct. Use `expectedHash` and a rollback plan. `validateSyntax` checks the source **after** the edit has been written, not as a pre-write guarantee. With `createIfMissing`, Studio may create a script before a later hash or match check fails; avoid it when that side effect is unacceptable. Validate behavior in a permitted Studio test place.

`batch_write` checks all MCP file entries before confirmation, task creation, syntax-check fallback, or job submission. Invalid entries that older clients sent may now be rejected; direct REST/plugin jobs bypass this MCP preflight. The plugin attempts one ChangeHistory recording in Edit mode, but may run without one; cancellation or rollback is not guaranteed for every failure or external side effect. A mock rollback is not evidence of Studio rollback. Inspect targets, take a backup, and verify after the batch. Both `write_script` and batch writes can destroy a same-named non-script instance; `expectedHash` only checks an existing script and does not guard a missing target or non-script collision. Syntax validation can be unavailable, in which case writes can proceed with a warning.

For MCP instance operations, delete, mass-property and reparent requests require nonempty, string-valued paths; property edits require changes, multi-target rename is rejected, and clone count/offset must fit the advertised bounds. These preflight checks do not establish that paths exist or are safe, and direct REST/plugin jobs bypass them. `delete_instance` may delete some targets while reporting others missing; `mass_set_properties` can also partially succeed. Check the returned `ok`, `missing`, `errors` and per-property failures. An Edit-mode ChangeHistory recording is attempted, not guaranteed; take a backup and verify rather than assuming atomic rollback.

`manage_camera` defaults to the read-only `get` action, including when the bridge itself runs in Inspector mode. MCP camera writes require valid finite three-number coordinates, a position when using `lookAt`, and FOV within 1–120; focus requires a target path. Malformed requests that previously reached the plugin may now be rejected. These checks do not verify peer availability or actual camera behavior, and direct REST/plugin jobs bypass them.

For first-session design, search for `onboarding progression` and load `roblox-onboarding-progression`. Its `references/milestone-matrix.md` helps map objectives, authoritative completion evidence, retry/rejoin behavior and observations. The skill provides guidance only; it does not alter an agent, place or DataStore. Player comprehension, device usability and multi-client behavior require separate authorized tests.

`luau_check` is local-only and never falls back to a Studio job. If local binaries are missing or non-executable it reports an unavailable toolchain; install or enable them explicitly rather than relying on the bridge to change permissions. Each file result reports `checksPerformed`: a compiler-only success does not mean undefined globals or unused locals were checked. This changes the previous implicit Studio fallback for `luau_check`. Write-capable tools such as `write_script` and `batch_write` have separate validation behavior and may still try a Studio parser fallback or proceed with a warning when validation is unavailable. Local checking uses temporary files and executes locally installed tools; it is not a sandbox for untrusted binaries.

For large streaming-enabled worlds, use `roblox-world-streaming` and its `references/streaming-test-matrix.md` to plan client instance lifecycle, server-authoritative interactions and traversal tests. The guidance references Roblox Creator Docs; it does not simulate streaming or change Studio. Verify multi-client behavior and real-device performance separately in an authorized test environment.

### Read-only and scoped safety

Inspector mode and read-scoped MCP tokens check individual actions, not just tool annotations. Bus reads are allowed, but posting, presence/status changes and exports to disk require write access. `self_test` defaults to a live scratch write/read/delete cycle and requires write access; pass `includeStudioSmoke:false` for the isolated mock suite. The REST `/api/self-test` automatically skips its live smoke for read-scoped tokens and in Inspector mode. `require_fresh` requires write access: module initialization or probe code can affect state outside the temporary clone. Opening an editor tab, device-simulator sweeps and rendering with `saveFile` also require write access; their read-only alternatives remain available. REST bus actions use the same classification, and write-scoped tokens can call write-capable MCP tools. Confirmation policy, peer gating and normal authorization still apply.

Inspector mode also blocks REST snapshot saves and fleet pin/label changes while retaining snapshot and fleet reads. Other privileged operational controls have separate authorization and are not a blanket read-only filesystem or operator sandbox.

An Inspector **bridge** now accepts only explicitly classified read-only Studio job types and safe mixed-action variants. This closes omissions such as `batch_write` and `scatter_props` and blocks unknown/custom jobs by default; extensions must be reviewed and added to the allowlist before they can run in Inspector mode. Non-Inspector custom job behavior is unchanged. `mesh_capabilities` is permitted as a diagnostic but creates temporary unparented objects and attempts cleanup; it is not a zero-allocation probe. An Inspector-only stdio client connected to a separate non-Inspector bridge does not protect other clients of that bridge.

The local dashboard token bootstrap and anonymous loopback discovery now require a loopback connection, a localhost/127.0.0.1/[::1] Host matching the bridge port, and, when present, a same-origin `Origin` and compatible `Sec-Fetch-Site`. Forwarded requests and cross-origin browser requests do not receive the bootstrap token or anonymous discovery access; remote dashboards must supply a token. This protects those convenience endpoints, not every possible local process or a misconfigured proxy. Keep tokens private and do not expose an unauthenticated bridge.

Studio plugin polling (`GET /api/poll`) registers a peer and claims queued jobs, so it now requires a **write** or admin token; a read-only token can no longer run the plugin poll loop. The usual setup installs the primary token in the plugin, but custom installations using a read-scoped token must replace it with a write-scoped token. This does not change Inspector mode’s restrictions on requested actions.

HTTP MCP tasks and in-band confirmations are associated with the authenticated token ID, not caller-supplied principal metadata or token labels, including in batches. Clients sharing a token share that identity; with authentication disabled, callers share the open identity. Operator confirmation controls are a separate trust boundary: write-scoped access to `POST /api/confirmations` can make operator decisions, so grant that scope only to trusted operators. These safeguards are not a substitute for securing the bridge and its tokens.

Read-only mode limits these operations; it is not a security sandbox for untrusted plugins or externally controlled Studio sessions. Check results and perform live tests only with permission.

### Connection diagnostics

Run `python arena_studio.py doctor` or call the read-only MCP tool `diagnose_connection`. It reads bridge health and the authenticated peer registry, without submitting Studio jobs or changing a place. Results distinguish `bridge_unreachable`, `bridge_error`, `auth_error`, `no_peer`, and `connected`; the CLI exits zero only for `connected`. A reachable bridge with no online Studio peer is **not** a successful Studio connection. `readAccess` distinguishes authenticated, open, direct-dispatch and unknown access; reported plugin versions are compared to the bundled plugin version, but are peer-reported and do not prove runtime compatibility. A paused queue and older or unknown reported versions are surfaced as warnings.

The report omits credentials, peer names, place IDs, URLs and raw transport errors. Check tokens and plugin configuration locally; never paste credentials into chat. The registry is a snapshot: `connected` and advertised client support are not proof that a Studio job, a client RPC, a particular feature or a device test worked. Use `ping_studio` or the relevant runtime capability probe when a real connected Studio peer is available and authorized. The tool does not inspect the installed plugin file or repair configuration.

### 2. Connect Your MCP Client

#### Claude Code
```bash
claude mcp add roblox-studio -- python /full/path/to/roblox-bridge/mcp_server.py --auto-install-plugin
```

#### Codex CLI
```bash
codex mcp add roblox-studio -- python /full/path/to/roblox-bridge/mcp_server.py --auto-install-plugin
```

#### Cursor / Claude Desktop / Windsurf (`.cursor/mcp.json` or `claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "roblox-studio": {
      "command": "python",
      "args": ["C:/path/to/Tunneling/roblox-bridge/mcp_server.py", "--auto-install-plugin"]
    }
  }
}
```
*(Add `"--read-only"` to `args` for safe Read-Only Inspector mode.)*

#### Streamable HTTP MCP Client (Local or Tunnel)
- Endpoint: `http://127.0.0.1:8077/mcp` (or `https://some-random-words.trycloudflare.com/mcp`)
- Header: `Authorization: Bearer <contents of bridge.token>`

---

## MCP Tool Suite and Open-Source Vault

Use `tools/list` for the authoritative schemas and current inventory. The table below groups the main tools; `search_tools` can find tools by task without a Studio peer.

| Domain | MCP Tools |
|---|---|
| **Session & Peers (3)** | `get_studio_state`, `list_studios`, `ping_studio` |
| **Explore & Diff (5)** | `survey_place`, `inspect_instance`, `search_game_tree`, `get_properties`, `compare_instances` |
| **Scripts & Fresh Require (9)** | `script_read`, `read_scripts_batch`, `write_script`, `multi_edit`, `script_search`, `script_grep`, `find_and_replace_scripts`, `open_script_in_editor`, `require_fresh` |
| **Luau & Playtesting (4)** | `execute_luau` (`edit`/`server`/`client`), `start_stop_play`, `run_playtest_scenario`, `get_console_output` (`sinceIndex` cursor + level filter) |
| **Instances & Bulk Ops (6)** | `build_tree`, `set_properties`, `mass_set_properties`, `clone_instance` (grid/offset duplication), `reparent_or_rename`, `delete_instance` |
| **Attributes & Tags (2)** | `manage_attributes` (`get`/`set`/`delete`), `manage_tags` (`list_all`/`find_tagged`/`get`/`add`/`remove`) |
| **World, Camera, Undo & Assets (8)** | `manage_selection`, `manage_camera`, `undo_redo`, `edit_terrain`, `insert_asset`, `build_biome`, `build_skybox`, `scatter_props` |
| **Toolchain, Build, Sync & Snapshots (7)** | `luau_check`, `drift_and_sync`, `manage_snapshots`, `batch_write`, `probe`, `wait_for`, `self_test` |
| **Discovery, Skills & Localization** | `diagnose_connection`, `search_tools`, `search_skills`, `apply_skill`, `skill_references`, `audit_localization`, `audit_onboarding_plan` (offline and read-only) |
| **Interactive Testing** | `simulate_input`, `manage_device_simulator`, `run_studio_tests`, `playtest_capabilities` (probe runtime support and connected peers first) |
| **Ops, Telemetry & Multi-Agent (2)** | `agent_bus` (presence, mailbox, lease-based task board, handoff export), `bridge_ops` (ops report, metrics, audit verification, live events, tunnel control) |
| **3D Viewport Vision, Rendering, Physics QA, `.rbxlx`, Open Cloud & Asset Vault (24)** | `capture_viewport`, `export_geometry`, `render_offline` (also accepts exported trees without a peer), `screenshot_gui` (client-measured or derived), `perf_audit`, `mesh_capabilities` (runtime API probe; version string alone is not evidence), `search_assets` (offline licensed asset registry), `audit_3d_scene`, `export_place_rbxlx`, `import_place_rbxlx`, `open_cloud_action`, `audit_ui_layout`, `install_dropin_system`, `import_audio`, `import_rbxmx`, `export_rbxmx`, `install_template`, `search_cloud_icons`, `import_image`, `import_images_batch`, `generate_procedural_image`, `browse_kit`, `install_kit_module`, `spawn_prefab` |

### Repo-Committed Skills, Assets & Modules
- **Find the right tool**: `search_tools` accepts a task query such as `{"query":"performance","readOnlyOnly":true}` and returns ranked names, descriptions, original annotations and pagination. Empty queries list visible tools; filler-only queries return no matches. `readOnlyOnly` filters strictly by the tool's original read-only annotation and omits mixed-action tools; Inspector results otherwise include only its visible tools. Annotations are hints, not guarantees of authorization, peer availability or safe use of every sub-action. Use `tools/list` for complete input schemas and check `get_studio_state` before live work.
- **Onboarding plan audit (offline)**: `audit_onboarding_plan` checks caller-supplied milestones without Studio. Example: `{"milestones":[{"id":"first","objective":"Try the tool","completionEvidence":"Server validates the action","recovery":"Retry after respawn","prerequisites":[]}]}`. IDs are exact and case-sensitive. It reports missing or blank objective/evidence/recovery fields, duplicate IDs or prerequisites, unknown or ambiguous prerequisites, and cycles among uniquely identified milestones. Duplicate IDs are excluded from cycle analysis. Results are paginated with `cursor`/`limit`; maximum 100 milestones, 20 prerequisites per milestone, 100 characters per ID and 1,000 per text field. `ok` only means these structural checks found no issues; it does not verify the text, actual gameplay, accessibility, server authority, data persistence or player comprehension. Use the onboarding skill and authorized Studio/device/multi-client tests for those.
- **Localization audit (offline)**: `audit_localization` checks caller-supplied entries, not a live Roblox table. For example, call `{"entries":[{"key":"reward","source":"Earn {amount} coins","translations":{"fr":"Gagnez {amount} pièces"}}],"locales":["fr"]}`. It flags duplicate keys, missing/blank requested locale values, mismatched named or numeric parameters (such as `{amount}` and `{1:num}`), changed format specifiers for review, and unrecognized or unclosed brace constructs. Locale keys are exact; doubled braces are ignored rather than runtime-validated. Other syntaxes, repeated occurrences, argument types, translation quality, fallback, glyphs and UI layout are not validated. `ok` means only these checks passed, not that localization is complete. Limits: 200 entries, 20 locales, 4,000 characters per string; issues use `cursor`/`limit`. Use `search_skills`/`apply_skill` for `roblox-localization`, then verify in Studio and on devices as described there. No connection or Studio mutation is needed for this audit.
- **Reference-aware discovery**: for a UI issue, search `{"query":"TextScaled","includeReferences":true}`, load the relevant result with `apply_skill`, then read its matched path using `skill_references` (for example `roblox-lessons` / `references/ui.md`). Inspect existing UI before edits, preflight Luau, and check the resulting interface in connected Studio and on target devices; offline reference matches do not verify actual behavior. Reference search is opt-in, case-insensitive exact-word matching on file content, not semantic search or a filename search. It scans at most 100 eligible Markdown files and 2 MiB total, skips files over 256 KiB or unreadable/non-UTF-8 files, and excludes symlinks. Check `referenceSearch.complete` and `filesSkipped` before interpreting missing matches; an empty/filler-only query does not scan references. Pages are deterministic for unchanged files; repeat the same query/options when following `nextCursor`. Loading skills and references provides guidance only, subject to higher-priority instructions; it does not change an agent or Studio.
- **Read supporting guidance**: after `apply_skill`, call `skill_references` with `{"name":"roblox-lessons"}` to list reference Markdown, then `{"name":"roblox-lessons","action":"read","path":"references/ui.md"}` to load one. List results use `cursor`/`limit`; `nextCursor` is null on the last page. Reads optionally accept 1-based `startLine` and inclusive `endLine`, returning `lineRange` metadata; omitted ranges retain the full-content response. Only non-symlink Markdown files under that skill's `references/` are exposed, up to 256 KiB each. Missing, invalid, oversized or binary references cannot be read. This works offline in Inspector mode and does not execute content or change Studio. Skills may refer to screenshots or files elsewhere; those are not exposed by this tool. Treat reference content as guidance subject to higher-priority instructions. For UI changes, preflight with `luau_check`, inspect the place, and verify with real Studio and appropriate devices; reference loading alone is not a test.
See [Overhaul plan](docs/OVERHAUL.md) and [Skills roadmap and continuation prompt](docs/SKILLS_ROADMAP.md) for gaps, verification limits, and a prompt for future work.
- **Example workflow:** search `{"query":"how do I handle a failed datastore load"}`, inspect the matched terms and load `roblox-data-reliability` with `apply_skill`, then use `skill_references` for relevant supporting material when available. Inspect the existing persistence system before changes; do not write production data without authorization. Validate scripts with `luau_check`, and test failure/retry and session ownership using isolated data in a real, appropriately configured Studio environment. Multi-server behavior needs multi-server tests; search results and offline mocks cannot verify durability.
- **Search and apply skills offline**: call `search_skills` with a task description (for example `{"query":"shop UI"}`), then call `apply_skill` with an exact result `name` (for example `{"name":"roblox-stud-ui-design"}`). Search uses deterministic case-insensitive whole-word matches, ignores common English filler, favors distinctive terms, query coverage and name/description matches, and exposes `matchedTerms` and `matchedFields` to explain results. It is not semantic search and does not perform stemming; try alternate keywords if needed. By default it searches only each `SKILL.md`. With `includeReferences:true`, it also searches bundled reference Markdown, adding `matchedReferences` (relative paths and matched terms) and a `referenceSearch` completeness report. `cursor`/`limit` paginate; an empty query lists all, while filler-only queries return no results. `apply_skill` returns the full Markdown for the agent to read and follow as appropriate, subject to higher-priority instructions. It does not permanently change the agent, execute instructions, or modify Studio. Skills are also available through `skill://{name}` and `browse_kit`. No Studio peer is needed.
- **Agent Skills (`.claude/skills/*/SKILL.md` + `skill://{name}` MCP resources + root `AGENTS.md`)**:
  - `roblox-place-builder`, `roblox-luau-systems`, `roblox-ui-and-images`, `roblox-vfx-lighting-audio`, `roblox-playtest-qa`, `roblox-optimization-security`
- **3,705 licensed raster images plus 50 sky panoramas** are catalogued in `roblox-kit/assets/registry.json` and indexed in `roblox-kit/modules/Assets.lua`; each panorama has six generated 8-bit LDR cube faces (not HDRIs).
- **500 pinned Kenney CC0 GLB models** plus 20 repository-authored `.rbxmx` models are catalogued with thumbnail metadata. GLB thumbnails are explicitly marked as neutral format placeholders until real mesh rendering is available.
- **45 CC0 WAV audio files** have waveform thumbnails. Every entry carries SHA-256, source, pinned revision, licence, provenance status, and thumbnail path; 3,656 files additionally match exact upstream Git blob hashes.
- Verify and browse with `python3 -B tools/verify_licences.py` and `roblox-kit/assets/contact-sheets/index.html`. Rebuild with `tools/fetch_assets.py` and `tools/build_skies.py`; see `roblox-kit/assets/README.md` for the limitations and reproduction commands.
- **8 Ready-to-Spawn Prefabs (`roblox-kit/prefabs/*.json`)**:
  - `lighting_cinematic_sunset`, `lighting_cyber_night`, `lighting_stylized_day`, `hud_modern_shell`, `interact_pad`, `spawn_plaza`, `world_sign_board`, `vfx_collectible_orb`

### MCP Resources & Prompts Also Exposed
- **Resources**: `studio://state`, `studio://peers`, `studio://console`, `studio://selection`, `studio://survey`, `studio://bus`, `studio://ops`, `studio://audit`
- **Resource Templates**: `studio://scripts/{path}`, `studio://instance/{path}`
- **Prompts**: `survey_and_architecture`, `safe_module_edit`, `automated_playtest_debug`, `sync_repo_package`

---

## CLI & HANDS MODE Quick Reference (`arena_studio.py` / `ab.ps1`)

```powershell
.\ab.ps1 health                                             # Bridge + connected peers
.\ab.ps1 ping                                               # Verify pluginVersion "4.1.0" & latency
.\ab.ps1 state                                              # Full Studio state (memory, FPS, undo)
.\ab.ps1 check .\src                                        # Offline compile + undefined-global audit
.\ab.ps1 drift .\src ServerScriptService.App                # Exact 32-bit FNV-1a diff report
.\ab.ps1 sync  .\src ServerScriptService.App                # Pre-check + snapshot + atomic 1-undo push
.\ab.ps1 grep "RemoteEvent" --root ReplicatedStorage        # Search code across live Studio scripts
.\ab.ps1 edit ServerScriptService.Main --old "DEBUG = false" --new "DEBUG = true" --replace
.\ab.ps1 require-fresh ReplicatedStorage.Shared.Config --code "return MODULE"
.\ab.ps1 scenario --mode play --server-code "return {players = #game.Players:GetPlayers()}"
```

---

## Automated Verification Suite

Run the full offline & stateful mock Studio test suite (verifies FNV-1a exact vectors, `luau_check`, token recovery, Streamable HTTP MCP, `stdio` NDJSON + LSP framing, Read-Only Inspector enforcement, UTF-8 round-trip, and all registered tools including live package sync, the MRTR confirmation gate and the 2026-07-28 playtest surface):

```bash
python3 roblox-bridge/test_bridge_mcp.py
```

---

## Files

| File | Role |
|---|---|
| `setup.py` | Installs `ArenaBridge.lua` v4.1.0, recovers/manages `bridge.token`, writes `.mcp.json`, launches server/tunnel |
| `mcp_server.py` | Standalone `stdio` MCP server (auto-attaches to `server.py` or starts an embedded bridge thread) |
| `mcp_core.py` | Zero-dependency MCP protocol engine, tool schemas/handlers, resources, prompts, `luau_check`, `drift_and_sync` |
| `server.py` | Multi-peer long-poll HTTP bridge + `/mcp` Streamable HTTP endpoint + snapshot store + live web dashboard |
| `ArenaBridge.lua` | Roblox Studio local plugin v4.1.0 (`edit` + `server` + silent `client` RPC bridge, VirtualInput + Device Simulator + StudioTestService handlers) |
| `arena_studio.py` | UTF-8-safe CLI covering all bridge & MCP operations |
| `ab.ps1` | Windows PowerShell wrapper with automatic token lookup and UTF-8 console setup |
| `mock_studio.py` | Stateful in-memory Studio v4.1.0 simulator (including simulated Play Server/Client peers & undo/redo) |
| `test_bridge_mcp.py` | End-to-end automated test suite |
