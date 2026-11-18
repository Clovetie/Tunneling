# Deployment

> **Current status (2026-11-18):** a bounded Vercel validation deployment exists and its unauthenticated health/config endpoints have been checked. This is not production acceptance. The instructions below remain the operator runbook.

## Current bounded validation deployment

- Vercel project: `foundry-agent-studio` (`prj_2PUOqEgrFG0QiZKAilUKluUoakCu`) in team `team_bEw39gpiuDYm86KVHziUXMdg`; root directory `foundry`; production alias `https://foundry-agent-studio.vercel.app`.
- Approved scope: created only after the operator selected `deploy_generated`. Only `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, pooled `DATABASE_URL`, `FOUNDRY_SECRET_ENCRYPTION_KEY`, `PGPOOL_MAX=3` and `NODE_OPTIONS=--dns-result-order=ipv4first` are configured. No provider, worker, bridge, tunnel, Studio, custom-domain or public-preview secrets are configured.
- The working connection uses `aws-1-ap-southeast-1.pooler.supabase.com:6543/postgres?uselibpqcompat=true&sslmode=require`. `pg-connection-string` interprets that combination as TLS with `rejectUnauthorized:false`; it avoids the earlier self-signed-certificate-chain error but is **not** full TLS certificate verification. The `aws-0-ap-southeast-1` pooler returned `tenant/user ... not found`. Generated values are held in Vercel encrypted env storage and are not in the workspace.
- The initial DB-healthy deployment `dpl_NYBhmqm3PvdeccVT8XXHpqsHJqQu` (source `af56e9a6a6dfd9867deb71b75e0a5076c560ad70`) built and was `READY`/`PROMOTED`. Its cache-busted `/api/health` returned `ok:true`, `database.ok:true` (`latencyMs:1676`), `migration:true`, `worker:false`, `providers:[]`; `/api/config` returned `mode:"ready"` with warnings for the missing worker and model provider.
- The final cleanup deployment `dpl_GYTmuumMXzLwKsoBVcYcawZTTVRu` (source `5ef6d504e0e178995ceda56d622db54d569c32b2`) is `READY`/`PROMOTED` and removed the unused temporary probe route from the build. Its cache-busted `/api/health` rechecked `ok:true`, `database.ok:true` (`latencyMs:1561`), `migration:true`, `worker:false`, `providers:[]`; `/api/config` remains `mode:"ready"` with worker/provider warnings. Unmatched paths render the sign-in gate.
- The sign-in gate renders on `/overview` and `/roblox`, and `/api/foundry/dashboard` returns `UNAUTHENTICATED` without a session. This is unauthenticated config/health validation only: no browser Auth session, DB-backed FOUNDRY API CRUD, Realtime subscription, provider/worker execution, real Studio, tunnel or HANDS MODE acceptance is claimed.

FOUNDRY has three runtime pieces. Do not collapse them into one serverless request.

## 1. Web app — Vercel

1. Import this repository into Vercel and set the root directory to `foundry`.
2. Configure environment variables from `.env.example` (the bounded validation deployment deliberately sets only the first two public values, `DATABASE_URL`, `FOUNDRY_SECRET_ENCRYPTION_KEY` and runtime tuning; provider and bridge values remain absent):
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

- `/api/health` from FOUNDRY reports `auth`, `realtime`, `database.ok=true` and `migration=true`; `worker` remains `false` and no providers are configured.
- Tunneling integration test reports `version 6.0.0`, tool count > 0, and honest `studioConnected`.
- With Studio open: `/api/health` shows a live Edit peer and `/api/studios` lists it. A MockWorld peer must not satisfy this item.
- A read-only discovered tool succeeds; a write-capable tool creates a FOUNDRY approval.
- `agent_team` status works; starting a project opens Tunneling ConfirmGate.
- Evidence PNG proxy returns `image/png`.
- Worker heartbeat updates within 30 seconds.
- Audit chain verification succeeds.
- One graph run and one objective run complete with a real provider and durable task records.
- A named tunnel is reachable from the deployed app, and deployed browser auth UX works.

Some schema-level prerequisites are verified separately through the Supabase connector, and the unauthenticated deployed health/config checks above are verified; no HANDS MODE or authenticated live-acceptance item has been claimed complete in this sandbox.
