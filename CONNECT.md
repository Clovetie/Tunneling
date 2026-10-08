# CONNECT — Arena ↔ Roblox Studio bridge, operator's manual

**For the next agent.** This file is the entire connection story, written
2026-10-07 after a full session of debugging exactly this. Read it once, top
to bottom, before touching anything connection-related. **Do not re-derive any
of this** — every fact below was paid for with real debugging time and, where
marked, verified against Roblox's official documentation.

Credentials live in `SESSION.md` (repo root). They never live here.

---

## 0. The 30-second version

> **2026-10-08 — check §11 first.** There is now a **RELAY MODE**: the *sandbox*
> serves the wire on a public preview URL (`https://<port>-<sandboxId>.e2b.app`)
> and the user's **browser** (zero install) or `relay/relay.ps1` polls it and
> drives their local bridge. That inverts the direction that made HANDS MODE
> necessary and needs no tunnel at all. Use it; HANDS MODE (§4) is the fallback
> when the preview URL is not reachable from the user's machine.
>
> ```bash
> python3 relay/relay_server.py --port 8787 --token <shared secret> --url <preview url>
> python3 relay/relay.py state      # clients attached? Studio connected?
> python3 relay/relay.py ping       # health through the whole chain
> ```
>
> Read `relay/README.md` for the mechanics, the test rig, and the endpoints.

1. Get `BRIDGE_URL` + `TOK` from `SESSION.md`.
2. One call: `curl -s -m 15 "$BRIDGE_URL/api/state?token=$TOK"`
3. Branch on the result:
   - **200, `studio_connected: true`** → connected. Work.
   - **`studio_connected: false`** → tunnel is up but Studio is closed or the
     plugin is off. Ask the user to open the place (with plugin v2 it
     auto-connects — no toolbar click).
   - **curl exit 35 / `SSL_ERROR_SYSCALL`** → the sandbox egress is blocking
     the tunnel (§5). **Do not fight it. Switch to HANDS MODE (§4).**
     Spend no more than ~5 minutes re-verifying this.
   - **TLS works but 404 / 502 / timeout** → the tunnel is dead (§6). Ask the
     user to re-run `python setup.py --tunnel` and paste the new URL; update
     `SESSION.md`.
   - **`{"error":"unauthorized"}`** → wrong token. The user's
     `roblox-bridge/bridge.token` is canonical.
4. In HANDS MODE the user runs `.\ab.ps1 <command>` on their own PC — the
   tunnel is not involved at all (§4).

---

## 1. The actors — who can talk to whom

```
 agent sandbox (Linux)                 user's PC                              Roblox Studio
 ─────────────────────                 ─────────                              ─────────────
 agent tools ──HTTPS──▶ *.trycloudflare.com (quick tunnel)
                            │  (cloudflared, user's machine, inbound only)
                            ▼
                     server.py :8077   ◀── GET /api/poll ──  ArenaBridge.lua (local plugin)
                     (job queue +        (one job to ONE      runs jobs on a real Studio
                      dashboard)          poller — atomic      thread, posts /api/result
                                           pop under a lock)
 user's PC processes may also talk to server.py directly: http://127.0.0.1:8077
```

Facts that settle architecture questions (don't re-litigate):

- **The bridge runs on the user's machine, behind their own tunnel.** Hosting
  `server.py` in the sandbox and handing out an `*.e2b.app` preview URL is a
  **settled dead end**: preview URLs return 403 "Sandbox is secured with
  traffic access token" and Studio's `HttpService` cannot send that header.
- **The plugin may talk to localhost.** Roblox's plugin HTTP model exempts
  *local plugins* from the per-domain permission dialog, and localhost access
  is the same mechanism Rojo uses (Rojo's own plugin `RequestAsync`s
  `127.0.0.1:<port>`). The URL must be a full `http://127.0.0.1:8077`
  (bare `127.0.0.1` fails a trust check).
- **Rate limits** (HttpService): ~2000 req/min to localhost in Studio edit
  mode, 500/min in run mode. This plugin polls ~1/s — never a concern.
- **In RUN mode the client-side plugin instance cannot make HTTP requests at
  all** (engine rule: "Http requests can only be executed by game server");
  only the server-side instance can. So in a playtest the poll loop only
  works from the server instance, and a failed connectivity check on the
  client instance is normal, not a bug.
- **Multiple Studio windows are safe**: the server hands each job to exactly
  one poller (atomic queue pop under a lock in `server.py`).
- **Every mutating job is one `ChangeHistoryService` recording** (the
  officially documented plugin pattern: `TryBeginRecording` → make changes →
  `FinishRecording(…, Commit/Cancel)`; it returns `nil` — e.g. in a solo
  playtest — and the plugin handles that). One Ctrl+Z in Studio undoes a
  whole job, including a multi-file `build`.

---

## 2. Which URL from where — the #1 mistake

| Where the request is issued from | URL |
|---|---|
| The Arena sandbox / any non-user machine | `$BRIDGE_URL` — the `https://<words>.trycloudflare.com` tunnel from `SESSION.md` |
| Any process on the user's own PC (CLI, curl, the plugin) | `http://127.0.0.1:8077` |

The committed `ArenaBridge.lua` contains **placeholder** values
(`http://127.0.0.1:8077`, `arena-demo-7f3a`) on purpose. `setup.py` bakes the
real values into the *installed* copy by regex-replacing exactly two lines:

```
local BRIDGE_URL = "…"     →  local BRIDGE_URL = "http://127.0.0.1:<port>"
local BRIDGE_TOKEN = "…"   →  local BRIDGE_TOKEN = "<token from bridge.token>"
```

**Keep those two lines in that exact shape** if you ever edit the plugin.
The plugin also consults persisted settings `plugin:GetSetting("ArenaBridgeUrl"
/ "ArenaBridgeToken")` which override the baked values if present.

---

## 3. Endpoints, job envelope, CLI

Server endpoints (all take `?token=*** the token):

| endpoint | what |
|---|---|
| `GET /api/health` | `{ok, version, studio_connected, studio{place, last_seen, …}, queued, jobs}` |
| `GET /api/state` | same + `recent` job history + `server_time`. **The canonical liveness check.** There is no `/api/status` — it 404s. |
| `POST /api/jobs` | enqueue `{type, payload, note, wait}`; **blocks up to `wait` s (max 120)** and returns the job. Success: read `.result` — for `run_luau` the returned value is `.result.returned` (a string; if your Luau returns JSON, `jq` it). Failure: `.error`. |
| `GET /api/jobs/<id>` · `GET /api/jobs` | job detail / history |

Job types (full payloads in `nightloop/AGENTS.md`):
`ping` · `run_luau` · `read_script` · `read_scripts` · `write_script` ·
`build` · `delete` · `inspect` · `set_properties` · `selection` · `survey` ·
`console`.

**The pattern you will use constantly**: return JSON from Luau via
`game:GetService("HttpService"):JSONEncode(…)` and parse it on this side.

CLI: `arena_studio.py` reads `BRIDGE_URL` (default `http://127.0.0.1:8077`)
and `BRIDGE_TOKEN` from the environment. Subcommands: `health` `ping`
`watch` `survey` `console` `reads` `run` `runfile` `build` `inspect`
`read` `script` `delete`. It prints results pretty-JSON, truncated at 4000
chars — that truncation is in the CLI, not the server.

---

## 4. HANDS MODE — sandbox egress blocks the tunnel

**Status 2026-10-07: this is the mode of record.** The current sandbox cannot
reach `*.trycloudflare.com` (§5), so the user's PC executes the jobs. The
bridge + Studio are verified live this way (place "scary monster test 3",
90+ jobs served).

### The protocol

The user's terminal is at `roblox-bridge` on their PC (Windows PowerShell).

1. **One-time setup** (per §9 below): the `ab.ps1` helper +
   `jobs\baseline_survey.lua` are created in their `roblox-bridge` folder.
   `ab.ps1` sets `BRIDGE_URL`/`BRIDGE_TOKEN` itself and reads the token from
   `bridge.token` — the token never appears in commands or shell history.
2. **Small job (one-liner):**
   ```powershell
   .\ab.ps1 run "return game:GetService('HttpService'):JSONEncode({place=game.Name})"
   ```
   PowerShell rules for the inline Lua: no `$` and no backticks inside the
   double-quoted string; use single quotes inside the Lua.
3. **Anything longer: the here-string pattern** (verbatim — no `$` expansion,
   no quote mangling; always prefer it over a one-liner past ~100 chars):
   ```powershell
   Set-Content -Path job.lua -Encoding ascii -Value @'
   <luau code, verbatim>
   '@
   .\ab.ps1 runfile job.lua
   ```
4. The user pastes the CLI output back. That's the whole round trip.

**PowerShell traps** (all bitten or documented):
- `curl` is an **alias for `Invoke-WebRequest`** — use `curl.exe` or just
  the CLI. (The bash one-liners in older docs will misbehave in PS.)
- Double-quoted strings expand `$var`; single-quoted here-strings `@' … '@`
  are verbatim.
- Command line limit is ~8191 chars — another reason for `runfile`.

### Verification triad (first five minutes, any new session)

```powershell
.\ab.ps1 health
.\ab.ps1 ping
.\ab.ps1 runfile jobs\baseline_survey.lua
```

Expect: `health` → `ok: true`, `studio_connected: true`; `ping` → `[ok]` with
place/placeId/studio and (plugin v2) `"pluginVersion": "2.0"`; baseline → the
JSON in `jobs/baseline_survey.lua`'s header (diff it against `nightloop/src`).

---

## 5. Sandbox egress — facts, verified 2026-10-07

- This sandbox's outbound network is a **fixed allowlist**:
  `github.com`, `codeload.github.com`, `api.github.com`,
  `registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`.
- Anything else fails with the same fingerprint: **curl exit 35,
  `OpenSSL SSL_connect: SSL_ERROR_SYSCALL` at the TLS ClientHello,
  `http_code=000`**, within ~50ms. Verified against
  `*.trycloudflare.com` (both old and new tunnel URLs, IPv4 + IPv6) and
  unrelated hosts (`example.com`, `www.google.com`).
- Controls that work: `github.com` and `pypi.org` → HTTP 200.
- **The allowlist is a platform-level property of how this sandbox was
  provisioned.** It cannot be changed from inside the sandbox, and nothing on
  the user's machine affects it. A previous Arena session had permissive
  egress and reached the tunnel directly — if the user can identify that
  environment/workspace, recreating it is the real fix. Ask; don't assume.
- **Dead end (do not retry): the GitHub Actions relay.** A relay workflow was
  built (agent triggers a workflow; a GitHub-hosted runner POSTs to the
  tunnel) but the Arena GitHub App token has **no Actions scope**: pushing
  workflow files is rejected (`refusing to allow a GitHub App to create or
  update workflow … without workflows permission`) and the Actions API
  returns `403 Resource not accessible by integration`. Settled 2026-10-07.
- Different sandboxes may have different policies — when in doubt, run the
  control check once (curl a non-allowlisted host + one allowlisted host)
  before assuming.

---

## 6. Tunnel lifecycle

- Quick tunnels (`cloudflared tunnel --url http://localhost:8077`) **die on
  every restart** — machine reboot, process exit, sleep. This is normal, not
  anyone's fault. The URL **changes every time**; the token **persists** in
  `roblox-bridge/bridge.token` unless `--rotate` is passed.
- The user re-runs, in a terminal they leave open:
  ```
  cd <their real roblox-bridge path>
  python setup.py --tunnel
  ```
  Then paste the new `https://<words>.trycloudflare.com` URL to the agent →
  update `SESSION.md`.
- **Always give real paths.** A placeholder `cd path\to\roblox-bridge` was
  literally typed once.
- `winget install cloudflared` does not update an already-open shell's PATH —
  new terminal.
- **Kill the tunnel when idle** — while it's up, the bearer token is an open
  door to the place.

---

## 7. The plugin (`ArenaBridge.lua`, v2.0 as of 2026-10-07)

Install: `python setup.py --install-only` — re-bakes URL+token from
`bridge.token` and installs to `%LOCALAPPDATA%\Roblox\Plugins\ArenaBridge.lua`
(full `python setup.py --tunnel` does the same plus starts server+tunnel).
Keep `roblox-bridge/ArenaBridge.lua` and `nightloop/bridge/ArenaBridge.lua`
**in sync** (they were identical; diff before pushing).

**Token trap (bitten 2026-10-08):** `setup.py` run in a folder that has **no
`bridge.token`** silently generates a *fresh* token, writes it there, and
reinstalls the Studio plugin with the new token baked in. If a different
server is still running with the old token, the plugin then polls with HTTP
401 and nothing works — while `ab.ps1` (which was pointed at the old token
file) looks perfectly fine. Symptoms: Studio console
`[Arena] poll HTTP 401 (xN)`, `/api/health` → `studio_connected: false` with
a stale `last_seen`. Fix: run `python setup.py --install-only` from a folder
whose `bridge.token` matches the **running server's** token, then restart
Studio (the plugin reads its token at startup). Rule: never run `setup.py`
in a fresh copy before copying `bridge.token` into it — and remember a
token change requires a Studio restart to take effect in the plugin.

**v2.0 changes (2026-10-07):**
- **Auto-connect on load.** Local plugins are executed automatically whenever
  the DataModel loads, so the plugin now connects by itself if
  `server.py` is already up — **no toolbar click after a Studio restart**.
  The poll loop tolerates a down bridge (retries), so it latches on the
  moment `server.py` starts. Opt out (persisted):
  `plugin:SetSetting("AutoConnect", false)`; toggle any time with the
  "Arena" toolbar button.
- **`ping` returns `pluginVersion`** — how an agent verifies which build the
  user actually has installed: `.\ab.ps1 ping` → `"pluginVersion": "2.0"`.
- **Known defect fixed:** `survey` read `Lighting.Technology` unguarded,
  which throws `lacking capability RobloxScript` on a plugin thread. Now
  pcall-guarded (assume other properties can too — guard reads in
  Studio-facing code).

Behaviour notes:
- `plugin:GetSetting("ArenaBridgeUrl"/"ArenaBridgeToken")` override the
  baked values if present (persisted per user; all local plugins in the
  Plugins folder **share one settings namespace**, so keep keys namespaced).
- `run_luau` runs via `loadstring` (local plugins get it), falling back to a
  temp `ModuleScript` in ServerStorage — don't re-research that.
- In run mode, see §1: only the server-side instance can poll.
- **Never publish this plugin** — it is a local, machine-private plugin.

---

## 8. First five minutes — checklist for a new agent

1. Read `SESSION.md` → this file → `nightloop/AGENTS.md` (workflow + traps).
2. `curl -s -m 15 "$BRIDGE_URL/api/state?token=$TOK"` → branch per §0.
3. HANDS MODE: run the verification triad with the user (§4), get output.
4. Baseline: compare `jobs/baseline_survey.lua` output against
   `nightloop/src` — drift is the first thing to fix.
5. Every push follows the AGENTS.md workflow: write → `tools/luau-compile
   --binary` → `tools/check_globals.sh` → push → verify with `run_luau`
   returning JSON. Run `bash tools/fix-perms.sh` before the toolchain
   (workspace snapshots drop the exec bits). **Toolchain gotcha (verified
   2026-10-07):** the checked-in `luau-compile`/`luau-analyze` binaries
   predate Luau's bitwise operators — `~`, `|`, `&` fail local compilation
   (they work fine in Studio). Avoid bitwise ops in jobs (table-based XOR
   pattern: `jobs/drift_audit.lua`) or refresh the binaries from a machine
   with internet (GitHub release assets redirect to
   `objects.githubusercontent.com`, which is not on the sandbox egress
   allowlist).
6. **Studio's Luau rounds large multiplies through double — decompose 32-bit
   hashes in jobs.** `(h * 16777619) % 2^32` with `h < 2^32` gives products
   ~3.6e16..7.2e16 (> 2^53): Studio rounds them to the nearest double before
   the mod. Measured 2026-10-07 (`jobs/hash_diagnostic.lua`): results
   off-by-one / off-by-three vs the exact integer values, so a naive FNV-1a in
   a job hashes live files differently than Python on the identical bytes —
   the first drift audit ("22 files drifted") was this, not real drift.
   `jobs/drift_audit.lua` uses the exact, still-fast decomposition
   `16777619 = 2^24 + 2^8 + 147`:
   `h = (h * 147 + h * 256 + (h % 256) * 16777216) % 4294967296` (intermediates
   < 2^41, exact in double). Rule of thumb: in any job, keep multiply products
   < 2^53.
7. After work: update `SESSION.md` state; if the session is done, the user
   rotates the token (`python setup.py --rotate`) and stops the local server
   (or tunnel, if one is ever back).

---

## 9. File map

| path | what |
|---|---|
| `SESSION.md` (root) | live credentials + session state. **Tracked in this repo** (contrary to its own old header) — rotate the token when the session ends. |
| `CONNECT.md` (root) | this file. |
| `roblox-bridge/` | the user's working bridge dir: `server.py`, `setup.py`, `arena_studio.py`, `ArenaBridge.lua` (canonical), `ab.ps1` (HANDS MODE helper), `bridge.token`, `tools/` (luau binaries + `check_globals.sh` + `fix-perms.sh`). |
| `roblox-bridge/tools/.selftest` | — (scratch, deleted after use) |
| `nightloop/bridge/` | export-repo copy of the bridge (`ArenaBridge.lua`, `server.py`, `setup.py`, `arena_studio.py`, `ab.ps1`) — keep in sync with `roblox-bridge/`. |
| `nightloop/src/` | the NightLoop game package (Rojo tree in `default.project.json` → `ServerScriptService.NightLoop` + `StarterPlayerScripts`). |
| `jobs/` | paste-ready Luau jobs for HANDS MODE (`baseline_survey.lua` …). |
| `relay/` | RELAY MODE (§11): `relay_server.py` (the wire), `relay.py` (agent CLI), `relay.ps1` + the page at `/` (user clients), `selftest.py` (fake user's PC). |
| `.relay-state/` | relay runtime: `relay.json` (token/port/url), `events.jsonl`. **Gitignored.** |
| `nightloop/AGENTS.md` | workflow, traps, dead ends, security, entity status. |

One-time HANDS MODE setup the user pastes into their `roblox-bridge` terminal
(the agent should only have to ask for this once per machine):

```powershell
Set-Content -Path ab.ps1 -Encoding ascii -Value @'
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Cmd)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:BRIDGE_URL) { $env:BRIDGE_URL = "http://127.0.0.1:8077" }
if (-not $env:BRIDGE_TOKEN) {
	$tok = Join-Path $here "bridge.token"
	if (Test-Path $tok) { $env:BRIDGE_TOKEN = (Get-Content $tok -Raw).Trim() }
}
python (Join-Path $here "arena_studio.py") @Cmd
exit $LASTEXITCODE
'@
mkdir jobs -ErrorAction SilentlyContinue
```

---

## 10. Sources (verified 2026-10-07)

- Plugin HTTP permissions (local plugins bypass the dialog):
  <https://devforum.roblox.com/t/introducing-plugin-http-permissions/493269>
- Rojo polls `127.0.0.1:<port>` from its plugin (same mechanism):
  <https://devforum.roblox.com/t/getting-data-from-port-for-plugin/649693>
- HttpService localhost limits: 2000/min edit mode, 500/min run mode;
  client instance in run mode cannot do HTTP:
  <https://devforum.roblox.com/t/plugin-localhost-httpservice-limit-affected-by-run-mode/3046079>
- Local plugins auto-run whenever the DataModel loads; shared settings
  namespace for local plugins:
  <https://devforum.roblox.com/t/studio-autorunning-a-plugin-within-the-local-plugins-folder/2008731>
- `plugin:GetSetting` / `SetSetting` persist across Studio close (JSON store):
  <https://developer.roblox.com/en-us/api-reference/function/Plugin/SetSetting>
- `ChangeHistoryService` `TryBeginRecording`/`FinishRecording` is the
  documented plugin undo pattern:
  <https://create.roblox.com/docs/studio/plugins>

---

## 11. RELAY MODE — the sandbox serves the wire (2026-10-08)

**Status: built and proven inside the sandbox; the first live user connection
is the open item.** Files: `relay/` (see `relay/README.md`). Mode of record is
now this, not §4 — HANDS MODE remains the fallback.

### The idea, in one line

Egress out of the sandbox is a fixed allowlist, but **ingress to a sandbox port
is served for us**: any process listening on `0.0.0.0:<port>` gets
`https://<port>-<sandboxId>.e2b.app`, and the *user's* machine can reach it.
So don't send jobs to the user's PC — let the user's PC come and get them.

```
  agent (sandbox)          relay :8787 (sandbox, public URL)        user's PC
  ───────────────          ─────────────────────────────────        ─────────
  relay.py POST /api/jobs ─▶ queue ─┬─ SSE push ─▶ browser page ─┐
                                    └─ polling ──▶ relay.ps1 ─────┴─▶ POST /api/jobs
     ◀────────── POST /api/result ───────────────────────────────────  on 127.0.0.1:8077
```

Two interchangeable clients, both in `relay/`:

1. **The browser page** at `GET /` on the preview URL — zero install. The user
   opens the Arena preview, pastes the bridge token once, and leaves the tab
   open. It holds an SSE stream (live, no polling needed) and does
   `fetch('http://127.0.0.1:8077/…')` directly — legal because `server.py`
   already sends `Access-Control-Allow-Origin: *`, and because loopback is a
   secure context, an HTTPS page may talk to it. Fallback to 2 s polling if the
   proxy kills the stream.
2. **`relay.ps1`** — PowerShell polling client, served at `/relay.ps1`. No
   browser, no CORS, no PNA; the boring reliable one. It reads `bridge.token`
   itself.

### Operating it (agent side)

```bash
# start the wire (token = the user's bridge token, so one secret does both hops)
python3 relay/relay_server.py --port 8787 --token <tok> \
      --url https://8787-<sandboxId>.e2b.app
python3 relay/relay.py state     # attached clients + Studio health + counts
python3 relay/relay.py ping      # round trip through the whole chain
python3 relay/relay.py runfile jobs/baseline_survey.lua --wait 90
python3 relay/relay.py tail      # follow .relay-state/events.jsonl
```

The port is fixed by `--port`, so the preview URL is stable for the life of the
sandbox; if the sandbox restarts, the URL changes and the user must re-point
(their client prints the failure, they don't have to guess).

### Proven, not assumed (2026-10-08, inside this sandbox)

Full chain with **no user**: `server.py` (fake local bridge) + `mock_studio.py`
(fake plugin) + `relay_server.py` + `selftest.py` (fake user's PC).

| check | result |
|---|---|
| `ping` round trip | **251 ms**, `done`, correct place name |
| `runfile jobs/baseline_survey.lua` (7.9 KB, 77 lines) | `done`, byte-exact payload |
| SSE push to a second client | instant, `data: {"type":"job"…}` |
| client killed mid-job | janitor requeued after 20 s → other client completed it |
| fire-and-forget (`wait: 0`) | `accepted` (local bridge took it, no answer awaited) |
| auth gate | 401 without / with wrong token, on every `/api/*` |
| sandbox reaching its own preview URL | **still blocked** (curl exit 35) — only the user's machine needs to reach it |

### Traps

- **Preview gate.** If the preview URL needs `?e2b-traffic-access-token=…`, a
  bare `curl`/`Invoke-WebRequest` from the user's PowerShell gets **403** and
  the browser page is the path that works (Arena's iframe carries the token) —
  or the user copies the value out of the address bar and passes `-TrafficToken`.
  `relay.ps1` detects 403 and says exactly that.
- **Chrome PNA/LNA** can gate public → loopback requests; `server.py` now sends
  `Access-Control-Allow-Private-Network: true` on the preflight (both copies,
  kept in sync). An already-running old server won't have it — restart from an
  updated folder if the page reports a private-network error.
- **The token is one secret for both hops** (relay + `bridge.token`), so a
  mismatch shows up as HTTP 401 *from the local bridge* while the relay itself
  looks healthy. The page and `relay.ps1` both relay that error verbatim.
- `.relay-state/` holds the token (mode 600) and the event log. **Gitignored —
  never commit it.** Check `git status` before any `git add -A`.
- A job is delivered to exactly one client; if that client goes silent for 20 s
  the job is requeued. Clients dedupe by job id.
- PowerShell: `relay.ps1` is ~7 KB — fine as a here-string paste, but prefer
  downloading it from the relay (`/relay.ps1`) or opening it in the browser.
