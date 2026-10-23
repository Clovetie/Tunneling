# AGENTS.md — Roblox Studio MCP, Open-Source UI Kit & Direct Image Pipeline Guide

This repository provides a complete, zero-dependency **Model Context Protocol (MCP) Server, Multi-DataModel Studio Bridge, Open-Source Game UI & Asset Vault, Direct Image Importer, Reusable Luau Kit, Prefab Library, and Agent Skills Pack** for building, styling, and playtesting any Roblox Studio experience.

---

## 1. Repository Layout

| Path | Purpose |
|---|---|
| `roblox-bridge/` | Primary MCP Server (`mcp_server.py`, `mcp_core.py`), HTTP Bridge & Visual Control Center UI (`server.py` v3.4), 3D Viewport + HUD Software Rasterizer (`viewport_renderer.py`), 3D Physics/Geometry Auditor (`scene_auditor.py`), Direct Image/Audio Engine (`image_pipeline.py`), `.rbxmx`/`.rbxlx` XML Pipeline (`rbxmx_pipeline.py`), Multi-Device UI Linter (`ui_linter.py`), Studio Plugin (`ArenaBridge.lua`), CLI (`arena_studio.py`, `ab.ps1`), Stateful Simulator (`mock_studio.py`), and Test Suite (`test_bridge_mcp.py`) |
| `roblox-kit/assets/images/` & `audio/` | **405 versioned retina PNGs** across **18 CC0/procedural packs** (`kenney_ui`, `kenney_fantasy`, `kenney_scifi`, `kenney_adventure`, `kenney_medals`, `kenney_minimap`, `kenney_emotes`, `kenney_rpg_combat`, `kenney_controls`, `kenney_pets`, `kenney_prototype`, `kenney_icons`, `kenney_inputs`, `kenney_cursors`, `kenney_vfx`, `ui`, `vfx`, `textures`) + **45 curated CC0 Kenney `.wav` sound effects** locked in `asset-lock.json` & `Assets.lua` |
| `roblox-kit/modules/` | **19 compiler-verified Luau `ModuleScript`s** (`StudUI`, `StudMotion`, `CheckUIIcons` [1,022 glossy 3D icons], `ModernUI`, `WidgetKit`, `OverheadHUD`, `MinimapRadar`, `LucideIcons` [1,894 outline icons], `LucideFilledIcons` [1,895 filled icons], `SoundFX`, `FormatNumber`, `DataStoreSafe`, `Maid`, `Signal`, `NetGuard`, `TagBinder`, `SpringUI`, `StateStore`, `Assets`) |
| `roblox-kit/systems/` | **14 production server+shared+client game systems** (`core/{data [ProfileStore], economy, format, remotes}`, `ui/kit`, `liveops/{badges, codes, group-rewards, playtime-gifts, social-prompts}`, `monetization/{gamepasses, offline-earnings, paid-random-items, receipts}`) installed via `install_dropin_system` |
| `roblox-kit/templates/` | **6 complete 1-click UI screen & multi-script economy templates** (`full_stud_ui_suite`, `stud_shop_window`, `stud_hud_shell`, `stud_daily_rewards`, `stud_rebirth_window`, `stud_rarity_index`) installed via `install_template` |
| `roblox-kit/prefabs/` & `models/` | **20 ready-to-spawn JSON prefabs** (`spawn_prefab`) + **20 standalone `.rbxmx` XML models** + full **`.rbxlx` place export/import** (`export_place_rbxlx` / `import_place_rbxlx`) |
| `.claude/skills/` | **16 specialized Roblox Studio agent skills** (automatically exposed as `skill://<name>` MCP resources) |
| `THIRD_PARTY_LICENSES.md` | Full commercial-safe open-source attributions & license texts (CC0 1.0 Public Domain, MIT, ISC & Apache-2.0) |

---

## 2. Core Capabilities Available to Agents (55 MCP Tools + CLI)

1. **Multimodal 3D Place + HUD Viewport Vision & 3D Scene Physics QA (`capture_viewport`, `audit_3d_scene`)**:
   - Call **`capture_viewport`** (`perspective`, `iso_ne`, `iso_nw`, `top_blueprint`, `front`) to receive a real **`image/png` MCP vision block** showing shaded 3D `Workspace` geometry, world grid, callouts, and `StarterGui` HUD widgets.
   - Call **`audit_3d_scene`** (`autoFixUnanchored=true, snapFloating=true`) to detect and repair unanchored parts, floating geometry gaps, blocked `SpawnLocation` clearance, and Z-fighting coplanar faces.
2. **Triple Cloud Icon Engine (`4,811` Icons) & Multi-Viewport UI Linter (`StudUI`, `ModernUI`, `WidgetKit`, `search_cloud_icons`, `audit_ui_layout`)**:
   - Search **4,811 verified live Roblox `rbxassetid://` icons** across **`stud` (`1,022`)**, **`lucide` (`1,894` outline)**, and **`lucide_fill` (`1,895` filled)** — zero uploads required!
   - Run **`audit_ui_layout`** across **Desktop (`1920x1080`)**, **Tablet (`1024x768`)**, and **Mobile (`844x390`)**.
3. **Standalone `.rbxlx` Place Authoring, Open Cloud API, 14 Drop-In Systems, Images, Audio & `.rbxmx` Models**:
   - Export or generate complete standalone `.rbxlx` place files (`export_place_rbxlx` / `import_place_rbxlx`), publish or query live DataStores/Messaging via `open_cloud_action`, and install any of 14 `ProfileStore`/LiveOps/Monetization systems (`install_dropin_system`).
4. **Full Place Exploration, Surgical Editing, Sync & Playtesting**:
   - Multi-DataModel targeting (`dm = "edit" | "server" | "client"`), `survey_place`, `search_game_tree`, `script_read`, `multi_edit`, `script_grep`, `luau_check`, `drift_and_sync`, `manage_snapshots`, and `run_playtest_scenario`.

---

## 3. Golden Engineering Rules for Every Agent Session

1. **Ground truth first**: Call `get_studio_state` and `survey_place(includeSource=false)` before creating or editing instances.
2. **Pre-flight every script**: Always validate Luau with `luau_check` (`0` syntax errors, `0` undefined globals) before writing to Studio.
3. **Persist edits in `dm="edit"`**: Edits made in `dm="server"` or `dm="client"` during Play Solo are runtime-only and disappear on Stop.
4. **Bust cached `ModuleScript`s**: Pass `replaceInstance=true` when updating already-required `ModuleScript`s, or test isolated module logic with `require_fresh`.
5. **Verify in Play Mode**: Run `run_playtest_scenario` and check `get_console_output` before finishing a feature.
