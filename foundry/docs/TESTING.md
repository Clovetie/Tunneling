# Testing

## Automated checks run in this workspace

```bash
cd foundry
npm ci                 # required on a fresh checkout; node_modules is not committed
npm run typecheck
npm test
npm run build

# Live HTTP/MCP protocol checks against a reachable Tunneling bridge
FOUNDRY_TEST_BRIDGE_URL=http://127.0.0.1:8077 \
FOUNDRY_TEST_BRIDGE_TOKEN=<token> npm run verify:tunneling

# Live stdio connector check against the same bridge
FOUNDRY_TEST_BRIDGE_URL=http://127.0.0.1:8077 \
FOUNDRY_TEST_BRIDGE_TOKEN=<token> npm run verify:stdio
```

Results on 2026-11-18:

- `npm run typecheck`: clean.
- `npm test`: `Test Files 8 passed (8)`, `Tests 33 passed (33)`.
- `npm run build`: compiled successfully; all ten UI routes and three API route families generated.
- `verify:tunneling` against local MockWorld: `ok:true`, protocol `2026-07-28`, `toolCount:101`, `hasAgentTeam:true`, bridge version `6.0.0`, studios `1/1`, Bus agents `9`, tasks `10`, messages `24`.
- `verify:stdio` against the same bridge: `ok:true`, protocol `2026-07-28`, `toolCount:101`, `hasAgentTeam:true`.
- `/api/bridge/preview`: live read-only health, 101 MCP schemas, 80 events, 80 audit entries, orchestrator project `proj-bb2b78e95e` and three evidence filenames.
- `/api/bridge/evidence/<filename>`: `200 image/png`.
- Browser-like JSDOM smoke of `/overview`, `/agents`, `/builder`, `/workflows`, `/projects`, `/integrations`, `/knowledge`, `/activity`, `/settings`, `/roblox`: ten navigation links, honest unconfigured and MockWorld preview notices, zero console errors.
- Supabase connector validation: migration applied to `foundry-agent-studio` (`yagbgbzbqoajdvyrnnlp`); 24 FOUNDRY tables, 19 RLS policies, 5 helper functions, pgvector extension, 2 vector indexes, 8 Realtime publication tables, memory-version trigger and two-user RLS isolation checked. See `docs/SUPABASE_VALIDATION.md`.
- Remote Vercel production alias (via `fetch_page`, cache-busted): `/api/health` returned `ok:true`, `database.ok:true` (`latencyMs:1676`), `migration:true`, `worker:false`, `providers:[]`; `/api/config` returned `mode:"ready"` with missing worker/provider warnings; `/overview` and `/roblox` show the sign-in gate; `/api/foundry/dashboard` returns `UNAUTHENTICATED`. Bash `curl` to Vercel/Supabase is blocked by sandbox egress, so no authenticated POST/CRUD could be exercised from this workspace.

The two verification scripts and the preview proxy use a real HTTP/MCP protocol run against the repository's stateful `MockWorld`. That is **not** a claim about a live Roblox Studio, a public tunnel, a production database or deployed FOUNDRY.

## Unit coverage

- Workflow graph validation, topological ordering, cycles, missing config, condition/router/transform.
- Task dependency readiness, retry policy, lease recovery and resource conflict detection.
- Tool permission enforcement, approval categories, communication permissions and secret redaction.
- Memory scope isolation and project knowledge isolation.
- AES-GCM secret round trip and tamper detection.
- Hash-chain audit computation and token-bucket rate limit.
- MCP SSE and stdio frame parsing, schema validation and SSE response parsing.
- Signed ConfirmGate `requestState`/`inputResponses` forwarding on retry, scoped bridge-token headers for Bus writes, stdio cwd traversal rejection and array-valued peer count summarization.
- Bounded JSONB serialization: ordinary values unchanged, oversized output wrapped in valid JSON, circular values handled without invalid JSON.
- Integration contract fixtures from the observed `tools/list` shape.

## Gaps / skipped surfaces

Database-backed command tests require a runtime that can reach `DATABASE_URL`. A dedicated Supabase project now has the migration applied; schema, RLS, pgvector extension/indexes, helper functions, Realtime publication and the memory-version trigger were checked through the Supabase connector. Direct Postgres egress and `curl` access from this sandbox remain blocked: the direct DB host is IPv6-only and unreachable, while the IPv4 pooler resets the PostgreSQL handshake; local Postgres is also unavailable (`psql`/`postgres`/`pg_ctl` and Python `psycopg2`/`psycopg` are absent). The Vercel deployment did connect through the production transaction pooler for `/api/health` and confirmed `public.agents`, but that is unauthenticated app-level DB health only. `npm run migrate`, `npm run seed`, `npm run worker`, authenticated FOUNDRY API CRUD, job leasing, approvals and audit-chain calls were **not** run from a local command or a real browser session. Supabase Auth browser sessions and live Realtime subscriptions were **not** exercised. Do not treat SQL-level validation rows, an in-memory test, a rendered UI card or unauthenticated health as app-level production persistence.

Real Roblox Studio, named tunnel egress, provider-backed model runs, deployed browser auth UX, persistent worker heartbeat and HANDS MODE acceptance remain manual acceptance items. A bounded Vercel deployment exists and its unauthenticated health/config endpoints are verified.
