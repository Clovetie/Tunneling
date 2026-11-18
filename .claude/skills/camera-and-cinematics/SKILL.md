---
name: camera-and-cinematics
description: Build smooth Roblox 3D camera systems including spring-damped over-the-shoulder action cameras, isometric/tactical rigs, trauma-based CameraShaker impulses, boss lock-on framing, and keyframed cutscenes.
---

# 3D Camera Systems, Trauma Shake & Cinematics

A great Roblox camera feels weightless during exploration and visceral during combat or horror encounters.

## 1. Camera Engineering Rules
1. **Use `BindToRenderStep` at `Enum.RenderPriority.Camera.Value + 1`**:
   - Never drive a custom camera from `task.wait()` or `Heartbeat`. Always update `workspace.CurrentCamera.CFrame` inside `RunService:BindToRenderStep` at `RenderPriority.Camera.Value + 1` so camera motion is 100% tear-free.
2. **Damped Harmonic Springs over Linear Lerp**:
   - Raw `CFrame:Lerp(goal, 0.1)` is frame-rate dependent. Use exponential decay (`1 - math.exp(-stiffness * dt)`) or critically damped springs (`CameraShaker`, `SpringUI`).
3. **Trauma-Squared Camera Shake (`shake = trauma * trauma`)**:
   - Store `trauma` in `[0, 1]`, decay it linearly over time (`trauma = math.max(0, trauma - decay * dt)`), and scale rotational/positional offsets by `trauma ^ 2` so big hits punch hard and settle smoothly.
4. **Raycast Camera Collision (Whisker Whiskers)**:
   - Cast a ray from the character's head to the desired camera position (`RaycastFilterType.Exclude` excluding character + non-collidable VFX parts) and pull the camera forward by `0.5` studs on hit so players never see through walls.

## 2. MCP Tools & Modules
- **`configure_camera_rig`**: Generates a complete client camera controller (`over_shoulder_action`, `isometric_arpg`, `top_down_tactical`, `first_person_flashlight`, `boss_lock_on`).
- **`build_cutscene_sequence`**: Builds keyframed 3D camera tracks with letterbox bars (`CutsceneDirector`).
- **`install_kit_module`**: Install `CameraShaker` and `CutsceneDirector`.

See `references/camera-rigs-and-springs.md` for implementation patterns.
