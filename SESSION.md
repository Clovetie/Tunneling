# SESSION — live connector credentials

> **For the next agent.** Read `/home/user/nightloop/AGENTS.md` first — it
> explains what the bridge is, the job contracts, the workflow, and the traps.
> This file holds only the volatile, session-scoped bits.
>
> **Not committed.** It is gitignored in the repo and lives outside it on
> purpose. Do not paste these values into any tracked file.

## Credentials

**Working connection (2026-10-07): local HANDS MODE, not a tunnel.** The
cloudflare tunnels (`conditions-regard-qualifications-suppliers` /
`programs-lots-grow-further.trycloudflare.com`) are dead and unreachable from
the sandbox anyway (egress allowlist). The live bridge is the **local server
on the user's machine** at `http://127.0.0.1:8077`; the token lives in
`bridge.token` in their `roblox-bridge` dir (do not paste it into a tracked
file — `.\ab.ps1` reads it automatically). To sanity-check from the sandbox
you can't (sandbox cannot reach the user's localhost); the user runs jobs and
pastes output back.

**2026-10-08:** the user's active working copy is a branch ZIP download at
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
- **Pushing code: the repo is PRIVATE (verified 2026-10-07 via the API), so
  `raw.githubusercontent.com` 404s for anonymous requests.** `curl.exe -L -o
  push_climb.lua https://raw.githubusercontent.com/...` downloads a 14-byte
  `404: Not Found`, and running that as a job fails with a syntax error. Do
  not hand it to the user as a one-liner. Delivery that works, in order of
  preference:
  1. **Branch ZIP** (the user already does this - that is where the
     `Tunneling-arena-<id>-tunneling` folders come from). In a logged-in
     browser:
     `https://github.com/Clovetie/Tunneling/archive/refs/heads/<branch>.zip`
     Extract, **copy `bridge.token` into the extracted `roblox-bridge`**, run
     from there.
  2. **Single raw file via the browser** (the session cookie authenticates
     it): `https://github.com/Clovetie/Tunneling/raw/refs/heads/<branch>/jobs/push_climb.lua`
     - save it into the `roblox-bridge` folder that already has the token.
  3. Last resort: paste the file into chat. It is ~50 KB.
  Every generated job is ASCII-only precisely so routes 1 and 2 cannot
  corrupt it (PowerShell's `Set-Content -Encoding ascii` and the CLI's
  default-encoding read both mangle non-ASCII - see `jobs/make_push.py`).
  Do not hand-edit `push_climb.lua`; regenerate with `make_push.py`.

- **The folder has to contain `bridge.token`, or `ab.ps1` refuses to run**
  ("No token found. Set BRIDGE_TOKEN or put a bridge.token file next to
  ab.ps1"). Folders on the user's machine:
  | path | what |
  |---|---|
  | `C:\Users\Gamef\Downloads\Tunneling-arena-6d7d4b8a-tunneling\Tunneling-arena-6d7d4b8a-tunneling\roblox-bridge` | token copied here; the successful Config probe ran from it |
  | `C:\Users\Gamef\Downloads\workspace-01a115c6-25b3-719b-a117-f3400625cd10\roblox-bridge` | the original workspace; **the running server process was started from here** |
  | `C:\Users\Gamef\Downloads\Tunneling-arena-8673c420-tunneling\...\roblox-bridge` | a DIFFERENT Arena session's branch, no token - this is the folder that tripped the user twice |

  Copying `bridge.token` into a new folder is safe and is the fix. What is
  NOT safe is running `setup.py` in a folder that has no token: it mints a
  fresh one and bakes it into the plugin, and the running server then
  rejects the plugin with HTTP 401 (CONNECT.md section 7).

### Jobs in `jobs/`

| file | what it does |
|---|---|
| `push_climb.lua` | GENERATED by `make_push.py`. Installs Config / MonsterAnimator / Entities.WindowMonster as new instances. |
| `climb_check.lua` | read-only. Config, per-spot classification, cycle shape, and a full climb on a scaffold at y=2000. |
| `preview_climb.lua` | run ONE real climb on the real house, animated in the viewport, then a clip score and cleanup. **Run in Edit mode, not during a playtest** - it spawns a second rig that the live entity cannot see. |
| `night_watch.lua` | run DURING a playtest. Samples the live monster for 20s at 2 Hz and reports a trace (height, knee pitch, visibility, strikes) plus whether the climb pose was seen. The entity objects are a local inside Director.lua, so this watches the model instead. |
| `set_config_flag.lua` | flip one indented flag in Config.lua in place (built for `Hud.ShowPhase = false`). Edits `.Source`, so it only takes effect on the next playtest / reload - the require cache is per Instance. |
| `set_whisperer_ids.lua` | fill `Whisperer.WhisperSoundIds` / `FakeGlassSoundIds` from lists at the top of the file. Counts matches first and writes nothing unless each is found exactly once. |
| `whisperer_check.lua` | preloads every Whisperer ID and reports `TimeLength`, so a bad/private ID is caught before a night is played. |
| `baseline_survey.lua` | the package inventory. |
| `drift_audit.lua` | FNV-1a hashes of every live script. Only re-run if drift is suspected for a NEW reason (the 22/22 result was a bug in an older version of this job). |



---

## v5.4 playtest surface + MRTR confirmation gate (2026-11-05, branch `arena/f956d2f6-tunneling`)

The bridge is now `5.4.0` (plugin `ArenaBridge.lua` v4.1.0). This round started from
research — the 2026-07-28 MCP changelog and MRTR/patterns docs, plus Roblox's own
2026 testing-API announcements and the competing Studio MCP servers — and shipped
what that research proved out:

- **A human can be put in the loop, opt-in.** `confirm_gate.py` implements
  `tools/call` → `resultType: "input_required"` → answer → retry, with
  HMAC-signed single-use `requestState` bound to the caller principal, the method
  and a digest of the arguments. `--require-confirm write` is the lockdown;
  `destructive` gates only what is flagged destructive; `all` gates reads too.
- **Two ways to answer, one queue.** Modern clients get the ask in-band
  (needs `elicitation` to answer it), legacy clients are waited on for
  `--confirm-wait` seconds and then told the `requestId` to have approved, parked
  calls can be answered as a task via `tasks/update`, and operators answer out of
  band with `GET|POST /api/confirmations` or `arena_studio.py approve|deny|confirmations`.
  An agent may read the queue and answer *another* principal's request, but
  approving its own is refused with `operator_only`.
- **`subscriptions/listen`** now streams `notifications/resources/updated` on the
  long-lived stdio stream or the SSE `POST /mcp` response
  (`X-Arena-Subscription`, `_meta["io.modelcontextprotocol/subscriptionId"]`), with
  `GET /api/subscriptions` + `POST /api/subscriptions/close` and `arena_studio.py
  subscriptions`. Idle capabilities are reported as `idleTypes`, not faked.
- **Four new tools (75 total).** `simulate_input` (VirtualInput: keys, typing,
  mouse, touch, drags, wheel — and an honest `not_playing` refusal when no playtest
  is running), `manage_device_simulator` (presets, orientation, resolution, DPI,
  scaling mode, custom `DeviceConfiguration` profiles), `run_studio_tests`
  (`StudioTestService` run/play/multiplayer 1–8 players, watchdog, temporary
  runner Script that is always destroyed) and `playtest_capabilities` (what the
  connected Studio actually exposes, against baselines 0.720/0.717/0.700/0.715).
- **Protocol polish:** `_meta`-based version negotiation (it outranks
  `Mcp-Protocol-Version`), `ttlMs`/`cacheScope` on list and read results,
  `X-Arena-Task` on task handles, `-32021` when `tasks/*` is used without the
  extension, `_meta` log level → real `notifications/message` (embedded
  `io.modelcontextprotocol/logEntries` on single-shot HTTP), and OTel
  `traceparent`/`tracestate`/`baggage` carried into jobs and the audit chain.
- **Fixed on the way:** `/api/mcp/events`, `POST /api/confirmations` and
  `/api/subscriptions/close` had been pasted into `do_GET`, so every POST to them
  404'd; `manage_device_simulator`'s schema enum and its validator had drifted
  apart (`action: "set"` was rejected); `bridge_ops` reads were not marked
  read-only, so a locked-down bridge could not even show its own queue; the mock
  treated a requested `dm="client"` as proof a play client existed.
- **Verified:** `python3 -B roblox-bridge/test_bridge_mcp.py` → **64 tests, OK**
  (was 43), including `ArenaBridge.lua` through the bridge's own `luau_check`
  (clean, no unused locals) and a new test that fails if any module's version
  string disagrees.
- **CI:** the workflow is committed at `.github/ci.yml` (suite on 3.10/3.12/3.13,
  a stdlib-only import audit with guarded optional imports excepted, an undefined
  Luau global scan over every `.lua`, the kit licence manifest, and a guard that
  each doc claiming a bridge version agrees with `server.py`). It does **not**
  run from there: GitHub refuses any push that creates a file under
  `.github/workflows/` unless the acting GitHub App holds the `workflows`
  permission, and this session's App does not (a `git push` is rejected by a
  server-side hook; the Contents API answers `403 Resource not accessible by
  integration`). Activate it with `git mv .github/ci.yml .github/workflows/ci.yml
  && git push`, or grant the App Workflows: Read and write and move it yourself.
  Every step is also a plain local command, so CI is convenience, not a gate on
  contributing.
- **Still needs a live peer:** the four playtest tools are proven against the mock
  and contract-identical plugin handlers, but a real Studio run is what confirms
  CoreGui refusal messages, device-override effects on the viewport image, and
  `StudioTestService` end-to-end. `jobs/live_acceptance.py` is the harness; the
  capability probe is `playtest_capabilities`.

---

## v5 ops platform (2026-11-03, branch `arena/706ae6d5-tunneling`)

The bridge was `5.3.1` when this section was written (now `5.4.0` — see above).
New runtime state lives in **gitignored**
`roblox-bridge/bridge_state/` (`audit.jsonl`, `tokens.json`, `bus.json`,
`tunnel.json`). Nothing there is needed to connect Studio.

- **Supervised tunnel:** `python setup.py --tunnel` (or `server.py --tunnel`).
  Named tunnel: `--tunnel-token`. External tunnel: `--public-url`. The bridge now
  **refuses to publish itself with no token** (state `blocked`), so do not pass
  `--tunnel` to a token-less server and expect a URL.
- **New endpoints:** `GET /api/ops`, `/api/metrics[?format=prometheus]`,
  `/api/events` (SSE; `?once=1` for JSON), `/api/audit[?verify=1]`,
  `GET|POST /api/bus`, `GET|POST /api/tunnel`, `GET|POST /api/tokens` (admin).
  `/api/health` stays public.
- **Scoped tokens:** `bridge.token` stays the admin owner credential. Mint
  read/write/admin tokens with `arena_studio.py tokens create`; read tokens
  cannot mutate Studio.
- **Multi-agent bus:** `arena_studio.py bus …` or MCP `agent_bus`. Partner work
  order for Gemini 4 Argon — remember it is a **model**, not an agent: it returns
  patches/docs, the operator applies and verifies. Files:
  `handoff/GEMINI-4-ARGON.md`, `handoff/bus_seed.json`,
  `handoff/Tunneling-v5-Code.pdf` (88 pp, sources + diffs) and
  `handoff/Gemini-4-Argon-Tasks.pdf` (12 pp, G1–G8 prompt briefs).
  Rebuild the PDFs with `python3 roblox-bridge/tools/build_handoff_pdfs.py`
  (build-time only, needs `reportlab` + `pygments`); seed the board with
  `python roblox-bridge/tools/seed_agent_bus.py`.
- **Self-test** now covers the v5 suite (43 tests total).
- **v5.1 partner tasks landed:** named-token validation + `docs/OPS.md` §1.1
  runbook; `agent_bus sweep` (stale-agent reaper with counters); incremental
  `export since/summaryOnly`; 7 MCP prompts; responsive Ops tab with reconnecting
  EventSource and "copy MCP config (tunnel)"; operator scripts
  `jobs/measure_tunnel_latency.py`, `jobs/live_acceptance.py`,
  `jobs/drill_studio_kill.py`. Live Studio evidence (G7/G8) still needs a peer.
- **v5.3.1 (console diagnostics):** the dashboard now *says* when it is not
  authenticated (`401` → token banner, token saved in `localStorage`, `?token=`
  supported) or out of date (version mismatch banner), and `setup.py --dry-run`
  prints the resolved launch plan (`server`, `tunnel: supervised|external|not
  requested`, dashboard) without starting anything. `--tunnel` warns up front
  when no tunnel binary is on PATH; the watcher reports `error`/`blocked` with
  `lastError` + `logTail`. See `docs/OPS.md` §12.
- **v5.3 (multi-studio + modern MCP):** `GET|POST /api/studios` and the
  `studio_fleet` tool manage several Studio windows — `list`, `set_active` (pin),
  `clear_active`, `label`, and a read-only `broadcast` fan-out with per-peer
  results. Pins live in `bridge_state/fleet.json` and are skipped automatically
  when the pinned Studio closes. The MCP endpoint is dual-era: `server/discover`,
  per-request version in `MCP-Protocol-Version`/`_meta`, `-32022`/`-32020`
  errors, `resultType` on modern results, and task-augmented tool calls
  (`params.task` → `tasks/get` / `tasks/result` / `tasks/list` / `tasks/cancel`).
  The console has a sticky studio strip, pin/broadcast buttons and motion.
- **v5.2 (unattended handoff):** background stale-agent sweep every 300 s
  (`--sweep-interval`, `--sweep-offline`), maintenance pause
  (`POST /api/queue/pause|resume`, Ops Console button), `studio://jobs` +
  `studio://handoff` MCP resources, `jobLatency` p50/p95 in `/api/ops`, and
  `arena_studio.py queue status|pause|resume|clear`.

```
roblox-bridge/docs/OPS.md        full ops reference
roblox-bridge/CHANGELOG.md       5.0.0 through 5.4.0 entries
handoff/*.pdf                    shareable code + task pack
```
