# Arena ↔ Roblox Studio MCP Server & Bridge (v4.0.0)

Zero-dependency **Model Context Protocol (MCP)** server (`stdio` + `Streamable HTTP`), multi-DataModel Studio local plugin (`ArenaBridge.lua` v4.0.0), live web dashboard, and CLI (`arena_studio.py` / `ab.ps1`) for connecting AI coding agents to Roblox Studio.

Works simultaneously across **every connection mode**:

| Mode | Transport | Clients Supported | Best For |
|---|---|---|---|
| **1. Local MCP (`stdio`)** | `python mcp_server.py` | Claude Code, Claude Desktop, Cursor, Codex CLI, VS Code Copilot, Windsurf, Cline, Zed | Direct local agent control — auto-attaches to a running bridge or starts an embedded one automatically |
| **2. Remote / Local MCP (`Streamable HTTP`)** | `POST http://127.0.0.1:8077/mcp` or `https://<the-tunnel>.trycloudflare.com/mcp` | Any MCP 2025-06-18 / 2024-11-05 Streamable HTTP client | Shared multi-agent sessions or remote MCP clients over a tunnel |
| **3. Read-Only Inspector MCP** | `--read-only` flag on `mcp_server.py` or `server.py` | Any MCP client or REST caller | Zero-risk place auditing, architecture exploration, and live log debugging |
| **4. Arena REST + HANDS MODE CLI** | `POST /api/jobs` · `.\ab.ps1 <cmd>` | Arena cloud agents & PowerShell / Bash | Cloud sandboxes with restricted egress or direct terminal scripting |
| **5. Coexistence with Studio's Built-in MCP** | Parallel sidecar | Studio Built-in MCP (`StudioMCP`) + `arena-roblox-studio-mcp` | Combines Roblox's cloud AI mesh/material tools with repo FNV-1a sync, `luau_check`, snapshots, terrain, and `require_fresh` |

---

## Ecosystem Research Summary (2026 Best Practices Applied)

This v4.0.0 architecture incorporates verified patterns across the 2026 Roblox MCP ecosystem:

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
   - Offline pre-push `luau_check` catching syntax errors **and** non-strict undefined globals before code touches Studio.
   - One-command `drift_and_sync` (`check` / `push` / `pull`) between local Git folders and Studio containers.
   - Automatic embedded-script scanning & stripping (`stripScripts: true`) in `insert_asset` to prevent free-model backdoors.
   - Automatic token recovery in `setup.py` and `ab.ps1` from installed `%LOCALAPPDATA%\Roblox\Plugins\ArenaBridge.lua`, eliminating `HTTP 401` token mismatches across fresh branch ZIP folders.

---

## Quick Start

### 1. One-Command Install & Launch

```powershell
cd roblox-bridge
python setup.py --write-mcp
```

- Installs `ArenaBridge.lua` v4.0.0 into `%LOCALAPPDATA%\Roblox\Plugins` (Windows) or `~/Documents/Roblox/Plugins` (macOS) with your token pre-baked.
- Writes `.mcp.json` ready for Claude Code / Cursor / VS Code.
- Starts `server.py` v4.0.0 on `http://127.0.0.1:8077` (Web Dashboard + REST API + `POST /mcp` Streamable HTTP).
- Add `--tunnel` if exposing to a remote cloud agent via Cloudflare Quick Tunnel, or `--install-only` if letting `mcp_server.py` self-host the embedded bridge on demand.

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

## Full 68-Tool MCP Suite + Multimodal 3D Viewport Vision, 3D Physics QA, `.rbxlx` & Open-Source Vault

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
| **3D Viewport Vision, Rendering, Physics QA, `.rbxlx`, Open Cloud & Asset Vault (24)** | `capture_viewport`, `export_geometry`, `render_offline` (also accepts exported trees without a peer), `screenshot_gui` (client-measured or derived), `perf_audit`, `mesh_capabilities` (runtime API probe; version string alone is not evidence), `search_assets` (offline licensed asset registry), `audit_3d_scene`, `export_place_rbxlx`, `import_place_rbxlx`, `open_cloud_action`, `audit_ui_layout`, `install_dropin_system`, `import_audio`, `import_rbxmx`, `export_rbxmx`, `install_template`, `search_cloud_icons`, `import_image`, `import_images_batch`, `generate_procedural_image`, `browse_kit`, `install_kit_module`, `spawn_prefab` |

### Repo-Committed Skills, Assets & Modules
- **Agent Skills (`.claude/skills/*/SKILL.md` + `skill://{name}` MCP resources + root `AGENTS.md`)**:
  - `roblox-place-builder`, `roblox-luau-systems`, `roblox-ui-and-images`, `roblox-vfx-lighting-audio`, `roblox-playtest-qa`, `roblox-optimization-security`
- **3,705 licensed raster images plus 50 sky panoramas** are catalogued in `roblox-kit/assets/registry.json` and indexed in `roblox-kit/modules/Assets.lua`; each panorama has six generated 8-bit LDR cube faces (not HDRIs).
- **500 pinned Kenney CC0 GLB models** plus 20 repository-authored `.rbxmx` models are catalogued with thumbnail metadata. GLB thumbnails are explicitly marked as neutral format placeholders until real mesh rendering is available.
- **45 CC0 WAV audio files** have waveform thumbnails. Every entry carries SHA-256, source, pinned revision, licence, provenance status, and thumbnail path; 3,656 files additionally match exact upstream Git blob hashes.
- Verify and browse with `python3 -B tools/verify_licences.py` and `roblox-kit/assets/contact-sheets/index.html`. Rebuild with `tools/fetch_assets.py` and `tools/build_skies.py`; see `roblox-kit/assets/README.md` for the limitations and reproduction commands.
- **8 Ready-to-Spawn Prefabs (`roblox-kit/prefabs/*.json`)**:
  - `lighting_cinematic_sunset`, `lighting_cyber_night`, `lighting_stylized_day`, `hud_modern_shell`, `interact_pad`, `spawn_plaza`, `world_sign_board`, `vfx_collectible_orb`

### MCP Resources & Prompts Also Exposed
- **Resources**: `studio://state`, `studio://peers`, `studio://console`, `studio://selection`, `studio://survey`
- **Resource Templates**: `studio://scripts/{path}`, `studio://instance/{path}`
- **Prompts**: `survey_and_architecture`, `safe_module_edit`, `automated_playtest_debug`, `sync_repo_package`

---

## CLI & HANDS MODE Quick Reference (`arena_studio.py` / `ab.ps1`)

```powershell
.\ab.ps1 health                                             # Bridge + connected peers
.\ab.ps1 ping                                               # Verify pluginVersion "4.0.0" & latency
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

Run the full offline & stateful mock Studio test suite (verifies FNV-1a exact vectors, `luau_check`, token recovery, Streamable HTTP MCP, `stdio` NDJSON + LSP framing, Read-Only Inspector enforcement, UTF-8 round-trip, and all 68 registered tools including live package sync):

```bash
python3 roblox-bridge/test_bridge_mcp.py
```

---

## Files

| File | Role |
|---|---|
| `setup.py` | Installs `ArenaBridge.lua` v4.0.0, recovers/manages `bridge.token`, writes `.mcp.json`, launches server/tunnel |
| `mcp_server.py` | Standalone `stdio` MCP server (auto-attaches to `server.py` or starts an embedded bridge thread) |
| `mcp_core.py` | Zero-dependency MCP protocol engine, 68 tool schemas/handlers, resources, prompts, `luau_check`, `drift_and_sync` |
| `server.py` | Multi-peer long-poll HTTP bridge + `/mcp` Streamable HTTP endpoint + snapshot store + live web dashboard |
| `ArenaBridge.lua` | Roblox Studio local plugin v4.0.0 (`edit` + `server` + silent `client` RPC bridge) |
| `arena_studio.py` | UTF-8-safe CLI covering all bridge & MCP operations |
| `ab.ps1` | Windows PowerShell wrapper with automatic token lookup and UTF-8 console setup |
| `mock_studio.py` | Stateful in-memory Studio v4.0.0 simulator (including simulated Play Server/Client peers & undo/redo) |
| `test_bridge_mcp.py` | End-to-end automated test suite |
