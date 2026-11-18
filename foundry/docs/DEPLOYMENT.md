# Deployment

> **Current status:** FOUNDRY has **not** been deployed to Vercel. A dedicated Supabase validation project exists and the migration has been applied, with schema/RLS/pgvector/Realtime-publication checks passed through the connector; see `docs/SUPABASE_VALIDATION.md`. The local app remains a compile-validated UI with a read-only MockWorld protocol preview because this sandbox cannot reach Supabase Postgres/Auth directly and no deployed environment variables have been approved. The instructions below are the operator runbook.

FOUNDRY has three runtime pieces. Do not collapse them into one serverless request.

## 1. Web app — Vercel

1. Import this repository into Vercel and set the root directory to `foundry`.
2. Configure environment variables from `.env.example`:
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   - `DATABASE_URL` or `SUPABASE_DB_URL`
   - `FOUNDRY_SECRET_ENCRYPTION_KEY`
   - model provider keys
   - optional `FOUNDRY_TUNNELING_URL` and `FOUNDRY_TUNNELING_TOKEN` for one-time bootstrap
3. Apply `supabase/migrations/0001_foundry.sql` in the Supabase SQL editor or with `npm run migrate` (already applied to the dedicated validation project; do not reapply blindly outside the idempotent chunks).
4. Enable Realtime for the publication tables listed in the migration.
5. Deploy. Build command: `npm run build`.

## 2. Persistent worker — external Node host

Run on Render, Railway, Fly.io, a small VPS, or a home machine that stays on:

```bash
cd foundry
npm ci
npm run worker
```

The worker needs the same `DATABASE_URL`, provider keys and `FOUNDRY_SECRET_ENCRYPTION_KEY`. It polls `durable_jobs` with `FOR UPDATE SKIP LOCKED`, leases/recovers jobs, and is intended to be safe for one or more processes. Never run it as a Vercel function. **No worker heartbeat has been validated against a real database yet.**

## 3. Local Roblox bridge + connector — user's machine

The existing Tunneling bridge must run where Roblox Studio is installed:

```bash
cd roblox-bridge
python3 setup.py
# optional remote access:
python3 setup.py --tunnel
```

For a public named tunnel, configure Cloudflare/ngrok in Tunneling itself. FOUNDRY reads `/api/tunnel`; it does not start or duplicate a tunnel.

If FOUNDRY is deployed remotely, use Tunneling's Streamable HTTP `/mcp` endpoint through a named tunnel. If FOUNDRY's worker runs on the same machine as the bridge, use the local stdio connector.

## Local read-only bridge preview

For a local `next dev` preview, set:

```bash
FOUNDRY_TUNNELING_URL=http://127.0.0.1:8077
FOUNDRY_TUNNELING_TOKEN=<scoped bridge token>
```

`/api/bridge/preview` then performs **read-only** bridge calls and a runtime MCP `tools/list`, and `/api/bridge/evidence/<filename>` proxies PNG/JPEG evidence. The token remains server-side. The UI explicitly labels MockWorld peers and disables mutations, discovery persistence, approvals and durable execution when Auth/Postgres are unconfigured.

In production this preview is disabled unless `FOUNDRY_PUBLIC_BRIDGE_PREVIEW=true` is set explicitly. Only enable that flag for a deliberately public, read-only status surface; it is not a substitute for an authenticated FOUNDRY integration.

## Bootstrap

After deploy, open Settings and run **Bootstrap personal workspace**, or:

```bash
npm run seed
```

Then create an integration in **Integrations** with the bridge URL and a server-side stored token, run **Test**, then **Discover tools**. These steps are operator-gated and have not been exercised against a real database here.

## Live acceptance checklist (HANDS MODE / operator-gated)

- `/api/health` from FOUNDRY reports `database`, `auth`, `realtime`, `worker` configured.
- Tunneling integration test reports `version 6.0.0`, tool count > 0, and honest `studioConnected`.
- With Studio open: `/api/health` shows a live Edit peer and `/api/studios` lists it. A MockWorld peer must not satisfy this item.
- A read-only discovered tool succeeds; a write-capable tool creates a FOUNDRY approval.
- `agent_team` status works; starting a project opens Tunneling ConfirmGate.
- Evidence PNG proxy returns `image/png`.
- Worker heartbeat updates within 30 seconds.
- Audit chain verification succeeds.
- One graph run and one objective run complete with a real provider and durable task records.
- A named tunnel is reachable from the deployed app, and deployed browser auth UX works.

Some schema-level prerequisites are verified separately through the Supabase connector, but none of the deployed live-acceptance items above has been claimed complete in this sandbox.
