# START-HERE — Arena ↔ Roblox Studio, in one prompt

**For the user:** paste the prompt below into a fresh Arena session and the
agent has everything it needs. Nothing else to explain.

**For the agent:** read this file top to bottom first (two minutes), then
`CONNECT.md` §11 and `relay/README.md` if you need the mechanics. Do not
re-derive the connection — it is solved, documented, and tested.

---

## The prompt (copy everything in the box)

```text
Connect to my Roblox Studio and get to work.

Read START-HERE.md in the repo, then follow it exactly.

  My working copy:  C:\Users\Gamef\Downloads\Tunneling-arena-8673c420-tunneling\Tunneling-arena-8673c420-tunneling
  Bridge token:     (in roblox-bridge\bridge.token — ask me to run the
                     one-liner in START-HERE.md if you cannot read it)
  Local bridge:     http://127.0.0.1:8077   (already running, my Studio is open)

Steps: start the relay, give me the ONE PowerShell line to run, verify with
health + ping + the baseline survey, then run the publish pipeline (drift ->
push dry -> review -> push --apply) and tell me what changed.
```

---

## What the agent does (exact commands)

### 1. Start the relay (sandbox side)

```bash
cd <repo root>                       # /home/user/Tunneling in a fresh Arena session
TOK=$(<the 24-hex token the user pastes>)      # = their roblox-bridge/bridge.token
python3 relay/relay_server.py --port 8787 --token "$TOK" \
        --url "https://8787-${E2B_SANDBOX_ID}.e2b.app"
```

* Port **8787** is fixed by convention, so the preview URL is stable for the
  life of the sandbox: `https://8787-<E2B_SANDBOX_ID>.e2b.app`.
* The relay token is deliberately the *same* value as the user's
  `bridge.token` — one secret for both hops. Never commit it
  (`.relay-state/` is gitignored; `SESSION.md` holds no token).
* `python3 relay/relay.py state` → `clients: []` means nobody is attached yet.

### 2. Give the user ONE line (their folder, their Python)

The user's token never needs to be typed — `poll_local.py` finds it:

```powershell
cd "C:\Users\Gamef\Downloads\Tunneling-arena-8673c420-tunneling\Tunneling-arena-8673c420-tunneling"
python .\relay\poll_local.py --url https://8787-<SANDBOX_ID>.e2b.app
```

Expect `local bridge ok - Studio CONNECTED, place "..."` then
`attached. Leave this window open; Ctrl+C to stop.`

**That is the whole connection.** If the file is missing from their ZIP (older
download), two alternatives, in order of preference:

1. the preview page's green **Save poll_local.py** button — it writes the file
   straight from the browser (no download, no token, exact name). Then:
   `python .\poll_local.py --url https://8787-<SANDBOX_ID>.e2b.app`
2. run it from the old workspace copy, which has it:

   ```powershell
   cd "C:\Users\Gamef\Downloads\workspace-01a115c6-25b3-719b-a117-f3400625cd10\roblox-bridge"
   curl.exe ...        # NO — the preview is gated; use the Save button instead
   ```

   …or, if they still have a copy of the file anywhere:

   ```powershell
   $env:BRIDGE_TOKEN = (Get-Content .\bridge.token -Raw).Trim()
   python <path>\poll_local.py --url https://8787-<SANDBOX_ID>.e2b.app
   ```

If the relay is not reachable at all, the *browser page* is the fallback
client: open the preview, paste the token, **Connect** — it does the same job
over SSE + `fetch`.

### 3. Verify (agent side, after the user is attached)

```bash
python3 relay/relay.py state          # clients: [{id: py-…}], studio.connected: true
python3 relay/relay.py ping           # place + placeId + pluginVersion "2.0"
python3 relay/relay.py drift          # live vs repo, per file (read-only)
```

`drift` should be all-identical right after a publish. Then:

```bash
python3 relay/relay.py push           # dry: what WOULD change (read-only in Studio)
python3 relay/relay.py push --apply   # publish the differences
```

Both print per-file `unchanged / replaced / created`, old → new bytes, the
`require()` result of Config/WindowMonster/Director, and the live `Config`
tuning. A non-zero exit means something did not verify — read it, do not
hand-wave.

### 4. Then do the work

Backlog, priorities and the state of play are in `SESSION.md` ("Open items")
and `nightloop/AGENTS.md`. The two things that need the user:

* **Whisperer audio IDs** — `jobs/set_whisperer_ids.lua` +
  `jobs/whisperer_check.lua` are written and idle. Ask for IDs; never invent.
* **`Config.Hud.ShowPhase`** must go back to `false` when testing ends
  (`jobs/set_config_flag.lua` flips it in place).

---

## Traps that cost real time (do not re-learn these)

| trap | fact |
|---|---|
| Sandbox egress | allowlist only (github/pypi/npm). The sandbox can **never** reach the user's machine; the relay inverts the direction. `CONNECT.md` §5. |
| Preview downloads | the preview URL is **traffic-token gated**: `curl.exe` gets 157 bytes of gate JSON instead of a file. Downloads must happen in the browser (or carry `?e2b-traffic-access-token=…`). That is why the page embeds the files and has **Save** buttons. |
| PowerShell 5.1 | it decodes `.ps1` as ANSI unless the file starts with a UTF-8 BOM → mojibake → parse errors. All our `.ps1` files are ASCII-only **and** served with a BOM. It also has no PowerShell 7 syntax (`$x = if (…) {…} else {…}`, `??`, `&&`). Prefer `poll_local.py`. |
| Filenames | `python poll_local` (no extension) and pasted markdown links (`poll_[local.py](http://local.py)`) both fail. Always give `python .\poll_local.py`. |
| Token mismatch | the relay token and `bridge.token` are the same value; a *different* value shows up as HTTP 401 **from the relay**, and a stale `bridge.token` vs a running server shows up as 401 **from the local bridge**. `CONNECT.md` §7. |
| Studio + `require` | replacing a module with a **new instance** is the only way to change live code; editing `.Source` keeps serving the cached module. `push_all.lua` does this correctly. |
| Publishing | run it in **Edit mode**, not during a playtest; one job = one Ctrl+Z. |
| Luau numbers | keep multiply products < 2^53 (FNV is decomposed on purpose). `CONNECT.md` §8. |
| Secrets | `.relay-state/relay.json` (mode 600) and `bridge.token` are gitignored. Never paste a token into a tracked file; rotate with `python setup.py --rotate` at the end of a session (and remember the plugin needs a Studio restart then). |

---

## File map (only what matters for connecting)

| path | what |
|---|---|
| `relay/relay_server.py` | the wire: queue + SSE + the browser control panel + `/jobs/*` downloads |
| `relay/poll_local.py` | the client the user runs (`python .\relay\poll_local.py --url …`); finds `bridge.token` itself |
| `relay/relay.ps1` | PowerShell client, if Python is unavailable (ASCII-only, served with a BOM) |
| `relay/relay.py` | agent CLI: `state` `ping` `run` `runfile` `survey` `drift` `push` `job` `wait` `tail` |
| `jobs/make_push_all.py` → `jobs/push_all.lua` | publishes `nightloop/**` into the place, verifying every file (bytes + FNV) |
| `roblox-bridge/` (user) | `server.py` :8077, `ArenaBridge.lua` plugin, `bridge.token` |
| `CONNECT.md` | the full connection manual; §11 is relay mode, §4 is the older hands mode |
| `SESSION.md` | live state, credentials policy, open items |
