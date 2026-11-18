---
name: procedural-world-and-kitbashing
description: Generate cohesive 3D worlds, seeded dungeons, and modular kitbashed props prioritizing 3D meshes (MeshPart, SpecialMesh, .rbxmx models) over blocky primitives, with raycast ground snapping and Poisson prop scattering.
---

# Procedural World Generation & 3D Mesh Kitbashing

Build rich, silhouette-distinct 3D worlds by combining seeded procedural layout tools with 3D mesh kitbashing.

## 1. Mesh-First World Building Workflow
1. **Macro Layout (`generate_dungeon_layout`, `build_biome`)**:
   - Establish navigable floors, corridors, and terrain foundations with guaranteed reachability (`allRoomsReachable: true`).
2. **Hero Props & Landmarks via 3D Meshes (`kitbash_mesh_prop`, `spawn_kit_mesh`, `import_rbxmx`)**:
   - Never populate rooms with plain rectangular `Part` boxes. Use `kitbash_mesh_prop` (`runic_obelisk`, `crystal_lantern`, `treasure_shrine`, `forge_anvil`, `arcane_staff`, `guardian_statue`), `spawn_kit_mesh`, or `import_rbxmx` from the 520-model CC0 catalog.
3. **Poisson-Disc Scattering & Ground Snapping (`scatter_props`, `audit_3d_scene`)**:
   - Scatter foliage, rocks, and lanterns with minimum spacing (`minSpacing >= 6`) and run `audit_3d_scene(autoFixUnanchored=True, snapFloating=True)` so zero props float in mid-air.
4. **Atmosphere & Visual Verification (`configure_atmosphere_fx`, `capture_viewport`)**:
   - Pair every biome/dungeon with an atmosphere preset (`BloodMoon`, `CyberpunkNeon`, `GoldenHour`, `DeepAbyss`, `ArcticBlizzard`) and inspect with `capture_viewport` (checking `meshPartsRendered` in `meta`).

See `references/modular-kitbashing-and-terrain.md` for kitbashing recipes and material harmony tables.
