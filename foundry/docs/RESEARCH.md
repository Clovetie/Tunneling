# FOUNDRY research pass — Tunneling integration

Date: 2026-11-18. Branch: `arena/ba57f52b-tunneling`.

## Source inventory

Repository inventory and targeted inspection (not a line-by-line audit of every bridge module, TOOL_SPEC, kit manifest or test):

- `AGENTS.md` — repository layout, golden rules, tool categories, `dm="edit"`, `luau_check`, `replaceInstance`, playtest verification.
- `README.md` — v6.0.0 overview, 101 MCP tools, quick start, tunnel setup, verification commands.
- `CONNECT.md` — operator manual, URL rules, HANDS MODE, sandbox egress limits, tunnel lifecycle, plugin token traps, endpoint/job envelope, CLI.
- `SESSION.md` — live credential/state history, HANDS MODE, dead tunnels, token-rotation warning, prior drift closure.
- `roblox-bridge/docs/OPS.md` — supervised tunnel, Agent Bus, scoped tokens, resilient jobs, observability, REST reference, confirmation gate, subscriptions, playtest refusals, tests/drills.
- `roblox-bridge/docs/MULTI_AGENT_ARCHITECTURE.md` — existing `arena_team/` architecture, layering rules, permissions, risks, verification plan.
- `roblox-bridge/docs/MULTI_AGENT_GUIDE.md` — `agent_team` actions, approval modes, REST/MCP usage, Tab 9, evidence, configuration.
- `roblox-bridge/mcp_server.py` — stdio MCP entry, NDJSON + `Content-Length` framing, embedded/attach bridge lifecycle, env flags.
- `roblox-bridge/server.py` — REST route inventory, `/mcp`, `/api/orchestrator`, `/api/bus`, `/api/studios`, `/api/confirmations`, `/api/events`, `/api/audit`, `/api/ops`, `/api/tunnel`.
- `roblox-bridge/mcp_core.py` — `MCPDispatcher`, `initialize`, `tools/list`, `tools/call`, `server/discover`, `tasks/*`, annotations, `TOOL_SPECS`.
- `roblox-bridge/auth_core.py` — `read < write < admin`, hashed scoped tokens, rate limits.
- `roblox-bridge/confirm_gate.py` — signed MRTR `requestState`, approval/deny/expiry, principal binding.
- `roblox-bridge/bus_core.py` — presence, mailbox, task board, leases, dependencies, feed/export.
- `roblox-bridge/telemetry.py` and `roblox-bridge/tunnel_manager.py` — events, audit chain, tunnel supervisor.

## Verified live surface

A local **MockWorld / simulated Studio** bridge on `http://127.0.0.1:8077` was queried with a scoped preview token. This validates HTTP/MCP contracts only; it is not a real Roblox Studio, public tunnel or production deployment. Observed:

- `GET /api/health`: `version 6.0.0`, `mcp_tools 101`, `studio_connected true`, `confirmGate.mode write`, Tunneling tunnel `state stopped`.
- `GET /api/state`: keys `bus`, `mcp_tools`, `peers`, `queued`, `read_only`, `recent`, `server_time`, `snapshots`, `studio`, `studio_connected`, `tunnel`, `uptimeSeconds`, `version`.
- `GET /api/peers`: `{peers, onlineCount}`; peer rows include `peerId`, `dm`, `place`, `placeId`, `gameId`, `pluginVersion`, `online`, `ageSeconds`, `hasClient`, `playerCount`.
- `GET /api/studios`: `{count, hint, ok, online, pinOnline, pinned, routing, studios}`; studio rows include `peerId`, `studioId`, `label`, `dm`, `place`, `placeId`, `online`, `queuedJobs`, `defaultTarget`.
- `GET /api/bus?action=stats`: `{agents, agentsOnline, tasks, taskCounts, messages, events, cursor, ...}`.
- `GET /api/orchestrator?action=tool_manifest&query=agent`: returns real tool names, descriptions and `readOnlyHint`; no inventory is hardcoded by FOUNDRY.
- `GET /api/confirmations`: `{pending, pendingCount, mode, counters, allowances, recentDecisions, subscriptions, ...}`.
- `POST /mcp tools/list` with `MCP-Protocol-Version: 2026-07-28`: `101` tools; each has `name`, `description`, `inputSchema`, `annotations`, `execution.taskSupport`. `agent_team` is present with an `action` enum.
- `GET /api/events?once=1`: `{events, count, cursor}`; event rows have `seq`, `ts`, `kind`, `level`, `data`.
- `GET /api/audit?limit=2`: `{entries, count, cursor}`; entries include SHA-256 `prev`/`hash` chain fields.
- `GET /api/ops`: `{audit, bus, fleet, health, jobLatency, mcp, metrics, ok, openTasks, queue, recentJobs, servedAt, sweeper, tunnel, uptimeSeconds, version}`.
- `GET /api/tunnel`: state machine fields including `state`, `running`, `publicUrl`, `mcpUrl`, `lastKnownUrl`, `binaryAvailable`, `namedTokenValid`, `health`.

## Constraints carried into FOUNDRY

1. Do **not** duplicate the MCP server, Studio plugin, Agent Bus, ConfirmGate, scoped-token store, tunnel supervisor, or Roblox tool implementations.
2. Discover MCP tool schemas with `tools/list` / `server/discover`; never freeze a tool inventory.
3. Keep all bridge tokens, provider keys and tunnel credentials server-side and encrypted at rest.
4. Use `dm="edit"` for persistent Studio changes; treat `no_peer` and `studio_connected:false` as real failures.
5. Use `ConfirmGate` / `/api/confirmations` as Tunneling's approval store; FOUNDRY adds its own higher-level approval records but must not claim a second bridge gate.
6. The sandbox cannot reach `*.trycloudflare.com` or a user's localhost. Real Studio/tunnel acceptance requires HANDS MODE or deployment with a reachable named tunnel.
7. Studio's plugin polls the local bridge from the user's machine. `/api/poll` is not a general agent API.
8. Client DataModel plugin instances cannot issue `HttpService` requests; `agentTeam` summary travels through `/api/poll`.
9. Durable execution must run in a persistent worker process, not inside a Vercel serverless request.
10. The existing `arena_team` layer is the Roblox specialist supervisor. FOUNDRY is a secondary, normalized orchestrator that delegates Roblox work to it while supporting non-Roblox agents.
