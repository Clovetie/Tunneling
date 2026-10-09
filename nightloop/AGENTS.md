# Notes for the next agent

Read this before touching anything. It is the accumulated result of a long
build session, including the mistakes. Everything here was verified live
against a real Studio session, not assumed.

**No secrets live in this file.** The live tunnel URL and token are session
scoped and are kept in an uncommitted `SESSION.md` in the workspace root. If it
is missing or the URL 404s, ask the user for a fresh one — quick tunnels die
whenever their machine restarts the process.

**The connection itself is documented in `CONNECT.md` at the repo root** —
how to reach the bridge, what each failure mode means (including the sandbox
egress allowlist), HANDS MODE for when the sandbox cannot reach the tunnel,
and the plugin (v2.0: auto-connect on load). Read it before debugging any
connection problem; it exists so you never have to re-derive this.

**Since 2026-10-09, script changes go through Rojo over git** (`nightloop/docs/ROJO-SYNC.md`).
Commit under `nightloop/src/` or `nightloop/client/`, run `bash nightloop/tools/precheck.sh`
(it must print `precheck: passed`), then push the session branch. Do not push synced
scripts with the bridge's `write_script`: the next Rojo sync would revert them.

---

## What the connector is

An HTTP bridge that lets an agent read and write a running Roblox Studio place.

```
  agent (sandbox)                user's machine                Studio
  ───────────────                ──────────────                ──────
  curl / python  ──HTTPS──▶  cloudflared tunnel
                                    │
                                    ▼
                             server.py  :8077   ◀──polling──  ArenaBridge.lua
                             (job queue)         ──jobs──▶    (local plugin)
```

The agent POSTs a **job**; the plugin polls for it, runs it on a real Studio
thread, and posts the result back. The agent's HTTP call blocks until the
result arrives (`wait` seconds) or times out.

**It must run on the user's machine.** Do not try to host the server in the
sandbox and expose it — see "Dead ends" below.

### Starting it (user does this)

```bash
cd path/to/bridge
python setup.py --tunnel          # installs the plugin, starts server + tunnel
```

`setup.py` generates/reuses `bridge.token`, bakes the URL and token into
`ArenaBridge.lua`, installs it to the OS plugin directory, and starts
`server.py`. Flags: `--port` (default 8077), `--tunnel`, `--install-only`,
`--rotate` (new token), `--plugins-dir`.

Since plugin v2.0 the plugin **auto-connects on load** if `server.py` is
already running (local plugins execute whenever the DataModel loads; the poll
loop latches on the moment the bridge comes up) — no toolbar click after a
Studio restart. Opt out: `plugin:SetSetting("AutoConnect", false)`; the
"Arena" toolbar button still toggles. `ping` returns `pluginVersion`, so
verify which build the user has installed. Every mutating job is wrapped in
one `ChangeHistoryService` recording, so **Ctrl+Z undoes a whole job** —
including a multi-file push.

### Talking to it

```bash
export BRIDGE_URL="https://<the-tunnel>.trycloudflare.com"
export TOK="<token>"

jq -n --arg code "$(cat payload.lua)" \
   '{type:"run_luau",payload:{code:$code},note:"what this does",wait:60}' \
 | curl -s -m 90 -X POST "$BRIDGE_URL/api/jobs?token=$TOK" \
        -H 'content-type: application/json' --data-binary @- \
 | jq -r '.result.returned'
```

There is also a CLI, `bridge/arena_studio.py` (`health`, `ping`, `run`,
`runfile`, `build`, `script`, `inspect`, `read`, `reads`, `delete`, `watch`,
`survey`, `console`), which reads `BRIDGE_URL` and `BRIDGE_TOKEN`.

### Job types

| type | payload | returns |
|---|---|---|
| `run_luau` | `{code}` | `{returned}` — return a JSON string and `jq` it |
| `build` | `{parent, tree[], select}` | `{created[]}` — whole trees, one undo step |
| `write_script` | `{parent, name, className, source}` | `{bytes, path}` |
| `read_script` / `read_scripts` | `{path}` / `{paths[], maxBytes}` | source text |
| `inspect` | `{path, depth}` | instance tree |
| `set_properties` | `{path, props}` | Instance values as `{"__t":"Instance","v":"<dot path>"}` |
| `delete` | `{path}` | `done` |
| `selection`, `ping`, `survey`, `console` | — | — |

`build` is the one to use for multi-file pushes: one job, one undo step.
Nest with `children`, put `source` on each script, `replaceExisting: true` on
the root.

---

## Workflow that works

1. Write Luau to disk.
2. `tools/luau-compile --binary file.lua` — catches syntax errors before a push.
3. **`tools/check_globals.sh`** — catches undefined globals. See below; this is
   not optional.
4. Push (`build` for trees, `write_script` for one file).
5. Verify with an Edit-mode `run_luau` that returns `HttpService:JSONEncode(...)`,
   then `jq` the result. Assert on numbers, not vibes.
6. Clean up anything you spawned.

Test geometry in an isolated rig at `y = 2000+` and destroy it afterwards.

---

## Traps, all of which cost real time here

**`require` is cached per ModuleScript instance.** `write_script` edits
`.Source` in place, so Studio keeps serving the OLD module for the rest of the
session. A "fixed" module can test byte-identical to the broken one. Either
push with `build` (which makes new instances) or clone the package into a
sandbox folder and require the clone — see `tools/capture_poses.lua`.
Observed 2026-10-07: a probe returning a module's config read a key as `nil`
hours after the package was first loaded in the session, while the module
source was correct on disk and in the place — the session was serving the
pre-patch cached module. Restarting Studio (or reloading the place) is the
fast way to clear it; a `build` push that replaces the instances works too.

**Undefined globals compile clean.** Luau in non-strict mode reads an unknown
global as `nil`. Deleting a local function and missing one call site produces
code that compiles, pushes, and then throws at runtime in a signal handler
where you may never see it. This exact bug silently made a monster invisible.
`tools/check_globals.sh` runs `luau-analyze` and filters out the Roblox API
surface it has no types for, leaving only genuine undefined references.
**Run it before every push.**

**`Lighting.Technology` cannot be read from a plugin thread** — it throws
`lacking capability RobloxScript`. Assume other properties can too; pcall-guard
property reads in Studio-facing code.

**Deep `inspect` blows the output limit.** Cap depth or filter by ClassName.

**`/tmp` is wiped between tool calls.** Keep helper scripts and binaries in the
workspace (`tools/`), not `/tmp`.

**Never `pkill -f <pattern>`** where the pattern also matches the invoking
command line — the shell kills itself mid-script.

---

## Dead ends — do not retry

- **Hosting the bridge on a sandbox preview URL.** `https://<port>-<id>.e2b.app`
  returns 403 `"Sandbox is secured with traffic access token"`. Studio's
  `HttpService` cannot supply that header. The bridge runs on the user's
  machine, behind their own tunnel. Settled.
- **Rojo against the sandbox.** The Rojo plugin cannot target an HTTPS preview
  proxy.
- **`loadstring` in plugins.** Already solved in `ArenaBridge.lua`: try
  `loadstring`, else write a temp `ModuleScript` into `ServerStorage` and
  `require` it. Do not re-research.
- **winget PATH in an open shell.** After `winget install cloudflared`, an
  already-open terminal still cannot find it. Open a new one.
- Do not put placeholder paths in instructions for the user. They will be typed
  literally; `cd path\to\roblox-bridge` actually happened.

---

## Known defect in the plugin

`survey` reads `Lighting.Technology` unguarded and throws on a real plugin
thread. Either pcall it or drop that field.

---

## Security

- The token is a bearer credential. It has been visible in chat, so treat it as
  burned the moment the session ends.
- `python setup.py --rotate` issues a new one.
- Kill the tunnel when idle.
- `ArenaBridge.lua` must stay a **local** plugin — never publish it.
- `bridge.token` and `SESSION.md` are gitignored. Keep it that way.

---

## Where the game is

See `docs/NIGHTLOOP.md` for the package itself — architecture, entities, the
axis conventions that will bite you, and the per-entity tuning. The short
version: one `Script` (`Bootstrap`), everything else `ModuleScript`s, all
tuning in `Config.lua`, all place-specific names in `Config.World`, entities
self-validate and disable with a warning rather than erroring the night.

Three entities are live (Window Monster, Knocker, Crawl). `Whisperer` is coded
but needs audio asset IDs **the user will supply — do not invent them**.
`Breathless` and `TickingMan` are disabled stubs that need room volumes, which
nothing in the place currently marks.

The Window Monster **climbs**: a spot with no floor under it is reached by
scaling the wall rather than skipped (`Config.Entities.WindowMonster.Climbing`).
Its animation is the package's first *cycling* pose — `MonsterAnimator:SetCycle`
must be driven by distance climbed, not by the clock, or the hands slide out of
time with the body.
