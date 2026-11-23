# 520-Model 3D Mesh Catalog & Kitbashing Reference

## Bundled 3D Model Categories (`search_assets(assetType="model")`)

| Category | Example Queries for `spawn_kit_mesh` / `search_assets` | Ideal Use Cases |
|---|---|---|
| **Dungeon & Fantasy** | `torch`, `barrel`, `chest`, `column`, `altar`, `banner`, `trap`, `gate` | Dungeons, boss arenas, shrines, safe rooms |
| **Horror & Graveyard** | `crypt`, `gravestone`, `coffin`, `lantern`, `candlestick`, `fence`, `dead_tree` | horror cabins, cemeteries, overgrown yards |
| **Furniture & Interior** | `chair`, `table`, `bookshelf`, `bed`, `cabinet`, `lamp`, `desk`, `rug` | Mansions, safe houses, roleplay interiors |
| **Weapons & Combat** | `sword`, `shield`, `bow`, `axe`, `hammer`, `staff`, `dagger`, `blaster` | Combat loadouts, shop pedestals, loot drops |
| **Nature & Foliage** | `pine`, `oak`, `rock`, `boulder`, `bush`, `mushroom`, `crystal`, `log` | Biomes, forests, caves, outdoor borders |
| **Sci-Fi, Urban & Vehicles** | `crate`, `console`, `turret`, `antenna`, `car`, `truck`, `street_light` | Cyberpunk hubs, obby checkpoints, bases |

## Kitbashing Formula for High-Detail Props
1. **Base Silhouette**: Spawn a 3D mesh via `spawn_kit_mesh(query="...", position=[x, y, z])`.
2. **Material & Rim Accent**: Add a subtle `Highlight` (`OutlineTransparency = 0.65`, `FillTransparency = 1.0`) or `SpecialMesh` trim on interactive objectives.
3. **VFX & Audio Anchor**: Attach `build_vfx_emitter` and a spatial `Sound` (`RollOffMode = InverseTapered`) to the mesh's `PrimaryPart`.
