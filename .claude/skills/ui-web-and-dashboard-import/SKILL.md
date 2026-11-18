---
name: ui-web-and-dashboard-import
description: Import UI directly from Web HTML/CSS/Tailwind/SVG/JSON or from the Control Center Dashboard into Roblox Studio ScreenGui and SurfaceGui trees.
---

# Direct Web & Control Center Dashboard UI Import Skill

**IMPORTANT FOR EVERY AGENT**: You do **not** have to hand-code every Roblox `ScreenGui` from scratch in raw Luau. UI layouts, icons, and theme packs can be **imported directly from the Web (HTML/CSS/Tailwind/SVG/JSON) and from the Control Center Dashboard** into Roblox Studio in a single step!

## 1. Three Direct UI Import Pathways

### Pathway A: Import Web HTML / CSS / Tailwind / JSON Directly (`import_ui_from_web`)
- Call the MCP tool **`import_ui_from_web`** with:
  - `html`: Any HTML / Tailwind / inline-CSS snippet (`<div class="card">...</div>`, `<button>`, `<img>`, `<p>`, flex/grid containers) or a `preset` (`"hud_minimal_bar"`, `"shop_icon_grid"`, `"boss_health_banner"`, `"dialogue_choice_modal"`, `"inventory_slot_dock"`).
  - `deSlop`: Defaults to `true` (automatically strips em-dashes, AI filler connectors, and truncates bloated text walls into crisp visual-first labels).
  - `installInStudio`: Set `true` to build the converted `ScreenGui` (`UDim2` scale, `UICorner`, `UIStroke`, `UIListLayout` / `UIGridLayout`) directly in `StarterGui`!
- In the **Control Center Web Dashboard (`Tab 2: Image Importer & Studio Kit` and `Tab 8: Studio Plugin & Playtest v6`)**, operators and agents can paste any Web HTML/CSS snippet and click **Import Web UI to Studio** (`POST /api/ui/import-web`).

### Pathway B: Import Web / Repo Images & 4,811 Cloud Icons Directly (`import_image`, `search_cloud_icons`)
- Search **4,811 ready-to-use `rbxassetid://` icons** via `search_cloud_icons(query="...", pack="stud" | "lucide" | "lucide_fill")` — zero file uploads needed.
- Or import any PNG/JPG from disk, URL, or the 3,705-image kit catalog via `import_image(..., targetPath="StarterGui", applyMode="screen_gui")`.

### Pathway C: Generate Cohesive Multi-Device Theme Packs & `.rbxmx` UI (`generate_ui_theme_pack`, `import_rbxmx`)
- Call `generate_ui_theme_pack(theme="HorrorBloodMoon" | "CyberpunkNeon" | "FantasyGold" | "MinimalDark" | "StudRetro", installInStudio=true)` or `install_template` / `spawn_prefab` for production-tested HUDs.

## 2. Visual Clarity Rules for Imported UI
1. **Icon + Short Label (`<= 3 Words`)**: Every interactive button should pair an `ImageLabel` icon (`search_cloud_icons`) with a concise 1–3 word action verb (`"Upgrade"`, `"Equip"`, `"450 Gold"`), never a sentence.
2. **Scale-Based `UDim2` + `UIAspectRatioConstraint`**: Always size root HUD cards in scale (`UDim2.fromScale`) and verify across Desktop (`1920x1080`), Tablet (`1024x768`), and Mobile (`844x390`) with `audit_ui_layout`.

## Verification
- Run `import_ui_from_web`, then call `audit_ui_layout` and `de_slop_audit` to confirm 0 overlaps, 0 off-screen overflows, and 100/100 visual clarity.
