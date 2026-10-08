# SESSION — live connector credentials

> **For the next agent.** Read `/home/user/nightloop/AGENTS.md` first — it
> explains what the bridge is, the job contracts, the workflow, and the traps.
> This file holds only the volatile, session-scoped bits.
>
> **Not committed.** It is gitignored in the repo and lives outside it on
> purpose. Do not paste these values into any tracked file.

## Credentials

**2026-10-08 — RELAY MODE is the mode of record** (see `CONNECT.md` §11 and
`relay/README.md`). The sandbox serves the wire itself:

```
relay (sandbox, running now)   http://127.0.0.1:8787  →  https://8787-i9pfa1g4i0ly7j09arkjg.e2b.app
relay token                    = the user's bridge token (one secret, both hops)
bridge (user's PC)             http://127.0.0.1:8077   (unchanged)
agent CLI                      python3 relay/relay.py state | ping | runfile …
```

The token is intentionally **not** written into this tracked file; it lives in
`.relay-state/relay.json` (gitignored, mode 600) and in the user's
`bridge.token`. The user connected on 2026-10-08 with the token they pasted in
chat (`13bbda…`); rotate with `python setup.py --rotate` when the session ends
and remember a token change needs a Studio restart (CONNECT.md §7).

Verified end-to-end inside the sandbox with the fake rig (`relay/README.md`):
`ping` 251 ms, `survey`, a 7.9 KB `runfile`, SSE push, janitor requeue — all
good. What has *not* happened yet is the first real round trip to the user's
Studio; that is the open item for the next agent.

**Working connection (2026-10-07): local HANDS MODE, not a tunnel.** The
cloudflare tunnels (`conditions-regard-qualifications-suppliers` /
`programs-lots-grow-further.trycloudflare.com`) are dead and unreachable from
the sandbox anyway (egress allowlist). The live bridge is the **local server
on the user's machine** at `http://127.0.0.1:8077`; the token lives in
`bridge.token` in their `roblox-bridge` dir (do not paste it into a tracked
file — `.\ab.ps1` reads it automatically). To sanity-check from the sandbox
you can't (sandbox cannot reach the user's localhost); the user runs jobs and
pastes output back.

**2026-10-08 (later): the user's active working copy is a branch ZIP of THIS
branch** — `C:\Users\Gamef\Downloads\Tunneling-arena-8673c420-tunneling\
Tunneling-arena-8673c420-tunneling` — which contains the whole repo (`relay/`,
`jobs/`, `nightloop/`) plus their copied-in `roblox-bridge\bridge.token`.
Start of session for the next agent: `START-HERE.md` (one-prompt bootstrap).
The client to hand them is `python .\relay\poll_local.py --url …` run from that
repo root: `poll_local.py` searches for `bridge.token` next to itself, in
`..\roblox-bridge\`, in the cwd, and in `cwd\roblox-bridge`, so nothing has to
be copied anywhere. It is ASCII-only and needs no PowerShell quoting.

**2026-10-08 (earlier):** an older working copy is a branch ZIP download at
`C:\Users\Gamef\Downloads\Tunneling-arena-6d7d4b8a-tunneling\Tunneling-arena-6d7d4b8a-tunneling`
— `bridge.token` was copied into its `roblox-bridge` folder (the ZIP doesn't
ship it; it's gitignored). The running server process is still the one
started from the original workspace folder
(`C:\Users\Gamef\Downloads\workspace-01a115c6-25b3-719b-a117-f3400625cd10\roblox-bridge`);
both folders now hold the same token, so `ab.ps1` works from either. The
PowerShell execution-policy prompt on `ab.ps1` was cleared with
`Unblock-File`.

```bash
# user's machine (PowerShell):
$env:BRIDGE_URL   = "http://127.0.0.1:8077"
$env:BRIDGE_TOKEN = (Get-Content bridge.token).Trim()
.\ab.ps1 ping            # -> pluginVersion 2.0, studio connected
```

Reachability: 2026-10-07 — bridge + Studio LIVE from the user's machine:
`studio_connected: true`, place "scary monster test 3"
(placeId 110457957229133), Studio 0.741.19.7411056. **Protocol:
`CONNECT.md` §4 (HANDS MODE)** — `.\ab.ps1 <cmd>` is the standard one-liner,
longer jobs ship as `jobs/*.lua` + here-string + `runfile`. Plugin v2.0
(auto-connect on load, `pluginVersion` in ping, survey Lighting.Technology
fix) is committed 2026-10-07 and installed on the user's machine — verify
with `.\ab.ps1 ping` → `"pluginVersion": "2.0"`.

**Drift saga: CLOSED 2026-10-07 (session 3).** The v3 audit JSON was never
pasted back, but it no longer matters: after a Studio restart the Config probe
returned `{"ground":true,"rig":true,"on":true,"turn":2}`, which is exactly the
repo's `Config.Entities.WindowMonster` (`Enabled`, `RequireGround`,
`UseBuiltInRig` true, `TurnSpeed = 2.0`). The nil `turn` was the stale require
cache, as diagnosed. **Do not re-run `jobs/drift_audit.lua` for that, and do
not chase drift again** — the earlier "22/22 files differ" was the FNV
double-rounding bug in the job, not real drift (CONNECT.md section 8 item 6).

**Live baseline 2026-10-07 (this sandbox, session 2):** package intact —
14 `NightLoop` entries + `Entities` (7), spots "1"–"8" (8), no
`MonsterPreview`, both client scripts. Live Config: `showPhase=true`,
`night=900`, WindowMonster `{on, ground, rig}` **but `turn` (TurnSpeed) was
nil live** (repo has 2.0 from phase 8) → drift suspected, then DISPROVEN:
the 2026-10-07-07 drift audit ("22/22 hashes differ") was a tool bug — Studio's
Luau rounds the naive FNV multiply through double (>2^53 products); the
live Config.lua byte sample was identical to the repo file. Corrected audit
`jobs/drift_audit.lua` v3 (exact decomposed multiply — CONNECT.md §8 items
5–6 gotchas) is the one to run. **`turn` nil = stale require cache** (AGENTS.md
pitfall #1, now observed live) — clearing it is a Studio restart / place
reload, not a code fix. After restart, verify with the smoke script that
`wm.turn == 2.0`.

> **Track state of this file changed.** In this checkout (`Tunneling`,
> commit 5303da6) `SESSION.md` is *tracked* — the "not committed / gitignored"
> header above no longer holds. Token therefore sits in repo history. Rotate
> with `python setup.py --rotate` when this session ends, and consider
> `git filter-repo` if the repo is ever public.

## Check it in one call (HANDS MODE — user's PC)

```powershell
# in roblox-bridge on the user's PC (PowerShell: use the CLI, not curl —
# PS aliases curl to Invoke-WebRequest):
.\ab.ps1 health        # -> ok: true, studio_connected: true
.\ab.ps1 ping          # -> place "scary monster test 3 ", placeId
                       #    110457957229133, pluginVersion "2.0"
```

The sandbox itself cannot reach the user's localhost (egress allowlist), so
these checks always run on the user's machine and the output comes back
pasted. (`/api/health` also exists over HTTP. There is no `/api/status` —
it returns `{"error":"not found"}`. `studio_connected: false` means the
local server is up but Studio is closed or the plugin is off.)

If that fails, or jobs start timing out: the **local server** on the
user's machine has stopped (it dies with the machine/terminal, and there is
no tunnel to fall back on). Ask the user to start it again:

```bash
cd path/to/roblox-bridge  # the real path on their machine
python server.py          # serves http://127.0.0.1:8077
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

_Last verified 2026-10-07 (Asia/Bangkok). Phases 1-8; session 3 built the
climbing animation (not yet playtested)._

Phases 1-8 are done and checked against a live night, not a unit test.

**Session 3 — the Watcher climbs (built, awaiting a playtest).** A spot with no
floor under it used to be skipped, so the upper-floor windows were dead. Now it
is climbed to: the Watcher blinks to the foot of the wall and hauls itself up
where you can see it. `Config.Entities.WindowMonster.Climbing` holds the
tunables; `MonsterAnimator` gained its first cycling pose (`climb`, driven by
`SetCycle(phase)` from distance climbed, not the clock); `WindowMonster`
gained `_classify` / `_poseCFrame` / `_startClimb` / `_updateClimb` / `_goTo`.
You can flash it mid-climb and it loses its grip. Pushed to the live place by
`jobs/push_climb.lua` (generated by `jobs/make_push.py`), verified by
`jobs/climb_check.lua` — see `nightloop/docs/CHANGES.md` for the write-up.
`Config.Hud.ShowPhase` is still `true` (testing not finished).

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
  never invent IDs.** The moment they arrive it is two commands and no code:
  paste them into the two lists at the top of `jobs/set_whisperer_ids.lua`,
  commit, then the user runs that and `jobs/whisperer_check.lua`.
- `Config.Hud.ShowPhase` must go to `false` when testing ends. Keep the bar.
- `Breathless` and `TickingMan` are stubs. They need room volumes, and nothing
  in the place marks rooms yet.
- **Climbing is built but has never been seen in a playtest.** `jobs/climb_check.lua`
  proves the geometry and the animation numerically on a scaffold at y=2000;
  it does not prove it looks right on the house. Tune
  `Climbing.Speed` / `Stride` / `Grip` after watching one.
- `tools/fix-perms.sh` - workspace snapshots drop the executable bit on the
  toolchain. Run it before `luau-compile` or `check_globals.sh`.
- Pushing code: run `python3 jobs/make_push.py` after editing
  `nightloop/src/*`, commit, and have the user fetch the job as one line:
  ```powershell
  curl.exe -L -o push_climb.lua https://raw.githubusercontent.com/Clovetie/Tunneling/<branch>/jobs/push_climb.lua
  .\ab.ps1 runfile push_climb.lua
  ```
  Do not paste 50 KB of job into chat, and do not hand-edit `push_climb.lua`.
  (`curl.exe` with the .exe: plain `curl` is aliased to `Invoke-WebRequest`
  in PowerShell and `Invoke-WebRequest` may need TLS 1.2 forced.)

### Publishing game code -> the place

`nightloop/src/**` + `nightloop/client/**` are the game; the place is a copy of
them. Two ways to publish, both one command now:

```bash
python3 relay/relay.py drift            # read-only: live vs repo, per file
python3 relay/relay.py push             # dry pass (what would change)
python3 relay/relay.py push --apply     # publish the differences
```

That drives `jobs/make_push_all.py` (generic; supersedes the three-file
`make_push.py`, which is kept for the climb write-up) and the generated
`jobs/push_all.lua` / `jobs/push_all_dry.lua`. The job replaces only files whose
source differs, keeps a replaced script's `Disabled` flag, verifies every
installed file against the repo byte count **and** an FNV-1a 32 hash, and
reports the live `Config` tuning in the same pass. `jobs/repo_hashes.py`
(`--check`) is the repo-side hash table for the same comparison.

Without the relay, the same job runs locally from the user's PC:

```powershell
# download from the relay (browser download — the GitHub repo is private and
# curl.exe gets the preview gate; /jobs/ is served by the relay itself):
#   https://8787-i9pfa1g4i0ly7j09arkjg.e2b.app/jobs/push_all_dry.lua   (dry)
#   https://8787-i9pfa1g4i0ly7j09arkjg.e2b.app/jobs/push_all.lua       (real)
.\ab.ps1 runfile push_all_dry.lua     # look first
.\ab.ps1 runfile push_all.lua         # then publish
```

Run a push in **Edit mode**, not during a playtest (replacing modules mid-run
invalidates `require` for the running session). One job = one Ctrl+Z.

### Jobs in `jobs/`

| file | what it does |
|---|---|
| `push_all.lua` | GENERATED by `make_push_all.py`. The whole package, replacing only what differs; verifies bytes + FNV hash. **The publisher.** |
| `push_all_dry.lua` | GENERATED, read-only: per-file live-vs-repo diff, writes nothing. |
| `push_climb.lua` | GENERATED by `make_push.py` (three files). Superseded by `push_all.lua`, kept for the climb write-up. |
| `climb_check.lua` | read-only. Config, per-spot classification, cycle shape, and a full climb on a scaffold at y=2000. |
| `preview_climb.lua` | run ONE real climb on the real house, animated in the viewport, then a clip score and cleanup. **Run in Edit mode, not during a playtest** - it spawns a second rig that the live entity cannot see. |
| `night_watch.lua` | run DURING a playtest. Samples the live monster for 20s at 2 Hz and reports a trace (height, knee pitch, visibility, strikes) plus whether the climb pose was seen. The entity objects are a local inside Director.lua, so this watches the model instead. |
| `set_config_flag.lua` | flip one indented flag in Config.lua in place (built for `Hud.ShowPhase = false`). Edits `.Source`, so it only takes effect on the next playtest / reload - the require cache is per Instance. |
| `set_whisperer_ids.lua` | fill `Whisperer.WhisperSoundIds` / `FakeGlassSoundIds` from lists at the top of the file. Counts matches first and writes nothing unless each is found exactly once. |
| `whisperer_check.lua` | preloads every Whisperer ID and reports `TimeLength`, so a bad/private ID is caught before a night is played. |
| `baseline_survey.lua` | the package inventory. |
| `drift_audit.lua` | FNV-1a hashes of every live script. Only re-run if drift is suspected for a NEW reason (the 22/22 result was a bug in an older version of this job). |

