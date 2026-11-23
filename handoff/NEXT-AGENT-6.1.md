# Work order: Tunneling 6.1.0 — security fixes, NightLoop removal, new content

**From:** the Arena review agent, branch `arena/0ad23913-tunneling` (base `992332f`)
**For:** the next coding agent working this repo
**Date:** 2026-11-23
**Status of the tree this was written against:** clean, 117 bridge tests green, `verify_licences.py` green, `check_globals.sh` green

This is a **work order, not a wish list**. Each item has an ID, the files it owns, the
evidence that it is real, the required change, the test that proves it, and a
definition of done. Work the IDs in order. If you disagree with an item, say so in
the PR description and skip it — do not silently drop it.

---

## 0. Paste-ready kickoff

> You are taking over `Clovetie/Tunneling` on the current branch. Read
> `handoff/NEXT-AGENT-6.1.md` in full before touching anything, then read
> `AGENTS.md`, `README.md`, and `roblox-bridge/docs/OPS.md`.
>
> The tree is clean at `992332f` and everything currently passes. Your baseline
> gate is: `cd roblox-bridge && python3 -B test_bridge_mcp.py` → **117 tests OK
> (~105 s)**, `python3 -B tools/verify_licences.py` → OK 4320, and
> `bash roblox-bridge/tools/check_globals.sh roblox-bridge` → `clean`. Re-run all
> three before you change anything and after every phase. If a number moves,
> you broke something — stop and fix that first.
>
> Work the phases in order: **P0 security → P1 correctness → P2 delete NightLoop
> → P3 tests/CI → P4 docs/DX → P5 new content**. Each phase must leave the
> baseline gate green. Commit per phase with the item IDs in the message. Open
> one PR at the end, titled `6.1.0: security hardening, NightLoop removal, kit
> expansion`, and paste the acceptance checklist from §10 into it with every box
> ticked or explicitly waived.
>
> Hard rules — these are not negotiable and are all violations I found in review:
> 1. **Do not add third-party Python imports.** `roblox-bridge/*.py` is stdlib-only
>    and CI enforces it. `PIL`, `reportlab`, `pygments` may appear only inside a
>    `try:` with a documented fallback, or in the build-only tools already flagged.
> 2. **Never claim Studio behavior you have not verified.** Offline/mock proof is
>    not live proof. Say "verified against MockWorld" or "operator-only, unverified".
> 3. **Do not write to a live place, tick a DataStore, or spend Robux** without
>    explicit operator permission. Anything needing a real Studio peer is
>    labelled *operator-run* in your report, not "done".
> 4. **`dm="edit"` for anything meant to persist.** Play-mode edits evaporate on Stop.
> 5. **Never commit `bridge.token`, `bridge_state/`, `tokens.json`, or `audit.jsonl`.**
>    `.gitignore` covers most of it; check `git status` before every commit.
> 6. Do not "fix" the 101 `except Exception` handlers by deleting them wholesale —
>    P1-B6 says how to narrow them safely.

---

## 1. Verified baseline (trust this, but re-verify)

| Fact | Value | How it was checked |
|---|---|---|
| Bridge tests | 117 pass, ~104 s | `python3 -B test_bridge_mcp.py` |
| Tool specs | **100** (`TOOL_SPECS`) | `len(mcp_core.TOOL_SPECS)` |
| MCP resources / templates / prompts | 12 / 3 / 7 | `mcp_core.RESOURCES`, `RESOURCE_TEMPLATES`, `PROMPTS` |
| Kit modules | **33** `.lua` in `roblox-kit/modules/` | `ls` |
| Drop-in systems | **24** dirs under `roblox-kit/systems/<group>/<system>/` | `_discover_dropin_systems()` |
| Kit prefabs | **36** JSON + 20 `.rbxmx` | `ls roblox-kit/prefabs`, `ls roblox-kit/models/*.rbxmx` |
| Asset registry | 4,320 rows, 0 missing, **4,677** files on disk | `verify_licences.py` + path diff |
| Cloud icons | 4,811 (1,894 lucide, 1,895 filled, 1,022 stud) | `_load_cloud_icons()` |
| Python / Lua / JS | 49,771 / 47,743 / 4,306 lines | `wc -l` |
| CI | **inactive** — `.github/ci.yml`, no `.github/workflows/` | `ls` |

Everything the README claims that is *wrong* is listed in P4-D2. Do not trust the
README's numbers; trust the table above.

---

## 2. P0 — security (do these first, they are exploitable today)

Each P0 item was **reproduced against a live in-process server** by the review
agent. Repro snippets are in §11 so you can confirm the bug before and after.

### P0-S1 — The confirmation gate is bypassable over REST

**Files:** `roblox-bridge/server.py` (`POST /api/jobs`, ~line 2757),
`roblox-bridge/mcp_core.py` (`_gate_pre` ~11002, `CONFIRM_GATED_TOOLS` ~208)

**Bug.** `--require-confirm` only wraps the MCP `tools/call` path. `BridgeState.enqueue_job()`
is the only shared chokepoint and it knows nothing about the gate.

**Evidence.** With `require_confirm="all"` and a **write**-scoped token:

```
POST /api/jobs {"type":"run_luau","payload":{"code":"print('pwned')"}}
→ 201 {"id":"b479d8c2","status":"queued"}
```

`run_luau` has no spec in `TOOL_MAP`; `execute_luau` and `probe` are annotated
`destructiveHint: true` but are **not** members of `CONFIRM_GATED_TOOLS`, so the
curated destructive set the gate consults is incomplete in both directions.

**Required change.**
1. Move the gate decision into `enqueue_job()` so both transports get it. Suggested
   shape: `enqueue_job(..., gate_bypass_token=None)` where an HTTP caller may pass a
   `confirmation` payload (`{requestId, stateToken}`) equivalent to MCP
   `requestState`; when `self.confirm_gate.enabled` and the job type is destructive or
   the gate mode is `all`, require a resolved, digest-matched decision — otherwise
   raise `PermissionError` and let the route return `403` with the pending `requestId`.
2. Add `run_luau`, `execute_luau`, `probe` to `CONFIRM_GATED_TOOLS`. Keep the
   existing self-approval refusal (`_local_ops`) intact.
3. `is_tool_call_read_only()` must stay the single source of truth for "is this a
   write" — route the REST path through it too, not through job-type string lists.

**Test (P3-T1).** For every transport × every destructive tool: assert
`gate_mode=all` blocks both `/mcp` `tools/call` and `POST /api/jobs` with the same
403 + `requestId`, and that approving once then retrying succeeds on both.
Done when a REST-only client cannot enqueue `run_luau` with the gate on.

### P0-S2 — `/api/plugin/download` and `/api/local-boot` hand out the owner token

**Files:** `roblox-bridge/server.py` ~2432–2457 (`/api/plugin/download`), ~2227 (`/api/local-boot`)

**Evidence** (server started with `token="owner-secret-zzz"`):

| Request | Result |
|---|---|
| `GET /api/plugin/download` — **no token at all** | `200`, body contains `local BRIDGE_TOKEN = "owner-secret-zzz"` |
| `GET /api/plugin/download` with a **read-only** token | `200`, same owner token baked in |
| `GET /api/local-boot` — **no token** | `200`, `"token": "owner-secret-zzz"` |

Both routes are `_require(qs, "read", allow_loopback=True)`, so any loopback caller
**and any read-scoped token** (demo viewer, CI token, a read-only inspector MCP you
handed to someone) can escalate to full owner. Minting works: `/api/tokens` correctly
returns `403` to the read token, so the leak defeats the entire role model.

**Required change.**
1. `/api/plugin/download`: require `admin`. If you want a convenience path for the
   local dashboard, mint a short-TTL **write**-scoped token for the download and bake
   *that* in, never `state.token`. Record the mint in the audit log.
2. `/api/local-boot`: require a real token unless `state.token` is empty. It is a
   convenience endpoint; disclosure of the owner credential is not the convenience.
3. Stop accepting `?token=` everywhere except SSE. Add `/_ticket` (POST, authenticated)
   → one-time token valid ~30 s for `GET /api/events?ticket=…`; use it from the
   dashboard `EventSource` (`server.py:5950`) and from `ArenaBridge.lua`? **No** — the
   plugin must keep its query token (it cannot set headers on `RequestAsync` reliably
   across versions); instead document that the plugin's token is a *dedicated* write
   token minted by `setup.py`, and stop the *dashboard* from putting the owner token in
   a URL.
4. Update `arena_studio.py:83,104` to send the header only (it already sends
   `X-Arena-Token`; the `?token=` append is redundant).

**Test (P3-T2).** A read token gets `403` on `/api/plugin/download`; a write token
gets a body whose baked token is **not** the owner token; `/api/local-boot` without a
token returns no `token` field. Assert no response body anywhere contains the owner
credential when the caller is not admin — write it as a loop over all routes, so new
routes are covered automatically.

### P0-S3 — Dashboard XSS: `esc()` does not escape quotes and is used inside inline JS

**Files:** `roblox-bridge/server.py:4034` (`esc`), `5665` (`studioCard` pin button),
`5000` (UI preview `onmouseenter`), plus ~24 other `onclick="fn('${esc(…)}')"` sites

```js
const esc = v => String(v==null?'':v).replace(/[&<>]/g, c => ({…}[c]));   // no ' and no "
```

`studioCard()` emits `<button onclick='pinStudio(${esc(JSON.stringify(s.peerId))})'>`.
`peerId` comes straight from `/api/poll?peer=` (`record_poll`), `place` from
`?place=`. A `'` in either escapes the attribute into executable JS. `/` is served
with **no CSP and no `X-Frame-Options`** (`grep -n "Content-Security-Policy"` → nothing),
and the token lives in `localStorage` (`4065–4072`), so the payload steals the admin token.
Net effect: **write scope → admin scope**, plus clickjacking on destructive buttons.

**Required change.**
1. `esc` must escape `& < > " '` (also `\`` is safer once you stop using inline handlers).
2. Replace inline `onclick` string interpolation with `data-*` attributes + delegated
   listeners, or attach handlers via DOM APIs. Start with the 24 sites that pass a
   value into an inline handler.
3. Serve `/` with `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'` and
   `X-Frame-Options: DENY` (CSP `frame-ancestors` is enough for modern browsers; send both).
   If the dashboard needs `'unsafe-inline'` for styles today, that is acceptable —
   script must **not** need it.
4. Move the dashboard out of Python (see P4-D1) so this is testable with a DOM harness.

**Test (P3-T3).** Unit-test `esc` against `'"<>&\``; a route-level test that
`GET /` sends both headers; and a rendering test that a peer named `x'); alert(1)//`
appears HTML-escaped and never as live script in `studioCard` output (extract the
function with a tiny Node harness or assert on the serialized markup string).

### P0-S4 — `import_image` / `render_offline` read and write anywhere on disk

**Files:** `roblox-bridge/mcp_core.py:7034–7076` (`repoAsset`, `imageUrl`),
`7060` (`urlopen`), `6650` (`saveFile`), `7074` (`saveSubdir`),
`roblox-bridge/image_pipeline.py:1069`, `696–716` (`upload_open_cloud_image` callers)

**Evidence.**

| Input | Result |
|---|---|
| `imageUrl: "file:///etc/hostname"` | accepted, `bytes: 9`, no error — `urlopen` accepts any scheme |
| `repoAsset: "../registry.json"` | read 3.7 MB from **outside** `assets/images/`, returned `uri: rbxasset://arena_assets/../registry.json` |
| `render_offline` + `saveFile: "~/.arena_write_probe.png"` | wrote the file outside the repo (3,665 bytes) |

`repoAsset` is joined as `images_dir / rel_clean` with **no** `resolve()` +
`is_relative_to()` check — the `/api/kit/*` handlers do this correctly
(`server.py:1996–2023`); this tool forgot. `saveSubdir` is character-filtered only.

**Required change.** Add one shared helper, e.g.
`mcp_core.safe_local_path(root: Path, rel: str) -> Path` that rejects absolute paths,
`..` after normalisation, and anything whose `resolve()` is not under `root.resolve()`.
Apply it to `repoAsset`, `saveSubdir`+`rel_path`, `saveFile` (renders, screenshots,
`import_rbxmx`, `export_rbxmx`, `export_place_rbxlx`), and every other path-ish tool
argument. Add `mcp_core.safe_url(url)` allowing only `http`/`https` (block
`file:`, `ftp:`, `data:` unless explicitly base64, and any scheme not in that list)
and use it at all four `urlopen` sites (`652`, `831`, `6992`, `7061`).
Route both through one place so a new tool cannot forget.

**Test (P3-T4).** Table-driven: for each tool with a path/URL argument, feed
`../../etc/passwd`, `/etc/passwd`, `C:\Windows\win.ini`, `file:///etc/hostname`,
`http://127.0.0.1:8077/api/state` (SSRF-to-self), and `\\?\GLOBALROOT\…`; assert a
clean `ValueError` and **no filesystem/network effect**. Then assert the happy paths
still work.

### P0-S5 — Default bind `0.0.0.0` + no token = open admin, and a shipped placeholder token

**Files:** `roblox-bridge/server.py:6209` (`--host` default `0.0.0.0`),
`6134` (`load_token_default` → `""`), `auth_core.py:52`,
`ArenaBridge.lua:46`, `arena_studio.py:71`, `setup.py:46`, `mock_studio.py:31`

**Evidence.** `load_token_default(None)` returns `''` when `bridge.token` is absent;
`authenticate()` then returns open admin; `/api/health` reports `authMode: "open"`;
the startup banner only says `token disabled`. The literal `arena-demo-7f3a` ships in
four files and `server.py` authenticates it happily.

**Required change.**
1. Default `--host` to `127.0.0.1`. Require an explicit `--host 0.0.0.0` (or
   `BRIDGE_HOST`) for LAN exposure, and print a `WARNING: exposed on all interfaces`
   line when it is used.
2. On first run with no token: generate one (reuse `setup.py:get_token()`), write
   `bridge.token` at `0600`, and print where it went. Only fall back to open mode if
   the operator passes `--no-auth` explicitly, and log that at `warn` + audit.
3. Make `authenticate()` refuse `PLACEHOLDER_TOKEN`. Replace the four hardcoded
   literals with a shared constant (import it; do not copy it a fifth time) and fail
   loudly in `setup.py` if a plugin would be baked with the placeholder.

**Test (P3-T5).** Creating a server with no token and no `--no-auth` yields a token;
`authenticate("arena-demo-7f3a")` returns `None`; `create_http_server()` binds
`127.0.0.1` by default.

### P0-S6 — `/api/kit/catalog` serves full module/asset content with no auth

**Files:** `roblox-bridge/server.py:2044` (catalog route), `mcp_core.py:5720` (`browse_kit_helper`)

**Evidence.** `GET /api/kit/catalog?category=modules&item=NetworkGuard` with **no
token** → `200` and 5,396 characters of `NetworkGuard.lua` source; `format=summary`
without a token returns the whole inventory. The `/api/kit/image|audio|thumbnail`
routes are unauthenticated too (they are traversal-safe, so lower severity).

**Required change.** Decide and document a policy: either kit **metadata** is public
(it is CC0 anyway) and **source content** requires `read`, or all of it is
unauthenticated. The inconsistency is the bug — `/api/kit/catalog` bypasses `_require`
entirely while every `/api/*` neighbour calls it. If you keep metadata public, gate
`item=` and `format=full` behind `read`, and add the route to `auth_core.READ_ONLY_PATHS`
so the policy is written down in one place (those two sets are currently **dead code** —
nothing reads them; either wire them into `_auth_ctx` or delete them).

**Test (P3-T6).** Public metadata 200; `item=` without a token 401; `item=` with a
read token 200. Plus a test that `READ_ONLY_PATHS`/`ADMIN_PATHS` actually influence
`_auth_ctx` (or are gone).

---

## 3. P1 — correctness and resilience

### P1-B1 — Jobs can requeue forever; the retry counter is a no-op

**File:** `roblox-bridge/server.py:438–455` (reaper), `1172` (`complete_job`)

The orphan path increments `job["attempts"]` but never compares it to `maxRetries`,
so a payload that reliably kills the Studio peer (a `run_luau` that hard-crashes the
place) is requeued on every sweep, indefinitely. Separately, line 1172 reads
`job["attempts"] = int(job.get("attempts") or 0)` — looks like an increment, is not —
so `attempts` and `retries` drift and any monitoring built on `attempts` lies.

**Change:** cap orphan requeues (`attempts >= max(2, maxRetries + 2)` →
`status: "error"`, `error: "peer_crash_loop after N attempts"`, audit `job.crash_loop`),
and make the transient branch either increment `attempts` or stop touching it.
**Test:** loop a fake peer that claims and dies 5×; assert the job ends `error` with
the crash-loop reason and exactly `maxRetries + 2` claims.

### P1-B2 — `manage_snapshots` restore mangles paths and has no partial-failure report

**File:** `roblox-bridge/mcp_core.py:4200–4220`

`entry["path"].split(".")` breaks any instance name containing a dot (`v1.2`,
`Part 1.5`, `UI.Shell`) — restore writes to the wrong parent or invents a name. Each
script is a separate 30 s job in a bare loop with no error collection, so a 200-script
restore takes ~an hour and a mid-way failure leaves the place half-restored with no
record of which half.

**Change:** capture `parent` (and `className`) at snapshot time in
`create_snapshot_helper` and use it on restore; keep the dot-split only as a legacy
fallback for old snapshots. Collect `[{path, ok, error}]`, return a summary, accept
`stopOnError` (default true) and `dryRun`. Report `restoredCount`/`failedCount`.
**Test:** snapshot a tree containing `A.B` and `X.Part 1.5`, restore, assert exact
names and parents; inject a failure on item 3 of 5 with `stopOnError=true` → 2 restored,
3 reported, nothing after.

### P1-B3 — `import_image` crashes with `KeyError: 'relPath'` *after* writing files

**File:** `roblox-bridge/image_pipeline.py:1155`, called from `mcp_core.py:7145`

```python
f'relPath = "{item["relPath"]}" }},'    # KeyError for the standalone-* map
```

Reproduced twice (name collision, and `repoAsset: "../registry.json"`). In both cases
the image was **already written** and `asset-lock.json` **already mutated** before the
traceback, so the tool reports failure after a partial apply. The committed
`asset-lock.json` still carries `"evil": {…}` from an earlier such run.

**Change:** `item.get("relPath")` with a skip+bucket for pathless rows; make the
manifest update atomic (build the text, compare, then one `os.replace`); order the
write so the manifest is updated only after the image is committed; and add
`update_repo_asset_manifest(..., check=True)` that returns drift without writing, so CI
can assert `asset-lock.json` and `Assets.lua` match the tree.
**Test:** manifest update on a tree with a pathless row (no crash, row preserved);
`check=True` is idempotent and non-mutating; a crash mid-update leaves the old manifest
byte-identical.

### P1-B4 — `fleet.json` is written non-atomically; snapshot code already does it right

**File:** `roblox-bridge/server.py:548` (`_save_fleet`) vs `1273` (`_persist_snapshot`)

`write_text` on the live path can leave a truncated file that `_load_fleet` silently
ignores, resetting pins and labels with no diagnostic.

**Change:** copy the `_persist_snapshot` pattern (`.tmp` + `os.replace`) and log a
warning (once) when a load fails instead of `except OSError: pass`.
**Test:** write a truncated `fleet.json`, start the server, assert a warning event is
emitted and defaults are used; assert a crash between write and replace cannot corrupt
the file.

### P1-B5 — Chunked / `Expect: 100-continue` bodies silently become empty

**File:** `roblox-bridge/server.py:1816` (`_read_body_raw`)

Returns `b""` when `Content-Length` is missing, so a chunked request (`Transfer-Encoding:
chunked`) or an `Expect: 100-continue` client is treated as an empty body: routes see
`{}`, and depending on the route you get a confusing 400 or a silent no-op. The
`32 MB` cap is also per-request only, with no cap on concurrent request threads.

**Change:** return an explicit `411 Length Required` (or `400`) with a clear message for
chunked bodies rather than `b""`; answer `Expect: 100-continue` with a `100` only when
the length is under the cap, else `413` before the body is read. Add a bounded worker
pool or a semaphore around the handler (see P1-B6) so a burst cannot spawn unbounded
threads (`ThreadingHTTPServer` + `daemon_threads=True`, no concurrency limit today).
**Test:** POST with `Transfer-Encoding: chunked` → clear 4xx, never a silent success;
`Expect: 100-continue` over the cap → `413` with no body read.

### P1-B6 — 101 broad `except Exception` handlers, no lint gate, four monolith functions

**Files:** `roblox-bridge/mcp_core.py` (67), `server.py` (9), `mock_studio.py` (4),
`mcp_server.py` (4), `arena_studio.py` (3), `confirm_gate.py` (3),
`image_pipeline.py` (3), `setup.py` (3), `viewport_renderer.py` (2), others (1 each)

There is no `pyproject.toml`, `setup.cfg`, `ruff.toml`, `.pre-commit-config.yaml`, or
`tox.ini` anywhere in the repo, so nothing stops new broad catches. The four worst
functions: `server.py:dashboard_html` (3,064 lines), `server.py:make_http_handler`
(1,425), `mcp_core.py:execute_tool` (889), `mock_studio.py:execute` (889).

**Change:** add `pyproject.toml` with `ruff` configured for `E,F,B,BLE` and a per-file
`# noqa` budget. Do **not** delete the existing catches: for each one, either narrow the
tuple (`except (OSError, ValueError)`) or keep `except Exception` with a comment saying
what it protects and why the caller cannot see it. Anything that swallows an error on a
path where the user expects success becomes a `warnings[]` entry or a logged `warn`
event. Split `make_http_handler` into `routes/` modules **after** P0 lands (P0 touches
those routes; a refactor first makes the diff unreadable).
**Test:** `ruff check` in CI (P3-C2); a `does_not_swallow` test per narrowed handler is
not required — the lint gate plus the P3-T1..T6 policy tests are the coverage.

---

## 4. P2 — delete NightLoop (the operator has declared it dead weight)

Scope: **`nightloop/` entirely**, plus its in-repo copies and every reference. Total
**111 tracked files** (82 under `nightloop/`, 29 under `roblox-bridge/nightloop/` +
`roblox-bridge/patches/`). ~27.5k lines in `nightloop/` alone, of which 12,985 Python
is a *third* divergent copy of the bridge (v3.4.0 vs 6.0.0).

**Do not delete:** `roblox-kit/systems/mechanics/horror-director/`, the horror prefabs
(`roblox-kit/prefabs/horror_*.json`, `ui_horror_sanity_vitals_hud.json`), or
`roblox-kit/assets` — those are kit content, not NightLoop.

### P2-N1 — Remove the directories
`git rm -r nightloop roblox-bridge/nightloop roblox-bridge/patches`

### P2-N2 — Reference map (every hit found by `grep -rn nightloop`, minus `nightloop/`)

| File / line | What it says | Action |
|---|---|---|
| `.gitignore:6,9,10,14` | `nightloop/bridge/{bridge.token,snapshots,*,bridge_state}` | delete those 4 lines |
| `roblox-bridge/mcp_core.py:298` | tool-binary search path includes `nightloop/.tools/bin` | drop that entry |
| `roblox-bridge/mcp_core.py:1658,1663,4683,4911` | `drift_and_sync` examples use `nightloop/src` | change examples to `src` / `roblox-kit/modules` |
| `roblox-bridge/mcp_core.py:6451` + `server.py:3371` | `showcase:horror_nightloop` | **keep it working** — it is built from hardcoded geometry + kit horror prefabs, not from `nightloop/`. Rename the display label to "Horror Cabin, FuseBox, SafeRoom & Generator"; optionally add `showcase:horror_cabin` as the new key and keep `horror_nightloop` as a deprecated alias |
| `roblox-bridge/test_bridge_mcp.py:25` | docstring says "on real `nightloop/src`" | fix the docstring (the actual test uses `tempfile` — verified, so no test breaks) |
| `roblox-bridge/test_bridge_mcp.py:4265` | asserts `showcase:horror_nightloop` renders | keep passing; if you rename, assert the alias too |
| `roblox-bridge/arena_studio.py:18–20`, `roblox-bridge/ab.ps1:11–13` | CLI examples use `../nightloop/src` | change to `../roblox-kit/modules` or `./src` |
| `roblox-bridge/image_pipeline.py:38` | comment mentions `nightloop/bridge/` | drop |
| `roblox-bridge/CHANGELOG.md:46–48` | NightLoop expansion entry | **keep** — history is history |
| `roblox-bridge/tools/render_posesheet.py:113–114` | writes `nightloop/monster-posesheet.svg` | delete the script (its only purpose was NightLoop) or repoint it at `roblox-kit`; prefer delete |
| `jobs/baseline_survey.lua`, `climb_check.lua`, `drift_audit.lua`, `hash_diagnostic.lua`, `night_watch.lua`, `preview_climb.lua`, `push_climb.lua`, `set_config_flag.lua`, `set_whisperer_ids.lua`, `whisperer_check.lua`, `repo_hashes_2026-10-07.txt`, `make_push.py` | 12 NightLoop-specific operator jobs (+ a generated artifact) | delete all of them |
| `README.md:41–43,52` | NightLoop row + `nightloop/` layout row | delete the row, fix the counts (P4-D2) |
| `CONNECT.md:7,289,375,428–430` | "keep `nightloop/bridge/` in sync", file map, historical v3.0 note referring to the mirror | remove §11's mirror references, drop the sync warning in §7, drop the file-map row |
| `SESSION.md` | whole file is a stale session journal with machine paths | see P4-D5 |
| `.claude/skills/mesh-first-3d-building/references/mesh-catalog-and-kitbashing.md:8` | "Horror cabins, cemeteries, NightLoop yards" | reword to "horror cabins, cemeteries, overgrown yards" |

**Leave `nightloop` out of the docs' "verification" claims entirely.** Do **not** wire
`nightloop/tools/audit.sh` into CI (it is being deleted); the equivalent check is
`bash roblox-bridge/tools/check_globals.sh <dir>`, which already exists.

### P2-N3 — Deletion acceptance
```bash
grep -rn "nightloop" --include=*.py --include=*.lua --include=*.js --include=*.md \
  --include=*.json --include=*.sh --include=*.ps1 . | grep -v "^./.git/" | grep -v CHANGELOG.md
# expect: no output
git ls-files | grep -c nightloop        # expect 0
cd roblox-bridge && python3 -B test_bridge_mcp.py   # expect 117 OK, still ~105 s
bash tools/check_globals.sh .            # expect clean
cd .. && python3 -B tools/verify_licences.py        # expect OK 4320
```
Also: the repo loses 4,306 JS lines and its only Node dependency tree — delete
`nightloop/tools/package-lock.json` concerns with it, and confirm `.gitignore` no longer
mentions `node_modules`/`reports` for a directory that is gone.

---

## 5. P3 — tests and CI

Baseline is 117 tests that all pass and cover 99 of 100 tools by name (only
`open_cloud_action` is never named). The gaps below are exactly the ones that let P0
through.

### P3-T1 — Transport parity harness
Assert that every policy decision (`is_tool_call_read_only`, gate participation,
scope requirement) yields the *same* answer on `/mcp` and `POST /api/jobs`. Drive it
from `TOOL_SPECS` × `is_tool_call_read_only` × a small arg matrix. This single test
would have caught S1 and S6.

### P3-T2 — Secret-disclosure sweep
Loop every GET/POST route with (a) no token, (b) read token, (c) write token, (d)
admin, and assert the owner token string never appears in a body/header for a/b/c.
Table-driven so future routes are covered.

### P3-T3 — Path and URL safety table
See P0-S4. One parametrised test over every tool that takes a path/URL. This is the
highest-value new test in the suite.

### P3-T4 — Manifest idempotency and non-mutation
`update_repo_asset_manifest` twice → byte-identical output; `check=True` never writes;
a pathless row does not crash.

### P3-T5 — Renderer golden images
`viewport_renderer.render_scene_png` and `image_pipeline` have **no direct tests**
(`rbxmx_pipeline.py` has zero references in the test file). Add 3–4 fixed scenes →
hash the PNG (the codebase already has FNV-1a; use it) so a rasteriser regression is a
test failure, not a visual surprise. Add an `rbxmx` round-trip
(`parse_rbxmx_to_tree` → rebuild → parse) with a fixture.

### P3-T6 — Kit integrity
`registry.json` ↔ files on disk ↔ `asset-lock.json` ↔ `modules/Assets.lua` must agree
(4,320 registered, 4,677 on disk of which 357 are docs/contact sheets — assert the
*image/audio/model* subset exactly). This makes P1-B3's drift detectable in CI.

### P3-T7 — Jobs/tools smoke
`jobs/*.py` (1,004 lines) and `tools/*.py` have zero tests. After P2 deletes the
NightLoop jobs, the survivors are `live_acceptance.py`, `measure_tunnel_latency.py`,
`drill_studio_kill.py` — assert each has `--help` working, exits non-zero with a clear
message when there is no peer (the existing test
`test_operator_scripts_fail_loudly_without_a_studio_peer` already covers one; extend it),
and that no script reaches the network on import.

### P3-C1 — Activate CI
`git mv .github/ci.yml .github/workflows/ci.yml && git push`. If the GitHub App still
lacks `workflows` permission, leave the file staged and say so in the PR — do not fake it.

### P3-C2 — Make CI actually green and broader
1. **The docs version guard fails today.** Ran verbatim, it exits 1 with
   `CONNECT.md: carries no version claim any more (guard is blind)` because CONNECT
   phrases it as "the bridge and `ArenaBridge.lua` … are now `6.0.0`" and none of the
   three `CLAIMS` regexes match. Add a `v?\d+\.\d+\.\d+` fallback pattern (keep the
   existing ones) and re-run before pushing, or the first CI run is red for the wrong reason.
2. Add a job that runs `bash roblox-bridge/tools/check_globals.sh` over
   `roblox-bridge`, `roblox-kit/modules`, `roblox-kit/systems` (the current job only
   scans `roblox-bridge`).
3. Add `ruff check` (P1-B6) and the kit-integrity check (T6).
4. Add a matrix entry for Python 3.11 (currently 3.10/3.12/3.13; 3.11 is the most common
   local version and the reviewer ran 3.11).
5. Keep the stdlib-only check as-is — it is good, and it caught nothing only because
   the code is genuinely clean. Do not weaken it.

### P3-C3 — Leak guards
Add a `check_secrets.sh`-style job that fails if `bridge.token`, `bridge_state/`,
`tokens.json`, `audit.jsonl`, or a `-----BEGIN … PRIVATE KEY-----` block is ever tracked.
The repo has already had a token in history (see `SESSION.md`'s own warning).

---

## 6. P4 — documentation and DX

### P4-D1 — Move the dashboard out of Python
`server.py:dashboard_html()` is 3,064 lines of embedded HTML/CSS/JS. Extract to
`roblox-bridge/static/{index.html,dashboard.css,dashboard.js}` with Python only
substituting `__BRIDGE_VERSION__` and the boot payload. This is a prerequisite for
doing P0-S3 properly (CSP without `'unsafe-inline'` script, plus a DOM test harness)
and cuts `server.py` by ~half.

### P4-D2 — Fix every wrong number in the docs (verified values in §1)
| Claim | Location | Correct |
|---|---|---|
| "90 Roblox Studio Tools" | `README.md:41` | **100** |
| "95 MCP tools" | `CONNECT.md:5` | **100** |
| "28 Luau `ModuleScript`s" | `README.md:42,52` | **33** |
| "22 drop-in game systems" | `README.md:42,52` | **24** |
| "30 prefabs" | `README.md:42` | **36** JSON prefabs (+20 `.rbxmx`) |
| "4,320 files" vs "4,677 on disk" | `README.md:3,42,52` | 4,320 **registered**; 4,677 files, the extra 357 being docs/contact sheets |
| "90 MCP Tools" | `CHANGELOG.md:5` (historical heading) | leave — history |
| `server.py:5216,5326` hardcode `4811` as a fallback | dashboard | keep the fallback but derive it from the API; do not let a second literal drift |
| `README.md:41` counts resources/prompts as 12/7 | correct (12 resources, 3 templates, 7 prompts) | no change |

Then **generate** instead of hand-maintaining: emit `docs/FACTS.md` from
`len(TOOL_SPECS)`, `glob('modules/*.lua')`, `_discover_dropin_systems()`, and
`registry.json`, and add a CI check (P3-C2) that fails when the README disagrees with it.

### P4-D3 — No root LICENSE
The repo ships `THIRD_PARTY_LICENSES.md` and `nightloop/LICENSE` (about to be deleted)
but **no `LICENSE`** for its own ~50k Python + ~47k Lua lines. That contradicts the
"commercial-safe" pitch for anyone adopting the bridge or kit. Add a root `LICENSE`
(ask the operator which: MIT is the least surprising for this kind of dev tool) and
link it from `README.md` and `THIRD_PARTY_LICENSES.md`.

### P4-D4 — `.gitignore` gaps
Add: `nightloop/tools/reports/` is moot after P2, but add `node_modules/` (only if any
JS returns), `roblox-kit/assets/images/**/__probe*`, and keep the existing
`bridge.token`/`bridge_state` entries. Verify `git status` is clean after running
`bash roblox-bridge/tools/check_globals.sh .` — it currently is, but the NightLoop
`audit.sh` used to create an untracked `reports/` (that goes away with P2).

### P4-D5 — `SESSION.md` does not belong in the tree
It calls itself "Not committed… gitignored" and then admits otherwise; it contains
`C:\Users\Gamef\…` paths, a named tunnel hostname, and its own instruction to rotate a
token that is in history. Either delete it or move the durable content into
`roblox-bridge/docs/OPS.md` §12 (troubleshooting) with paths sanitised, and
`git rm --cached SESSION.md` + add to `.gitignore`. Tell the operator the token needs
`python setup.py --rotate` regardless — you cannot un-leak history without a rewrite,
so state that plainly rather than implying the deletion fixes it.

### P4-D6 — Developer ergonomics
1. Add `pyproject.toml` (P1-B6) with `[tool.ruff]` and a `[tool.pytest]`-free,
   unittest-based `test` entry so `python3 -m unittest` works from `roblox-bridge/`.
2. Add a `Makefile` (or `justfile`) with `make test`, `make lint`, `make verify`,
   `make docs-check` — the CI commands should be the same strings as the local ones.
3. The 105 s test runtime is mostly nine `sleep`-ish E2E paths; add `--fast` that skips
   the live-HTTP E2E classes so the inner loop is <15 s. Keep the full suite the default
   for CI.
4. `arena_studio.py` and `ab.ps1` have no `--help` examples for the new surfaces
   (confirmations, snapshots, kit). Add a `arena_studio.py guide` subcommand that prints
   the 10 most useful commands, generated from the parser.

---

## 7. P5 — new content to build (this is the "add stuff" part)

Design rules for everything in this section: follow `roblox-kit/systems/CONVENTIONS.md`
(every system gets `README.md` + `shared/<X>Config.lua` + `server/` + `client/` +
`tests/*.spec.lua`), keep the Kit neutral (no game-specific nouns), and make every new
MCP surface appear in `TOOL_SPECS`/`RESOURCES`/`PROMPTS` **and** get a test.

### P5-K1 — New kit system: `mechanics/checkpoint-respawn`
Files: `roblox-kit/systems/mechanics/checkpoint-respawn/{README.md,shared/CheckpointConfig.lua,server/CheckpointService.server.lua,client/CheckpointUI.client.lua,tests/Checkpoint.spec.lua}`
Touch-to-claim checkpoints, ordered progression, respawn at the highest claimed one,
per-player state, server-authoritative (`NetGuard`/`NetworkGuard` validation), optional
party sharing off by default. **Why:** 24 systems and none of them handles death/respawn,
the single most-copied mechanic in any Roblox game. Verify with a `tests/` spec on the
pure ordering function.

### P5-K2 — New kit system: `ui/settings-menu`
Files: `roblox-kit/systems/ui/settings-menu/{README.md,shared/SettingsConfig.lua,client/SettingsMenu.client.lua,server/SettingsPersist.server.lua,tests/Settings.spec.lua}`
Volume/music/SFX sliders, camera sensitivity, invert-Y, reduce-motion, colour-blind
palette, keybind remap, and a "restore defaults". Persists per player through
`DataServiceAPI` (never a raw DataStore). **Why:** accessibility is a listed skill
(`roblox-cross-platform-accessibility`) but there is no shipped implementation, and
`settings` is the obvious missing UI surface. Wire the reduce-motion flag into
`SpringUI`/`StudMotion` so it actually does something.

### P5-K3 — New kit system: `liveops/leaderboard`
Files: `roblox-kit/systems/liveops/leaderboard/{README.md,shared/LeaderboardConfig.lua,server/LeaderboardService.server.lua,client/LeaderboardUI.client.lua,tests/Leaderboard.spec.lua}`
OrderedDataStore-backed global board + per-server board, throttled writes via
`DataStoreSafe`, pagination, opt-out. **Why:** `core/data` and `DataStoreSafe` exist but
no system demonstrates the ordered-store + throttle pattern the skills doc warns about.
Must degrade gracefully when `DataStore` is unavailable in Studio — return a warning, not
an error (that's the convention in this repo).

### P5-K4 — New module: `roblox-kit/modules/FeatureFlags.lua`
Attribute/`ReplicatedStorage`-driven flags with a default table, server override,
`Flags.get(name)`, `Flags.onChanged(name, fn)`, and a `--!strict` header.
**Why:** there is no way to stage a system without editing code; `set_config_flag.lua`
in `jobs/` was the ad-hoc version of this and it is being deleted in P2. Drop a
`shared/FlagsConfig.lua`-style default table convention into the README.

### P5-K5 — New module: `roblox-kit/modules/Throttle.lua`
Token bucket with `Throttle.new(rate, burst)`, `:attempt()` → `(ok, retryAfter)`,
`:wait()` that yields. **Why:** `NetGuard.lua` (79 lines) and `NetworkGuard.lua` (167)
both hand-roll buckets — two implementations of the same primitive, neither shared.
Extract it, refactor both to use it, keep their public APIs unchanged, and note the
consolidation in the README. (If you judge refactoring both too risky, ship
`Throttle` and mark the duplication as follow-up — but say which you did.)

### P5-M1 — New MCP tools (3, all offline-testable)
Add to `TOOL_SPECS` in `roblox-bridge/mcp_core.py` with `_ann(...)` annotations, a
`execute_tool` branch, and a helper:

1. **`audit_kit_integrity`** (`readOnlyHint: true`) — compares `registry.json`, the files
   on disk, `asset-lock.json`, and `modules/Assets.lua`; returns
   `{missingFromDisk, unregisteredOnDisk, lockDrift, assetsLuaDrift, checked}`. Pure
   filesystem, no Studio. **This is the CI check from P3-T6 with an agent-facing wrapper** —
   build it once, use it in both places.
2. **`audit_asset_references`** (`readOnlyHint: true`, needs a peer OR a supplied tree) —
   scans a Studio tree for `rbxassetid://` and `rbxasset://arena_assets/…` references and
   reports ones not present in the kit manifest, plus duplicate IDs. Catches the classic
   "image got deleted from the kit, place still points at it".
3. **`run_testez_suite`** (`readOnlyHint: false`, confirm-gated) — installs a local
   `tests/` dir (the `CONVENTIONS.md` `*.spec.lua` layout) into a scratch container and
   runs it through `StudioTestService`, returning per-spec pass/fail and durations.
   **Why:** the kit ships `tests/` directories and *nothing runs them today*; this closes
   the loop between P5-K1..K3 and reality. Mark it operator-verify in your report, because
   it needs a live peer.

Register all three in `INSPECTOR_MIXED_TOOLS` where appropriate, and add
`run_testez_suite` to `CONFIRM_GATED_TOOLS` (it installs into the place).

### P5-M2 — New MCP resources (2, only if RESOURCES has room for the pagination caps)
`roblox-bridge/mcp_core.py:RESOURCES` (currently 12):
1. `mcp://kit/integrity` — the `audit_kit_integrity` report (cheap, cache ~30 s).
2. `mcp://bridge/policy` — the *effective* security posture: `authMode`, gate mode,
   destructive-tool set, token count/roles, bind address, tunnel state, body cap,
   CORS allowlist. This is the resource an operator should be able to read before
   exposing the bridge. Emit it from one function that the dashboard also uses, so the
   dashboard cannot disagree with the MCP surface.
(Check `RESOURCE_TEMPLATES` and the pagination caps at `4868`/`11721` before adding.)

### P5-M3 — New MCP prompts (3)
`PROMPTS` (currently 7, each rendering live bus state with a safe fallback):
1. `security_review` — walk the operator through the P0 checklist against the *running*
   bridge (`mcp://bridge/policy` + `/api/audit` + `/api/tokens`), and produce a report
   with evidence.
2. `kit_integrity_triage` — repair flow for `audit_kit_integrity` failures, safest first.
3. `testez_first_workflow` — "write the spec, then the feature" loop for kit systems.
Each must degrade when there is no Studio peer and must not imply it proved anything.

### P5-SK1 / P5-SK2 — Two new skills
Follow the existing `SKILL.md` + `references/` shape and end with a **Verification**
section that states limits (the test at `test_bridge_mcp.py:82` asserts `## Verification`
exists in every skill — do not break it):
1. `.claude/skills/roblox-observability-and-telemetry/` — client/server logging, FPS and
   memory budget checks, crash reporting, what you can and cannot learn from Studio's
   output window; pairs with `perf_audit`/`get_console_output`.
2. `.claude/skills/roblox-save-migration/` — DataStore schema versioning, migration
   functions, rollback, what a `ProfileStore`/`DataServiceAPI` migration must guarantee.
   Pairs with `roblox-data-reliability`, but goes deep on versioning, which that skill
   only mentions.
Update `roblox-bridge/docs/SKILLS_ROADMAP.md` (the "Current assessment" list) so the next
agent knows these landed, and check `search_skills` still returns sensible results for
"telemetry" and "migration".

### P5-U1..U3 — Dashboard / CLI polish
1. **Policy panel**: render `mcp://bridge/policy` in the Ops tab with a red banner when
   `authMode == "open"` or the gate is off. This is the highest-value UI change: it makes
   P0-S5 visible to the operator instead of buried in a log line.
2. **Copy-config buttons per transport**: `stdio` (JSON for Claude/Cursor), `Streamable
   HTTP` local, `Streamable HTTP` tunnel. Today the dashboard has one generic snippet.
3. **Responsive pass at 390×844** for the Ops/fleet tabs, plus keyboard shortcuts
   (`/` search, `r` refresh, `g` then `o` for Ops) and `aria-label`s on the icon buttons.
   Keep the existing `de_slop_audit` / `audit_ui_layout` story honest: run
   `audit_ui_layout` against the dashboard markup in a test if you can extract it
   (P4-D1 gives you the HTML as data).

### P5-X1 — Optional, only if everything above lands: `bridge_ops` write actions for the kit
`bridge_ops action=kit_integrity` (read) and `kit_sync_manifest` (admin, confirm-gated)
so a remote agent can repair the manifest without shell access. Skip if P5-M1 is enough.

---

## 8. Release mechanics

- **Version:** bump to **6.1.0** in exactly these places (verified list):
  `server.py:59` (`VERSION`), `mcp_core.py:51` (`MCP_SERVER_VERSION`), `auth_core.py:28`,
  `bus_core.py:29`, `confirm_gate.py:53`, `mock_studio.py:36` (env-overridable),
  `ArenaBridge.lua:49` (`PLUGIN_VERSION`) + its header/`v6.0.0` docstrings,
  `arena_studio.py:3`, `mcp_server.py:4`, plus `PLUGIN_VERSION_MATRIX` (`mcp_core.py:8290`)
  and `bundled_ver` (`8327`), and the generated-code stamps at `8660`/`8771`.
- **Tests that hardcode `6.0.0`** (update with the bump, don't leave them asserting a
  stale string): `test_bridge_mcp.py:676,696,1230,2327,2346,2846,2849,3790,3899,4282`.
- `CHANGELOG.md`: new `## 6.1.0` section listing P0 as **security**, P1 as *fixed*,
  P2 as **removed**, P5 as **added**. Keep it honest about `run_testez_suite` and
  `audit_asset_references` needing a live peer.
- `README.md`: fix P4-D2 counts, drop NightLoop, add the new tools/systems/skills, and
  add a `## Security posture` section that states the defaults (loopback bind, token
  required, gate modes) — that section is the thing a new user needs most.
- `roblox-bridge/docs/OPS.md`: new §14 "Security model and review history" documenting
  P0-S1..S6 with the bypasses found and how each is now blocked, so the next audit
  starts from evidence rather than a repeat of this one.

---

## 9. What NOT to do

- Do not delete `roblox-kit/systems/mechanics/horror-director`, the horror prefabs, or
  the `showcase:horror_nightloop` renderer scene (P2-N2 explains why it still works).
- Do not convert the repo to a package with `pip install`, do not add `fastapi`/`flask`,
  do not replace `http.server`. The stdlib-only property is the product.
- Do not "modernise" the MCP protocol versions or drop the legacy eras — downstream
  clients depend on them.
- Do not regenerate `roblox-kit/assets/**` (4,677 files, hashed registry). If
  `verify_licences.py` fails after your change, you moved a file, not the fetcher.
- Do not rewrite `SESSION.md` with new machine paths; delete it (P4-D5).
- Do not run `import_image`/`render_offline` against the real repo as a smoke test
  without restoring the tree afterwards — both mutate `asset-lock.json`/`Assets.lua`
  and write files (P1-B3). The reviewer had to `git checkout` those three times.

---

## 10. Acceptance checklist (paste into the PR)

**Gate**
- [ ] `python3 -B test_bridge_mcp.py` → all pass, count ≥ 117, runtime ≤ 150 s
- [ ] `python3 -B tools/verify_licences.py` → `Licence registry OK: 4320 assets`
- [ ] `bash roblox-bridge/tools/check_globals.sh roblox-bridge` → `clean`
- [ ] `bash roblox-bridge/tools/check_globals.sh roblox-kit` → clean (new)
- [ ] `ruff check` → clean (new)
- [ ] kit-integrity check → no drift (new)
- [ ] docs-version guard → `docs agree on bridge 6.1.0` (currently **fails** — fix the regex)
- [ ] `git status` clean after running every verification command

**P0**
- [ ] S1 REST `POST /api/jobs` with `run_luau` + gate `all` → 403 + requestId; approve → succeeds
- [ ] S2 read token + write token + no token all fail to obtain the owner credential
- [ ] S3 `esc` escapes quotes; `/` sends CSP + `X-Frame-Options`; injection test passes
- [ ] S4 every path/URL argument rejects traversal and non-http(s) schemes; no FS/network effect
- [ ] S5 default bind is `127.0.0.1`; first run mints a token; placeholder rejected
- [ ] S6 `/api/kit/catalog` content requires `read`; policy sets are wired or deleted

**P1**
- [ ] B1 crash-loop capped at `maxRetries + 2`; `attempts` is truthful
- [ ] B2 snapshot restore preserves dotted names; per-script results; `stopOnError`/`dryRun`
- [ ] B3 no `KeyError: 'relPath'`; no partial apply; `check=True` non-mutating
- [ ] B4 `fleet.json` atomic; corrupt file warns instead of silently resetting
- [ ] B5 chunked/`Expect` requests get a clear 4xx, never a silent empty body
- [ ] B6 ruff gate in place; narrowed handlers justified; `make_http_handler` split (if attempted)

**P2**
- [ ] `grep -rn nightloop` (excluding CHANGELOG) → no output
- [ ] `git ls-files | grep -c nightloop` → 0
- [ ] `showcase:horror_nightloop` (or its alias) still renders; kit horror content intact
- [ ] docs no longer claim a `nightloop/` layout row

**P4/P5**
- [ ] README numbers match `docs/FACTS.md`; generated + CI-enforced
- [ ] root `LICENSE` present
- [ ] 3 new systems + 2 new modules + 3 new tools + 2 resources + 3 prompts + 2 skills,
      each with a test and a README/doc mention
- [ ] dashboard policy panel shows `authMode`/gate mode and warns on open mode

**Report**
- [ ] Every *operator-run* item (live Studio, live tunnel, TestEZ-in-Studio, DataStore)
      is listed as unverified, with the exact command an operator should run

---

## 11. Appendix — original repros (confirm the bug, then confirm the fix)

All snippets are stdlib-only and run from `roblox-bridge/`. They create
`bridge_state/` — delete it afterwards.

**S1 gate bypass**
```python
import json, threading, urllib.request, sys; sys.path.insert(0, '.')
import server
srv, st = server.create_http_server(host="127.0.0.1", port=0, token="o", require_confirm="all")
port = srv.server_address[1]; threading.Thread(target=srv.serve_forever, daemon=True).start()
def post(p, tok, body):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{p}", method="POST",
        data=json.dumps(body).encode(), headers={"X-Arena-Token": tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as x: return x.status, x.read().decode()
    except urllib.error.HTTPError as e: return e.code, e.read().decode()
_, b = post("/api/tokens", "o", {"action": "create", "label": "w", "role": "write"})
w = json.loads(b)["token"]
print(post("/api/jobs", w, {"type": "run_luau", "payload": {"code": "print(1)"}}))  # was 201
srv.shutdown()
```

**S2 owner-token disclosure**
```python
# same harness, token="owner-secret-zzz", then GET both routes with no token:
# /api/plugin/download -> 200 and the body contains BRIDGE_TOKEN = "owner-secret-zzz"
# /api/local-boot      -> 200 and json()["token"] == "owner-secret-zzz"
```

**S4 traversal / file scheme / arbitrary write**
```python
import sys; sys.path.insert(0, '.')
import mcp_core
class D:  # minimal client stub
    read_only = False
    def __getattr__(self, n): return lambda *a, **k: {}
print(mcp_core.execute_tool(D(), "import_image",
      {"imageUrl": "file:///etc/hostname", "saveToRepo": False, "name": "leak"}))  # was 200 + bytes:9
print(mcp_core.execute_tool(D(), "import_image",
      {"repoAsset": "../registry.json", "saveToRepo": False, "name": "probe"}))   # was 3.7 MB read
print(mcp_core.execute_tool(D(), "render_offline",
      {"tree": {"name": "Workspace", "className": "Workspace", "children": []},
       "saveFile": "~/.arena_write_probe.png", "returnInline": False,
       "width": 240, "height": 160}))                                              # wrote outside repo
# cleanup: rm ~/.arena_write_probe.png; git checkout -- roblox-kit/assets/asset-lock.json
```

**S5 open admin**
```python
import inspect, sys; sys.path.insert(0, '.'); import server
print(repr(server.load_token_default(None)))                       # '' when bridge.token is absent
print(inspect.signature(server.create_http_server).parameters['host'].default)   # '0.0.0.0'
srv, st = server.create_http_server(host="127.0.0.1", port=0)      # no token
print(st.authenticate("arena-demo-7f3a")[0] is not None)           # was True: placeholder accepted
print(st.direct_dispatch("health", {})["authMode"])                # 'open'
srv.server_close()   # NOT srv.shutdown() — without serve_forever() that blocks ~90 s
```

**B1 crash loop** — drive `state.enqueue_job("run_luau", …)` with a fake peer that
claims via `poll_for_job` then never reports; call `state.reap_jobs()` in a loop and
print `job["status"]`/`job["attempts"]`. Before the fix it stays `queued` forever.

**B3 manifest crash**
```python
# any import_image call whose args collide with an existing key, e.g.
# {"repoAsset": "../registry.json", "saveToRepo": True, "name": "escape_probe"}
# -> KeyError 'relPath' in image_pipeline.py:1155, after the file write + lock mutation
```

**Docs guard (fails today)**
```bash
python3 - <<'PY'   # the exact body from .github/ci.yml's "Docs reference a version" step
# ... paste from the workflow ...
PY
# exit 1: CONNECT.md: carries no version claim any more (guard is blind)
```
