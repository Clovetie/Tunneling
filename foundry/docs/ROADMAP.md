# FOUNDRY roadmap

Status legend: **source present** means code/migration/docs exist and compile; **unit-validated** means pure tests pass; **protocol preview** means a live read-only HTTP/MCP run against local MockWorld; **operator-gated** means real credentials, database, worker, Studio or deployment are required.

| Phase | Status | Source present / validated | Remaining / operator-gated |
|---|---|---|---|
| 1 Foundation | Source present, schema-validated on Supabase | Next.js shell, ten routes, Auth gate, migration SQL, schema, agents/templates/projects/workspace screens, config banner, build/typecheck; migration applied to a dedicated Supabase project with 24 tables, 19 RLS policies, pgvector, 8 Realtime publication tables, memory-version trigger and two-user RLS isolation checked | Configure deployed Auth/DB/Realtime env; validate real browser session and DB-backed CRUD through the FOUNDRY API |
| 2 Agent execution | Source present, unit-validated | Provider adapters, encrypted secrets, tool registry, Ajv validation, durable queue code, worker entrypoint, audit/rate-limit tests | Run external worker continuously; supply provider keys; verify streaming, leases, retries and token usage against Postgres |
| 3 Multi-agent | Source present, unit-validated | Supervisor/coordinator/executor, durable task model, dependencies, communication, approvals, retry/lease/conflict unit tests | Live concurrency soak; DB-backed recovery; real supervisor model planning; end-to-end run output |
| 4 Visual builder | Source present, UI-validated | React Flow with thirteen node types, inspector, validation, save/run wiring, Realtime status overlays, intermediate results, JSDOM route smoke | Persisted save/run, live Realtime overlays, undo/redo, multi-select, copy/paste, minimap persistence |
| 5 Tunneling | Source present, protocol-preview-validated | HTTP + stdio MCP clients, REST adapters, live `tools/list` preview, Roblox workspace, fleet/status/evidence/approvals, nine presets, Bus normalization, MockWorld verify scripts | Persist discovered schemas in DB; real Studio/tunnel HANDS MODE; ConfirmGate write flow; named tunnel credentials; durable tool calls |
| 6 Advanced | Foundations, unit-validated | pgvector schema, embeddings adapter with lexical fallback label, memory/knowledge CRUD + versions + citations, templates, analytics view, isolation tests | Production embedding provider, DB-backed retrieval, file attachment storage, richer analytics, more tool adapters |

## Verified in this sandbox

- `npm run typecheck`, `npm test` (8 files / 33 tests) and `npm run build`.
- Ten route HTML responses, production route table, `/api/config`, `/api/health`.
- `verify:tunneling` and `verify:stdio` against local MockWorld: protocol `2026-07-28`, 101 tools, `agent_team` present.
- Read-only `/api/bridge/preview`: live health, 101 schemas, MockWorld peer, orchestrator project, events, audit, evidence; PNG proxy `image/png`.
- Browser-like JSDOM smoke across all ten routes: ten navigation links, honest unconfigured/preview notices and zero console errors.
- Supabase connector: created `foundry-agent-studio` (`yagbgbzbqoajdvyrnnlp`), applied `0001_foundry_schema_core` and `0001_foundry_security_realtime`, checked service health, 24 tables, 19 owner policies, 5 helper functions, pgvector extension, 2 vector indexes, 8 Realtime publication tables, memory-version snapshot and two-user RLS isolation. See `docs/SUPABASE_VALIDATION.md`.

## Explicitly not verified

- Supabase Auth browser session, real user JWT and `requireUser(request)` against GoTrue.
- Live Realtime subscriptions; pgvector semantic queries with real embeddings (the extension, functions and indexes exist but were not queried with embeddings).
- Provider-backed execution, external persistent worker heartbeat, DB-backed FOUNDRY API CRUD, job leasing, approvals or artifact persistence from the app.
- Vercel deployment, public named tunnel, real Roblox Studio plugin peer, tunnel egress, deployed browser auth UX.
- Production security review against live credentials.

## Next engineering slices

1. Add gated DB-backed integration tests that skip with a clear message when `DATABASE_URL` is absent.
2. Configure a deployed FOUNDRY runtime against the Supabase validation project and verify Auth session, DB-backed CRUD, Realtime and pgvector retrieval.
3. Run a persistent worker and execute one graph workflow and one objective workflow with a configured model provider.
4. Persist live MCP discovery into the FOUNDRY `tools` table and queue one read-only tool call.
5. Exercise ConfirmGate and FOUNDRY approvals against a reachable bridge with a real Studio peer.
6. Add Supabase Storage signed URLs for attachments and workflow versioning/diff plus builder undo/redo.
7. Add automated live acceptance for a named tunnel and genuine Roblox Studio.
8. Add scheduled maintenance jobs for memory consolidation and knowledge re-embedding.
