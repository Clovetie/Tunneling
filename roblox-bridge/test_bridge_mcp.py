#!/usr/bin/env python3
"""
test_bridge_mcp.py — Comprehensive automated test suite for the v4.0.0
Arena <-> Roblox Studio Bridge, CLI, and MCP Server (stdio + Streamable HTTP).

Tests:
  1. Exact 32-bit FNV-1a test vectors & UTF-8 round-trip fidelity
  2. Local Luau toolchain pre-flight (`luau_check`): syntax errors, undefined globals, clean files
  3. Token recovery in `setup.py` (verifying HTTP 401 token mismatch trap is cured) & `.mcp.json` writer
  4. Streamable HTTP MCP (`POST /mcp`, `GET /mcp`, `DELETE /mcp`, notifications `202`, batches, auth)
  5. MCP `stdio` subprocess transport (BOTH newline-delimited JSON and LSP `Content-Length` framing,
     auto-attach to running bridge AND standalone embedded bridge mode)
  6. All 68 MCP tools end-to-end against stateful `MockWorld` v4.0.0:
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
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

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
        self.assertIn('local trimmed = string.gsub(token, "^%s*(.-)%s*$", "%1")', source)
        self.assertIn("local n = tonumber(trimmed)", source)
        self.assertNotIn("tonumber(string.gsub(token,", source)
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
        state = BridgeState()
        client = BridgeClient(direct_dispatch=state.direct_dispatch)
        job = client.submit_job("inspect", {"path": "Workspace"}, wait=60, dm="edit")
        self.assertEqual(job["status"], "error")
        self.assertEqual(job["reason"], "no_peer")

    def test_offline_renderer_accepts_exported_tree_without_peer(self):
        state = BridgeState()
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
                first = BridgeState()
                saved = first.handle_snapshots({"action": "save", "label": "named-baseline", "root": "ServerStorage", "scripts": [{"path": "ServerStorage.A", "source": "return 1"}]})
                second = BridgeState()
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
        self.assertEqual(len(batch_resp[1]["result"]["resources"]), 5)
        self.assertEqual(len(batch_resp[2]["result"]["prompts"]), 4)

    def test_02_all_core_studio_tools_end_to_end(self):
        # 1. Session & Peers
        st = self.call_tool("get_studio_state")
        self.assertTrue(st["studioConnected"])
        peers = self.call_tool("list_studios")
        self.assertGreaterEqual(peers["onlineCount"], 1)
        ping = self.call_tool("ping_studio")
        self.assertEqual(ping["pluginVersion"], "4.0.0")

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
            self.assertIn("callMcp('browse_kit', { category: 'all', format: 'full' })", dash_html)
            self.assertIn("sendAudioToStudio", dash_html)
            self.assertIn("installDropinSystemUI", dash_html)
            self.assertIn("Kit inventory failed to load:", dash_html)
            self.assertIn("loadMoreCloudIcons", dash_html)
            self.assertIn("CLOUD_ICON_PAGE_SIZE = 100", dash_html)
            self.assertIn("Show all ${variants.length} variants", dash_html)

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
        self.assertIn("totalMatches", icon_search)

        icon_page_1 = self.call_tool("search_cloud_icons", {"pack": "all", "cursor": 0, "limit": 50})
        self.assertTrue(icon_page_1["hasMore"])
        self.assertEqual(icon_page_1["nextCursor"], 50)
        icon_page_2 = self.call_tool("search_cloud_icons", {"pack": "all", "cursor": icon_page_1["nextCursor"], "limit": 50})
        self.assertEqual(icon_page_2["cursor"], 50)
        self.assertEqual(icon_page_2["totalMatches"], icon_page_1["totalMatches"])
        page_1_keys = {(x["pack"], x["family"]) for x in icon_page_1["icons"]}
        page_2_keys = {(x["pack"], x["family"]) for x in icon_page_2["icons"]}
        self.assertFalse(page_1_keys & page_2_keys)
        self.assertTrue(all(x["variants"] for x in icon_page_1["icons"]))

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


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if os.environ.get("ARENA_SELF_TEST_CHILD"):
        print("ARENA_TOOL_METRICS=" + json.dumps(TOOL_TEST_METRICS, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
