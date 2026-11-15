# Web HTML / CSS / Tailwind to Roblox `ScreenGui` Mapping Reference

## Supported HTML Elements -> Roblox Instance Classes

| Web Tag / Pattern | Roblox ClassName | Automatic Child Constraints |
|---|---|---|
| `<div class="row flex">` / `<header>` / `<nav>` | `Frame` | `UIListLayout` (`FillDirection.Horizontal`), `UICorner`, `UIPadding` |
| `<div class="grid">` | `Frame` | `UIGridLayout`, `UICorner`, `UIPadding` |
| `<div class="card">` / `<section>` | `Frame` | `UICorner` (`10px`), `UIStroke` (`1.5px`), dark translucent surface |
| `<button>` / `<a class="btn">` | `TextButton` | `UICorner` (`8px`), `UIStroke`, bold concise text |
| `<img src="rbxassetid://...">` / `<svg>` | `ImageLabel` | `BackgroundTransparency = 1`, `ScaleType = Fit` |
| `<h1>`..`<h4>` / `<span>` / `<p>` | `TextLabel` | `TextScaled = true`, `UITextSizeConstraint` (`Min=11, Max=28`) |
| `<input>` | `TextBox` | `UICorner` (`6px`), `UIStroke`, `PlaceholderText` |

## Example Web HTML Snippet for `import_ui_from_web`
```html
<div class="card" style="width:0.32;height:0.08;x:0.5;y:0.03;bg:#141b2d;stroke:#38bdf8;radius:12">
  <img src="rbxassetid://16884178261" style="width:0.16;height:0.75;x:0.04;y:0.12" />
  <span style="width:0.48;height:0.7;x:0.22;y:0.15;color:#f8fafc">1,250 GOLD</span>
  <button style="width:0.26;height:0.72;x:0.70;y:0.14;bg:#2563eb;color:#ffffff;radius:8">SHOP</button>
</div>
```
