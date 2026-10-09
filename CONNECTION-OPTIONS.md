# Connection options: letting the agent edit the Roblox project directly

> Research only, 2026-10-09. Nothing in this file is built yet.
> Companion to `CONNECT.md`, which still describes the tunnel as the working route.
> That route is not reachable from this sandbox.

## Bottom line

- **The missing piece is the Studio side.** The agent can already add and edit files in this repo and push them to `arena/04020e4e-tunneling`. What it cannot do is get them into the open place.
- **Recommended first step: Rojo over git.** `nightloop/default.project.json` already maps this repo into Studio. The agent commits, the user's PC pulls, and Rojo syncs the files into Studio. No port needs to be reachable from the sandbox, and there is no new server or new credential beyond Git.
- **If the agent also needs to run code, read the console, or playtest:** build a small relay on the user's PC. It polls GitHub for jobs and drives Studio through Roblox's official MCP server or the existing bridge. This is more work, and it is a code-execution channel into Studio, so jobs must be signed.

## What was verified on 2026-10-09

| Check | Result |
|---|---|
| Egress to `api.github.com`, `github.com`, `registry.npmjs.org`, `pypi.org` | HTTP 200 |
| Egress to `*.trycloudflare.com`, `app.github.dev` | DNS does not resolve |
| Egress to `ngrok.io`, `ngrok.com`, `tailscale.com`, `apis.roblox.com`, `create.roblox.com`, `raw.githubusercontent.com`, `objects.githubusercontent.com`, `gist.github.com`, `github.dev`, `example.com` | TLS fails (curl 35) |
| Arena connector for Roblox (`roblox`) | `unsupported` |
| Repo `Clovetie/Tunneling` | Private. The sandbox token has push permission. `git push --dry-run` to the session branch succeeds |
| GitHub Issues list and Actions workflows, with the sandbox token | 403 "Resource not accessible by integration" |
| GitHub gists list, with the sandbox token | Succeeds, returns an empty list. Creating or updating a gist is untested |
| Rojo layout, `node nightloop/tools/validate-project.js` | 0 errors, 0 warnings |
| Place | "scary monster test 3", placeId `110457957229133` (from `nightloop/docs/ANALYSIS.md`) |

## Options

| # | Route | Agent can | Needs on the user's PC | Effort | Verdict |
|---|---|---|---|---|---|
| 1 | **Rojo over git** | Add, edit, and delete NightLoop scripts. Changes appear in Studio after the next pull | Git, Rojo CLI and Studio plugin, a clone of this repo | Low: config plus one loop script | **Do first** |
| 2 | **GitHub relay to Studio** | Run Luau, read the console, start and stop play, take screenshots, read results back | A new relay script, git push access, Studio MCP enabled or the bridge running | Medium to high | Second, only if live feedback is needed |
| 3 | Original tunnel (`setup.py --tunnel`) | Everything the bridge does, live | Nothing new | None | Only from a sandbox with open egress |

### Option 1: Rojo over git

```
agent:  edit nightloop/src/... -> static checks -> commit -> push arena/04020e4e-tunneling
user:   git pull (automatic loop) -> rojo serve (port 34872) -> Studio "Connect"
```

Verified:
- `default.project.json` maps `src/` to `ServerScriptService.NightLoop` and `client/` to `StarterPlayer.StarterPlayerScripts`.
- Rojo syncs file changes into the open place in real time, served on port 34872 by default. ([rojo.space](https://rojo.space/docs/v7/project-format/))
- The filesystem is the source of truth. Two-way sync exists but is optional. The Rojo changelog labelled it experimental. ([GitHub](https://github.com/rojo-rbx/rojo))
- Rojo maps `*.server.lua` to Script, `*.client.lua` to LocalScript, `*.lua` to ModuleScript, and `.rbxm` and `.rbxmx` files to models. ([sync details](https://rojo.space/docs/v7/sync-details/))
- The agent can run static checks before each push. `validate-project.js` passes today. The `luau-compile` and `check_globals` steps in `nightloop/AGENTS.md` are the other gates; they were not re-run today.

Costs and hazards:
1. **Studio-only edits in synced folders get overwritten or removed.** For `$path` mappings, `$ignoreUnknownInstances` defaults to `false`, so an instance with no file behind it is deleted on sync. ([project format](https://rojo.space/docs/v7/project-format/))
2. **The repo must match the place before the first connect.** Live-only edits from earlier jobs (`set_config_flag.lua`, `set_whisperer_ids.lua`) must be committed first, or Rojo will revert them. Run `jobs/drift_audit.lua` once in HANDS MODE before connecting.
3. **Require cache.** A changed module can keep running old code until Studio restarts (`nightloop/AGENTS.md`). Restart Studio after module changes.
4. **Some things never sync live.** Rojo cannot sync some property types in real time, for example Terrain and CSG data, `MeshPart.MeshId`, and `HttpService.HttpEnabled`. World geometry stays out of Rojo unless it is committed as model files. `tool/FlashInput.client.lua` is deliberately outside the synced tree (`nightloop/README.md`).
5. **Wrong-place guard.** Set `servePlaceIds` to `[110457957229133]` so Rojo refuses to sync into other places. ([project format](https://rojo.space/docs/v7/project-format/))
6. **Pulling is manual unless automated.** A loop on the user's PC running `git pull --ff-only` every ~15 seconds is enough, since Rojo watches the folder.
7. **Clone instead of ZIP.** Git will ask the user to sign in to GitHub once.

Limit: nothing comes back from Studio. There is no read-back, console, or playtest. Those need option 2.

### Option 2: GitHub relay on the user's PC

```
agent --commits job file--> GitHub (private repo) <--pull / push--> relay.py (user's PC)
                                                                        |
                         stdio JSON-RPC (Studio MCP)  or  POST 127.0.0.1:8077/api/jobs
                                                                        v
                                                                   Roblox Studio
```

Verified:
- Studio's MCP server is local-only, over stdio. Its documented tools include `execute_luau` (Edit, Client, or Server), `multi_edit`, `script_read`, `script_grep`, `search_game_tree`, `inspect_instance`, `start_stop_play`, `get_console_output`, and `screen_capture`. Roblox says to connect only clients you trust. ([Roblox docs](https://create.roblox.com/docs/studio/mcp))
- The agent is not an MCP client, so the relay on the user's PC would be the client.
- The bridge already accepts jobs on `POST /api/jobs` with a `wait` field (`roblox-bridge/server.py`), so the relay could use it instead of MCP.
- GitHub limits: 5,000 authenticated requests per hour. A conditional GET that returns 304 does not count against that. Content creation is capped at 80 per minute and 500 per hour, and writes should be spaced at least 1 second apart. ([rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api), [best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api))

Decisions it needs:
- **Mailbox.** (a) `relay/inbox` and `relay/outbox` folders on this session branch. This is the simplest option, but every job becomes a commit and shows in PR diffs. (b) A separate private repo. This is cleaner, but the user has to create it and grant access. (c) A secret gist. This makes no commits, but writes are untested.
- **Executor.** Studio MCP has richer tools but is not yet tested here. The existing bridge is verified: `CONNECT.md` records 90+ jobs served.
- **Auth.** Each job is HMAC-signed with a per-session key the user gives once and that is never committed. This is the same trust level as the bridge token today. The relay rejects unsigned jobs and any job type outside an allowlist.

Risks:
- Even with signing, this is remote execution in the user's Studio. Anyone who can write to the mailbox, or who obtains the key, can run code in the place. Keep the repo private, rotate keys each session, and keep a kill switch.
- Each round trip takes seconds (git plus polling), not milliseconds. This is an estimate; it has not been measured.
- Do not let the relay and Rojo both own the same script.

## Ruled out (verified or settled; do not retry)

| Route | Why |
|---|---|
| Tunnels: trycloudflare, ngrok, cloudflared, tailscale | Blocked at egress (verified) |
| e2b preview URL (`{port}-{id}.e2b.app`) | Returns 403 and needs the browser's traffic token (`roblox-bridge/README.md`, `nightloop/AGENTS.md`) |
| Codespaces or `github.dev` port forwarding | Blocked at egress (verified) |
| `raw.githubusercontent.com`, `objects.githubusercontent.com`, `gist.github.com` from the sandbox | Blocked (verified). Use the API or git instead |
| GitHub Actions relay | No workflows scope; the Actions API returns 403 (`CONNECT.md` section 5, re-checked today) |
| GitHub Issues as a mailbox | 403 for the sandbox token (verified) |
| Open Cloud (`apis.roblox.com`) | Blocked from the sandbox (verified). Its Luau Execution runs on a separate server and does not change the open session ([DevForum beta announcement](https://devforum.roblox.com/t/beta-open-cloud-engine-api-for-executing-luau/3172185)) |
| Arena Roblox connector | `unsupported` (verified) |
| Rojo served from the sandbox | Cannot target the HTTPS preview proxy (`nightloop/AGENTS.md`). Option 1 is different: Rojo runs on the user's PC and git is the transport |

## Security issue, regardless of route

- **`SESSION.md` is tracked in git.** Its header says it is gitignored, and `nightloop/AGENTS.md` says the same. This repo has no `.gitignore`. The file itself says the token "sits in repo history."
- Fix: rotate the bridge token (`python setup.py --rotate`), untrack `SESSION.md`, and add a `.gitignore`. This has not been changed, because it affects the user's workflow.

## Decisions needed

1. Adopt option 1 (Rojo over git)? This needs Rojo on the user's PC and a clone of this repo.
2. Build option 2 (relay)? If so, which mailbox, and which executor?
3. Rotate the token and untrack `SESSION.md`?
4. Does the user know of a sandbox with open egress? The original tunnel route works there unchanged.

## Sources

- Roblox Creator Hub, Connect to the Roblox Studio MCP server: https://create.roblox.com/docs/studio/mcp
- Rojo docs v7, Project Format: https://rojo.space/docs/v7/project-format/
- Rojo docs v7, Sync Details: https://rojo.space/docs/v7/sync-details/
- Rojo on GitHub (README, changelog): https://github.com/rojo-rbx/rojo
- GitHub Docs, Rate limits for the REST API: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- GitHub Docs, Best practices for the REST API: https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api
- Roblox DevForum, Open Cloud Engine API for Executing Luau (beta): https://devforum.roblox.com/t/beta-open-cloud-engine-api-for-executing-luau/3172185
- In repo: `CONNECT.md`, `nightloop/AGENTS.md`, `roblox-bridge/README.md`, `roblox-bridge/server.py`, `nightloop/default.project.json`, `nightloop/tools/validate-project.js`
