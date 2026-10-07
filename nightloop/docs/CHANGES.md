# Changes applied to Place1 — 2026-10-07

Originals backed up in `patches/ORIGINAL_*.lua`. Each job was one undo step in Studio.

## 1. `Workspace.BASEmodel.PrimaryPart` → `HumanoidRootPart`

Was `Cube`, a 23.6 × 31.4 × 4.5 skinned MeshPart. Your own note at line 648 of the
stalker script. Every AlignPosition target, pivot and ground raycast now works off a
4.0 × 1.0 × 2.0 root instead of a 31-stud slab.

## 2. `StarterPack.Flashlight."On/Off"` — rewritten

- `script.Parent.Light.Attachment` → `LightOrigin`. This was throwing on line 2, so
  `Tool.Activated` was never connected and **the flashlight had never worked.**
- `Shadow` is now toggled symmetrically (it was only ever switched off, never on).
- Added `tool:SetAttribute("LightOn", state)` as a clean public signal.
- Beam switches off on `Unequipped`, so you can't stash a lit torch in your backpack.

## 3. `ServerScriptService.WindowMonster` — rewritten for the beam model

**Mechanic.** Hold the beam on the monster for `BEAM_REPEL_TIME` (1.0s) of cumulative
exposure and it retreats. Look away and exposure bleeds off at `BEAM_DECAY_RATE`
(1.5×), so sweeping a room works but a stray flick doesn't. The monster fades toward
`FADE_MAX` (0.65 transparency) as exposure builds — free visual feedback, no new assets.

**No more RemoteEvent.** The old `FlashEvent` was never fired by anything in the place.
Rather than wire a client trigger, the server now polls each player's equipped
flashlight and reads the SpotLight's own `.Enabled`. Fully server-authoritative,
nothing to spoof.

**Hitbox matches the visible cone.** Detection reads `SpotLight.Angle` and
`SpotLight.Range` at runtime:

| | Before | Now |
|---|---|---|
| Cone half-angle | 12.3° tested vs 36° visible | 34.6° of 36° |
| Range | 40 studs vs 15 visible | 15.75 (Range × 1.05) |

Ray sampling rebuilt as centre + two interleaved rings of 4. The old code scaled every
sample by 0.4/0.6, so a nominal 20° cone only ever tested 12.3°.

**Other fixes in the same pass**
- Requires an *equipped* tool (parented to Character), not one sitting in the Backpack.
- Targets the light named `Light` specifically — the old `getToolLightColor` returned
  whichever of `Light`/`Shadow`/`SurfaceLight` enumerated first and never checked
  `.Enabled`, so once wired you could have repelled it with the torch switched off.
- Retreat uses `HumanoidRootPart`, not `Character.PrimaryPart` (usually nil), so the
  teleport-to-farthest-spot actually fires now.
- `spawn`/`wait`/`delay` → `task.*`; `SetPrimaryPartCFrame` → `PivotTo`;
  `RaycastFilterType.Blacklist` → `Exclude`.
- Spots list cached with invalidation on `ChildAdded`/`ChildRemoved` instead of being
  rebuilt on every call.

## Verified in Studio (no playtest needed)

18/18 preflight checks pass: flashlight wiring, 8 spots present, `jumpscare` clones and
exposes a BasePart, PrimaryPart fix holds, both new sources live, no deprecated calls
left, cone geometry covers the visible beam.

---

## Tunables

In `ServerScriptService.WindowMonster`:

```lua
local BEAM_REPEL_TIME = 1.0      -- seconds of light to repel
local BEAM_DECAY_RATE = 1.5      -- exposure lost per second off target
local BEAM_CHECK_INTERVAL = 0.08 -- seconds between beam tests
local FADE_MAX = 0.65            -- transparency at full exposure
local RETREAT_COOLDOWN = 17      -- seconds hidden after a repel
```

Beam reach is **not** in the script any more — change `Angle` / `Range` on
`Flashlight.Light.LightOrigin.Light` and detection follows.

> Worth a look: that SpotLight is `Range = 15`, `Brightness = 0.5`. Short and dim.
> Fine for a horror game, but you now have to get within ~15 studs to repel anything.

## Still outstanding

- `Flashlight.Turn` leaks a `while wait()` loop on every equip; they stack and fight
  over `Shoulder.C0`. Also hard-codes R15, so it errors on R6.
- `BASEmodel.Script` (the stalker) still uses `spawn`/`wait`/`delay` and
  `SetPrimaryPartCFrame` throughout.
- The design doc lives in a disabled Script (`ServerScriptService.List`). Should be a
  ModuleScript or a file in the repo.
- Entities 2–6 (Whisperer, Knocker, Crawl, Breathless, Ticking Man) not started.
