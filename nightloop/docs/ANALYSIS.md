# Place1 — survey & diagnosis

**Read live from Studio** (placeId `110457957229133`, gameId `8610207536`) on 2026-10-07.
2,068 instances, 7 scripts, StreamingEnabled, terrain present, 0 teams, no ScreenGuis.

## What this is

A **5-phase, 15-minute house horror survival game**. The design bible is sitting in
`ServerScriptService.List` — a *disabled Script used as a notepad*. It specifies six
entities escalating over 15 minutes:

| Entity | Role | Built? |
|---|---|---|
| **Window Monster** | Rattles windows, must be spotted and repelled | Code exists, **non-functional** |
| **Whisperer** | Fake audio, mimics window sounds, calls your name | Not started |
| **Knocker** | Door knocks and slams | Not started |
| **Crawl** | Skitters across ceilings/walls, drops into rooms | Not started |
| **Breathless** | Makes a room suffocating | Not started |
| **Ticking Man** | Freezes player time in phase 5 | Not started |

Plus one entity *not* in the design doc: a **floating align-based Stalker**
(`Workspace.BASEmodel.Script`, 648 lines) that approaches from behind the player.

So: roughly one and a half of six entities implemented, and the one that's furthest
along doesn't currently run.

## Current assets

- `Workspace.BASEmodel` — the Stalker. Skinned `Cube` MeshPart + `HumanoidRootPart`,
  Humanoid, `Animations` folder (Idle / Walk / StalkStart / StalkHold), 648-line
  server script with AlignPosition/AlignOrientation movement, ground raycasts,
  float offset, failsafe watchdog. Genuinely sophisticated.
- `ServerStorage.jumpscare` — Window Monster template. A Model containing a single
  `Part`. No PrimaryPart (script falls back correctly).
- `Workspace.Spots` — 8 BaseParts, the monster's teleport destinations.
- `StarterPack.Flashlight` — Handle, Light part, `LightOrigin` attachment holding
  `Light` (SpotLight), `Shadow` (SpotLight), `SurfaceLight`, plus Sound/Sound2.
- `Workspace.Rig` — standard R15 dummy with the default `Animate` script. Test rig.
- Two unnamed 300-part `Model`s and an `OutHouse` — the environment.

---

## Bugs found

### 1. The flashlight crashes on line 2 — BLOCKING

```lua
local lightorig = script.Parent.Light.Attachment   -- ✗ no child named "Attachment"
```

The attachment is named **`LightOrigin`**. This throws immediately, so
`Tool.Activated` is never connected and **the flashlight never turns on at all.**

Note the Window Monster script gets this right (`handle:FindFirstChild("LightOrigin")`)
— only the toggle script is wrong.

### 2. Nothing ever fires `FlashEvent` — BLOCKING

`ServerScriptService.WindowMonster` creates a `FlashEvent` RemoteEvent and listens on
`OnServerEvent`. Grep across all 7 scripts: **zero** `FireServer` calls anywhere in
the place. The entire "shine light → monster retreats" mechanic has no trigger.

The monster therefore spawns, teleports between the 8 spots forever, and can never
be repelled.

### 3. Stalker PrimaryPart was the whole mesh — FIXED

The author's own note at line 648: `--Problem is that the entire primary part is THE whole MODEL.`

`BASEmodel.PrimaryPart` was `Cube`, a **23.6 × 31.4 × 4.5 stud** skinned MeshPart.
Every AlignPosition/AlignOrientation target, pivot and ground raycast was computed
against a 31-stud slab instead of the character root.

**Applied:** `PrimaryPart` → `HumanoidRootPart` (4.0 × 1.0 × 2.0). One Ctrl+Z reverts it.

### 4. Light detection grabs the wrong light

`getToolLightColor` returns the **first** SpotLight/PointLight/SurfaceLight found in
the tool. That's whichever of `Light` / `Shadow` / `SurfaceLight` enumerates first —
and it never checks `.Enabled`, so once the remote is wired a player could repel the
monster with the flashlight **switched off**.

### 5. `Turn` LocalScript leaks a loop per equip

```lua
script.Parent.Equipped:Connect(function()
    ...
    while wait() do ... end    -- never exits on Unequipped
end)
```

Every equip starts another永-running loop that keeps writing `Shoulder.C0`. Equip/unequip
ten times and ten loops fight each other. Also hard-codes R15 (`RightUpperArm`), so it
errors on R6.

### 6. Deprecated API throughout

`spawn`/`wait`/`delay` → `task.*`; `SetPrimaryPartCFrame` → `PivotTo`;
`Enum.RaycastFilterType.Blacklist` → `Exclude`; `CFrame:toObjectSpace`/`.p`/`.unit` →
`ToObjectSpace`/`.Position`/`.Unit`. Also `player.Character.PrimaryPart` in the retreat
handler is unreliable — should be `FindFirstChild("HumanoidRootPart")`.

---

## Recommended order

1. **Fix the flashlight toggle** (`LightOrigin`, symmetric `Shadow`, server-side)
2. **Wire the repel loop** — server-authoritative, checks `Enabled`
3. **Decide repel model:** click-to-flash vs. continuous beam (gameplay decision)
4. Rebuild `Turn` so it stops on unequip and handles R6
5. Modernise deprecated calls in both monster scripts
6. Move the design doc out of a disabled Script into a ModuleScript or `.md`
7. Then start on entity #2 — the **Whisperer** is the cheapest big win: pure audio,
   no rig, no animation, and it makes the Window Monster scarier by poisoning the
   player's trust in sound.
