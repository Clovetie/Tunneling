# FOUNDRY / Tunneling integration contracts

> **Status:** these are the intended wire-level contracts and the client code written to follow them. Pure clients/tests and a read-only MockWorld protocol preview are validated; DB-backed persistence, provider execution, real Studio and tunnel egress remain operator-gated.

FOUNDRY stores one `integrations` row of `kind='tunneling'`. Secrets live in `integration_secrets`, encrypted with AES-256-GCM using `FOUNDRY_SECRET_ENCRYPTION_KEY`.

## Config shape

```json
{
  "kind": "tunneling",
  "name": "Primary Roblox bridge",
  "transport": "streamable_http",
  "baseUrl": "https://mcp.example.com",
  "config": {
    "mcpEndpoint": "https://mcp.example.com/mcp",
    "protocolVersion": "2026-07-28",
    "stdioCommand": "python3",
    "stdioArgs": ["roblox-bridge/mcp_server.py", "--no-embedded"],
    "stdioCwd": "roblox-bridge",
    "httpAllowlist": [],
    "timeoutMs": 30000
  }
}
```

For `transport='stdio'`, the connector spawns `stdioCommand` with `stdioArgs` via `child_process.spawn(..., {shell:false})`, sets `BRIDGE_URL` / `BRIDGE_TOKEN` from server-side config, and speaks NDJSON or `Content-Length` JSON-RPC. Commands are validated against an allowlist; untrusted agents cannot choose a host command.

For `transport='streamable_http'`, requests go to `${baseUrl}/mcp` with:

```
Authorization: Bearer <bridge token>
X-Arena-Token: <bridge token>
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
```

## MCP methods used

- `initialize` — negotiates protocol version; FOUNDRY prefers `2026-07-28`.
- `server/discover` — reads supported versions/capabilities without a handshake.
- `tools/list` — **the only source of tool inventory**; follows `nextCursor`.
- `tools/call` — validates arguments against the discovered `inputSchema` before sending.
- `resources/list`, `resources/templates/list`, `prompts/list` — optional discovery.
- `tasks/get`, `tasks/result`, `tasks/list`, `tasks/cancel`, `tasks/update` — optional task handles.
- `subscriptions/listen` — not used by the browser; Supabase Realtime is FOUNDRY's UI stream.

`tools/call` may return `resultType:'input_required'` (MRTR). FOUNDRY stores `requestState` in an approval record and only retries after a human decision.

## REST endpoints used

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | public liveness, version, `studio_connected`, gate counts, tunnel summary |
| `GET /api/state` | canonical state + recent jobs |
| `GET /api/peers` | raw DataModel peer list |
| `GET /api/studios` | fleet, pin, routing, labels |
| `POST /api/studios` | `set_active`, `clear_active`, `label`, read-only `broadcast` |
| `GET /api/ops` | consolidated operations report |
| `GET /api/events?once=1` | event snapshot; SSE stream optional |
| `GET /api/audit` / `?verify=1` | hash-chained audit |
| `GET /api/tunnel` | supervised tunnel state |
| `GET /api/confirmations` | ConfirmGate queue |
| `POST /api/confirmations` | approve/deny/expire with `requestId` |
| `GET|POST /api/bus` | Agent Bus reads/writes, normalized into FOUNDRY messages |
| `GET|POST /api/orchestrator` | `agent_team` status, plan, roster, approvals, report, evidence metadata |
| `GET /api/orchestrator/evidence/<basename>` | authenticated PNG evidence |
| `POST /api/jobs` | low-level job compatibility; FOUNDRY prefers MCP tools |

## Observed shapes

- Health: `{ok, version, mcp_tools, studio_connected, studio, peers, queued, jobs, uptimeSeconds, authMode, confirmGate, tunnel, agents, agentsOnline, studios, queuePaused, sweeper}`.
- Studio peer: `{peerId, dm, place, placeId, gameId, pluginVersion, studioVersion, client, hasClient, playerCount, online, ageSeconds, polls, ...}`.
- Fleet studio: `{peerId, studioId, label, dm, place, placeId, gameId, online, ageSeconds, pinned, queuedJobs, defaultTarget, ...}`.
- MCP tool: `{name, title, description, inputSchema, annotations{readOnlyHint,destructiveHint,idempotentHint,...}, execution{taskSupport,confirmationRequired}}`.
- Tool call result: `{content:[{type,text|data}], structuredContent?, isError?, resultType?, inputRequests?, _meta?}`.
- Event: `{seq, ts, kind, level, data}`.
- Audit entry: `{seq, ts, action, actor, target, outcome, detail, prev, hash}`.
- Bus stats: `{agents, agentsOnline, tasks, taskCounts, messages, events, cursor, ...}`.

## Security mapping

| Tunneling role | FOUNDRY use |
|---|---|
| `read` | health, state, peers, studios, tool discovery, read-only MCP calls, evidence, approvals queue |
| `write` | MCP writes, Agent Bus writes, `/api/studios`, `agent_team` write actions |
| `admin` | token/tunnel/queue controls; FOUNDRY does not expose these by default |

FOUNDRY stores one admin/write bridge credential server-side. Per-agent ceilings are enforced in FOUNDRY before any tool call. Consequential operations also require a FOUNDRY human approval. Neither the browser nor a model ever receives the bridge token.
