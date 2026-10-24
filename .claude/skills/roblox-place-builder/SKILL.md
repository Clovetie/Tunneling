---
name: roblox-place-builder
description: 3D world greyboxing, modular architectural scale in studs, clean model hierarchies, prefab spawning, and batch placement in Roblox Studio.
---

# Roblox 3D Place Builder Skill

Use this skill whenever constructing 3D environments, maps, obbies, lobbies, shops, interiors, or props in Roblox Studio.

## Stud Scale Reference (R15 Avatar Standard)
- **Avatar height**: `~5.5 studs` (`2 studs` wide, `1 stud` deep)
- **Jump height / max step**: default `JumpHeight = 7.2 studs`; comfortable stair step = `0.8–1.0 stud` rise × `1.8–2.0 studs` run
- **Doorway clearance**: minimum `6 studs` wide × `8.5–9.5 studs` tall
- **Room ceiling**: `12–16 studs` residential/corridor; `22–36 studs` lobby/hall
- **Walkway / bridge width**: `8–12 studs` single lane; `16–24 studs` main plaza path

## Construction Discipline
1. **Survey before placing**: Always call `get_studio_state` and `inspect_instance(path="Workspace", depth=2)` first so new geometry never overlaps existing structures.
2. **Start from built-in prefabs when helpful**: Call `browse_kit(category="prefabs")` and `spawn_prefab` (`spawn_plaza`, `interact_pad`, `world_sign_board`, `lighting_stylized_day`, `lighting_cinematic_sunset`, `lighting_cyber_night`).
3. **Group cleanly into `Model`s**:
   - Every multi-part prop or zone belongs inside a named `Model` under an organized `Folder` (`Workspace.Map`, `Workspace.Props`, `Workspace.Interactables`).
   - Always explicitly set `"Anchored": true` on static geometry (`Part` defaults to `Anchored = false` and will collapse on Play!).
   - Disable `"CanCollide": false` and `"CastShadow": false` on tiny trim, neon strips, and decorative details.
4. **Use grid duplication instead of huge JSON blobs**:
   - Build ONE clean archetype (`LampPost`, `FenceSegment`, `Step`, `Crate`) via `build_tree`, then duplicate rows/columns in 1 call via `clone_instance(path=..., count=N, offset=[dx, dy, dz], newName="Lamp_{i}")`.
5. **Apply world textures cleanly**:
   - Import custom or procedural textures (`grid_dark_1m`, `hazard_stripes`, `scifi_panel_tile`, `brushed_metal_noise`, or AI-generated seamless PNGs) with `import_image` and tile them using a `Texture` instance (`StudsPerTileU`, `StudsPerTileV`).
