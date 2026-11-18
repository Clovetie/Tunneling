# Arena Multi-Agent Orchestration — Integration Architecture

_Applies to bridge `6.0.0` + the `arena_team/` orchestration layer. Written after a targeted
inventory of `server.py`, `mcp_core.py`, `bus_core.py`, `confirm_gate.py`, `auth_core.py`,
`telemetry.py`, `tunnel_manager.py`, `ArenaBridge.lua`, `mock_studio.py`, and
`test_bridge_mcp.py`. Nothing below invents an MCP tool: every tool name used by the
agent layer was read out of `mcp_core.TOOL_SPECS` / `TOOL_MAP` at runtime._

---

## 1. What already exists (do not rebuild)

| Existing component | Where | Reused for |
|---|---|---|
| Studio plugin, 3 DataModels, `ChangeHistoryService` recording, long-poll job queue, `/api/poll`, `/api/result` | `ArenaBridge.lua`, `server.py` | all real place edits; `dm="edit"` persists |
| MCP `stdio` + Streamable HTTP, dual-era dispatcher, annotation-based read-only classification, scoped-token scope enforcement | `mcp_server.py`, `mcp_core.MCPDispatcher`, `server._enforce_mcp_scope` | the new `agent_team` tool and every agent tool call |
| 101 annotated MCP tools (schemas in `TOOL_SPECS`) | `mcp_core.py` | agents execute **existing** tools through `execute_tool()` |
| `luau_check` pre-flight, `write_script{expectedHash,replaceInstance}`, `script_read`, `multi_edit`, `drift_and_sync` | `mcp_core.py` | validated writes, cache-busting, version guards |
| `survey_place`, `get_studio_state`, `inspect_instance`, `manage_selection{action:"get"}`, `capture_viewport`, `render_offline`, `audit_3d_scene` | `mcp_core.py` | place analysis, viewport evidence, visual inspection |
| `run_playtest_scenario`, `get_console_output`, `start_stop_play`, `playtest_capabilities` | `mcp_core.py` | QA verification, error inspection |
| `audit_network_security`, `perf_audit`, `audit_ui_layout`, `de_slop_audit`, `script_grep` | `mcp_core.py` | security/performance/UI audits |
| `spawn_kit_mesh`, `search_assets`, `import_ui_from_web`, `install_template`, `install_kit_module`, `build_gameplay_mechanic`, `build_tree`, `search_cloud_icons` | `mcp_core.py`, `roblox-kit/` | mesh-first world building, UI, gameplay systems |
| Agent Bus: presence, mailbox (direct/broadcast, threads, read receipts), task board with **leases, dependencies, priorities, artifacts, sweep/recovery**, event feed, markdown export | `bus_core.AgentBus` | registration, messaging, task creation/claim/complete, broadcasts, handoffs |
| Multi-Studio fleet: peer registry, pin/label, read-only broadcast, `peerId` routing | `server.fleet_snapshot`, `studio_fleet` | multiple simultaneous Studios, DataModel targeting |
| Human-in-the-loop MRTR confirmation gate (signed `requestState`, HMAC, TTL, nonces, allowances, `/api/confirmations`) | `confirm_gate.ConfirmGate` | **the** approval queue for consequential agent operations |
| Scoped tokens `read < write < admin`, hashed storage, rate limits | `auth_core.TokenStore` | permission levels for REST, MCP and per-agent ceilings |
| Telemetry: metrics, SSE events, SHA-256 hash-chained audit | `telemetry.py` | full execution history, progress reporting |
| Tunnel supervisor + kill switch | `tunnel_manager.py` | external access (unchanged) |
| Control Center (8 tabs) + Studio `DockWidgetPluginGui` | `server.dashboard_html()`, `ArenaBridge.lua` | new Tab 9 + compact in-Studio agent strip |
| `mock_studio.MockWorld` + `test_bridge_mcp.py` | `roblox-bridge/` | offline end-to-end verification |

### Discovered tool-schema facts that constrain the design

* `write_script`/`multi_edit` accept `expectedHash` and `replaceInstance`; `validateSyntax`
  runs `luau_check` before the write. `execute_tool` raises on a failed Studio job
  (`NoPeerError` on `no_peer`), so a driver can distinguish "not verified" from "done".
* `capture_viewport{saveFile, returnInline:false}` writes a real PNG and returns metadata;
  `capture_viewport` requires an `edit` peer because it submits an `inspect` job.
* `run_playtest_scenario` returns `ok`, `consoleCount`, `serverResult`, `clientResult`,
  `sessionStopped`/`sessionSurvived`; `get_console_output{level:"Error"}` is the error channel.
* `manage_selection{action:"get"}` is the read-only selection query; `manage_snapshots`
  supports `create`/`restore`/`diff` for undo/recovery.
* `agent_bus` and `bridge_ops` are mixed read/write tools; `is_tool_call_read_only()`
  classifies their actions, and `server._enforce_mcp_scope()` uses that classification.
  Any new mixed tool **must** be added to both, plus `INSPECTOR_MIXED_TOOLS`.
* `test_bridge_mcp.py` asserts exactly 7 prompts and 12 resources. The new surface is
  therefore **one MCP tool + REST endpoints**, not new resources/prompts, so the existing
  suite stays green.
* `/api/health` is public and must never carry previews or secrets; `/api/poll` is the
  plugin's authenticated 1 Hz channel and is the cheapest way to surface agent state
  inside Studio.

---

## 2. Integration architecture

```
                     ┌──────────────────────── Arena Control Center Tab 9 ─────────────────────┐
                     │ roster · board · feed · plan · logs · approvals · previews · timeline   │
                     └───────────────┬───────────────────────────────────┬───────────────────┘
                                     │ GET/POST /api/orchestrator          │ POST /api/confirmations
  MCP host (stdio/HTTP)              │                                      │
  agent_team tool ───────────────────┤                                      │
                                     ▼                                      ▼
  ┌────────────────────────────────────────── server.py / BridgeState ───────────────────────────────┐
  │  direct_dispatch("orchestrator", body) ──► Orchestrator.handle(body, principal)                  │
  │                                             │                                                   │
  │      ┌──────────────────────────────────────┼───────────────────────────────────────┐          │
  │      ▼                                      ▼                                       ▼          │
  │  AgentRegistry                        ProjectStore                         ConfirmGate       │
  │  (bus presence, heartbeats,           (persistent context, revision,       (existing MRTR    │
  │   config, ownership, drivers)          plan, timeline, evidence)            approval queue)  │
  │      │                                      │                                       │          │
  │      └──────────────► Scheduler / project worker thread ◄──────────────────────┘          │
  │                            │  claim lease (bus) → dispatch → verify → complete              │
  │                            ▼                                                                │
  │                     AgentDriver (builtin playbook | external HTTP | human)                    │
  │                            │  ToolRunner policy gate                                          │
  │                            │  · allowedTools · permission · owned paths · conflicts          │
  │                            │  · expectedHash / revision · approvals · audit/timeline          │
  │                            ▼                                                                │
  │                     mcp_core.execute_tool(state.mcp_client, …)  ← SAME tool pipeline        │
  └───────────────────────────────────────┬────────────────────────────────────────────────────┘
                                          │ Studio jobs (edit / server / client)
                       ┌──────────────────▼──────────────────┐
                       │ ArenaBridge.lua plugin (Studio fleet) │  /api/poll carries agentTeam summary
                       └─────────────────────────────────────┘
```

### Layering rules

1. **No new MCP tools for Studio work.** Agents call `execute_tool()` with names that are
   looked up in `TOOL_MAP`; an unknown name is rejected before any job is submitted.
2. **The Bus is the only coordination store.** Tasks, leases, dependencies, messages,
   presence and handoffs live in `bus_core.AgentBus`. `arena_team` keeps *project*
   context (objective, design, evidence, timeline) in its own JSON files, never Studio
   data and never a second task board.
3. **The gate is the only approval store.** Consequential steps call
   `ConfirmGate.request(...)`, wait on `Confirmation.event`, and are answerable from the
   existing `/api/confirmations` API, `arena_studio.py approve/deny`, or the new Tab 9.
4. **The bridge token never leaves the server.** `Orchestrator` is given service objects
   (bus, telemetry, gate, mcp_client, fleet snapshotter), not the raw token. All outbound
   previews and status payloads are passed through a redactor. External driver credentials
   are read from environment variables and are never echoed in status/config output.
5. **Studio work is serialized on a write lock** and confined to `dm="edit"` for
   persistent changes; playtest tools target `server`/`client` and are verification-only.
6. **Bus presence is lazy.** An idle bridge can render the configured roster and
   answer read-only `status` calls without inserting seats into the operator's
   existing Agent Bus. Seats and the `human-operator` seat are registered on the
   first project/write action (or when a persisted project is restored).

---

## 3. Development pipeline (implemented)

`start_project` → analysis (`get_studio_state`, `diagnose_connection`, `survey_place`,
`manage_selection`, `playtest_capabilities`) → technical design derived from the **real**
survey numbers → DAG with dependencies and exclusive path ownership → human approval of
the project-wide change set (default `approvalMode="plan"`) → claim leases and execute
independent tasks concurrently (writes serialized) → Luau pre-flight before every script
write → persist in `dm="edit"`, `replaceInstance=true` for cached ModuleScripts,
`expectedHash` from the read-back → read-back verification → Lead Developer review → QA
(`run_playtest_scenario`, `get_console_output`, `capture_viewport`, `audit_*`) → automatic
repair tasks for reproducible failures → final report that explicitly states
`verified: true|false` and cites the evidence.

A task is only `done` when its driver returned and its verification read-back succeeded.
`NoPeerError`, an approval denial, a syntax error, a hash conflict, or a console error
produce a `failed`/`blocked` task and an alert message — never a claimed success.

---

## 4. Ownership, conflicts and versions

* Each agent config declares `ownedPaths` (Studio dot-path prefixes) and `allowedTools`.
  An implementation task declares `paths`; plan generation and `ToolRunner` both reject a
  path outside the owner's namespace.
* Overlapping namespaces between two agents are detected at plan time and reported as
  conflicts; the task is not created until ownership is explicit.
* `ProjectStore.revision` increments on every successful write. A task records
  `baseRevision` at claim time; before a write `ToolRunner` re-reads the target and
  computes `expectedHash` via `fnv1a_hex`, so a concurrent edit fails with a real
  `hash mismatch` from the plugin instead of silently overwriting another agent.
* Leases come from the Bus (`claim_task`/`task_renew`/`task_release`); a crashed agent's
  work is recovered by the existing sweeper / lease expiry. Project workers can resume
  from persisted state after a bridge restart.

---

## 5. Permissions and human supervision

| Level | REST `/api/orchestrator` | MCP `agent_team` | Agent ceiling |
|---|---|---|---|
| `read` | `status`, `roster`, `plan`, `board`, `feed`, `report`, `evidence`, `approvals` (read) | same actions | read-only tools |
| `write` | `start_project`, `pause`, `resume`, `cancel`, `message`, `approve`/`deny` (delegates to gate) | same actions | declared `allowedTools` |
| `admin` | `configure` (agent team), `approvalMode="off"`, token/tunnel controls (existing) | not exposed | admin tools |

Every consequential category (`destructive`, `publish`, `external_communication`,
`project_wide`, plus `each_write` when requested) opens a signed confirmation with a
redacted change preview, before/after source comparison, and the snapshot/undo receipt.
Denials, timeouts and approvals land in the hash-chained audit and SSE stream. Undo uses
`undo_redo{action:"undo"}` or `manage_snapshots{action:"restore"}` (both existing tools).

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Existing regression suite asserts 7 prompts / 12 resources / tool count | No new resources/prompts; one new tool; tool-count assertions are dynamic (`len(TOOL_SPECS)`) |
| Read-scoped tokens bypass new mixed tool | `is_tool_call_read_only("agent_team", args)` + `INSPECTOR_MIXED_TOOLS` + REST role checks |
| Orchestrator calls bypass the MCP dispatcher's gate | `ToolRunner` enforces its own permission/ownership/approval policy and uses `ConfirmGate` directly |
| `ConfirmGate` is off by default (`--require-confirm off`) | Orchestration approvals use `gate.request()` regardless of MCP gate mode; `approvalMode="off"` requires admin + explicit flag and is audited |
| Token/secret leakage into status, previews or browser | Redactor scrubs sensitive keys and the live token value; external credentials stay in env vars; evidence PNGs are served only to authenticated `read` callers |
| Concurrent agents overwriting each other | exclusive `ownedPaths`, plan-time conflict detection, `expectedHash`, revision checks, serialized write lock, bus leases |
| ModuleScript require cache staleness | every update passes `replaceInstance=true`; `require_fresh` available for isolated tests |
| `capture_viewport`/playtest without a peer | real `NoPeerError` → task fails, report says `verified:false`; no offline render is passed off as Studio evidence |
| Plugin dashboard HTTP in client DataModel | agent summary travels in the existing `/api/poll` response (server DataModel only), no new plugin HTTP calls |
| Multiple Studios / experiences | every tool call accepts `peerId`; plan records the pinned `peerId` and all jobs are addressed to it; fleet broadcast remains read-only |
| Long-running work blocking HTTP | one daemon worker thread per project + bounded thread pool; REST returns immediately with a project id |
| Persistence size | projects/evidence under `bridge_state/` (gitignored); evidence PNGs are bounded and referenced by URL, never inlined into state |

---

## 7. Verification plan

1. `python3 -B roblox-bridge/test_bridge_mcp.py` — full existing regression suite stays green.
2. `python3 -B roblox-bridge/test_arena_team.py` — new unit + end-to-end suite against
   `MockWorld`: register roster, plan, approval, real `dm="edit"` script write verified by
   `script_read`, `replaceInstance`/`expectedHash` enforcement, playtest + console QA,
   viewport evidence file, final report, conflict/release/recovery, scoped REST denial,
   `agent_team` MCP tool, Studio poll summary.
3. Manual/live: `python server.py --require-confirm write`, open Tab 9, start an objective,
   approve the plan, watch the task board and evidence, verify in Studio's Edit DataModel,
   Ctrl+Z (one `ChangeHistoryService` recording per job) for recovery.

See `docs/MULTI_AGENT_GUIDE.md` for operator instructions.
