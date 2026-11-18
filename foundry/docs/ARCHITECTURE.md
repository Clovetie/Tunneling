# FOUNDRY architecture

## Purpose

FOUNDRY is a secondary orchestration studio. It supplies a general-purpose, normalized agent/task/workflow layer and a visual web UI. Tunneling remains the system of record for Roblox Studio access.

```
Browser (Next.js / React Flow / Zustand)
  │ authenticated REST + Supabase Auth
  │ Supabase Realtime: events, tasks, agents, approvals, messages
  ▼
Next.js API routes (Vercel) ──► PostgreSQL / Supabase / pgvector
  │ enqueue durable job (never execute long work in request)
  ▼
FOUNDRY persistent worker (external Node process)
  │ model providers (Vercel AI SDK)
  │ normalized tool adapters
  ├──────────────► Tunneling REST /mcp /api/*  (existing bridge)
  │                  ├── MCPDispatcher / 101 discovered tools
  │                  ├── Agent Bus / ConfirmGate / Telemetry / Tunnel manager
  │                  └── ArenaBridge.lua ─► Roblox Studio
  └──────────────► allowlisted HTTP APIs / custom tools / memory / knowledge
```

## Ownership split

| Concern | Owner |
|---|---|
| Roblox Studio jobs, Luau, DataModels, viewport, playtest, assets | Tunneling |
| MCP server implementation and tool schemas | Tunneling `mcp_core.py` / `server.py` |
| Roblox specialist team, Roblox project plan, evidence, verified Studio report | Tunneling `arena_team/` |
| Studio plugin and in-Studio poll/dock widget | Tunneling `ArenaBridge.lua` |
| Bridge/tunnel auth, tunnel supervisor, scoped bridge tokens | Tunneling |
| General agent registry, arbitrary workflows, visual builder, cross-project tasks | FOUNDRY |
| FOUNDRY user accounts, projects, memories, knowledge, audit, approvals | FOUNDRY Postgres/Supabase |
| Durable FOUNDRY execution | FOUNDRY worker process |
| Live FOUNDRY UI updates | Supabase Realtime |

## Serverless vs persistent

**Vercel / Next.js request:**

- authenticate with Supabase;
- validate zod input;
- read/write small database records;
- encrypt/decrypt integration secrets server-side only;
- enqueue a `durable_jobs` row and return `202`;
- proxy short read-only Roblox status/evidence calls.

**Persistent worker:**

- polls `durable_jobs` with `FOR UPDATE SKIP LOCKED`;
- leases/recovers stale jobs;
- runs model streaming, tool calls, task dependencies, retries, approvals and supervisor review;
- emits events/messages into Postgres for Realtime;
- can host the local stdio MCP connector when deployed near the bridge.

**Local connector:**

- stdio transport spawns the existing `roblox-bridge/mcp_server.py` with an allowlisted command, never an arbitrary shell;
- Streamable HTTP transport calls `${BRIDGE_URL}/mcp` with a server-side bearer token;
- both use real MCP JSON-RPC 2.0 framing.

## Database schema summary

The migration `supabase/migrations/0001_foundry.sql` defines:

- identity/access: `profiles`, `projects`, `project_members`;
- agents: `agents`, `agent_templates`;
- workflows/runs/tasks: `workflows`, `workflow_runs`, `tasks`, `artifacts`;
- communication: `messages`, `events`;
- memory/knowledge: `memories`, `memory_versions`, `knowledge_documents`, `knowledge_chunks` with `vector(1536)`;
- tools/integrations: `tools`, `integrations`, `integration_secrets`;
- supervision/security: `approvals`, `audit_logs`, `rate_limits`;
- durable execution: `durable_jobs`, `execution_workers`;
- personal workspace: `workspace_items`.

All user-owned tables have `owner_id`, RLS policies, and explicit owner filters in server-side SQL. Private agent memory is constrained to `scope='agent' AND agent_id = <agent>`. Project memory and knowledge are constrained by `project_id`. Workflow memory is constrained by `run_id`.

## Execution interfaces

```ts
interface WorkflowGraph {
  nodes: Array<{
    id: string;
    type: FoundryNodeType;
    position: {x:number;y:number};
    data: {label:string; config:Record<string,unknown>; status?:NodeStatus};
  }>;
  edges: Array<{id:string;source:string;target:string;sourceHandle?:string;targetHandle?:string}>;
}

interface TaskRecord {
  id: string; runId: string; key: string; nodeId: string;
  kind: FoundryNodeType | 'supervisor' | 'tool';
  status: 'todo'|'running'|'waiting_approval'|'blocked'|'failed'|'completed'|'cancelled';
  dependsOn: string[]; assigneeAgentId?: string;
  input: unknown; output?: unknown; artifacts?: unknown;
  attempts: number; maxAttempts: number; timeoutMs: number;
  resourceLock?: string; leaseOwner?: string; leaseExpiresAt?: string;
}

interface DurableJob {
  id: string; type: 'run.workflow'|'run.coordinate'|'task.execute'|'tool.call'|'approval.continue'|'mcp.refresh';
  payload: Record<string, unknown>; status: 'queued'|'running'|'done'|'failed'|'cancelled';
  attempts: number; maxAttempts: number; leaseOwner?: string; leaseExpiresAt?: string;
}
```

## Supervisor pattern

1. `startRun` validates the workflow or asks the supervisor model for a JSON plan.
2. The plan is converted to durable `tasks` rows with dependencies, agent assignment, tool permissions and resource locks.
3. `run.coordinate` resolves dependency-ready tasks and enforces concurrency/execution limits.
4. `task.execute` builds a scoped context from agent/project/team/workflow memory, knowledge citations, dependency artifacts and messages.
5. It streams model output where a provider is configured; deterministic nodes execute without pretending to be an LLM.
6. Failures are retried, escalated to approval, or marked failed. Stale leases are recovered.
7. A final review produces a consolidated output and run status.

## Roblox delegation

FOUNDRY does not re-plan Roblox Studio internals. It can:

- discover current Tunneling MCP schemas;
- show health/peers/place/DataModel/fleet/tunnel/gate/audit/events;
- call read-only discovered tools with schema validation;
- send consequential tool calls through FOUNDRY approval and a durable job;
- start/observe/approve the existing `agent_team` project through MCP or `/api/orchestrator`;
- proxy authenticated evidence PNGs;
- integrate Tunneling Agent Bus messages/tasks into normalized FOUNDRY communication.

If `studio_connected` is false, every write-capable Studio action remains unavailable. No disconnected Studio is represented as online.

## Local preview boundary

When Supabase Auth/Postgres are not configured, the app uses `/api/bridge/preview` for a **read-only** live protocol demonstration. This route uses server-side `FOUNDRY_TUNNELING_URL` / `FOUNDRY_TUNNELING_TOKEN`, calls a fixed allowlist of read-only REST endpoints plus MCP `initialize`/`tools/list`, and never writes to the bridge or FOUNDRY database. It is a protocol validation surface, not a production integration or a real Studio connection. The authenticated `/api/foundry/tunneling/*` routes remain the durable, owner-scoped integration path once migration, Auth, secrets and worker are configured.
