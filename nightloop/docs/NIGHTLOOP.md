# NightLoop

A portable night-loop framework for Roblox horror games. Phase director, entity
registry, client HUD. Built for Place1, designed to be copied out of it.

```
ServerScriptService/
  NightLoop/              <- copy this ONE folder to port the system
    Bootstrap   (Script)        the only Script; everything else is a module
    Director    (ModuleScript)  public API — the clock, phases, start/stop
    Config      (ModuleScript)  every tunable in the package
    Registry    (ModuleScript)  discovers + validates entity modules
    EntityBase  (ModuleScript)  shared helpers for entities
    Signal      (ModuleScript)  tiny signal, no BindableEvents
    Net         (ModuleScript)  creates RemoteEvents on demand
    Entities/
      WindowMonster   active
      Whisperer       needs audio IDs
      Knocker         stub, disabled
      Crawl           stub, disabled
      Breathless      stub, disabled
      TickingMan      stub, disabled
      _Template       copy this to add your own

StarterPlayer/StarterPlayerScripts/
  NightLoopClient (LocalScript)   HUD — optional, delete it and nothing breaks
```

## One-night mode

This is the default and the reason the package is shaped this way.

```lua
local Director = require(game.ServerScriptService.NightLoop.Director)

Director:StartNight({ mode = "OneNight", duration = 300 })

Director.NightEnded:Connect(function(result, detail)
    -- result: "survived" | "failed" | "aborted"
end)
```

Set `Config.AutoStart = false` and your own round system owns the lifecycle.
Nothing in the package assumes it is the only thing running.

### Full API

```lua
Director:StartNight(opts?)   -- opts: { mode, duration, intensityBonus }
Director:StopNight(reason)   -- -> NightEnded("aborted", reason)
Director:FailNight(reason, player)  -- -> NightEnded("failed", {...})
Director:IsRunning()  -> boolean
Director:GetState()   -> { running, night, mode, phaseIndex, phaseName,
                           phaseCount, intensity, elapsed, remaining, duration }
Director:Cue(player, cue)    -- one-off client cue

Director.NightStarted : Signal(nightNumber)
Director.PhaseChanged : Signal(phase, index)
Director.NightEnded   : Signal(result, detail)
```

`Config.Mode = "Endless"` chains nights instead, adding
`EndlessIntensityStep` to intensity each time up to `EndlessMaxIntensity`.

## Phases

Five phases over 15 minutes, straight from the design doc. `Intensity` (0–1)
is what entities read to scale their aggression.

| # | Name | Starts | Intensity |
|---|---|---|---|
| 1 | Unease | 0:00 | 0.15 |
| 2 | Tension Builds | 3:00 | 0.35 |
| 3 | House Awake | 6:00 | 0.55 |
| 4 | Overload | 9:00 | 0.78 |
| 5 | Breaking Point | 12:00 | 1.00 |

Verified scaling on WindowMonster:

| | Phase 1 | Phase 5 |
|---|---|---|
| Seconds of beam to repel | 0.92 | 1.60 |
| Min seconds between teleports | 8.1 | 3.0 |
| Retreat cooldown | 18.1s | 7.0s |

## Adding an entity

Copy `Entities/_Template`, rename, add a matching `Config.Entities` block.
Modules starting with `_` are ignored.

```lua
function Entity:Validate(ctx) return true end   -- deps present?
function Entity:Start(ctx) end                  -- night begins
function Entity:OnPhase(phase, ctx) end         -- phase changed
function Entity:Update(dt, ctx) end             -- every heartbeat
function Entity:Stop(ctx) end                   -- night over
```

`ctx` carries `Config`, `Director`, `Net`, `Intensity`, `PhaseIndex`, `Phase`,
`Elapsed`, `Remaining`, `Night`, `Random`.

Helpers from `EntityBase`: `self:ByIntensity(easy, hard, ctx.Intensity)`,
`self:Track(connection)` and `self:Own(instance)` for automatic cleanup,
`self:Log()` / `self:Warn()`.

### Validation is the portability trick

`Validate` returning `false, reason` drops the entity with a clear warning
instead of erroring the night. Drop this folder into a place with no
`Workspace.Spots` and WindowMonster simply disables itself — everything else
still runs. That is what makes it safe to reuse.

Live example from the current place:

```
[NightLoop] Whisperer disabled — no WhisperSoundIds set —
            add audio IDs in Config.Entities.Whisperer
```

## The monster: rig + procedural animation

`MonsterRig.lua` builds **The Pale Watcher** from primitives at runtime — 27
parts, 26 Motor6D joints, 9.85 studs tall (nearly double the player). Nothing to
import, no asset IDs: a skinned mesh would need an uploaded KeyframeSequence,
but a Motor6D rig can be posed from code, so the whole character ships as
source. Proportions are deliberately wrong — too tall, arms too long, narrow
head — because the silhouette is what reads through a window at night.

`MonsterAnimator.lua` animates it procedurally. Poses are per-joint CFrame
offsets blended toward each frame, with breathing, sway and an
intensity-scaled tremor layered on top so it never holds perfectly still.

| State | What it does |
|---|---|
| `idle` | breathing, weight shifting |
| `watch` | head tracks the nearest player, body unnaturally still |
| `twitch` | single hard jerk, auto-returns (fires more often as intensity rises) |
| `recoil` | flashed: throws back, forearms up across the face |
| `lunge` | breaching: drives forward, arms reaching, jaw wide |
| `retreat` | folds down and away |

See `monster-posesheet.svg` — rendered from the live rig geometry, not concept
art. A preview copy stands beside the front door in Workspace.

### Axis convention (the thing that will bite you)

The sign flips depending on which side of the joint a part sits:

- **Limbs hang below their joint** (arms, legs) — `+X` swings them **forward**.
  Knees bend on `-X`.
- **The spine sits above its joint** (Waist, Spine, NeckLower, Neck) — `-X`
  leans **forward**. The opposite.

Rotations also compound down the chain: Waist, Spine and NeckLower each at 20
degrees folds the body 60. Keep torso values small.

### Two traps worth knowing

1. **Hit detection.** Every rig part is `CanCollide = false`, and
   `Beam.castThrough` deliberately penetrates non-collidable parts — so the
   flash punched straight through the monster and never registered.
   `Beam.coneHitsTarget` now counts parts the beam **passed through** as well
   as the one it stopped on.
2. **`setHidden` anchors.** A rig anchors **only its root**; anchoring the limbs
   freezes every Motor6D and the animation dies silently. Each part also stores
   its own `BaseTransparency` so showing it again never guesses.

Set `Config.Entities.WindowMonster.UseBuiltInRig = false` to go back to cloning
`ServerStorage.jumpscare` instead.

### It stands on the floor, and it looks at you

Two placement bugs that made it read wrong:

**It hovered.** Placement was `spot.CFrame + Vector3.new(0, 0.5, 0)`, which
ignores the floor entirely. `_placeAt` now raycasts down for a surface and
offsets the pivot by however far the model extends below its own pivot
(measured from `GetBoundingBox`, so it works for the rig *and* for a template
model). Measured result: feet at **−0.00 studs** from the floor.

`RequireGround = true` also makes it skip any spot with no floor within
`GroundSearch` studs — the Watcher walks, it does not climb. If *no* spot is
grounded, `Validate` fails with a clear reason instead of spawning it in
mid-air. All 8 of your spots currently pass.

**It stared past you.** Three separate causes:

1. Placement inherited the **spot part's** orientation, so the Watcher faced
   whatever arbitrary direction a 1×1×1 marker happened to point — usually away
   from the room. Head tracking is clamped to a human-ish arc, so it could
   never turn far enough to recover. It now turns to face the nearest player on
   spawn and on every teleport (**0.0°** facing error).
2. The look-at **pitch was negated**. Same above/below-the-joint trap as the
   poses: the head sits *above* its joint, so `-X` pitches it down. Negating
   the pitch made it crane *upward* while you stood below it. Worst-case stare
   error went from **~24° to 10°**, typically 5–8°.
3. Facing was only set on spawn — but at night start the player's `Character`
   usually has not replicated yet, so there was nobody to face and it landed on
   a default heading. `Update` now swivels the body toward the nearest player
   continuously at `TurnSpeed` (2.0), because the head alone is clamped to a
   human arc and can never recover from a badly-oriented body.

### Undefined globals will bite you

Luau in non-strict mode reads an unknown global as `nil`. Deleting a local
function and missing one call site produces code that compiles, pushes, and
then throws at runtime inside a signal handler where you may never see it.
That exact bug — one orphaned `placeAt(...)` call left in `_repel` — hid the
monster the instant you flashed it, then swallowed the relocation and the
`monsterRepelled` cue.

Run `tools/check_globals.sh` before every push. It wraps `luau-analyze` and
filters out the Roblox API surface it has no types for, leaving only real
undefined references.

## First person

`FirstPerson.client.lua` (StarterPlayerScripts, standalone — no dependency on
the package). Locks the camera to first person and keeps your own body
rendered. Three things the naive version gets wrong, all fixed:

**1. Hair and hats in your face.** Hiding only the part literally named `Head`
leaves every head accessory rendering a few studs in front of the camera. The
old heuristic looked for an attachment whose name contained `"hat"` — which
misses `HairAttachment` entirely, so hair hung in the middle of the screen.
Accessories are now resolved by following the `Handle`'s weld back to the body
part it is attached to, so anything welded to the head is hidden regardless of
what it is called.

**2. Fighting the engine every frame.** Roblox's `TransparencyController`
re-applies `LocalTransparencyModifier = 1` to the whole character continuously
in first person, which is why one-shot loops "don't work". Instead of
re-walking `GetDescendants()` on every `RenderStepped`, each part gets a
`GetPropertyChangedSignal("LocalTransparencyModifier")` watcher that pins the
value back the instant the engine changes it. Tool descendants are skipped,
matching engine behaviour. The pinned value is `part.Transparency`, not a
hardcoded `0`, so genuinely invisible parts stay invisible.

**3. Camera inside the skull.** `LockFirstPerson` puts the camera at the centre
of the head, so you look out from inside your own face and the torso clips the
near plane. `Humanoid.CameraOffset` now pushes it forward `EYE_FORWARD` studs
to roughly eye position, with a forward raycast that pulls it back when you
walk into a wall so it never pokes through geometry.

Also guards `BindToRenderStep` against re-binding the same name, which threw on
the first respawn.

Tunables at the top of the file: `ENABLED`, `SHOW_OWN_BODY`, `EYE_FORWARD`,
`WALL_SKIN`.

## Fail state

Straight from the design doc: *"Fail → Window Monster breaches too many times,
or player succumbs to another monster's effect."* So it is a shared strike
budget, not an instant catch.

```lua
Config.Survival = {
    MaxStrikes = 3,
    StrikeGrace = 2.5,   -- immunity window, stops one event double-hitting
}
```

Anything that can hurt the player calls **`Director:AddStrike(source, detail)`**.
It returns `true` if the strike landed, `false` if the grace window swallowed
it. At `MaxStrikes` the Director calls `FailNight` automatically. Entities never
need to know about each other or about the fail condition — that is the whole
point of routing it through the Director.

### The breach siege

The Window Monster now applies real pressure. Each appearance arms a countdown;
let it expire and it breaks in, costing a strike.

| Phase | Seconds to react | Times it relocates while the clock runs |
|---|---|---|
| Unease | 30.2 | ~2.7 |
| Tension Builds | 25.2 | ~2.6 |
| House Awake | 20.2 | ~2.4 |
| Overload | 14.5 | ~2.2 |
| Breaking Point | 9.0 | ~1.8 |

**The countdown deliberately survives teleports.** Relocating between windows is
how it hunts, not a reprieve — only a successful flash buys time back. (Getting
this wrong makes the game unloseable: it teleports faster than it can breach, so
a per-window timer would reset forever.)

At 45% remaining you get a `breachWarning` cue — a red edge pulse — so a breach
is never unsignalled.

## Crawl

Phase 3 onward. A six-legged shadow that runs across ceilings and walls, and
from phase 4 drops into the room with you.

`CrawlerRig.lua` builds it from 17 parts / 16 Motor6D joints, almost black so it
reads as a silhouette. Animation is a **gait**, not a pose set: six legs on an
alternating tripod driven by phase-offset sine waves, with step frequency and
stride scaled by speed — so it visibly scrambles when it flees.

| Phase | Behaviour |
|---|---|
| 3 | crosses a surface and vanishes — pure paranoia, no threat |
| 4+ | 45% chance to drop into the room at the end of a run |
| dropped | stay within 7 studs for 1.6 s and it lands a **strike** |
| flashed | bolts. The flash buys distance, not safety — it cannot be killed |

Crossing takes **1.44 s** (26 studs at 18 studs/s). The first tuning ran it at
26 studs/s over 20 studs — a 0.75 s blur you would never consciously register.

### It is wall-first because of your place

A survey of the real house found the ground floor has a ceiling 6.7 studs up,
the middle floor has ceilings ~7 studs up, but **the top floor is open sky** —
and an earlier survey of `Workspace.Model` (a different building, centred 50
studs away from your windows) found no ceiling at all. A ceiling-only crawler
would have skipped most appearances in silence.

So it tries a ceiling first, then falls back to the nearest wall, running along
the wall's horizontal tangent at `WallHeight`. It re-casts into the surface
every frame to hug geometry — measured max deviation **0.45 studs**, exactly
`SurfaceOffset`. If it finds neither surface it skips that appearance quietly
rather than spawning in mid-air.

### Pressure curve, all three entities

```
Unease          breach in 30.2s | crawl every 49.5s | knock every 44.6s
Tension Builds  breach in 25.2s | crawl every 42.0s | knock every 37.4s
House Awake     breach in 20.2s | crawl every 34.6s | knock every 30.2s
Overload        breach in 14.5s | crawl every 26.1s | knock every 21.9s
Breaking Point  breach in  9.0s | crawl every 18.0s | knock every 14.0s
```

Two things can now cost you the night: a **window breach** and a **Crawl
contact**. Both route through `Director:AddStrike`, so they share the budget
of 3.

## Atmosphere and the dawn payoff

`Atmosphere.lua` drives Lighting across the night and plays the sunrise on
survival: *"dawn light breaks through the 14 windows."*

It captures every property it touches on start and restores them on stop, so it
never leaves your place edited. Set `Config.Atmosphere.ControlLighting = false`
and it does nothing at all — use that if the host game owns its own day/night
cycle.

> Your place currently sits at `ClockTime = 14.5`, `Brightness = 3`,
> `FogEnd = 100000` — broad daylight. Atmosphere drops it to `ClockTime 0`,
> `Brightness 0.35`, `FogEnd 320` for the night, tightens fog to 110 as
> intensity climbs, then tweens to `ClockTime 6.6` over 8 s when you survive.

## Flash system

Client can only ever *ask*. `FlashInput` fires `FlashRequest`; the server checks
cooldown and charges, boosts the light, enables it for `BurstDuration`, restores
it, and raises `Flash.Fired(player, originCFrame, originPos, spot)`. Entities
subscribe to that signal — they never see the remote.

```lua
Config.Flash = {
    Charges = 3,          -- pips shown on the HUD
    RechargeTime = 7,     -- seconds per charge, refills even outside a night
    BurstDuration = 0.14,
    Cooldown = 0.5,
    BurstRange = 90,      -- the burst reaches much further than the torch
    BurstBrightness = 8,
}
```

Charges tick on their own Heartbeat started from `Bootstrap`, so the flashlight
works in a lobby or sandbox with no night running.

**Client cues:** `flashFired` (FOV punches out 14° then eases back),
`flashRecharged` (small inward breath), `flashEmpty` (pips flash red),
`flashState` (silent resync), `knock`.

## Knocker

Works with a plain anchored door `Part` — **no HingeConstraint needed**. It
jolts the door along its own thinnest axis and snaps it back, escalating from
knocks to slams at `SlamFromPhase`. Audio is optional: with `KnockSoundIds = {}`
you still get the physical rattle.

Live on `Workspace.Door`, every 44.6 s at phase 1 down to 14.0 s at phase 5.

## Hiding the phase title

When you finish testing, in `Config.Hud`:

```lua
ShowPhase = false,   -- hides "Tension Builds" etc., keeps the bar
ShowClock = true,
```

Both are read live from the server state broadcast, so you can flip them
mid-session without restarting.

## Porting checklist

1. Copy `ServerScriptService/NightLoop` into the target game.
2. Copy `NightLoopClient` into StarterPlayerScripts, or skip the HUD.
3. Open `Config` and fix `Config.World` — the names of your Spots folder,
   flashlight tool, and the parts inside it.
4. Set `Config.Mode` and `Config.AutoStart`.
5. Enable only the entities that place supports. Anything missing its props
   disables itself and tells you why.

No hard-coded instance paths outside `Config.World`. No pre-built instances —
`Net` creates its RemoteEvents at runtime.

## Status

- **WindowMonster** — complete, beam-repel, intensity-scaled
- **Whisperer** — complete, needs `WhisperSoundIds`
- **Knocker / Crawl / Breathless / TickingMan** — stubs, disabled
