# v5 Ops Platform — tunnels, multi-agent coordination, telemetry, scoped auth

_Applies to bridge `6.1.0`._

Everything in this document ships in `roblox-bridge/` and is **stdlib-only**
(no pip installs). It layers on top of the v4 MCP + plugin bridge; nothing in the
old workflow breaks.

```
┌─────────────┐   /api/poll   ┌──────────────────────────────┐   /mcp  ┌────────────┐
│ Roblox      │◀─────────────▶│  server.py v5                │◀───────▶│ AI agents  │
│ Studio      │   /api/result │  · job queue (dedupe/retry)  │  HTTP   │ Claude,    │
│ (plugin)    │               │  · scoped tokens + limits    │         │ Gemini,    │
└─────────────┘               │  · agent bus (tasks, mail)   │         │ Codex…     │
                              │  · metrics + audit chain     │         └────────────┘
                              │  · tunnel supervisor         │
                              └───────────┬──────────────────┘
                                          │ cloudflared / ngrok
                                          ▼
                              https://<random>.trycloudflare.com
                              https://<random>.trycloudflare.com/mcp
```

---

## 1. Supervised tunnel

`setup.py --tunnel` now hands the tunnel to a supervisor instead of a one-shot
child process.

```bash
python setup.py --tunnel                      # auto-detect cloudflared or ngrok (QUIC -> http2 fallback)
python setup.py --tunnel --tunnel-protocol http2   # force HTTP/2 behind UDP-blocking firewalls
python setup.py --tunnel --tunnel-flavor ngrok
python setup.py --tunnel --tunnel-token "$CF_TOKEN" --public-url https://mcp.example.com
BRIDGE_PUBLIC_URL=https://mcp.example.com python setup.py   # you run the tunnel
```

```bash
python server.py --tunnel --tunnel-flavor cloudflared --tunnel-protocol auto --port 8077
```

What the supervisor does:

| Behaviour | Detail |
|---|---|
| Auto-detect | `cloudflared`, then `ngrok` on `PATH` (install hints surface in the UI/API if absent) |
| URL discovery | Scrapes `trycloudflare.com` / `ngrok` URLs from child output; remembers the last one |
| Auto-restart | Exponential backoff 1 → 2 → 4 … 30 s when the child exits |
| Health probes | Every 20 s calls `<publicUrl>/api/health`; 2 failures → `degraded`, 4 → restart |
| Reconnect accounting | `restarts`, `urlChanges`, `onlineSeconds`, `lastError`, `logTail` in `/api/tunnel` |
| LAN sharing | `lanUrls` for phones/tablets on the same Wi-Fi (no tunnel needed) |
| Named tunnel | `--tunnel-token` runs `cloudflared tunnel run --token …` for a stable hostname |
| Kill switch | Ops tab buttons or `POST /api/tunnel {"action":"stop"}` |

**Safety guard:** the bridge refuses to start a tunnel when no token is
configured (state `blocked`, `lastError` explains how to create one), so an
accidental `server.py --tunnel` can never publish an unauthenticated control
surface.

State machine: `stopped → starting → online → degraded → reconnecting → …`.
`unavailable` means no binary was found; `error` means the process failed to launch.
A user-managed tunnel (`BRIDGE_PUBLIC_URL`) is reported as `online` and never
supervised.

### 1.1 Named tunnel with a stable hostname

A quick tunnel prints a new `https://<random>.trycloudflare.com` every restart, so
MCP clients have to be reconfigured. A **named tunnel** gives one permanent
hostname. Cloudflare owns the ingress config; the bridge only runs the connector.

1. **Create the tunnel** (once, in the Cloudflare dashboard):
   Zero Trust → Networks → Tunnels → *Create a tunnel* → Cloudflared → name it
   (e.g. `arena-studio`).
2. **Add a public hostname** in that tunnel:
   subdomain `mcp`, domain `example.com`, service `HTTP` → `127.0.0.1:8077`.
   That mapping is the routing; no CLI flag changes it.
3. **Copy the connector token**: `cloudflared tunnel token arena-studio`, or the
   install command shown by the dashboard. The token is a long base64 value — not a
   URL, not the tunnel UUID.
4. **Run the bridge with the token:**
   ```bash
   python setup.py --tunnel --tunnel-token "$CF_TOKEN" --tunnel-name mcp.example.com
   # or
   python server.py --tunnel --tunnel-token "$CF_TOKEN" --tunnel-name mcp.example.com
   ```
   `--tunnel-name` is **display metadata only** (`/api/tunnel` → `name`); it never
   reaches the cloudflared command line.
5. **Verify:**
   ```bash
   curl -s localhost:8077/api/tunnel | python3 -m json.tool | grep -E 'state|publicUrl|namedTokenValid|name'
   # state=online, publicUrl=https://mcp.example.com, namedTokenValid=true
   python3 jobs/live_acceptance.py --url https://mcp.example.com --token "$BRIDGE_TOKEN"
   ```

The composed command is exactly
`cloudflared tunnel --no-autoupdate run --token <TOKEN>` — never `--url` (that
flag is for quick tunnels only), and the token is validated *before* the process
spawns.

| Symptom | Cause | Fix |
|---|---|---|
| `state=error`, `Invalid Cloudflare tunnel token: …whitespace / URL / too short` | pasted a URL, a UUID, or a quoted value | re-copy the token from the tunnel's *Install connector* panel, no quotes |
| `401` on `/mcp` through the hostname | the bridge has a token but the client sends none | put `Authorization: Bearer <token>` in the MCP client config (Ops tab → *Copy MCP config (tunnel)*) |
| A `trycloudflare.com` URL still appears | `--tunnel-token` was not passed / env `TUNNEL_TOKEN` empty | check `/api/tunnel` → `named`, `namedTokenValid` |
| `state=unavailable` | `cloudflared` is not on `PATH` | install it, or use `BRIDGE_PUBLIC_URL` for a tunnel you run yourself |

---

## 2. Agent Bus — many agents, one Studio

The bus is how two workers (say Claude, and Gemini 4 Argon through an operator
wrapper) share one place without overwriting each other's work. It stores only
bridge coordination state, never Studio data.

A plain model has no bus identity: it cannot register, claim, or complete. When
the partner is a text model, the operator registers it, claims on its behalf,
pastes the prompt, applies the returned patch, and completes the task with the
artifacts. The prompt-ready briefs live in `handoff/Gemini-4-Argon-Tasks.pdf`
and `handoff/GEMINI-4-ARGON.md`.

```bash
python arena_studio.py bus register --name "Gemini 4 Argon" --role partner
python arena_studio.py bus agents
python arena_studio.py bus post --agent agent-1234 --subject "handoff" --body "take the UI pass" --to "Claude"
python arena_studio.py bus inbox --agent agent-1234
python arena_studio.py bus task-create --title "Polish shop UI" --priority high --tags ui --agent agent-1234
python arena_studio.py bus task-claim --task task-abcd --agent agent-1234
python arena_studio.py bus task-complete --task task-abcd --agent agent-1234 --result "shipped"
python arena_studio.py bus export --out HANDOFF.md
```

Via MCP (works from any client):

```jsonc
{"name":"agent_bus","arguments":{"action":"register","name":"Gemini 4 Argon","role":"partner"}}
{"name":"agent_bus","arguments":{"action":"task_claim","taskId":"task-abcd","agentId":"agent-1234"}}
{"name":"agent_bus","arguments":{"action":"post","agentId":"agent-1234","subject":"blocked",
                                 "body":"Need the Lighting preset approved","to":"Claude","priority":"high"}}
```

### Stale agents and incremental briefs

- `agent_bus {action:'sweep', offlineSeconds:900}` (CLI: `bus sweep --offline-seconds 900`)
  releases every `in_progress` task whose owner has been silent for the threshold,
  posts one `alert` message, and increments `agentsReaped` / `tasksReaped` in
  `stats()`. It never touches tasks held by an agent that is still heart-beating,
  and a second sweep in a row is a no-op.
- `agent_bus {action:'export', since:<cursor>, summaryOnly:true}` renders an
  incremental brief: messages after the cursor only, open tasks always, and one
  line per task with no detail/result/artifacts. Use `stats().cursor` as the
  cursor you hand out.

### Task semantics

| Concept | Rule |
|---|---|
| Statuses | `todo → in_progress → blocked → done / cancelled` |
| Leases | A claim holds a lease (default 900 s). It auto-expires, returning the task to `todo` and incrementing `leaseExpiries` — a crashed agent cannot deadlock the board. |
| Dependencies | `dependsOn` tasks must be `done` before a claim is accepted (`blockedBy` is reported) |
| Double claim | Rejected while another live lease exists — safe for racing agents |
| Priorities | `low / normal / high / critical` |
| Artifacts | `task_complete` accepts file paths/URLs for the next agent |
| Handoff brief | `action:"export"` renders markdown: roster, board (incl. results), recent messages, stats |

Messages support `note / question / answer / handoff / alert / result`, direct or
broadcast, with a cursor (`since`) and read receipts (`mark_read`).

Seed the partner work order with:

```bash
python roblox-bridge/tools/seed_agent_bus.py            # online
python roblox-bridge/tools/seed_agent_bus.py --offline  # writes state dir directly
```

---

## 2.1 Several Studios at once (the fleet)

The bridge has always tracked multiple DataModel peers; v5.3 turns that into a
managed fleet you can address deliberately. Roblox's built-in Studio MCP server
ships the same idea as `list_roblox_studios` + `set_active_studio`; the bridge
mirrors it with one difference that matters for a queue: **a pin is a
preference, not a hard gate** — if the pinned Studio closes, untargeted jobs fall
back to automatic `edit`-first routing instead of stalling.

```bash
curl -s $BRIDGE/api/studios -H "X-Arena-Token: $BRIDGE_TOKEN"            # the fleet
curl -s -X POST $BRIDGE/api/studios -H "X-Arena-Token: $BRIDGE_TOKEN" \
     -d '{"action":"set_active","peerId":"edit-1234"}'                    # pin
curl -s -X POST $BRIDGE/api/studios -H "X-Arena-Token: $BRIDGE_TOKEN" \
     -d '{"action":"label","peerId":"edit-1234","label":"Lobby (main)"}'  # human name
curl -s -X POST $BRIDGE/api/studios -H "X-Arena-Token: $BRIDGE_TOKEN" \
     -d '{"action":"broadcast","type":"ping","timeout":12}'                # fan-out
```

Via MCP:

```jsonc
{"name":"studio_fleet","arguments":{"action":"list"}}
{"name":"studio_fleet","arguments":{"action":"set_active","peerId":"edit-1234"}}
{"name":"studio_fleet","arguments":{"action":"clear_active"}}
{"name":"studio_fleet","arguments":{"action":"broadcast","broadcastTool":"survey_place"}}
```

| Field (from `list`) | Meaning |
|---|---|
| `peerId` / `studioId` | Stable handle for this Studio window — pass it as `peerId` on **any** tool |
| `dm` | `edit`, `server` or `client` DataModel |
| `place`, `placeId`, `gameId` | Which experience is open |
| `online`, `ageSeconds` | Last poll age; `online` means polled within 5 s |
| `pinned`, `defaultTarget` | Which Studio the pin selects, and which one an untargeted job actually reaches |
| `queuedJobs` | Jobs currently addressed to this peer |
| `label` | Human name (empty clears it) |

**Routing rules**

1. Explicit `peerId` on the job/tool → that Studio only.
2. `place_id` → the Studio with that place open.
3. Pinned Studio online → untargeted jobs go there (`routing: "pinned"`).
4. Otherwise `auto`: `edit` DataModels first, then any live peer.

Pins and labels persist in `bridge_state/fleet.json`, so a restart does not
forget which window you were working in. The file is written atomically
(`fleet.json.tmp` → `os.replace`), and a file that cannot be parsed is *not*
silently replaced with defaults: it is moved aside as
`fleet.json.corrupt-<unix ts>`, a warning is emitted (`fleet.load_failed`), and
`GET /api/fleet` reports it as `fleetLoadWarning` so a reset pin is visible
instead of looking like a choice nobody made.

**Broadcast** (`studio_fleet {action:'broadcast'}`) fans one job out to every
online Studio and returns per-peer `{ok, status, durationMs, result, error}`. It
is deliberately restricted to read-only kinds — `ping`, `studio_state`,
`survey`, `inspect`, `search_tree`, `get_properties`, `compare_instances`,
`read_script`, `read_scripts`, `get_console` — so a fan-out can never edit every
place at once. Anything else is a `400 ValueError`, not a silent write.

---

## 3. Scoped tokens & rate limits

`bridge.token` remains the admin owner credential. Mint additive tokens:

```bash
python arena_studio.py tokens create --label "ci" --role write --ttl 86400 --rate 5
python arena_studio.py tokens list
python arena_studio.py tokens revoke --id tok-xxxxxxxx
```

| Role | Can do |
|---|---|
| `read` | state, peers, survey, reads, metrics, audit, bus reads. Mutating MCP tools return `insufficient_scope`. |
| `write` | everything above + jobs, script writes, snapshots, bus writes |
| `admin` | + token minting/revocation, tunnel start/stop, queue clear |

* Tokens are stored **hashed**; only the first 8 chars are kept for display, and
  the full token is shown exactly once.
* Optional expiry (`ttlSeconds`).
* Per-token token-bucket rate limit (`rateLimitPerSecond`, `burst`); over-limit
  requests get `429` with `Retry-After`.
* **Request limits.** Bodies are capped by `BRIDGE_MAX_BODY_BYTES` /
  `--max-body-bytes`: over the cap answers `413`, and a `Transfer-Encoding:
  chunked` body answers `411` (the bridge reads `Content-Length`, so a chunked
  body used to arrive as `{}` — a `400` about a missing field on some routes and
  a *silent no-op* on others). `Expect: 100-continue` is honoured, but only after
  the declared size is checked, so an oversized body is refused before it is
  uploaded. Concurrency is capped too (default 64 in flight,
  `BRIDGE_MAX_CONCURRENT_REQUESTS` or `create_http_server(max_concurrent_requests=…)`);
  beyond it a new connection gets `503` + `Retry-After: 1` immediately, counted in
  `bridge_http_rejected_total` with one `http.saturated` warning per second.
* Every rejection is counted in `bridge_auth_rejections_total{code,path}`.
* Token state lives in `bridge_state/tokens.json` (mode 600, gitignored).

```bash
curl -H "Authorization: Bearer arb_…" http://127.0.0.1:8077/api/state
curl -X POST -H "Authorization: Bearer $ADMIN" -H 'Content-Type: application/json' \
     -d '{"action":"create","label":"viewer","role":"read","ttlSeconds":3600}' \
     http://127.0.0.1:8077/api/tokens
```

---

## 4. Resilient job queue

| Feature | How to use |
|---|---|
| Idempotency | `POST /api/jobs {"idempotencyKey":"build-ui-42"}` or the `Idempotency-Key` header. Repeat submissions return the original job; `deduplicated` counts the hits. |
| Priority | `"priority": 0..9` (0 = highest). Selection is priority then FIFO, so a critical probe jumps ahead of a bulk build. |
| Retries | `"maxRetries": 1..5`. Transient failures (peer disappeared, timeout, connection reset, explicit `"retryable": true` in the result) requeue the job instead of failing it. |
| Timeouts | `"timeout": 60` seconds. The reaper fails stuck jobs and emits `job.timeout` into the audit chain. |
| Orphan recovery | A job claimed by a peer that stopped polling for >20 s is requeued automatically (`bridge_jobs_reaped_total`, `job.requeued_orphan` event). |
| Crash-loop cap | Orphan recovery is bounded: after `max(2, maxRetries + 2)` *claims* the job fails with `peer_crash_loop after N attempts…` instead of being requeued forever, its waiter is woken with that error, and the outcome is counted (`bridge_jobs_crash_loop_total`), emitted (`job.crash_loop`, level `error`) and audited (actor `reaper`). `reap_jobs()` reports `{recovered, timedOut, crashLoops}`. |
| Cancellation | `POST /api/jobs/<id>/cancel`, queue-wide `POST /api/queue/clear` (admin). |
| Maintenance pause | `POST /api/queue/pause {"reason":"asset import"}` (admin) stops *handing* jobs to Studio while new work keeps queueing; peers stay registered (`/api/poll` returns `{"job":null,"paused":true}` every 8 s). `POST /api/queue/resume` releases the held work and wakes waiting peers. MCP: `bridge_ops {"action":"queue_pause"\|"queue_resume"}`; dashboard: **Pause queue** button in the Ops Console; CLI `python arena_studio.py queue pause --reason "asset import"` / `queue resume` / `queue status`. Nothing is cancelled by a pause. |
| Dead-peer handoff | `POST /api/bus {"action":"sweep"}` releases tasks leased by agents silent for `offlineSeconds` (default 900) so a live peer can claim them immediately; `POST /api/ops {"action":"sweep"}` and `bridge_ops {"action":"sweep"}` do the same. |

---

## 5. Observability

**Metrics** — `GET /api/metrics` (JSON) or `?format=prometheus`:

```
bridge_jobs_enqueued_total{type="write_script"}
bridge_jobs_total{type="ping",dm="edit",outcome="ok"}
bridge_job_duration_ms{...} p50/p90/p95/p99, count, sum
bridge_job_retries_total, bridge_job_dedupe_total, bridge_jobs_reaped_total
bridge_jobs_crash_loop_total, bridge_http_rejected_total
bridge_queue_depth, bridge_peers_online, bridge_agents_online, bridge_tasks_open
bridge_auth_rejections_total{code,path}, bridge_token_requests_total
bridge_tunnel_online, bridge_queue_paused, bridge_sweeps_total
bridge_agents_reaped_total, bridge_tasks_released_total, bridge_uptime_seconds
```

```bash
python arena_studio.py metrics
python arena_studio.py metrics --prometheus | curl --data-binary @- http://pushgateway/api/v1/metrics/job/bridge
```

**Live events** — `GET /api/events` is a Server-Sent Events stream (sequence ids,
`since`/`kinds` filters, keepalives). `?once=1` returns a JSON snapshot for CLIs.

**Audit log** — append-only JSONL with a SHA-256 hash chain. Every mutating job,
token action, tunnel action, queue clear and auth rejection is recorded.

```bash
python arena_studio.py audit --limit 20
python arena_studio.py audit --verify
```

`verify` recomputes the whole chain and reports the first broken entry
(`prev-hash mismatch` = insertion/removal/reorder, `content hash mismatch` = edit).
Appends take an OS file lock and adopt the on-disk head, so two processes cannot
silently fork the chain.

**Ops report** — `GET /api/ops` bundles health, tunnel, bus, metrics, open tasks,
recent jobs and audit head, plus four v5.2 blocks:

* `queue` — `{paused, reason, pausedAt, depth}`: the maintenance switch and how
  much work it is holding.
* `sweeper` — `{intervalSeconds, offlineSeconds, lastSweep, sweeps, tasksReaped, agentsReaped}`:
  the automatic stale-agent sweep, which runs on the reaper thread every
  `--sweep-interval` seconds (default 300, `BRIDGE_SWEEP_INTERVAL`, `0` disables)
  and releases tasks from agents silent longer than `--sweep-offline`
  (default 900, `BRIDGE_SWEEP_OFFLINE`).
* `jobLatency` — per-tool `{count, p50, p95, max, mean}` wall time over the last
  300 completed jobs, computed in-process (no Prometheus needed) and shown as a
  one-line summary under the dashboard metric box.
* `fleet` — the same document as `GET /api/studios` (per-Studio `online`,
  `pinned`, `defaultTarget`, `queuedJobs`, labels).
* `mcp` — `{protocolVersions, modernProtocol, tasks}`: which protocol eras the
  bridge speaks and how many task-augmented calls are tracked.
* `recentJobs` / `openTasks` — unchanged, newest first.

**Dashboard.** The Ops Console got a v5.3 visual pass: a sticky **studio strip**
under the header with one chip per connected Studio (online dot, place, DataModel
badge, pin button, broadcast ping), animated tab and pane transitions, glass
panels with hover lift, count-up stat numbers, and a `prefers-reduced-motion`
opt-out that disables every animation. No external assets, fonts or CDNs — the
page is one self-contained HTML document served by the bridge itself.

The dashboard's **tab 7 — Ops Console & Agent Bus** renders it live, and adds
three operator controls: **Pause queue / Resume queue**, **Sweep** (release dead
peers' tasks) and the per-task **Claim / Done** buttons that post as
`web-operator` (or whatever `arena_agent_id` the browser has stored).

---

## 6. REST reference (v5 additions)

| Method & path | Role | Purpose |
|---|---|---|
| `GET /api/ops` | read | consolidated ops report |
| `GET /api/metrics[?format=prometheus]` | read | metrics |
| `GET /api/events[?once=1&since=&limit=&kinds=]` | read | SSE stream / snapshot |
| `GET /api/audit[?limit=&verify=1]` | read | audit tail / chain verification |
| `GET /api/bus?action=…` | read | bus reads (stats, agents, inbox, tasks, feed) |
| `POST /api/bus` | write | register, post, task_* mutations, export |
| `GET /api/tunnel` | read | tunnel status |
| `POST /api/tunnel` | admin | `start` / `stop` / `restart` |
| `GET /api/tokens` | admin | list tokens (never returns hashes) |
| `POST /api/tokens` | admin | `create` / `revoke` / `roles` |
| `POST /api/ops` | admin | `reap`, `queue_clear`, `queue_pause`, `queue_resume`, `sweep` |
| `POST /api/queue/pause` | admin | hold jobs for maintenance (`reason` recorded in audit) |
| `POST /api/queue/resume` | admin | release held jobs and wake waiting peers |
| `GET /api/studios` | read | fleet: peers, labels, pin, routing, queued counts |
| `POST /api/studios` | write/admin | `set_active`, `clear_active`, `label`, `broadcast` (broadcast needs `write`) |
| `POST /api/jobs` | write | + `idempotencyKey`, `priority`, `maxRetries`, `timeout` |

`GET /api/health` stays public (no place data, no secrets) so tunnels and uptime
monitors can probe it.

---

## 7. MCP surface changes

_Historical: the counts below are what each v5.x release changed, in the words of that
release. Today's surface is generated rather than hand-counted — `docs/FACTS.md`,
rewritten by `python3 -B tools/emit_facts.py` and verified by `--check`._

* **71 tools** (was 68 at v5.0): `agent_bus`, `bridge_ops` and `studio_fleet`.
* **7 prompts** (was 4): `multi_agent_handoff`, `bus_standup` and `task_triage`
  render live bus state (roster, task board, cursor) and fall back to a one-line
  "live bus state is unavailable" note when the bridge is offline.
* **11 resources** (was 5 at v4, 8 at v5.0): `studio://state`, `peers`,
  `console`, `selection`, `survey`, `bus`, `ops`, `audit`, plus v5.2's
  `studio://jobs` (queue depth, pause state, sweeper counters, recent jobs and
  per-tool p50/p95), `studio://studios` (the fleet) and `studio://handoff` (the
  bus export as markdown — `text/markdown`, paste-ready brief for the next agent
  or human).
* **Dual-era protocol.** The server speaks the modern **`2026-07-28`** revision
  and every legacy revision back to `2024-11-05`:
  * `server/discover` returns `supportedVersions`, `capabilities`, `serverInfo`,
    `instructions`, `ttlMs` and `cacheScope` — no handshake required.
  * A modern client can send any request first and put its version in the HTTP
    `MCP-Protocol-Version` header or in per-request
    `_meta["io.modelcontextprotocol/protocolVersion"]`; an unknown version gets
    `-32022` with the supported list, and `Mcp-Method` / `Mcp-Name` headers that
    disagree with the JSON-RPC body get `-32020`.
  * Modern responses carry `resultType: "complete"`; list results carry
    `ttlMs: 60000` and `cacheScope: "private"`. Legacy clients see the old shape,
    unchanged.
* **Task-augmented tool calls.** Any tool can run as a task:
  ```jsonc
  {"method":"tools/call","params":{"name":"survey_place","arguments":{},"task":{"ttl":600000}}}
  // → {"resultType":"task","taskId":"task-…","status":"working","pollIntervalMs":1000,"ttlMs":600000}
  {"method":"tasks/get","params":{"taskId":"task-…"}}      // working → completed / failed
  {"method":"tasks/result","params":{"taskId":"task-…","timeoutMs":120000}}
  {"method":"tasks/list"}  {"method":"tasks/cancel","params":{"taskId":"task-…"}}
  ```
  `tasks/result` blocks until the task is terminal (or the timeout) and tags the
  payload with `_meta["io.modelcontextprotocol/related-task"]`. Tools advertise
  `execution.taskSupport: "optional"`, and `capabilities.tasks` declares
  `list`, `cancel` and `requests.tools.call`. Cancelling marks the task
  `cancelled`; the in-flight Studio job still finishes and its result is dropped.
* **Fleet tool**: `studio_fleet` (`list`, `set_active`, `clear_active`, `label`,
  `broadcast`) plus the `studio://studios` resource.
* Read-scoped tokens see mutating tools as blocked, both in `tools/call` and in
  the returned `isError` payload (`reason: insufficient_scope`).

```jsonc
{"name":"bridge_ops","arguments":{"action":"status"}}
{"name":"bridge_ops","arguments":{"action":"metrics","format":"prometheus"}}
{"name":"bridge_ops","arguments":{"action":"audit_verify"}}
{"name":"bridge_ops","arguments":{"action":"tunnel_restart"}}
{"name":"bridge_ops","arguments":{"action":"queue_pause","reason":"asset import"}}
{"name":"bridge_ops","arguments":{"action":"queue_resume"}}
{"name":"bridge_ops","arguments":{"action":"sweep"}}
```

`bridge_ops` actions: `status`, `metrics`, `audit`, `audit_verify`, `events`,
`tunnel`, `tunnel_start`, `tunnel_stop`, `tunnel_restart`, `queue_pause`
(`reason`), `queue_resume`, `queue_clear`, `sweep`, `reap`.

---

## 8. Filing & state

All runtime state is gitignored under `roblox-bridge/bridge_state/`:

```
bridge_state/
├── audit.jsonl     # hash-chained audit (append-only)
├── tokens.json     # hashed scoped tokens (0600)
├── bus.json        # agents, messages, tasks
├── fleet.json      # Studio pins & labels (atomic write; a bad file is kept as
│                   #   fleet.json.corrupt-<ts> and reported as fleetLoadWarning)
└── tunnel.json     # last known public URL
```

Override with `--state-dir` or `BRIDGE_STATE_DIR`.

---

## 9. Tests

```bash
python3 -B roblox-bridge/test_bridge_mcp.py          # 170 tests
bash roblox-bridge/tools/check_globals.sh roblox-bridge   # undefined Luau globals
python3 -B tools/verify_licences.py                  # kit asset licence manifest
python3 roblox-bridge/image_pipeline.py --check      # committed asset manifest
                                                     #   matches the tree (exit 1
                                                     #   on drift, writes nothing)
node roblox-bridge/tools/dashboard_harness.js        # dashboard XSS contract
bash tools/check_lint.sh                             # ruff gate + blind-except
                                                     #   ratchet (exit 2 = ruff not
                                                     #   installed; see pyproject.toml)
python3 -B tools/emit_facts.py --check               # docs/FACTS.md and every doc
                                                     #   claim agree with the tree
bash tools/check_secrets.sh                          # no credential, state file or
                                                     #   private key is tracked
```

`make ci` runs all of the above plus the Luau scan over `roblox-kit/{modules,systems}`,
in the same order CI does. For the inner loop, `test_bridge_mcp.py` takes `--fast`
(every test, minus the two nested self-test suite runs: ~71 s instead of ~215 s) and
`--unit` (the offline classes only, no port bound: 63 tests in ~2 s), and accepts test
names — `python3 -B test_bridge_mcp.py TestV61SnapshotRestore` runs just that class.

`.github/workflows/ci.yml` is **active** and runs exactly those, as five job definitions
(eight jobs once the matrix expands): `bridge-suite` (`make test`, on Python 3.10, 3.11,
3.12 and 3.13, with a stdlib-only import audit and the log/upload of failures),
`lint` (`make lint`: ruff plus the blind-except ratchet), `studio-plugin` (`make luau` and
the two plugin pre-flight tests), `kit-and-docs` (`make verify kit docs-check secrets`) and
`dashboard` (`make harness` on node 22). Nothing in it is a command CI invented: each job
runs the same `make` target a contributor runs locally, so "green in CI" and "green on my
machine" cannot drift apart.

v6.1 coverage added on top: the P0 security sweeps (gate parity on both
transports, the credential sweep over every route, path/URL confinement over every
file-shaped tool argument, CSP + static-asset policy, the declared route policy)
and the P1 reliability items — a capped peer crash-loop, fleet state surviving a
corrupt file and a crash mid-write, chunked/`Expect: 100-continue`/oversized
bodies and the bounded worker pool, manifest regeneration that is byte-identical
and refuses to write under `--check`, and snapshot restore keeping dotted
instance names while reporting a partial failure. The P3 additions are the three offline
classes: `TestV61RendererGoldens` (five hashed scenes, so a silent change to the software
rasteriser is a diff and not a mystery), `TestV61KitIntegrity` (the kit's four descriptions
of itself — `registry.json`, the files on disk, `asset-lock.json` and `modules/Assets.lua` —
must agree) and `TestV61OperatorScriptSmoke` (`jobs/*.py` and `tools/*.py`: `--help` works
and writes nothing, no network at import time, a missing optional dependency says so).

v5 / v5.1 / v5.2 / v5.3 coverage: scoped roles over HTTP and MCP, rate-limit 429s, bus presence/
mailbox/lease expiry/handoff export, stale-agent sweeping, incremental
(`since` / `summaryOnly`) handoff briefs, MCP prompt rendering with live bus state
and the offline fallback, named-token validation, idempotency, priority ordering,
retry-then-fail, orphan requeue, timeout reaping, Prometheus exposition, SSE
snapshot, audit tamper detection, responsive Ops tab + `node --check` of the
dashboard script, the latency script (percentiles, 401 path), the two operator
scripts' failure modes, tunnel URL scraping and the missing-binary path, and
v5.2's maintenance pause (jobs held, admin-only, resume reclaims the same job),
the automatic sweeper (release + metric + audit entry, silent no-op, idempotent),
per-tool latency percentiles, the `studio://jobs` / `studio://handoff` resources,
the `arena_studio.py queue` CLI round trip, and v5.3's fleet (list, pin, routing
fallback when the pin goes offline, persistence across a restart, labels,
broadcast fan-out with per-peer results, refusal of write kinds), the modern MCP
era (`server/discover`, `-32022`, `-32020` header mismatch, `resultType`), and the
task lifecycle (`tools/call` + `task`, `tasks/get`, `tasks/result`, `tasks/list`,
`tasks/cancel`); v5.4 adds 19 tests for the confirmation gate and subscriptions
(mode scoping, signed state, digest and single-use enforcement, principal binding,
deny/expiry accounting, the task flip to `input_required`, `subscriptions/listen`
on both transports, the CLI/REST approval loop, `X-Arena-Task` and the health /
ops / metrics surface), the four playtest tools end to end against the mock
(including "no playtest running" refusals and device-profile application), the
`--read-only` guard over the new job types, and a coverage contract that fails the
suite if any registered tool stops being exercised.

## 10. Live acceptance and drills

Both checks below need a real Studio peer; the sandbox that builds this repo cannot
provide one, so the runs are recorded here by the operator, never simulated.

### 10.1 Acceptance run (G7)

```bash
python3 jobs/live_acceptance.py --url http://127.0.0.1:8077 --token "$BRIDGE_TOKEN" \
    --self-test --json-out handoff/live-acceptance.json
# through the tunnel instead of loopback:
python3 jobs/live_acceptance.py --url https://mcp.example.com --token "$BRIDGE_TOKEN"
```

The script checks, in order: bridge reachable + version, a Studio peer is attached,
`/api/tunnel` is `online` with a URL, the public URL answers `/api/health` with the
same version, `bridge_ops.status` through `/mcp` reports `studio_connected=true`,
`get_studio_state` works through the tunnel, the optional full `self_test`, and the
audit chain still verifies. It prints PASS/FAIL per step and exits non-zero on any
failure, so it can gate a release.

Paste the emitted JSON under **Live acceptance**:

```jsonc
// handoff/live-acceptance.json (produced by the command above)
{
  "kind": "live-acceptance",
  "verdict": "pass",            // <- what the operator observed
  "steps": [ /* per-step PASS/FAIL with the measured values */ ]
}
```

### 10.2 Kill-switch drill (G8)

```bash
python3 jobs/drill_studio_kill.py --token "$BRIDGE_TOKEN" \
    --json-out handoff/kill-switch-drill.json
```

The drill enqueues a 25-second `execute_luau` job, waits for Studio to claim it,
then asks you to **stop Studio** (close the place, disable the plugin, or drop the
network). It watches `/api/jobs/<id>` and `/api/metrics` and passes when the job is
requeued (`status` back to `queued`, `attempts` incremented) or
`bridge_jobs_reaped_total` moves. The reaper only considers a peer gone after the
peer stops heart-beating (20 s grace), so expect a ~30 s window.

| Result | Meaning |
|---|---|
| `verdict: pass`, job `queued` then `done` | reaper requeued the orphan and Studio picked it up on reconnect |
| `verdict: pass`, `reaped delta > 0` | job was reaped by the timeout/requeue path |
| `verdict: fail` | the job stayed `running`; check the bridge log for `job.requeued_orphan` events |

## 11. Latency budget (G2)

Tunnel overhead is a product decision, not a curiosity: it decides whether a team
runs a named tunnel, a local agent, or LAN URLs. Measure, never guess.

```bash
# both targets in one run; writes raw samples for reproducibility
python3 jobs/measure_tunnel_latency.py \
    --url local=http://127.0.0.1:8077 \
    --url tunnel=https://mcp.example.com \
    --token "$BRIDGE_TOKEN" --runs 20 --json-out jobs/tunnel_latency.json
```

The script POSTs `/api/jobs` (`dm='edit'`, `wait=60`) and times the full round trip
for `ping_studio`, `get_studio_state` and `script_read`, discarding one warm-up
call, then prints per-target `n / p50 / p95 / max` plus a markdown table with the
delta. It exits `2` on a rejected token, `3` when no Studio peer is connected and
`4` when every request failed, so a broken setup cannot produce a plausible-looking
table.

Two rules make that exit-code claim true rather than decorative:

- **Each target's peer list is read first** (`GET /api/peers`). A target with no peer
  cannot be measured at all — every job simply waits out `--timeout` — so the script
  says so in a second instead of after `runs × tools × --timeout` seconds.
- **Only a job that comes back `"done"` counts as a sample.** A job that outlives its
  wait window is returned still `"queued"` with no `error` field, and `mcp_core._unwrap_job`
  treats anything but `"done"` as "Studio did not finish in time". Counting that case
  reported the wait window itself as the measurement: with no peer and `--timeout 60`
  the table read `p50 60000 ms`, which looks like a finding about the tunnel.

When nothing was measured the script prints **no table at all** (a `--markdown` table of
`n/a` rows is the exact thing that gets pasted into the budget below as if it were real) and
instead prints up to five distinct failure reasons on stderr, so the operator does not have
to go read the bridge log for something the tool already knew. The pre-flight is advisory:
a target that cannot answer `/api/peers` — a bridge without that route, a token without read
scope, an unreachable host — is measured anyway and judged on its jobs.

Both were found by the P3-T7 smoke test and both are covered by the suite
(`TestV5OpsPlatform.test_operator_scripts_fail_loudly_without_a_studio_peer`).

**Tunnel latency budget** — fill this from a real run; do not publish estimates:

| Tool | local p50 / p95 (ms) | tunnel p50 / p95 (ms) | delta p50 |
|---|---|---|---|
| ping_studio | _pending operator run_ | _pending_ | _pending_ |
| get_studio_state | _pending operator run_ | _pending_ | _pending_ |
| script_read | _pending operator run_ | _pending_ | _pending_ |

Record alongside the numbers: sample size (`--runs`), date, provider and flavour
(`cloudflared` quick vs named, `ngrok`), region, and whether the MCP client or this
script produced them.

---

## 12. Console troubleshooting

### "The dashboard doesn't update"

The console is a static page plus `fetch()` calls against the authenticated
`/api/*` endpoints. It updates itself only while those calls succeed.

| Symptom | Cause | Fix |
|---|---|---|
| Everything sits at 0 / "checking…" after the bridge restarted | the page holds a stale token (or none) and every call is `401 bad token` | the console now shows a **token banner**; paste `bridge.token` (or reload from `http://127.0.0.1:<port>` where it auto-loads) |
| Same, but you opened the page from the **tunnel or LAN URL** | `/api/local-boot` deliberately returns **no** token for non-loopback clients, so the page cannot authenticate itself | paste the token once — it is kept in `localStorage` for that browser; or use `http://127.0.0.1:<port>` locally |
| Banner says "This tab is from an older bridge version" | the tab was opened before a bridge update | click **Reload** |
| Status pill shows **bridge unreachable** | the server process died or the port changed | restart `setup.py`; check the console window |

Why the token is not handed out over the network: the bridge is often exposed
through a public tunnel. A page that could read its own credential would hand
that credential to anyone who loads the URL. Loopback (`127.0.0.1`, `::1`) is the
only origin trusted to bootstrap — a forwarded address (`CF-Connecting-IP`,
`X-Forwarded-For`) is never treated as local.

```bash
# what does my command actually do?
python setup.py --dry-run --tunnel
#   server   : …\server.py --port 8077 --token … --tunnel --tunnel-flavor auto
#   tunnel   : supervised auto
#   dashboard: http://127.0.0.1:8077
```

### "Which URL do I give Arena?"

| You want | Use | Notes |
|---|---|---|
| Local only | `http://127.0.0.1:8077/mcp` | fastest; no token prompt in the console |
| Same LAN | `http://<lan-ip>:8077/mcp` | paste the token in the console |
| Across the internet | the `Tunnel is live` URL printed by `setup.py --tunnel` | `https://<host>/mcp`, same token |

`/api/health` is public on purpose (counts and versions only, no place data), so
a tunnel or uptime monitor can probe it without credentials.

## 13. Confirmation gate, subscriptions, and the playtest surface (v5.4)

### 13.1 Making a human decide (`--require-confirm`)

The gate implements MCP's MRTR pattern: a gated `tools/call` does not run and does
not fail — it answers `resultType: "input_required"` with an `inputRequests.confirm`
question and a signed `requestState`. When somebody approves it, the caller retries
with that exact `requestState` *and a different JSON-RPC id*, and only then does the
tool run.

| Mode | Gates | Use it when |
|---|---|---|
| `off` (default) | nothing | your agent is the only writer, and Studio is a sandbox |
| `destructive` | the destructive tools (`delete_instance`, `mass_set_properties`, `undo_redo`, …) | a shared place where deletion is the scary part |
| `write` | every tool that changes Studio (`annotations`-driven) | a real project you care about — the recommended lockdown |
| `all` | reads too | incident review / demonstration of the protocol |

```bash
python3 roblox-bridge/server.py --require-confirm write --confirm-ttl 180 --confirm-allowance 600
python3 roblox-bridge/mcp_server.py --require-confirm destructive   # stdio, same flags
```

`BRIDGE_REQUIRE_CONFIRM`, `BRIDGE_CONFIRM_TTL`, `BRIDGE_CONFIRM_ALLOWANCE`,
`BRIDGE_CONFIRM_WAIT_SECONDS` and `BRIDGE_MCP_AUTO_TASK_MS` are the env
equivalents; `BRIDGE_CONFIRM_SECRET` overrides the HMAC key (default: the bridge
token, so a per-process secret is fine but restarts invalidate pending requests).

**What a tool may touch on disk and on the network.** Every file-shaped tool
argument (`filePath`, `saveFile`, `outputFile`, `repoAsset`, `localDir`,
`directory`, `placeFile`, `path` on `luau_check`, `agent_bus` `export`) is
resolved through `mcp_core.safe_import_path()` / `safe_local_path()`: absolute
paths, `..` segments, `~`, NUL bytes and symlinks that leave the root are
refused, and so is anything outside an *import root* — the kit root, the
repository, and the system temp directory. `BRIDGE_IMPORT_ROOTS` adds more,
`os.pathsep`-separated (`~/Downloads:~/captures`), which is how you let
`import_image` read a screenshot from your Desktop without opening the whole
filesystem to a prompt-injected agent. Outbound fetches (`imageUrl`) go through
`safe_url()`: `http`/`https` only, and never at loopback, link-local, private,
reserved or cloud-metadata hosts.

**Two eras, one queue.** A 2026-07-28 client gets `input_required` immediately and
holds no connection. A legacy client (no MRTR) is *waited on* for
`--confirm-wait` seconds (default 45) so older agents still get an answer in-band;
on timeout the error names the `requestId` and how to approve it. A call promoted
to a task flips the task to `input_required` and `tasks/update {taskId,
inputResponses}` is the answer path.

**Who may answer.**

| Path | Notes |
|---|---|
| `POST /api/confirmations` `{"action":"approve","requestId":"cfm-…","note":"…","remember":true}` | the operator path; `source` defaults to `operator` |
| `arena_studio.py confirmations` / `approve <id> [--remember]` / `deny <id> [--note]` | same thing from a terminal (`BRIDGE_URL` + `BRIDGE_TOKEN`) |
| `bridge_ops` `confirm_approve` / `confirm_deny` | an *agent* may answer another principal's request (audited as `mcp-operator`); approving your own is refused with `reason: "operator_only"` |
| in-band `inputResponses` | **refused by default since 6.1.0.** The `elicitation` capability arrives in per-request `_meta`, which the caller writes, so a declared capability is not evidence that a human was asked — an agent could approve its own `execute_luau`. The caller gets `input_required` again with `reason: "client_answers_disabled"` and the operator command in the note. Set `BRIDGE_TRUST_CLIENT_ELICITATION=1` only if you run a host that genuinely elicits a human; `bridge_ops confirmations` reports which policy is in force as `trustClientElicitation` |

`remember: true` opens an allowance (`--confirm-allowance` seconds) for that
tool + principal, so a repeated loop does not re-prompt. Read actions and the
queue itself (`bridge_ops` `confirmations` / `subscriptions`) are never gated —
gating the answer to an approval is circular.

**Why the state is signed.** `requestState` is attacker-controlled data. It is an
HMAC over `{v,id,sub,aud,iat,exp,jti,dig}` where `sub` is the calling principal,
`aud` is `tools/call`, and `dig` is a canonical digest of the arguments. So a
stolen token cannot be used by another principal (`principal_mismatch`), cannot
authorize a *different* call (`digest_mismatch`), cannot outlive the TTL
(`expired`) and cannot be spent twice (`replayed` — the nonce is consumed when the
decision is taken). Declined and timed-out requests are terminal: retry the tool
call from scratch.

**What it costs in the open.** `/api/health` carries `confirmGate` counts only —
never the pending rows, because that endpoint needs no token. The queue with
argument previews is on `GET /api/confirmations` (control scope) and in
`/api/ops` → `mcp.confirmGate`.

### 13.2 Subscriptions

`subscriptions/listen {types: [...]}` is accepted for
`toolsListChanged | resourceSubscriptions` (and reports
`promptsListChanged`/`resourcesListChanged` as `idleTypes` rather than lying about
them). Unknown types are refused with `-32602`; a mix answers with
`unknownTypes`. The transport owns the stream: stdio keeps writing notifications to
stdout, Streamable HTTP switches that `POST /mcp` response to
`text/event-stream` (header `X-Arena-Subscription`). Every notification carries
`_meta["io.modelcontextprotocol/subscriptionId"]`.

`GET /api/subscriptions` lists live ones (`count`, per-sub `types`, `resources`,
`emitted`, `ageSeconds`); `POST /api/subscriptions/close {"subscriptionId":…}`
retires one; a `maxSeconds` pump retires itself when it finishes. `prompts/list`
and `prompts/get` therefore never hold a connection.

### 13.3 Playtest tools, and why they refuse

`simulate_input`, `manage_device_simulator`, `run_studio_tests` and
`playtest_capabilities` talk to `UserInputService:CreateVirtualInput()`,
`StudioDeviceSimulatorService` and `StudioTestService`. Baselines are **0.720**
(VirtualInput), **0.717** (Device Simulator), **0.700** for `StudioTestService`
and **0.715** for `ExecuteMultiplayerTestAsync`; a build below them gets
`unsupported: true` plus the exact call the bridge probed, not a fake success.
Contract rules both peers follow:

* no playtest running → `simulate_input` returns `ok: false`, `delivered: 0` and
  per-step `reason: "not_playing"` — VirtualInput into the edit DataModel is a
  no-op, so the bridge refuses rather than reporting a green delivery;
* anything aimed at built-in CoreGui is refused by the engine → the step is
  marked `reason: "coregui_refused"`, and `pinch` explains that
  `CreateVirtualInput()` exposes one pointer (script two drags);
* resolution / DPI / orientation / scaling overrides need an active device →
  `reason: "no_active_device"`; `action: "set"` applies device-then-overrides in
  one job because `SetDeviceAsync` clears the others; device changes stay applied
  until `stop`;
* `run_studio_tests` injects a temporary runner Script, always destroys it, and
  a watchdog ends the session (`EndTest({ok=false,…})` / `LeaveTest()`) so a hung
  test never leaves Studio in play mode.

Start with `playtest_capabilities` (a read) when you are unsure what the attached
Studio supports; it reports the probe result per service and the `playtest` state.
In `--read-only` mode input, device application and test runs are all blocked at
the queue (device `status` / `list` stay available).

### 13.4 Telemetry to page on

| Signal | Where |
|---|---|
| `bridge_confirmations_pending` | gauge — a human is being waited on |
| `bridge_mcp_subscriptions` | gauge — live `subscriptions/listen` streams |
| `bridge_mcp_events_total{kind}` | counter, one per `mcp.confirm.*` event |
| `mcp.confirm.requested / approved / denied / expired / applied` | `GET /api/events` (SSE or `?once=1`) |
| audit rows `actor="mcp"`, `outcome="denied"` for declines/expiry | `GET /api/audit?verify=1` |
| `confirmGate` block | `/api/health` (counts), `/api/ops` (full queue) |

`/api/audit?verify=1` covers approvals like everything else, so "who let the agent
delete that" is answerable from the hash-chained log alone.

### 13.5 When it does not behave

| Symptom | Cause and fix |
|---|---|
| `POST /api/confirmations` returns 404 | you are on a pre-5.4 build; those routes live in `do_POST` now |
| The blocked call never resumes | the retry must echo `requestState` **unchanged** with a new JSON-RPC `id`; check `structuredContent.reason` (`replayed` = you already used it once) |
| `reason: "digest_mismatch"` | the retried arguments differ from what was approved — re-issue the call and approve the new request |
| `reason: "confirmation_required"` with no way to answer | the client did not declare `elicitation`; an operator must approve (`arena_studio.py approve <id>`) |
| `-32021` on `tasks/*` | the client has not declared `extensions["io.modelcontextprotocol/tasks"]` |
| Every read prompts too | mode is `all`; `write` is the intended lockdown |
| Nothing is gated | `--require-confirm` was not passed, or the tool's annotations mark it read-only |
| Log notifications never arrive | the client must send `_meta["io.modelcontextprotocol/logLevel"]`; over single-shot HTTP the entries come back embedded as `io.modelcontextprotocol/logEntries` |

---
