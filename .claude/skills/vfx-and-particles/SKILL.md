---
name: vfx-and-particles
description: Curated VFX libraries, ParticleEmitter flipbooks, Beams, Trails, Highlights, and burst combat/environment particle recipes for Roblox Studio.
---

# Roblox VFX, Flipbooks & Particle Emitter Skill

Use this skill when building combat impacts, magic abilities, environmental hazards, loot beams, portals, or atmospheric effects.

## 1. Curated Open-Source & Roblox VFX Libraries (Direct Links)
- **Kenney Particle Pack (CC0 Public Domain — 80+ soft glows, sparks, flames, magic, smoke, stars)**:
  - Web: `https://kenney.nl/assets/particle-pack`
  - Bundled in this repo under `roblox-kit/assets/images/` (`search_assets(query="particle", assetType="image")`) and procedural presets (`generate_procedural_image(preset="vfx_shockwave_ring" | "vfx_magic_rune_circle" | "vfx_slash_crescent" | "vfx_blood_splatter")`).
- **Effekseer Open-Source Particle & Flipbook Library (CC0 / MIT Sprite Sheets)**:
  - Web: `https://effekseer.github.io/en/` · GitHub: `https://github.com/effekseer/Effekseer`
- **OpenGameArt CC0 VFX & Flipbook Atlas Collection**:
  - Web: `https://opengameart.org/art-search-advanced?keys=particle+cc0`
- **Roblox Creator Store Free Verified Flipbook & VFX Packs**:
  - Web: `https://create.roblox.com/store/models?keyword=VFX+Flipbook`
  - Official Roblox Flipbook Documentation: `https://create.roblox.com/docs/effects/particle-emitters#flipbooks`
- **Built-in Repo VFX Module & Tool**:
  - MCP Tool: `build_vfx_emitter` (spawns pre-tuned `ParticleEmitter` + `PointLight` + `Highlight` attachments in 1 call).
  - Kit Module: `install_kit_module(module="VFXManager")` (`ReplicatedStorage.Shared.VFXManager` for deterministic `:Emit(count)` bursts, hit-flash `Highlight` pulses, and shockwave rings).

## 2. Core VFX Engineering Rules
1. **Burst via `:Emit(n)`, Not `Enabled = true` + `task.wait()`**:
   - For hit impacts, slashes, explosions, and chest openings, set `ParticleEmitter.Rate = 0` and call `emitter:Emit(burstCount)`. This guarantees identical particle counts regardless of frame rate or server replication lag.
2. **Use `Attachment` Hosts on 3D Meshes**:
   - Parent `ParticleEmitter`, `PointLight`, and `Trail` instances inside `Attachment`s on a `MeshPart` (`spawn_kit_mesh`) rather than spawning unanchored invisible `Part`s every frame.
3. **Calibrate `LightEmission` & `Brightness` With `BloomEffect`**:
   - Set `LightEmission = 0.85` to `1.0` for energy/fire/magic particles so they blend additively without washing out into solid white rectangles.
4. **Prioritize 3D Mesh Shockwaves + Particles Together**:
   - Combine a fast-expanding 3D mesh ring (`MeshPart` or `SpecialMesh`) with a 12–20 particle burst and a 120ms `Highlight` flash on the target model.

## Verification
- Call `build_vfx_emitter(preset="sword_slash_arc" | "magic_portal_vortex" | "loot_legendary_beam", buildInStudio=true)` and inspect the resulting hierarchy in `Workspace` or preview it in the 3D Viewport (`capture_viewport`).
