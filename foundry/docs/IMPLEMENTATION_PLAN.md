# Implementation plan

Method: `RESEARCH → ARCHITECTURE → IMPLEMENT → TEST → DEBUG → REVIEW → DOCUMENT`, repeated per phase. Priority is a working vertical slice over superficial completion.

> **Status note (2026-11-18):** this file describes the intended phases and what is present as source code. It is not a claim that all phases are production-complete. FOUNDRY is a **partial, bounded-deployed validation scaffold** with all ten navigation routes compiling, a read-only live protocol preview against a local MockWorld bridge, a dedicated Supabase validation project whose migration and schema-level checks have been applied, and a Vercel deployment whose unauthenticated `/api/health` confirms app-to-Postgres connectivity and migration presence. No real Auth session, DB-backed FOUNDRY API CRUD, provider-backed run, real Roblox Studio peer, public tunnel or persistent worker has been accepted here. `mode:"ready"` is not production acceptance.

## Phase 1 — Foundation

Planned / source present:

- Next.js App Router, TypeScript, Tailwind and shadcn-style components.
- Supabase Auth login and server-side bearer verification.
- Postgres migration with pgvector, RLS and Realtime publication.
- Application shell with ten navigation areas.
- Agent registry, templates, projects and personal workspace items.
- Config/health endpoints that honestly report missing environment variables.

Current verification: `npm run typecheck`, `npm test`, `npm run build`, all route HTML responses, `/api/config`, `/api/health` locally and on the bounded Vercel deployment, plus Supabase connector migration/schema/RLS/pgvector/Realtime-publication checks documented in `docs/SUPABASE_VALIDATION.md`. The deployed `/api/health` returned `ok:true`, `database.ok:true`, `migration:true`; direct Postgres egress from this sandbox remains blocked. Authenticated Auth/database CRUD calls have not been exercised through a real browser session.

## Phase 2 — Agent execution

Planned / source present:

- Vercel AI SDK provider adapters (OpenAI/Anthropic/Google/OpenAI-compatible).
- AES-GCM encrypted integration secrets.
- Tool registry with runtime-discovered schemas, Ajv validation, permissions and timeouts.
- Postgres-backed durable job queue and persistent worker entrypoint.
- Model streaming into `events`; token usage where available.
- Persistent task records, execution history, artifacts, audit chain and rate limits.

Current verification: unit tests for crypto, audit hash chaining, rate limits, policy, MCP framing and workflow logic. **Not verified:** provider execution, DB-backed job leasing, worker heartbeats, real approvals, real streaming or token accounting.

## Phase 3 — Multi-agent system

Planned / source present:

- Normalized supervisor, plan/task compiler, dependency resolution and parallel-ready dispatch.
- Agent-to-agent messaging, broadcasts, handoffs, artifacts and shared project context.
- Retry policies, lease recovery, conflict detection and approval gates.
- Consolidated run output and failure handling.
- Tunneling Agent Bus adapter for Roblox collaboration.

Current verification: pure unit tests for graph/task readiness, retry policy, conflict detection and workflow execution. **Not verified:** live concurrency against Postgres, DB-backed recovery, real supervisor model planning or end-to-end delegated Roblox project execution.

## Phase 4 — Visual builder

Planned / source present:

- React Flow editor with thirteen node types.
- Node inspector, connect/delete, validation, save/run and execution overlays.
- Zustand graph state and Realtime-driven status highlighting.
- Intermediate results panel and task dependency view.

Current verification: production build, graph validation tests, and browser-like JSDOM smoke of all ten routes with zero console errors. **Not verified:** persisted save/run against Supabase, Realtime status transitions from a live worker, or large-graph UX flows.

## Phase 5 — Tunneling integration

Planned / source present:

- Streamable HTTP and stdio MCP clients.
- REST health/state/peers/fleet/ops/tunnel/audit/events/confirmations/orchestrator/bus adapters.
- A read-only preview proxy that uses server-side bridge credentials and runtime `tools/list`.
- Roblox workspace with health, peers, DataModel/fleet, ConfirmGate, orchestrator, evidence, playtest/console surfaces and nine role presets.
- No fake connected state; a disconnected bridge is reported as disconnected.

Current verification: `verify:tunneling` and `verify:stdio` against the repository's local stateful **MockWorld** bridge returned protocol `2026-07-28`, `101` tools and `agent_team`. The FOUNDRY `/api/bridge/preview` route returned live health, 101 discovered schemas, 80 events, 80 audit entries and three evidence filenames; the evidence PNG proxy returned `image/png`. The UI labels the peer `MockWorld simulated Studio`, not a real Studio. **Not verified:** persisted discovery into the FOUNDRY `tools` table, real Studio, public named tunnel, real ConfirmGate approvals, durable tool calls or tunnel egress.

## Phase 6 — Advanced features

Planned / foundations present:

- pgvector schema, embeddings adapter with lexical fallback label and source citations.
- Agent/project/team/workflow memories with version history and strict isolation.
- Knowledge document ingestion and chunk retrieval.
- Custom tools, workflow templates, analytics, prompt library and saved outputs.
- Deployment docs for Vercel, external persistent worker and local connector.

Current verification: memory isolation and knowledge isolation unit tests; AES-GCM and audit-chain tests. **Not verified:** DB-backed vector similarity, actual embedding provider, Supabase Storage attachments, production analytics or memory consolidation.

## Current delivery posture

The repository contains a **compile-clean, test-passing FOUNDRY application scaffold plus a read-only live MockWorld protocol preview** and a bounded Vercel validation deployment. The migration is applied to the Supabase validation project and generated credentials are held outside the workspace. It does **not** contain a production-complete FOUNDRY system, a validated real Auth session, DB-backed CRUD, provider-backed execution, a persistent worker heartbeat, a real Roblox Studio peer, a public tunnel or HANDS MODE acceptance. Every unconfigured or disconnected surface is intended to be reported as such; docs and UI must not describe mock or unimplemented integrations as working.

## Operator-gated next steps

1. Use or promote a production Supabase project; configure Auth redirect URLs, `DATABASE_URL`, Realtime and `FOUNDRY_SECRET_ENCRYPTION_KEY`. The validation project already has the migration applied.
2. Run DB-backed acceptance tests against a signed-in session, including CRUD, bootstrap, discovery, approvals and durable jobs.
3. Configure provider keys and run `npm run worker` on a persistent host.
4. Create a real FOUNDRY integration with the bridge URL and server-side token; run `Test MCP` and `Discover tools`.
5. Repeat `verify:tunneling` / `verify:stdio` against a reachable bridge, then perform HANDS MODE acceptance with genuine Roblox Studio and an optional named tunnel.
6. Only after those checks should any phase be called production-complete.
