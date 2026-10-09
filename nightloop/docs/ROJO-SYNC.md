# Rojo over git: how agent changes reach Studio

Set up 2026-10-09. This is option 1 from `CONNECTION-OPTIONS.md`. It replaces copy-paste for scripts. It does not replace the bridge for live jobs (`CONNECT.md`).

## How it works

```
agent:  edit nightloop/src or nightloop/client -> precheck.sh -> commit -> push
you:    rojo_pull.py (every ~15 s) -> git fast-forward -> rojo serve -> Studio "Connect"
```

- The agent never reaches your PC. Your PC only pulls from GitHub.
- Rojo builds the place from files on disk. Git is the only transport.
- It is one-way. Nothing from Studio comes back into git.

## What is covered

| Path | Lands in Studio as |
|---|---|
| `nightloop/src/**` | `ServerScriptService.NightLoop`. Script, LocalScript or ModuleScript, by filename |
| `nightloop/client/**` | `StarterPlayer.StarterPlayerScripts` |

Not covered, on purpose:
- `nightloop/tool/FlashInput.client.lua` lives inside a Tool. Keep it by hand (`nightloop/README.md`).
- Workspace geometry (the house, spots, monster rigs). Rojo cannot sync everything live: Terrain, CSG, `MeshPart.MeshId` and `HttpService.HttpEnabled` are among the exceptions. Keep using the bridge's `build` for geometry.
- Console output, `run_luau`, playtesting. Those need the bridge (`CONNECT.md`).

## One-time setup (you)

1. **Git.** Install Git for Windows if needed. The first pull asks you to sign in to GitHub once.
2. **Rojo 7.** Install from https://rojo.space, or with Rokit: `rokit add rojo-rbx/rojo` (as in `nightloop/tools/README.md`).
3. **Rojo Studio plugin.** Run `rojo plugin install`, then restart Studio.
4. **Clone and switch branch.**
   ```
   git clone https://github.com/Clovetie/Tunneling.git
   cd Tunneling
   git checkout arena/04020e4e-tunneling
   ```
   The branch belongs to the agent's session. If the agent names a different branch, use that one.
5. **Drift check before the first connect.** Rojo makes `NightLoop` match the repo, so Studio-only edits there are lost. Run the read-only job `jobs/drift_audit.lua` the way `CONNECT.md` section 4 describes, and paste the output to the agent. The agent compares it with the repo. Do not connect Rojo until the two match, or until you have decided which copy is right and committed it.
6. **Start Rojo.** In a terminal, from the clone:
   ```
   cd nightloop
   rojo serve
   ```
   Leave it running (port 34872). In Studio, open the place. `servePlaceIds` allows only `110457957229133` ("scary monster test 3"). Open the Rojo panel and press **Connect**.
7. **Start the pull loop.** In a second terminal, from the clone root:
   ```
   python nightloop/tools/rojo_pull.py
   ```
   Use `python3` on macOS. Leave it running. Ctrl+C stops it.

## Each change

- **Agent:** edits the files, runs `bash nightloop/tools/precheck.sh` (it must end with `precheck: passed`), then commits and pushes to the session branch.
- **You:** nothing, while the pull loop runs. Within about 15 seconds it fast-forwards the clone and prints what changed. Rojo then syncs those files into Studio.
- **Before judging a module change,** restart Studio, or at least the play session. A module keeps its old code until the session reloads (the require-cache trap in `nightloop/AGENTS.md`).

## Rules

- **Do not edit synced scripts in Studio.** Rojo overwrites them. It also removes any child of `NightLoop` that has no file behind it (`$ignoreUnknownInstances` defaults to false for `$path` mappings). Make the change in the repo.
- **Do not run bridge jobs on synced scripts.** `write_script`, `jobs/set_config_flag.lua` and `jobs/set_whisperer_ids.lua` edit the same scripts, so the next sync reverts them. Commit the change instead.
- **One owner per script.** Either git or the bridge owns a script, never both.
- **`rojo_pull.py` never discards work.** It refuses to pull while tracked files have local edits, and when the branch has diverged. It prints the reason. Fix the state by hand, and it resumes on its own.
- **Other places.** Rojo refuses to sync into a place that is not in `servePlaceIds` (`nightloop/default.project.json`). To test in another place, add its ID in a commit.
- **Line endings.** `.gitattributes` keeps Luau files LF, so the files Rojo reads match the repo byte for byte.

## Files

| File | What |
|---|---|
| `nightloop/default.project.json` | Rojo mapping, plus the `servePlaceIds` allowlist |
| `nightloop/tools/rojo_pull.py` | Your PC. Fast-forward-only pull loop (Python standard library only) |
| `nightloop/tools/precheck.sh` | Agent. Pre-push gate: Rojo layout, `luau-compile`, undefined globals |
| `.gitattributes` | Keeps Luau files LF |
| `.gitignore` | Keeps `SESSION.md`, `bridge.token` and tool output out of git |

## What was tested (2026-10-09, sandbox)

- `precheck.sh` passes on the repo (23 files). On a scratch copy it fails on a syntax error and on an undefined global.
- `rojo_pull.py` ran against a local bare remote, with a "PC" clone and an "agent" clone. Covered: up to date, fast-forward, a change outside the Rojo trees, a blocked pull with local edits, a diverged branch, no upstream, a deleted file, Ctrl+C, and running outside a clone.
- **Not tested:** Rojo and Studio. Those need your PC, so the first real connect is the test. If the place shows anything unexpected after step 6, press Disconnect and tell the agent.
