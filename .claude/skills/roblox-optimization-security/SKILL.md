---
name: roblox-optimization-security
description: Server security validation, exploit prevention, safe asset insertion, physics anchoring, and frame-rate/memory optimization in Roblox Studio.
---

# Roblox Security & Optimization Skill

## Mandatory Security Rules
1. **Zero Client Trust**: Clients control their character physics and can fire any `RemoteEvent` / `RemoteFunction` with arbitrary values (`NaN`, `math.huge`, huge tables, negative prices, other players' instances).
2. **Validate Every Remote**: Wrap server handlers with `NetGuard.bindRemoteEvent` — check `typeof()`, finite bounds (`price > 0`), distance (`(rootPart.Position - station.Position).Magnitude <= 18`), and per-player cooldowns.
3. **Strip Embedded Toolbox Scripts**: Always keep `stripScripts = true` (default) when calling `insert_asset` so third-party models cannot inject hidden `require(assetId)` backdoors.

## Performance & Memory Checklist
- Confirm static `BasePart`s have `Anchored = true` so physics solver cost stays near zero.
- Turn `CanQuery = false` and `CanTouch = false` on purely visual VFX / decoration parts so raycasts and `.Touched` events stay fast.
- Reuse a bounded pool or `Maid` cleanup for projectiles and temporary VFX attachments (`Debris:AddItem(part, lifetime)`).
