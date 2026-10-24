---
name: ui-layout-audit-and-rbxmx
description: >
  Multi-device responsive UI layout linting (Desktop 1920x1080, Tablet 1024x768,
  Mobile 844x390), Dual Icon Packs (Stud 1,022 glossy 3D + Lucide 565 clean vectors),
  CC0 UI/Game SoundFX wiring, and .rbxmx XML model import/export in Roblox Studio.
---

# Responsive UI Layout QA, Dual Icon Packs, Audio & `.rbxmx` Workflow

## 1. Choose the Right Visual Style for Your Genre
- **Simulator / Tycoon / Pet / Obby / Family-Friendly**:
  Use **`StudUI`** + **`StudMotion`** + **`CheckUIIcons`** (`1,022` high-res 3D glossy icons).
- **Horror / Sci-Fi / Tactical / RPG / Competitive / Admin**:
  Use **`ModernUI`** (`ModernUI.window`, `ModernUI.button`, `ModernUI.badge`, `ModernUI.progressBar`) + **`LucideIcons`** (`565` flat crisp vector icons: `LucideIcons.get("shield")`, `LucideIcons.get("settings")`).

```lua
local ModernUI = require(game.ReplicatedStorage.Shared.ModernUI)
local SoundFX = require(game.ReplicatedStorage.Shared.SoundFX)

ModernUI.init()
local gui = Instance.new("ScreenGui")
gui.Name = "TacticalHud"
gui.IgnoreGuiInset = false
gui.ResetOnSpawn = false
gui.Parent = game.Players.LocalPlayer:WaitForChild("PlayerGui")
ModernUI.bindResponsiveScale(gui)

local win = ModernUI.window({
    Title = "LOADOUT",
    Icon = "shield",
    Size = UDim2.fromOffset(520, 340),
    Parent = gui,
})
SoundFX.bindGuiSounds(gui) -- Automatic hover + press SFX on every button!
```

## 2. Always Audit Every Screen Across Desktop, Tablet & Mobile!
Never ship fixed-offset UI without running **`audit_ui_layout`**:
- MCP tool: `audit_ui_layout(path="StarterGui")` or `audit_ui_layout(prefab="ui_kenney_shop_window")`
- CLI: `python roblox-bridge/arena_studio.py audit-ui --path StarterGui`
- Automatically computes exact pixel rectangles across **Desktop (`1920x1080`)**, **Tablet (`1024x768`)**, and **Mobile (`844x390`)**, flagging:
  1. `VIEWPORT_OVERFLOW` — panels wider/taller than mobile `390px` height when missing responsive `UIScale`
  2. `TOUCH_TARGET_TOO_SMALL` — interactive buttons smaller than `36px` on mobile
  3. `UNWRAPPED_OFFSET_SIZE` — fixed `Offset > 420px` without `UISizeConstraint` or `UIScale`
  4. `TEXT_NOT_SCALED` — multi-character `TextLabel`/`TextButton` clipping on narrow mobile frames

## 3. Open-Source `.rbxmx` Model Import & Export
- Import any community or generated `.rbxmx` XML file straight into Studio (`stripScripts=True` safely sanitizes untrusted community models!):
  `import_rbxmx(filePath="path/to/model.rbxmx", parent="Workspace", stripScripts=False)`
- Export any live Studio hierarchy (`StarterGui.ShopGui` or `Workspace.Map`) to a standalone `.rbxmx` file:
  `export_rbxmx(path="StarterGui.ShopGui", outputFile="roblox-kit/models/my_shop.rbxmx")`
