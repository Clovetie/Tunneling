---
name: roblox-vfx-lighting-audio
description: Atmosphere, post-processing lighting presets, custom particle emitter sprites, highlights, beams, and spatial sound polish in Roblox Studio.
---

# Roblox Lighting, VFX & Audio Polish Skill

Use this skill to give places immediate visual atmosphere, readable game-feel feedback, and clean performance.

## Instant Lighting Foundation
- Spawn a tuned preset via `spawn_prefab(prefab="lighting_cinematic_sunset" | "lighting_cyber_night" | "lighting_stylized_day")`, then fine-tune `Lighting` properties (`ClockTime`, `Brightness`, `ExposureCompensation`) and child `Atmosphere` / `BloomEffect` / `ColorCorrectionEffect`.
- Keep `BloomEffect.Threshold >= 0.95` so only `Material.Neon` parts and bright highlights bloom rather than washing out UI/whites.

## Custom VFX with Imported Sprites
- Built-in repo sprites ready for `ParticleEmitter` and `Beam`:
  - `vfx/particle_soft_glow.png` — additive aura, magic orb, muzzle glow (`LightEmission = 1`)
  - `vfx/particle_spark_star.png` — reward burst, critical hit, collectible shimmer
  - `vfx/particle_smoke_puff.png` — dust land, dash puff, ambient vent mist (`LightEmission = 0.1`)
  - `vfx/particle_shockwave_ring.png` — ground slam, jump pad pulse, AoE indicator
  - `vfx/beam_energy_streak.png` — laser link, zip-line energy, weapon trail
- For burst feedback in gameplay code, set `emitter.Rate = 0` at rest and trigger deterministic bursts via `emitter:Emit(16)`.
