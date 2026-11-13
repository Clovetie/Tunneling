# Skill pack: gaps and continuation prompt

## Current assessment

The pack covers place building, Luau, UI, assets, audio, visual QA, monetization and security. The added skills give agents more actionable checklists for cross-platform accessibility, shared multiplayer interactions, data reliability and localization. The offline `audit_localization` checks supplied entries for missing text, duplicate keys and named/positional parameter and format mismatches; it does not inspect Studio, assess translation quality or prove layout correctness. These are guidance, not runtime guarantees; real Studio, device, multi-client and DataStore checks still require the appropriate environment.

Useful next areas to investigate against the existing pack and user needs:

1. **Onboarding and progression follow-up:** the `roblox-onboarding-progression` skill, milestone matrix and offline structural `audit_onboarding_plan` cover first-session objectives, player agency and verification. Validate the workflow with permitted real-player/device tests before claiming it improves outcomes.
2. **Performance profiling follow-up:** the profiling skill now covers baselines and verification; investigate reproducible measurement tooling and device/network regression comparisons grounded in actual profiler capabilities.
3. **Localization follow-up:** richer format-aware checks, verified import/export adapters and locale/device testing. The current audit accepts supplied JSON entries, not native translation-table exports, and checks a documented subset of Roblox parameters; other formats and runtime behavior still need verification.
4. **World streaming follow-up:** `roblox-world-streaming` and its test matrix cover instance lifecycle, server-validated traversal and test planning against Creator Docs. Validate with authorized real Studio clients and devices before claiming a fix.
5. **Discovery improvements:** semantic and multilingual discovery, skill metadata consistency and safe access to non-Markdown supporting files. Search ignores common English filler and weights distinctive terms; optional bounded reference-content search reports incomplete scans, but matching remains exact-word based, not semantic; `apply_skill` loads only `SKILL.md`, while `skill_references` lists and reads bounded Markdown under `references/`. Screenshots and other assets are not exposed.

Prioritize a small number of gaps; audit existing skills to avoid duplication. New guidance should name verification limits rather than imply a mock or screenshot proves behavior.

## Copyable continuation prompt

> Continue improving the Roblox Studio connector and agent skill pack on the current branch. First inspect the existing tools, skills, tests and documentation, including `roblox-bridge/docs/SKILLS_ROADMAP.md`. Identify the highest-impact missing capability or content without duplicating what exists. Implement a focused, useful increment: add or improve skills and, if needed, connector tools that make them discoverable and usable. Preserve existing MCP compatibility, read-only and peer-gating behavior; never claim that loading a skill permanently modifies an agent or that an offline test verifies Studio behavior. Include concrete Roblox workflows, safety constraints and verification steps, and distinguish untested assumptions from evidence. Add automated tests for new behavior and discovery, run relevant tests, update documentation, and summarize changes and remaining gaps. Do not modify a live place or production data without permission.
