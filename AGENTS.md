# AGENTS.md — Roblox Studio MCP, Image Pipeline & Developer Kit Guide

This repository provides a complete, zero-dependency **Model Context Protocol (MCP) Server, Multi-DataModel Studio Bridge, Direct Image Importer, Reusable Luau Kit, Prefab Library, and Agent Skills Pack** for building and testing any Roblox Studio experience.

---

## 1. Repository Layout

| Path | Purpose |
|---|---|
| `roblox-bridge/` | Primary MCP Server (`mcp_server.py`, `mcp_core.py`), HTTP Bridge & Control Center UI (`server.py`), Direct Image Engine (`image_pipeline.py`), Studio Local Plugin (`ArenaBridge.lua`), CLI (`arena_studio.py`, `ab.ps1`), Stateful Simulator (`mock_studio.py`), and Test Suite (`test_bridge_mcp.py`) |
| `roblox-kit/assets/images/` | Versioned repository PNG library (`ui/`, `vfx/`, `textures/`) + `asset-lock.json` |
| `roblox-kit/modules/` | Drop-in zero-warning Luau `ModuleScript`s (`Maid`, `Signal`, `NetGuard`, `TagBinder`, `SpringUI`, `StateStore`, `Assets`) |
| `roblox-kit/prefabs/` | Ready-to-spawn 3D, UI, VFX, and Lighting JSON prefabs (`spawn_prefab` MCP tool) |
| `.claude/skills/` | 6 specialized Roblox Studio agent skills (`roblox-place-builder`, `roblox-luau-systems`, `roblox-ui-and-images`, `roblox-vfx-lighting-audio`, `roblox-playtest-qa`, `roblox-optimization-security`) |

---

## 2. Core Capabilities Available to Agents (43 MCP Tools + CLI)

1. **Direct Image Import (`import_image`, `import_images_batch`, `generate_procedural_image`)**:
   - Import any local file (`.png`, `.jpg`, `.bmp`, etc.), `base64Data` payload (works across cloud tunnels!), remote URL, repo asset, or procedural icon/texture directly into Roblox Studio.
   - Zero-key instant mode writes into `roblox-kit/assets/images/` + local Studio `content/arena_assets/` (`rbxasset://arena_assets/...`) **and** supports live `EditableImage` RGBA pixel transfer + optional Studio `CreateAssetAsync` or Roblox Open Cloud `rbxassetid://` publishing.
   - Can directly apply or create `ImageLabel`, `ImageButton`, `Decal`, `Texture`, `BillboardGui`, `SurfaceGui`, or `ScreenGui` instances in one step.
2. **Reusable Kit, Prefabs & Skills (`browse_kit`, `install_kit_module`, `spawn_prefab`)**:
   - Inspect any skill/prefab/module/asset, inject `Maid`/`Signal`/`NetGuard`/`TagBinder`/`SpringUI`/`StateStore`/`Assets` into `ReplicatedStorage.Shared`, or spawn complete lighting/UI/world prefabs in one undoable operation.
3. **Full Place Exploration, Surgical Editing, Sync & Playtesting**:
   - Multi-DataModel targeting (`dm = "edit" | "server" | "client"`), `survey_place`, `search_game_tree`, `script_read`, `multi_edit`, `script_grep`, `luau_check`, `drift_and_sync`, `manage_snapshots`, and `run_playtest_scenario`.

---

## 3. Golden Engineering Rules for Every Agent Session

1. **Ground truth first**: Call `get_studio_state` and `survey_place(includeSource=false)` before creating or editing instances.
2. **Pre-flight every script**: Always validate Luau with `luau_check` (`0` syntax errors, `0` undefined globals) before writing to Studio.
3. **Persist edits in `dm="edit"`**: Edits made in `dm="server"` or `dm="client"` during Play Solo are runtime-only and disappear on Stop.
4. **Bust cached `ModuleScript`s**: Pass `replaceInstance=true` when updating already-required `ModuleScript`s, or test isolated module logic with `require_fresh`.
5. **Verify in Play Mode**: Run `run_playtest_scenario` and check `get_console_output` before finishing a feature.
