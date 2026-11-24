# Skill pack: gaps and continuation prompt

## Current assessment

The pack covers place building, Luau, UI, assets, audio, visual QA, monetization and security. The added skills give agents more actionable checklists for cross-platform accessibility, shared multiplayer interactions, data reliability and localization. The offline `audit_localization` checks supplied entries for missing text, duplicate keys and named/positional parameter and format mismatches; it does not inspect Studio, assess translation quality or prove layout correctness. These are guidance, not runtime guarantees; real Studio, device, multi-client and DataStore checks still require the appropriate environment.

**Landed in v6.1.0 (P5-SK1/SK2), 35 skills total.** Two skills close gaps the pack could not
reach, and both are written against the code in this repository rather than against general
Roblox lore — every tool, field and module behaviour they name is asserted against source by
`TestV61NewSkills` in `test_bridge_mcp.py`, so a rename or a removed field fails a test instead
of quietly leaving stale prose:

- `roblox-observability-and-telemetry` (+ `references/logging-patterns.md`,
  `references/budgets-and-sampling.md`) — client/server logging discipline, FPS and memory
  budget checks, crash detection, and what Studio's output window cannot tell you. It separates
  the **two halves** of observability that a confusing report usually conflates: Studio-side
  (`get_console_output`, and `get_studio_state`'s `memoryMb`/`physicsFps`, both `nil` when the
  read failed rather than zero) and bridge-side (`/api/metrics`, `studio://jobs`,
  `/api/events`, `/api/audit?verify=1`). It states that the only crash signal available is
  bridge-side inference — `peer_crash_loop`, `job.crash_loop`, `bridge_jobs_crash_loop_total`
  after the reaper's ~30 s grace — because the process that would have logged a crash is the
  one that died. It also records what `perf_audit` will *not* tell you: `triangleCount` and
  `textureMemoryEstimateMB` are always `null`, `uniqueMeshCount` counts instances rather than
  distinct meshes, and `mobileVerdict` is a threshold over a walk bounded by `depth: 20` and
  `maxChildren: 500` of the **edit** DataModel, so a `low` verdict can be an artifact of the
  child cap. Pairs with `roblox-performance-profiling`, which turns a measurement into a fix.
- `roblox-save-migration` (+ `references/migration-steps.md`,
  `references/datastoresafe-gaps.md`) — DataStore schema versioning, migration-step guarantees,
  and rollback, built on the kit's real engine in `systems/core/data`
  (`DataConfig`/`DataTemplate`/`DataMigrations`/`SaveMigrations`/`DataService`). Its central
  lesson is the kit's own comment — *migrate before Reconcile, which would fill a missing
  `dataVersion` from the template* — because reconciling first makes every old row read as
  current and no step ever runs. It documents all six statuses (`current`, `migrated`, `reset`,
  `newer`, `missingStep`, `failed`), the guarantee that a refusal leaves the save untouched, why
  rollback is the `newer` status rather than a down-migration, and why `DataService`
  deliberately does not warn on `newer`. `references/datastoresafe-gaps.md` covers the kit's
  *other* persistence module, which has no migration hook at all: `reconcile` stamps a version
  before you can read the row, `Save()` returns `true` after three failed cloud writes, and the
  memory copy is written first so `Get` reports new data while the store still holds the old
  row — which is why steps must be idempotent. Pairs with `roblox-data-reliability`, which
  covers persistence broadly and only mentions migration.

Discovery for both was checked rather than assumed: `search_skills` returns
`roblox-observability-and-telemetry` first for "telemetry", "observability", "logging", "crash
reporting" and "memory budget on mobile", and `roblox-save-migration` first for "migration",
"schema version" and every natural save-format phrasing, with `roblox-data-reliability`
consistently second so the pairing is visible instead of one skill hiding the other. The
existing top hits ("failed datastore load" → `roblox-data-reliability`, "performance profiling"
→ `roblox-performance-profiling`, "receipts and duplicate purchase rewards" →
`roblox-monetization`) are re-asserted in the same test, because adding a skill changes every
IDF in the index and can steal a query nobody thought to re-check.

One defect found while writing these: `roblox-stud-ui-design/SKILL.md` declared
`name: stud-ui-clean-roblox` in its frontmatter. Nothing parses that field — `_skill_files()`
keys skills by directory — so the mismatch was invisible to `search_skills` and `apply_skill`,
and an agent that read the frontmatter would have called `apply_skill("stud-ui-clean-roblox")`
and got `KeyError: Skill not found`. Fixed to match the directory, and the invariant is now
asserted for all 35 skills.

Useful next areas to investigate against the existing pack and user needs:

1. **Onboarding and progression follow-up:** the `roblox-onboarding-progression` skill, milestone matrix and offline structural `audit_onboarding_plan` cover first-session objectives, player agency and verification. Validate the workflow with permitted real-player/device tests before claiming it improves outcomes.
2. **Performance profiling follow-up:** the profiling skill now covers baselines and verification; investigate reproducible measurement tooling and device/network regression comparisons grounded in actual profiler capabilities.
3. **Localization follow-up:** richer format-aware checks, verified import/export adapters and locale/device testing. The current audit accepts supplied JSON entries, not native translation-table exports, and checks a documented subset of Roblox parameters; other formats and runtime behavior still need verification.
4. **World streaming follow-up:** `roblox-world-streaming` and its test matrix cover instance lifecycle, server-validated traversal and test planning against Creator Docs. Validate with authorized real Studio clients and devices before claiming a fix.
5. **`## Verification` coverage:** the section that states a skill's limits exists in only 14
   of the 35 skills — 12 pre-existing plus the two added here — so 21 skills give an agent
   instructions with no statement of what those instructions cannot prove. The requirement is
   real (the roadmap's own closing rule is "new guidance should name verification limits rather
   than imply a mock or screenshot proves behavior") but was only ever asserted for three named
   skills, which is why the gap is this wide. Add the section to the remaining 21, starting with
   the ones that name MCP tools and kit modules (`camera-and-cinematics`, `combat-and-hitboxes`,
   `multiplayer-replication-and-netguard`, `npc-ai-and-pathfinding`), then widen
   `TestV61NewSkills.test_the_new_skills_end_with_a_verification_section_that_states_limits`
   from its current named list to the whole pack and let the count assertion (14) ratchet upward.
6. **Discovery improvements:** semantic and multilingual discovery, skill metadata consistency and safe access to non-Markdown supporting files. Search ignores common English filler and weights distinctive terms; optional bounded reference-content search reports incomplete scans, but matching remains exact-word based, not semantic; `apply_skill` loads only `SKILL.md`, while `skill_references` lists and reads bounded Markdown under `references/`. Screenshots and other assets are not exposed.

Prioritize a small number of gaps; audit existing skills to avoid duplication. New guidance should name verification limits rather than imply a mock or screenshot proves behavior.

## Copyable continuation prompt

> Continue improving the Roblox Studio connector and agent skill pack on the current branch. First inspect the existing tools, skills, tests and documentation, including `roblox-bridge/docs/SKILLS_ROADMAP.md`. Identify the highest-impact missing capability or content without duplicating what exists. Implement a focused, useful increment: add or improve skills and, if needed, connector tools that make them discoverable and usable. Preserve existing MCP compatibility, read-only and peer-gating behavior; never claim that loading a skill permanently modifies an agent or that an offline test verifies Studio behavior. Include concrete Roblox workflows, safety constraints and verification steps, and distinguish untested assumptions from evidence. Add automated tests for new behavior and discovery, run relevant tests, update documentation, and summarize changes and remaining gaps. Do not modify a live place or production data without permission.
