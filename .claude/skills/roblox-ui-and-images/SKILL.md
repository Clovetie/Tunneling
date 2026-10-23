---
name: roblox-ui-and-images
description: Importing PNG/JPG/base64/procedural images directly into Roblox Studio and designing responsive, tactile ScreenGui, SurfaceGui, and BillboardGui interfaces.
---

# Roblox UI & Direct Image Import Skill

Use this skill whenever creating UI, HUDs, icons, shop cards, world signage, decals, or custom textures.

## Direct Image Import Pipeline (`import_image` & `import_images_batch`)
Agents can import images directly into Roblox Studio **without leaving the workflow**:

1. **Instant Zero-Key Local Import (Default — `<50ms`, No Moderation Delay)**:
   - Call `import_image` with ANY of:
     - `filePath`: path to a local `.png` / `.jpg` / `.bmp` (e.g. from an AI image tool or disk)
     - `base64Data`: raw PNG/JPEG base64 string (ideal when the agent runs in a cloud sandbox over a tunnel!)
     - `imageUrl`: remote HTTPS image URL
     - `repoAsset`: built-in asset in `roblox-kit/assets/images/` (e.g. `"ui/icon_coin.png"`)
     - `proceduralPreset`: synthesize a crisp transparent icon or tileable surface texture (`icon_coin`, `icon_gem`, `icon_shield`, `icon_sword`, `icon_heart`, `icon_star`, `icon_bolt`, `icon_lock`, `icon_gear`, `icon_crown`, `panel_glass_9slice`, `crosshair_dot`, `particle_soft_glow`, `particle_spark_star`, `grid_dark_1m`, `scifi_panel_tile`, etc.)
   - Automatically saves a tracked copy into `roblox-kit/assets/images/`, updates `roblox-kit/modules/Assets.lua`, installs into local Studio `content/arena_assets/` (`rbxasset://arena_assets/<path>`), **and** supports live `EditableImage` RGBA pixel hydration (`useEditableImage=true`)!
2. **Auto-Apply to Scene or UI in One Step**:
   - Pass `targetPath` + `applyMode`:
     - `"auto"` — sets `.Image` / `.ImageContent` on `ImageLabel`/`ImageButton`, or `.Texture` on `Decal`/`Texture`/`ParticleEmitter`
     - `"decal"` — creates/updates a `Decal` on a `Part`
     - `"billboard"` — creates a floating 3D `BillboardGui` icon badge above a `Part`/`Model`
     - `"surface_gui"` — creates a sharp `PixelsPerStud` `SurfaceGui` poster on a `Part`
     - `"screen_gui"` — creates a preview `ImageLabel` inside `StarterGui`
3. **Publishing Permanent Production `rbxassetid://` IDs**:
   - For live multiplayer publishing, pass `publishVia="studio"` (uses Studio's logged-in `AssetService:CreateAssetAsync` for `EditableImage`) or `publishVia="open_cloud"` (uses `ROBLOX_API_KEY` + `ROBLOX_CREATOR_ID` via Open Cloud Assets v1) — automatically writing the permanent `rbxassetid://<id>` into `asset-lock.json` and `Assets.lua`.

## Responsive UI Design Checklist
- Always add a root `UIScale` inside `ScreenGui` and set `ResetOnSpawn = false`, `ZIndexBehavior = Enum.ZIndexBehavior.Sibling`.
- Use `AnchorPoint = Vector2.new(0.5, 0.5)` for centered modals and `UIListLayout` / `UIGridLayout` + `UIPadding` instead of hardcoded pixel offsets.
- Combine dark translucent fills (`BackgroundColor3 = [0.09, 0.12, 0.17]`, `BackgroundTransparency = 0.12`), `UICorner`, subtle `UIStroke`, and `SpringUI.bindButton` hover feedback.
