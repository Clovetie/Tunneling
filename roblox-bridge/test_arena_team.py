#!/usr/bin/env python3
"""ARENA multi-agent orchestration — unit + end-to-end regression suite.

Runs entirely offline with Python 3.10+ stdlib plus the repository's stateful
``MockWorld``. It verifies the real MCP tool surface, scoped auth, the Agent Bus
roster/leases, human approvals, real ``dm="edit"`` writes, ``luau_check``
pre-flight, ``expectedHash``/``replaceInstance`` updates, read-backs, playtest +
console QA, viewport evidence, the hash-chained audit, the Control Center Tab 9,
the Studio poll summary and a ``verified: true`` development report.

A denied project, an unavailable Studio peer and Read-Only Inspector mode are
also exercised so a failure can never be reported as a success.

Run:
    python3 -B roblox-bridge/test_arena_team.py
"""

import json
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bus_core import AgentBus  # noqa: E402
from mcp_core import (  # noqa: E402
    AGENT_TEAM_ACTIONS,
    AGENT_TEAM_ADMIN_ACTIONS,
    AGENT_TEAM_READ_ACTIONS,
    AGENT_TEAM_WRITE_ACTIONS,
    PROMPTS,
    RESOURCES,
    TOOL_MAP,
    TOOL_SPECS,
    build_gameplay_mechanic_helper,
    check_luau_source,
    execute_tool,
    is_tool_call_read_only,
)
from mock_studio import run_mock_loop  # noqa: E402
from server import (  # noqa: E402
    agent_team_required_role,
    create_http_server,
    dashboard_html,
)
from arena_team import workflows  # noqa: E402
from arena_team.config import TeamConfig, load_config, redact  # noqa: E402
from arena_team.planner import build_design, build_plan  # noqa: E402
from arena_team.registry import AgentRegistry  # noqa: E402


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ArenaHttpMixin:
    """Authenticated HTTP helpers shared by the integration test classes."""

    def http_json(self, path: str, payload=None, token: str = None, timeout: float = 45.0):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Content-Type": "application/json"}
        tok = token if token is not None else self.token
        if tok:
            headers["Authorization"] = "Bearer " + tok
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method="POST" if payload is not None else "GET",
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                body = json.loads(raw) if raw else {}
            except ValueError:
                body = {"error": raw[:400]}
            return exc.code, body

    def http_raw(self, path: str, token: str = None, timeout: float = 30.0):
        headers = {}
        tok = token if token is not None else self.token
        if tok:
            headers["Authorization"] = "Bearer " + tok
        req = urllib.request.Request(self.base_url + path, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), exc.headers.get("Content-Type", "")

    def mcp_http(self, rpc: dict, token: str = None, timeout: float = 45.0):
        headers = {"Content-Type": "application/json"}
        tok = token if token is not None else self.token
        if tok:
            headers["Authorization"] = "Bearer " + tok
        req = urllib.request.Request(
            self.base_url + "/mcp",
            data=json.dumps(rpc).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})

    def call_tool(self, name: str, args: dict = None, token: str = None):
        status, body = self.mcp_http({
            "jsonrpc": "2.0",
            "id": "arena-team-" + name,
            "method": "tools/call",
            "params": {"name": name, "arguments": args or {}},
        }, token=token)
        self.assertEqual(status, 200)
        self.assertIn("result", body, msg=json.dumps(body)[:600])
        return body["result"]

    def wait_for_project(self, project_id: str = None, timeout: float = 90.0):
        suffix = ("&projectId=" + urllib.parse.quote(project_id)) if project_id else ""
        deadline = time.time() + timeout
        last = {}
        while time.time() < deadline:
            code, status = self.http_json("/api/orchestrator?action=status" + suffix)
            self.assertEqual(code, 200, msg=json.dumps(status)[:400])
            last = status
            p = status.get("project") or {}
            if p.get("status") in ("completed", "failed", "cancelled"):
                return status
            time.sleep(0.25)
        self.fail("Project did not reach a terminal state: " + json.dumps(last.get("project") or {})[:400])

    def drain_one_approval(self, decision: str, timeout: float = 60.0):
        seen = set()
        deadline = time.time() + timeout
        while time.time() < deadline:
            code, approvals = self.http_json("/api/orchestrator?action=approvals")
            self.assertEqual(code, 200, msg=json.dumps(approvals)[:400])
            for row in approvals.get("pending") or []:
                rid = row.get("requestId")
                if not rid or rid in seen:
                    continue
                seen.add(rid)
                code, answer = self.http_json("/api/orchestrator", {
                    "action": decision,
                    "requestId": rid,
                    "note": "test_arena_team.py automated decision",
                })
                self.assertEqual(code, 200, msg=json.dumps(answer)[:400])
                return rid
            time.sleep(0.2)
        self.fail(f"No pending confirmation appeared for decision={decision!r}")


class TestArenaTeamUnits(unittest.TestCase):
    def test_01_tool_surface_and_constants(self):
        self.assertIn("agent_team", TOOL_MAP)
        spec = TOOL_MAP["agent_team"]
        self.assertFalse(spec["annotations"]["readOnlyHint"])
        self.assertFalse(spec["annotations"]["destructiveHint"])
        self.assertEqual(set(spec["inputSchema"]["properties"]["action"]["enum"]), set(AGENT_TEAM_ACTIONS))
        self.assertTrue(is_tool_call_read_only("agent_team", {"action": "status"}))
        self.assertFalse(is_tool_call_read_only("agent_team", {"action": "start_project"}))
        self.assertFalse(is_tool_call_read_only("agent_team", {"action": "not_an_action"}))
        self.assertEqual(agent_team_required_role("status"), "read")
        self.assertEqual(agent_team_required_role("approve"), "write")
        self.assertEqual(agent_team_required_role("configure"), "admin")
        self.assertEqual(len(PROMPTS), 7)
        self.assertEqual(len(RESOURCES), 12)
        self.assertTrue(AGENT_TEAM_READ_ACTIONS.isdisjoint(AGENT_TEAM_WRITE_ACTIONS))
        self.assertTrue(AGENT_TEAM_ADMIN_ACTIONS.isdisjoint(AGENT_TEAM_WRITE_ACTIONS))

    def test_02_config_registry_and_redaction(self):
        cfg = load_config()
        self.assertEqual(len(cfg.agents), 8)
        with tempfile.TemporaryDirectory() as temp:
            bus = AgentBus(Path(temp) / "bus.json", persist=False)
            registry = AgentRegistry(bus, cfg, TOOL_MAP)
            self.assertEqual(registry.unknown_tools(), [])
            self.assertEqual(registry.ownership_conflicts(), [])
            self.assertEqual(len(registry.roster()), 8)
            self.assertTrue(all(a["driverReady"] for a in registry.roster()))

            def agent(key, paths, tools):
                return {"key": key, "name": key, "role": "test", "permission": "write",
                        "ownedPaths": paths, "allowedTools": tools,
                        "driver": {"type": "builtin", "playbook": "project_manager"}}

            bad = TeamConfig({"agents": [agent("bad", [], ["not_a_real_tool"])]})
            self.assertEqual(AgentRegistry(bus, bad, TOOL_MAP).unknown_tools(),
                             ["bad:not_a_real_tool"])
            overlap = TeamConfig({"agents": [
                agent("a", ["Workspace.A"], ["get_studio_state"]),
                agent("b", ["Workspace.A.B"], ["get_studio_state"]),
            ]})
            self.assertTrue(AgentRegistry(bus, overlap, TOOL_MAP).ownership_conflicts())

        scrubbed = redact({"token": "abc12345", "nested": {"Authorization": "Bearer xyz"},
                           "body": "use abc12345 here"}, extra_secrets=["abc12345"])
        self.assertEqual(scrubbed["token"], "[redacted]")
        self.assertEqual(scrubbed["nested"]["Authorization"], "[redacted]")
        self.assertNotIn("abc12345", scrubbed["body"])

    def test_03_planner_and_generated_luau(self):
        with tempfile.TemporaryDirectory() as temp:
            bus = AgentBus(Path(temp) / "bus.json", persist=False)
            cfg = load_config()
            roster = AgentRegistry(bus, cfg, TOOL_MAP).roster()
        analysis = {
            "studioState": {"studioConnected": True,
                            "studio": {"place": {"name": "TestPlace", "placeId": 123}}},
            "survey": {"totals": {"scripts": 2, "instances": 10}},
            "peers": {"onlineCount": 1},
        }
        design = build_design("Build a crystal shrine with a responsive HUD", analysis, roster, "edit-test")
        self.assertIn("Technical design", design["title"])
        self.assertEqual(design["currentPlace"]["place"], "TestPlace")
        self.assertEqual(len(design["components"]), 4)
        plan = build_plan("Build a crystal shrine with a responsive HUD", design, roster, cfg.settings)
        self.assertEqual(plan["conflicts"], [])
        self.assertEqual(len(plan["tasks"]), 10)
        keys = {t["key"] for t in plan["tasks"]}
        self.assertTrue({"analysis", "design", "review", "qa", "security", "report"} <= keys)
        report = next(t for t in plan["tasks"] if t["key"] == "report")
        self.assertEqual(set(report["dependsOn"]), {"qa", "security"})

        config_source = (workflows.CONFIG_MODULE
                         .replace("__OBJECTIVE__", json.dumps("test objective"))
                         .replace("__PROJECT_ID__", json.dumps("proj-test")))
        for label, source in [("config", config_source), ("bootstrap", workflows.BOOTSTRAP_SCRIPT)]:
            check = check_luau_source(source, label + ".lua")
            self.assertTrue(check.get("ok"), msg=f"{label}: {check}")
            self.assertTrue(check.get("syntaxOk"))
        bundle = build_gameplay_mechanic_helper(None, {"mechanic": "dialogue-npc", "installInStudio": False})
        self.assertGreaterEqual(bundle["scriptCount"], 3)
        for script in bundle["scripts"]:
            check = check_luau_source(str(script.get("source") or ""), script["name"] + ".lua")
            self.assertTrue(check.get("ok"), msg=f"{script['name']}: {check}")

    def test_04_dashboard_and_plugin_surface(self):
        html = dashboard_html()
        self.assertIn('id="tab-team"', html)
        self.assertIn("9. ARENA Multi-Agent Team", html)
        self.assertIn("refreshArenaTeam", html)
        self.assertIn("teamStartProject", html)
        plugin = (HERE / "ArenaBridge.lua").read_text(encoding="utf-8")
        self.assertIn("agentTeamSummary", plugin)
        self.assertIn("decoded.agentTeam", plugin)
        self.assertIn("ArenaTeamSummary", plugin)


class TestArenaTeamEndToEnd(ArenaHttpMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.token = "arena-team-e2e-admin-token"
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.state_dir = tempfile.mkdtemp(prefix="arena-team-e2e-")
        cls.httpd, cls.state = create_http_server(
            host="127.0.0.1", port=cls.port, token=cls.token,
            read_only=False, state_dir=cls.state_dir,
            require_confirm="write",
        )
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.stop_mock = threading.Event()
        cls.mock_thread = threading.Thread(
            target=run_mock_loop, args=(cls.base_url, cls.token, cls.stop_mock), daemon=True
        )
        cls.mock_thread.start()
        for _ in range(100):
            if cls.state.direct_dispatch("health", {})["studio_connected"]:
                break
            time.sleep(0.05)
        cls.assertTrue(cls.state.direct_dispatch("health", {})["studio_connected"],
                       "Mock Studio peer did not connect")
        cls.read_token = cls.state.tokens.create("arena-team-reader", "read")["token"]
        cls.write_token = cls.state.tokens.create("arena-team-writer", "write")["token"]
        cls.pid1 = None
        cls.pid2 = None
        cls.pid3 = None

    @classmethod
    def tearDownClass(cls):
        cls.stop_mock.set()
        try:
            cls.state.orchestrator.shutdown()
        except Exception:
            pass
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_01_tools_list_and_scoped_access(self):
        _, listed = self.mcp_http({"jsonrpc": "2.0", "id": "list", "method": "tools/list", "params": {}})
        tools = listed["result"]["tools"]
        self.assertEqual(len(tools), len(TOOL_SPECS))
        self.assertIn("agent_team", {t["name"] for t in tools})

        code, body = self.http_json("/api/orchestrator?action=status", token=self.read_token)
        self.assertEqual(code, 200)
        self.assertTrue(body.get("ok"))

        code, body = self.http_json("/api/orchestrator", {
            "action": "start_project", "objective": "should be blocked by read scope",
            "requireStudio": False,
        }, token=self.read_token)
        self.assertEqual(code, 403, msg=json.dumps(body)[:400])

        code, body = self.http_json("/api/orchestrator", {"action": "configure"}, token=self.write_token)
        self.assertEqual(code, 403, msg=json.dumps(body)[:400])

        code, body = self.http_json("/api/orchestrator", {
            "action": "start_project", "objective": "off requires admin",
            "approvalMode": "off", "allowUnattended": True, "requireStudio": False,
        }, token=self.write_token)
        self.assertEqual(code, 403, msg=json.dumps(body)[:400])

        result = self.call_tool("agent_team", {"action": "status"}, token=self.read_token)
        self.assertTrue((result.get("structuredContent") or {}).get("ok"),
                        msg=json.dumps(result)[:600])

        blocked = self.call_tool("agent_team", {"action": "configure"}, token=self.write_token)
        sc = blocked.get("structuredContent") or {}
        self.assertTrue(blocked.get("isError") or sc.get("reason") == "insufficient_scope",
                        msg=json.dumps(blocked)[:600])

        blocked = self.call_tool("agent_team", {"action": "start_project", "objective": "blocked"},
                                 token=self.read_token)
        sc = blocked.get("structuredContent") or {}
        self.assertTrue(blocked.get("isError") or sc.get("reason") == "insufficient_scope",
                        msg=json.dumps(blocked)[:600])

    def test_02_plan_approval_verified_end_to_end(self):
        objective = "Build a crystal shrine with a responsive HUD and server-authoritative loot chest"
        code, started = self.http_json("/api/orchestrator", {
            "action": "start_project", "objective": objective,
            "approvalMode": "plan", "requireStudio": True,
        })
        self.assertEqual(code, 200, msg=json.dumps(started)[:600])
        self.assertTrue(started.get("studioConnected"))
        self.pid1 = started["projectId"]
        self.__class__.pid1 = self.pid1

        request_id = self.drain_one_approval("approve")
        self.assertTrue(request_id.startswith("cfm-"))
        status = self.wait_for_project(self.pid1)
        project = status["project"]
        self.assertEqual(project["status"], "completed", msg=json.dumps(project)[:600])
        self.assertTrue(project["verified"])

        # Worker-thread tool calls carry MCP provenance into queued Studio jobs.
        tagged_jobs = [
            j for j in self.state.jobs.values()
            if str((j.get("actor") or {}).get("principal") or "").startswith("arena_team:")
        ]
        self.assertTrue(tagged_jobs, "No Studio job carried arena_team actor provenance")
        self.assertTrue(any((j.get("trace") or {}).get("projectId") == self.pid1
                            for j in tagged_jobs))
        self.assertTrue(all((j.get("trace") or {}).get("layer") == "arena_team"
                            for j in tagged_jobs))

        _, report_body = self.http_json("/api/orchestrator?action=report&projectId=" + self.pid1)
        report = report_body["report"]
        self.assertTrue(report["verified"])
        self.assertEqual(report["status"], "verified")
        self.assertTrue(report["auditChain"]["ok"])
        self.assertEqual(report["verification"]["tasksDone"], report["verification"]["tasksTotal"])
        self.assertEqual(report["verification"]["writeTasks"], report["verification"]["verifiedWriteReadBacks"])
        self.assertTrue(report["verification"]["qaPassed"])
        self.assertEqual(report["verification"]["consoleErrors"], 0)
        self.assertGreater(report["verification"]["evidenceCount"], 0)
        self.assertEqual(report["failedTasks"], [])
        self.assertEqual(report["warnings"], [])

        # The place really changed in the Edit DataModel, and scripts read back.
        client = self.state.mcp_client
        config = execute_tool(client, "script_read", {
            "path": "ServerScriptService.ArenaTeam_Scripts.ArenaTeamConfig", "dm": "edit"})
        self.assertIn(self.pid1, str(config.get("source") or ""))
        for path in ("ServerScriptService.ArenaTeam_Scripts.ArenaTeamBootstrap",
                     "ServerScriptService.ArenaTeam_Gameplay",
                     "ReplicatedStorage.ArenaTeam_Gameplay",
                     "StarterPlayer.StarterPlayerScripts.ArenaTeam_Gameplay",
                     "StarterGui.ArenaTeam_UI_HUD", "Workspace.ArenaTeam_World"):
            row = execute_tool(client, "inspect_instance", {"path": path, "depth": 1, "dm": "edit"})
            self.assertTrue(row.get("name") or row.get("children"), msg=f"{path}: {row}")

        # Authenticated viewport evidence and Studio poll summary.
        evidence = status.get("evidence") or []
        self.assertTrue(evidence)
        filename = evidence[0]["filename"]
        code, raw, ctype = self.http_raw("/api/orchestrator/evidence/" + urllib.parse.quote(filename))
        self.assertEqual(code, 200)
        self.assertIn("image/png", ctype)
        self.assertTrue(raw.startswith(b"\x89PNG\r\n\x1a\n"))

        query = urllib.parse.urlencode({
            # Reuse the live mock peer id: inventing a second edit peer without a
            # poll loop would make job routing time out for up to PEER_ONLINE_WINDOW.
            "peer": "edit-mock0001", "dm": "edit", "place": "DemoPlace",
            "placeId": "110457957229133", "client": "studio", "hasClient": "0",
            "players": "0", "pv": "6.0.0", "sv": "mock-3.0", "wait": "0",
        })
        code, polled = self.http_json("/api/poll?" + query)
        self.assertEqual(code, 200, msg=json.dumps(polled)[:400])
        self.assertIn("agentTeam", polled)
        self.assertGreaterEqual(polled["agentTeam"]["tasksTotal"], 10)

    def test_03_unattended_update_uses_hash_and_replace_instance(self):
        code, started = self.http_json("/api/orchestrator", {
            "action": "start_project",
            "objective": "Refactor the validated Luau module and rebuild the world and HUD",
            "approvalMode": "off", "allowUnattended": True, "requireStudio": True,
        })
        self.assertEqual(code, 200, msg=json.dumps(started)[:600])
        self.pid2 = started["projectId"]
        self.__class__.pid2 = self.pid2
        status = self.wait_for_project(self.pid2)
        self.assertEqual(status["project"]["status"], "completed",
                         msg=json.dumps({"project": status["project"],
                                         "failed": [{"key": t["key"], "error": t.get("error")}
                                                    for t in status["plan"]["tasks"]
                                                    if t.get("status") != "done"]})[:1200])
        _, report_body = self.http_json("/api/orchestrator?action=report&projectId=" + self.pid2)
        self.assertTrue(report_body["report"]["verified"])

        luau = next(t for t in status["plan"]["tasks"] if t["key"] == "luau")
        receipt = luau["verification"]["ServerScriptService.ArenaTeam_Scripts.ArenaTeamConfig"]
        self.assertTrue(receipt["readBack"])
        self.assertTrue(receipt["expectedHash"])
        self.assertTrue(receipt["replaceInstance"])

    def test_04_denied_project_does_not_write(self):
        code, started = self.http_json("/api/orchestrator", {
            "action": "start_project", "objective": "This plan must be rejected by the human",
            "approvalMode": "plan", "requireStudio": True,
        })
        self.assertEqual(code, 200, msg=json.dumps(started)[:600])
        self.pid3 = started["projectId"]
        self.__class__.pid3 = self.pid3
        self.drain_one_approval("deny")
        status = self.wait_for_project(self.pid3)
        self.assertEqual(status["project"]["status"], "failed")
        self.assertEqual(status["project"]["revision"], 0)
        _, report_body = self.http_json("/api/orchestrator?action=report&projectId=" + self.pid3)
        self.assertFalse(report_body["report"]["verified"])

    def test_05_views_audit_and_bus_history(self):
        _, projects = self.http_json("/api/orchestrator?action=projects")
        self.assertGreaterEqual(len(projects["projects"]), 3)
        _, manifest = self.http_json("/api/orchestrator?action=tool_manifest&query=agent")
        self.assertIn("agent_team", [t["name"] for t in manifest["tools"]])
        _, config_view = self.http_json("/api/orchestrator?action=config")
        self.assertEqual(config_view["unknownTools"], [])
        self.assertEqual(config_view["ownershipConflicts"], [])
        _, roster_view = self.http_json("/api/orchestrator?action=roster")
        self.assertEqual(len(roster_view["roster"]), 8)
        _, feed = self.http_json("/api/orchestrator?action=feed&projectId=" + self.pid1)
        self.assertTrue(feed["messages"])
        _, logs = self.http_json("/api/orchestrator?action=logs&projectId=" + self.pid1)
        self.assertTrue(logs["logs"])
        _, approvals = self.http_json("/api/orchestrator?action=approvals&projectId=" + self.pid1)
        self.assertTrue(any(r["state"] == "approved" for r in approvals["projectRequests"]))
        _, evidence = self.http_json("/api/orchestrator?action=evidence&projectId=" + self.pid1)
        self.assertTrue(evidence["evidence"])
        _, stats = self.http_json("/api/orchestrator?action=stats&projectId=" + self.pid1)
        self.assertGreater(stats["project"]["toolCalls"], 0)
        _, studio = self.http_json("/api/orchestrator?action=studio")
        self.assertTrue(studio["studio"]["connected"])
        code, audit = self.http_json("/api/audit?verify=1")
        self.assertEqual(code, 200)
        self.assertTrue(audit.get("ok"), msg=json.dumps(audit)[:400])


class TestArenaTeamNoPeer(ArenaHttpMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.token = "arena-team-no-peer-admin"
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.state_dir = tempfile.mkdtemp(prefix="arena-team-nopeer-")
        cls.httpd, cls.state = create_http_server(
            host="127.0.0.1", port=cls.port, token=cls.token,
            read_only=False, state_dir=cls.state_dir, require_confirm="off",
        )
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.state.orchestrator.shutdown()
        except Exception:
            pass
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_01_no_studio_peer_is_not_a_success(self):
        code, started = self.http_json("/api/orchestrator", {
            "action": "start_project", "objective": "Attempt real edits with no Studio peer",
            "approvalMode": "off", "allowUnattended": True, "requireStudio": False,
        })
        self.assertEqual(code, 200, msg=json.dumps(started)[:600])
        self.assertFalse(started.get("studioConnected"))
        status = self.wait_for_project(started["projectId"], timeout=90)
        self.assertEqual(status["project"]["status"], "failed", msg=json.dumps(status["project"])[:600])
        _, report_body = self.http_json("/api/orchestrator?action=report&projectId=" + started["projectId"])
        report = report_body["report"]
        self.assertFalse(report["verified"])
        self.assertFalse(report["verification"]["studioConnected"])
        self.assertTrue(report["failedTasks"])
        all_errors = " ".join(str(t.get("error") or "") for t in report["tasks"])
        self.assertIn("no_peer", all_errors.lower())


class TestArenaTeamReadOnlyInspector(ArenaHttpMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.token = "arena-team-readonly-admin"
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.state_dir = tempfile.mkdtemp(prefix="arena-team-readonly-")
        cls.httpd, cls.state = create_http_server(
            host="127.0.0.1", port=cls.port, token=cls.token,
            read_only=True, state_dir=cls.state_dir, require_confirm="off",
        )
        cls.server_thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.state.orchestrator.shutdown()
        except Exception:
            pass
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_01_inspector_reads_allowed_writes_blocked(self):
        code, status = self.http_json("/api/orchestrator?action=status")
        self.assertEqual(code, 200)
        self.assertTrue(status.get("ok"))
        code, blocked = self.http_json("/api/orchestrator", {
            "action": "start_project", "objective": "should be blocked in Inspector mode",
            "approvalMode": "off", "allowUnattended": True, "requireStudio": False,
        })
        self.assertEqual(code, 403, msg=json.dumps(blocked)[:400])

        _, listed = self.mcp_http({"jsonrpc": "2.0", "id": "ro-list", "method": "tools/list", "params": {}})
        self.assertIn("agent_team", {t["name"] for t in listed["result"]["tools"]})
        ok = self.call_tool("agent_team", {"action": "status"})
        self.assertTrue((ok.get("structuredContent") or {}).get("ok"))
        denied = self.call_tool("agent_team", {"action": "start_project", "objective": "blocked"})
        self.assertTrue(denied.get("isError") or
                        (denied.get("structuredContent") or {}).get("reason"),
                        msg=json.dumps(denied)[:600])


if __name__ == "__main__":
    unittest.main(verbosity=2)
