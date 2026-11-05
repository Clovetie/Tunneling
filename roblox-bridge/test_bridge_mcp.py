#!/usr/bin/env python3
"""
test_bridge_mcp.py — Comprehensive automated test suite for the v5.4.0
Arena <-> Roblox Studio Bridge, CLI, and MCP Server (stdio + Streamable HTTP).

Tests:
  1. Exact 32-bit FNV-1a test vectors & UTF-8 round-trip fidelity
  2. Local Luau toolchain pre-flight (`luau_check`): syntax errors, undefined globals, clean files
  3. Token recovery in `setup.py` (verifying HTTP 401 token mismatch trap is cured) & `.mcp.json` writer
  4. Streamable HTTP MCP (`POST /mcp`, `GET /mcp`, `DELETE /mcp`, notifications `202`, batches, auth)
  5. MCP `stdio` subprocess transport (BOTH newline-delimited JSON and LSP `Content-Length` framing,
     auto-attach to running bridge AND standalone embedded bridge mode)
  6. All MCP tools end-to-end against stateful `MockWorld` v4.1.0:
     - Session/State (`get_studio_state`, `list_studios`, `ping_studio`)
     - Exploration (`survey_place`, `inspect_instance`, `search_game_tree`, `get_properties`, `compare_instances`)
     - Scripts (`script_read` with line ranges, `read_scripts_batch`, `write_script` with luau_check + expectedHash,
       `multi_edit` exact & line-splice & ambiguity guard, `script_search`, `script_grep`,
       `find_and_replace_scripts` dryRun + live + auto-snapshot, `open_script_in_editor`, `require_fresh`)
     - Execution & Playtest (`execute_luau` Edit/Server/Client, `start_stop_play`, `run_playtest_scenario`, `get_console_output`)
     - Instances (`build_tree`, `set_properties`, `mass_set_properties`, `clone_instance` grid offset,
       `reparent_or_rename`, `delete_instance`)
     - Attributes & Tags (`manage_attributes`, `manage_tags`)
     - World (`manage_selection`, `manage_camera`, `undo_redo` real tree rollback!, `edit_terrain`,
       `insert_asset` script-stripping protection)
     - Drift, Package Sync & Snapshots (`drift_and_sync` push/check/pull on real `nightloop/src`, `manage_snapshots` restore)
  7. Read-Only Inspector mode server-side enforcement (both MCP tools/call AND REST `/api/jobs`)
  8. CLI (`arena_studio.py`) end-to-end execution including UTF-8 non-ASCII characters (`—`, `·`)
"""

import json
import os
import re
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from bus_core import AgentBus

HERE = Path(__file__).resolve().parent
TOOL_TEST_METRICS = {}

def record_tool_metric(name: str, elapsed: float, passed: bool = True):
    metric = TOOL_TEST_METRICS.setdefault(name, {"status": "pass", "calls": 0, "durationSec": 0.0})
    metric["calls"] += 1
    metric["durationSec"] = round(metric["durationSec"] + elapsed, 4)
    if not passed:
        metric["status"] = "fail"

sys.path.insert(0, str(HERE))

from mcp_core import BridgeClient, TOOL_SPECS, execute_tool, fnv1a_32, fnv1a_hex, run_luau_check  # noqa: E402
from mock_studio import run_mock_loop  # noqa: E402
from scene_auditor import audit_3d_hierarchy  # noqa: E402
from ui_linter import audit_tree_on_viewport, run_multi_viewport_audit  # noqa: E402
from viewport_renderer import _blit_scaled_rgba, render_scene_png  # noqa: E402
from server import BridgeState, create_http_server  # noqa: E402
import server as bridge_server  # noqa: E402
import setup as bridge_setup  # noqa: E402


class TestV5OpsPlatform(unittest.TestCase):
    """v5: scoped tokens, rate limits, multi-agent bus, resilient jobs, audit chain, tunnel."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arena-v5-")
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.admin_token = "admin-secret-v5"
        self.httpd, self.state = create_http_server(
            host="127.0.0.1", port=self.port, token=self.admin_token, state_dir=self.tmp,
        )
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.state._reaper_stop.set()
        self.state.tunnel.stop()
        self.httpd.shutdown()
        self.httpd.server_close()

    def _req(self, path, method="GET", body=None, token=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw and raw.strip().startswith(("{", "[")) else raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            return exc.code, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw)

    # -- tokens / scopes / rate limiting ------------------------------------

    def test_scoped_tokens_enforce_roles_and_rate_limits(self):
        created = self.state.tokens.create("reader", role="read", rate_limit=100, burst=100)
        reader = created["token"]
        self.assertTrue(created["note"])
        self.assertEqual(self._req("/api/state", token=reader)[0], 200)
        self.assertEqual(self._req("/api/jobs", method="POST", body={"type": "ping"}, token=reader)[0], 403)
        self.assertEqual(self._req("/api/tokens", token=reader)[0], 403)

        # Read scope cannot invoke a mutating MCP tool, even over /mcp.
        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "write_script", "arguments": {"path": "ServerScriptService.X", "source": "print(1)"}}}
        status, body = self._req("/mcp", method="POST", body=rpc, token=reader)
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["isError"])
        self.assertIn("insufficient_scope", json.dumps(body))

        # Admin can list, mint a write token, and revoke it.
        status, listing = self._req("/api/tokens", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(listing["count"], 2)
        writer = self.state.tokens.create("writer", role="write", rate_limit=50)
        status, jobs = self._req("/api/jobs", method="POST",
                                 body={"type": "ping", "wait": 0}, token=writer["token"])
        self.assertEqual(status, 201)
        status, revoked = self._req("/api/tokens", method="POST",
                                    body={"action": "revoke", "id": writer["id"]}, token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertTrue(revoked["revoked"])
        self.assertEqual(self._req("/api/jobs", method="POST", body={"type": "ping"}, token=writer["token"])[0], 401)

        # Unknown tokens are rejected everywhere except the public health probe.
        self.assertEqual(self._req("/api/state", token="nope")[0], 401)
        self.assertEqual(self._req("/api/health")[0], 200)

        # Rate limiting: one-request burst then 429 with Retry-After.
        slow = self.state.tokens.create("firehose", role="read", rate_limit=0.01, burst=1)
        self.assertEqual(self._req("/api/state", token=slow["token"])[0], 200)
        status, body = self._req("/api/state", token=slow["token"])
        self.assertEqual(status, 429)
        self.assertIn("rate limit", body["error"])
        self.assertGreaterEqual(body["retryAfter"], 1)

    # -- multi-agent bus ----------------------------------------------------

    def test_agent_bus_presence_mailbox_leases_and_handoff(self):
        res = self._req("/api/bus", method="POST", token=self.admin_token, body={
            "action": "register", "name": "Gemini 4 Argon", "role": "partner",
            "capabilities": ["roblox", "qa"],
        })
        self.assertEqual(res[0], 200)
        gem = res[1]
        claude = self.state.bus.handle({"action": "register", "name": "Claude", "role": "builder"})
        self.assertEqual(self.state.bus.stats()["agents"], 2)

        self.state.bus.handle({"action": "post", "agentId": gem["id"], "to": claude["id"],
                               "subject": "handoff", "body": "take the UI pass", "kind": "handoff"})
        self.state.bus.handle({"action": "post", "agentId": gem["id"], "subject": "broadcast",
                               "body": "standup in 5"})
        inbox = self.state.bus.handle({"action": "inbox", "agentId": claude["id"]})
        self.assertEqual(inbox["count"], 2)
        self.assertEqual(inbox["unread"], 2)
        self.state.bus.handle({"action": "mark_read", "agentId": claude["id"], "all": True})
        self.assertEqual(self.state.bus.handle({"action": "inbox", "agentId": claude["id"]})["unread"], 0)

        task = self.state.bus.handle({"action": "task_create", "title": "Polish shop UI",
                                      "createdBy": gem["id"], "priority": "critical", "tags": ["ui"]})
        # Claim with a one-second lease; a second agent must be refused while it holds.
        self.state.bus.handle({"action": "task_claim", "taskId": task["id"], "agentId": gem["id"], "leaseSeconds": 1})
        with self.assertRaises(ValueError):
            self.state.bus.handle({"action": "task_claim", "taskId": task["id"], "agentId": claude["id"]})
        # ...and allowed once the lease expires, without any manual cleanup.
        time.sleep(1.2)
        taken = self.state.bus.handle({"action": "task_claim", "taskId": task["id"], "agentId": claude["id"]})
        self.assertEqual(taken["assignee"], claude["id"])
        self.assertEqual(taken["leaseExpiries"], 1)

        done = self.state.bus.handle({"action": "task_complete", "taskId": task["id"], "agentId": claude["id"],
                                      "result": "UI shipped", "artifacts": ["StarterGui/Shop"]})
        self.assertEqual(done["status"], "done")
        handoff = self.state.bus.handle({"action": "export", "path": os.path.join(self.tmp, "HANDOFF.md")})
        self.assertIn("# Agent handoff brief", handoff["markdown"])
        self.assertIn("Polish shop UI", handoff["markdown"])
        self.assertTrue(os.path.isfile(handoff["path"]))

    # -- resilient job queue ------------------------------------------------

    def test_job_idempotency_priority_retry_and_reaping(self):
        state = self.state
        job1, _ = state.enqueue_job("ping", note="first", idempotency_key="abc123")
        job2, _ = state.enqueue_job("ping", note="dedupe", idempotency_key="abc123")
        self.assertEqual(job1["id"], job2["id"])
        self.assertEqual(state.jobs[job1["id"]]["deduplicated"], 1)
        self.assertEqual(len([j for j in state.jobs.values() if j["status"] == "queued"]), 1)

        low, _ = state.enqueue_job("ping", note="low", priority=7)
        high, _ = state.enqueue_job("ping", note="high", priority=0)
        peer = state.record_poll({"peer": ["peer-priority"], "dm": ["edit"], "place": ["p"], "placeId": ["1"]})
        first = state.poll_for_job(peer, wait_seconds=0.1)
        self.assertEqual(first["id"], high["id"])
        second = state.poll_for_job(peer, wait_seconds=0.1)
        self.assertEqual(second["id"], job1["id"])  # priority 5 beats priority 7
        third = state.poll_for_job(peer, wait_seconds=0.1)
        self.assertEqual(third["id"], low["id"])

        # Transient failure is retried once, then fails permanently.
        retry_job, evt = state.enqueue_job("ping", note="retry", max_retries=1)
        state.poll_for_job(peer, wait_seconds=0.1)
        state.complete_job({"id": retry_job["id"], "ok": False, "error": "peer disconnected mid-job",
                            "peerId": "peer-priority"})
        self.assertEqual(state.jobs[retry_job["id"]]["status"], "queued")
        self.assertFalse(evt.is_set())
        state.poll_for_job(peer, wait_seconds=0.1)
        state.complete_job({"id": retry_job["id"], "ok": False, "error": "peer disconnected again",
                            "peerId": "peer-priority"})
        self.assertEqual(state.jobs[retry_job["id"]]["status"], "error")
        self.assertTrue(evt.is_set())
        self.assertEqual(state.jobs[retry_job["id"]]["retries"], 1)

        # A job claimed by a peer that vanishes is requeued by the reaper.
        orphan, _ = state.enqueue_job("ping", note="orphan")
        state.poll_for_job(peer, wait_seconds=0.1)
        with state.lock:
            state.jobs[orphan["id"]]["claimed"] = time.time() - 60
            for pid in list(state.peers):
                state.peers[pid]["lastPoll"] = 0
        result = state.reap_jobs()
        self.assertGreaterEqual(result["recovered"], 1)
        self.assertEqual(state.jobs[orphan["id"]]["status"], "queued")

        # Timeout reaping finalises a stuck running job.
        state.clear_queue()
        stuck, stuck_evt = state.enqueue_job("ping", note="stuck", timeout=1)
        peer2 = state.record_poll({"peer": ["peer-stuck"], "dm": ["edit"], "place": ["p"], "placeId": ["1"]})
        state.poll_for_job(peer2, wait_seconds=0.1)
        with state.lock:
            state.jobs[stuck["id"]]["claimed"] = time.time() - 30
        state.reap_jobs()
        self.assertEqual(state.jobs[stuck["id"]]["status"], "error")
        self.assertIn("timeout", state.jobs[stuck["id"]]["error"])
        self.assertTrue(stuck_evt.is_set())

    # -- observability ------------------------------------------------------

    def test_metrics_events_and_tamper_evident_audit(self):
        self.state.enqueue_job("ping", note="metrics")
        self.state.telemetry.metrics.observe("unit_latency_ms", 12.5, {"route": "/api/jobs"})
        self.state.telemetry.metrics.observe("unit_latency_ms", 30.0, {"route": "/api/jobs"})
        self.state.telemetry.events.emit("unit.event", {"hello": "world"})

        self.state.tokens.create("audited", role="read")
        status, metrics = self._req("/api/metrics", token=self.admin_token)
        self.assertEqual(status, 200)
        names = {c["name"] for c in metrics["counters"]}
        self.assertIn("bridge_jobs_enqueued_total", names)
        hist = next(h for h in metrics["histograms"] if h["name"] == "unit_latency_ms")
        self.assertEqual(hist["count"], 2)
        self.assertGreaterEqual(hist["p95"], hist["p50"])

        status, text = self._req("/api/metrics?format=prometheus", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertIn("# TYPE bridge_jobs_enqueued_total counter", text)
        self.assertIn('unit_latency_ms_count{route="/api/jobs"} 2', text)

        status, events = self._req("/api/events?once=1&since=0&limit=20", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertTrue(any(e["kind"] == "unit.event" for e in events["events"]))

        status, verify = self._req("/api/audit?verify=1", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertTrue(verify["ok"])
        self.assertGreater(verify["entries"], 0)

        # Tamper with an entry in memory: the chain verification must notice.
        with self.state.telemetry.audit.lock:
            victim = self.state.telemetry.audit.entries[-1]
            victim["actor"] = "someone-else"
        broken = self.state.telemetry.audit.verify()
        self.assertFalse(broken["ok"])
        self.assertIn("hash", broken["reason"])

    def test_tunnel_refuses_to_publish_an_unauthenticated_bridge(self):
        open_state = BridgeState(token="", read_only=False, state_dir=tempfile.mkdtemp(prefix="arena-open-"))
        blocked = open_state.tunnel_action({"action": "start"})
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn("without a token", blocked["lastError"])
        # Status checks are always allowed.
        self.assertIn(open_state.tunnel_action({"action": "status"})["state"], ("stopped", "unavailable"))
        # With a token configured the guard steps aside (the sandbox has no binary,
        # so the supervisor reports unavailable rather than blocked).
        allowed = self.state.tunnel_action({"action": "start"})
        self.assertNotEqual(allowed["state"], "blocked")

    def test_read_only_bridge_blocks_playtest_mutations_and_keeps_reads(self):
        ro = BridgeState(token="t", read_only=True, state_dir=tempfile.mkdtemp(prefix="arena-ro-"))
        # Synthetic input drives the live game and the native test runner writes a
        # temporary Script, so both are mutations; so is applying a device profile.
        for kind, payload in (
            ("virtual_input", {"steps": [{"action": "press", "key": "E"}]}),
            ("studio_tests", {"mode": "run"}),
            ("device_simulator", {"action": "set_device", "deviceId": "iPhone15Pro"}),
        ):
            with self.assertRaises(PermissionError) as caught:
                ro.enqueue_job(kind, payload)
            self.assertIn("--read-only", str(caught.exception))
        # Reading the simulated device is what an Inspector-only bridge is for.
        job, _evt = ro.enqueue_job("device_simulator", {"action": "status"})
        self.assertEqual(job["type"], "device_simulator")
        self.assertEqual(len(ro.pending), 1)
        self.assertEqual(len(ro.MUTATING_JOB_TYPES & {"virtual_input", "studio_tests"}), 2)

    def test_ops_report_and_public_health_shape(self):
        status, health = self._req("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["version"], "5.4.0")
        self.assertIn("tunnel", health)
        self.assertIn("agents", health)
        status, ops = self._req("/api/ops", token=self.admin_token)
        self.assertEqual(status, 200)
        for key in ("health", "tunnel", "bus", "audit", "recentJobs", "openTasks"):
            self.assertIn(key, ops)

    # -- v5.1 partner tasks: sweeper, incremental handoff, prompts, tunnel token --

    def test_stale_agent_sweeper_and_incremental_handoff(self):
        bus = self.state.bus
        builder = bus.handle({"action": "register", "name": "Builder", "role": "builder"})
        partner = bus.handle({"action": "register", "name": "Partner", "role": "qa"})
        live_task = bus.handle({"action": "task_create", "title": "Held by a live agent",
                                "createdBy": builder["id"], "priority": "high"})
        dead_task = bus.handle({"action": "task_create", "title": "Held by a dead agent",
                                "createdBy": builder["id"], "detail": "should be released"})
        bus.handle({"action": "task_claim", "taskId": live_task["id"], "agentId": builder["id"]})
        bus.handle({"action": "task_claim", "taskId": dead_task["id"], "agentId": partner["id"]})

        # Partner stops heart-beating a long time ago; builder is still alive.
        bus.agents[partner["id"]]["lastSeen"] = time.time() - 5000
        swept = bus.handle({"action": "sweep", "offlineSeconds": 900})
        self.assertEqual(swept["tasksReaped"], 1)
        self.assertEqual(swept["released"][0]["taskId"], dead_task["id"])
        self.assertEqual(bus.tasks[dead_task["id"]]["status"], "todo")
        self.assertIsNone(bus.tasks[dead_task["id"]]["assignee"])
        self.assertIsNone(bus.tasks[dead_task["id"]]["lease"])
        self.assertEqual(bus.tasks[live_task["id"]]["status"], "in_progress")

        # Idempotent: a second sweep releases nothing and posts no second alert.
        again = bus.handle({"action": "sweep", "offlineSeconds": 900})
        self.assertEqual(again["tasksReaped"], 0)
        self.assertFalse(again["alerted"])
        alerts = [m for m in bus.messages if m["kind"] == "alert"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("Stale agent tasks released", alerts[0]["subject"])

        stats = bus.stats()
        self.assertEqual(stats["tasksReaped"], 1)
        self.assertEqual(stats["agentsReaped"], 1)
        self.assertIsNotNone(stats["lastSweepAt"])
        # Counters survive a reload (persisted with the board).
        reloaded = AgentBus(bus.path)
        self.assertEqual(reloaded.stats()["tasksReaped"], 1)

        # Export: cursor filtering keeps old activity out, open tasks stay in.
        bus.handle({"action": "post", "agentId": builder["id"], "body": "BEFORE-MARKER", "subject": "old"})
        cursor = bus.stats()["cursor"]
        bus.handle({"action": "post", "agentId": builder["id"], "body": "AFTER-MARKER", "subject": "new"})
        fresh = bus.handle({"action": "export", "since": cursor})
        self.assertIn("AFTER-MARKER", fresh["markdown"])
        self.assertNotIn("BEFORE-MARKER", fresh["markdown"])
        self.assertIn("Held by a live agent", fresh["markdown"])

        summary = bus.handle({"action": "export", "summaryOnly": True})
        self.assertIn("Held by a live agent", summary["markdown"])
        self.assertNotIn("should be released", summary["markdown"])  # detail omitted
        self.assertIn("_No messages._", summary["markdown"])
        self.assertLess(len(summary["markdown"]), len(bus.handle({"action": "export"})["markdown"]))

    def test_mcp_prompts_render_live_bus_state(self):
        from mcp_core import PROMPTS, get_mcp_prompt
        names = [p["name"] for p in PROMPTS]
        self.assertEqual(len(PROMPTS), 7)
        for name in ("multi_agent_handoff", "bus_standup", "task_triage"):
            self.assertIn(name, names)

        self.state.bus.handle({"action": "register", "name": "Roster Probe", "role": "qa"})
        self.state.bus.handle({"action": "task_create", "title": "Probe task", "priority": "critical"})
        for name in ("multi_agent_handoff", "bus_standup", "task_triage"):
            prompt = get_mcp_prompt(name, {}, self.state.mcp_client)
            text = prompt["messages"][0]["content"]["text"]
            self.assertIn("Roster Probe", text, name)
            self.assertIn("Probe task", text, name)
            self.assertTrue(prompt["description"])

        # No client (or a dead bridge) must degrade to the safe fallback, never raise.
        offline = get_mcp_prompt("bus_standup", {}, None)
        self.assertIn("unavailable", offline["messages"][0]["content"]["text"])

        # The dispatcher wires the client through prompts/get.
        resp = self.state.mcp_dispatcher.handle_payload(
            {"jsonrpc": "2.0", "id": 9, "method": "prompts/get",
             "params": {"name": "task_triage", "arguments": {}}})
        self.assertIn("Roster Probe", resp["result"]["messages"][0]["content"]["text"])

    def test_named_tunnel_token_is_validated_before_spawn(self):
        from tunnel_manager import TunnelManager, validate_named_token
        good = "eyJhIjoiY2Y4ZjQxY2EiLCJ0IjoiYWJjZGVmIn0="
        self.assertTrue(validate_named_token(good)[0])
        for bad, needle in (("", "empty"), ("   ", "empty"), ("short", "too short"),
                            ("has space", "whitespace"), ("https://x.trycloudflare.com", "URL"),
                            ("bad$chars$here$and$long$enough", "unexpected")):
            ok, reason = validate_named_token(bad)
            self.assertFalse(ok, bad)
            self.assertIn(needle.lower(), reason.lower(), bad)

        bad_mgr = TunnelManager(port=self.port, flavor="cloudflared", named_token="bad token here",
                                state_dir=self.tmp)
        started = bad_mgr.start()
        self.assertEqual(started["state"], "error")
        self.assertIn("token", started["lastError"].lower())
        self.assertFalse(started["namedTokenValid"])
        bad_mgr.binary = "/bin/true"  # even with a binary present, the token never reaches a subprocess
        bad_mgr.flavor = "cloudflared"
        with self.assertRaises(RuntimeError):
            bad_mgr._command()

        named = TunnelManager(port=self.port, flavor="cloudflared", named_token=good, state_dir=self.tmp,
                              name="studio.example.com")
        named.binary, named.flavor = "/bin/true", "cloudflared"
        cmd = named._command()
        self.assertEqual(cmd, ["/bin/true", "tunnel", "--no-autoupdate", "run", "--token", good])
        self.assertNotIn("--url", cmd)
        shown = named.status()
        self.assertTrue(shown["namedTokenValid"])
        self.assertEqual(shown["name"], "studio.example.com")

    def test_dashboard_ops_tab_is_responsive_and_eventsource_reconnects(self):
        from server import dashboard_html
        html = bridge_server.dashboard_html()
        self.assertIn("copyMcpConfig", html)
        self.assertIn("Copy MCP config (tunnel)", html)
        self.assertIn("@media(max-width:720px)", html)
        self.assertIn("grid-template-columns:repeat(2,minmax(0,1fr))", html)
        self.assertIn("Reconnecting (${Math.round(wait/1000)}s)", html)
        self.assertIn("EV_DELAY * 2", html)
        self.assertIn("overflow-x:hidden", html)

        script = re.search(r"<script>(.*?)</script>", html, re.S)
        self.assertIsNotNone(script)
        node = shutil.which("node")
        if node:
            path = os.path.join(self.tmp, "dashboard.js")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(script.group(1))
            checked = subprocess.run([node, "--check", path], capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stderr)

    def test_latency_script_measures_percentiles_and_reports_auth_errors(self):
        import http.server
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = os.path.join(repo, "jobs", "measure_tunnel_latency.py")

        class FakeBridge(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                if self.headers.get("Authorization") != "Bearer good-token":
                    body = b'{"error":"unauthorized"}'
                    self.send_response(401)
                else:
                    body = json.dumps({"id": "job-1", "status": "done", "result": {"ok": True}}).encode()
                    time.sleep(0.005)
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeBridge)
        threading.Thread(target=fake.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{fake.server_address[1]}"
        try:
            ok = subprocess.run([sys.executable, script, "--url", f"local={base}", "--token", "good-token",
                                 "--runs", "3", "--markdown"], capture_output=True, text=True, timeout=120)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("| Tool |", ok.stdout)
            self.assertIn("ping_studio", ok.stdout)

            denied = subprocess.run([sys.executable, script, "--url", f"local={base}", "--token", "nope",
                                     "--runs", "2"], capture_output=True, text=True, timeout=120)
            self.assertEqual(denied.returncode, 2)
            self.assertIn("token rejected", denied.stderr)
        finally:
            fake.shutdown()
            fake.server_close()

    def test_operator_scripts_fail_loudly_without_a_studio_peer(self):
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        acceptance = subprocess.run(
            [sys.executable, os.path.join(repo, "jobs", "live_acceptance.py"),
             "--url", self.base, "--token", self.admin_token],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(acceptance.returncode, 1)
        self.assertIn("no Studio peer", acceptance.stdout)
        self.assertIn("bridge-reachable", acceptance.stdout)

        drill = subprocess.run(
            [sys.executable, os.path.join(repo, "jobs", "drill_studio_kill.py"),
             "--url", self.base, "--token", self.admin_token, "--wait", "5"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(drill.returncode, 3)
        self.assertIn("no Studio peer", drill.stderr)

    # -- v5.2: maintenance pause, automatic sweeper, ops resources -----------

    def test_queue_pause_holds_jobs_until_resumed(self):
        status, paused = self._req("/api/queue/pause", method="POST",
                                   body={"reason": "maintenance window"}, token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["reason"], "maintenance window")

        job, _ = self.state.enqueue_job("ping", note="during pause")
        peer = self.state.record_poll({"peer": ["peer-pause"], "dm": ["edit"], "place": ["p"], "placeId": ["1"]})
        self.assertIsNone(self.state.poll_for_job(peer, wait_seconds=0.2))
        with self.state.lock:
            self.assertEqual(self.state.jobs[job["id"]]["status"], "queued")

        status, health = self._req("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["queuePaused"])

        status, resumed = self._req("/api/queue/resume", method="POST", body={}, token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertFalse(resumed["paused"])
        claimed = self.state.poll_for_job(peer, wait_seconds=1.0)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], job["id"])

        _, ops = self._req("/api/ops", token=self.admin_token)
        self.assertIn("queue", ops)
        self.assertFalse(ops["queue"]["paused"])
        self.assertIn("sweeper", ops)
        self.assertIn("jobLatency", ops)
        # Pausing requires admin, not a read token.
        reader = self.state.tokens.create("pause-reader", role="read", rate_limit=50)
        denied = self._req("/api/queue/pause", method="POST", body={}, token=reader["token"])
        self.assertEqual(denied[0], 403)

    def test_automatic_sweeper_releases_stale_tasks_and_counts_them(self):
        bus = self.state.bus
        ghost = bus.handle({"action": "register", "name": "Ghost", "role": "qa"})
        task = bus.handle({"action": "task_create", "title": "Ghost task", "createdBy": ghost["id"]})
        bus.handle({"action": "task_claim", "taskId": task["id"], "agentId": ghost["id"]})
        bus.agents[ghost["id"]]["lastSeen"] = time.time() - 5000

        before = self.state.metrics_snapshot()["counters"]
        swept = self.state.auto_sweep()
        self.assertEqual(swept["tasksReaped"], 1)
        self.assertEqual(bus.tasks[task["id"]]["status"], "todo")
        self.assertEqual(self.state._sweeps, 1)
        self.assertTrue(self.state._last_sweep)

        metrics = self.state.metrics_snapshot()
        names = {c["name"]: c["value"] for c in metrics["counters"]}
        self.assertGreaterEqual(names.get("bridge_agents_reaped_total", 0), 1)
        audit = self.state.telemetry.audit.tail(5)["entries"]
        self.assertTrue(any(e["action"] == "bus.sweep" for e in audit))

        # A quiet sweep is silent (no new alert message) and still counts a cycle.
        bus.agents[ghost["id"]]["lastSeen"] = time.time()
        quiet = self.state.auto_sweep()
        self.assertEqual(quiet["tasksReaped"], 0)
        self.assertEqual(self.state._sweeps, 2)
        self.assertEqual(len([m for m in bus.messages if m["kind"] == "alert"]), 1)

        # Latency summary surfaces per-tool percentiles once jobs have durations.
        with self.state.lock:
            self.state.jobs["job-lat"] = {
                "id": "job-lat", "type": "ping_studio", "status": "done", "note": "lat",
                "ok": True, "error": None, "result": None, "duration_ms": 42.0,
                "created": time.time(), "finished": time.time(),
            }
            self.state.history.append("job-lat")
        summary = self.state._job_latency_summary()
        self.assertIn("ping_studio", summary)
        self.assertEqual(summary["ping_studio"]["p50"], 42.0)

    def test_bridge_ops_control_actions_and_ops_resources(self):
        from mcp_core import MCPDispatcher, RESOURCES
        uris = [r["uri"] for r in RESOURCES]
        self.assertEqual(len(uris), 11)
        self.assertIn("studio://jobs", uris)
        self.assertIn("studio://handoff", uris)

        dispatcher = MCPDispatcher(self.state.mcp_client)
        paused = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "bridge_ops", "arguments": {"action": "queue_pause", "reason": "unit test"}},
        })
        self.assertFalse(paused["result"].get("isError"), paused)
        self.assertTrue(self.state.queue_paused)
        resumed = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "bridge_ops", "arguments": {"action": "queue_resume"}},
        })
        self.assertFalse(resumed["result"].get("isError"), resumed)
        self.assertFalse(self.state.queue_paused)

        self.state.bus.handle({"action": "register", "name": "Resource Probe", "role": "qa"})
        handoff = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 3, "method": "resources/read",
            "params": {"uri": "studio://handoff"},
        })
        text = handoff["result"]["contents"][0]["text"]
        self.assertIn("# Agent handoff brief", text)
        self.assertIn("Resource Probe", text)

        jobs = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 4, "method": "resources/read",
            "params": {"uri": "studio://jobs"},
        })
        payload = json.loads(jobs["result"]["contents"][0]["text"])
        for key in ("queue", "sweeper", "health", "jobLatency", "recentJobs"):
            self.assertIn(key, payload)

    def test_cli_queue_pause_status_resume(self):
        env = dict(os.environ, BRIDGE_URL=self.base, BRIDGE_TOKEN=self.admin_token)
        cli = [sys.executable, str(HERE / "arena_studio.py"), "queue"]

        paused = subprocess.run(cli + ["pause", "--reason", "cli smoke"], env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(paused.returncode, 0, msg=paused.stdout + paused.stderr)
        self.assertTrue(json.loads(paused.stdout)["paused"])
        self.assertTrue(self.state.queue_paused)

        status = subprocess.run(cli + ["status"], env=env, capture_output=True,
                                text=True, encoding="utf-8", timeout=30)
        self.assertEqual(status.returncode, 0, msg=status.stdout + status.stderr)
        report = json.loads(status.stdout)
        self.assertTrue(report["queue"]["paused"])
        self.assertEqual(report["queue"]["reason"], "cli smoke")
        self.assertIn("sweeper", report)

        resumed = subprocess.run(cli + ["resume"], env=env, capture_output=True,
                                 text=True, encoding="utf-8", timeout=30)
        self.assertEqual(resumed.returncode, 0, msg=resumed.stdout + resumed.stderr)
        self.assertFalse(json.loads(resumed.stdout)["paused"])
        self.assertFalse(self.state.queue_paused)

        # `studios` CLI mirrors /api/studios (empty fleet here, but the shape holds).
        studios = subprocess.run(cli[:-1] + ["studios", "list"], env=env, capture_output=True,
                                 text=True, encoding="utf-8", timeout=30)
        self.assertEqual(studios.returncode, 0, msg=studios.stdout + studios.stderr)
        fleet = json.loads(studios.stdout)
        self.assertEqual(fleet["count"], 0)
        self.assertIn("studios", fleet)

    # -- v5.3: multi-studio fleet, modern MCP era, tasks ---------------------

    def _two_studios(self):
        alpha = self.state.record_poll({"peer": ["peer-alpha"], "dm": ["edit"], "place": ["Alpha"],
                                        "placeId": ["111"], "gameId": ["9001"], "pv": ["3.1"]})
        beta = self.state.record_poll({"peer": ["peer-beta"], "dm": ["edit"], "place": ["Beta"],
                                       "placeId": ["222"], "gameId": ["9002"], "pv": ["3.1"]})
        return alpha, beta

    def test_fleet_list_pin_routing_and_persistence(self):
        alpha, beta = self._two_studios()
        status, fleet = self._req("/api/studios", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(fleet["count"], 2)
        self.assertEqual(fleet["routing"], "auto")
        ids = [s["peerId"] for s in fleet["studios"]]
        self.assertEqual(ids, ["peer-alpha", "peer-beta"])
        self.assertEqual([s["defaultTarget"] for s in fleet["studios"]], [True, False])

        status, pinned = self._req("/api/studios", method="POST",
                                   body={"action": "set_active", "peerId": "peer-beta"},
                                   token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(pinned["active"], "peer-beta")

        job, _ = self.state.enqueue_job("ping", note="pinned routing")
        self.assertIsNone(self.state.poll_for_job(alpha, wait_seconds=0.1))
        claimed = self.state.poll_for_job(beta, wait_seconds=0.5)
        self.assertEqual(claimed["id"], job["id"])
        self.state.complete_job({"id": job["id"], "ok": True, "result": {"pong": True}, "peerId": "peer-beta"})

        # The pin survives a restart (fleet.json in the state dir).
        reloaded = BridgeState(token=self.admin_token, state_dir=self.tmp)
        try:
            self.assertEqual(reloaded.fleet_snapshot()["pinned"], "peer-beta")
        finally:
            reloaded._reaper_stop.set()

        # A closed pinned Studio must not stall the queue: routing falls back to auto.
        self.state.peers["peer-beta"]["lastPoll"] = time.time() - 300
        self.assertTrue(self.state.fleet_snapshot()["routing"] == "auto")
        job2, _ = self.state.enqueue_job("ping", note="fallback routing")
        self.assertIsNotNone(self.state.poll_for_job(alpha, wait_seconds=0.5))
        self.state.complete_job({"id": job2["id"], "ok": True, "result": {}, "peerId": "peer-alpha"})

        status, cleared = self._req("/api/studios", method="POST", body={"action": "clear_active"},
                                    token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertIsNone(cleared["active"])

        # Unknown peers are a 404, not a silent success.
        bad = self._req("/api/studios", method="POST", body={"action": "set_active", "peerId": "nope"},
                        token=self.admin_token)
        self.assertEqual(bad[0], 404)

    def test_fleet_labels_broadcast_and_write_refusal(self):
        _alpha, _beta = self._two_studios()
        status, labelled = self._req("/api/studios", method="POST",
                                     body={"action": "label", "peerId": "peer-beta", "label": "Beta Main"},
                                     token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(labelled["label"], "Beta Main")
        _, fleet = self._req("/api/studios", token=self.admin_token)
        self.assertEqual([s["label"] for s in fleet["studios"]], ["Alpha (edit)", "Beta Main"])

        # Broadcast fans the job out to every online peer and collects results.
        def serve(peer):
            job = self.state.poll_for_job(peer, wait_seconds=3.0)
            if job:
                self.state.complete_job({"id": job["id"], "ok": True, "result": {"pong": peer["peerId"]},
                                         "peerId": peer["peerId"]})

        peers = [self.state.peers["peer-alpha"], self.state.peers["peer-beta"]]
        threads = [threading.Thread(target=serve, args=(p,), daemon=True) for p in peers]
        for th in threads:
            th.start()
        status, result = self._req("/api/studios", method="POST",
                                   body={"action": "broadcast", "type": "ping", "timeout": 8},
                                   token=self.admin_token)
        for th in threads:
            th.join(timeout=5)
        self.assertEqual(status, 200)
        self.assertEqual(result["peers"], 2)
        self.assertEqual(result["succeeded"], 2)
        self.assertEqual({r["peerId"] for r in result["results"]}, {"peer-alpha", "peer-beta"})
        self.assertEqual(result["results"][0]["result"]["pong"], "peer-alpha")

        # Mutating kinds are refused — a broadcast must never edit every place.
        with self.assertRaises(ValueError):
            self.state.broadcast(kind="write_script", payload={}, timeout=1)
        refused = self._req("/api/studios", method="POST",
                            body={"action": "broadcast", "type": "write_script", "timeout": 1},
                            token=self.admin_token)
        self.assertEqual(refused[0], 400)

    def test_fleet_mcp_tool_and_resource(self):
        self._two_studios()
        from mcp_core import MCPDispatcher
        dispatcher = MCPDispatcher(self.state.mcp_client)
        listed = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "studio_fleet", "arguments": {"action": "list"}},
        })
        self.assertFalse(listed["result"]["isError"], listed)
        structured = listed["result"]["structuredContent"]
        self.assertEqual(structured["count"], 2)

        pinned = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "studio_fleet", "arguments": {"action": "set_active", "peerId": "peer-alpha"}},
        })
        self.assertEqual(pinned["result"]["structuredContent"]["active"], "peer-alpha")

        resource = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 3, "method": "resources/read",
            "params": {"uri": "studio://studios"},
        })
        payload = json.loads(resource["result"]["contents"][0]["text"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["pinned"], "peer-alpha")

        # Broadcast through MCP with a write-only tool name is rejected.
        bad = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "studio_fleet", "arguments": {"action": "broadcast", "broadcastTool": "script_read",
                                                             "timeout": 1}},
        })
        self.assertTrue(bad["result"]["isError"])
        self.assertIn("path", bad["result"]["content"][0]["text"])

    def test_modern_mcp_era_discover_resulttype_and_headers(self):
        from mcp_core import MCPDispatcher, MODERN_PROTOCOL_VERSION
        dispatcher = MCPDispatcher(self.state.mcp_client)
        discover = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 1, "method": "server/discover"})
        result = discover["result"]
        self.assertIn(MODERN_PROTOCOL_VERSION, result["supportedVersions"])
        self.assertIn("tasks", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "arena-roblox-studio-mcp")

        # Version travels per request in _meta; unknown versions get -32022.
        rejected = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 2, "method": "tools/list",
            "_meta": {"io.modelcontextprotocol/protocolVersion": "2019-01-01"},
        })
        self.assertEqual(rejected["error"]["code"], -32022)
        self.assertIn("supportedVersions", rejected["error"]["data"])

        modern = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 3, "method": "tools/list",
            "_meta": {"io.modelcontextprotocol/protocolVersion": MODERN_PROTOCOL_VERSION},
        })
        self.assertEqual(modern["result"]["resultType"], "complete")
        self.assertEqual(modern["result"]["ttlMs"], 60000)
        self.assertEqual(modern["result"]["cacheScope"], "private")

        legacy = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        self.assertNotIn("resultType", legacy["result"])

        # Over HTTP the version can ride in MCP-Protocol-Version and the routing
        # headers must agree with the body (-32020 when they do not).
        req = urllib.request.Request(
            self.base + "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/list"}).encode(),
            headers={"Authorization": f"Bearer {self.admin_token}",
                     "Content-Type": "application/json",
                     "MCP-Protocol-Version": MODERN_PROTOCOL_VERSION},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
        self.assertEqual(payload["result"]["resultType"], "complete")


        mismatch = urllib.request.Request(
            self.base + "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 6, "method": "tools/list"}).encode(),
            headers={"Authorization": f"Bearer {self.admin_token}",
                     "Content-Type": "application/json",
                     "Mcp-Method": "tools/call"},
            method="POST",
        )
        try:
            urllib.request.urlopen(mismatch, timeout=20)
            self.fail("expected -32020 for a Mcp-Method/body mismatch")
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode())
            self.assertEqual(exc.code, 400)
            self.assertEqual(body["error"]["code"], -32020)

    def test_mcp_tasks_lifecycle(self):
        from mcp_core import MCPDispatcher
        dispatcher = MCPDispatcher(self.state.mcp_client)
        created = dispatcher.handle_payload({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "studio_fleet", "arguments": {"action": "list"},
                       "task": {"ttl": 60000}},
        })
        task = created["result"]
        self.assertEqual(task["resultType"], "task")
        self.assertTrue(task["taskId"].startswith("task-"))
        self.assertEqual(task["status"], "working")
        self.assertEqual(task["pollIntervalMs"], 1000)

        deadline = time.time() + 10
        status = "working"
        while time.time() < deadline:
            got = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 2, "method": "tasks/get",
                                             "params": {"taskId": task["taskId"]}})
            status = got["result"]["status"]
            if status in ("completed", "failed"):
                break
            time.sleep(0.05)
        self.assertEqual(status, "completed")

        result = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 3, "method": "tasks/result",
                                            "params": {"taskId": task["taskId"], "timeoutMs": 5000}})
        payload = result["result"]
        self.assertEqual(payload["_meta"]["io.modelcontextprotocol/related-task"]["taskId"], task["taskId"])
        self.assertFalse(payload["isError"])
        self.assertTrue(payload["structuredContent"]["studios"] == [])

        listing = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 4, "method": "tasks/list"})
        self.assertEqual(len(listing["result"]["tasks"]), 1)

        unknown = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 5, "method": "tasks/get",
                                             "params": {"taskId": "task-nope"}})
        self.assertEqual(unknown["error"]["code"], -32602)

        # A cancelled task reports cancelled and keeps its terminal state.
        cancelled = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 6, "method": "tasks/cancel",
                                               "params": {"taskId": task["taskId"]}})
        self.assertEqual(cancelled["result"]["status"], "completed")  # finished tasks are untouched

        tools = dispatcher.handle_payload({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
        self.assertEqual(tools["result"]["tools"][0]["execution"]["taskSupport"], "optional")

    def test_dashboard_fleet_strip_motion_and_health_fields(self):
        html = self.state.dashboard_html() if hasattr(self.state, "dashboard_html") else None
        if html is None:
            from server import dashboard_html
            html = bridge_server.dashboard_html()
        for needle in ("fleet-bar", "fleet-chips", "studio-card", "broadcastPing",
                       "prefers-reduced-motion", "animateValue", "paneIn", "brand"):
            self.assertIn(needle, html)
        status, health = self._req("/api/health")
        self.assertEqual(status, 200)
        self.assertIn("studios", health)
        self.assertEqual(health["studios"]["count"], 0)
        self.assertIn("protocolVersions", self._req("/api/ops", token=self.admin_token)[1]["mcp"])

    # -- v5.3.1: the "tunnel page looks frozen" trap --------------------------

    def test_dashboard_needs_token_off_loopback_and_says_so(self):
        """A dashboard opened through a tunnel/LAN address gets no token from
        /api/local-boot, so every /api call 401s. The page must surface that
        instead of silently keeping stale numbers."""
        html = bridge_server.dashboard_html()
        self.assertIn("auth-banner", html)
        self.assertIn("saveToken", html)
        self.assertIn("apiFetch", html)
        self.assertIn("PAGE_VERSION", html)
        self.assertNotIn("__BRIDGE_VERSION__", html)
        self.assertIn(f'PAGE_VERSION = "{bridge_server.VERSION}"', html)

        # Loopback: the token is handed out so the page authenticates itself.
        req = urllib.request.Request(self.base + "/api/local-boot")
        with urllib.request.urlopen(req, timeout=15) as resp:
            boot = json.loads(resp.read().decode())
        self.assertTrue(boot["local"])
        self.assertEqual(boot["token"], self.admin_token)

        # Tunnel / LAN: a forwarded client address is never loopback.
        proxied = urllib.request.Request(self.base + "/api/local-boot",
                                         headers={"CF-Connecting-IP": "203.0.113.9"})
        with urllib.request.urlopen(proxied, timeout=15) as resp:
            boot_remote = json.loads(resp.read().decode())
        self.assertFalse(boot_remote["local"])
        self.assertEqual(boot_remote["token"], "")
        self.assertTrue(boot_remote["tokenRequired"])

        # And the API really does refuse that page until a token is supplied.
        state_proxied = urllib.request.Request(self.base + "/api/state",
                                               headers={"CF-Connecting-IP": "203.0.113.9"})
        with self.assertRaises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(state_proxied, timeout=15)
        self.assertEqual(denied.exception.code, 401)
        authed = urllib.request.Request(self.base + "/api/state",
                                        headers={"CF-Connecting-IP": "203.0.113.9",
                                                 "Authorization": f"Bearer {self.admin_token}"})
        with urllib.request.urlopen(authed, timeout=15) as resp:
            self.assertEqual(resp.status, 200)

    def test_setup_dry_run_explains_the_tunnel_plan(self):
        import setup as bridge_setup
        cmd = ["python", "server.py", "--port", "8077"]
        supervised = "\n".join(line for line, _ in
                              bridge_setup.describe_launch(cmd, True, "auto", None, 8077))
        self.assertIn("Dry run", supervised)
        self.assertIn("supervised auto", supervised)
        self.assertIn("http://127.0.0.1:8077", supervised)

        local_only = "\n".join(line for line, _ in
                               bridge_setup.describe_launch(cmd, False, "auto", None, 8077))
        self.assertIn("not requested", local_only)

        external = "\n".join(line for line, _ in
                              bridge_setup.describe_launch(cmd, False, "auto", "https://x.example", 8077))
        self.assertIn("external https://x.example", external)

    # -- tunnel supervisor --------------------------------------------------

    def test_tunnel_supervisor_scrapes_url_and_restarts(self):
        from tunnel_manager import TunnelManager
        fake = os.path.join(self.tmp, "fake-cloudflared")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(
                "#!/usr/bin/env python3\n"
                "import sys, time\n"
                "print('INF Requesting new quick Tunnel...', flush=True)\n"
                "print('INF |  https://unit-test-tunnel.trycloudflare.com  |', flush=True)\n"
                "time.sleep(30)\n"
            )
        os.chmod(fake, 0o755)
        tunnel = TunnelManager(port=self.port, binary=fake, flavor="cloudflared",
                               state_dir=self.tmp, health_interval=3600)
        try:
            started = tunnel.start()
            self.assertIn(started["state"], ("starting", "online"))
            for _ in range(60):
                if tunnel.status()["state"] == "online":
                    break
                time.sleep(0.1)
            status = tunnel.status()
            self.assertEqual(status["state"], "online")
            self.assertEqual(status["publicUrl"], "https://unit-test-tunnel.trycloudflare.com")
            self.assertEqual(status["mcpEndpoint"], "https://unit-test-tunnel.trycloudflare.com/mcp")
            self.assertTrue(status["running"])
            stopped = tunnel.stop()
            self.assertEqual(stopped["state"], "stopped")
        finally:
            tunnel.stop()

        # Named Cloudflare tunnels use `run --token`, never a quick-tunnel `--url`.
        named = TunnelManager(port=self.port, binary=fake, flavor="cloudflared",
                              named_token="eyJhIjoiY2Y4ZjQxY2EiLCJ0IjoiYWJjZGVmIn0=", state_dir=self.tmp)
        named._detect()
        cmd = named._command()
        self.assertIn("run", cmd)
        self.assertIn("--token", cmd)
        self.assertNotIn("--url", cmd)
        self.assertNotIn("http://127.0.0.1", " ".join(cmd))
        self.assertTrue(named.status()["named"])

        # No binary -> explicit unavailable state with install hints, never a crash.
        with mock.patch("tunnel_manager.find_tunnel_binary", return_value=(None, None)):
            missing = TunnelManager(port=self.port, state_dir=self.tmp)
            result = missing.start()
        self.assertEqual(result["state"], "unavailable")
        self.assertIn("cloudflared", result["lastError"])
        self.assertTrue(result["installHints"])


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestHashAndToolchain(unittest.TestCase):
    def test_fnv1a_exact_vectors(self):
        # Must match ArenaBridge.lua & jobs/drift_audit.lua test vectors bit-for-bit
        self.assertEqual(fnv1a_32("a"), 3826002220)
        self.assertEqual(fnv1a_hex("a"), "e40c292c")
        self.assertEqual(fnv1a_32("hello"), 1335831723)
        self.assertEqual(fnv1a_hex("hello"), "4f9f2cab")

    def test_every_self_stamped_module_agrees_on_the_version(self):
        """A version string is not feature evidence — but a stale one means two
        halves of the bridge were released apart. Everything that stamps itself
        must agree, including the changelog head and the plugin/mock pair."""
        import auth_core
        import bus_core
        import confirm_gate
        import telemetry
        import tunnel_manager
        from mcp_core import MCP_SERVER_VERSION
        import server as bridge_server

        stamped = {
            "server.VERSION": bridge_server.VERSION,
            "mcp_core.MCP_SERVER_VERSION": MCP_SERVER_VERSION,
            "confirm_gate.GATE_VERSION": confirm_gate.GATE_VERSION,
            "auth_core.AUTH_VERSION": auth_core.AUTH_VERSION,
            "bus_core.BUS_VERSION": bus_core.BUS_VERSION,
            "telemetry.TELEMETRY_VERSION": telemetry.TELEMETRY_VERSION,
            "tunnel_manager.TUNNEL_VERSION": tunnel_manager.TUNNEL_VERSION,
        }
        self.assertEqual(set(stamped.values()), {MCP_SERVER_VERSION}, str(stamped))

        changelog = (HERE / "CHANGELOG.md").read_text(encoding="utf-8")
        head = re.search(r"^## (\d+\.\d+\.\d+)", changelog, re.M)
        self.assertTrue(head, "CHANGELOG.md must open with a versioned entry")
        self.assertEqual(head.group(1), MCP_SERVER_VERSION)

        lua = (HERE / "ArenaBridge.lua").read_text(encoding="utf-8")
        lua_version = re.search(r'local PLUGIN_VERSION = "([0-9.]+)"', lua).group(1)
        import mock_studio
        self.assertEqual(mock_studio.PLUGIN_VERSION, lua_version,
                         "the mock must claim the plugin version it implements")
        # The mock registers itself with `pv=<plugin version>` exactly as the
        # plugin does, so /api/peers and the dashboard tell the same story.
        self.assertIn('"pv": PLUGIN_VERSION',
                      (HERE / "mock_studio.py").read_text(encoding="utf-8"))
        setup_text = (HERE / "setup.py").read_text(encoding="utf-8")
        self.assertIn(f"v{MCP_SERVER_VERSION}", setup_text)

    def test_luau_check_catches_syntax_and_undefined_globals(self):
        good = run_luau_check(code="local x = Vector3.new(1, 2, 3)\nreturn tostring(x)\n")
        self.assertTrue(good["ok"])

        bad_syntax = run_luau_check(code="local function broken(\nreturn 1\n")
        self.assertFalse(bad_syntax["ok"])
        self.assertFalse(bad_syntax["results"][0]["syntaxOk"])

        bad_global = run_luau_check(code="local function f()\n\treturn deletedHelperFunction(42)\nend\nreturn f\n")
        if bad_global["toolchainAvailable"]:
            self.assertFalse(bad_global["ok"])
            syms = [g["symbol"] for g in bad_global["results"][0]["undefinedGlobals"]]
            self.assertIn("deletedHelperFunction", syms)

    def test_arena_bridge_lua_passes_luau_check(self):
        res = run_luau_check(path=str(HERE / "ArenaBridge.lua"))
        self.assertTrue(res["ok"], msg=json.dumps(res, indent=2))
        self.assertEqual(res["results"][0]["unusedVariables"], [])


class TestV4RegressionFixtures(unittest.TestCase):
    def test_viewport_scaled_blit_accepts_fractional_ui_rectangles(self):
        dst = bytearray([0, 0, 0, 255] * 24 * 16)
        src = bytes([255, 80, 40, 255] * 4)
        _blit_scaled_rgba(dst, 24, 16, src, 2, 2, 3.25, 2.5, 10.5, 7.25)
        self.assertIn(bytes([255, 80, 40, 255]), [dst[i:i+4] for i in range(0, len(dst), 4)])

    def test_property_coercion_failures_are_explicit_in_plugin(self):
        source = (HERE / "ArenaBridge.lua").read_text(encoding="utf-8")
        self.assertIn("unsupported property coercion tag:", source)
        self.assertIn("unknown Enum item:", source)
        self.assertIn("Instance reference did not resolve:", source)
        self.assertIn("Color3 components must be finite normalized numbers", source)
        self.assertIn("One or more properties were not applied.", source)
        self.assertIn("ok = not anyFailed", source)
        self.assertIn("build transaction rolled back:", source)
        self.assertNotIn("parseHexColor(v) or Color3.new(1, 1, 1)", source)

    def test_viewport_renders_geometry_camera_and_hud(self):
        workspace = {"name": "Workspace", "className": "Workspace", "children": [{
            "name": "VisibleCrate", "className": "Part", "properties": {},
            "structuredProperties": {"Size": {"__t": "Vector3", "v": [8, 6, 8]}, "Position": {"__t": "Vector3", "v": [0, 3, 0]}, "Color": {"__t": "Color3", "v": [0.9, 0.2, 0.1]}, "Anchored": True},
        }]}
        a, ma = render_scene_png(workspace, width=480, height=270, include_hud=False, show_grid=False)
        b, mb = render_scene_png(workspace, width=480, height=270, explicit_cam_pos=(16, 12, 24), explicit_look_at=(0, 3, 0), include_hud=False, show_grid=False)
        from image_pipeline import decode_any_to_rgba
        wa, ha, pa = decode_any_to_rgba(a)
        self.assertEqual((wa, ha), (480, 270))
        self.assertGreater(ma["partsRendered"], 0)
        self.assertGreater(ma["facesRasterized"], 0)
        self.assertNotEqual(a, b)
        bg = pa[:4]
        self.assertGreater(sum(1 for i in range(0, len(pa), 4) if pa[i:i+4] != bg), 500)
        gui = [{"name": "HUD", "className": "ScreenGui", "structuredProperties": {"ResetOnSpawn": False}, "children": [{"name": "Panel", "className": "Frame", "structuredProperties": {"Size": {"__t": "UDim2", "v": [0, 140, 0, 80]}, "Position": {"__t": "UDim2", "v": [0, 12, 0, 16]}, "BackgroundColor3": {"__t": "Color3", "v": [0.1, 0.3, 0.8]}, "BackgroundTransparency": 0}}]}]
        _, mh = render_scene_png(workspace, gui_tree=gui, width=480, height=270, include_hud=True, show_grid=False)
        self.assertGreater(mh["hudElementsComposited"], 0)

    def test_ui_bounds_and_reset_property_are_read_from_typed_data(self):
        tree = [{"name": "Root", "className": "ScreenGui", "structuredProperties": {"ResetOnSpawn": False}, "children": [{"name": "Card", "className": "Frame", "structuredProperties": {"Position": {"__t": "UDim2", "v": [0, 0, 0, 0]}, "Size": {"__t": "UDim2", "v": [0, 140, 0, 126]}, "BackgroundTransparency": 0}}]}]
        report = audit_tree_on_viewport(tree, 640, 470, "desktop")
        card = next(e for e in report["elements"] if e["name"] == "Card")
        self.assertEqual(card["rect"], [0.0, 0.0, 140.0, 126.0])
        self.assertFalse(any(x["code"] == "reset_on_spawn_enabled" for x in report["issues"]))

    def test_missing_live_peer_fast_fails(self):
        state = BridgeState(state_dir=tempfile.mkdtemp(prefix="arena-state-"))
        client = BridgeClient(direct_dispatch=state.direct_dispatch)
        job = client.submit_job("inspect", {"path": "Workspace"}, wait=60, dm="edit")
        self.assertEqual(job["status"], "error")
        self.assertEqual(job["reason"], "no_peer")

    def test_offline_renderer_accepts_exported_tree_without_peer(self):
        state = BridgeState(state_dir=tempfile.mkdtemp(prefix="arena-state-"))
        client = BridgeClient(direct_dispatch=state.direct_dispatch)
        tree = {"name": "Workspace", "className": "Workspace", "children": [{
            "name": "OfflinePart", "className": "Part", "structuredProperties": {
                "Size": {"__t": "Vector3", "v": [8, 4, 8]},
                "Position": {"__t": "Vector3", "v": [0, 2, 0]},
                "Color": {"__t": "Color3", "v": [0.2, 0.7, 0.3]},
            },
        }]}
        result = execute_tool(client, "render_offline", {"tree": tree, "width": 480, "height": 270})
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "exported_tree")
        self.assertGreater(result["facesRasterized"], 0)

    def test_live_ui_measurements_and_device_safe_area_are_reported(self):
        tree = [{
            "name": "RuntimeGui", "className": "ScreenGui",
            "structuredProperties": {
                "ScreenInsets": {"__t": "Enum", "v": "Enum.ScreenInsets.DeviceSafeInsets"},
                "DeviceSafeAreaInsets": [12, 24, 12, 18],
            },
            "children": [{
                "name": "Measured", "className": "Frame",
                "structuredProperties": {
                    "Position": {"__t": "UDim2", "v": [0, 0, 0, 0]},
                    "Size": {"__t": "UDim2", "v": [0, 100, 0, 50]},
                    "AbsolutePosition": {"__t": "Vector2", "v": [12, 24]},
                    "AbsoluteSize": {"__t": "Vector2", "v": [100, 50]},
                },
            }],
        }]
        report = run_multi_viewport_audit(tree, ["mobile"])
        self.assertEqual(report["measuredFrom"], "client")
        self.assertTrue(report["safeArea"][0]["safeAreaAware"])
        self.assertEqual(report["safeArea"][0]["insetsPx"], [12.0, 24.0, 12.0, 18.0])
        self.assertEqual(report["viewports"]["mobile"]["elements"][0]["rect"], [12.0, 24.0, 100.0, 50.0])

    def test_snapshots_persist_to_disk_and_reload(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(bridge_server, "SNAPSHOT_DIR", Path(folder)):
                first = BridgeState(state_dir=folder)
                saved = first.handle_snapshots({"action": "save", "label": "named-baseline", "root": "ServerStorage", "scripts": [{"path": "ServerStorage.A", "source": "return 1"}]})
                second = BridgeState(state_dir=folder)
                got = second.handle_snapshots({"action": "get", "id": saved["id"]})
                self.assertEqual(got["label"], "named-baseline")
                self.assertEqual(got["scripts"][0]["path"], "ServerStorage.A")
                self.assertTrue((Path(folder) / f"{saved['id']}.json.gz").is_file())

    def test_z_fighting_requires_real_overlap_and_collidable_surface(self):
        def part(name, pos, color, collide=True):
            return {"name": name, "className": "Part", "properties": {}, "structuredProperties": {"Size": [10, 1, 10], "Position": pos, "Anchored": True, "CanCollide": collide, "Color": color}}
        clean = {"name": "Workspace", "className": "Workspace", "children": [part("FarA", [0, 5, 0], [1, 0, 0]), part("FarB", [30, 5, 0], [0, 1, 0])]}
        self.assertFalse(any(x["code"] == "Z_FIGHTING_COPLANAR" for x in audit_3d_hierarchy(clean)["issues"]))
        overlap = {"name": "Workspace", "className": "Workspace", "children": [part("A", [0, 5, 0], [1, 0, 0]), part("B", [1, 5, 0], [0, 1, 0])]}
        self.assertTrue(any(x["code"] == "Z_FIGHTING_COPLANAR" for x in audit_3d_hierarchy(overlap)["issues"]))


class TestSetupTokenRecovery(unittest.TestCase):
    def test_recovers_token_from_installed_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            plugins_folder = tmp_dir / "Plugins"
            token_path = tmp_dir / "bridge.token"
            orig_token_file = bridge_setup.TOKEN_FILE
            try:
                bridge_setup.TOKEN_FILE = token_path
                # First install writes a custom token into Plugins/ArenaBridge.lua
                bridge_setup.install_plugin("customsecret998877", 8077, explicit_dir=plugins_folder)
                self.assertFalse(token_path.exists())

                # Simulate fresh branch ZIP folder with NO bridge.token!
                recovered = bridge_setup.get_token(rotate=False, explicit_dir=plugins_folder)
                self.assertEqual(recovered, "customsecret998877")
                self.assertEqual(token_path.read_text(encoding="utf-8").strip(), "customsecret998877")

                # Verify .mcp.json writer preserves existing servers cleanly
                mcp_file = bridge_setup.write_mcp_config(tmp_dir, port=8077, read_only=False)
                parsed = json.loads(mcp_file.read_text(encoding="utf-8"))
                self.assertIn("roblox-studio", parsed["mcpServers"])
            finally:
                bridge_setup.TOKEN_FILE = orig_token_file


class TestEndToEndBridgeAndMCP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.token = "test-secret-token-v3"
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.state_dir = tempfile.mkdtemp(prefix="arena-e2e-state-")
        cls.httpd, cls.state = create_http_server(
            host="127.0.0.1",
            port=cls.port,
            token=cls.token,
            read_only=False,
            state_dir=cls.state_dir,
        )
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()

        cls.stop_mock = threading.Event()
        cls.mock_thread = threading.Thread(
            target=run_mock_loop,
            args=(cls.base_url, cls.token, cls.stop_mock),
            daemon=True,
        )
        cls.mock_thread.start()

        # Wait for mock Studio peer to register its first poll
        for _ in range(40):
            if cls.state.direct_dispatch("health", {})["studio_connected"]:
                break
            threading.Event().wait(0.05)

    @classmethod
    def tearDownClass(cls):
        cls.stop_mock.set()
        cls.httpd.shutdown()

    def mcp_http(self, rpc_body, auth_mode="bearer"):
        data = json.dumps(rpc_body).encode("utf-8")
        url = f"{self.base_url}/mcp"
        headers = {"Content-Type": "application/json"}
        if auth_mode == "bearer":
            headers["Authorization"] = f"Bearer {self.token}"
        elif auth_mode == "header":
            headers["X-Arena-Token"] = self.token
        elif auth_mode == "query":
            url += f"?token={self.token}"
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None, dict(resp.headers)

    def call_tool(self, name: str, arguments: dict = None) -> dict:
        started = time.monotonic()
        passed = False
        try:
            status, body, _ = self.mcp_http({
                "jsonrpc": "2.0",
                "id": f"call-{name}",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            })
            self.assertEqual(status, 200)
            self.assertIn("result", body, msg=f"{name} -> {json.dumps(body)[:600]}")
            res = body["result"]
            self.assertFalse(res.get("isError"), msg=f"Tool {name} errored: {res}")
            passed = True
            return res["structuredContent"]
        finally:
            record_tool_metric(name, time.monotonic() - started, passed)

    def test_01_streamable_http_handshake_notifications_batches_and_auth(self):
        # Unauthorized without token -> 401
        req = urllib.request.Request(
            f"{self.base_url}/mcp",
            data=b'{"jsonrpc":"2.0","id":1,"method":"ping"}',
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

        # Initialize handshake
        st, init_resp, hdrs = self.mcp_http({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "unit-test", "version": "1.0"}},
        })
        self.assertEqual(st, 200)
        self.assertEqual(init_resp["result"]["protocolVersion"], "2025-06-18")
        self.assertIn("Mcp-Session-Id", hdrs)

        # Notification returns HTTP 202 Accepted with empty body
        st_n, body_n, _ = self.mcp_http({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(st_n, 202)
        self.assertIsNone(body_n)

        # Tools list returns every annotated tool
        _, list_resp, _ = self.mcp_http({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, auth_mode="header")
        tools = list_resp["result"]["tools"]
        self.assertEqual(len(tools), len(TOOL_SPECS))
        self.assertEqual(len(tools), len(TOOL_SPECS))
        _, unknown_arg, _ = self.mcp_http({"jsonrpc": "2.0", "id": "unknown-arg", "method": "tools/call", "params": {"name": "browse_kit", "arguments": {"typo": True}}})
        self.assertEqual(unknown_arg["error"]["code"], -32602)
        self.assertIn("Unknown parameter", unknown_arg["error"]["message"])
        for t in tools:
            self.assertIn("annotations", t)
            self.assertIn("readOnlyHint", t["annotations"])

        # Dashboard/API self-test route includes the registered-tool matrix.
        if not os.environ.get("ARENA_SELF_TEST_CHILD"):
            self_test_req = urllib.request.Request(f"{self.base_url}/api/self-test", headers={"X-Arena-Token": self.token})
            with urllib.request.urlopen(self_test_req, timeout=90) as self_test_resp:
                self_test_api = json.loads(self_test_resp.read().decode("utf-8"))
            self.assertTrue(self_test_api["ok"], msg=self_test_api["humanTable"])
            self.assertEqual(len(self_test_api["toolMatrix"]), len(TOOL_SPECS))
            self.assertTrue(all("durationSec" in row and row["calls"] > 0 for row in self_test_api["toolMatrix"]))

        # JSON-RPC batch call works cleanly
        st_b, batch_resp, _ = self.mcp_http([
            {"jsonrpc": "2.0", "id": "b1", "method": "ping"},
            {"jsonrpc": "2.0", "id": "b2", "method": "resources/list"},
            {"jsonrpc": "2.0", "id": "b3", "method": "prompts/list"},
        ], auth_mode="query")
        self.assertEqual(st_b, 200)
        self.assertEqual(len(batch_resp), 3)
        self.assertEqual(len(batch_resp[1]["result"]["resources"]), 11)
        self.assertEqual(len(batch_resp[2]["result"]["prompts"]), 7)

    def test_02_all_core_studio_tools_end_to_end(self):
        # 0. v5 bridge-level tools (no Studio peer required)
        reg = self.call_tool("agent_bus", {"action": "register", "name": "pytest-agent", "role": "qa"})
        self.assertTrue(reg["online"])
        posted = self.call_tool("agent_bus", {"action": "post", "agentId": reg["id"], "subject": "test", "body": "hello bus"})
        self.assertTrue(posted["id"].startswith("msg-"))
        task = self.call_tool("agent_bus", {"action": "task_create", "title": "v5 smoke", "agentId": reg["id"], "priority": "high"})
        self.assertEqual(task["status"], "todo")
        claimed = self.call_tool("agent_bus", {"action": "task_claim", "taskId": task["id"], "agentId": reg["id"]})
        self.assertEqual(claimed["status"], "in_progress")
        done = self.call_tool("agent_bus", {"action": "task_complete", "taskId": task["id"], "agentId": reg["id"], "result": "ok"})
        self.assertEqual(done["status"], "done")
        ops = self.call_tool("bridge_ops", {"action": "status"})
        self.assertEqual(ops["version"], "5.4.0")
        self.assertIn("tunnel", ops)
        metrics = self.call_tool("bridge_ops", {"action": "metrics"})
        self.assertIn("counters", metrics)
        verify = self.call_tool("bridge_ops", {"action": "audit_verify"})
        self.assertTrue(verify["ok"])
        fleet = self.call_tool("studio_fleet", {"action": "list"})
        self.assertGreaterEqual(fleet["count"], 1)
        self.assertTrue(fleet["studios"][0]["defaultTarget"])
        relabelled = self.call_tool("studio_fleet", {"action": "label", "peerId": fleet["studios"][0]["peerId"],
                                                     "label": "Primary Edit"})
        self.assertEqual(relabelled["label"], "Primary Edit")

        # 1. Session & Peers
        st = self.call_tool("get_studio_state")
        self.assertTrue(st["studioConnected"])
        peers = self.call_tool("list_studios")
        self.assertGreaterEqual(peers["onlineCount"], 1)
        ping = self.call_tool("ping_studio")
        self.assertEqual(ping["pluginVersion"], "4.1.0")

        # 2. Survey, Inspect, Search, Properties, Compare
        survey = self.call_tool("survey_place", {"includeSource": True})
        self.assertGreaterEqual(survey["totals"]["scripts"], 2)

        insp = self.call_tool("inspect_instance", {"path": "Workspace.Door", "depth": 2, "descendantSummary": True})
        self.assertEqual(insp["attributes"]["Locked"], True)
        self.assertIn("Interactable", insp["tags"])

        srch = self.call_tool("search_game_tree", {"isA": "LuaSourceContainer"})
        self.assertGreaterEqual(srch["count"], 2)

        props = self.call_tool("get_properties", {"paths": ["Workspace.Baseplate"]})
        self.assertEqual(props["count"], 1)

        # 3. Script CRUD, Line Slicing, Optimistic Hash Lock, Multi-Edit, Grep, Search, Replace, Fresh Require
        read1 = self.call_tool("script_read", {
            "path": "ServerScriptService.GameSystems.Config",
            "startLine": 1,
            "endLine": 3,
            "withLineNumbers": True,
        })
        self.assertIn("1 |", read1["source"])
        orig_hash = read1["hash"]

        # Stale hash lock must reject cleanly
        _, bad_hash_resp, _ = self.mcp_http({
            "jsonrpc": "2.0",
            "id": "stale-check",
            "method": "tools/call",
            "params": {
                "name": "multi_edit",
                "arguments": {
                    "path": "ServerScriptService.GameSystems.Config",
                    "expectedHash": "00000000",
                    "edits": [{"oldText": "DebugHud = true", "newText": "DebugHud = false"}],
                },
            },
        })
        self.assertTrue(bad_hash_resp["result"]["isError"])

        # Valid atomic multi_edit with expectedHash + syntax check
        edit_res = self.call_tool("multi_edit", {
            "path": "ServerScriptService.GameSystems.Config",
            "expectedHash": orig_hash,
            "replaceInstance": True,
            "edits": [{"oldText": "DebugHud = true", "newText": "DebugHud = false"}],
        })
        self.assertEqual(edit_res["editsApplied"], 1)
        self.assertNotEqual(edit_res["hash"], orig_hash)

        grep_res = self.call_tool("script_grep", {"pattern": "DebugHud = false"})
        self.assertEqual(grep_res["count"], 1)

        ssearch_res = self.call_tool("script_search", {"query": "cfg"})
        self.assertGreaterEqual(len(ssearch_res["matches"]), 0)

        batch_read = self.call_tool("read_scripts_batch", {"paths": ["ServerScriptService.GameSystems.Config"]})
        self.assertIn("DebugHud = false", batch_read["scripts"][0]["source"])

        fresh = self.call_tool("require_fresh", {"path": "ServerScriptService.GameSystems.Config"})
        self.assertEqual(fresh["value"]["freshHash"], edit_res["hash"])

        open_res = self.call_tool("open_script_in_editor", {"path": "ServerScriptService.GameSystems.Config", "line": 2})
        self.assertEqual(open_res["opened"], "ServerScriptService.GameSystems.Config")

        # 4. Find & Replace dry-run + live auto-snapshot + snapshot restore
        dry_rep = self.call_tool("find_and_replace_scripts", {
            "find": "MoveSpeed = 16.0",
            "replace": "MoveSpeed = 24.0",
            "root": "ServerScriptService.GameSystems",
            "dryRun": True,
        })
        self.assertEqual(dry_rep["totalReplacements"], 1)

        live_rep = self.call_tool("find_and_replace_scripts", {
            "find": "MoveSpeed = 16.0",
            "replace": "MoveSpeed = 24.0",
            "root": "ServerScriptService.GameSystems",
            "dryRun": False,
        })
        self.assertIn("snapshotId", live_rep)

        # Restore pre-replace snapshot!
        restored = self.call_tool("manage_snapshots", {"action": "restore", "snapshotId": live_rep["snapshotId"]})
        self.assertGreaterEqual(restored["restoredCount"], 1)
        after_restore = self.call_tool("script_read", {"path": "ServerScriptService.GameSystems.Config"})
        self.assertIn("MoveSpeed = 16.0", after_restore["source"])

        # 5. Build Tree, Clone Grid, Set/Mass-Set Properties, Reparent/Rename, Compare, Undo/Redo, Delete
        built = self.call_tool("build_tree", {
            "parent": "Workspace",
            "tree": [{
                "name": "Lamp",
                "className": "Part",
                "replaceExisting": True,
                "properties": {"Position": [0, 5, 0], "Anchored": True},
                "attributes": {"BrightnessLevel": 3},
                "tags": ["LightSource"],
            }],
        })
        self.assertEqual(built["created"], ["Workspace.Lamp"])

        clones = self.call_tool("clone_instance", {
            "path": "Workspace.Lamp",
            "newName": "Lamp_{i}",
            "count": 3,
            "offset": [10, 0, 0],
        })
        self.assertEqual(clones["count"], 3)

        cmp_res = self.call_tool("compare_instances", {"pathA": "Workspace.Lamp_1", "pathB": "Workspace.Lamp_2"})
        self.assertTrue(len(cmp_res["propertyDiffs"]) >= 1)

        self.call_tool("set_properties", {"path": "Workspace.Lamp_1", "properties": {"Material": "Neon"}})
        self.call_tool("mass_set_properties", {
            "paths": ["Workspace.Lamp_1", "Workspace.Lamp_2"],
            "relative": {"Position": [0, 2, 0]},
        })
        self.call_tool("reparent_or_rename", {
            "paths": ["Workspace.Lamp_3"],
            "newName": "Lamp_End",
        })

        # Attributes & Tags CRUD
        attr_set = self.call_tool("manage_attributes", {
            "action": "set",
            "paths": ["Workspace.Lamp_End"],
            "attributes": {"Voltage": 220},
        })
        self.assertEqual(attr_set["results"][0]["attributes"]["Voltage"], 220)

        tag_add = self.call_tool("manage_tags", {
            "action": "add",
            "paths": ["Workspace.Lamp_End"],
            "tags": ["SpecialLamp"],
        })
        self.assertIn("SpecialLamp", tag_add["results"][0]["tags"])
        tagged = self.call_tool("manage_tags", {"action": "find_tagged", "tag": "SpecialLamp"})
        self.assertEqual(tagged["total"], 1)

        # Selection, Camera, Terrain, Safe Asset Insertion
        sel = self.call_tool("manage_selection", {"action": "set", "paths": ["Workspace.Lamp_End"]})
        self.assertEqual(sel["count"], 1)
        cam = self.call_tool("manage_camera", {"action": "set", "position": [10, 20, 30], "lookAt": [0, 5, 0], "fov": 75})
        self.assertEqual(cam["fov"], 75.0)
        terr = self.call_tool("edit_terrain", {"action": "fill_block", "material": "Rock", "position": [0, -4, 0], "size": [16, 4, 16]})
        self.assertEqual(terr["material"], "Rock")
        asset = self.call_tool("insert_asset", {"assetId": 999888, "parent": "Workspace", "stripScripts": True})
        self.assertTrue(asset["scriptsStripped"])
        self.assertEqual(asset["embeddedScriptsFound"], 1)

        # Delete & Undo restores deleted instance!
        self.call_tool("delete_instance", {"paths": ["Workspace.Lamp_End"]})
        self.call_tool("undo_redo", {"action": "undo", "steps": 1})
        restored_lamp = self.call_tool("inspect_instance", {"path": "Workspace.Lamp_End", "depth": 0})
        self.assertEqual(restored_lamp["name"], "Lamp_End")

        # 6. Automated Playtest Scenario (spawns simulated Server + Client peers, runs evals, stops cleanly)
        scen = self.call_tool("run_playtest_scenario", {
            "mode": "play",
            "warmupSeconds": 0.2,
            "serverCode": "return {serverOk = true}",
            "clientCode": "return {clientOk = true}",
            "consoleLimit": 1,
        })
        self.assertTrue(scen["ok"])
        self.assertTrue(scen["sessionSurvived"])
        self.assertTrue(scen["clientRouteReady"])
        self.assertLessEqual(scen["consoleCount"], 1)
        self.assertEqual(scen["serverResult"]["dm"], "server")
        self.assertEqual(scen["clientResult"]["dm"], "client")

        # 7. Generic Local Package Sync (`src/` -> `ServerScriptService.App`) + Zero-Drift Verification!
        with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as pulled_dir:
            src_root = Path(src_tmp)
            (src_root / "Shared").mkdir(parents=True)
            (src_root / "Boot.server.lua").write_text("print('Booting generic game server')\n", encoding="utf-8")
            (src_root / "Hud.client.lua").write_text("print('Booting client HUD')\n", encoding="utf-8")
            (src_root / "Shared" / "Settings.lua").write_text(
                "return {\n\tMaxPlayers = 12,\n\tRoundSeconds = 180,\n}\n",
                encoding="utf-8",
            )

            push_res = self.call_tool("drift_and_sync", {
                "mode": "push",
                "localDir": str(src_root),
                "studioRoot": "ServerScriptService.App",
            })
            self.assertEqual(push_res["pushedScripts"], 3)

            drift_after = self.call_tool("drift_and_sync", {
                "mode": "check",
                "localDir": str(src_root),
                "studioRoot": "ServerScriptService.App",
            })
            self.assertTrue(drift_after["inSync"], msg=json.dumps(drift_after, indent=2))
            self.assertEqual(drift_after["modifiedCount"], 0)
            self.assertEqual(drift_after["localOnlyCount"], 0)
            self.assertEqual(drift_after["studioOnlyCount"], 0)

            pull_res = self.call_tool("drift_and_sync", {
                "mode": "pull",
                "localDir": pulled_dir,
                "studioRoot": "ServerScriptService.App",
            })
            self.assertEqual(pull_res["filesWritten"], 3)
            pull_drift = self.call_tool("drift_and_sync", {
                "mode": "check",
                "localDir": pulled_dir,
                "studioRoot": "ServerScriptService.App",
            })
            self.assertTrue(pull_drift["inSync"])

        # Resources read check + Control Center HTML + /api/local-boot + /api/agent-guide
        _, res_read, _ = self.mcp_http({
            "jsonrpc": "2.0",
            "id": "r1",
            "method": "resources/read",
            "params": {"uri": "studio://scripts/ServerScriptService.App.Shared.Settings"},
        })
        self.assertIn("RoundSeconds = 180", res_read["result"]["contents"][0]["text"])

        with urllib.request.urlopen(f"{self.base_url}/", timeout=5) as dash_resp:
            dash_html = dash_resp.read().decode("utf-8")
            self.assertIn("Next-Agent Handoff &amp; Prompts", dash_html)
            self.assertIn("Commands &amp; MCP Config", dash_html)
            self.assertIn("Helpful Tips &amp; Gotchas", dash_html)

        with urllib.request.urlopen(f"{self.base_url}/api/local-boot", timeout=5) as boot_resp:
            boot_data = json.loads(boot_resp.read().decode("utf-8"))
            self.assertTrue(boot_data["local"])
            self.assertEqual(boot_data["token"], self.token)
            self.assertEqual(len(boot_data["tools"]), len(TOOL_SPECS))

        with urllib.request.urlopen(f"{self.base_url}/api/agent-guide", timeout=5) as guide_resp:
            guide_data = json.loads(guide_resp.read().decode("utf-8"))
            self.assertEqual(guide_data["toolCounts"]["total"], len(TOOL_SPECS))
            self.assertGreaterEqual(len(guide_data["keyTips"]), 5)

        # 8. Direct Image/Audio Import, expanded asset catalog, 3D tools and bounded kit browsing
        kit_summary = self.call_tool("browse_kit", {})
        self.assertEqual(kit_summary["format"], "summary")
        self.assertNotIn("images", kit_summary)
        self.assertGreaterEqual(kit_summary["counts"]["images"], 400)
        image_page = self.call_tool("browse_kit", {"section": "images", "cursor": 0, "limit": 10})
        self.assertEqual(len(image_page["items"]), 10)
        self.assertTrue(image_page["hasMore"])
        self.assertTrue(all(item.get("license") == "CC0-1.0" for item in image_page["items"]))
        self.assertTrue(all(item.get("provenance") for item in image_page["items"]))
        asset_lock = json.loads((HERE.parent / "roblox-kit" / "assets" / "asset-lock.json").read_text(encoding="utf-8"))
        image_relpaths = [asset.get("relPath") for asset in asset_lock.get("images", {}).values() if asset.get("relPath")]
        self.assertEqual(len(image_relpaths), len(set(image_relpaths)), "Assets.lua index must not silently alias duplicate basenames")
        kit_all = self.call_tool("browse_kit", {"category": "all", "format": "full"})
        self.assertGreaterEqual(len(kit_all["skills"]), 16)
        self.assertGreaterEqual(len(kit_all["modules"]), 18)
        self.assertGreaterEqual(len(kit_all["systems"]), 14)
        self.assertGreaterEqual(len(kit_all["models"]), 20)
        self.assertGreaterEqual(len(kit_all["audio"]), 45)
        self.assertGreaterEqual(len(kit_all["templates"]), 6)
        self.assertGreaterEqual(len(kit_all["prefabs"]), 20)
        self.assertGreaterEqual(len(kit_all["images"]), 400)
        self.assertEqual(kit_all["cloudIconsCount"], 4811)

        icon_search = self.call_tool("search_cloud_icons", {"query": "shopping cart", "limit": 10})
        self.assertEqual(icon_search["totalCatalogIcons"], 4811)
        self.assertGreaterEqual(icon_search["returnedCount"], 1)
        self.assertTrue(icon_search["icons"][0]["uri"].startswith("rbxassetid://"))

        lucide_search = self.call_tool("search_cloud_icons", {"query": "shield", "pack": "lucide_fill", "limit": 10})
        self.assertGreaterEqual(lucide_search["returnedCount"], 1)
        self.assertEqual(lucide_search["icons"][0]["pack"], "lucide_fill")
        egg_search = self.call_tool("search_cloud_icons", {"query": "egg", "pack": "stud", "limit": 10})
        self.assertGreaterEqual(egg_search["returnedCount"], 1)
        self.assertGreaterEqual(len(egg_search["icons"][0]["variants"]), 1)

        # Multimodal 3D Viewport + HUD capture emits genuine MCP image/png content block!
        _, vp_raw, _ = self.mcp_http({
            "jsonrpc": "2.0",
            "id": "vp1",
            "method": "tools/call",
            "params": {"name": "capture_viewport", "arguments": {"preset": "perspective", "includeHud": True, "width": 480, "height": 270}},
        })
        vp_res = vp_raw["result"]
        self.assertFalse(vp_res["isError"])
        self.assertEqual(vp_res["content"][0]["type"], "image")
        self.assertEqual(vp_res["content"][0]["mimeType"], "image/png")
        import base64 as _b64_vp
        vp_png = _b64_vp.b64decode(vp_res["content"][0]["data"])
        self.assertEqual(vp_png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreaterEqual(vp_res["structuredContent"]["partsRendered"], 2)
        no_hud_default = self.call_tool("capture_viewport", {"width": 480, "height": 270, "returnInline": False})
        self.assertEqual(no_hud_default["hudElementsComposited"], 0)
        native = self.call_tool("capture_viewport", {"mode": "native"})
        self.assertFalse(native["ok"])
        self.assertEqual(native["reason"], "native_capture_unsafe")

        geometry = self.call_tool("export_geometry", {"path": "Workspace"})
        self.assertGreaterEqual(geometry["count"], 2)
        self.assertTrue(all(len(part["s"]) == 3 and len(part["r"]) == 9 for part in geometry["parts"]))
        offline = self.call_tool("render_offline", {"path": "Workspace", "width": 480, "height": 270, "camera": {"position": [20, 18, 26], "target": [0, 2, 0], "fov": 58}})
        self.assertEqual(offline["renderer"], "software_3d_hud_rasterizer")
        self.assertGreater(offline["facesRasterized"], 0)
        gui_shot = self.call_tool("screenshot_gui", {"path": "StarterGui", "width": 480, "height": 270})
        self.assertIn(gui_shot["measuredFrom"], ("derived", "client"))
        self.assertIn("safeArea", gui_shot)
        perf = self.call_tool("perf_audit", {"path": "Workspace"})
        self.assertGreaterEqual(perf["partCount"], 2)
        mesh_caps = self.call_tool("mesh_capabilities", {})
        self.assertEqual(mesh_caps["editableMesh"]["status"], "not_probed_by_mock")
        self.assertIn("Mock", mesh_caps["warnings"][0])
        caps = self.call_tool("playtest_capabilities", {})
        self.assertTrue(caps["ok"], msg=json.dumps(caps))
        self.assertTrue(caps["virtualInput"]["available"])
        self.assertTrue(caps["deviceSimulator"]["available"])
        self.assertTrue(caps["multiplayerTest"]["available"])
        self.assertEqual(caps["baselineVersions"]["VirtualInput"], "0.720")
        self.assertTrue(caps["studioVersion"])

        # Park the play state first: an earlier scenario may have left the
        # session alive (`keepAlive` defaults to true). With no playtest running,
        # input has nowhere to land and the bridge says so instead of returning a
        # green "delivered" it did not earn.
        self.call_tool("start_stop_play", {"action": "stop"})
        # `dm="edit"` pins the job to the Studio edit world, so a play peer left
        # over from an earlier scenario cannot answer it.
        refused = self.call_tool("simulate_input", {"steps": [{"action": "press", "key": "E"}],
                                                    "dm": "edit"})
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["delivered"], 0)
        self.assertEqual(refused["steps"][0]["reason"], "not_playing")
        self.assertTrue(any("playtest" in w for w in refused["warnings"]), msg=json.dumps(refused["warnings"]))

        self.call_tool("start_stop_play", {"action": "start_play"})
        try:
            live = self.call_tool("simulate_input", {"steps": [
                {"action": "press", "key": "W"},
                {"action": "click", "target": "Workspace.Door"},
                {"action": "type", "text": "hello"},
                {"action": "drag", "from": [10, 10], "to": [90, 60]},
                {"action": "pinch", "from": [10, 10], "to": [90, 60]},
            ]})
            self.assertTrue(live["ok"], msg=json.dumps(live))
            self.assertEqual(live["delivered"], 4, msg=json.dumps(live["steps"]))
            self.assertEqual(live["errors"], 1, msg=json.dumps(live["steps"]))
            self.assertIn("pinch", live["steps"][4]["error"])
            self.assertEqual(live["steps"][1]["via"], "SendMouseButton")
            self.assertEqual(live["steps"][3]["pathPoints"], 8)
            self.assertTrue(live["viewport"]["width"])
        finally:
            self.call_tool("start_stop_play", {"action": "stop"})

        dev_status = self.call_tool("manage_device_simulator", {"action": "status"})
        self.assertEqual(dev_status["current"]["deviceId"], "default")
        self.assertFalse(dev_status["current"]["simulating"])
        unapplied = self.call_tool("manage_device_simulator", {"action": "set_orientation", "orientation": "Portrait"})
        self.assertFalse(unapplied["applied"])
        self.assertEqual(unapplied["reason"], "no_active_device")
        listed = self.call_tool("manage_device_simulator", {"action": "list"})
        self.assertIn("iPhone15Pro", [d["deviceId"] for d in listed["devices"]])
        applied = self.call_tool("manage_device_simulator", {"action": "set_device", "deviceId": "iPhone15Pro"})
        self.assertTrue(applied["applied"])
        self.assertEqual(applied["current"]["resolution"], [1179, 2556])
        self.assertTrue(any("stays applied" in w for w in applied["warnings"]))
        overridden = self.call_tool("manage_device_simulator", {
            "action": "set", "orientation": "Portrait", "width": 800, "height": 600,
            "density": 160, "scalingMode": "ActualResolution",
        })
        self.assertEqual(overridden["current"]["orientation"], "Portrait")
        self.assertEqual(overridden["current"]["pixelDensity"], 160.0)
        unknown = self.call_tool("manage_device_simulator", {"action": "set_device", "deviceId": "NokiaN99"})
        self.assertEqual(unknown["reason"], "unknown_device")
        self.assertIn("iPhone15Pro", unknown["knownDevices"])
        profile = self.call_tool("manage_device_simulator", {"action": "create_profile", "profile": {
            "name": "Budget Phone", "width": 720, "height": 360, "pixelDensity": 160, "deviceForm": "Phone",
        }})
        self.assertEqual(profile["profile"]["Name"], "Budget Phone")
        self.assertTrue(profile["profile"]["IsCustom"])
        stopped = self.call_tool("manage_device_simulator", {"action": "stop"})
        self.assertTrue(stopped["stopped"])
        self.assertEqual(stopped["current"]["deviceId"], "default")

        self.call_tool("write_script", {"parent": "ServerStorage", "name": "ShopTests", "className": "ModuleScript",
                                        "source": "return { run = function(ctx) return { passed = 2, failed = 0 } end }"})
        scoped = self.call_tool("run_studio_tests", {"mode": "multiplayer", "players": 3,
                                                    "module": "ServerStorage.ShopTests", "collectClientLogs": True})
        self.assertTrue(scoped["ok"], msg=json.dumps(scoped))
        self.assertEqual(scoped["engineCall"], "StudioTestService:ExecuteMultiplayerTestAsync(3, args)")
        self.assertEqual(len(scoped["clientLogs"]), 3)
        self.assertGreaterEqual(scoped["passed"], 1)
        self.call_tool("delete_instance", {"paths": ["ServerStorage.ShopTests"]})
        bad_run = self.mcp_http({"jsonrpc": "2.0", "id": "tests-missing-module", "method": "tools/call",
                                 "params": {"name": "run_studio_tests",
                                            "arguments": {"mode": "run", "module": "ServerStorage.NotATest"}}})
        self.assertTrue(bad_run[1]["result"]["isError"])
        self.assertIn("test module not found", json.dumps(bad_run[1]["result"]))

        asset_search = self.call_tool("search_assets", {"query": "coin", "assetType": "image", "license": "CC0-1.0", "limit": 5})
        self.assertGreater(asset_search["totalMatches"], 0)
        self.assertTrue(all(item["license"] == "CC0-1.0" for item in asset_search["results"]))
        sky_search = self.call_tool("search_assets", {"assetType": "sky", "limit": 1})
        self.assertEqual(sky_search["results"][0]["thumbnailKind"], "equirectangular-panorama")
        self.assertIsNotNone(sky_search["nextCursor"])

        batch = self.call_tool("batch_write", {"files": [
            {"parent": "ServerStorage", "name": "BatchA", "className": "Script", "source": "return 1"},
            {"parent": "ServerStorage", "name": "BatchB", "className": "ModuleScript", "source": "return 2"},
        ]})
        self.assertEqual(batch["count"], 2)
        self.call_tool("delete_instance", {"paths": ["ServerStorage.BatchA", "ServerStorage.MissingScratch"]})
        probe = self.call_tool("probe", {"code": "return {ready=true}", "dm": "edit"})
        self.assertTrue(probe["ok"])
        peer_wait = self.call_tool("wait_for", {"peer": "edit", "condition": "peer_online", "timeout": 1})
        self.assertTrue(peer_wait["ok"])
        checked = self.call_tool("luau_check", {"code": "local value = 42\nreturn value"})
        self.assertTrue(checked["ok"])
        written = self.call_tool("write_script", {"parent": "ServerStorage", "name": "SelfTestScript", "className": "Script", "source": "return 'written'"})
        self.assertEqual(written["path"], "ServerStorage.SelfTestScript")
        self.assertIn("written", self.call_tool("script_read", {"path": "ServerStorage.SelfTestScript"})["source"])
        executed = self.call_tool("execute_luau", {"code": "return 'execute-ok'", "dm": "edit"})
        self.assertTrue(executed["ok"])
        console = self.call_tool("get_console_output", {"limit": 5, "source": "game", "dm": "edit"})
        self.assertLessEqual(len(console["lines"]), 5)
        self.call_tool("delete_instance", {"paths": ["ServerStorage.SelfTestScript"]})
        generated = self.call_tool("generate_procedural_image", {"preset": "icon_coin", "name": "self_test_transient", "width": 32, "height": 32, "saveToRepo": False})
        self.assertTrue(generated["ok"])
        sample_image = str(HERE.parent / "roblox-kit" / "assets" / "images" / "kenney_ui" / "blue_btn_depth_gloss.png")
        batched_images = self.call_tool("import_images_batch", {"files": [sample_image], "saveSubdir": "self_test", "syncAssetsModule": False})
        self.assertEqual(batched_images["importedCount"], 1)
        play_started = self.call_tool("start_stop_play", {"action": "start_run", "waitForPeer": True})
        self.assertTrue(play_started["started"])
        play_status = self.call_tool("start_stop_play", {"action": "status"})
        self.assertTrue(play_status["playtestActive"] or play_status["isRunning"])
        self.call_tool("start_stop_play", {"action": "stop"})
        stopped_scenario = self.call_tool("run_playtest_scenario", {"mode": "run", "warmupSeconds": 0.1, "keepAlive": False})
        self.assertTrue(stopped_scenario["ok"])
        self.assertTrue(stopped_scenario["sessionStopped"])
        self.assertFalse(stopped_scenario["sessionSurvived"])
        cloud_started = time.monotonic()
        cloud_passed = False
        try:
            _, cloud_error, _ = self.mcp_http({"jsonrpc": "2.0", "id": "cloud-no-key", "method": "tools/call", "params": {"name": "open_cloud_action", "arguments": {"action": "datastore_list", "universeId": 1}}})
            self.assertTrue(cloud_error["result"]["isError"])
            self.assertIn("Missing Open Cloud API key", cloud_error["result"]["content"][0]["text"])
            cloud_passed = True
        finally:
            record_tool_metric("open_cloud_action", time.monotonic() - cloud_started, cloud_passed)
        import os as _os
        if not _os.environ.get("ARENA_SELF_TEST_CHILD"):
            self_test = self.call_tool("self_test", {"includeStudioSmoke": False, "timeout": 120})
            self.assertTrue(self_test["ok"], msg=json.dumps(self_test.get("suite"), indent=2))
            self.assertEqual(len(self_test["toolMatrix"]), len(TOOL_SPECS))
            self.assertTrue(all(row["status"] == "pass" for row in self_test["toolMatrix"]))

        biome = self.call_tool("build_biome", {"preset": "tundra", "center": [0, 0, 0], "size": [24, 24], "tileSize": 12, "seed": 7, "decorationDensity": 0})
        self.assertEqual(biome["tiles"], 4)
        self.assertEqual(biome["created"][0], "Workspace.ArenaBiome")
        sky = self.call_tool("build_skybox", {"faces": {k: f"rbxassetid://{1000+i}" for i, k in enumerate(("bk", "dn", "ft", "lf", "rt", "up"))}, "atmosphere": {"Density": 0.25, "Color": [0.5, 0.6, 0.8]}})
        self.assertTrue(sky["atmosphereCreated"])
        _, bad_sky, _ = self.mcp_http({"jsonrpc": "2.0", "id": "bad-sky-nested", "method": "tools/call", "params": {"name": "build_skybox", "arguments": {"faces": {"bk": "x", "dn": "x", "ft": "x", "lf": "x", "rt": "x", "up": "x", "silentlyIgnored": True}}}})
        self.assertTrue(bad_sky["result"]["isError"])
        self.assertIn("unknown", bad_sky["result"]["structuredContent"]["error"])
        self.call_tool("build_tree", {"parent": "Workspace", "tree": [{"name": "ScatterTemplate", "className": "Part", "properties": {"Anchored": True, "Position": [0, 2, 0]}}], "select": False})
        scattered = self.call_tool("scatter_props", {"sourcePath": "Workspace.ScatterTemplate", "center": [10, 2, 10], "radius": 12, "count": 4, "minSpacing": 0, "seed": 11})
        self.assertEqual(scattered["count"], 4)
        self.assertEqual(scattered["requestedCount"], 4)

        # 3D Scene & Physics Auditor catches unanchored parts and auto-fixes cleanly
        self.call_tool("build_tree", {
            "parent": "Workspace",
            "tree": [{
                "name": "LooseTestCrate",
                "className": "Part",
                "properties": {
                    "Anchored": False,
                    "Size": {"__t": "Vector3", "v": [4, 4, 4]},
                    "Position": {"__t": "Vector3", "v": [6, 5.5, 6]},
                },
            }],
        })
        scene_check = self.call_tool("audit_3d_scene", {"path": "Workspace"})
        self.assertIn("Workspace.LooseTestCrate", scene_check["unanchoredPaths"])
        scene_fixed = self.call_tool("audit_3d_scene", {"path": "Workspace", "autoFixUnanchored": True, "snapFloating": True})
        self.assertEqual(scene_fixed["errorCount"], 0)
        self.assertGreaterEqual(scene_fixed["autoFixedUnanchored"], 1)

        # Full .rbxlx Place XML export + re-import round-trip
        with tempfile.TemporaryDirectory() as rbxlx_tmp:
            place_xml = Path(rbxlx_tmp) / "FullPlace.rbxlx"
            exp_place = self.call_tool("export_place_rbxlx", {"outputFile": str(place_xml), "offlineStarterPlace": True})
            self.assertTrue(place_xml.is_file())
            self.assertGreater(exp_place["bytes"], 5000)
            imp_place = self.call_tool("import_place_rbxlx", {"filePath": str(place_xml)})
            self.assertIn("Workspace", imp_place["servicesSynced"])

        _, skill_read, _ = self.mcp_http({
            "jsonrpc": "2.0",
            "id": "sk1",
            "method": "resources/read",
            "params": {"uri": "skill://roblox-stud-ui-design"},
        })
        self.assertIn("StudUI", skill_read["result"]["contents"][0]["text"])

        with urllib.request.urlopen(f"{self.base_url}/api/kit/image/kenney_ui/blue_btn_depth_gloss.png", timeout=5) as png_resp:
            png_bytes = png_resp.read()
            self.assertEqual(png_bytes[:8], b"\x89PNG\r\n\x1a\n")

        with urllib.request.urlopen(f"{self.base_url}/api/kit/audio/click_001.wav", timeout=5) as wav_resp:
            wav_bytes = wav_resp.read()
            self.assertEqual(wav_bytes[:4], b"RIFF")

        aud_res = self.call_tool("import_audio", {"repoAudio": "click_001.wav", "targetPath": "SoundService"})
        self.assertEqual(aud_res["studioCreated"], ["SoundService.click_001"])

        sys_res = self.call_tool("install_dropin_system", {"system": "liveops/codes"})
        self.assertIn("liveops/codes", sys_res["systemsInstalled"])
        self.assertGreaterEqual(sys_res["installedCount"], 3)

        ui_audit = self.call_tool("audit_ui_layout", {"prefab": "ui_kenney_shop_window"})
        self.assertIn("mobile", ui_audit["viewports"])
        self.assertGreaterEqual(ui_audit["viewports"]["desktop"]["elementCount"], 5)

        with tempfile.TemporaryDirectory() as rbx_tmp:
            out_rbx = Path(rbx_tmp) / "exported_shop.rbxmx"
            exp_res = self.call_tool("export_rbxmx", {"prefab": "ui_kenney_shop_window", "outputFile": str(out_rbx)})
            self.assertTrue(out_rbx.is_file())
            self.assertGreater(exp_res["bytes"], 500)
            imp_rbx = self.call_tool("import_rbxmx", {"filePath": str(out_rbx), "parent": "StarterGui"})
            self.assertEqual(len(imp_rbx["created"]), 1)

        import base64 as _b64
        from image_pipeline import encode_png_rgba, generate_procedural_rgba
        w, h, rgba = generate_procedural_rgba("icon_gem", 32, 32)
        b64_uri = "data:image/png;base64," + _b64.b64encode(encode_png_rgba(w, h, rgba)).decode("ascii")

        imp_res = self.call_tool("import_image", {
            "base64Data": b64_uri,
            "name": "test_badge_gem",
            "saveToRepo": False,
            "useEditableImage": True,
            "targetPath": "Workspace.Baseplate",
            "applyMode": "billboard",
        })
        self.assertEqual(imp_res["width"], 32)
        self.assertTrue(imp_res["studioApplied"]["editableCreated"])
        self.assertEqual(imp_res["studioApplied"]["appliedTo"], "Workspace.Baseplate.test_badge_gem.Icon")

        mod_res = self.call_tool("install_kit_module", {"module": "all", "parent": "ReplicatedStorage.Shared"})
        self.assertGreaterEqual(mod_res["count"], 12)

        prefab_res = self.call_tool("spawn_prefab", {"prefab": "ui_kenney_shop_window"})
        self.assertEqual(prefab_res["created"], ["StarterGui.KenneyShopGui"])

        tmpl_res = self.call_tool("install_template", {"template": "full_stud_ui_suite"})
        self.assertEqual(len(tmpl_res["installedModules"]), 5)
        self.assertEqual(len(tmpl_res["installedScripts"]), 2)

    def test_03_mcp_stdio_ndjson_and_lsp_framing_plus_readonly_mode(self):
        env = dict(os.environ)
        env["BRIDGE_URL"] = self.base_url
        env["BRIDGE_TOKEN"] = self.token

        # Spawn real mcp_server.py process in --read-only Inspector mode attached to live server
        proc = subprocess.Popen(
            [sys.executable, str(HERE / "mcp_server.py"), "--port", str(self.port), "--read-only"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            # 1) Send newline-delimited JSON-RPC initialize + ping_studio read tool + blocked write_script
            reqs = [
                {"jsonrpc": "2.0", "id": 10, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "ping_studio", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {
                    "name": "manage_attributes",
                    "arguments": {"action": "set", "paths": ["Workspace.Door"], "attributes": {"Hack": 1}},
                }},
            ]
            for r in reqs:
                proc.stdin.write((json.dumps(r) + "\n").encode("utf-8"))
            proc.stdin.flush()

            responses = {}
            for _ in range(3):
                line = proc.stdout.readline().decode("utf-8").strip()
                msg = json.loads(line)
                responses[msg["id"]] = msg

            self.assertIn("Read-Only Inspector", responses[10]["result"]["instructions"])
            self.assertFalse(responses[11]["result"]["isError"])
            self.assertTrue(responses[12]["result"]["isError"])
            self.assertIn("Blocked by Read-Only Inspector", responses[12]["result"]["content"][0]["text"])

            # 2) Also verify LSP Content-Length header framing works cleanly on the exact same server!
            lsp_payload = json.dumps({"jsonrpc": "2.0", "id": 99, "method": "ping"}).encode("utf-8")
            proc.stdin.write(f"Content-Length: {len(lsp_payload)}\r\n\r\n".encode("ascii") + lsp_payload)
            proc.stdin.flush()

            hdr_line = proc.stdout.readline().decode("ascii").strip()
            self.assertTrue(hdr_line.lower().startswith("content-length:"))
            body_len = int(hdr_line.split(":")[1].strip())
            blank = proc.stdout.readline()
            self.assertEqual(blank.strip(), b"")
            lsp_body = json.loads(proc.stdout.read(body_len).decode("utf-8"))
            self.assertEqual(lsp_body["id"], 99)
        finally:
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()
            proc.terminate()
            proc.wait(timeout=5)

    def test_05_mcp_stdio_standalone_embedded_bridge(self):
        fresh_port = _free_port()
        env = dict(os.environ)
        env.pop("BRIDGE_URL", None)
        env["BRIDGE_TOKEN"] = "embedded-tok-123"

        proc = subprocess.Popen(
            [sys.executable, str(HERE / "mcp_server.py"), "--port", str(fresh_port)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            proc.stdin.write((json.dumps({
                "jsonrpc": "2.0",
                "id": 201,
                "method": "tools/call",
                "params": {"name": "get_studio_state", "arguments": {}},
            }) + "\n").encode("utf-8"))
            proc.stdin.flush()

            line = proc.stdout.readline().decode("utf-8").strip()
            msg = json.loads(line)
            self.assertEqual(msg["id"], 201)
            self.assertFalse(msg["result"]["isError"])
            self.assertEqual(msg["result"]["structuredContent"]["bridge"]["mcp_tools"], len(TOOL_SPECS))
        finally:
            proc.stdin.close()
            proc.stdout.close()
            proc.stderr.close()
            proc.terminate()
            proc.wait(timeout=5)

    def test_04_cli_utf8_round_trip(self):
        env = dict(os.environ, BRIDGE_URL=self.base_url, BRIDGE_TOKEN=self.token)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".lua", encoding="utf-8", delete=False) as tf:
            # Contains non-ASCII em-dash (—) and middle-dot (·) that previously broke on Windows cp1252!
            tf.write("-- Pale Watcher — phase · 1\nreturn { title = 'Watcher — Phase · 1' }\n")
            tf_path = tf.name
        try:
            proc = subprocess.run(
                [sys.executable, str(HERE / "arena_studio.py"), "script", "ServerScriptService", "Utf8Probe", tf_path, "--class", "ModuleScript"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                timeout=20,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
            read_back = self.call_tool("script_read", {"path": "ServerScriptService.Utf8Probe"})
            self.assertIn("Watcher — Phase · 1", read_back["source"])
        finally:
            os.unlink(tf_path)




class TestV54ConfirmationGate(unittest.TestCase):
    """v5.4.0: MRTR confirmations, subscription streams and call provenance."""

    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.token = "gate-secret-v54"
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.state_dir = tempfile.mkdtemp(prefix="arena-v54-state-")
        cls.httpd, cls.state = create_http_server(
            host="127.0.0.1", port=cls.port, token=cls.token, state_dir=cls.state_dir,
            require_confirm="write", confirm_ttl_seconds=120, confirm_wait_seconds=6,
        )
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.stop_mock = threading.Event()
        cls.mock_thread = threading.Thread(target=run_mock_loop,
                                           args=(cls.base_url, cls.token, cls.stop_mock), daemon=True)
        cls.mock_thread.start()
        for _ in range(60):
            if cls.state.direct_dispatch("health", {})["studio_connected"]:
                break
            threading.Event().wait(0.05)
        cls.dispatcher = cls.state.mcp_dispatcher
        cls.modern = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
        cls.caps = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {
                        "extensions": {"io.modelcontextprotocol/tasks": {}}, "elicitation": {}}}

    @classmethod
    def tearDownClass(cls):
        cls.stop_mock.set()
        cls.state._reaper_stop.set()
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        # Confirmations and "remember this" allowances live on the shared
        # dispatcher, so each test starts from a clean registry.
        gate = self.dispatcher.gate
        with gate._lock:
            gate._pending.clear()
            gate._allowances.clear()
            gate._decisions.clear()
            gate._consumed.clear()
        with self.dispatcher._sub_lock:
            self.dispatcher.subscriptions.clear()

    # -- helpers ---------------------------------------------------------

    def call(self, msg_id, method, params=None, meta=None):
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method, "_meta": dict(meta or self.modern)}
        if params is not None:
            msg["params"] = params
        return self.dispatcher.handle_payload(msg)

    def gated_call(self, args=None, extra_params=None, meta=None, principal=None):
        params = {"name": "delete_instance", "arguments": args or {"paths": ["Workspace.Baseplate"]}}
        if extra_params:
            params.update(extra_params)
        if principal:
            meta = dict(meta or self.modern)
            meta["x-arena-principal"] = principal
        return self.call("gated", "tools/call", params, meta=meta)

    def rest(self, path, method="GET", body=None):
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw

    def answer(self, request_id, action="approve", **extra):
        body = {"action": action, "requestId": request_id}
        body.update(extra)
        return self.rest("/api/confirmations", "POST", body)

    # -- policy ----------------------------------------------------------

    def test_gate_is_off_unless_the_operator_turns_it_on(self):
        from mcp_core import MCPDispatcher
        default = MCPDispatcher(self.state.mcp_client)
        self.assertFalse(default.gate.enabled)
        listed = default.handle_payload({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        flagged = [t["name"] for t in listed["result"]["tools"]
                   if (t.get("execution") or {}).get("confirmationRequired")]
        self.assertEqual(flagged, [])
        mine = self.dispatcher.handle_payload({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        gated = {t["name"] for t in mine["result"]["tools"]
                 if (t.get("execution") or {}).get("confirmationRequired")}
        self.assertIn("delete_instance", gated)
        self.assertNotIn("get_studio_state", gated)

    def test_reads_stay_free_in_write_mode_even_on_write_capable_tools(self):
        """Mode `write` gates writes, not tools that happen to be able to write.
        `manage_device_simulator`/`bridge_ops` are reads when their action is a
        read; gating those would leave a locked-down bridge blind to its own state
        (and unable to answer the very queue it is waiting on)."""
        free = (
            ("manage_device_simulator", {"action": "status"}),
            ("bridge_ops", {"action": "status"}),
            ("playtest_capabilities", {}),
            ("get_studio_state", {}),
        )
        for index, (name, arguments) in enumerate(free, start=1):
            res = self.call(f"free-{index}", "tools/call", {"name": name, "arguments": arguments})
            self.assertNotIn("error", res, name)
            self.assertNotEqual(res["result"].get("resultType"), "input_required", name)
        # The same tool, asked to mutate, is gated.
        mutate = self.call("mut", "tools/call", {
            "name": "manage_device_simulator",
            "arguments": {"action": "set_device", "deviceId": "iPhone15Pro"},
        })
        self.assertEqual(mutate["result"]["resultType"], "input_required")
        self.assertEqual(mutate["result"]["structuredContent"]["tool"], "manage_device_simulator")
        self.assertEqual(self.dispatcher.gate.status()["pendingCount"], 1)

    def test_gate_status_reports_the_policy_in_force(self):
        from confirm_gate import ConfirmGate, GATE_VERSION
        status = self.dispatcher.gate.status()
        self.assertEqual(status["mode"], "write")
        self.assertEqual(status["gateScope"], "every tool that is not annotated readOnlyHint")
        self.assertEqual(status["gatedTools"], [], "only `destructive` names a fixed list")
        self.assertGreater(status["destructiveToolCount"], 0)
        self.assertEqual(status["version"], GATE_VERSION)
        off = ConfirmGate(mode="off")
        self.assertEqual(off.status()["gateScope"], "nothing")
        self.assertFalse(off.enabled)

    def test_write_mode_leaves_reads_free_and_all_mode_gates_everything(self):
        from confirm_gate import ConfirmGate
        from mcp_core import MCPDispatcher
        strict = MCPDispatcher(self.state.mcp_client, gate=ConfirmGate(mode="all"))
        strict_result = strict.handle_payload({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "_meta": dict(self.modern),
            "params": {"name": "get_studio_state", "arguments": {}}})["result"]
        self.assertEqual(strict_result["resultType"], "input_required",
                         "`all` is the lockdown mode: even a read needs a person")
        for name in ("get_studio_state", "ping_studio", "playtest_capabilities"):
            res = self.call(name, "tools/call", {"name": name, "arguments": {}})["result"]
            self.assertNotEqual(res.get("resultType"), "input_required", msg=name)
            self.assertFalse(res.get("isError"), msg=name)
        self.assertEqual(self.dispatcher.gate.status()["pending"], [])

    def test_modern_gated_call_answers_input_required_with_a_signed_state(self):
        res = self.gated_call()["result"]
        self.assertEqual(res["resultType"], "input_required")
        self.assertEqual(list(res["inputRequests"]), ["confirm"])
        elicit = res["inputRequests"]["confirm"]["params"]
        self.assertEqual(elicit["mode"], "form")
        self.assertEqual(elicit["requestedSchema"]["required"], ["decision"])
        self.assertIn("Workspace.Baseplate", elicit["message"])
        self.assertEqual(res["structuredContent"]["reason"], "confirmation_required")
        self.assertIn("requestId", res["structuredContent"])
        self.assertIn("io.modelcontextprotocol/serverInfo", res["_meta"])
        state = self.dispatcher.gate.parse_state(res["requestState"])
        self.assertEqual(state["aud"], "tools/call")
        self.assertAlmostEqual(state["exp"], state["iat"] + 120, places=0)
        with self.assertRaises(Exception) as ctx:
            self.dispatcher.gate.parse_state(res["requestState"][:-2] + "aa")
        self.assertEqual(ctx.exception.reason, "bad_signature")

    def test_an_approval_belongs_to_the_principal_it_was_issued_to(self):
        res = self.gated_call()["result"]
        state = res["requestState"]
        self.answer(res["structuredContent"]["requestId"])
        # The approval is recorded for `owner`; another caller cannot spend it.
        other = self.gated_call(principal="other-agent", extra_params={
            "requestState": state,
            "inputResponses": {"confirm": {"action": "accept", "content": {"decision": "approve"}}},
        })
        self.assertEqual(other["result"]["resultType"], "input_required")
        self.assertEqual(other["result"]["structuredContent"]["reason"], "principal_mismatch")
        # It survives for its owner, exactly once.
        mine = self.gated_call(extra_params={"requestState": state,
                                            "inputResponses": {"confirm": {"action": "accept"}}})
        self.assertNotEqual(mine["result"].get("resultType"), "input_required")
        third = self.gated_call(extra_params={"requestState": state,
                                             "inputResponses": {"confirm": {"action": "accept"}}})
        self.assertEqual(third["result"]["structuredContent"]["reason"], "replayed")

    def test_request_state_is_bound_to_arguments_and_single_use(self):
        first = self.gated_call()["result"]
        state = first["requestState"]
        swapped = self.gated_call(args={"paths": ["Workspace.Treasure"]}, extra_params={
            "requestState": state,
            "inputResponses": {"confirm": {"action": "accept", "content": {"decision": "approve"}}},
        })
        self.assertEqual(swapped["result"]["structuredContent"]["reason"], "digest_mismatch")
        self.answer(first["structuredContent"]["requestId"])
        ran = self.gated_call(extra_params={"requestState": state,
                                            "inputResponses": {"confirm": {"action": "accept"}}})
        self.assertNotEqual(ran["result"].get("resultType"), "input_required")
        replay = self.gated_call(extra_params={"requestState": state,
                                               "inputResponses": {"confirm": {"action": "accept"}}})
        self.assertEqual(replay["result"]["resultType"], "input_required")
        self.assertEqual(replay["result"]["structuredContent"]["reason"], "replayed")
        # The re-prompt is a *new* request: the fresh token is not approved yet.
        fresh = replay["result"]["structuredContent"]["requestId"]
        self.assertNotEqual(fresh, first["structuredContent"]["requestId"])

    def test_a_client_that_cannot_ask_a_human_never_self_approves(self):
        res = self.gated_call()["result"]
        blind_caps = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                      "io.modelcontextprotocol/clientCapabilities": {}}
        retry = self.gated_call(meta=blind_caps, extra_params={
            "requestState": res["requestState"],
            "inputResponses": {"confirm": {"action": "accept"}},
        })
        self.assertEqual(retry["result"]["resultType"], "input_required")
        self.assertIn("arena_studio.py approve", retry["result"]["structuredContent"]["note"])
        self.answer(res["structuredContent"]["requestId"], "deny")

    def test_operator_approval_through_the_cli_unblocks_a_legacy_call(self):
        # Legacy clients have no MRTR, so the bridge waits and a human answers
        # out-of-band — here through arena_studio.py itself, end to end.
        outcome = {}

        def run_legacy():
            meta = {"io.modelcontextprotocol/protocolVersion": "2025-11-25"}
            outcome["res"] = self.call("legacy", "tools/call", {
                "name": "write_script",
                "arguments": {"parent": "ServerStorage", "name": "GatedByOperator",
                              "className": "ModuleScript", "source": "return 'written under approval'"},
            }, meta=meta)

        thread = threading.Thread(target=run_legacy)
        thread.start()
        request_id = None
        for _ in range(200):
            pending = self.dispatcher.gate.status()["pending"]
            if pending:
                request_id = pending[0]["requestId"]
                break
            time.sleep(0.02)
        self.assertIsNotNone(request_id, "the call should be parked on a confirmation")
        proc = subprocess.run(
            [sys.executable, str(HERE / "arena_studio.py"), "approve", request_id, "--note", "pair-tested"],
            capture_output=True, text=True, timeout=90, cwd=str(HERE),
            env={**os.environ, "BRIDGE_URL": self.base_url, "BRIDGE_TOKEN": self.token},
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("approved", proc.stdout)
        thread.join(40)
        self.assertNotIn("error", outcome["res"], msg=json.dumps(outcome["res"]))
        written = outcome["res"]["result"]["structuredContent"]
        self.assertTrue(written["ok"], msg=json.dumps(written))
        read_back = self.call("read", "tools/call", {"name": "script_read", "arguments": {
            "path": "ServerStorage.GatedByOperator"}})
        self.assertIn("written under approval", read_back["result"]["structuredContent"]["source"])
        self.call("cleanup", "tools/call", {"name": "delete_instance",
                                            "arguments": {"paths": ["ServerStorage.GatedByOperator"]}})
        for row in self.dispatcher.gate.status()["pending"]:
            self.answer(row["requestId"])
        self.assertEqual(self.dispatcher.gate.status()["pending"], [])

    def test_legacy_wait_timeout_names_the_request_id(self):
        from confirm_gate import ConfirmGate
        from mcp_core import MCPDispatcher
        short = MCPDispatcher(self.state.mcp_client, gate=ConfirmGate(mode="all", ttl_seconds=30),
                              legacy_wait_seconds=0.4)
        err = short.handle_payload({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                    "params": {"name": "delete_instance",
                                               "arguments": {"paths": ["Workspace.Baseplate"]}}})
        self.assertTrue(err["result"]["isError"])
        detail = err["result"]["structuredContent"]
        self.assertEqual(detail["reason"], "confirmation_timeout")
        self.assertIn("requestId", detail)
        # a dispatcher built outside the server has its own queue, so check it
        # directly rather than through /api/confirmations.
        pending = short.gate.status()["pending"]
        self.assertEqual([r["requestId"] for r in pending], [detail["requestId"]])
        denied = short.gate.resolve(detail["requestId"], {"approve": False, "note": "no"},
                                    source="operator")
        self.assertEqual(denied["state"], "denied")

    def test_deny_is_final_counted_and_audited(self):
        res = self.gated_call()["result"]
        request_id = res["structuredContent"]["requestId"]
        denied = self.answer(request_id, "deny", note="not in this place")
        self.assertEqual(denied["state"], "denied")
        retry = self.gated_call(extra_params={"requestState": res["requestState"],
                                             "inputResponses": {"confirm": {"action": "decline"}}})
        self.assertEqual(retry["result"]["structuredContent"]["reason"], "declined")
        counters = self.dispatcher.gate.status()["counters"]
        self.assertGreaterEqual(counters["denied"], 1)
        audit = self.rest("/api/audit?limit=60")
        self.assertIn("mcp.confirm.denied", [e["action"] for e in audit["entries"]])
        events = self.rest("/api/events?once=1&since=0&limit=200")
        kinds = [e["kind"] for e in (events.get("events") or [])]
        self.assertIn("mcp.confirm.denied", kinds)

    def test_gated_task_flips_to_input_required_and_tasks_update_answers_it(self):
        handle = self.gated_call(extra_params={"task": {"ttl": 60000}}, meta=self.caps)["result"]
        self.assertEqual(handle["resultType"], "task")
        self.assertEqual(handle["status"], "working")
        task_id = handle["taskId"]
        view = {}
        for _ in range(200):
            view = self.call("get", "tasks/get", {"taskId": task_id}, meta=self.caps)["result"]
            if view["status"] != "working":
                break
            time.sleep(0.05)
        self.assertEqual(view["status"], "input_required", msg=json.dumps(view))
        self.assertEqual(list(view["inputRequests"]), ["confirm"])
        blind = self.call("blind-update", "tasks/update",
                          {"taskId": task_id, "inputResponses": {"confirm": {"action": "accept"}}},
                          meta={"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                "io.modelcontextprotocol/clientCapabilities": {}})
        self.assertEqual(blind["error"]["code"], -32021)
        self.assertEqual(blind["error"]["data"]["requiredCapability"], "elicitation")
        answered = self.call("update", "tasks/update", {"taskId": task_id, "inputResponses": {
            "confirm": {"action": "accept", "content": {"decision": "approve", "remember": True}}}}, meta=self.caps)
        self.assertEqual(answered["result"]["decision"], "approved")
        for _ in range(200):
            view = self.call("get2", "tasks/get", {"taskId": task_id}, meta=self.caps)["result"]
            if view["status"] in ("completed", "failed"):
                break
            time.sleep(0.05)
        self.assertIn(view["status"], ("completed", "failed"), msg=json.dumps(view))
        self.assertTrue(self.dispatcher.gate.allowance_active("delete_instance", "owner"),
                        "remember=True must open a documented allowance window")
        # Inside the allowance the same tool runs without asking again.
        quiet = self.gated_call(args={"paths": ["ServerStorage.Nonexistent"]})
        self.assertNotEqual(quiet["result"].get("resultType"), "input_required")

    def test_cancel_and_expiry_leave_the_gate_consistent(self):
        res = self.gated_call()["result"]
        request_id = res["structuredContent"]["requestId"]
        self.assertTrue(self.dispatcher.gate.cancel(request_id, reason="test cancel"))
        self.assertFalse(self.dispatcher.gate.cancel(request_id, reason="again"))
        retry = self.gated_call(extra_params={"requestState": res["requestState"],
                                             "inputResponses": {"confirm": {"action": "accept"}}})
        # A cancelled request's token is spent, so the caller is re-prompted.
        self.assertEqual(retry["result"]["resultType"], "input_required")
        self.assertEqual(retry["result"]["structuredContent"]["reason"], "replayed")
        pending = self.dispatcher.gate.status()["pending"]
        self.assertEqual([r["requestId"] for r in pending],
                         [retry["result"]["structuredContent"]["requestId"]])
        again = self.gated_call(extra_params={"requestState": retry["result"]["requestState"]})["result"]
        self.assertEqual(again["resultType"], "input_required")
        # Nothing is stale yet, so a sweep is a no-op...
        self.assertEqual(self.rest("/api/confirmations", "POST", {"action": "expire"})["swept"], 0)
        # ...and the reaper's sweep fires once the window closes (clock forced).
        rec = self.dispatcher.gate.pending(again["structuredContent"]["requestId"])
        rec.expires = time.time() - 1
        self.assertEqual(self.rest("/api/confirmations", "POST", {"action": "expire"})["swept"], 1)
        self.assertGreaterEqual(self.dispatcher.gate.status()["counters"]["expired"], 1)
        self.assertEqual(self.dispatcher.gate.status()["pending"], [])

    def test_ops_health_and_metrics_expose_the_gate(self):
        health = self.rest("/api/health")
        self.assertEqual(health["confirmGate"]["mode"], "write")
        self.assertIn("counters", health["confirmGate"])
        self.assertNotIn("pending", health["confirmGate"],
                         "the open health endpoint must not leak argument previews")
        ops = self.rest("/api/ops")
        gate_ops = ops["mcp"]["confirmGate"]
        self.assertEqual(gate_ops["mode"], "write")
        self.assertTrue(gate_ops["enabled"])
        self.assertIn("pending", gate_ops)
        self.assertIn("allowances", gate_ops)
        self.assertIn("subscriptions", ops["mcp"])
        text = self.rest("/api/metrics?format=prometheus")
        self.assertIsInstance(text, str)
        self.assertIn("bridge_confirmations_pending", text)
        self.assertIn("bridge_mcp_subscriptions", text)

    def test_bridge_ops_reads_the_queue_but_cannot_unblock_itself(self):
        res = self.gated_call()["result"]
        request_id = res["structuredContent"]["requestId"]
        listed = self.call("ops-list", "tools/call", {"name": "bridge_ops", "arguments": {"action": "confirmations"}})
        pending = listed["result"]["structuredContent"].get("pending") or []
        self.assertIn(request_id, [row["requestId"] for row in pending])
        self.assertTrue(listed["result"]["structuredContent"]["ok"])
        self_answer = self.call("ops-approve", "tools/call", {"name": "bridge_ops", "arguments": {
            "action": "confirm_approve", "requestId": request_id, "note": "let me in"}})
        refusal = self_answer["result"]["structuredContent"]
        self.assertFalse(refusal["ok"])
        self.assertEqual(refusal["reason"], "operator_only")
        self.assertTrue(any("arena_studio.py approve" in hint for hint in refusal["howToAnswer"]))
        # Still pending: the refusal changed nothing.
        self.assertIn(request_id, [row["requestId"] for row in
                                  self.dispatcher.gate.status()["pending"]])
        # A different principal on the same bridge may answer it.
        other = self.call("ops-approve-other", "tools/call", {"name": "bridge_ops", "arguments": {
            "action": "confirm_approve", "requestId": request_id, "note": "reviewed"}})
        # the caller principal comes from the connection, so this client is still
        # `owner`; the operator path (REST, admin scope) is the one that works.
        self.assertEqual(other["result"]["structuredContent"]["reason"], "operator_only")
        self.answer(request_id)
        ran = self.gated_call(extra_params={"requestState": res["requestState"],
                                           "inputResponses": {"confirm": {"action": "accept"}}})
        self.assertNotEqual(ran["result"].get("resultType"), "input_required")
        subs = self.call("ops-subs", "tools/call", {"name": "bridge_ops", "arguments": {"action": "subscriptions"}})
        self.assertIn("subscriptions", subs["result"]["structuredContent"])

    def test_subscriptions_listen_pumps_resource_updates(self):
        from mcp_core import take_subscription_stream
        self.state.publish_mcp_event("console.output", {"message": "before the subscription"})
        ack = self.call("sub", "subscriptions/listen", {"types": ["resourceSubscriptions", "toolsListChanged"],
                                                        "maxSeconds": 0.35, "pollSeconds": 0.05})
        self.assertNotIn("error", ack)
        marker = take_subscription_stream(ack)
        self.assertTrue(marker and marker.get("subscriptionId"))
        self.assertNotIn("_arenaStream", ack["result"])
        live = self.rest("/api/subscriptions")
        self.assertGreaterEqual(live["count"], 1)
        self.assertEqual(live["subscriptions"][0]["types"], ["toolsListChanged", "resourceSubscriptions"])
        self.assertIn("studio://state", live["subscriptions"][0]["resources"])

        frames = []
        report = self.dispatcher.pump_subscription(marker["subscriptionId"], frames.append,
                                                   max_seconds=0.5, poll_seconds=0.05)
        self.assertTrue(report["ok"])
        self.assertGreaterEqual(report["emitted"], 1)
        tagged = {f["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] for f in frames}
        self.assertEqual(tagged, {marker["subscriptionId"]})
        # a finished `maxSeconds` pump retires the subscription on its own
        after = self.rest("/api/subscriptions")
        self.assertEqual(after["count"], 0)
        closed = self.rest("/api/subscriptions/close", "POST", {"subscriptionId": marker["subscriptionId"]})
        self.assertFalse(closed["dropped"], "the pump already retired it")
        self.assertFalse(self.rest("/api/subscriptions/close", "POST",
                                   {"subscriptionId": "sub-nope"})["dropped"])
        # an explicit close works on a live one
        long_lived = self.call("sub2", "subscriptions/listen",
                               {"types": ["toolsListChanged"], "maxSeconds": 60, "pollSeconds": 5})
        second = take_subscription_stream(long_lived)
        self.assertEqual(self.rest("/api/subscriptions")["count"], 1)
        self.assertTrue(self.rest("/api/subscriptions/close", "POST",
                                  {"subscriptionId": second["subscriptionId"]})["dropped"])
        self.assertEqual(self.rest("/api/subscriptions")["count"], 0)
        bad = self.call("bad-sub", "subscriptions/listen", {"types": ["madeUpThing"]})
        self.assertEqual(bad["error"]["code"], -32602)
        unknown = self.call("unk-sub", "subscriptions/listen",
                            {"types": ["toolsListChanged", "madeUpThing"]})
        self.assertIn("madeUpThing", unknown["result"]["unknownTypes"])

    def test_streamable_http_subscription_streams_on_the_post_response(self):
        body = {"jsonrpc": "2.0", "id": "sse", "method": "subscriptions/listen",
                "params": {"types": ["resourceSubscriptions"], "maxSeconds": 0.4, "pollSeconds": 0.05},
                "_meta": dict(self.modern)}
        req = urllib.request.Request(
            self.base_url + "/mcp", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}",
                     "Mcp-Method": "subscriptions/listen",
                     "Accept": "application/json, text/event-stream"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.headers.get("Content-Type").split(";")[0], "text/event-stream")
            self.assertTrue(resp.headers.get("X-Arena-Subscription"))
            self.assertIsNone(resp.headers.get("Mcp-Session-Id"),
                              "the 2026-07-28 era removed protocol sessions")
            resp_sub = resp.headers.get("X-Arena-Subscription")
            raw = resp.read().decode("utf-8")
        ack = None
        notes = []
        for line in raw.splitlines():
            if not line.startswith("data: "):
                continue
            obj = json.loads(line[len("data: "):])
            if obj.get("method") is not None:
                notes.append(obj)
            else:
                ack = obj
        self.assertTrue(ack and ack["id"] == "sse", msg=raw[:400])
        self.assertNotIn("_arenaStream", ack["result"],
                         "the transport consumes the marker; the client gets the header")
        self.assertEqual(resp_sub, ack["result"]["subscriptionId"])
        self.assertTrue(notes, msg=raw[:400])
        for note in notes:
            self.assertTrue(note["method"].startswith("notifications/"))
            self.assertIn("io.modelcontextprotocol/subscriptionId", note["params"]["_meta"])
        self.assertEqual(ack["result"]["types"], ["resourceSubscriptions"])
        self.assertTrue(ack["result"]["resources"])

    def test_modern_and_legacy_http_headers_and_session_id(self):
        def post(body, headers=None):
            hdrs = {"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"}
            hdrs.update(headers or {})
            req = urllib.request.Request(self.base_url + "/mcp", data=json.dumps(body).encode("utf-8"),
                                         method="POST", headers=hdrs)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "null"), dict(resp.headers)

        status, discover, hdrs = post({"jsonrpc": "2.0", "id": 1, "method": "server/discover",
                                       "_meta": dict(self.modern)},
                                      {"Mcp-Method": "server/discover"})
        self.assertEqual(status, 200)
        self.assertNotIn("Mcp-Session-Id", hdrs)
        self.assertEqual(discover["result"]["protocolVersion"], "2026-07-28")
        self.assertIn("extensions", discover["result"]["capabilities"])
        self.assertIn("io.modelcontextprotocol/tasks", discover["result"]["capabilities"]["extensions"])
        _, legacy, legacy_hdrs = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                                       "params": {}})
        self.assertEqual(legacy_hdrs.get("Mcp-Session-Id"), self.state.session_id)
        self.assertNotIn("resultType", legacy["result"])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            post({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "_meta": dict(self.modern)},
                 {"Mcp-Method": "tools/call"})
        self.assertEqual(json.loads(ctx.exception.read().decode())["error"]["code"], -32020)

    def test_jobs_carry_the_mcp_call_that_queued_them(self):
        res = self.call("state", "tools/call", {"name": "get_studio_state", "arguments": {}})
        self.assertFalse(res["result"].get("isError"))
        rows = self.rest("/api/jobs?limit=8")
        jobs = rows.get("jobs") or rows.get("recent") or []
        with_provenance = [row for row in jobs if row.get("actor")]
        self.assertTrue(with_provenance, msg=json.dumps(rows)[:400])
        self.assertEqual(with_provenance[0]["actor"].get("principal"), "owner")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if os.environ.get("ARENA_SELF_TEST_CHILD"):
        print("ARENA_TOOL_METRICS=" + json.dumps(TOOL_TEST_METRICS, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
