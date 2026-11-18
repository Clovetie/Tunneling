---
name: roblox-data-reliability
description: Plan and review Roblox player data persistence, schema migration, session ownership, failure recovery, and safe save testing.
---

# Reliable player data

Use for saved progress, inventory and progression changes. Inspect existing data modules and any ProfileStore or DataStore conventions before editing; do not install a second persistence layer over a live one.

## Design
- Define the owned data, defaults, schema version and migration path. Validate and reconcile loaded data conservatively; preserve unknown fields where possible and never silently overwrite a failed load with defaults.
- Use one authoritative server-side owner per player session. For multi-server session ownership, use the existing tested session-locking system; do not invent a lock around a simple GetAsync/SetAsync pair. Separate transient UI state from saved state.
- Budget and throttle writes, handle failures with bounded retry/backoff, and surface unrecoverable errors. Use atomic update semantics for competing writers where needed. Do not promise exactly-once behavior from retries alone; design idempotency for purchases and grants using durable transaction identifiers where applicable.
- Plan player removal and server shutdown saves without assuming either callback is guaranteed to finish. Avoid releasing ownership or reporting success before the underlying save succeeds. Never store secrets in game scripts or expose private player data to clients.
- For destructive schema changes, plan backups, compatibility, rollout and rollback. Do not modify production DataStores or enable API access without explicit user authorization.

## Verification
Use `luau_check` before writes. Test with isolated test data and a connected Studio environment configured for DataStore access; cover new and existing records, malformed/old schemas, load failure, retry, duplicate requests, disconnect/rejoin and concurrent session attempts. Check server output and distinguish mock results from real DataStore tests. If access or multiple servers are unavailable, report the gap rather than claiming durability was verified.
