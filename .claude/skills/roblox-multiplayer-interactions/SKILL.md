---
name: roblox-multiplayer-interactions
description: Build and test server-authoritative Roblox multiplayer interactions, proximity prompts, RemoteEvents, cooldowns, player lifecycle, and client feedback.
---

# Multiplayer interactions

Use for doors, pickups, checkpoints, prompts, shared switches and other player-triggered actions. First survey the place and inspect existing scripts, remotes, ownership and persistence; reuse the project's conventions instead of creating competing systems.

## Authority and lifecycle
- Keep shared state and consequences on the server. The client may display prompts, animations and optimistic feedback, but its requests and reported positions, rewards, target instances and timestamps are untrusted.
- For each request validate types, action, target ancestry and current state, player eligibility and server-observed proximity. Apply per-player or per-target rate limits as appropriate. Validate again before mutation; make repeated requests safe and avoid yielding between the final check and state change where possible.
- Use narrowly scoped remotes and bounded payloads; do not let clients choose arbitrary instances, code, asset IDs, currency amounts or permissions. Use server-side tables/attributes as the source of truth, not client UI.
- Decide explicitly whether state is per-player or shared, whether it resets after respawn or server restart, and how concurrent requests resolve. Clean connections and temporary instances on player departure, character replacement, object destruction and shutdown.
- Check permission, feedback and rollback/failure paths; avoid giving a reward twice when two players interact simultaneously. Keep persistent rewards under a robust save/receipt strategy rather than assuming a remote or prompt is a transaction.

## Verification
Preflight Luau with `luau_check`, write persistent scripts to `dm="edit"`, and use `replaceInstance=true` for already-required modules when needed. With Studio peers, playtest server and multiple clients where available: simultaneous activation, repeated/spam requests, malformed payloads, distant players, death/respawn and leaving. Check `get_console_output` and verify server state as well as client feedback. Report when a multi-client test was not possible; a single-client mock is not proof of multiplayer safety.
