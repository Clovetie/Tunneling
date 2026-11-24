# Arena ↔ Roblox Studio MCP Bridge, Open-Source Kit & Tunnel Platform (`v6.1.0`)

A zero-dependency **Model Context Protocol (MCP) Server** (`stdio` + `Streamable HTTP`), multi-DataModel **Roblox Studio Plugin** (`ArenaBridge.lua` `v6.1.0` with in-Studio `DockWidgetPluginGui` dashboard & bulletproof playtest closing), supervised **Cloudflare / ngrok Tunnel Manager**, **Multi-Agent Coordination Bus**, **4,320-Asset Commercial-Safe Roblox Kit**, and **Luau Static Analysis Suite**.

---

## Quick Start

### 1. Install the Studio Plugin & Start the Bridge

```bash
cd roblox-bridge
python3 setup.py --write-mcp
```

- Mints (or recovers from an already-installed `ArenaBridge.lua`) your shared secret in `roblox-bridge/bridge.token` (`0600`).
- Installs `ArenaBridge.lua` (`v6.1.0`) into your OS Roblox Studio `Plugins` directory (or download it pre-configured from `GET /api/plugin/download`).
- Writes `.mcp.json` (`--mcp-transport stdio` by default, or `--mcp-transport http` for Streamable HTTP).
- Starts `server.py` (`v6.1.0`) on `http://127.0.0.1:8077` serving the Web Control Center, REST API, and Streamable HTTP MCP (`POST /mcp`).

### 2. Expose Over a Supervised Public Tunnel (Optional)

```bash
# Quick tunnel (auto-falls back from QUIC to HTTP/2 when UDP :7844 is blocked)
python3 roblox-bridge/setup.py --tunnel --tunnel-protocol auto

# Named Cloudflare tunnel with a permanent hostname
python3 roblox-bridge/setup.py --tunnel --tunnel-token "$CF_TUNNEL_TOKEN" --public-url https://mcp.example.com
```

---

## Key Capabilities (`v6.1.0`)

| Area | Highlights |
|---|---|
| **Dual-Era MCP (`2026-07-28`, `2025-06-18`, `2025-03-26`, `2024-11-05`)** | Stateless Streamable HTTP (`POST /mcp`) + `stdio` (`mcp_server.py`); `server/discover`, SEP-2243 Base64 `=?base64?...?=` header codec & `-32020` validation, `completion/complete` (`completions` capability), cursor pagination on list methods, `resource_link` content blocks, `tasks/*`, `subscriptions/listen` SSE with Cloudflare-safe `: connected` / `: keepalive` flushes, and MRTR human-in-the-loop confirmations (`--require-confirm`). |
| **Studio Plugin v6 (`ArenaBridge.lua` `v6.1.0`) & Playtest Lifecycle** | Built-in Roblox Studio `DockWidgetPluginGui` Dashboard (`ArenaBridgeDashboardV6`) + `Arena v6 UI` toolbar toggle; bulletproof playtest closing (`StudioTestService:EndTest` + `LeaveTest` fallback, idempotent already-stopped handling, automatic `__ArenaTestRunner_*` cleanup, dual `edit`+`server` stop routing, and `/api/poll?goodbye=1` instant ghost-peer eviction); `plugin_info` telemetry; and Control Center Tab 8 (`Studio Plugin & Playtest v6`). |
| **Zero-Auth `.well-known` Discovery** | SEP-1649 MCP Server Cards at `GET /.well-known/mcp/server-card.json` & `/.well-known/mcp.json` (plus `mcp://server-card.json`), and RFC 9728 OAuth 2.0 Protected Resource Metadata at `GET /.well-known/oauth-protected-resource/mcp` with `WWW-Authenticate` challenges on `401`/`403`. |
| **HTTP & Tunnel Hardening** | Browser `Origin` validation (DNS-rebinding protection), bounded `Content-Length` enforcement (`BRIDGE_MAX_BODY_BYTES`), `X-Content-Type-Options: nosniff`, Cloudflare QUIC → `http2` automatic fallback (`--tunnel-protocol auto\|http2\|quic`), strict `/api/health` JSON probe (`tunnel_probe`), and non-leaking `lastKnownUrl` state. |
| **103 Roblox Studio Tools, 14 Resources, 10 Prompts** | Multi-DataModel (`edit`, `server`, `client`) script sync & surgical `multi_edit`, Z-buffered 3D software viewport + HUD rasterizer (`capture_viewport`, `render_offline`), 3D physics/scene auditor (`audit_3d_scene`), multi-device UI linter (`audit_ui_layout`), VirtualInput/DeviceSimulator/`StudioTestService` playtesting, plugin & playtest lifecycle controller (`manage_plugin_and_playtest`), RemoteEvent security auditor (`audit_network_security`), 3D camera cutscene builder (`build_cutscene_sequence`), responsive UI theme pack generator (`generate_ui_theme_pack`), procedural 3D dungeon layout generator (`generate_dungeon_layout`), 1-click gameplay mechanic builder (`build_gameplay_mechanic`), atmosphere/weather post-FX configurator (`configure_atmosphere_fx`), procedural 16-bit PCM WAV synthesizer (`synthesize_sound_effect`), `.rbxmx`/`.rbxlx` pipelines, Open Cloud actions, and the v6.1.0 kit auditors (`audit_kit_integrity`, `audit_asset_references`, `run_testez_suite`). |
| **Commercial-Safe Roblox Kit (`roblox-kit/`)** | **4,320 hash-verified CC0/MIT/ISC/Apache-2.0 assets** (`python3 -B tools/verify_licences.py`), **35 Luau `ModuleScript`s** (`CutsceneDirector`, `NetworkGuard`, `DialogueSystem`, `StateMachine`, `CooldownManager`, `LootTable`, `QuestTracker`, `CombatPipeline`, `DungeonGenerator`, `StudUI`, `ModernUI`, `WidgetKit`, `FeatureFlags`, `Throttle`, `4,811` cloud icons; indexed in `roblox-kit/modules/README.md`), **27 drop-in game systems**, **6 UI templates**, **36 prefabs**, **38 procedural PNG shaders**, and **12 procedural WAV synth presets**. |

---

## Repository Layout

| Directory | Contents |
|---|---|
| `roblox-bridge/` | MCP server (`mcp_core.py`, `mcp_server.py`), HTTP bridge & Web UI (`server.py`), tunnel supervisor (`tunnel_manager.py`), MRTR gate (`confirm_gate.py`), scoped tokens (`auth_core.py`), multi-agent bus (`bus_core.py`), metrics/SSE/SHA-256 audit log (`telemetry.py`), Studio plugin (`ArenaBridge.lua`), CLI (`arena_studio.py`, `ab.ps1`), mock Studio (`mock_studio.py`), and regression suite (`test_bridge_mcp.py`). |
| `roblox-kit/` | Verified asset registry (`4,320` files), 35 Luau gameplay/UI/utility modules, 27 server/client systems, 6 UI templates, and 36 prefabs (+ 20 `.rbxmx` models). |
| `jobs/` | Operator benchmarks, live Studio acceptance checks, and crash-recovery drills (`live_acceptance.py`, `drill_studio_kill.py`, `measure_tunnel_latency.py`). |
| `.claude/skills/` | Bundled Roblox Studio engineering skills exposed offline via `search_skills` / `apply_skill` and `skill://<name>`. |

---

## Verification & Test Suite

Everything runs offline with Python 3.10+ stdlib only. `make` wraps the same commands
CI runs (`make help` lists them, `make ci` runs all of them in order):

```bash
make test        # full MCP + bridge + mock Studio regression suite (236 tests, ~255 s)
make test-fast   # every test, minus the two nested self-test suite runs (~71 s)
make test-unit   # offline classes only: no port is bound, 63 tests in ~2 s
make lint        # ruff + the blind-except ratchet (tools/check_lint.sh)
make luau        # undefined-global scan over the bridge and the kit's Luau
make verify      # kit asset licence manifest
make kit         # committed asset manifest matches the tree (writes nothing)
make docs-check  # docs/FACTS.md and every doc claim agree with the tree
make harness     # dashboard XSS/CSP contract (needs node)
make secrets     # nothing credential-shaped is tracked
```

The same gates without make:

```bash
python3 -B roblox-bridge/test_bridge_mcp.py          # add --fast or --unit
bash tools/check_lint.sh
bash roblox-bridge/tools/check_globals.sh roblox-bridge
python3 -B tools/verify_licences.py
python3 roblox-bridge/image_pipeline.py --check
python3 -B tools/emit_facts.py --check
node roblox-bridge/tools/dashboard_harness.js
bash tools/check_secrets.sh
```

Every count in those docs is generated, not typed: `python3 -B tools/emit_facts.py`
rewrites [`docs/FACTS.md`](docs/FACTS.md) from `TOOL_SPECS`, `RESOURCES`, the kit
directories and `registry.json`, and `--check` fails when this README, `CONNECT.md`,
`AGENTS.md`, `roblox-bridge/README.md` or `roblox-bridge/docs/OPS.md` disagrees with it.

## Licence

This repository's own code — the bridge (`roblox-bridge/`), the operator jobs (`jobs/`),
the tooling (`tools/`) and the docs — is **MIT licensed**: see [`LICENSE`](LICENSE). The
bundled kit and skills keep their own per-asset licences (CC0 1.0, MIT, ISC, Apache-2.0),
recorded in [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md) and per file in
`roblox-kit/assets/registry.json`; `python3 -B tools/verify_licences.py` checks them.

For full architecture and operational details, see [`AGENTS.md`](AGENTS.md), [`CONNECT.md`](CONNECT.md), [`roblox-bridge/README.md`](roblox-bridge/README.md), and [`roblox-bridge/docs/OPS.md`](roblox-bridge/docs/OPS.md).
