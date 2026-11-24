# mechanics/checkpoint-respawn — touch checkpoints, ordered progression, respawn at the last one

## 1. What it does

**Player-facing:** walking onto a checkpoint claims it. A small pill near the bottom of the
screen shows `Checkpoint 3 / 12` with a progress bar, and a toast says the checkpoint was
reached. Dying — or rejoining — puts the player back on the highest checkpoint they have
claimed instead of at the start of the level.

**Technical:**
- `CheckpointOrder` (pure, no DataModel) owns every progression decision: what the highest
  claimed checkpoint is, whether a given one may be claimed next, where a player respawns,
  and how far through the course they are. `tests/CheckpointOrder.spec.lua` covers it, so
  the rules can be changed without a Studio session.
- Checkpoints are found by `CollectionService` tag (`Checkpoint`) or as children of
  `Workspace.Checkpoints`. The id is the `CheckpointId` attribute or the instance name; the
  order is the `CheckpointOrder` attribute or the sibling position. `CheckpointOrder.audit`
  reports duplicate or non-integer orders at startup rather than letting `pairs()` decide
  which of two checkpoints at order 3 wins.
- **Server-authoritative.** Touch claims are detected on the server (`Touched` plus a living
  `Humanoid`), so nothing about them is client-supplied. The remote path exists for games
  that claim by proximity or prompt instead, and every request is checked three ways before
  it is believed: distance from the checkpoint (`CLAIM_RADIUS`), a per-player cooldown, and
  a `NetGuard` rate limit. A forged `CheckpointClaim` for a checkpoint the player is not
  standing next to is dropped silently.
- `STRICT_ORDER` (on by default) means only the *next* unclaimed checkpoint can be claimed:
  touching checkpoint 7 while running past checkpoint 2 does not skip the level. A game that
  wants free roaming sets it to `false` and any unclaimed checkpoint can be taken.
- Progress is persisted through `shared.DataServiceAPI` into `data.checkpoints.claimed`. If
  `core/data` is not installed the system warns once and keeps progress in memory for the
  session instead of failing — and a claim made in the seconds before a profile finishes
  loading is merged into the loaded save, not thrown away.
- A checkpoint id that no longer exists in the place is ignored everywhere (highest,
  progress, respawn), so deleting a checkpoint from a level cannot strand a player at a
  respawn point that is not there.
- `PARTY_SHARE` is **off by default**. On, a claim also grants the same checkpoint to every
  player within `PARTY_RADIUS` — through the same claim path, so each player's own
  progression still applies. A game that wants a strong player to carry the party sets
  `STRICT_ORDER = false` as well.
- `shared.CheckpointAPI` is assigned before any yield, so another server script can use it
  immediately: `GetCurrent`, `GetClaimed`, `GetProgress`, `Claim(player, id)` (for a quest or
  purchase that awards a checkpoint), `TeleportTo(player, id)`, `GetOrder`, `GetCheckpoints`
  and `Rescan()` after the place edits its checkpoints at runtime.

## 2. Depends on

- `systems/core`: remotes (`Remotes.event`), and data (`shared.DataServiceAPI`) when progress
  should survive a rejoin.
- Optional: `modules/NetGuard.lua` **and its sibling `modules/Throttle.lua`** for the claim
  rate limit. Without them the distance and cooldown checks still apply and the system warns
  once at startup. Copy all three files or none.
- Nothing from `systems/ui/kit`: the pill and the toast are built here so the system installs
  on its own.

## 3. Install

| File | Destination |
| --- | --- |
| `server/CheckpointService.server.lua` | `src/server/` |
| `client/CheckpointUI.client.lua` | `src/client/` |
| `shared/CheckpointConfig.lua` | `src/shared/` |
| `shared/CheckpointOrder.lua` | `src/shared/` |
| `tests/CheckpointOrder.spec.lua` | `tests/` |
| `modules/NetGuard.lua` + `modules/Throttle.lua` | `ReplicatedStorage` (optional, rate limit) |

**DataTemplate:** add `checkpoints = { claimed = {} }`.

**Place content:** either tag each checkpoint Part with `Checkpoint`, or put them in a
Folder named `Checkpoints` in `Workspace`. Give each one a `CheckpointId` attribute (string)
and a `CheckpointOrder` attribute (integer ≥ 1). Without the order attribute the sibling
position is used, which is fine for a folder built by hand and fragile for one built by
script — set the attribute.

**Config (`CheckpointConfig.lua`):**

| Key | Default | Meaning |
| --- | --- | --- |
| `CHECKPOINT_TAG` | `"Checkpoint"` | `CollectionService` tag on the claim Part |
| `CHECKPOINT_FOLDER` | `"Checkpoints"` | Fallback: a Folder in `Workspace`, children in order |
| `ID_ATTRIBUTE` / `ORDER_ATTRIBUTE` | `"CheckpointId"` / `"CheckpointOrder"` | Attributes, else instance name / sibling position |
| `STRICT_ORDER` | `true` | Only the next checkpoint can be claimed |
| `DEFAULT_CHECKPOINT_ID` | `nil` | Respawn point before anything is claimed; `nil` leaves the game's own `SpawnLocation`s alone |
| `TOUCH_TO_CLAIM` | `true` | Server-side `Touched` claiming. With `false`, the client asks by proximity and the server still decides |
| `CLAIM_RADIUS` | `24` | Studs a player may be from a checkpoint for a *requested* claim |
| `CLAIM_COOLDOWN` | `0.5` | Seconds between claims per player |
| `CLAIM_RATE_LIMIT` / `CLAIM_RATE_WINDOW` | `8` / `2` | `NetGuard.allowRate` window for the claim remote |
| `RESPAWN_AT_CHECKPOINT` | `true` | Move a new character to the highest claimed checkpoint |
| `RESPAWN_OFFSET_Y` | `3` | Studs above the checkpoint, so a spawn does not intersect the platform |
| `SAVE_KEY` | `"checkpoints"` | Profile field holding `{ claimed = { id = true } }` |
| `REQUIRE_DATA_SERVICE` | `false` | `true` makes a missing `core/data` a loud warning instead of an in-memory fallback |
| `PARTY_SHARE` / `PARTY_RADIUS` | `false` / `60` | Grant a claim to nearby players too |
| `REMOTE_CLAIM` / `REMOTE_STATE` | `"CheckpointClaim"` / `"CheckpointState"` | Remote names |
| `UI_ENABLED` | `true` | Show the pill and the toast |
| `UI_TITLE` | `"Checkpoint"` | The noun the game renames (`"Stage"`, `"Bonfire"`, `"Gate"`) |
| `UI_ANCHOR_X` / `UI_ANCHOR_Y` | `0.5` / `0.84` | Pill anchor, as scale |
| `UI_TOAST_SECONDS` | `2.5` | How long the "reached" toast stays |
| `UI_PROGRESS_FORMAT` / `UI_REACHED_FORMAT` | `"%d / %d"` / `"%s reached"` | `string.format` patterns |

No ids or assets to create.

## 4. Remotes

| Remote | Direction | Payload |
| --- | --- | --- |
| `CheckpointClaim` | client → server | `id: string`. Ignored unless it is a string ≤ 64 chars naming a known checkpoint the player is within `CLAIM_RADIUS` of, inside the cooldown and the rate limit, and next in order. |
| `CheckpointState` | server → client | `{ claimed, current, currentOrder, next, nextOrder, done, total, fraction }`. Sent on join, on every claim (including a party-shared one) and after a rescan. |

The client never tells the server what it has claimed; it asks, and the server answers with
the state it computed.

## 5. Verify in Studio (MCP)

1. Build four Parts in `Workspace.Checkpoints` with `CheckpointId` `start`/`bridge`/`gate`/
   `keep` and `CheckpointOrder` 1–4, then `start_stop_play` and `get_console_output`: no
   errors, and no `checkpoint-respawn:` warning (a warning means a checkpoint has no order).
2. **Claim by touch:** `execute_luau` (Server) to move the character onto `bridge`'s Part —
   or walk there in Play mode — then read the state:
   `shared.CheckpointAPI.GetCurrent(game.Players:GetPlayers()[1])` → `bridge, 2`.
3. **Strict order:** `shared.CheckpointAPI.Claim(player, "keep")` → `false, "out_of_order"`.
   `Claim(player, "gate")` → `true`. Order matters, not proximity.
4. **Unknown id:** `Claim(player, "nowhere")` → `false, "unknown_checkpoint"`.
5. **Forged remote:** `execute_luau` (Client) with
   `ReplicatedStorage.RemoteEvents.CheckpointClaim:FireServer("keep")` while standing at the
   start: nothing is claimed, because the server checks distance. Then fire `42`, `nil` and
   `string.rep("x", 200)`: no reply, no error in the output.
6. **Rate limit:** fire the remote 20 times in a second from the client and confirm
   `NetGuard` drops the excess (`GetAnomalies` if `NetworkGuard` is installed instead, or
   simply that the claim count does not exceed `CLAIM_RATE_LIMIT` per window).
7. **Respawn:** claim `gate`, then `execute_luau` (Server) `player:LoadCharacter()`: the new
   character's `HumanoidRootPart.Position` is `gate`'s position plus `RESPAWN_OFFSET_Y`.
8. **UI:** `execute_luau` (Client): `PlayerGui.CheckpointUI.Pill.Counter.Text` reads `3 / 4`,
   and `BarFill.Size.X.Scale` is `0.75`.
9. **Deleted checkpoint:** delete the `gate` Part, `shared.CheckpointAPI.Rescan()`, then check
   `GetCurrent` → `bridge, 2` (the stale claim is ignored, not honoured).
10. **Persistence:** with API access on, rejoin: `GetCurrent` is still `bridge`.
11. **Party share:** set `PARTY_SHARE = true`, put two characters within `PARTY_RADIUS`,
    claim one checkpoint: both players' state updates.
12. **Specs:** run `tests/CheckpointOrder.spec.lua` through TestEZ (see
    `roblox-kit/modules/README.md#specs`). 28 cases over the ordering, claiming, respawn and
    progress rules.

## 6. Origin

New in v6.1.0 (P5-K1). The kit shipped 24 systems and none of them handled death and
respawn, which is the most-copied mechanic in any Roblox game: every obby, every stage-based
simulator and every horror map re-derives it, usually with the client deciding where it
respawns.

Design choices worth knowing before editing:

- The ordering rules live in one pure module so they can be changed and verified without a
  Studio session, and so the server and any future client prediction cannot disagree.
- `claim` returns a **new** table rather than mutating the caller's, so a refused claim
  cannot half-apply to a save profile.
- Respawn moves the character rather than parenting a `SpawnLocation`, because a game's
  existing spawns belong to the game: with `DEFAULT_CHECKPOINT_ID = nil` and nothing claimed,
  this system does not touch where a player spawns at all.
- The UI is self-contained so the system installs into a game that does not use
  `systems/ui/kit`; a game that does use it can delete `CheckpointUI.client.lua` and drive
  its own HUD from `CheckpointState`.
