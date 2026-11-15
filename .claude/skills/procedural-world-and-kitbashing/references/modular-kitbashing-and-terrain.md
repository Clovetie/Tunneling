# Modular 3D Mesh Kitbashing & Ground-Snapping Recipes

## 1. Material & Palette Pairings for Kitbashed 3D Mesh Props
| Theme | Base Pedestal (`SpecialMesh` Cylinder) | Hero Core (`MeshPart` / `SpecialMesh` Sphere/FileMesh) | Accent Trim / Glow (`Neon` + `PointLight`) |
|---|---|---|---|
| **Arcane Sanctum** | `Basalt` `[45, 52, 68]` | `Glass` / `Neon` `[95, 195, 255]` | `Neon` `[145, 95, 255]` (`Range = 18`) |
| **Infernal Forge** | `Slate` `[38, 34, 34]` | `Metal` `[72, 76, 84]` | `Neon` `[255, 115, 35]` (`Range = 16`) |
| **Ancient Sylvan** | `Sandstone` `[138, 124, 102]` | `Marble` `[210, 205, 192]` | `Neon` `[95, 245, 165]` (`Range = 14`) |
| **Gilded Treasury** | `Marble` `[52, 48, 62]` | `Metal` `[235, 185, 55]` | `Neon` `[255, 225, 110]` (`Range = 15`) |

## 2. Raycast Ground Snapping for Multi-Part Models
Always snap `Model:PivotTo(...)` to the hit position returned by a downward `Workspace:Raycast` + half of `Model:GetExtentsSize().Y` so sculpted bases sit flush on uneven floors.
