---
name: roblox-playtest-qa
description: Closed-loop automated verification in Roblox Studio across Edit, Play Server, and Play Client DataModels with console log gating and snapshot rollback.
---

# Roblox Closed-Loop Playtest & QA Skill

Never declare a feature finished purely because code was written. Verify every feature in live Studio runtime:

## Standard 5-Step Verification Loop
1. **Static Pre-Flight**: Run `luau_check` on every new/modified script (`ok == true`, zero undefined globals, zero unused locals).
2. **Rollback Safety Point**: Call `manage_snapshots(action="create", label="before-<feature>")` before multi-script edits.
3. **Apply Changes in `edit` DataModel**: Build instances / write scripts in `dm="edit"` (changes made during Play Solo inside `dm="server"` discard when Play stops!).
4. **Automated Live Playtest**:
   - Call `run_playtest_scenario(mode="play", warmupSeconds=2.5, serverCode=..., clientCode=...)`.
   - Assert expected server state (spawned entities, leaderstats, remotes, tags) AND client state (`PlayerGui` HUD elements, camera, local controllers).
5. **Zero-Error Console Gate**:
   - Inspect `consoleLines` returned by `run_playtest_scenario` (or `get_console_output`). Fix any error or unexpected warning immediately in `dm="edit"`, then re-run to confirm green.
