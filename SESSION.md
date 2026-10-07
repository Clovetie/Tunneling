# SESSION — live connector credentials

> **For the next agent.** Read `/home/user/nightloop/AGENTS.md` first — it
> explains what the bridge is, the job contracts, the workflow, and the traps.
> This file holds only the volatile, session-scoped bits.
>
> **Not committed.** It is gitignored in the repo and lives outside it on
> purpose. Do not paste these values into any tracked file.

## Credentials

```bash
export BRIDGE_URL="https://programs-lots-grow-further.trycloudflare.com"
export TOK="13bbda5372ab06c90cc84d7e"
```

Last confirmed alive: 2026-10-07, by a `run_luau` round trip that returned
real data from the open place.

## Check it in one call

```bash
curl -s -m 15 "$BRIDGE_URL/api/health?token=$TOK" | jq .
```

If that fails, or jobs start timing out, the tunnel is dead — **this is
normal**, quick tunnels do not survive a restart. Ask the user to run:

```bash
cd path/to/bridge          # the real path on their machine
python setup.py --tunnel
```

...and to paste the new `https://<words>.trycloudflare.com` URL. The token
survives restarts (it is stored in `bridge.token`) unless they pass
`--rotate`. Update this file with whatever they give you.

**The tunnel runs on the user's machine, not in the sandbox.** Nothing an
agent does can keep it open; the user must leave that terminal running. Do not
try to host the server in the sandbox — see the "Dead ends" section of
AGENTS.md for why that is settled.

## Smoke test that proves end-to-end control

```bash
cat > /tmp/ping.lua <<'LUA'
local HttpService = game:GetService("HttpService")
return HttpService:JSONEncode({
  place = game.Name,
  nightloop = game:GetService("ServerScriptService"):FindFirstChild("NightLoop") ~= nil,
  spots = #workspace.Spots:GetChildren(),
})
LUA
jq -n --arg code "$(cat /tmp/ping.lua)" \
   '{type:"run_luau",payload:{code:$code},note:"smoke test",wait:30}' \
 | curl -s -m 60 -X POST "$BRIDGE_URL/api/jobs?token=$TOK" \
        -H 'content-type: application/json' --data-binary @- \
 | jq -r '.result.returned' | jq .
```

Expected: the package is present and `Spots` has 8 children.

## Where things are

| Path | What |
|---|---|
| `/home/user/nightloop/` | **the export repo** — committed, clean, ready to push |
| `/home/user/roblox-bridge/` | the original working directory (bridge + `nightloop/` source) |
| `/home/user/roblox-bridge/tools/` | `luau-compile`, `luau-analyze`, `check_globals.sh` |

The repo is a copy. If you edit `roblox-bridge/nightloop/*`, re-sync into
`nightloop/src/` before committing, or just work in the repo and push from
there.

## State of play

_Last verified 2026-10-07 (Asia/Bangkok)._

Phases 1-8 are done and checked against a live night, not a unit test.

**Just fixed (phase 8):**
- **Invisible monster** - `_repel` still called `placeAt(...)`, a local that had
  become the method `_placeAt`. Non-strict Luau reads an unknown global as
  `nil`, so it compiled clean and threw at runtime *after* `setHidden(true)`.
  First flash made it disappear for good. Guard against the whole bug class
  with `tools/check_globals.sh`.
- **Watcher did not look at you** - the body heading was only set on spawn, and
  at night start the player's `Character` has usually not replicated, so there
  was nobody to face. `Update` now swivels the body at `Config.WindowMonster
  .TurnSpeed` (2.0). The head alone is clamped to a human arc and cannot
  recover from a bad body heading.
- **`Workspace.MonsterPreview` deleted.** This was a static anchored display
  copy posed once by the front door: no animator, not registered with the
  Director, so it could not animate, could not turn, and ignored the flash.
  Every symptom the user reported matched it exactly, and it sat where the real
  entity never spawns. The live entity was always fine.

**Live-night verification (real Director, real Heartbeat):**

| check | result |
|---|---|
| night running | yes |
| parts visible | 26 / 26 |
| anchored parts | 1 (the root only - anchoring limbs freezes Motor6Ds) |
| grounded | feet 0.00 studs off the floor |
| animates over wall-clock | Spine C0 -0.03131 -> -0.02283 -> -0.02011 |
| flash from the front | registers a hit |
| cleanup | nothing left in Workspace, Lighting restored |

Edit mode has no `Players`, so head tracking and body facing cannot be
exercised there - those are unit-tested (yaw +65.4 / -61.5, stare error 5-8
typical, worst 10). They need a real playtest to confirm end to end.

**Open items for the next agent:**
- `Whisperer` is written but disabled: it needs audio asset IDs. **Ask the user,
  never invent IDs.**
- `Config.Hud.ShowPhase` must go to `false` when testing ends. Keep the bar.
- `Breathless` and `TickingMan` are stubs. They need room volumes, and nothing
  in the place marks rooms yet.
- The Watcher skips ungrounded spots (`RequireGround`). A climbing animation
  would unlock the upper-floor windows - the user has approved that trade.
- `tools/fix-perms.sh` - workspace snapshots drop the executable bit on the
  toolchain. Run it before `luau-compile` or `check_globals.sh`.

