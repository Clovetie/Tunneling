# v5 Ops Platform — tunnels, multi-agent coordination, telemetry, scoped auth

_Applies to bridge `5.2.0`._

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
python setup.py --tunnel                      # auto-detect cloudflared or ngrok
python setup.py --tunnel --tunnel-flavor ngrok
python setup.py --tunnel --tunnel-token "$CF_TOKEN"     # named Cloudflare tunnel
BRIDGE_PUBLIC_URL=https://mcp.example.com python setup.py   # you run the tunnel
```

```bash
python server.py --tunnel --tunnel-flavor cloudflared --port 8077
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
* `recentJobs` / `openTasks` — unchanged, newest first.

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
| `POST /api/jobs` | write | + `idempotencyKey`, `priority`, `maxRetries`, `timeout` |

`GET /api/health` stays public (no place data, no secrets) so tunnels and uptime
monitors can probe it.

---

## 7. MCP surface changes

* **70 tools** (was 68): `agent_bus` and `bridge_ops`.
* **7 prompts** (was 4): `multi_agent_handoff`, `bus_standup` and `task_triage`
  render live bus state (roster, task board, cursor) and fall back to a one-line
  "live bus state is unavailable" note when the bridge is offline.
* **10 resources** (was 5 at v4, 8 at v5.0): `studio://state`, `peers`,
  `console`, `selection`, `survey`, `bus`, `ops`, `audit`, plus v5.2's
  `studio://jobs` (queue depth, pause state, sweeper counters, recent jobs and
  per-tool p50/p95) and `studio://handoff` (the bus export as markdown —
  `text/markdown`, paste-ready brief for the next agent or human).
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
└── tunnel.json     # last known public URL
```

Override with `--state-dir` or `BRIDGE_STATE_DIR`.

---

## 9. Tests

```bash
python3 -B roblox-bridge/test_bridge_mcp.py          # 35 tests
```

v5 / v5.1 / v5.2 coverage: scoped roles over HTTP and MCP, rate-limit 429s, bus presence/
mailbox/lease expiry/handoff export, stale-agent sweeping, incremental
(`since` / `summaryOnly`) handoff briefs, MCP prompt rendering with live bus state
and the offline fallback, named-token validation, idempotency, priority ordering,
retry-then-fail, orphan requeue, timeout reaping, Prometheus exposition, SSE
snapshot, audit tamper detection, responsive Ops tab + `node --check` of the
dashboard script, the latency script (percentiles, 401 path), the two operator
scripts' failure modes, tunnel URL scraping and the missing-binary path, and
v5.2's maintenance pause (jobs held, admin-only, resume reclaims the same job),
the automatic sweeper (release + metric + audit entry, silent no-op, idempotent),
per-tool latency percentiles, the two new `studio://` resources, and the
`arena_studio.py queue` CLI round trip.

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

**Tunnel latency budget** — fill this from a real run; do not publish estimates:

| Tool | local p50 / p95 (ms) | tunnel p50 / p95 (ms) | delta p50 |
|---|---|---|---|
| ping_studio | _pending operator run_ | _pending_ | _pending_ |
| get_studio_state | _pending operator run_ | _pending_ | _pending_ |
| script_read | _pending operator run_ | _pending_ | _pending_ |

Record alongside the numbers: sample size (`--runs`), date, provider and flavour
(`cloudflared` quick vs named, `ngrok`), region, and whether the MCP client or this
script produced them.
