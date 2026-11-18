# Arena Multi-Agent Team — Operator Guide

This guide covers the `agent_team` MCP tool, `GET|POST /api/orchestrator`,
Control Center Tab 9, and the compact Studio DockWidget strip. It is written for
bridge `6.0.0` with the `arena_team/` orchestration layer.

For design rationale and invariants, see
[`MULTI_AGENT_ARCHITECTURE.md`](MULTI_AGENT_ARCHITECTURE.md).

---

## 1. Before you start

1. Install the bundled Studio plugin and make sure an **Edit** DataModel peer is
   online. The team refuses a write project when `requireStudio=true` and no live
   Edit peer is present.
2. Start the bridge with a token and human approvals enabled:

   ```bash
   cd roblox-bridge
   python3 server.py --token "$(cat bridge.token)" --require-confirm write
   ```

   Default local address: `http://127.0.0.1:8077`. The token can also be supplied
   with `--token` or the `BRIDGE_TOKEN` environment variable. Do not paste
   the token into a project objective or a shared status payload.
3. Open the Control Center at `/` and select **ARENA Team** (Tab 9). The Studio
   DockWidget also shows a compact `ARENA TEAM` status from `/api/poll`.

The orchestrator does **not** create a second job queue, a second approval store,
or new Studio tools. It reuses the existing Agent Bus, MCP dispatcher/tool map,
`ConfirmGate`, telemetry/audit, snapshots, fleet routing and Studio plugin.

---

## 2. Permissions

Scoped tokens are enforced identically by REST and MCP.

| Role | Allowed `agent_team` actions |
|---|---|
| `read` | `status`, `roster`, `config`, `plan`, `board`, `feed`, `logs`, `approvals`, `confirmations`, `report`, `evidence`, `studio`, `stats`, `projects`, `studio_summary`, `tool_manifest` |
| `write` | all read actions plus `start_project`, `pause`, `resume`, `cancel`, `message`, `approve`, `deny`, `undo`, `create_snapshot`, `restore_snapshot`, `run_task`, `claim`, `complete` |
| `admin` | all write actions plus `configure` and `reset`; `approvalMode="off"` also requires `allowUnattended=true` and is audited |

`reset` is deliberately disabled; remove `bridge_state/arena_team/` manually
after stopping the bridge if you need a clean project history.

Read-Only Inspector mode (`--read-only`) permits read actions and blocks every
write action. A rejected action returns HTTP 403 or an MCP tool error; it never
queues a Studio job.

---

## 3. Start a project

### MCP `tools/call`

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "agent_team",
    "arguments": {
      "action": "start_project",
      "objective": "Build a crystal shrine with a responsive HUD and server-authoritative loot chest",
      "approvalMode": "plan",
      "requireStudio": true
    }
  }
}
```

Send `Authorization: Bearer <write-or-admin-token>` (or the bridge's
`X-Arena-Token` header). The call returns immediately with `projectId`,
`peerId`, `agentCount` and `studioConnected`; work runs on a daemon thread.

### REST

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8077/api/orchestrator \
  -d '{"action":"start_project","objective":"Build a crystal shrine with a responsive HUD and server-authoritative loot chest","approvalMode":"plan","requireStudio":true}'
```

`GET /api/orchestrator?action=<action>&...` accepts the same actions with query
parameters. `POST` is preferred for writes and long objectives.

### Approval modes

| Mode | Behaviour |
|---|---|
| `plan` (default) | One project-wide `agent_team:project_plan` confirmation after analysis/design. Approve it in Tab 9, `/api/confirmations`, or `arena_studio.py approve`. |
| `each_write` | Same plan approval plus a confirmation before every consequential write. |
| `off` | Admin-only, requires `"allowUnattended": true`. No human gate, but the bypass is written to the hash-chained audit and timeline. |

A denial, expiry, missing Studio peer, Luau syntax failure, hash conflict or
console error fails the project. The final report never says `verified: true`
unless tasks, read-backs, QA, evidence and the audit chain all pass.

---

## 4. Observe and control

Common read actions:

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:8077/api/orchestrator?action=status'
```

| Action | What it returns |
|---|---|
| `status` | One redacted Control Center snapshot: project, studio, roster, plan, task counts, feed, logs, approvals, evidence, report, config diagnostics |
| `roster` | Configured agents merged with live Bus presence |
| `plan` / `board` | Design, DAG tasks, Agent Bus task board |
| `feed` / `logs` | Bus thread + project timeline |
| `approvals` / `confirmations` | `ConfirmGate` queue and project approval rows |
| `report` | Final report with `verified`, `verification`, `failedTasks`, `warnings`, evidence and audit-chain status |
| `evidence` | Evidence metadata; fetch PNGs from `/api/orchestrator/evidence/<filename>` with a `read` token |
| `studio` / `studio_summary` | Peer, place, selection, gate and read-only posture |
| `stats` / `projects` | Counters and persisted project list |
| `tool_manifest` | Real MCP tool names/descriptions; agents may call only names present here |

Common write actions:

```bash
curl -sS -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -X POST http://127.0.0.1:8077/api/orchestrator \
  -d '{"action":"approve","requestId":"cfm-...","note":"reviewed plan"}'
```

Use `approve`/`deny` with the `requestId` from `approvals.pending` or Tab 9.
`pause`, `resume`, `cancel`, `message`, `run_task`, `claim` and `complete` take
optional `projectId` / `taskKey`. `undo` and `restore_snapshot` open a
confirmation before calling the existing `undo_redo` / `manage_snapshots` tools.
`create_snapshot` writes a recovery point before risky work.

Every response is redacted: bearer tokens, external-driver credentials and
sensitive keys are never echoed.

---

## 5. Control Center Tab 9 and Studio strip

Tab 9 (**ARENA Team**) polls `status`, `roster`, `plan`, `board`, `feed`, `logs`,
`approvals`, `evidence` and `report` every 2.5 seconds while active. It renders:

* objective, phase, status, verified badge and Studio peer;
* start / pause / resume / cancel / message / snapshot / undo controls;
* roster with online state, activity, ownership and allowed tools;
* plan DAG, task board, Bus feed, timeline logs;
* pending confirmations with Approve / Deny / Remember;
* evidence thumbnails linking to the authenticated PNG route;
* final Markdown report and audit-chain verification.

`Start Project` sends `start_project`. Approval buttons send `approve`/`deny`.
The UI uses the same scoped token as the rest of the Control Center; a `read`
token can observe but cannot mutate.

The Studio plugin's DockWidget reads `agentTeam` from the existing `/api/poll`
response. It shows status/phase, objective, agents online, tasks done, pending
approvals, the active task and `VERIFIED YES/NO`. Client DataModels cannot call
`HttpService`, so the poll summary is the only safe in-Studio channel; no new
plugin HTTP endpoint is introduced.

---

## 6. Configuration

The default team lives in `arena_team/agents.json`. Each agent declares:

* `key`, `name`, `role`, `capabilities`, `permission`;
* `ownedPaths` — exclusive Studio dot-path prefixes;
* `allowedTools` — must be real `mcp_core.TOOL_MAP` names;
* `driver` — `builtin`, `external` (endpoint from an environment variable), or
  `human` (claims Bus tasks through the UI).

`status_view()` exposes `config.unknownTools` and
`config.ownershipConflicts`; both must be empty. To reload safely:

```json
{"action":"configure","configPath":"/absolute/path/to/team.json"}
```

or pass an inline `agents` array plus optional `settings`. `configure` requires
`admin`. The registry validates every tool name, detects overlapping ownership,
and re-registers Bus seats. Idle bridges do **not** pre-register seats: presence
is created on the first project/write action, so an operator's existing Agent
Bus is not polluted by merely opening the dashboard.

---

## 7. What a verified project actually does

1. Analysis uses `get_studio_state`, `diagnose_connection`, `survey_place`,
   `manage_selection{action:"get"}` and `playtest_capabilities`.
2. A deterministic planner derives a technical design and a dependency DAG from
   the real survey/peer data. Conflicting ownership is rejected.
3. The default `plan` mode waits for a human `ConfirmGate` decision.
4. Implementation tasks claim Bus leases. Every script write runs
   `luau_check` first, uses `dm="edit"`, `expectedHash` from a live read, and
   `replaceInstance=true` for cached ModuleScripts.
5. Writes are serialized on the orchestrator lock; read-backs are recorded as
   verification receipts. Worker threads install an MCP call context, so each
   queued Studio job carries an `arena_team:*` actor and a trace containing the
   project, task, agent and tool — without source text or credentials.
6. QA runs playtest/console/viewport/audit tools. Reproducible failures can add
   bounded repair tasks; a missing peer or console error is never reported as
   success.
7. The report is `verified: true` only when all tasks are done, every write has
   a read-back, QA passed, Studio was connected, evidence exists, and the
   SHA-256 audit chain verifies.

Evidence PNGs are stored under `bridge_state/arena_evidence/` (gitignored) and
served only to authenticated `read` callers.

---

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| `no_peer: no live Studio Edit peer` | Plugin installed, Studio open, Edit DataModel polling, `/api/health` shows `studio_connected:true`; pass `requireStudio:false` only for analysis-only runs. |
| `approvalMode='off' requires admin scope and allowUnattended=true` | Use an admin token and set both fields; the bypass is audited. |
| Project stuck at `awaiting_approval` | Answer the pending `requestId` from `approvals` or Tab 9; denials now settle the project as `failed` without a stale-status race. |
| `agent_team ... requires write scope` | Mint a `write` token via `TokenStore.create(label, role="write")` or use the primary admin token. |
| `unknownTools` not empty | Fix `allowedTools` names against `tool_manifest`; do not invent MCP tools. |
| `ownershipConflicts` not empty | Give each implementation agent a disjoint `ownedPaths` prefix. |
| `verified:false` | Open `report.failedTasks`, `report.warnings`, `report.verification` and the audit chain; do not treat the place as successfully changed. |
| Tab 9 blank | Confirm the bridge token is present in the browser session, no CSP/proxy blocks `/api/orchestrator`, and the dashboard JS loaded without console errors. |
| Studio strip blank | Confirm the plugin is v6, the DockWidget is open and `/api/poll` includes `agentTeam`. |

---

## 9. Verification commands

```bash
# Dedicated offline unit + MockWorld end-to-end suite
python3 -B roblox-bridge/test_arena_team.py

# Full existing bridge/MCP regression suite (includes self_test tool matrix)
python3 -B roblox-bridge/test_bridge_mcp.py

# Syntax-only checks after editing dashboard JS or Luau
node --check /tmp/arena_dashboard.js
python3 -B -m py_compile roblox-bridge/server.py roblox-bridge/mcp_core.py roblox-bridge/arena_team/*.py
```

Both suites are offline and use temporary state directories. A successful
`test_arena_team.py` run exercises scoped tokens, plan approval, real
`dm="edit"` writes with `expectedHash`/`replaceInstance`, Luau pre-flight,
read-backs, playtest QA, viewport PNG evidence, the poll summary, audit-chain
verification, denied plans, no-peer failure and Read-Only Inspector mode.

Real Studio, tunnel/egress and browser rendering still require a manual check
against a live plugin and the deployed preview host.
