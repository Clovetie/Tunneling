# relay/ — the sandbox-hosted wire to the user's Studio

**Why.** The bridge (`server.py` + the `ArenaBridge` plugin) lives on the user's
machine. The sandbox cannot reach it: egress is a fixed allowlist, so the
cloudflared tunnel the user runs is invisible from here (CONNECT.md §5). Today's
sandbox *does* serve a public preview URL for any port it listens on, and the
**user's** machine can reach that URL. So the connection is inverted.

```
  agent (this sandbox)              relay (this sandbox, :8787)          user's PC
  ────────────────────              ────────────────────────────         ─────────
  relay.py  POST /api/jobs ──▶ queue ──SSE push──▶ browser page ─┐
                                                          │      ├─▶ POST 127.0.0.1:8077
                                 ◀──POST /api/result──────┴──────┘        (server.py)
                                   relay.ps1 (polling client) ─────┘            │
                                                                          ArenaBridge.lua
                                                                           (Studio plugin)
```

Two interchangeable clients, both shipped here:

| client | where it runs | how | notes |
|---|---|---|---|
| the page at `GET /` | the user's **browser**, on the Arena preview tab | SSE stream down, `fetch` to `127.0.0.1:8077` up | zero install; needs `server.py`'s CORS (`*`, it has it) and the token pasted once |
| `relay.ps1` | the user's **PowerShell** | polls `/api/jobs`, POSTs to the local bridge, POSTs the answer back | no browser quirks; needs the same preview URL to be reachable, plus `-TrafficToken` if the preview is token-gated |

Both deliver the *exact* job envelope the bridge already speaks
(`{type, payload, note, wait}`) and return the local server's answer verbatim,
so a caller sees `result.result.returned` — identical to talking to
`server.py` directly.

## Files

| file | role |
|---|---|
| `relay_server.py` | the relay: job queue, SSE, control-panel page, `/relay.ps1`, `/api/*` |
| `relay.py` | **agent-side CLI** — `state`, `ping`, `run`, `runfile`, `survey`, `job`, `wait`, `tail` |
| `relay.ps1` | user-side PowerShell client (also served at `/relay.ps1`) |
| `selftest.py` | plays the user's machine so the whole chain can be tested with no user |

Runtime state (token, event log) lives in `../.relay-state/` — **gitignored**,
never commit it.

## Run it (agent)

```bash
python3 relay/relay_server.py --port 8787 --token <shared secret> \
    --url https://8787-<sandboxId>.e2b.app          # start the wire

python3 relay/relay.py state                         # who is attached, Studio status
python3 relay/relay.py ping                          # health through the whole chain
python3 relay/relay.py runfile jobs/baseline_survey.lua --wait 90
python3 relay/relay.py run "return game.Name"
python3 relay/relay.py tail                          # follow .relay-state/events.jsonl
```

## Publishing the game code

```bash
python3 relay/relay.py drift            # live place vs repo, per file (read-only)
python3 relay/relay.py push             # generate + run the DRY pass, print the diff
python3 relay/relay.py push --apply     # publish what actually differs
```

`push` regenerates `jobs/push_all.lua` / `push_all_dry.lua` from
`nightloop/src/**` + `nightloop/client/**` (via `jobs/make_push_all.py`, which
verifies its own payload decodes byte-identically before the job exists), runs
one of them through the relay, and prints per-file `unchanged / replaced /
created` with old -> new byte counts, the `require()` result of the key
modules, and the live tuning values from `Config`. Exit code is non-zero if the
job reported anything less than `ok`.

Job files are also served by the relay for browser download —
`https://<preview>/jobs/` lists them, `https://<preview>/jobs/push_all.lua`
fetches one — which sidesteps the private GitHub repo and the gated preview
(browser downloads carry the token; `curl.exe` does not).

The preview URL is `https://<port>-<E2B_SANDBOX_ID>.e2b.app`; the port is fixed
by `--port`, so the URL is stable for the life of the sandbox.

## Prove it without the user

```bash
python3 roblox-bridge/server.py --port 8077 --token dev &                 # fake local bridge
BRIDGE_URL=http://127.0.0.1:8077 BRIDGE_TOKEN=dev \
  python3 roblox-bridge/mock_studio.py &                                  # fake plugin
python3 relay/relay_server.py --port 8787 --token dev &
python3 relay/selftest.py --relay http://127.0.0.1:8787 --token dev \
    --bridge http://127.0.0.1:8077 --bridge-token dev &                   # fake user's PC
python3 relay/relay.py ping
```

Measured 2026-10-08 with exactly this rig: `ping` round trip **251 ms**,
`survey` and a 7.9 KB `runfile` job (baseline_survey) both `done`, SSE push
instant, and a job whose client died mid-flight was requeued by the janitor
after 20 s and completed by the surviving client.

## Endpoints (all token-gated except `/`, `/relay.ps1`, `/healthz`)

| endpoint | who | what |
|---|---|---|
| `GET /` | user | the browser client / control panel |
| `GET /events?token=&client=` | browser | SSE: pending jobs on connect, then live pushes |
| `GET /api/jobs?token=&client=&kind=` | `relay.ps1` | take a batch (adds `body`: ready-to-POST local request) |
| `POST /api/jobs?token=` | agent | enqueue `{type,payload,note,wait}`; long-polls for the answer when `wait>0` |
| `POST /api/result?token=` | client | `{id, response, error, ms}` |
| `POST /api/report?token=` | client | local-bridge health, for `relay.py state` |
| `GET /api/state?token=` | agent | clients, counts, last report, recent jobs |
| `GET /api/result/<id>?token=` | agent | one job record |

Delivery is once per client; a job whose client goes silent for 20 s is
requeued, so a dropped tab or terminal never strands work. Job `status` is
`queued` → `delivered` → `done` / `error` / `accepted` (`accepted` = a
`wait: 0` fire-and-forget job the local bridge took but never answered).

## Security

The relay URL is public-ish and the token is the only gate — it is the same
secret as `bridge.token`, so anyone holding it could drive the user's Studio.
It lives in `.relay-state/relay.json` (mode 600, gitignored), never in a
tracked file. Stop the relay (or the user's client) when the session is idle.
