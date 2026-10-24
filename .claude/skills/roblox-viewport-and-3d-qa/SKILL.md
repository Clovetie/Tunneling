---
name: roblox-viewport-and-3d-qa
description: >
  Visual 3D viewport capture (multimodal PNG vision feedback + HUD overlay compositing)
  and automated 3D scene geometry/physics/grounding audits (unanchored hazards, floating
  parts, blocked spawns, Z-fighting coplanar surfaces).
---

# Visual Viewport Capture & 3D Scene QA Workflow

## 1. Visually Inspect Your 3D Builds & HUD Overlays (`capture_viewport`)
After spawning or editing 3D geometry or `StarterGui` screens, call **`capture_viewport`** to receive a real `image/png` multimodal frame directly in your tool response:
- `capture_viewport(preset="perspective", includeHud=true, width=800, height=450)` — shaded 3D perspective camera + active `StarterGui` HUD overlay composited on top.
- `capture_viewport(preset="top_blueprint", includeHud=false)` — orthographic top-down architectural floorplan view for verifying map spacing, symmetry, corridors, and spawn layout.
- `capture_viewport(preset="front")` / `preset="iso_ne"` / `preset="iso_nw"` — check building heights, rooflines, and silhouette readability.

## 2. Run `audit_3d_scene` Before Every Playtest!
Never guess whether parts are anchored or grounded — run **`audit_3d_scene`**:
- Detects **`UNANCHORED_PART`** (non-character parts that collapse into the void when Play starts).
- Detects **`FLOATING_GEOMETRY`** (parts hovering `0.20–12` studs in mid-air above floors with air underneath; intentionally floating obby pads/orbs should have `CollectionService` tag `"Floating"` or `"Collectible"`).
- Detects **`SPAWN_OBSTRUCTED`** (`SpawnLocation` headroom blocked by `CanCollide = true` parts).
- Detects **`Z_FIGHTING_COPLANAR`** (overlapping parts sharing identical coplanar faces with different colors).
- One-call auto-remediation (single `Ctrl+Z` undo step):
  `audit_3d_scene(path="Workspace", autoFixUnanchored=true, snapFloating=true)`
