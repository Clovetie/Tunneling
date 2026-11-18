# Security model

## Authentication and data access

- Supabase Auth issues the browser session. API routes verify the access token with `supabase.auth.getUser(token)`.
- Every user-owned row has `owner_id`; server-side SQL always filters by the authenticated user id.
- RLS policies are included for direct Supabase clients. The server uses a service/direct DB connection and therefore also enforces owner filters explicitly.
- Project membership rows support `owner`, `editor`, `viewer`; destructive project operations require owner/editor checks.

## Secrets

- Bridge bearer tokens, provider API keys and tunnel credentials are never returned to the browser.
- Integration secrets are stored AES-256-GCM encrypted in `integration_secrets` using `FOUNDRY_SECRET_ENCRYPTION_KEY`.
- Provider keys are read from server environment variables, never from agent config.
- `/api/config` and `/api/health` return booleans and non-secret labels only.

## Tool boundaries

- Discovered MCP input schemas are validated with Ajv before `tools/call`.
- Per-agent `tool_permissions` are checked before execution.
- Consequential categories (`destructive`, `publish`, `external_communication`, `credential`, `project_wide`) require FOUNDRY human approval.
- Tunneling `ConfirmGate` remains the bridge's own approval store; FOUNDRY never self-approves a bridge request from an untrusted agent.
- HTTP tools use an explicit origin allowlist and reject loopback/private targets by default.
- Stdio connector commands are server-configured and allowlisted; `shell:false` prevents arbitrary host command execution.
- Code-execution and arbitrary database/file tools are registered as `unavailable` until an isolated sandbox is configured. FOUNDRY does not claim they work.

## Audit and rate limits

- Every mutating command writes a hash-chained `audit_logs` row.
- Rate limits are keyed by user/action/window in Postgres.
- Approval decisions, tool calls, task state transitions, model usage and errors emit `events` rows for Supabase Realtime.

## Roblox-specific

- `studio_connected:false` is shown as disconnected. Writes are blocked.
- Persistent edits are delegated to Tunneling's `dm="edit"` path; FOUNDRY does not bypass `luau_check`, `expectedHash`, `replaceInstance`, snapshots or `ConfirmGate`.
- Evidence PNGs are proxied server-side with the bridge token; filenames are path-traversal validated.
- The bridge token is redacted from errors and status payloads.

## Read-only local bridge preview

- `/api/bridge/preview` and `/api/bridge/evidence/<filename>` are available in `next dev` only when `FOUNDRY_TUNNELING_URL` and `FOUNDRY_TUNNELING_TOKEN` are set server-side.
- Production requires the explicit `FOUNDRY_PUBLIC_BRIDGE_PREVIEW=true` opt-in. Do not enable it unless a public, read-only bridge status surface is intended.
- The preview route only performs read-only REST calls plus MCP `initialize`/`tools/list`; it never forwards arbitrary browser paths, POST bodies or credentials.
- The UI labels MockWorld peers, never claims a disconnected Studio is available, and disables mutations when Auth/Postgres are unconfigured.
- Evidence filenames are path-segment validated and restricted to PNG/JPEG before a server-side bridge request.
