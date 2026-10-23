#!/usr/bin/env python3
"""
test_bridge_mcp.py — Comprehensive automated test suite for the v3.0
Arena <-> Roblox Studio Bridge, CLI, and MCP Server (stdio + Streamable HTTP).

Tests:
  1. Exact 32-bit FNV-1a test vectors & UTF-8 round-trip fidelity
  2. Local Luau toolchain pre-flight (`luau_check`): syntax errors, undefined globals, clean files
  3. Token recovery in `setup.py` (verifying HTTP 401 token mismatch trap is cured) & `.mcp.json` writer
  4. Streamable HTTP MCP (`POST /mcp`, `GET /mcp`, `DELETE /mcp`, notifications `202`, batches, auth)
  5. MCP `stdio` subprocess transport (BOTH newline-delimited JSON and LSP `Content-Length` framing,
     auto-attach to running bridge AND standalone embedded bridge mode)
  6. All 37 MCP tools end-to-end against stateful `MockWorld` v3.0:
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
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from mcp_core import TOOL_SPECS, fnv1a_32, fnv1a_hex, run_luau_check  # noqa: E402
from mock_studio import run_mock_loop  # noqa: E402
from server import create_http_server  # noqa: E402
import setup as bridge_setup  # noqa: E402


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
        cls.httpd, cls.state = create_http_server(
            host="127.0.0.1",
            port=cls.port,
            token=cls.token,
            read_only=False,
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
        status, body, _ = self.mcp_http({
            "jsonrpc": "2.0",
            "id": f"call-{name}",
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        })
        self.assertEqual(status, 200)
        res = body["result"]
        self.assertFalse(res.get("isError"), msg=f"Tool {name} errored: {res}")
        return res["structuredContent"]

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

        # Tools list returns all 37 annotated tools
        _, list_resp, _ = self.mcp_http({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, auth_mode="header")
        tools = list_resp["result"]["tools"]
        self.assertEqual(len(tools), 37)
        self.assertEqual(len(tools), len(TOOL_SPECS))
        for t in tools:
            self.assertIn("annotations", t)
            self.assertIn("readOnlyHint", t["annotations"])

        # JSON-RPC batch call works cleanly
        st_b, batch_resp, _ = self.mcp_http([
            {"jsonrpc": "2.0", "id": "b1", "method": "ping"},
            {"jsonrpc": "2.0", "id": "b2", "method": "resources/list"},
            {"jsonrpc": "2.0", "id": "b3", "method": "prompts/list"},
        ], auth_mode="query")
        self.assertEqual(st_b, 200)
        self.assertEqual(len(batch_resp), 3)
        self.assertEqual(len(batch_resp[1]["result"]["resources"]), 5)
        self.assertEqual(len(batch_resp[2]["result"]["prompts"]), 4)

    def test_02_all_core_studio_tools_end_to_end(self):
        # 1. Session & Peers
        st = self.call_tool("get_studio_state")
        self.assertTrue(st["studioConnected"])
        peers = self.call_tool("list_studios")
        self.assertGreaterEqual(peers["onlineCount"], 1)
        ping = self.call_tool("ping_studio")
        self.assertEqual(ping["pluginVersion"], "3.0")

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
        })
        self.assertTrue(scen["ok"])
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
            self.assertEqual(len(boot_data["tools"]), 37)

        with urllib.request.urlopen(f"{self.base_url}/api/agent-guide", timeout=5) as guide_resp:
            guide_data = json.loads(guide_resp.read().decode("utf-8"))
            self.assertEqual(guide_data["toolCounts"]["total"], 37)
            self.assertGreaterEqual(len(guide_data["keyTips"]), 5)

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
            self.assertEqual(msg["result"]["structuredContent"]["bridge"]["mcp_tools"], 37)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
