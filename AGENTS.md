# AGENTS.md — Roblox Studio MCP, Open-Source UI Kit & Direct Image Pipeline Guide

This repository provides a complete, zero-dependency **Model Context Protocol (MCP) Server, Multi-DataModel Studio Bridge, Open-Source Game UI & Asset Vault, Direct Image Importer, Reusable Luau Kit, Prefab Library, and Agent Skills Pack** for building, styling, and playtesting any Roblox Studio experience.

---

## 1. Repository Layout

| Path | Purpose |
|---|---|
| `roblox-bridge/` | Primary MCP Server (`mcp_server.py`, `mcp_core.py`), HTTP Bridge & Visual Control Center UI (`server.py` v3.3), Direct Image/Audio Engine (`image_pipeline.py`), `.rbxmx` XML Parser/Serializer (`rbxmx_pipeline.py`), Multi-Device UI Linter (`ui_linter.py`), Studio Plugin (`ArenaBridge.lua`), CLI (`arena_studio.py`, `ab.ps1`), Stateful Simulator (`mock_studio.py`), and Test Suite (`test_bridge_mcp.py`) |
| `roblox-kit/assets/images/` & `audio/` | **249 versioned retina PNGs (~1.6 MB)** across 11 CC0/procedural packs + **45 curated CC0 Kenney `.wav` sound effects (~910 KB)** locked in `asset-lock.json` & `Assets.lua` |
| `roblox-kit/modules/` | **15 compiler-verified Luau `ModuleScript`s** (`StudUI`, `StudMotion`, `CheckUIIcons` [1,022 glossy 3D icons], `ModernUI` [minimalist dark frosted UI kit], `LucideIcons` [565 clean vector `rbxassetid://` icons], `SoundFX`, `FormatNumber`, `DataStoreSafe`, `Maid`, `Signal`, `NetGuard`, `TagBinder`, `SpringUI`, `StateStore`, `Assets`) |
| `roblox-kit/systems/` | **14 production server+shared+client game systems** (`core/{data [ProfileStore], economy, format, remotes}`, `ui/kit`, `liveops/{badges, codes, group-rewards, playtime-gifts, social-prompts}`, `monetization/{gamepasses, offline-earnings, paid-random-items, receipts}`) installed via `install_dropin_system` |
| `roblox-kit/templates/` | **6 complete 1-click UI screen & multi-script economy templates** (`full_stud_ui_suite`, `stud_shop_window`, `stud_hud_shell`, `stud_daily_rewards`, `stud_rebirth_window`, `stud_rarity_index`) installed via `install_template` |
| `roblox-kit/prefabs/` & `models/` | **14 ready-to-spawn JSON prefabs** (`spawn_prefab`) + **14 standalone `.rbxmx` XML models** (`import_rbxmx` / `export_rbxmx`) |
| `.claude/skills/` | **14 specialized Roblox Studio agent skills** (`roblox-place-builder`, `roblox-luau-systems`, `roblox-ui-and-images`, `roblox-stud-ui-design`, `ui-layout-audit-and-rbxmx`, `roblox-monetization-and-policy`, `roblox-player-data`, `roblox-liveops-retention`, `roblox-multiplayer-security`, `roblox-ui-ux`, `roblox-performance-memory`, `roblox-game-architecture`, `roblox-vfx-lighting-audio`, `roblox-playtest-qa`, `roblox-optimization-security`) |
| `THIRD_PARTY_LICENSES.md` | Full open-source attributions & license texts (CC0 1.0 Public Domain, MIT, ISC & Apache-2.0) |

---

## 2. Core Capabilities Available to Agents (50 MCP Tools + CLI)

1. **Dual UI Design Systems, 1,587 Cloud Icons & Multi-Viewport UI Linter (`StudUI`, `ModernUI`, `search_cloud_icons`, `audit_ui_layout`)**:
   - Search **1,587 verified live Roblox `rbxassetid://` icons** across **`stud` (1,022 glossy 3D icons)** and **`lucide` (565 flat crisp vector icons)** — zero uploads required!
   - Run **`audit_ui_layout`** across **Desktop (`1920x1080`)**, **Tablet (`1024x768`)**, and **Mobile (`844x390`)** to compute exact pixel bounds and catch viewport overflow, tiny touch targets, unwrapped offsets, and clipped text before shipping.
2. **14 Drop-In LiveOps, Monetization & `ProfileStore` Data Systems (`install_dropin_system`)**:
   - Install `core/data` (`ProfileStore`), `core/economy`, `liveops/codes`, `liveops/playtime-gifts`, `liveops/group-rewards`, `liveops/social-prompts`, `liveops/badges`, `monetization/offline-earnings`, `monetization/gamepasses`, `monetization/paid-random-items` (`PolicyService` compliant!), and `monetization/receipts` in one call.
3. **Direct Image, CC0 Audio & `.rbxmx` Model Pipeline (`import_image`, `import_audio`, `import_rbxmx`, `export_rbxmx`)**:
   - Import PNG/JPG/BMP images, 45 built-in CC0 `.wav` SFX (or custom audio), and community/repo `.rbxmx` XML models directly into Roblox Studio (`stripScripts=true` option sanitizes external models automatically).
4. **Full Place Exploration, Surgical Editing, Sync & Playtesting**:
   - Multi-DataModel targeting (`dm = "edit" | "server" | "client"`), `survey_place`, `search_game_tree`, `script_read`, `multi_edit`, `script_grep`, `luau_check`, `drift_and_sync`, `manage_snapshots`, and `run_playtest_scenario`.

---

## 3. Golden Engineering Rules for Every Agent Session

1. **Ground truth first**: Call `get_studio_state` and `survey_place(includeSource=false)` before creating or editing instances.
2. **Pre-flight every script**: Always validate Luau with `luau_check` (`0` syntax errors, `0` undefined globals) before writing to Studio.
3. **Persist edits in `dm="edit"`**: Edits made in `dm="server"` or `dm="client"` during Play Solo are runtime-only and disappear on Stop.
4. **Bust cached `ModuleScript`s**: Pass `replaceInstance=true` when updating already-required `ModuleScript`s, or test isolated module logic with `require_fresh`.
5. **Verify in Play Mode**: Run `run_playtest_scenario` and check `get_console_output` before finishing a feature.
