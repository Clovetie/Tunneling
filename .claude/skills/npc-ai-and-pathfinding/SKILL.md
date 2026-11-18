---
name: npc-ai-and-pathfinding
description: Build intelligent Roblox NPC AI using PathfindingService, PathfindingModifiers, finite state machines (Idle, Patrol, Chase, Attack, Flee), throttled line-of-sight raycasting, and 3D mesh rigs.
---

# NPC AI, Finite State Machines & `PathfindingService`

Build NPCs that navigate complex 3D mesh environments reliably without getting stuck on corners or lagging the server.

## 1. Core NPC AI Principles
1. **3D Mesh Rigs over Blocky Dummy Parts**:
   - Always assemble NPCs and monsters using `MeshPart`, `SpecialMesh`, or `.rbxmx` models from the 520-model CC0 catalog (`spawn_kit_mesh`, `kitbash_mesh_prop`) rather than unstyled rectangular `Part` bricks.
2. **Throttled Path Recomputation (`0.35s – 0.6s`)**:
   - Never call `path:ComputeAsync()` every frame. Recompute only when the target moves more than `6` studs from the previous path goal or when `path.Blocked` fires.
3. **Direct Chase Shortcut (`Workspace:Raycast`)**:
   - When the target is within `24` studs and an unobstructed raycast confirms flat ground and line of sight, call `humanoid:MoveTo(targetRoot.Position)` directly to avoid `ComputeAsync` overhead.
4. **Explicit Agent Parameters**:
   - Always pass `{ AgentRadius = 2.2, AgentHeight = 5.5, AgentCanJump = true, WaypointSpacing = 4.0 }` to `PathfindingService:CreatePath(...)`.
5. **Network Ownership**:
   - Set `npcRoot:SetNetworkOwner(nil)` on server-controlled AI rigs so nearby players cannot physics-spoof or freeze the NPC.

See `references/pathfinding-and-state-machines.md` for complete Luau FSM and blocked-waypoint recovery patterns.
