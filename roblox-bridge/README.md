# Connecting AI to Roblox Studio

**Short answer: yes — but not through an Arena connector.** Arena has no Roblox
integration (I checked: `roblox` returns `unsupported`), and an Arena agent runs
in a cloud sandbox, not on your PC, so it can't reach into Studio by itself.

What *does* work is a bridge: something on your machine that Studio can talk to,
which an AI then drives. There are four routes, ordered by how little work they take.

| Route | Who drives Studio | Setup | Best for |
|---|---|---|---|
| **A. Studio's built-in MCP server** | Claude Code / Cursor / Codex / Copilot | ~2 min, official | Day-to-day agentic building. **Start here.** |
| **B. Studio Assistant + your own API key** | Assistant, backed by Anthropic / OpenAI / Gemini | ~1 min | Staying inside Studio's UI |
| **C. This bridge (`server.py` + `ArenaBridge.lua`)** | **Arena**, over a tunnel | ~10 min | Having *Arena* build things directly |
| **D. File handoff + Rojo** | You (paste/sync) | ~0 min | Scripts & systems, no live link |

---

## A. The official path — Studio's built-in MCP server

Since early 2026 the MCP server ships **inside Studio**; the old open-source
`Roblox/studio-rust-mcp-server` is now just a reference implementation.

1. Studio → open **Assistant** → `…` → **Manage MCP Servers**
2. Turn on **Enable Studio as MCP server**
3. Under **Quick connect**, toggle your client — Claude Code, Claude Desktop,
   Cursor, Codex CLI, Gemini CLI, VS Code, and Antigravity are supported
4. Restart the client

The agent then gets real tools against your open place: `execute_luau`,
`multi_edit` / `script_read` / `script_grep`, `search_game_tree`,
`inspect_instance`, `generate_mesh`, `generate_material`,
`generate_procedural_model`, `insert_asset`, `start_stop_play`,
`get_console_output`, `screen_capture`, `user_keyboard_input`,
`user_mouse_input`, and `character_navigation`.

That last group matters: the agent can **playtest its own work**, read the
console, and iterate — which is the thing that actually makes AI building useful
rather than a code-paste machine.

> Arena's agent is not an MCP client, so it cannot attach to this. Route A means
> running a local AI client alongside Studio.

## B. Studio Assistant with your own model

Assistant can be pointed at third-party models (Anthropic, OpenAI, Google
Gemini) with your own API key. Same Assistant UI, your choice of brain, no
external client. Doesn't involve Arena either.

## C. This bridge — putting Arena in the loop

The folder you're reading is a working implementation of exactly what you asked
about: Arena generating things directly in Studio.

```
Arena agent ──POST /api/jobs──▶ server.py (your PC) ◀──poll──  ArenaBridge.lua (Studio)
                                      ▲                              │
                                      └──────── result ◀─────────────┘
```

### Why the server runs on *your* machine

I tested hosting it in the Arena sandbox. Sandbox preview URLs are gated behind
an `e2b-traffic-access-token` header that only your browser session has, so
Roblox Studio gets a `403`. The queue therefore lives on your PC — which is also
where Roblox explicitly permits plugins to connect (`localhost` / `127.0.0.1`,
the same mechanism Rojo uses).

### Setup — one command

Download this folder to your machine, then:

```bash
python3 setup.py --tunnel
```

That single command:

1. generates a secret into `bridge.token`
2. writes `ArenaBridge.lua` into your Studio plugins folder **with the token
   already baked in** — nothing to edit (Windows `%LOCALAPPDATA%\Roblox\Plugins`,
   macOS `~/Documents/Roblox/Plugins`)
3. starts the bridge on `:8077`
4. starts `cloudflared` and prints the public URL

Then: **restart Studio** → click the **Arena Bridge** toolbar button → the
dashboard at <http://localhost:8077> flips to *Studio connected*.

Finally paste the two lines it prints to Arena:

```
URL:   https://something-random.trycloudflare.com
TOKEN: 82a46e452573e8776c5c6994
```

From that point I can POST jobs straight into your open place.

Need `cloudflared`? `winget install --id Cloudflare.cloudflared` on Windows,
`brew install cloudflared` on macOS — or run `ngrok http 8077` yourself and
paste that URL instead.

Other modes:

```bash
python3 setup.py --install-only   # just place the plugin
python3 setup.py                  # local only, no tunnel
python3 setup.py --rotate         # new token, re-installs the plugin
```

Recon — the first thing I'll run once you're connected:

```bash
python3 arena_studio.py survey          # whole place -> survey.json
python3 arena_studio.py console         # recent output
```

Self-test without Arena:

```bash
export BRIDGE_TOKEN=$(cat bridge.token)
python3 arena_studio.py ping
python3 arena_studio.py build examples/coin.json
python3 arena_studio.py script ServerScriptService CoinHandler examples/coin_handler.lua
```

### Job types the plugin understands

| Type | Payload | Does |
|---|---|---|
| `ping` | — | place name, id, Studio version |
| `survey` | `maxScriptBytes`, `includeSource` | **reads the entire place in one job** — instance census, every script, remotes, tags, GUI, settings |
| `read_scripts` | `paths[]` | batch-pull full sources |
| `console` | `limit` | recent Studio output/warnings/errors |
| `build` | `parent`, `tree[]` | creates an instance tree from JSON |
| `write_script` | `parent`, `name`, `className`, `source` | creates/overwrites a script |
| `read_script` | `path` | returns source |
| `inspect` | `path`, `depth` | JSON dump of the data model |
| `set_properties` | `path`, `properties` | edits an instance |
| `delete` | `path` | destroys an instance |
| `run_luau` | `code` | runs arbitrary Luau in Studio |

Typed values use a small tag format so JSON can express Roblox datatypes:

```json
{"Size":     {"__t": "Vector3", "v": [4, 1, 4]},
 "Color":    {"__t": "Color3",  "v": [1, 0.82, 0.25]},
 "Material": {"__t": "Enum",    "v": "Material.Metal"}}
```

Also supported: `Vector2`, `CFrame`, `UDim`, `UDim2`, `BrickColor`, `Instance`.

### Design notes

- Every mutating job runs inside a `ChangeHistoryService` recording — **one
  Ctrl+Z undoes a whole job**, and a failed job is rolled back rather than
  half-applied.
- `run_luau` prefers `loadstring`; if it's unavailable it falls back to a
  temporary `ModuleScript` + `require`, then cleans up.
- The poll is held open ~8s server-side, so jobs land in well under a second
  without hammering Roblox's HTTP rate limits.
- `mock_studio.py` speaks the same protocol, so you can test the whole loop
  with Studio closed.

### Security

This is a remote-code-execution channel into your Studio session — treat it that way.

- Always set `--token`; never run with the token disabled on a public tunnel.
- Only tunnel while you're actively using it, then kill it.
- `ArenaBridge.lua` is a **local plugin** — don't publish it to the Creator
  Store; plugins that execute fetched code get moderated.
- Work in a test place first. `run_luau` and `delete` do exactly what they say.

## D. No live link at all

Often the fastest option. I write `.lua` / `.rbxmx` / a full Rojo project into
this workspace, you `rojo serve` and sync, or just drag files in. No tunnels, no
tokens, works across sessions. For "write me a round-based combat system" this
beats a live connection, because the bottleneck is the code, not the clicking.

---

## Honest limitations

- **Arena can't attach to Studio's MCP server.** Route A needs a local MCP client.
- **The bridge is manual to start.** Server + plugin + tunnel, every session.
- **The sandbox isn't permanent.** These files persist; running processes don't —
  the bridge is meant to run on your PC anyway.
- **3D asset generation is Roblox's, not mine.** `generate_mesh` /
  `generate_material` / Cube are Studio-side features. I can generate concept
  images and all the Luau you want, but I can't hand Studio a finished mesh.

## Files

| File | What |
|---|---|
| `setup.py` | **Start here.** Installs the plugin, runs the server, opens the tunnel. |
| `server.py` | Job-queue bridge + live dashboard. No dependencies. |
| `ArenaBridge.lua` | Roblox Studio local plugin. |
| `arena_studio.py` | CLI: `ping`, `run`, `build`, `script`, `inspect`, `watch`. |
| `mock_studio.py` | Fake Studio client for testing without Studio. |
| `examples/coin.json` | Example `build` spec — a glowing coin model. |
| `examples/coin_handler.lua` | Example script pushed via `write_script`. |
