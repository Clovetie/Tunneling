# Supabase validation

Status on **2026-11-18**: FOUNDRY has a dedicated Supabase validation project with the migration applied and schema-level checks completed through the connected Supabase app. This is **not** a Vercel deployment, **not** a real browser Auth session, and **not** proof that the FOUNDRY web API or worker has executed against Postgres.

## Project

- Name: `foundry-agent-studio`
- Project ref: `yagbgbzbqoajdvyrnnlp`
- Organization: `Clovetie's Org` (`fupfvceyuzntndfgxfsr`)
- Region: `ap-southeast-1`
- Postgres: `17`
- Project health after startup: `ACTIVE_HEALTHY` for `auth`, `db`, `pooler`, `realtime`, `rest` and `storage`. Realtime briefly reported `COMING_UP` immediately after creation, then `ACTIVE_HEALTHY`.

The project was empty before this work (`list_projects` returned no items). Generated DB/encryption values were used for validation and are **not stored in this repository**. No model provider key, bridge token, tunnel credential, Vercel project environment variable or production secret was written to the workspace.

## Applied migrations

The committed `supabase/migrations/0001_foundry.sql` was split into two idempotent chunks and applied through `create_migration`:

1. `20261118150157` — `0001_foundry_schema_core`
2. `20261118150246` — `0001_foundry_security_realtime`

`list_migrations` returned both entries. The split was necessary because the migration is larger than the connector's single-query payload limit; the SQL content is unchanged apart from whitespace and the two transaction boundaries.

## Schema checks

`execute_query` returned:

| Check | Result |
|---|---:|
| FOUNDRY tables present | 24 / 24 |
| `foundry_owner_*` RLS policies | 19 |
| Helper functions (`match_memories`, `match_knowledge_chunks`, `foundry_project_access`, `foundry_snapshot_memory`, `foundry_touch_updated_at`) | 5 |
| `vector` extension | 1 |
| IVFFLAT embedding indexes | 2 |
| Realtime publication tables (`events`, `tasks`, `workflow_runs`, `agents`, `messages`, `approvals`, `tools`, `workspace_items`) | 8 |
| `public.agents` / migration health | present |

Two SQL-level users and one synthetic workspace dataset were inserted for validation. The dataset includes a project, agent, unconfigured integration, placeholder tool, workflow, queued run, task, durable job, approval, artifact, message, event, knowledge document/chunk, memory, workspace item, audit row and a **validation-only** worker heartbeat row. The memory snapshot trigger was exercised separately: an update produced `version=2` and one row in `memory_versions`.

The integration row is deliberately `status='unconfigured'` and points at the local MockWorld URL; it is **not** a connected Studio. The worker row is deliberately marked `validation=true, external=false`; it is **not** an external persistent worker.

## RLS isolation

Using `set local role authenticated` plus `request.jwt.claim.sub` / `request.jwt.claims` in the Supabase SQL connector:

- User `11111111-1111-4111-8111-111111111111` saw `projects=1`, `agents=1`, `workflow_runs=1`, `memories=1`, `durable_jobs=0`, `audit_logs=0`.
- User `22222222-2222-4222-8222-222222222222` saw `projects=1`, `agents=0`, `workflow_runs=0`, `memories=0`, `durable_jobs=0`, `audit_logs=0`.

Each user saw only their own project; the second user saw none of the first user's workspace records. Service-only tables remained hidden from the simulated `authenticated` role. These are SQL-level RLS checks, not browser-session checks.

## What remains unverified

- Direct Postgres connectivity from this sandbox to the Supabase project: the direct DB host resolves only to IPv6 and is unreachable here; the IPv4 transaction pooler accepts TCP but the PostgreSQL handshake is reset by the sandbox egress policy. `npm run migrate`, `npm run seed`, `npm run worker`, FOUNDRY API CRUD, job leasing, approvals and audit-chain calls were therefore **not** run against this database from the app.
- Supabase Auth sign-up/sign-in, a real user JWT, browser session and `requireUser(request)` against GoTrue. The SQL-level users exist for foreign keys and RLS simulation only.
- Live Realtime subscriptions, pgvector semantic retrieval with real embeddings, provider-backed execution, external worker heartbeat, Vercel deployment, deployed browser auth UX, real Roblox Studio, public tunnel and HANDS MODE acceptance.
- Exact template catalog seeding via `npm run seed`; the validation dataset uses placeholder rows and intentionally does not claim the thirteen application templates are installed.

## Next gates

1. Obtain approval for a Vercel project/team and deployment, then set only operator-approved environment variables.
2. From the deployed app, verify `/api/health`, Auth session, DB-backed CRUD/bootstrap/discovery/approvals/durable jobs and Realtime.
3. Run `npm run seed` from a host with working Postgres egress to install the exact template catalog.
4. Add a real external worker and provider keys, then execute one graph workflow and one objective workflow.
5. Keep real Studio/tunnel work behind HANDS MODE with operator-provided values.
