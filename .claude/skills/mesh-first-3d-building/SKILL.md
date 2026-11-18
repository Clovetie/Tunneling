---
name: mesh-first-3d-building
description: Prioritize 3D meshes (520 bundled Kenney CC0 models, .rbxmx props, MeshParts, SpecialMeshes) over raw blocky Parts when building 3D worlds in Roblox Studio.
---

# Mesh-First 3D World Building Skill

**CORE MANDATE**: Whenever building 3D environments, props, dungeons, shops, horror rooms, or interactive stations, **3D meshes MUST be prioritized over raw blocky `Part` primitives**.

## 1. Why Mesh-First Building Matters
- Environments built exclusively from rectangular `Instance.new("Part")` boxes look like greybox programmer art.
- This repository bundles **520 verified commercial-safe CC0 3D models** (`roblox-kit/assets/models/` + `.rbxmx` models in `roblox-kit/models/`) and **32 detailed 3D/VFX prefabs** (`roblox-kit/prefabs/`).

## 2. Mesh-First Workflow (3 Fast Tools)
1. **Spawn Ready-to-Use 3D Meshes (`spawn_kit_mesh`)**:
   - Call `spawn_kit_mesh(query="lantern" | "chest" | "tree" | "sword" | "chair" | "pillar" | "altar" | "car" | "grave", position=[x, y, z], scale=1.0)` to search the 520-model registry and spawn a detailed 3D mesh/model directly into `Workspace`!
2. **Search All 520 3D Models by Category (`search_assets`)**:
   - Call `search_assets(assetType="model", query="...", limit=30)` across categories (`dungeon`, `furniture`, `nature`, `weapons`, `vehicles`, `graveyard`, `survival`, `pirate`, `space`, `urban`, `arcade`, `food`, `holiday`, `toon`).
3. **Import `.rbxmx` Models & Probe Runtime `EditableMesh` (`import_rbxmx`, `mesh_capabilities`)**:
   - Call `import_rbxmx` to inject any of the 20 pre-authored `.rbxmx` 3D prop hierarchies, or `mesh_capabilities` to check if the connected Studio build supports runtime `AssetService:CreateEditableMesh()`.

## 3. Combining Structural Parts with 3D Meshes
- Use anchored `Part`s (`Material.Slate`, `Material.Cobblestone`, `Material.WoodPlanks`) **only** for large architectural slabs (floors, walls, ceilings, terrain foundations).
- Dress every room, corner, doorway, objective, and silhouette with **3D meshes (`spawn_kit_mesh`)**, **`SpecialMesh` bevels/cylinders/spheres**, and **VFX emitters (`build_vfx_emitter`)**.

## Verification
- Run `capture_viewport` (which highlights `MeshPart`/`SpecialMesh` counts in `meta.meshPartsCount`) and `de_slop_audit` to verify strong 3D mesh usage and zero unanchored/floating geometry (`audit_3d_scene`).
