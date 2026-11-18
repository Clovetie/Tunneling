---
name: combat-and-hitboxes
description: Build responsive Roblox melee, ranged, and AoE combat using OverlapParams spatial queries (GetPartBoundsInBox, GetPartBoundsInRadius), server-authoritative hit validation, HitboxSystem, VFXManager burst particles, and CameraShaker.
---

# Combat & Spatial Hitboxes (`OverlapParams` + VFX/SFX Juice)

Never use `.Touched` events for melee weapons or fast combat abilities—`.Touched` relies on physics step contacts and misses stationary or fast-moving targets. Always use deterministic spatial queries (`workspace:GetPartBoundsInBox`, `workspace:GetPartBoundsInRadius`, or raycast sweeps) paired with `VFXManager` `:Emit(n)` bursts and layered spatial SFX.

## 1. Core Combat Architecture
1. **Client Windup & Immediate Feedback**:
   - Play animation track and swing whoosh (`SoundFX` with `±8%` pitch variance).
   - Sample local spatial hitbox (`HitboxSystem.QueryBox`) for responsive visual hit-confirmations and fire `CombatHitRemote:FireServer(swingId, targetModel, hitPosition)`.
2. **Server Authority (`NetworkGuard` + Spatial Sanity Check)**:
   - Rate-limit attack remotes per player (`cooldown >= weaponSwingInterval * 0.85`).
   - Validate `math.isnan` / `math.huge` on all vectors.
   - Verify ` (attackerRoot.Position - targetRoot.Position).Magnitude <= maxReach + latencyTolerance ` and check line-of-sight so exploiters cannot hit through walls or across the map.
3. **Impact Juice (`VFXManager` + `CameraShaker` + `DamageNumbers`)**:
   - Trigger `VFXManager.EmitAll(hitAttachment, 28)` (`Rate = 0` burst emitter).
   - Flash target `Highlight` (`FillTransparency = 0.2 -> 1.0` over `0.15s`).
   - Apply `CameraShaker:AddTrauma(0.35)` and spawn concise, icon-badged `DamageNumbers` (never verbose text sentences).

## 2. MCP Tools & Kit Modules for Combat
- **`build_combat_hitbox`**: Generates a complete server+client combat rig (`melee_combo`, `aoe_ground_slam`, `projectile_spell`, `dash_strike`) with `OverlapParams`, `VFXManager` bursts, and `NetworkGuard` validation.
- **`install_kit_module`**: Install `HitboxSystem`, `VFXManager`, `CameraShaker`, `DamageNumbers`, `SoundFX`, and `NetworkGuard`.
- **`build_vfx_emitter`**: Spawn `slash_hit`, `explosion_spark`, or `frost_shatter` burst rigs.

See `references/spatial-hitbox-and-combo-patterns.md` for ready-to-use Luau spatial query and server validation recipes.
