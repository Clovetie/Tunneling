---
name: multiplayer-replication-and-netguard
description: Harden Roblox multiplayer networking with NetworkGuard schema validation, per-player rate limiting, NaN/Inf vector guards, UnreliableRemoteEvent state sync, and client-side VFX/SFX replication.
---

# Multiplayer Replication & `NetworkGuard` Security

Never trust client payloads on `.OnServerEvent` or `.OnServerInvoke`, and never replicate high-frequency visual effects by tweening instances on the server.

## 1. Five Golden Rules of Roblox Networking
1. **Zero Client Trust on Economy, Damage & Inventory**:
   - The client sends **intent** (`itemId = "RunicBlade"` or `abilitySlot = 2`), never `price`, `damage`, `currencyDelta`, or `cooldown`. The server looks up authoritative stats from `ReplicatedStorage.Shared` / `ServerStorage`.
2. **Validate Every Argument with `NetworkGuard`**:
   - Check `typeof(arg)`, finite numbers (`v == v and math.abs(v) < 1e9`), string length caps (`#s <= 64`), and per-player token-bucket rate limits before running game logic.
3. **Replicate VFX & SFX as Compact Event Payloads**:
   - Instead of creating particles or tweens on the server, fire `VFXRemote:FireAllClients("slash_hit", hitPosition)` and let each client run `VFXManager` `:Emit(n)` and `SoundFX` locally at 60+ FPS.
4. **Use `UnreliableRemoteEvent` for High-Frequency Ephemeral State**:
   - Aim angles, cursor look-vectors, and continuous charge meters belong on `UnreliableRemoteEvent` capped at `15–20 Hz`.
5. **Audit with `audit_network_security`**:
   - Run `audit_network_security(includeStudio=True)` to verify a `100/100` `securityScore` before shipping.

See `references/remote-security-and-replication.md` for `NetworkGuard` and client-replication patterns.
