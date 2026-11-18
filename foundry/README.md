# FOUNDRY

FOUNDRY is a separate Next.js companion app in this repository: a visual multi-agent development studio designed to sit **on top of** the existing Tunneling bridge rather than replace its MCP server, Studio plugin, Agent Bus, ConfirmGate, tunnel manager or `arena_team` layer.

> **Current status (2026-11-18):** compile-clean UI scaffold with unit-tested core logic, a read-only live HTTP/MCP preview against the repository's local MockWorld bridge, and a dedicated Supabase validation project whose migration and schema-level checks have been applied. FOUNDRY is **not deployed**, **not authenticated through a real browser session**, **not DB-backed API validated from this sandbox**, **not provider/worker validated**, and **not connected to a real Roblox Studio or public tunnel**. Unconfigured surfaces are labeled as such. See `docs/SUPABASE_VALIDATION.md`, `docs/IMPLEMENTATION_PLAN.md` and `docs/ROADMAP.md` for exact verification boundaries.

## What is present

- Ten navigation routes: Overview, Agents, Agent Builder, Workflows, Projects, Integrations, Knowledge, Activity, Settings and Roblox Workspace.
- Next.js App Router API surface for Auth/Postgres-backed CRUD, jobs, workflows, approvals, tunneling adapters, health and config.
- Supabase/Postgres migration defining owner-scoped tables, pgvector functions, RLS and Realtime publication.
- Runtime MCP discovery clients for Streamable HTTP and allowlisted stdio.
- Visual React Flow builder with thirteen node types, inspector, validation and save/run wiring.
- Scoped memory/knowledge UI, encrypted secret boundary, tool policy/approvals, durable job/worker code and hash-chained audit libraries.
- Read-only `/api/bridge/preview` that uses server-side bridge credentials for a live MockWorld protocol demonstration, including evidence PNG proxying.

## Quick local preview

```bash
cd foundry
npm ci
npm run typecheck
npm test
npm run build

# Optional read-only live bridge preview. The token stays server-side.
FOUNDRY_TUNNELING_URL=http://127.0.0.1:8077 \
FOUNDRY_TUNNELING_TOKEN=<scoped bridge token> \
npm run dev
```

Open `http://localhost:3000/overview`. Without Supabase Auth and `DATABASE_URL`, the UI shows honest warnings and database-backed actions are disabled. With `FOUNDRY_TUNNELING_URL`/`TOKEN` set in `next dev`, `/roblox` shows a read-only live protocol preview and labels a MockWorld peer as simulated, not real Studio.

In production the read-only preview requires the explicit `FOUNDRY_PUBLIC_BRIDGE_PREVIEW=true` opt-in. Prefer an authenticated FOUNDRY integration for real use.

## Verification scripts

```bash
FOUNDRY_TEST_BRIDGE_URL=http://127.0.0.1:8077 \
FOUNDRY_TEST_BRIDGE_TOKEN=<token> npm run verify:tunneling

FOUNDRY_TEST_BRIDGE_URL=http://127.0.0.1:8077 \
FOUNDRY_TEST_BRIDGE_TOKEN=<token> npm run verify:stdio
```

These exercise real Streamable HTTP and stdio MCP handshakes. In this sandbox they returned protocol `2026-07-28`, 101 discovered tools and `agent_team` against MockWorld. They are not proof of real Studio or tunnel egress.

## Production prerequisites

1. Supabase project with Auth, Postgres, Realtime and applied `supabase/migrations/0001_foundry.sql`.
2. Server-side `DATABASE_URL`, `FOUNDRY_SECRET_ENCRYPTION_KEY`, provider keys and integration secrets.
3. A persistent `npm run worker` host; never execute long workflows inside a Vercel request.
4. A reachable Tunneling bridge, optionally through a named Cloudflare/ngrok tunnel configured by Tunneling itself.
5. HANDS MODE acceptance with genuine Roblox Studio and a deployed browser session.

See `docs/SUPABASE_VALIDATION.md`, `docs/DEPLOYMENT.md`, `docs/SECURITY.md`, `docs/TESTING.md` and the other documents in `docs/` before calling any phase production-complete.
