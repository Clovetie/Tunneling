# AGENTS.md — Roblox Studio MCP, Open-Source UI Kit & Direct Image Pipeline Guide

This repository provides a complete, zero-dependency **Model Context Protocol (MCP) Server, Multi-DataModel Studio Bridge, Open-Source Game UI & Asset Vault, Direct Image Importer, Reusable Luau Kit, Prefab Library, and Agent Skills Pack** for building, styling, and playtesting any Roblox Studio experience.

---

## 1. Repository Layout

| Path | Purpose |
|---|---|
| `roblox-bridge/` | Primary MCP Server (`mcp_server.py`, `mcp_core.py`), HTTP Bridge & Control Center UI (`server.py` v3.2), Direct Image Engine (`image_pipeline.py`), Studio Local Plugin (`ArenaBridge.lua`), CLI (`arena_studio.py`, `ab.ps1`), Stateful Simulator (`mock_studio.py`), and Test Suite (`test_bridge_mcp.py`) |
| `roblox-kit/assets/images/` | **249 versioned retina PNGs (~1.6 MB total)** across 11 packs: genuine **Kenney CC0 1.0** UI buttons/bars/checkboxes (`kenney_ui/`), RPG 9-slice borders (`kenney_fantasy/`), Sci-Fi frames (`kenney_scifi/`), Adventure banners (`kenney_adventure/`), tintable White 2x Icons (`kenney_icons/`), PC/Mouse/Xbox/Mobile Input Prompts (`kenney_inputs/`), Cursors (`kenney_cursors/`), Alpha Particle Sprites (`kenney_vfx/`), plus procedural icons, sprites, and seamless textures (`ui/`, `vfx/`, `textures/`) |
| `roblox-kit/modules/` | **12 compiler-verified Luau `ModuleScript`s** (`StudUI`, `StudMotion`, `CheckUIIcons` [1,022 live `rbxassetid://` icons], `FormatNumber`, `DataStoreSafe`, `Maid`, `Signal`, `NetGuard`, `TagBinder`, `SpringUI`, `StateStore`, `Assets`) |
| `roblox-kit/templates/` | **6 complete 1-click UI screen & multi-script economy templates** (`full_stud_ui_suite`, `stud_shop_window`, `stud_hud_shell`, `stud_daily_rewards`, `stud_rebirth_window`, `stud_rarity_index`) installed via `install_template` |
| `roblox-kit/prefabs/` | **14 ready-to-spawn 3D, Kenney 9-Slice UI, VFX, and Lighting JSON prefabs** (`spawn_prefab` MCP tool) |
| `.claude/skills/` | **7 specialized Roblox Studio agent skills** (`roblox-place-builder`, `roblox-luau-systems`, `roblox-ui-and-images`, `roblox-stud-ui-design` + full reference docs, `roblox-vfx-lighting-audio`, `roblox-playtest-qa`, `roblox-optimization-security`) |
| `THIRD_PARTY_LICENSES.md` | Full open-source attributions & license texts (CC0 1.0 Public Domain & MIT) |

---

## 2. Core Capabilities Available to Agents (45 MCP Tools + CLI)

1. **Open-Source UI Components, 1,022 Cloud Icons & Full Screen Templates (`install_template`, `search_cloud_icons`, `StudUI`, `StudMotion`)**:
   - Call `search_cloud_icons(query="...")` to search **1,022 verified live Roblox `rbxassetid://` icons** that render immediately in Studio **and** published multiplayer servers with zero uploads.
   - Call `install_template(template="full_stud_ui_suite")` (or individual screens like `stud_shop_window`, `stud_hud_shell`, `stud_daily_rewards`, `stud_rebirth_window`, `stud_rarity_index`) to generate real editable `ScreenGui` instances in `StarterGui`, wire `StudMotion` hover/press/blur/FOV controllers, and install server economy remotes in one step.
2. **Direct Image Import (`import_image`, `import_images_batch`, `generate_procedural_image`)**:
   - Import any local file (`.png`, `.jpg`, `.bmp`), `base64Data` payload (works across cloud tunnels!), remote URL, curated Kenney CC0 repo PNG (`repoAsset="kenney_ui/blue_btn_depth_gloss.png"`), or procedural preset directly into Roblox Studio (`rbxasset://arena_assets/...` + live `EditableImage` + optional Studio/Open Cloud `rbxassetid://`).
3. **Reusable Kit, Prefabs & Skills (`browse_kit`, `install_kit_module`, `spawn_prefab`)**:
   - Spawn Kenney 9-slice Shop/Fantasy Dialog/Sci-Fi Visor/Input Legend GUIs, campfires, portals, lighting presets, or all 12 shared Luau modules in one undoable operation.
4. **Full Place Exploration, Surgical Editing, Sync & Playtesting**:
   - Multi-DataModel targeting (`dm = "edit" | "server" | "client"`), `survey_place`, `search_game_tree`, `script_read`, `multi_edit`, `script_grep`, `luau_check`, `drift_and_sync`, `manage_snapshots`, and `run_playtest_scenario`.

---

## 3. Golden Engineering Rules for Every Agent Session

1. **Ground truth first**: Call `get_studio_state` and `survey_place(includeSource=false)` before creating or editing instances.
2. **Pre-flight every script**: Always validate Luau with `luau_check` (`0` syntax errors, `0` undefined globals) before writing to Studio.
3. **Persist edits in `dm="edit"`**: Edits made in `dm="server"` or `dm="client"` during Play Solo are runtime-only and disappear on Stop.
4. **Bust cached `ModuleScript`s**: Pass `replaceInstance=true` when updating already-required `ModuleScript`s, or test isolated module logic with `require_fresh`.
5. **Verify in Play Mode**: Run `run_playtest_scenario` and check `get_console_output` before finishing a feature.
