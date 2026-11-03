# Gemini 4 Argon — work order from the tunnel side

**From:** the Arena coding agent on branch `arena/706ae6d5-tunneling`
**For:** Gemini 4 Argon (partner agent on the same bridge)
**Bridge version:** 5.0.0 · **Date:** 2026-11-03

You can work on any of these in parallel with me. Everything below is scoped so it
does **not** collide with the v5 ops work I just landed (`server.py`, `mcp_core.py`,
`arena_studio.py`, new modules). Claim a task on the live bus before you start
(see "How to claim" at the end) so we never edit the same file twice.

---

## Priority 1 — make the tunnel survive real life

| # | Task | Files you own | Definition of done |
|---|---|---|---|
| G1 | **Named-tunnel + custom-domain runbook.** Test `cloudflared tunnel run --token` against a Cloudflare Zero Trust named tunnel, document DNS/CNAME setup, and add a `--tunnel-name` validation path that fails loudly when the token is malformed. | `roblox-bridge/tunnel_manager.py`, `docs/OPS.md` (new section only) | A step-by-step doc a non-expert can follow, plus a unit test for the composed command line. |
| G2 | **Tunnel latency budget.** Measure request overhead of `/mcp` + `/api/jobs` through the quick tunnel vs localhost (p50/p95) using `python arena_studio.py metrics --prometheus` before/after. Add the numbers to `docs/OPS.md`. | `docs/OPS.md`, `jobs/` | Table of p50/p95 for 20 calls each of `ping_studio`, `get_studio_state`, `script_read`. |
| G3 | **Mobile/tunnel dashboard polish.** The new Ops tab is functional but plain. Improve responsive layout under 420 px, add a "copy MCP config with tunnel URL" button, and make the EventSource reconnect with backoff instead of dying. | `roblox-bridge/server.py` (dashboard JS only — do not touch Python) | Works at 390×844 in Chrome devtools, stream reconnects after killing the server for 10 s. |

## Priority 2 — agent-side intelligence

| # | Task | Files you own | Definition of done |
|---|---|---|---|
| G4 | **`agent_bus` prompt recipes.** Add 3 MCP prompts (`multi_agent_handoff`, `bus_standup`, `task_triage`) that teach an agent how to use the bus effectively. | `roblox-bridge/mcp_core.py` (`PROMPTS` + `get_mcp_prompt` only) | `prompts/list` returns 7 prompts; each renders with live bus state. |
| G5 | **Stale-agent sweeper.** Agents that stop heart-beating stay `offline` but keep their `in_progress` tasks until leases expire. Add a sweeper that (a) releases tasks of agents offline > 15 min, (b) posts an `alert` broadcast, (c) increments `bridge_agents_reaped_total`. | `roblox-bridge/bus_core.py`, tests | Unit test with fake clocks; no change to the public `handle()` action names. |
| G6 | **Handoff diff.** `action='export'` currently dumps everything. Add `since` support so the brief only contains activity after a cursor, and a `summaryOnly` flag. | `roblox-bridge/bus_core.py` | Existing handoff tests still pass; new test covers `since`. |

## Priority 3 — Studio-side verification (needs a live peer)

| # | Task | Definition of done |
|---|---|---|
| G7 | **Live acceptance run.** With Studio open, run `python setup.py --tunnel`, then from the Ops tab: start tunnel → confirm `state=online` and `health.ok=true`; run `bridge_ops {action:'status'}` from an MCP client; confirm `studioSmoke=pass` in `self_test`. Paste the JSON into `docs/OPS.md` under "Live acceptance". |
| G8 | **Kill-switch drill.** Stop Studio mid-job and confirm the reaper requeues the job (`bridge_jobs_reaped_total` increments, job returns to `queued`, then runs when Studio reconnects). Record before/after event feed excerpt. |

---

## Contract / conventions

- New modules are stdlib-only. Keep zero pip dependencies.
- Never weaken auth: `bridge.token` is the admin owner credential; scoped tokens are additive.
- Every state file goes under `roblox-bridge/bridge_state/` (gitignored).
- Run `python3 -B roblox-bridge/test_bridge_mcp.py` before pushing. It must stay green (24 tests as of this branch).
- Do not edit `roblox-bridge/bridge_state/**`, `bridge.token`, or `SESSION.md`.
- Commit on your own branch and open a PR against `main`; mention `v5 ops` in the title.

## How to claim a task on the live bus

```bash
# 1. register (returns your agent id)
python roblox-bridge/arena_studio.py bus register --name "Gemini 4 Argon" --role partner

# 2. see the board
python roblox-bridge/arena_studio.py bus tasks

# 3. claim with a 30-minute lease
python roblox-bridge/arena_studio.py bus task-claim --task task-xxxxxxxx --agent agent-xxxxxxxx

# 4. report progress / ask a question
python roblox-bridge/arena_studio.py bus post --agent agent-xxxxxxxx \
  --to "Claude" --subject "G2 in progress" --body "p95 through quick tunnel is 240ms"

# 5. finish
python roblox-bridge/arena_studio.py bus task-complete --task task-xxxxxxxx \
  --agent agent-xxxxxxxx --result "docs/OPS.md updated" 
```

Or from MCP: `agent_bus {action:"task_claim", taskId:"…", agentId:"…"}`.

The seeded board is created by `python roblox-bridge/tools/seed_agent_bus.py`
(read `handoff/bus_seed.json` for the exact task list and acceptance criteria).
