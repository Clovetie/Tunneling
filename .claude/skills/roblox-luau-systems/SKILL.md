---
name: roblox-luau-systems
description: Server-authoritative Luau game architecture, reusable roblox-kit modules, ModuleScript cache busting, and zero-warning static analysis.
---

# Roblox Luau Systems Engineering Skill

Use this skill whenever writing, refactoring, or debugging `Script`, `LocalScript`, or `ModuleScript` code.

## Standard Container Layout
- `ReplicatedStorage.Shared` — Shared `ModuleScript`s (`Maid`, `Signal`, `SpringUI`, `StateStore`, `Assets`, config/types)
- `ReplicatedStorage.Events` — All `RemoteEvent`s and `RemoteFunction`s
- `ServerScriptService` — Single server bootstrap `Script` + domain service `ModuleScript`s
- `ServerStorage` — Server-only templates, loot tables, and sensitive logic (never replicated to clients)
- `StarterPlayer.StarterPlayerScripts` — Client bootstrap `LocalScript` + UI/input controllers

## Install Verified Foundation Modules in One Call
Instead of rewriting boilerplate every session, call `install_kit_module(module="all", parent="ReplicatedStorage.Shared")` to install:
- `Maid` — Deterministic cleanup for connections, instances, threads, and sub-components
- `Signal` — Fast pure-Luau custom events (`Connect`, `Once`, `Fire`, `Wait`)
- `NetGuard` — Server Remote rate-limiter + `NaN`/`Inf`/bounds sanitizer
- `TagBinder` — `CollectionService` component lifecycle manager (`bind(tag, Class)`)
- `SpringUI` — Tactile hover/press `UIScale` tweens and pop reveals
- `StateStore` — Observable state dictionary (`Get`, `Set`, `Increment`, `Observe`)
- `Assets` — Auto-maintained map of imported repo image URIs (`Assets.Images.<key>`)

## Critical Luau Safety Rules
1. **Always run `luau_check` first**: Catch syntax errors, non-strict undefined globals (e.g. a renamed helper function), and unused variables offline before pushing to Studio.
2. **Bust cached `ModuleScript`s**: Luau caches `require(module)` once per DataModel lifetime. Pass `replaceInstance: true` on `write_script` / `multi_edit`, or test isolated changes via `require_fresh`.
3. **Never write mixed array+dict tables for JSON**: `HttpService:JSONEncode` silently drops string keys if array indices also exist in the same table.
4. **Never trust client arguments**: Validate every parameter in `OnServerEvent` / `OnServerInvoke` with `typeof(...)` and `NetGuard.isFiniteNumber(...)`.
