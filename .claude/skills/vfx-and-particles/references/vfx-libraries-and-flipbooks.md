# VFX Libraries, Flipbook Atlases & ParticleEmitter Recipes

## External VFX & Flipbook Resource Directory

| Library / Source | License | Best For | URL |
|---|---|---|---|
| **Kenney Particle Pack** | CC0 1.0 | Soft glows, sparks, muzzle flashes, magic runes, smoke puffs, star bursts | `https://kenney.nl/assets/particle-pack` |
| **Kenney Smoke Particles** | CC0 1.0 | Volumetric-style dust clouds, explosion plumes, dash trails | `https://kenney.nl/assets/smoke-particles` |
| **Kenney Splat Pack** | CC0 1.0 | Decals, impact craters, paint/slime/blood floor splatters | `https://kenney.nl/assets/splat-pack` |
| **Effekseer Sample Effect Library** | CC0 / MIT | 4x4 and 8x8 animated flipbook sprite sheets for slashes, lightning, portals | `https://github.com/effekseer/Effekseer` |
| **Roblox Official Flipbook Textures** | Roblox Docs | Built-in `Grid2x2`, `Grid4x4`, and `Grid8x8` particle flipbook sheets | `https://create.roblox.com/docs/effects/particle-emitters#flipbooks` |

## Flipbook Configuration in Roblox (`ParticleEmitter`)
When using a `4x4` or `8x8` sprite sheet imported via `import_image`:
- `FlipbookLayout = Enum.ParticleFlipbookLayout.Grid4x4` (or `Grid8x8`)
- `FlipbookMode = Enum.ParticleFlipbookMode.OneShot` (for explosions/impacts) or `Loop` (for ambient fire/auras)
- `FlipbookFramerate = NumberRange.new(24, 30)`

## High-Clarity Combat & Loot VFX Recipes

### 1. Crisp Sword Slash / Melee Hit
- **Primary Burst (`Rate = 0`, `:Emit(14)`)**:
  - `Speed = NumberRange.new(18, 32)`
  - `Drag = 6.5` (fast initial snap that brakes sharply in air)
  - `Lifetime = NumberRange.new(0.18, 0.32)`
  - `Orientation = Enum.ParticleOrientation.VelocityParallel` (stretches sparks along motion vector)
  - `Squash = NumberSequence.new(-0.6, 0)`

### 2. Legendary Loot Drop Pillar
- **Vertical Beam (`Beam` between `Attachment0` at `[0, 0, 0]` and `Attachment1` at `[0, 18, 0]`)**:
  - `Width0 = 2.8`, `Width1 = 0.4`, `FaceCamera = true`, `LightEmission = 1`
- **Rising Shimmer (`ParticleEmitter`, `Rate = 12`)**:
  - `Acceleration = Vector3.new(0, 8, 0)`, `Speed = NumberRange.new(2, 5)`
