# Connector overhaul

This is an incremental plan, not a statement that the whole connector has been audited or polished.

## First tranche

- **Discovery:** `search_tools` searches the visible catalog without a Studio connection; `tools/list` remains the authoritative schema. Inspector visibility and mixed-action restrictions still apply.
- **Less context overhead:** `skill_references` can return a 1-based inclusive line range while preserving full reads for existing clients.
- **Performance workflow:** `roblox-performance-profiling` guides baseline collection, cautious changes, and repeat measurements. `perf_audit` is a heuristic, not a profiler.
- **Documentation polish:** remove stale fixed tool counts from the guide/dashboard and document these workflows.

## Connection diagnostics tranche

- Added `diagnose_connection` and `arena_studio.py doctor`: read-only health and peer-registry checks, actionable status codes, version comparison, redacted errors and no Studio jobs. A connected registry entry is not a runtime probe.
- Tested no-peer, reachability, authentication, malformed registry, version warnings, Inspector mode and mock-connected paths. Actual Studio/plugin/device checks remain separate and require permission.

## Action-safety tranche

- Corrected mixed-action classifications for bus operations, self-test, editor tabs, UI simulator sweeps and file-saving renderers. `require_fresh` is write-capable because cloned module initialization and probe code may have side effects. Read-only sub-actions remain usable.
- Enforced the same bus action checks on GET and POST REST routes; read-only self-test requests skip the live scratch cycle. Corrected write-scoped MCP authorization, Inspector visibility annotations and local confirmation control ordering.
- Added mock and HTTP tests for scope, Inspector, confirmation gates, no-file-write behavior and safe sub-actions. This is a targeted audit, not an exhaustive security review.
- Bound HTTP task and in-band confirmation identity to authenticated token IDs, including batches; isolated task operations and lists by caller. Plugin polling now requires write scope because it registers peers and claims jobs. Read-token plugin installations need a write-scoped replacement; setup normally uses the primary token. Shared tokens and auth-disabled mode still share an identity, and write-scoped operator confirmation controls remain a separate trust boundary. Regression coverage is HTTP/mock only; real plugin compatibility has not been verified.

## Targeted edit validation

- MCP `multi_edit` validates nonempty edits, text and line forms, numeric line bounds and `replaceAll` before confirmation, task creation or job submission. Regression tests cover malformed and supported insertion forms without Studio. This is targeted validation rather than complete schema enforcement; direct REST/plugin jobs do not pass through it.
- Clarified that syntax validation occurs after the source write and that `createIfMissing` can create a script before a later plugin-side check fails. Real Studio undo, rollback and failure behavior still require an authorized test.

## Batch-write preflight

- MCP validates every batch entry before gating, tasks, parser fallback or Studio jobs, and advertises entry fields in the schema. Empty batches and malformed entries now fail rather than risking a partial batch. Direct REST/plugin jobs bypass this preflight.
- Corrected the rollback guarantee: the plugin attempts an Edit-mode ChangeHistory recording but can execute when no recording is available. The mock models rollback and cannot prove real Studio behavior. Script writes can replace a same-named non-script, which `expectedHash` does not protect. Real rollback, collisions and parser-fallback behavior require an authorized Studio test.

## Instance-operation preflight

- MCP rejects malformed or empty instance path lists, no-op property/reparent requests, multi-target renames the plugin would ignore, and invalid clone count/offset before gating, tasks or jobs. Existing clients sending these malformed forms now receive an invalid-parameters error. Direct REST/plugin jobs bypass the MCP checks.
- Corrected descriptions that implied guaranteed atomic deletion: missing targets and property errors can produce partial success, and plugin ChangeHistory recording is not guaranteed. Inspect result details and retain backups; real Studio behavior has not been verified.

## Camera defaults and validation

- MCP camera reads now dispatch an explicit `get`, matching both the advertised default and Inspector bridge job checks. HTTP read-scope and Inspector regression tests use a mocked job result; actual camera inspection needs a Studio peer.
- MCP preflight rejects malformed focus and set inputs, including invalid coordinates and FOV, before a plugin can partially apply them. Direct REST/plugin jobs remain outside this validation. Existing clients sending malformed camera writes may receive invalid-parameters errors.

## Inspector job boundary

- Replaced the Inspector bridge job denylist with an explicit read-only allowlist plus classified mixed-action reads. `batch_write`, `scatter_props`, unknown/future jobs and other writes now fail closed before queuing; these known mutations are also included in job audit classification. Non-Inspector custom job behavior is unchanged. Custom read jobs need review and allowlisting before Inspector use.
- HTTP regression tests exercise denied and allowed jobs without Studio. `mesh_capabilities` remains allowed as an existing diagnostic and attempts temporary-object cleanup; this is not proof of zero side effects or cleanup success in Studio. A read-only stdio client does not put a separately running bridge into Inspector mode.

## Local dashboard and asset boundary

- Restricted loopback token bootstrap and anonymous loopback discovery to local Host/port and same-origin browser requests. Cross-origin, forwarded and non-local Host requests no longer receive those privileges. Explicit token authentication and normal local dashboard access remain available; this does not protect against untrusted local processes or all proxy configurations.
- Fixed image/audio asset path containment to use path components rather than string prefixes, including symlink escape rejection. HTTP regression tests use local temporary fixtures, not a deployed tunnel or browser.

## Onboarding guidance and Inspector REST parity

- Added `roblox-onboarding-progression` and a reference milestone matrix for first-session objectives, player agency, authority, recovery, measurement and test planning. Discovery and reference reads are offline; actual comprehension, devices, persistence and multi-client behavior need authorized live testing.
- Blocked snapshot saving and fleet pin/label mutations through the Inspector bridge REST and direct dispatch paths while retaining reads. This does not make privileged operational controls or the host filesystem a general-purpose read-only sandbox.

## Offline onboarding plan audit

- Added read-only `audit_onboarding_plan` for supplied milestone descriptions: bounded missing-field, duplicate, prerequisite and cycle checks with deterministic issue pagination. Duplicate IDs are excluded from cycle analysis. It does not read Studio or validate whether described behavior is real or effective.
- Unit, read-scoped HTTP and Inspector stdio tests cover the offline path; actual tutorial comprehension, device accessibility, authority and persistence still require authorized live tests.

## Offline Luau checking boundary

- `luau_check` no longer silently submits `run_luau` as a fallback when local binaries are unavailable. It reports unavailable checks instead; this is a compatibility change for clients relying on the implicit Studio parser. Tool discovery no longer changes binary execute permissions. Write-capable tools retain their separate parser fallback.
- Per-file `checksPerformed` and warnings distinguish compiler-only runs from global/unused-local analysis. `ok` is not proof of runtime behavior or checks not performed. Mocked missing-toolchain tests and scoped HTTP tests confirm no Studio job; real Studio fallback behavior for write tools remains unverified.

## Large-world guidance

- Added `roblox-world-streaming` and a reference matrix for instance availability, lifecycle cleanup, server-validated traversal and multi-client/device verification. Guidance points to Roblox Creator Docs and does not change settings or simulate streaming.
- Offline skill discovery, reference search and Inspector stdio access are tested; no actual Studio streaming, physical-device or multi-client test has been performed.

## Ready-to-use source distribution

- Added a curated ZIP builder and installation guide. The archive includes connector, plugin source, skills, kit assets, licenses, self-test dependencies and per-file SHA-256 manifest, but excludes tokens, runtime state, local configuration and unrelated place dumps. The `.sha256` file detects corruption but is not a signature.
- The Linux Luau binaries are not portable; tool discovery now skips incompatible ELF/Windows executables when looking for native tools. Windows/macOS Studio, devices and live networking remain untested. A source distribution requires Python and user-side plugin installation.

## Next candidates

1. Continue auditing remaining tool schemas and action-specific classifications, including runtime side effects and REST/stdio parity; add targeted regression tests before changing compatibility-sensitive paths.
2. Extend diagnostics with optional explicit, non-destructive live probes and locally installed plugin checks where supported, with clear consent, redaction and platform-specific tests.
3. Validate onboarding and streaming guidance against real-world workflows where authorized; extend only after identifying non-duplicative gaps.
4. Assess larger skill-reference search pagination and semantic/multilingual discovery without weakening the existing bounded local-file safeguards.
5. Run live acceptance separately with the user's permission: test representative Studio versions, device inputs, multi-client behavior, latency and rollback. Offline and mock tests do not establish those results.

For every phase: retain MCP protocol compatibility, read-only enforcement, confirmation gates, peer routing, test coverage and clear rollback. Never edit a live place or production data without authorization.
