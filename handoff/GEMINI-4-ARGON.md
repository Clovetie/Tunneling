# Gemini 4 Argon — work order from the tunnel side

**From:** the Arena coding agent on branch `arena/706ae6d5-tunneling`
**For:** Gemini 4 Argon — a **model**, worked through a human operator or an agent wrapper
**Bridge version:** 5.1.0 · **Date:** 2026-11-03 · **Status:** G1–G6 landed, G7/G8 operator-only

Gemini 4 Argon does not run commands, open the repo, claim a bus task, or push a
branch. It returns **text**: a patch or diff, a documentation section, a test
method, or a design decision. The operator (a person, or an agent that can run
tools) attaches the listed files, pastes the prompt, applies the result, runs the
verification commands, and records the outcome on the Agent Bus.

Two PDFs carry the same material in a form that is easy to paste from:

| File | What it is |
|---|---|
| `handoff/Tunneling-v5-Code.pdf` | 88 pages — complete v5 sources (tunnel_manager, bus_core, auth_core, telemetry, seed_agent_bus) plus unified diffs for every modified file |
| `handoff/Gemini-4-Argon-Tasks.pdf` | 12 pages — G1–G8 as self-contained prompt briefs with owner files, context, definition of done, verification commands, a paste-ready prompt, a status line and a report-back template |

Rebuild them with `python3 roblox-bridge/tools/build_handoff_pdfs.py`
(build-time only; needs `reportlab` and `pygments`, the runtime bridge stays pip-free).

---

## Priority 1 — make the tunnel survive real life

Status key: **landed** = implemented and unit-tested on this branch in 5.1.0;
**operator** = the script ships, the evidence needs a live Studio peer.

| # | Task | Files owned | Definition of done |
|---|---|---|---|
| G1 | **Named-tunnel + custom-domain runbook.** Harden the `cloudflared tunnel run --token` path, document DNS/CNAME setup, and make a malformed token fail loudly. | `roblox-bridge/tunnel_manager.py`, `docs/OPS.md` (new section only) | A step-by-step doc a non-expert can follow, plus a unit test for the composed command line. |
| G2 | **Tunnel latency budget.** Measure `/api/jobs` overhead through the quick tunnel vs localhost (p50/p95) and add a `docs/OPS.md` table. | `docs/OPS.md`, `jobs/` (new script) | Stdlib-only measurement script plus a table of p50/p95 for 20 calls each of `ping_studio`, `get_studio_state`, `script_read`. |
| G3 | **Mobile/tunnel dashboard polish.** Responsive Ops tab under 720 px, a "copy MCP config (tunnel)" button, and an EventSource that reconnects with backoff. | `roblox-bridge/server.py` (dashboard HTML/JS only — no Python changes) | Works at 390×844, the stream shows `reconnecting` and recovers after a 10 s outage. |

## Priority 2 — agent-side intelligence

| # | Task | Files owned | Definition of done |
|---|---|---|---|
| G4 | **`agent_bus` prompt recipes.** Add 3 MCP prompts (`multi_agent_handoff`, `bus_standup`, `task_triage`). | `roblox-bridge/mcp_core.py` (`PROMPTS` + `get_mcp_prompt` only) | `prompts/list` returns 7 prompts; each renders live bus state with a safe fallback. |
| G5 | **Stale-agent sweeper.** Release `in_progress` tasks held by agents offline > 15 min, post one `alert`, increment `agentsReaped`. | `roblox-bridge/bus_core.py`, `roblox-bridge/test_bridge_mcp.py` | Unit test with injected timestamps; public `handle()` action names unchanged. |
| G6 | **Incremental handoff brief.** Add `since` and `summaryOnly` to `action='export'`. | `roblox-bridge/bus_core.py`, tests | Existing handoff tests still pass; a new test proves only later messages appear. |

## Priority 3 — Studio-side verification (needs a live peer, operator-run)

| # | Task | Definition of done |
|---|---|---|
| G7 | **Live acceptance run.** With Studio open, start the tunnel and confirm `state=online` / `health.ok=true`, then `bridge_ops {action:'status'}` from an MCP client through the public URL and `self_test` with `includeStudioSmoke=true`. | Model returns the script and expected outputs; the operator pastes the JSON under "Live acceptance" in `docs/OPS.md`. |
| G8 | **Kill-switch drill.** Stop Studio mid-job; confirm `bridge_jobs_reaped_total` increments and the job returns to `queued`, then completes on reconnect. | Model returns the drill steps and the fields to capture; the operator records the feed excerpt. |

---

## Contract / conventions

- New modules are stdlib-only. Keep the bridge free of pip dependencies at runtime.
- Never weaken auth: `bridge.token` is the admin owner credential; scoped tokens are additive.
- Every state file goes under `roblox-bridge/bridge_state/` (gitignored).
- `python3 -B roblox-bridge/test_bridge_mcp.py` must stay green — **31 tests** on this branch.
- Do not edit `roblox-bridge/bridge_state/**`, `bridge.token`, or `SESSION.md`.
- Return a unified diff plus the exact commands the operator should run; say "unverified"
  instead of inventing measurements that need a real Studio peer or a real tunnel.

## How the operator works a task

```bash
# 0. see the board (the model cannot do this)
python roblox-bridge/arena_studio.py bus tasks

# 1. claim the task before pasting the prompt
python roblox-bridge/arena_studio.py bus task-claim --task task-xxxxxxxx --agent agent-xxxxxxxx

# 2. attach the listed files, paste the prompt block from the task PDF, apply the answer
python3 -B roblox-bridge/test_bridge_mcp.py         # expect: Ran 31 tests ... OK

# 3. post a note while it is in flight
python roblox-bridge/arena_studio.py bus post --agent agent-xxxxxxxx \
  --to "Claude" --subject "G2 in progress" --body "script drafted, measurements pending"

# 4. complete it with artifacts
python roblox-bridge/arena_studio.py bus task-complete --task task-xxxxxxxx \
  --agent agent-xxxxxxxx --result "docs/OPS.md updated" --detail "see jobs/measure_tunnel_latency.py"
```

The seeded board is created by `python roblox-bridge/tools/seed_agent_bus.py`
(read `handoff/bus_seed.json` for the exact task list and acceptance criteria).

## Where each task landed

| # | Status | Where |
|---|---|---|
| G1 | landed | `tunnel_manager.validate_named_token`, `setup.py --tunnel-name`, `docs/OPS.md` §1.1 |
| G2 | script landed, numbers pending | `jobs/measure_tunnel_latency.py`, `docs/OPS.md` §11 |
| G3 | landed | `server.py` dashboard (responsive CSS, reconnecting EventSource, copy MCP config) |
| G4 | landed | `mcp_core.py` `PROMPTS` + `get_mcp_prompt(client=…)` (7 prompts) |
| G5 | landed | `bus_core.sweep_stale`, `agent_bus {action:'sweep'}`, CLI `bus sweep` |
| G6 | landed | `bus_core.export_handoff(since=…, summary_only=…)` |
| G7 | operator | `jobs/live_acceptance.py` → paste JSON into `docs/OPS.md` §10.1 |
| G8 | operator | `jobs/drill_studio_kill.py` → paste JSON into `docs/OPS.md` §10.2 |
