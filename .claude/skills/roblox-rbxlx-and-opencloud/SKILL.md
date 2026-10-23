---
name: roblox-rbxlx-and-opencloud
description: >
  Standalone .rbxlx full Roblox Place XML export/import (works even before Studio opens)
  and Roblox Open Cloud v1/v2 publishing, DataStore inspection, and MessagingService broadcasts.
---

# Standalone `.rbxlx` Place Authoring & Open Cloud Integration

## 1. Export or Author Complete `.rbxlx` Place Files (`export_place_rbxlx` / `import_place_rbxlx`)
- Export an entire live Studio session (`Workspace`, `Lighting`, `ReplicatedStorage`, `ServerScriptService`, `StarterGui`, `StarterPlayer`, `SoundService`) to a clean Git-diffable `.rbxlx` XML file:
  `export_place_rbxlx(outputFile="build/MyExperience.rbxlx")`
- Author a standalone `.rbxlx` place file **offline** (even when no Roblox Studio instance is connected!) by passing `offlineStarterPlace=true` — users can immediately double-click the generated `.rbxlx` in Windows/macOS to open a working playable place with `StudUI`, `ModernUI`, `LucideIcons`, and starter lighting/scripts pre-installed!
- Sync any `.rbxlx` XML file into a connected Studio session with `import_place_rbxlx(filePath="...")`.

## 2. Roblox Open Cloud Automation (`open_cloud_action`)
When `ROBLOX_OPEN_CLOUD_API_KEY` is configured in the environment (or passed per call):
- **`publish_place`**: Upload a `.rbxlx`/`.rbxl` file to a live `universeId` + `placeId` (`versionType="Saved"` for safe staging by default, or `"Published"` when explicitly requested by the user).
- **`datastore_list` / `datastore_get` / `datastore_set`**: Inspect or repair live DataStore keys safely.
- **`messaging_publish`**: Broadcast cross-server LiveOps events (`MessagingService` topics) across active game servers.
