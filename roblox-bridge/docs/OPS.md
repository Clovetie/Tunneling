# v5 Ops Platform — tunnels, multi-agent coordination, telemetry, scoped auth

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
bridge_tunnel_online, bridge_uptime_seconds
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
recent jobs and audit head. The dashboard's **tab 7 — Ops Console & Agent Bus**
renders it live.

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
| `POST /api/ops` | admin | `reap`, `queue_clear` |
| `POST /api/jobs` | write | + `idempotencyKey`, `priority`, `maxRetries`, `timeout` |

`GET /api/health` stays public (no place data, no secrets) so tunnels and uptime
monitors can probe it.

---

## 7. MCP surface changes

* **70 tools** (was 68): `agent_bus` and `bridge_ops`.
* **3 new resources**: `studio://bus`, `studio://ops`, `studio://audit`.
* Read-scoped tokens see mutating tools as blocked, both in `tools/call` and in
  the returned `isError` payload (`reason: insufficient_scope`).

```jsonc
{"name":"bridge_ops","arguments":{"action":"status"}}
{"name":"bridge_ops","arguments":{"action":"metrics","format":"prometheus"}}
{"name":"bridge_ops","arguments":{"action":"audit_verify"}}
{"name":"bridge_ops","arguments":{"action":"tunnel_restart"}}
```

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
python3 -B roblox-bridge/test_bridge_mcp.py          # 25 tests
```

v5 coverage: scoped roles over HTTP and MCP, rate-limit 429s, bus presence/mailbox/
lease expiry/handoff export, idempotency, priority ordering, retry-then-fail,
orphan requeue, timeout reaping, Prometheus exposition, SSE snapshot, audit
tamper detection, tunnel URL scraping and the missing-binary path.

## 10. Live acceptance

_Live Studio acceptance for v5 is pending a connected peer._ Record the output of
`bridge_ops {action:"status"}`, `/api/tunnel` while online, and
`self_test.studioSmoke` here when a peer is attached. Planned drills: **G7**
(live acceptance) and **G8** (kill-switch) in `handoff/GEMINI-4-ARGON.md`.
