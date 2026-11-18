# Supabase validation

Status on **2026-11-18**: FOUNDRY has a dedicated Supabase validation project with the migration applied and schema-level checks completed through the connected Supabase app, plus a bounded Vercel validation deployment whose unauthenticated `/api/health` confirms app-to-Postgres connectivity and migration presence. This is **not** a production-complete deployment, **not** a real browser Auth session, **not** DB-backed FOUNDRY API CRUD, **not** provider/worker execution, and **not** proof that an authenticated FOUNDRY web API or worker has executed against Postgres.

## Project

- Name: `foundry-agent-studio`
- Project ref: `yagbgbzbqoajdvyrnnlp`
- Organization: `Clovetie's Org` (`fupfvceyuzntndfgxfsr`)
- Region: `ap-southeast-1`
- Postgres: `17.12.0.000`
- Project health after startup: `ACTIVE_HEALTHY` for `auth`, `db`, `pooler`, `realtime`, `rest` and `storage`. Realtime briefly reported `COMING_UP` immediately after creation, then `ACTIVE_HEALTHY`. A later health check still reported the service healthy but showed Realtime `db_connected:false` and `replication_connected:false`; live subscriptions remain unverified.

The project was empty before this work (`list_projects` returned no items). Generated DB/encryption values were used for validation and are **not stored in this repository**. The approved Vercel deployment holds the Supabase URL/anon key, pooled `DATABASE_URL` and encryption key in Vercel environment storage; no model provider key, bridge token, tunnel credential, worker secret, custom-domain/public-preview secret or production secret is written to the workspace.

## Vercel deployment and app-level health

- Vercel project: `foundry-agent-studio` (`prj_2PUOqEgrFG0QiZKAilUKluUoakCu`) in team `team_bEw39gpiuDYm86KVHziUXMdg`; root directory `foundry`; production alias `https://foundry-agent-studio.vercel.app`.
- Approved scope: only `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, pooled `DATABASE_URL`, `FOUNDRY_SECRET_ENCRYPTION_KEY`, `PGPOOL_MAX=3` and `NODE_OPTIONS=--dns-result-order=ipv4first`. No provider, worker, bridge, tunnel, Studio, custom-domain or public-preview secret was configured.
- The working connection uses `aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres?uselibpqcompat=true&sslmode=require`. `pg-connection-string` maps this combination to TLS with `rejectUnauthorized:false`; it avoids the earlier self-signed-certificate-chain error but is **not** full TLS certificate verification. The `aws-0-ap-southeast-1` pooler returned `tenant/user ... not found`. Raw generated values remain outside the workspace.
- The initial DB-healthy deployment `dpl_NYBhmqm3PvdeccVT8XXHpqsHJqQu` (source `af56e9a6a6dfd9867deb71b75e0a5076c560ad70`) was `READY`/`PROMOTED`. Cache-busted `/api/health` returned `ok:true`, `database.ok:true` (`latencyMs:1676`), `migration:true`, `worker:false`, `providers:[]`; `/api/config` returned `mode:"ready"` with worker/provider warnings.
- The final cleanup deployment `dpl_GYTmuumMXzLwKsoBVcYcawZTTVRu` (source `5ef6d504e0e178995ceda56d622db54d569c32b2`) is `READY`/`PROMOTED` and removed the unused temporary probe route from the build. Cache-busted `/api/health` rechecked `ok:true`, `database.ok:true` (`latencyMs:1561`), `migration:true`; `/api/config` remains `mode:"ready"` with worker/provider warnings. Unmatched paths render the sign-in gate.
- `/overview` and `/roblox` show the sign-in gate; `/api/foundry/dashboard` returns `UNAUTHENTICATED` without a session. This is unauthenticated config/health validation, not browser Auth, DB-backed CRUD, live Realtime, provider/worker execution, Studio/tunnel or HANDS MODE acceptance.

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

- Direct Postgres connectivity from this sandbox to the Supabase project: the direct DB host resolves only to IPv6 and is unreachable here; the IPv4 transaction pooler accepts TCP but the PostgreSQL handshake is reset by the sandbox egress policy. The Vercel app's unauthenticated `/api/health` did connect through the production transaction pooler and confirmed `public.agents`, but that is app-level DB health only.
- `npm run migrate`, `npm run seed`, `npm run worker`, authenticated FOUNDRY API CRUD, job leasing, approvals and audit-chain calls were **not** run against this database from a local command or a real browser session.
- Supabase Auth sign-up/sign-in, a real user JWT, browser session and `requireUser(request)` against GoTrue. The SQL-level users exist for foreign keys and RLS simulation only.
- Live Realtime subscriptions, pgvector semantic retrieval with real embeddings, provider-backed execution, external worker heartbeat, deployed browser auth UX, real Roblox Studio, public tunnel and HANDS MODE acceptance. A bounded Vercel deployment exists, but production acceptance is not claimed.
- Exact template catalog seeding via `npm run seed`; the validation dataset uses placeholder rows and intentionally does not claim the thirteen application templates are installed.

## Next gates

1. Keep the bounded Vercel deployment within the approved env scope; obtain fresh approval before adding provider, worker, bridge, tunnel, Studio, custom-domain or public-preview values.
2. `/api/health` and `/api/config` are verified; from the deployed app, validate a real Auth session, DB-backed CRUD/bootstrap/discovery/approvals/durable jobs and Realtime.
3. Run `npm run seed` from a host with working Postgres egress to install the exact template catalog.
4. Add a real external worker and provider keys, then execute one graph workflow and one objective workflow.
5. Keep real Studio/tunnel work behind HANDS MODE with operator-provided values.
