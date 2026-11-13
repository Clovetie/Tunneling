---
name: roblox-onboarding-progression
description: Design and test Roblox first-session onboarding, tutorial objectives, milestone pacing, rejoin recovery, and fair progression across input devices.
---

# First-session onboarding and progression

Use when players cannot find the first objective, get stuck, lose tutorial progress, or when a new progression loop needs a measurable first-session flow. Combine with `game-ui-ux`, `roblox-cross-platform-accessibility`, `roblox-data-reliability`, `roblox-multiplayer-interactions`, and `roblox-playtest-qa` as needed. Loading this skill supplies guidance; it does not change the agent or place.

## Inspect and plan
1. Get permission before modifying a place or production data. Use `diagnose_connection`, `get_studio_state`, and `survey_place(includeSource=false)` to establish what is reachable; inspect relevant UI, objective, remote and persistence code before proposing changes. If there is no peer, work from supplied source/design only and report the live-test gap.
2. Map the first-session path: spawn and orientation → one understandable action → feedback → an achievable first milestone → the next voluntary objective. For each stage record entry condition, server-authoritative completion evidence, feedback, retry/skip or alternative route, and recovery after respawn or rejoin. See `references/milestone-matrix.md` for a fill-in template. For a supplied structured plan, `audit_onboarding_plan` can flag missing fields and prerequisite problems offline; it cannot verify the claims or the experience.
3. Observe the current baseline before choosing targets. Establish event definitions and denominators (eligible players, session starts, completed/abandoned milestone), and record version and test conditions. Treat examples as hypotheses, not universal timing or retention benchmarks; avoid dark patterns, artificial urgency and pressure to buy or invite friends.

## Implementation constraints
- Keep tutorial/UI state distinct from authoritative rewards. The client may display hints and request actions, but the server validates eligibility and grants value. Make repeated or delayed events safe and idempotent where appropriate; never trust a client-submitted completion or assume a single player/server.
- Use the existing persistence/session-ownership system; define defaults, migration, rejoin and failure behavior before saving milestones. Never silently replace failed loads with defaults or modify production DataStores without explicit permission. See `roblox-data-reliability` for deeper persistence safety.
- Make instructions readable and actionable across touch, keyboard/mouse and gamepad, without relying solely on color, audio, precise timing or a single input gesture. Respect localization expansion, safe areas, focus and reduced-motion alternatives. Provide recovery when the target or UI is unavailable and an accessible way to revisit or dismiss optional guidance.
- Review objectives and rewards for fairness, clarity and player agency; do not gate essential accessibility behind purchases. Minimize telemetry to what is necessary, avoid personal data and secrets, and follow the experience's privacy and retention policies.

## Verification
Use `luau_check` before authorized script writes and retain a rollback plan. Test new and returning players, repeated actions, invalid or forged completion requests, missing targets, death/respawn, disconnect/rejoin and persistence failure with isolated test data. Check server outcomes and client UI together; use controlled multi-client sessions when competition or shared state matters. Inspect console output. Run representative touch, keyboard and gamepad flows in Studio and on physical devices where available; locale and usability tests need appropriate players/devices. Offline checks, emulation and mock peers cannot establish real player comprehension, device usability, production persistence or concurrency. Report what was measured, what was not tested, and whether milestones remain blocked.
