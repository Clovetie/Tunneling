#!/usr/bin/env python3
"""
test_bridge_mcp.py — Comprehensive automated test suite for the v6.1.0
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


# ---------------------------------------------------------------------------
# Dashboard sources (v6.1.0, P4-D1)
# ---------------------------------------------------------------------------
# `dashboard_html()` used to inline ~3,000 lines of HTML/CSS/JS, so a test could
# assert on the whole page in one string. The dashboard now lives in
# `roblox-bridge/static/` and the served page only references it, so assertions
# about behaviour read the three files and assertions about the served document
# read `dashboard_html()`.

DASHBOARD_SOURCES = ("index.html", "dashboard.css", "dashboard.js")


def dashboard_source(name: str) -> str:
    """One static dashboard file, with the version placeholder substituted."""
    return (bridge_server.STATIC_DIR / name).read_text(encoding="utf-8").replace(
        "__BRIDGE_VERSION__", bridge_server.VERSION
    )


def dashboard_bundle() -> str:
    """All three static sources concatenated, for whole-page assertions."""
    return "\n".join(dashboard_source(n) for n in DASHBOARD_SOURCES)


class TestSkillDiscovery(unittest.TestCase):
    def test_offline_search_pagination_and_application(self):
        from mcp_core import search_skills_helper, apply_skill_helper, read_mcp_resource
        first = search_skills_helper("", 0, 2)
        second = search_skills_helper("", first["nextCursor"], 2)
        self.assertTrue(set(x["name"] for x in first["results"]).isdisjoint(x["name"] for x in second["results"]))
        self.assertEqual(search_skills_helper("xyzzyunmatchedterm")["total"], 0)
        self.assertIn("Standalone .rbxlx", next(x for x in search_skills_helper(limit=100)["results"] if x["name"] == "roblox-rbxlx-and-opencloud")["description"])
        for name, query in (("roblox-cross-platform-accessibility", "accessibility"),
                            ("roblox-multiplayer-interactions", "multiplayer"),
                            ("roblox-data-reliability", "persistence")):
            self.assertIn(name, [item["name"] for item in search_skills_helper(query, limit=100)["results"]])
            self.assertIn("## Verification", apply_skill_helper(name)["instructions"])

        skill = apply_skill_helper("roblox-stud-ui-design")
        self.assertEqual(skill["instructions"], read_mcp_resource(None, skill["uri"])["contents"][0]["text"])
        for name in ("../AGENTS.md", "/tmp", "missing", ""):
            with self.assertRaises((KeyError, ValueError)):
                apply_skill_helper(name)
        for args in (("", -1, 1), ("", 0, 0), ("", 0, 101)):
            with self.assertRaises(ValueError):
                search_skills_helper(*args)

    def test_task_search_relevance_and_stability(self):
        from mcp_core import search_skills_helper, is_tool_call_read_only
        self.assertTrue(is_tool_call_read_only("search_skills", {}))
        self.assertEqual(execute_tool(None, "search_skills", {"query": "how do I"})["total"], 0)
        self.assertEqual(search_skills_helper("how do I fix xyzzyunmatchedterm")["total"], 0)
        for query, expected in (("how do I handle a failed datastore load", "roblox-data-reliability"),
                                ("receipts and duplicate purchase rewards", "roblox-monetization"),
                                ("show me adaptive music", "audio-design"),
                                ("ROBLOX-STUD-UI-DESIGN", "roblox-stud-ui-design")):
            results = search_skills_helper(query, limit=100)["results"]
            self.assertEqual(results[0]["name"], expected, query)
            self.assertTrue(results[0]["matchedFields"])
            self.assertNotIn("how", results[0]["matchedTerms"])
            self.assertEqual(results, search_skills_helper(query, limit=100)["results"])
        complete = search_skills_helper("gamepad menu", limit=100)["results"]
        page1 = search_skills_helper("gamepad menu", limit=1)
        page2 = search_skills_helper("gamepad menu", cursor=page1["nextCursor"], limit=1)
        self.assertEqual(page1["results"] + page2["results"], complete[:2])
        self.assertEqual(search_skills_helper("gamepad menu", cursor=100)["results"], [])
        self.assertGreater(search_skills_helper("  ")["total"], 0)
        self.assertEqual(search_skills_helper("!!!")["total"], 0)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, description in (("alpha", "Roblox common uncommon"), ("beta", "Roblox common"), ("gamma", "Roblox common")):
                directory = root / name
                directory.mkdir()
                (directory / "SKILL.md").write_text(f"---\ndescription: {description}\n---\n", encoding="utf-8")
            with mock.patch("mcp_core._find_skills_dir", return_value=root):
                results = search_skills_helper("common uncommon", limit=100)["results"]
                self.assertEqual([item["name"] for item in results], ["alpha", "beta", "gamma"])
                self.assertGreater(results[0]["score"], results[1]["score"])
                self.assertEqual(results[1]["score"], results[2]["score"])


    def test_search_reference_only_and_limits(self):
        from mcp_core import search_skills_helper, is_tool_call_read_only
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("alpha", "beta"):
                skill = root / name
                (skill / "references").mkdir(parents=True)
                (skill / "SKILL.md").write_text("---\ndescription: example\n---\nGeneral advice", encoding="utf-8")
                (skill / "references" / "guide.md").write_text("Uniquephrase use carefully", encoding="utf-8")
            (root / "alpha" / "references" / "oversized.md").write_bytes(b"x" * (256 * 1024 + 1))
            (root / "alpha" / "references" / "invalid.md").write_bytes(b"\xff\xfe")
            (root / "secret.md").write_text("secretphrase", encoding="utf-8")
            try:
                (root / "alpha" / "references" / "link.md").symlink_to(root / "secret.md")
            except (OSError, NotImplementedError):
                pass
            with mock.patch("mcp_core._find_skills_dir", return_value=root):
                self.assertEqual(search_skills_helper("uniquephrase")["total"], 0)
                result = execute_tool(None, "search_skills", {"query": "UNIQUEPHRASE", "includeReferences": True, "limit": 1})
                self.assertTrue(is_tool_call_read_only("search_skills", {"includeReferences": True}))
                self.assertEqual(result["total"], 2)
                self.assertEqual(result["results"][0]["name"], "alpha")
                self.assertEqual(result["results"][0]["matchedFields"], ["references"])
                self.assertEqual(result["results"][0]["matchedReferences"], [{"path": "references/guide.md", "matchedTerms": ["uniquephrase"]}])
                self.assertEqual(result["referenceSearch"]["filesSkipped"], 2)
                self.assertFalse(result["referenceSearch"]["complete"])
                page = search_skills_helper("uniquephrase", cursor=result["nextCursor"], limit=1, include_references=True)
                self.assertEqual(page["results"][0]["name"], "beta")
                self.assertEqual(search_skills_helper("secretphrase", include_references=True)["total"], 0)
                self.assertFalse(search_skills_helper("", include_references=True)["referenceSearch"]["complete"])
                with mock.patch("mcp_core.SKILL_SEARCH_MAX_REFERENCE_FILES", 1):
                    capped = search_skills_helper("uniquephrase", include_references=True)
                    self.assertFalse(capped["referenceSearch"]["complete"])
                    self.assertEqual(capped["referenceSearch"]["filesScanned"], 1)
                with mock.patch("mcp_core.SKILL_SEARCH_MAX_REFERENCE_BYTES", 1):
                    capped = search_skills_helper("uniquephrase", include_references=True)
                    self.assertEqual(capped["total"], 0)
                    self.assertFalse(capped["referenceSearch"]["complete"])
                with self.assertRaises(ValueError):
                    search_skills_helper("x", include_references="true")

    def test_reference_listing_read_and_boundaries(self):
        from mcp_core import skill_references_helper, is_tool_call_read_only, SKILL_REFERENCE_MAX_BYTES
        self.assertTrue(is_tool_call_read_only("skill_references", {"action": "read"}))
        self.assertGreater(execute_tool(None, "skill_references", {"name": "roblox-lessons"})["total"], 0)
        first = skill_references_helper("roblox-lessons", limit=2)
        second = skill_references_helper("roblox-lessons", cursor=first["nextCursor"], limit=2)
        self.assertEqual(len(first["items"]), 2)
        self.assertNotEqual(first["items"][0]["path"], second["items"][0]["path"])
        data = skill_references_helper("roblox-lessons", "read", "references/ui.md")
        self.assertIn("TextScaled", data["content"])
        lines = data["content"].splitlines(keepends=True)
        excerpt = skill_references_helper("roblox-lessons", "read", "references/ui.md", start_line=2, end_line=4)
        self.assertEqual(excerpt["content"], "".join(lines[1:4]))
        self.assertEqual(excerpt["lineRange"], {"start": 2, "end": 4, "total": len(lines)})
        empty = skill_references_helper("roblox-lessons", "read", "references/ui.md", start_line=len(lines) + 1)
        self.assertEqual(empty["content"], "")
        self.assertIsNone(empty["lineRange"]["end"])
        for kwargs in ({"start_line": 0}, {"start_line": True}, {"start_line": 4, "end_line": 3}):
            with self.assertRaises(ValueError):
                skill_references_helper("roblox-lessons", "read", "references/ui.md", **kwargs)
        with self.assertRaises(ValueError):
            skill_references_helper("roblox-lessons", start_line=1)

        self.assertEqual(skill_references_helper("roblox-data-reliability")["total"], 0)
        for path in ("../SKILL.md", "/etc/passwd", "references/../SKILL.md", "references/missing.md", "references\\ui.md"):
            with self.assertRaises(KeyError):
                skill_references_helper("roblox-lessons", "read", path)
        with self.assertRaises(KeyError):
            skill_references_helper("../roblox-lessons")
        with self.assertRaises(ValueError):
            skill_references_helper("roblox-lessons", cursor=-1)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill = root / "sample"
            refs = skill / "references"
            refs.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Sample", encoding="utf-8")
            (refs / "large.md").write_bytes(b"x" * (SKILL_REFERENCE_MAX_BYTES + 1))
            (refs / "binary.png").write_bytes(b"not markdown")
            (root / "secret.md").write_text("secret", encoding="utf-8")
            try:
                (refs / "link.md").symlink_to(root / "secret.md")
                (refs / "linked-dir").symlink_to(root, target_is_directory=True)
            except (OSError, NotImplementedError):
                pass
            with mock.patch("mcp_core._find_skills_dir", return_value=root):
                result = skill_references_helper("sample")
                self.assertEqual([x["path"] for x in result["items"]], ["references/large.md"])
                self.assertFalse(result["items"][0]["readable"])
                with self.assertRaises(ValueError):
                    skill_references_helper("sample", "read", "references/large.md")
                with self.assertRaises(KeyError):
                    skill_references_helper("sample", "read", "references/link.md")


class TestToolDiscovery(unittest.TestCase):
    def test_search_catalog_offline_and_read_only_filtering(self):
        from mcp_core import search_tools_helper, is_tool_call_read_only
        self.assertTrue(is_tool_call_read_only("search_tools", {}))
        self.assertEqual(execute_tool(None, "search_tools", {"query": "performance"})["results"][0]["name"], "perf_audit")
        self.assertEqual(search_tools_helper("WRITE_SCRIPT")["results"][0]["name"], "write_script")
        self.assertEqual(search_tools_helper("how do I")["total"], 0)
        full = search_tools_helper(limit=100)
        first = search_tools_helper(limit=3)
        second = search_tools_helper(cursor=first["nextCursor"], limit=3)
        self.assertEqual(first["results"] + second["results"], full["results"][:6])
        self.assertEqual(search_tools_helper(cursor=999)["results"], [])
        strict = search_tools_helper(read_only_only=True, limit=100)
        self.assertTrue(all(x["annotations"]["readOnlyHint"] for x in strict["results"]))
        self.assertNotIn("manage_attributes", [x["name"] for x in strict["results"]])
        inspector = execute_tool(BridgeClient(read_only=True), "search_tools", {"limit": 100})
        names = {x["name"] for x in inspector["results"]}
        self.assertNotIn("write_script", names)
        self.assertIn("manage_attributes", names)
        self.assertFalse(next(x for x in inspector["results"] if x["name"] == "manage_attributes")["annotations"]["readOnlyHint"])
        for kwargs in ({"cursor": -1}, {"limit": 0}, {"limit": 101}, {"read_only_only": "true"}):
            with self.assertRaises(ValueError):
                search_tools_helper(**kwargs)

    def test_onboarding_skill_and_reference_discovery_offline(self):
        from mcp_core import search_skills_helper, apply_skill_helper, skill_references_helper
        name = "roblox-onboarding-progression"
        names = [x["name"] for x in search_skills_helper("first session onboarding progression", limit=100)["results"]]
        self.assertIn(name, names)
        text = apply_skill_helper(name)["instructions"]
        for term in ("server", "rejoin", "permission", "physical devices", "production DataStores"):
            self.assertIn(term, text)
        listing = skill_references_helper(name)
        self.assertIn("references/milestone-matrix.md", [entry["path"] for entry in listing["items"]])
        content = skill_references_helper(name, "read", "references/milestone-matrix.md")
        self.assertIn("Server completion evidence", content["content"])
        self.assertIn("offline/mock", content["content"])
        client = BridgeClient(read_only=True)
        result = execute_tool(client, "apply_skill", {"name": name})
        self.assertIn("does not change", result["instructions"])

    def test_streaming_skill_discovery_and_reference_without_peer(self):
        from mcp_core import search_skills_helper, apply_skill_helper, skill_references_helper
        name = "roblox-world-streaming"
        self.assertIn(name, [x["name"] for x in search_skills_helper("world streaming replication", limit=100)["results"]])
        instructions = apply_skill_helper(name)["instructions"]
        for phrase in ("RequestStreamAroundAsync", "server", "unbounded", "physical devices", "permission"):
            self.assertIn(phrase, instructions)
        listing = skill_references_helper(name)
        path = "references/streaming-test-matrix.md"
        self.assertIn(path, [item["path"] for item in listing["items"]])
        reference_search = search_skills_helper("resource-constrained", limit=100, include_references=True)
        match = next(item for item in reference_search["results"] if item["name"] == name)
        self.assertTrue(reference_search["referenceSearch"]["complete"])
        self.assertTrue(any(item["path"] == path for item in match["matchedReferences"]))
        text = skill_references_helper(name, "read", path)["content"]
        self.assertIn("Two clients", text)
        self.assertIn("not evidence", text)
        client = BridgeClient(read_only=True)
        with mock.patch.object(client, "submit_job") as jobs:
            self.assertIn("streamed-out", execute_tool(client, "apply_skill", {"name": name})["instructions"])
            jobs.assert_not_called()

    def test_performance_skill_discovery(self):
        from mcp_core import search_skills_helper, apply_skill_helper
        names = [x["name"] for x in search_skills_helper("performance profiling", limit=100)["results"]]
        self.assertIn("roblox-performance-profiling", names)
        skill = apply_skill_helper("roblox-performance-profiling")
        self.assertIn("perf_audit", skill["instructions"])
        self.assertIn("physical", skill["instructions"])


class TestMultiEditValidation(unittest.TestCase):
    def test_invalid_edits_rejected_before_gate_task_or_job(self):
        from mcp_core import MCPDispatcher
        from confirm_gate import ConfirmGate
        client = BridgeClient()
        dispatcher = MCPDispatcher(client, gate=ConfirmGate(mode="write"))
        invalid = [[], ["bad"], [{}], [{"oldText": "a", "newText": 3}],
                   [{"oldText": "a", "newText": "b", "replaceAll": "false"}],
                   [{"oldText": "a", "startLine": 1, "endLine": 2, "newText": "b"}],
                   [{"startLine": 2, "newText": "b"}],
                   [{"startLine": True, "endLine": 1, "newText": "b"}],
                   [{"startLine": 3, "endLine": 1, "newText": "b"}],
                   [{"startLine": 1, "endLine": 1, "newText": "b", "replaceAll": True}]]
        with mock.patch.object(client, "submit_job") as submit:
            for edits in invalid:
                for task in (False, True):
                    params = {"name": "multi_edit", "arguments": {"path": "game.X", "edits": edits, "createIfMissing": True}}
                    if task:
                        params["task"] = {}
                    result = dispatcher._handle_one({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
                    self.assertEqual(result["error"]["code"], -32602, edits)
            submit.assert_not_called()
        self.assertEqual(dispatcher.gate.status()["pendingCount"], 0)
        self.assertEqual(dispatcher.tasks, {})

    def test_valid_text_and_line_insertion_shapes(self):
        from mcp_core import validate_action_arguments
        for edits in ([{"oldText": "", "newText": "prefix"}],
                      [{"oldText": "a", "newText": "b", "replaceAll": True}],
                      [{"startLine": 1, "endLine": 0, "newText": "insert"}],
                      [{"startLine": 3, "endLine": 4, "newText": "replace"}]):
            self.assertEqual(validate_action_arguments("multi_edit", {"edits": edits}), "")


class TestBatchWriteValidation(unittest.TestCase):
    def test_invalid_later_entry_never_starts_validation_task_or_job(self):
        from mcp_core import MCPDispatcher
        from confirm_gate import ConfirmGate
        client = BridgeClient()
        dispatcher = MCPDispatcher(client, gate=ConfirmGate(mode="write"))
        good = {"parent": "ServerStorage", "name": "A", "source": "return 1"}
        invalid = [None, {}, {**good, "source": None}, {**good, "name": "  "},
                   {**good, "className": "Folder"}, {**good, "expectedHash": 10},
                   {**good, "replaceInstance": "false"}, {**good, "properties": []},
                   {**good, "attributes": False}, {**good, "tags": ["tag", 1]}]
        with mock.patch.object(client, "submit_job") as submit, mock.patch("mcp_core._check_with_fallback") as check:
            for bad in invalid:
                for task in (False, True):
                    params = {"name": "batch_write", "arguments": {"files": [good, bad]}}
                    if task:
                        params["task"] = {}
                    response = dispatcher._handle_one({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
                    self.assertEqual(response["error"]["code"], -32602, bad)
            submit.assert_not_called()
            check.assert_not_called()
        self.assertEqual(dispatcher.gate.status()["pendingCount"], 0)
        self.assertEqual(dispatcher.tasks, {})

    def test_supported_entries_and_empty_batch(self):
        from mcp_core import validate_action_arguments
        self.assertTrue(validate_action_arguments("batch_write", {"files": []}))
        entry = {"parent": "ServerStorage", "name": "A", "source": "", "className": "ModuleScript", "expectedHash": "", "replaceInstance": False, "attributes": {}, "properties": {}, "tags": ["tag"]}
        self.assertEqual(validate_action_arguments("batch_write", {"files": [entry]}), "")


class TestInstanceActionValidation(unittest.TestCase):
    def test_invalid_operations_rejected_before_gate_task_or_job(self):
        from mcp_core import MCPDispatcher
        from confirm_gate import ConfirmGate
        client = BridgeClient()
        dispatcher = MCPDispatcher(client, gate=ConfirmGate(mode="write"))
        invalid = [("delete_instance", {"paths": []}),
                   ("delete_instance", {"paths": ["Workspace.Part", None]}),
                   ("delete_instance", {"paths": ["./"]}),
                   ("mass_set_properties", {"paths": ["Workspace.Part"]}),
                   ("mass_set_properties", {"paths": ["Workspace.Part", 42], "properties": {"Name": "A"}}),
                   ("set_properties", {"path": "", "properties": {"Name": "A"}}),
                   ("set_properties", {"path": "Workspace.Part", "properties": {}}),
                   ("reparent_or_rename", {"paths": ["Workspace.Part"]}),
                   ("reparent_or_rename", {"paths": ["Workspace.A", "Workspace.B"], "newName": "X"}),
                   ("clone_instance", {"path": "Workspace.Part", "count": 501}),
                   ("clone_instance", {"path": "Workspace.Part", "offset": [0, True, 1]}),
                   ("clone_instance", {"path": "Workspace.Part", "offset": [0, 1]}),
                   ("clone_instance", {"path": "Workspace.Part", "offset": [0, float("inf"), 1]}),
                   ("clone_instance", {"path": "Workspace.Part", "offset": [0, 10**400, 1]})]
        with mock.patch.object(client, "submit_job") as submit:
            for name, args in invalid:
                for task in (False, True):
                    params = {"name": name, "arguments": args}
                    if task:
                        params["task"] = {}
                    response = dispatcher._handle_one({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
                    self.assertEqual(response["error"]["code"], -32602, (name, args))
            submit.assert_not_called()
        self.assertEqual(dispatcher.gate.status()["pendingCount"], 0)
        self.assertEqual(dispatcher.tasks, {})

    def test_supported_action_shapes(self):
        from mcp_core import validate_action_arguments
        valid = [("delete_instance", {"paths": ["Workspace.Part", "ServerStorage/Missing"]}),
                 ("mass_set_properties", {"paths": ["Workspace.Part"], "relative": {"Position": [0, 1, 0]}}),
                 ("set_properties", {"path": "Workspace.Part", "properties": {"Anchored": False}}),
                 ("reparent_or_rename", {"paths": ["Workspace.A", "Workspace.B"], "newParent": "Workspace.Folder"}),
                 ("reparent_or_rename", {"paths": ["Workspace.A"], "newName": "B"}),
                 ("clone_instance", {"path": "Workspace.Part", "count": 500, "offset": [0, 1.5, -2]})]
        for name, args in valid:
            self.assertEqual(validate_action_arguments(name, args), "", (name, args))


class TestCameraSafety(unittest.TestCase):
    def test_default_read_dispatches_explicit_get_through_inspector(self):
        from mcp_core import MCPDispatcher
        with tempfile.TemporaryDirectory() as temp:
            state = BridgeState(read_only=True, state_dir=temp)
            self.addCleanup(state._reaper_stop.set)
            self.addCleanup(state.tunnel.stop)
            client = BridgeClient(read_only=True)
            dispatcher = MCPDispatcher(client)
            def submit(kind, payload, **kwargs):
                state.enqueue_job(kind, payload)
                return {"status": "done", "result": {"fov": 70}}
            with mock.patch.object(client, "submit_job", side_effect=submit) as submit_job:
                for args in ({}, {"action": "get"}):
                    response = dispatcher._handle_one({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "manage_camera", "arguments": args}})
                    self.assertFalse(response["result"]["isError"])
                    self.assertEqual(submit_job.call_args.args[1]["action"], "get")
                response = dispatcher._handle_one({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "manage_camera", "arguments": {"action": "set", "fov": 80}}})
                self.assertTrue(response["result"]["isError"])
                self.assertEqual(submit_job.call_count, 2)
            with self.assertRaises(PermissionError):
                state.enqueue_job("camera", {"action": "set", "fov": 80})

    def test_invalid_camera_set_rejected_before_partial_mutation(self):
        from mcp_core import MCPDispatcher, validate_action_arguments
        client = BridgeClient()
        dispatcher = MCPDispatcher(client)
        invalid = [{"action": "focus"}, {"action": "focus", "path": "Workspace.Part", "distanceMultiplier": -1},
                   {"action": "set"}, {"action": "set", "lookAt": [0, 1, 2]},
                   {"action": "set", "fov": 70, "position": [1, 2]},
                   {"action": "set", "position": [1, True, 3]},
                   {"action": "set", "position": [1, float("nan"), 3]},
                   {"action": "set", "fov": 121}]
        with mock.patch.object(client, "submit_job") as submit:
            for args in invalid:
                for task in (False, True):
                    params = {"name": "manage_camera", "arguments": args}
                    if task:
                        params["task"] = {}
                    response = dispatcher._handle_one({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
                    self.assertEqual(response["error"]["code"], -32602, args)
            submit.assert_not_called()
        self.assertEqual(dispatcher.tasks, {})
        for args in ({}, {"action": "focus", "path": "Workspace.Part"},
                     {"action": "set", "position": [0, 1, 2], "lookAt": [0, 0, 0], "fov": 70},
                     {"action": "set", "fov": 1}):
            self.assertEqual(validate_action_arguments("manage_camera", args), "")


class TestActionSafety(unittest.TestCase):
    def test_mixed_action_classifications(self):
        from mcp_core import is_tool_call_read_only, TOOL_MAP, MCPDispatcher
        writable = [("agent_bus", {"action": "post"}),
                    ("agent_bus", {"action": "status", "agentId": "a", "status": "idle"}),
                    ("agent_bus", {"action": "export", "path": "out.md"}),
                    ("self_test", {}), ("require_fresh", {"path": "game.X"}),
                    ("open_script_in_editor", {"path": "game.X"}),
                    ("audit_ui_layout", {"deviceSweep": True}),
                    ("capture_viewport", {"saveFile": "x.png"}),
                    ("render_offline", {"saveFile": "x.png"}),
                    ("screenshot_gui", {"saveFile": "x.png"})]
        reads = [("agent_bus", {"action": "stats"}), ("agent_bus", {"action": "status"}),
                 ("agent_bus", {"action": "export"}), ("self_test", {"includeStudioSmoke": False}),
                 ("open_script_in_editor", {"listOnly": True}), ("audit_ui_layout", {}),
                 ("capture_viewport", {}), ("render_offline", {}), ("screenshot_gui", {})]
        for name, args in writable:
            self.assertFalse(is_tool_call_read_only(name, args), (name, args))
            self.assertFalse(TOOL_MAP[name]["annotations"]["readOnlyHint"], name)
        for name, args in reads:
            self.assertTrue(is_tool_call_read_only(name, args), (name, args))
        client = BridgeClient(read_only=True)
        dispatcher = MCPDispatcher(client)
        for name, args in writable:
            result = dispatcher._invoke_tool(name, args)
            self.assertTrue(result["isError"], (name, args))
        self.assertTrue(dispatcher._invoke_tool("bridge_ops", {"action": "confirm_approve", "requestId": "x"})["isError"])
        advertised = {x["name"]: x for x in dispatcher.visible_tools()}
        self.assertFalse(advertised["agent_bus"]["annotations"]["readOnlyHint"])
        self.assertNotIn("require_fresh", advertised)

    def test_rest_read_only_rejects_fresh_require(self):
        state = BridgeState(read_only=True, state_dir=tempfile.mkdtemp(prefix="arena-ro-require-"))
        try:
            with self.assertRaises(PermissionError):
                state.enqueue_job("require_fresh", {"path": "game.X"})
        finally:
            state._reaper_stop.set()
            state.tunnel.stop()


class TestLuauOfflineSafety(unittest.TestCase):
    def test_unavailable_toolchain_does_not_dispatch_to_studio(self):
        from mcp_core import MCPDispatcher
        client = BridgeClient(read_only=True)
        dispatcher = MCPDispatcher(client)
        with mock.patch("mcp_core._find_tool_binary", return_value=None), mock.patch.object(client, "submit_job") as submit:
            response = dispatcher._handle_one({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "luau_check", "arguments": {"code": "return 1"}}})
            result = response["result"]["structuredContent"]
            self.assertFalse(result["ok"])
            self.assertFalse(result["toolchainAvailable"])
            self.assertEqual(result["results"][0]["reason"], "toolchain_unavailable")
            submit.assert_not_called()

    def test_compiler_only_reports_missing_global_analysis(self):
        from mcp_core import check_luau_source
        def binary(name):
            return "/fake/luau-compile" if name == "luau-compile" else None
        proc = mock.Mock(returncode=0)
        with mock.patch("mcp_core._find_tool_binary", side_effect=binary), mock.patch("mcp_core.subprocess.run", return_value=proc):
            result = check_luau_source("return unknownGlobal")
        self.assertTrue(result["ok"])  # Only available checks passed, not proof of global validity.
        self.assertEqual(result["checksPerformed"], {"syntax": True, "undefinedGlobals": False, "unusedLocals": False})
        self.assertIn("analyzer unavailable", result["warnings"][0])

    def test_tool_discovery_skips_incompatible_bundled_binaries(self):
        from mcp_core import _find_tool_binary
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = root / "tools"
            tools.mkdir()
            linux = tools / "luau-compile"
            linux.write_bytes(b"\x7fELF" + b"fake")
            linux.chmod(0o755)
            exe = tools / "luau-compile.exe"
            exe.write_bytes(b"MZfake")
            exe.chmod(0o755)
            with mock.patch("mcp_core.__file__", str(root / "mcp_core.py")), mock.patch("shutil.which", return_value=None):
                with mock.patch("mcp_core.sys.platform", "win32"):
                    self.assertEqual(_find_tool_binary("luau-compile"), str(exe))
                with mock.patch("mcp_core.sys.platform", "darwin"):
                    self.assertIsNone(_find_tool_binary("luau-compile"))
                with mock.patch("mcp_core.sys.platform", "linux"):
                    self.assertEqual(_find_tool_binary("luau-compile"), str(linux))

    def test_tool_discovery_never_changes_executable_permissions(self):
        from mcp_core import _find_tool_binary
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tools").mkdir()
            binary = root / "tools" / "luau-compile"
            binary.write_text("not executable")
            binary.chmod(0o600)
            with mock.patch("mcp_core.__file__", str(root / "mcp_core.py")), mock.patch("shutil.which", return_value=None), mock.patch("os.chmod") as chmod:
                self.assertIsNone(_find_tool_binary("luau-compile"))
                chmod.assert_not_called()
            self.assertEqual(binary.stat().st_mode & 0o777, 0o600)


class TestOnboardingAudit(unittest.TestCase):
    def test_cycles_duplicates_missing_fields_and_pagination(self):
        from onboarding_auditor import audit_onboarding_plan
        items = [
            {"id": "start", "objective": "Do one action", "completionEvidence": "Server event", "recovery": "Retry", "prerequisites": ["finish", "missing", "missing", "duplicate"]},
            {"id": "finish", "objective": "Finish", "completionEvidence": "Server state", "recovery": "Resume", "prerequisites": ["start"]},
            {"id": "self", "objective": "", "completionEvidence": "server", "recovery": "", "prerequisites": ["self"]},
            {"id": "duplicate", "objective": "A", "completionEvidence": "state", "recovery": "retry"},
            {"id": "duplicate", "objective": "B", "completionEvidence": "state", "recovery": "retry"},
        ]
        whole = audit_onboarding_plan(items, limit=100)
        self.assertFalse(whole["ok"])
        self.assertEqual([x["code"] for x in whole["issues"]],
                         ["unknown_prerequisite", "duplicate_prerequisite", "ambiguous_prerequisite", "missing_field", "missing_field", "duplicate_id", "prerequisite_cycle", "prerequisite_cycle"])
        self.assertEqual([x["ids"] for x in whole["issues"] if x["code"] == "prerequisite_cycle"], [["start", "finish"], ["self"]])
        paged, cursor = [], 0
        while cursor is not None:
            page = audit_onboarding_plan(items, cursor=cursor, limit=2)
            paged.extend(page["issues"])
            cursor = page["nextCursor"]
        self.assertEqual(paged, whole["issues"])
        self.assertEqual(audit_onboarding_plan(items, cursor=999)["issues"], [])
        valid = [{"id": "start", "objective": "Move", "completionEvidence": "Server state", "recovery": "Retry"},
                 {"id": "next", "objective": "Collect", "completionEvidence": "Server verifies", "recovery": "Rejoin", "prerequisites": ["start"]}]
        self.assertTrue(audit_onboarding_plan(valid)["ok"])
        self.assertIn("Structural checks only", audit_onboarding_plan(valid)["warnings"][0])

    def test_bounded_and_malformed_inputs(self):
        from onboarding_auditor import audit_onboarding_plan
        for milestones, cursor, limit in (([], 0, 50), ([{}], 0, 50), ([{"id": " a "}], 0, 50),
                                           ([{"id": "a", "objective": None}], 0, 50),
                                           ([{"id": "a", "prerequisites": [" x "]}], 0, 50),
                                           ([{"id": "a", "prerequisites": ["x"] * 21}], 0, 50),
                                           ([{"id": "a"}] * 101, 0, 50), ([{"id": "a"}], True, 50),
                                           ([{"id": "a"}], 0, 101)):
            with self.assertRaises(ValueError):
                audit_onboarding_plan(milestones, cursor, limit)

    def test_read_only_dispatch_without_peer(self):
        from mcp_core import MCPDispatcher, is_tool_call_read_only
        client = BridgeClient(read_only=True)
        dispatcher = MCPDispatcher(client)
        args = {"milestones": [{"id": "first", "objective": "Try", "completionEvidence": "Server", "recovery": "Retry"}]}
        self.assertTrue(is_tool_call_read_only("audit_onboarding_plan", args))
        with mock.patch.object(client, "submit_job") as jobs:
            result = dispatcher._handle_one({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "audit_onboarding_plan", "arguments": args}})
            self.assertTrue(result["result"]["structuredContent"]["ok"])
            jobs.assert_not_called()


class TestLocalizationAudit(unittest.TestCase):
    def test_offline_checks_and_pagination(self):
        from mcp_core import is_tool_call_read_only
        entries = [
            {"key": "reward", "source": "Earn {amount} for {player} {{literal}}", "translations": {"fr": "{player} gagne {amount} {{littéral}}", "th": "ได้ {amount}"}},
            {"key": "reward", "source": "Welcome {player}", "translations": {"fr": "Bonjour {other}", "th": "  "}},
        ]
        args = {"entries": entries, "locales": ["fr", "th"], "limit": 2}
        self.assertTrue(is_tool_call_read_only("audit_localization", args))
        page = execute_tool(None, "audit_localization", args)
        self.assertFalse(page["ok"])
        self.assertEqual(page["totalIssues"], 4)
        self.assertEqual(page["issues"][0]["missing"], ["player"])
        self.assertEqual(page["issues"][1]["code"], "duplicate_key")
        next_page = execute_tool(None, "audit_localization", dict(args, cursor=page["nextCursor"]))
        self.assertEqual(next_page["issues"][0]["missing"], ["player"])
        self.assertEqual(next_page["issues"][0]["extra"], ["other"])
        self.assertEqual(next_page["issues"][1]["code"], "missing_translation")
        self.assertIsNone(next_page["nextCursor"])
        self.assertEqual(execute_tool(None, "audit_localization", dict(args, cursor=99))["issues"], [])
        self.assertIn("not validate", page["limitations"][1])
        valid = execute_tool(None, "audit_localization", {"entries": [entries[0]], "locales": ["fr"]})
        self.assertTrue(valid["ok"])
        self.assertEqual(valid["totalIssues"], 0)

    def test_roblox_parameters_and_review_diagnostics(self):
        from localization_auditor import audit_localization
        entry = {"key": "race", "source": "{race_time:fixed} seconds and {1:num}; {Place_Name:translate}",
                 "translations": {"es": "{Place_Name:translate}: {1:num} y {race_time:fixed} segundos"}}
        self.assertTrue(audit_localization([entry], ["es"])["ok"])
        changed = dict(entry, translations={"es": "{Place_Name:translate}: {1:int} y {race_time} segundos"})
        result = audit_localization([changed], ["es"], limit=1)
        self.assertEqual(result["totalIssues"], 2)
        self.assertEqual(result["issues"][0]["code"], "format_mismatch")
        self.assertEqual(result["issues"][0]["parameter"], "1")
        self.assertEqual(result["issues"][0]["sourceFormats"], ["num"])
        self.assertEqual(result["issues"][0]["translationFormats"], ["int"])
        following = audit_localization([changed], ["es"], cursor=result["nextCursor"], limit=1)
        self.assertEqual(following["issues"][0]["parameter"], "race_time")
        self.assertEqual(following["issues"][0]["translationFormats"], [""])
        unsupported = audit_localization([{"key": "x", "source": "{x:unknown}", "translations": {"es": "{x:unknown} and {broken"}}], ["es"])
        self.assertFalse(unsupported["ok"])
        self.assertEqual([i["code"] for i in unsupported["issues"]], ["unsupported_placeholder"] * 3)
        self.assertEqual([i["field"] for i in unsupported["issues"]], ["source", "translation", "translation"])
        missing = audit_localization([dict(entry, translations={"es": "{1:num} {race_time:fixed}"})], ["es"])
        self.assertEqual(missing["issues"][0]["missing"], ["Place_Name"])
        large = audit_localization([{"key": "x", "source": "{" * 3999 + "x", "translations": {"es": "ok"}}], ["es"], cursor=10000)
        self.assertEqual(large["issues"], [])
        self.assertFalse(large["ok"])  # The final unmatched brace is reported even off-page.
        many = audit_localization([{"key": "x", "source": "ok", "translations": {"es": "}a" * 2000}}], ["es"], cursor=1999, limit=2)
        self.assertEqual(many["totalIssues"], 2000)
        self.assertEqual(len(many["issues"]), 1)
        self.assertFalse(many["ok"])

    def test_invalid_and_bounded_inputs(self):
        from localization_auditor import audit_localization
        good = {"key": "x", "source": "hi", "translations": {"fr": "salut"}}
        for entries, locales, cursor, limit in (([], ["fr"], 0, 1), ([good] * 201, ["fr"], 0, 1),
                ([good], [], 0, 1), ([good], ["fr", "fr"], 0, 1), ([good], ["fr"], -1, 1),
                ([good], ["fr"], 0, 201), ([{"key": "x", "source": "a", "translations": {"fr": None}}], ["fr"], 0, 1),
                ([dict(good, source="x" * 4001)], ["fr"], 0, 1)):
            with self.assertRaises(ValueError):
                audit_localization(entries, locales, cursor, limit)
        from mcp_core import search_skills_helper, apply_skill_helper
        self.assertIn("roblox-localization", [item["name"] for item in search_skills_helper("localization", limit=100)["results"]])
        self.assertIn("audit_localization", apply_skill_helper("roblox-localization")["instructions"])


class TestConnectionDiagnostics(unittest.TestCase):
    def test_registry_only_reports_versions_and_redacts_untrusted_fields(self):
        from mcp_core import MCP_SERVER_VERSION, diagnose_connection_helper, is_tool_call_read_only
        client = mock.Mock()
        client.direct_dispatch = None
        client.read_only = True
        # Derive the version from the module under test instead of hardcoding it:
        # a release bump must not turn this into a false "older plugin" failure.
        client.health.return_value = {"ok": True, "version": MCP_SERVER_VERSION, "authMode": "scoped", "read_only": False, "queuePaused": True}
        client.peers.return_value = {"peers": [
            {"online": True, "dm": "edit", "pluginVersion": "4.1", "peerId": "secret-peer"},
            {"online": True, "dm": "server", "hasClient": True, "pluginVersion": "2.0"},
            {"online": True, "dm": "edit", "pluginVersion": "secret-token"},
            {"online": False, "dm": "server", "pluginVersion": "1.0"},
        ]}
        result = diagnose_connection_helper(client)
        self.assertTrue(is_tool_call_read_only("diagnose_connection", {}))
        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["readAccess"], "authorized")
        self.assertTrue(result["bridge"]["inspectorMode"])
        self.assertEqual(result["peers"]["online"], 3)
        self.assertEqual(result["peers"]["stale"], 1)
        self.assertEqual(result["peers"]["clientAdvertised"], 1)
        self.assertEqual(result["pluginVersions"]["unknownOnline"], 1)
        codes = {check["code"] for check in result["checks"]}
        self.assertTrue({"older_plugin_reported", "unknown_plugin_version", "queue_paused"} <= codes)
        self.assertNotIn("secret", json.dumps(result))
        client.submit_job.assert_not_called()
        client.peers.return_value = {"peers": [{"online": True, "dm": "edit", "pluginVersion": MCP_SERVER_VERSION}]}
        self.assertNotIn("older_plugin_reported", [x["code"] for x in diagnose_connection_helper(client)["checks"]])
        client.health.return_value["authMode"] = "open"
        self.assertEqual(diagnose_connection_helper(client)["readAccess"], "open")
        client.peers.return_value = {"peers": [{"dm": "edit"}]}
        invalid = diagnose_connection_helper(client)
        self.assertEqual(invalid["status"], "bridge_error")
        self.assertEqual(invalid["peers"]["status"], "unknown")

    def test_http_auth_offline_no_peer_and_cli_without_secrets(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        port = _free_port()
        httpd, state = create_http_server(host="127.0.0.1", port=port, token="correct-secret", state_dir=temp.name)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def stop():
            state._reaper_stop.set()
            state.tunnel.stop()
            httpd.shutdown()
            httpd.server_close()
        self.addCleanup(stop)
        base = f"http://127.0.0.1:{port}"
        good = execute_tool(BridgeClient(base_url=base, token="correct-secret"), "diagnose_connection", {})
        self.assertEqual(good["status"], "no_peer")
        self.assertEqual(good["readAccess"], "authorized")
        wrong = execute_tool(BridgeClient(base_url=base, token="wrong-secret"), "diagnose_connection", {})
        self.assertEqual(wrong["status"], "auth_error")
        self.assertEqual(wrong["readAccess"], "denied")
        self.assertNotIn("wrong-secret", json.dumps(wrong))
        self.assertNotIn("correct-secret", json.dumps(good))
        env = dict(os.environ, BRIDGE_URL=base, BRIDGE_TOKEN="wrong-secret")
        result = subprocess.run([sys.executable, str(HERE / "arena_studio.py"), "doctor"], env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["status"], "auth_error")
        self.assertNotIn("wrong-secret", result.stdout + result.stderr)
        unreachable = execute_tool(BridgeClient(base_url=f"http://127.0.0.1:{_free_port()}", token="unique-private-token"), "diagnose_connection", {})
        self.assertEqual(unreachable["status"], "bridge_unreachable")
        self.assertNotIn("unique-private-token", json.dumps(unreachable))

    def test_error_details_are_not_echoed(self):
        from mcp_core import diagnose_connection_helper
        client = mock.Mock()
        client.health.side_effect = RuntimeError("secret-token https://bad.example/?token=secret-token")
        report = diagnose_connection_helper(client)
        self.assertEqual(report["status"], "bridge_error")
        self.assertNotIn("secret-token", json.dumps(report))
        client.peers.assert_not_called()


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

    def test_mixed_actions_respect_read_scope_across_mcp_and_rest(self):
        reader = self.state.tokens.create("reader", role="read", rate_limit=100, burst=100)["token"]
        writer = self.state.tokens.create("writer", role="write", rate_limit=100, burst=100)["token"]
        target = os.path.join(self.tmp, "should-not-write.png")
        for name, args in (("agent_bus", {"action": "post", "body": "denied"}),
                           ("agent_bus", {"action": "status", "agentId": "a", "status": "idle"}),
                           ("agent_bus", {"action": "export", "path": target}),
                           ("self_test", {}), ("require_fresh", {"path": "game.X"}),
                           ("render_offline", {"tree": {"name": "Workspace"}, "saveFile": target}),
                           ("audit_ui_layout", {"prefab": "hud_modern_shell", "deviceSweep": True})):
            rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}}
            _, body = self._req("/mcp", method="POST", body=rpc, token=reader)
            self.assertEqual(body["result"]["structuredContent"]["reason"], "insufficient_scope", (name, body))
        self.assertFalse(os.path.exists(target))
        malformed = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "agent_bus", "arguments": ["bad"]}}
        code, reply = self._req("/mcp", method="POST", body=malformed, token=reader)
        self.assertEqual(code, 200)
        self.assertEqual(reply["error"]["code"], -32602)
        for route, method, body in (("/api/bus?action=register&name=reader", "GET", None),
                                    ("/api/bus?action=status&agentId=x&status=idle", "GET", None),
                                    ("/api/bus", "POST", {"action": "status", "agentId": "x", "status": "idle"}),
                                    ("/api/bus", "POST", {"action": "export", "path": target})):
            self.assertEqual(self._req(route, method=method, body=body, token=reader)[0], 403, route)
        self.assertEqual(self._req("/api/bus?action=stats", token=reader)[0], 200)
        rpc = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "agent_bus", "arguments": {"action": "register", "name": "writer"}}}
        _, body = self._req("/mcp", method="POST", body=rpc, token=writer)
        self.assertFalse(body["result"]["isError"])
        op_rpc = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "bridge_ops", "arguments": {"action": "queue_pause"}}}
        _, op_body = self._req("/mcp", method="POST", body=op_rpc, token=writer)
        self.assertEqual(op_body["result"]["structuredContent"]["reason"], "insufficient_scope")
        self.assertFalse(self.state.queue_paused)
        with mock.patch("server.run_bridge_self_test", return_value={"ok": True}) as test_run:
            self.assertEqual(self._req("/api/self-test", token=reader)[0], 200)
            self.assertFalse(test_run.call_args.kwargs["include_studio_smoke"])
            self.assertEqual(self._req("/api/self-test", token=self.admin_token)[0], 200)
            self.assertTrue(test_run.call_args.kwargs["include_studio_smoke"])
            self.state.read_only = True
            try:
                self.assertEqual(self._req("/api/self-test", token=self.admin_token)[0], 200)
                self.assertFalse(test_run.call_args.kwargs["include_studio_smoke"])
                self.assertEqual(self._req("/api/bus", method="POST", body={"action": "register", "name": "admin"}, token=self.admin_token)[0], 403)
            finally:
                self.state.read_only = False

    def test_poll_requires_write_and_does_not_claim_with_read_token(self):
        reader = self.state.tokens.create("reader", role="read", rate_limit=100, burst=100)["token"]
        writer = self.state.tokens.create("plugin", role="write", rate_limit=100, burst=100)["token"]
        job, _ = self.state.enqueue_job("ping")
        code, _ = self._req("/api/poll?peerId=test-peer&dm=edit", token=reader)
        self.assertEqual(code, 403)
        self.assertEqual(self.state.jobs[job["id"]]["status"], "queued")
        self.assertNotIn("test-peer", self.state.peers)
        code, result = self._req("/api/poll?peerId=test-peer&dm=edit", token=writer)
        self.assertEqual(code, 200)
        self.assertEqual(result["job"]["id"], job["id"])

    def test_tasks_are_bound_to_authenticated_token_even_in_batches(self):
        a = self.state.tokens.create("same-label", role="read", rate_limit=100, burst=100)
        b = self.state.tokens.create("same-label", role="read", rate_limit=100, burst=100)
        request = {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "_meta": {"x-arena-principal": "spoof"},
                   "params": {"name": "search_tools", "arguments": {"query": "performance"}, "task": {}}}
        _, body = self._req("/mcp", method="POST", body=[request], token=a["token"])
        task_id = body[0]["result"]["taskId"]
        for method in ("tasks/get", "tasks/result", "tasks/update", "tasks/cancel"):
            call = {"jsonrpc": "2.0", "id": method, "method": method,
                    "_meta": {"x-arena-principal": "token:" + a["id"]},
                    "params": {"taskId": task_id, "timeoutMs": 10, "_meta": {"x-arena-principal": "token:" + a["id"]}}}
            _, denied = self._req("/mcp", method="POST", body=call, token=b["token"])
            self.assertEqual(denied["error"]["message"], "Unknown task", method)
        _, listed = self._req("/mcp", method="POST", body={"jsonrpc": "2.0", "id": 2, "method": "tasks/list"}, token=b["token"])
        self.assertEqual(listed["result"]["tasks"], [])
        _, owner_list = self._req("/mcp", method="POST", body={"jsonrpc": "2.0", "id": 3, "method": "tasks/list"}, token=a["token"])
        self.assertEqual(owner_list["result"]["tasks"][0]["taskId"], task_id)
        _, owner_get = self._req("/mcp", method="POST", body={"jsonrpc": "2.0", "id": 4, "method": "tasks/get", "params": {"taskId": task_id}}, token=a["token"])
        self.assertEqual(owner_get["result"]["taskId"], task_id)

    def test_confirmation_identity_cannot_be_spoofed(self):
        from confirm_gate import ConfirmGate
        a = self.state.tokens.create("same-label", role="write", rate_limit=100, burst=100)
        b = self.state.tokens.create("same-label", role="write", rate_limit=100, burst=100)
        # Opted in deliberately: this test isolates *principal binding*, and the
        # default refusal of client answers would otherwise mask it (that policy
        # has its own test).
        gate = ConfirmGate(mode="write", trust_client_answers=True)
        previous = self.state.mcp_dispatcher.gate
        self.state.mcp_dispatcher.gate = gate
        self.addCleanup(setattr, self.state.mcp_dispatcher, "gate", previous)
        meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {"elicitation": {}}, "x-arena-principal": "spoof"}
        params = {"name": "delete_instance", "arguments": {"paths": ["Workspace.Baseplate"]}}
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "_meta": meta, "params": params}
        _, response = self._req("/mcp", method="POST", body=[request], token=a["token"])
        first = response[0]["result"]
        self.assertEqual(first["resultType"], "input_required")
        first_id = first["structuredContent"]["requestId"]
        self.assertEqual(gate.pending(first_id).principal, "token:" + a["id"])
        retry = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "_meta": dict(meta, **{"x-arena-principal": "token:" + a["id"]}),
                 "params": {**params, "requestState": first["requestState"], "inputResponses": {"confirm": {"action": "accept"}}}}
        _, rejected = self._req("/mcp", method="POST", body=retry, token=b["token"])
        self.assertEqual(rejected["result"]["structuredContent"]["reason"], "principal_mismatch")
        self.assertIsNotNone(gate.pending(first_id))
        self.assertEqual(len(self.state.jobs), 0)
        for pending in list(gate.status()["pending"]):
            gate.cancel(pending["requestId"])

    def test_read_scoped_camera_default_in_inspector(self):
        reader = self.state.tokens.create("camera-reader", role="read", rate_limit=100, burst=100)["token"]
        self.state.read_only = True
        self.state.mcp_client.read_only = True
        def submit(kind, payload, **kwargs):
            self.state.enqueue_job(kind, payload)
            return {"status": "done", "result": {"fov": 70}}
        with mock.patch.object(self.state.mcp_client, "submit_job", side_effect=submit) as jobs:
            def call(args):
                return self._req("/mcp", method="POST", token=reader, body={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "manage_camera", "arguments": args}})[1]
            self.assertFalse(call({})["result"]["isError"])
            self.assertEqual(jobs.call_args.args[1]["action"], "get")
            blocked = call({"action": "set", "fov": 80})
            self.assertTrue(blocked["result"]["isError"])
            self.assertEqual(blocked["result"]["structuredContent"]["reason"], "insufficient_scope")
            self.assertEqual(jobs.call_count, 1)

    def test_local_convenience_rejects_cross_origin_and_untrusted_host(self):
        job, _ = self.state.enqueue_job("ping")
        def request(path, headers):
            req = urllib.request.Request(self.base + path, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, json.load(resp)
            except urllib.error.HTTPError as exc:
                return exc.code, json.loads(exc.read())
        cases = [{"Origin": "https://attacker.example"},
                 {"Origin": "null"},
                 {"Sec-Fetch-Site": "cross-site"},
                 {"Host": f"attacker.example:{self.port}"},
                 {"X-Forwarded-For": "203.0.113.7"}]
        for headers in cases:
            code, boot = request("/api/local-boot", headers)
            self.assertEqual(code, 200)
            self.assertFalse(boot["local"], headers)
            self.assertEqual(boot["token"], "")
            self.assertEqual(boot["bridgeDir"], "")
            for path in ("/api/agent-guide", "/api/jobs/" + job["id"]):
                self.assertEqual(request(path, headers)[0], 401, (headers, path))
        # v6.1.0 (P0-S2): being on loopback is no longer a credential. An
        # unauthenticated local request still boots (200, so the dashboard can
        # render its token banner) but gets no token; an admin one does.
        for headers in ({}, {"Origin": self.base, "Sec-Fetch-Site": "same-origin"}):
            code, boot = request("/api/local-boot", headers)
            self.assertEqual(code, 200)
            self.assertTrue(boot["local"])
            self.assertEqual(boot["token"], "")
            self.assertTrue(boot["tokenWithheld"])
            # Read-only discovery endpoints keep the loopback convenience (they
            # carry no credential); the credential itself is what stopped being
            # handed out.
            self.assertEqual(request("/api/agent-guide", headers)[0], 200)
            authed = dict(headers, **{"X-Arena-Token": self.admin_token})
            self.assertEqual(request("/api/local-boot", authed)[1]["token"], self.admin_token)
            self.assertEqual(request("/api/agent-guide", authed)[0], 200)

    def test_kit_assets_cannot_escape_to_sibling_prefix_directory(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp, mock.patch("image_pipeline.find_kit_root", return_value=Path(tmp)):
            root = Path(tmp) / "assets"
            for folder in ("images", "audio", "images-secret", "audio-secret"):
                (root / folder).mkdir(parents=True)
            (root / "images" / "ok.png").write_bytes(b"image")
            (root / "audio" / "ok.wav").write_bytes(b"audio")
            (root / "images-secret" / "secret.png").write_bytes(b"private")
            (root / "audio-secret" / "secret.wav").write_bytes(b"private")
            (root / "images" / "link.png").symlink_to(root / "images-secret" / "secret.png")
            (root / "audio" / "link.wav").symlink_to(root / "audio-secret" / "secret.wav")
            for kind, good, bad in (("image", "ok.png", "../images-secret/secret.png"),
                                     ("audio", "ok.wav", "../audio-secret/secret.wav")):
                self.assertEqual(self._req(f"/api/kit/{kind}/{good}")[0], 200)
                self.assertEqual(self._req(f"/api/kit/{kind}/{bad}")[0], 404)
                link = "link.png" if kind == "image" else "link.wav"
                self.assertEqual(self._req(f"/api/kit/{kind}/{link}")[0], 404)

    def test_read_scoped_luau_check_never_falls_back_to_studio(self):
        reader = self.state.tokens.create("source-reader", role="read", rate_limit=100, burst=100)["token"]
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "luau_check", "arguments": {"code": "return 1"}}}
        with mock.patch("mcp_core._find_tool_binary", return_value=None), mock.patch.object(self.state.mcp_client, "submit_job") as jobs:
            code, response = self._req("/mcp", method="POST", token=reader, body=request)
            self.assertEqual(code, 200)
            result = response["result"]["structuredContent"]
            self.assertFalse(result["toolchainAvailable"])
            self.assertFalse(result["ok"])
            jobs.assert_not_called()
        self.assertEqual(len(self.state.jobs), 0)

    def test_read_scoped_onboarding_audit_without_studio(self):
        reader = self.state.tokens.create("plan-reader", role="read", rate_limit=100, burst=100)["token"]
        args = {"milestones": [{"id": "start", "prerequisites": ["missing"]}]}
        code, response = self._req("/mcp", method="POST", token=reader, body={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "audit_onboarding_plan", "arguments": args}})
        self.assertEqual(code, 200)
        self.assertFalse(response["result"]["isError"])
        result = response["result"]["structuredContent"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["totalIssues"], 4)
        self.assertEqual(len(self.state.jobs), 0)

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

    def test_a_peer_crash_loop_is_capped_counted_and_audited(self):
        """P1-B1: a payload that reliably kills the peer was requeued forever.

        The orphan path incremented `attempts` and never compared it with
        anything, so a `run_luau` that hard-crashed the place came back on every
        sweep, each time consuming another peer, and every requeue looked like
        progress in the queue view. The cap is `max(2, maxRetries + 2)` claims.
        """
        state = self.state
        state.clear_queue()
        job, evt = state.enqueue_job("run_luau", payload={"code": "while true do end"},
                                     note="crashy", max_retries=1)
        peer = state.record_poll({"peer": ["peer-crashy"], "dm": ["edit"], "place": ["p"],
                                  "placeId": ["1"]})
        cap = 3  # maxRetries(1) + 2
        claims = 0
        requeues = 0
        result = {}
        for _ in range(8):
            # The peer comes back, takes the job, and dies: re-register it (which
            # is what a reconnecting Studio does), claim, age the claim past the
            # orphan threshold, then drop it offline and let the reaper look.
            peer = state.record_poll({"peer": ["peer-crashy"], "dm": ["edit"], "place": ["p"],
                                      "placeId": ["1"]})
            claimed = state.poll_for_job(peer, wait_seconds=0.05)
            if not claimed:
                break
            self.assertEqual(claimed["id"], job["id"])
            claims += 1
            with state.lock:
                state.jobs[job["id"]]["claimed"] = time.time() - 60
                for pid in list(state.peers):
                    state.peers[pid]["lastPoll"] = 0
            result = state.reap_jobs()
            if state.jobs[job["id"]]["status"] == "error":
                break
            requeues += result.get("recovered", 0)

        final = state.jobs[job["id"]]
        self.assertEqual(final["status"], "error", f"the crash loop was not capped: {final}")
        self.assertIn("peer_crash_loop", final["error"])
        self.assertIn(str(cap), final["error"], "the message must say what the cap was")
        self.assertFalse(final["ok"])
        self.assertEqual(claims, cap, f"expected exactly {cap} peer claims, saw {claims}")
        self.assertEqual(final["attempts"], cap, "`attempts` must count claims, nothing else")
        self.assertEqual(final["requeues"], cap - 1, "one fewer recovery than claims")
        self.assertTrue(evt.is_set(), "a waiter must be woken when the job fails")
        self.assertEqual(result.get("crashLoops"), 1, result)
        self.assertIn("job.crash_loop",
                      [e["action"] for e in state.telemetry.audit.tail(60)["entries"]])
        kinds = [e["kind"] for e in state.telemetry.events.since(0, 200).get("events") or []]
        self.assertIn("job.crash_loop", kinds)

        # A job with no retries configured still gets the floor of 2 claims, so a
        # single flaky peer does not fail work that could have succeeded.
        state.clear_queue()
        plain, _ = state.enqueue_job("ping", note="no-retries")
        for expected_status in ("queued", "error"):
            peer = state.record_poll({"peer": ["peer-crashy"], "dm": ["edit"], "place": ["p"],
                                      "placeId": ["1"]})
            self.assertIsNotNone(state.poll_for_job(peer, wait_seconds=0.05),
                                 "the floor of 2 claims was not offered")
            with state.lock:
                state.jobs[plain["id"]]["claimed"] = time.time() - 60
                for pid in list(state.peers):
                    state.peers[pid]["lastPoll"] = 0
            state.reap_jobs()
            self.assertEqual(state.jobs[plain["id"]]["status"], expected_status)
        self.assertEqual(state.jobs[plain["id"]]["attempts"], 2)
        self.assertIn("peer_crash_loop", state.jobs[plain["id"]]["error"])
        state.clear_queue()

    def test_fleet_state_survives_a_corrupt_file_and_a_crash_mid_write(self):
        """P1-B4: `_save_fleet` used `write_text` on the live path, so a crash
        mid-write left a truncated `fleet.json` that `_load_fleet` swallowed with
        `except (OSError, ValueError): return` — pins and labels silently reset,
        no diagnostic anywhere, and the next save overwrote the evidence."""
        with tempfile.TemporaryDirectory(prefix="arena-v61-fleet-") as tmp:
            state_dir = Path(tmp)

            def state_with(**kw):
                st = bridge_server.BridgeState(token="fleet-token", state_dir=state_dir, **kw)
                self.addCleanup(st._reaper_stop.set)
                return st

            good = state_with()
            good.studio_pin = "peer-A"
            good.peer_labels = {"peer-A": "main place"}
            good._save_fleet()
            self.assertEqual(json.loads((state_dir / "fleet.json").read_text(encoding="utf-8")),
                             {"pinned": "peer-A", "labels": {"peer-A": "main place"}})

            reloaded = state_with()
            self.assertEqual(reloaded.studio_pin, "peer-A")
            self.assertEqual(reloaded.peer_labels, {"peer-A": "main place"})
            self.assertIsNone(reloaded.fleet_load_warning)
            self.assertIsNone(reloaded.fleet_snapshot()["fleetLoadWarning"])

            # Exactly what a crash mid-write leaves behind.
            truncated = '{"pinned": "peer-A", "lab'
            (state_dir / "fleet.json").write_text(truncated, encoding="utf-8")
            broken = state_with()
            self.assertIsNone(broken.studio_pin, "defaults are used")
            self.assertEqual(broken.peer_labels, {})
            self.assertIn("unreadable", broken.fleet_load_warning or "")
            self.assertIn("fleet.json", broken.fleet_snapshot()["fleetLoadWarning"] or "")
            kept = sorted(state_dir.glob("fleet.json.corrupt-*"))
            self.assertEqual(len(kept), 1, "the corrupt bytes must be kept, not overwritten")
            self.assertEqual(kept[0].read_text(encoding="utf-8"), truncated)
            self.assertFalse((state_dir / "fleet.json").exists(),
                             "the unreadable file is moved aside, so the next save starts clean")
            kinds = [e["kind"] for e in broken.telemetry.events.since(0, 200).get("events") or []]
            self.assertIn("fleet.load_failed", kinds)

            # A non-object payload is refused too, rather than half-applied.
            (state_dir / "fleet.json").write_text("[1, 2, 3]", encoding="utf-8")
            wrong_shape = state_with()
            self.assertIn("expected a JSON object", wrong_shape.fleet_load_warning or "")
            self.assertIsNone(wrong_shape.studio_pin)

            # And the atomic write itself: a stale temp file from an interrupted
            # save is never mistaken for the live one, and a successful save
            # leaves no temp file behind.
            (state_dir / "fleet.json").unlink(missing_ok=True)  # already quarantined above
            (state_dir / "fleet.json.tmp").write_text("{ partial", encoding="utf-8")
            after = state_with()
            self.assertIsNone(after.fleet_load_warning, "a stale .tmp is not the live file")
            after.studio_pin = "peer-B"
            after._save_fleet()
            self.assertEqual(json.loads((state_dir / "fleet.json").read_text(encoding="utf-8"))["pinned"],
                             "peer-B")
            self.assertFalse((state_dir / "fleet.json.tmp").exists(),
                             "os.replace consumed the temp file")

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

    def test_inspector_rest_blocks_snapshot_and_fleet_writes(self):
        with mock.patch.object(self.state, "_persist_snapshot"):
            saved = self.state.handle_snapshots({"action": "save", "label": "existing", "scripts": []})
        count = len(self.state.snapshots)
        self.state.read_only = True
        for action in ({"action": "save", "label": "blocked", "scripts": []},):
            code, _ = self._req("/api/snapshots", method="POST", token=self.admin_token, body=action)
            self.assertEqual(code, 403)
        self.assertEqual(len(self.state.snapshots), count)
        self.assertEqual(self._req("/api/snapshots", token=self.admin_token)[1]["count"], count)
        self.assertEqual(self._req("/api/snapshots/" + saved["id"], token=self.admin_token)[0], 200)
        for action in ({"action": "label", "peerId": "any", "label": "blocked"},
                       {"action": "clear_active"}, {"action": "set_active", "peerId": "any"}):
            code, _ = self._req("/api/studios", method="POST", token=self.admin_token, body=action)
            self.assertEqual(code, 403)
        self.assertEqual(self.state.peer_labels, {})
        self.assertIsNone(self.state.studio_pin)
        self.assertEqual(self._req("/api/studios", token=self.admin_token)[0], 200)
        with self.assertRaises(PermissionError):
            self.state.handle_snapshots({"action": "save"})
        with self.assertRaises(PermissionError):
            self.state.fleet_action({"action": "clear_active"})

    def test_inspector_jobs_fail_closed_on_unclassified_and_new_writes(self):
        self.state.read_only = True
        before = len(self.state.jobs)
        denied = [("batch_write", {"files": [{"parent": "ServerStorage", "name": "A", "source": "return 1"}]}),
                  ("scatter_props", {"sourcePath": "Workspace.Part", "points": [[0, 0, 0, 0]]}),
                  ("future_job", {}), ("require_fresh", {"path": "game.X"}),
                  ("camera", {"action": "set"}), ("find_replace_scripts", {"dryRun": False})]
        for kind, payload in denied:
            code, body = self._req("/api/jobs", method="POST", token=self.admin_token,
                                   body={"type": kind, "payload": payload})
            self.assertEqual(code, 403, (kind, body))
        self.assertEqual(len(self.state.jobs), before)
        allowed = [("ping", {}), ("mesh_capabilities", {}), ("camera", {"action": "get"}),
                   ("terrain", {"action": "materials"}), ("find_replace_scripts", {"dryRun": True}),
                   ("device_simulator", {"action": "status"})]
        for kind, payload in allowed:
            code, body = self._req("/api/jobs", method="POST", token=self.admin_token,
                                   body={"type": kind, "payload": payload})
            self.assertEqual(code, 201, (kind, body))
        self.assertEqual(len(self.state.jobs), before + len(allowed))
        self.state.read_only = False
        code, _ = self._req("/api/jobs", method="POST", token=self.admin_token,
                            body={"type": "future_job", "payload": {}})
        self.assertEqual(code, 201)  # Non-Inspector extensions keep their previous behavior.

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
        self.assertEqual(health["version"], "6.1.0")
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
        # v6.1.0 (P4-D1): the page is a shell that references /static/; the
        # behaviour lives in dashboard.css and dashboard.js.
        html = bridge_server.dashboard_html()
        self.assertIn('<link rel="stylesheet" href="/static/dashboard.css?v=', html)
        self.assertIn('<script src="/static/dashboard.js?v=', html)
        self.assertNotIn("__BRIDGE_VERSION__", html)
        # No inline script or handler can survive a `script-src 'self'` CSP, so
        # none may be emitted: this is what keeps P0-S3 from regressing.
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)")
        self.assertNotRegex(html, r"\son[a-z]+\s*=")

        bundle = dashboard_bundle()
        for needle in ("copyMcpConfig", "Copy MCP config (tunnel)", "@media(max-width:720px)",
                       "grid-template-columns:repeat(2,minmax(0,1fr))",
                       "Reconnecting (${Math.round(wait/1000)}s)", "EV_DELAY * 2",
                       "overflow-x:hidden"):
            self.assertIn(needle, bundle, needle)

        node = shutil.which("node")
        if node:
            path = os.path.join(self.tmp, "dashboard.js")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(dashboard_source("dashboard.js"))
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
        self.assertEqual(len(uris), 12)
        self.assertIn("mcp://server-card.json", uris)
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
        # The markup, the styles and the behaviour live in three files now
        # (P4-D1), so the whole-page assertions read the bundle.
        html = bridge_server.dashboard_html()
        for needle in ("fleet-bar", "fleet-chips"):
            self.assertIn(needle, html)
        bundle = dashboard_bundle()
        for needle in ("fleet-bar", "fleet-chips", "studio-card", "broadcastPing",
                       "prefers-reduced-motion", "animateValue", "paneIn", "brand"):
            self.assertIn(needle, bundle, needle)
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
        self.assertNotIn("__BRIDGE_VERSION__", html)
        bundle = dashboard_bundle()
        self.assertIn("saveToken", bundle)
        self.assertIn("apiFetch", bundle)
        self.assertIn("PAGE_VERSION", bundle)
        self.assertIn(f'PAGE_VERSION = "{bridge_server.VERSION}"', bundle)

        # v6.1.0 (P0-S2): loopback no longer means "here is the owner token".
        # An unauthenticated page still boots (so it can render the banner) but
        # gets no credential; an admin caller does.
        req = urllib.request.Request(self.base + "/api/local-boot")
        with urllib.request.urlopen(req, timeout=15) as resp:
            boot = json.loads(resp.read().decode())
        self.assertTrue(boot["local"])
        self.assertEqual(boot["token"], "")
        self.assertTrue(boot["tokenRequired"])
        self.assertTrue(boot["tokenWithheld"])
        self.assertFalse(boot["authenticated"])
        admin_req = urllib.request.Request(
            self.base + "/api/local-boot",
            headers={"X-Arena-Token": self.admin_token})
        with urllib.request.urlopen(admin_req, timeout=15) as resp:
            boot_admin = json.loads(resp.read().decode())
        self.assertEqual(boot_admin["token"], self.admin_token)
        self.assertTrue(boot_admin["authenticated"])

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

    def test_dashboard_asset_library_and_kit_endpoints(self):
        bundle = dashboard_bundle()
        self.assertIn("/api/kit/catalog?category=all&format=full", bundle)
        self.assertIn("format: 'full'", bundle)
        self.assertIn("previewDungeonLayout", bundle)
        self.assertIn("runMechanicBuilder", bundle)

        # v6.1.0 (P0-S6): kit *source* needs `read`; the CC0 *metadata* index the
        # gallery renders from stays public. `format=full` on a category is a
        # listing (name/path/bytes), so it is the public half of that split.
        st_open, open_cat = self._req("/api/kit/catalog?category=all&format=full")
        self.assertEqual(st_open, 200, "the dashboard gallery must render before it has a token")
        self.assertGreaterEqual(len(open_cat.get("images") or []), 3700)
        self.assertGreaterEqual(len(open_cat.get("models") or []), 520)
        self.assertGreaterEqual(len(open_cat.get("audio") or []), 45)
        self.assertGreaterEqual(len(open_cat.get("modules") or []), 25)
        self.assertGreaterEqual(len(open_cat.get("systems") or []), 20)
        self.assertGreaterEqual(len(open_cat.get("prefabs") or []), 26)
        self.assertEqual(len(open_cat.get("proceduralPresets") or []), 38)
        self.assertEqual(len(open_cat.get("audioSynthSpecs") or []), 12)
        self.assertNotIn("--!strict", json.dumps(open_cat)[:200000],
                         "a public listing must not carry Luau source")

        # The source half of the split stays behind `read`.
        st_src, src_body = self._req("/api/kit/catalog?category=modules&item=NetworkGuard")
        self.assertEqual(st_src, 401)
        self.assertNotIn("NetworkGuard", json.dumps(src_body))
        st_item, item = self._req("/api/kit/catalog?category=modules&item=NetworkGuard",
                                  token=self.admin_token)
        self.assertEqual(st_item, 200)
        self.assertGreater(len(item.get("content") or ""), 500)

        st_cat, cat = self._req("/api/kit/catalog?category=all&format=full",
                                token=self.admin_token)
        self.assertEqual(st_cat, 200)
        self.assertGreaterEqual(len(cat.get("images") or []), 3700)
        self.assertGreaterEqual(len(cat.get("models") or []), 520)
        self.assertGreaterEqual(len(cat.get("audio") or []), 45)
        self.assertGreaterEqual(len(cat.get("modules") or []), 25)
        self.assertGreaterEqual(len(cat.get("systems") or []), 20)
        self.assertGreaterEqual(len(cat.get("prefabs") or []), 26)
        self.assertEqual(len(cat.get("proceduralPresets") or []), 38)
        self.assertEqual(len(cat.get("audioSynthSpecs") or []), 12)

        st_ci, ci = self._req("/api/kit/cloud-icons?query=shield&limit=10")
        self.assertEqual(st_ci, 200)
        self.assertEqual(ci.get("totalCatalogIcons"), 4811)
        self.assertGreaterEqual(len(ci.get("icons") or []), 1)

        with urllib.request.urlopen(self.base + "/api/kit/procedural/icon_key.png", timeout=15) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(8), b"\x89PNG\r\n\x1a\n")

        with urllib.request.urlopen(self.base + "/api/kit/synth-audio/horror_heartbeat.wav", timeout=15) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(4), b"RIFF")

        with urllib.request.urlopen(self.base + "/api/kit/thumbnail/audio/click_001.png", timeout=15) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(8), b"\x89PNG\r\n\x1a\n")

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

    def mcp_http(self, rpc_body, auth_mode="bearer", timeout: float = 30.0):
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None, dict(resp.headers)

    def call_tool(self, name: str, arguments: dict = None) -> dict:
        started = time.monotonic()
        passed = False
        req_timeout = max(30.0, float((arguments or {}).get("timeout") or 30.0) + 30.0)
        try:
            status, body, _ = self.mcp_http({
                "jsonrpc": "2.0",
                "id": f"call-{name}",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }, timeout=req_timeout)
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

        # v6.1.0 (P0-S2): a credential in a query string is refused everywhere
        # except the SSE stream, so `?token=` on /mcp no longer authenticates —
        # it used to be one of the accepted auth modes and leaked into proxy and
        # access logs. The batch itself is header-authenticated.
        batch = [
            {"jsonrpc": "2.0", "id": "b1", "method": "ping"},
            {"jsonrpc": "2.0", "id": "b2", "method": "resources/list"},
            {"jsonrpc": "2.0", "id": "b3", "method": "prompts/list"},
        ]
        with self.assertRaises(urllib.error.HTTPError) as query_auth:
            self.mcp_http(batch, auth_mode="query")
        self.assertEqual(query_auth.exception.code, 401)

        # JSON-RPC batch call works cleanly
        st_b, batch_resp, _ = self.mcp_http(batch)
        self.assertEqual(st_b, 200)
        self.assertEqual(len(batch_resp), 3)
        self.assertEqual(len(batch_resp[1]["result"]["resources"]), 12)
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
        self.assertEqual(ops["version"], "6.1.0")
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
        self.assertEqual(ping["pluginVersion"], "6.1.0")

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

        # v6.1.0 (P0-S2): the boot payload is still served to an unauthenticated
        # local page (so the dashboard can render and ask for a token), but the
        # owner credential is only disclosed to an admin caller.
        with urllib.request.urlopen(f"{self.base_url}/api/local-boot", timeout=5) as boot_resp:
            boot_data = json.loads(boot_resp.read().decode("utf-8"))
            self.assertTrue(boot_data["local"])
            self.assertEqual(boot_data["token"], "")
            self.assertTrue(boot_data["tokenWithheld"])
            self.assertEqual(len(boot_data["tools"]), len(TOOL_SPECS))
        boot_req = urllib.request.Request(
            f"{self.base_url}/api/local-boot",
            headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(boot_req, timeout=5) as boot_resp2:
            self.assertEqual(json.loads(boot_resp2.read().decode("utf-8"))["token"], self.token)

        with urllib.request.urlopen(f"{self.base_url}/api/agent-guide", timeout=5) as guide_resp:
            guide_data = json.loads(guide_resp.read().decode("utf-8"))
            self.assertEqual(guide_data["toolCounts"]["total"], len(TOOL_SPECS))
            self.assertGreaterEqual(len(guide_data["keyTips"]), 5)

        # 8. Direct Image/Audio Import, expanded asset catalog, 3D tools and bounded kit browsing
        diagnostics = self.call_tool("diagnose_connection", {})
        self.assertEqual(diagnostics["status"], "connected")
        self.assertGreater(diagnostics["peers"]["online"], 0)
        self.assertNotIn(self.token, json.dumps(diagnostics))
        doctor = subprocess.run([sys.executable, str(HERE / "arena_studio.py"), "doctor"], env=dict(os.environ, BRIDGE_URL=self.base_url, BRIDGE_TOKEN=self.token), capture_output=True, text=True, timeout=10)
        self.assertEqual(doctor.returncode, 0)
        self.assertEqual(json.loads(doctor.stdout)["status"], "connected")
        self.assertNotIn(self.token, doctor.stdout + doctor.stderr)
        catalog = self.call_tool("search_tools", {"query": "performance", "readOnlyOnly": True})
        self.assertEqual(catalog["results"][0]["name"], "perf_audit")
        ranged = self.call_tool("skill_references", {"name": "roblox-lessons", "action": "read", "path": "references/ui.md", "startLine": 1, "endLine": 2})
        self.assertEqual(ranged["lineRange"]["end"], 2)
        onboarding = self.call_tool("audit_onboarding_plan", {"milestones": [{"id": "first", "objective": "Try", "completionEvidence": "Server", "recovery": "Retry"}]})
        self.assertTrue(onboarding["ok"])
        localization = self.call_tool("audit_localization", {"entries": [{"key": "welcome", "source": "Hello {player}", "translations": {"fr": "Salut {player}"}}], "locales": ["fr"]})
        self.assertTrue(localization["ok"])
        self.assertEqual(localization["entriesChecked"], 1)
        formatted = self.call_tool("audit_localization", {"entries": [{"key": "score", "source": "{1:num}", "translations": {"fr": "{1:int}"}}], "locales": ["fr"]})
        self.assertEqual(formatted["issues"][0]["code"], "format_mismatch")
        _, invalid_localization, _ = self.mcp_http({"jsonrpc": "2.0", "id": "invalid-localization", "method": "tools/call", "params": {"name": "audit_localization", "arguments": {"entries": [{"key": "bad", "source": "Hello", "translations": {"fr": 42}}], "locales": ["fr"]}}})
        self.assertTrue(invalid_localization["result"].get("isError"))
        skills = self.call_tool("search_skills", {"query": "stud ui", "limit": 2})
        ref_search = self.call_tool("search_skills", {"query": "TextScaled", "includeReferences": True, "limit": 100})
        lessons = next(item for item in ref_search["results"] if item["name"] == "roblox-lessons")
        self.assertTrue(any(item["path"] == "references/ui.md" for item in lessons["matchedReferences"]))
        self.assertTrue(ref_search["referenceSearch"]["complete"])
        self.assertEqual(skills["results"][0]["name"], "roblox-stud-ui-design")
        self.assertIsNotNone(skills["nextCursor"])
        natural = self.call_tool("search_skills", {"query": "how do I handle a failed datastore load"})
        self.assertEqual(natural["results"][0]["name"], "roblox-data-reliability")
        self.assertNotIn("how", natural["results"][0]["matchedTerms"])

        loaded = self.call_tool("apply_skill", {"name": skills["results"][0]["name"]})
        self.assertIn("StudUI", loaded["instructions"])
        self.assertIn("No persistent", loaded["application"])
        refs = self.call_tool("skill_references", {"name": "roblox-stud-ui-design", "limit": 2})
        self.assertTrue(refs["items"])
        ref = self.call_tool("skill_references", {"name": "roblox-stud-ui-design", "action": "read", "path": refs["items"][0]["path"]})
        self.assertTrue(ref["content"])


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

        dungeon_res = self.call_tool("generate_dungeon_layout", {"seed": 2026, "roomCount": 7, "theme": "horror_mansion", "buildInStudio": False})
        self.assertTrue(dungeon_res["ok"])
        self.assertEqual(dungeon_res["roomCount"], 7)
        self.assertTrue(dungeon_res["allRoomsReachable"])

        mechanic_res = self.call_tool("build_gameplay_mechanic", {"mechanic": "horror-director", "installInStudio": False})
        self.assertTrue(mechanic_res["ok"])
        self.assertGreaterEqual(mechanic_res["scriptCount"], 3)

        atmo_res = self.call_tool("configure_atmosphere_fx", {"preset": "BloodMoon", "intensity": 1.15, "applyToStudio": False})
        self.assertTrue(atmo_res["ok"])
        self.assertEqual(atmo_res["preset"], "BloodMoon")

        synth_res = self.call_tool("synthesize_sound_effect", {"preset": "horror_heartbeat", "saveToRepo": False})
        self.assertTrue(synth_res["ok"])
        self.assertGreater(synth_res["bytes"], 1000)

        plg_res = self.call_tool("manage_plugin_and_playtest", {"action": "status"})
        self.assertTrue(plg_res["ok"])
        self.assertEqual(plg_res["bundledPluginVersion"], "6.1.0")
        self.assertTrue(plg_res["allPeersUpToDate"])
        self.assertIsNotNone(plg_res["livePluginTelemetry"])
        self.assertEqual(plg_res["livePluginTelemetry"]["pluginVersion"], "6.1.0")

        net_res = self.call_tool("audit_network_security", {
            "scripts": [{
                "path": "ServerScriptService.ShopServer",
                "source": "BuyRemote.OnServerEvent:Connect(function(player, itemId, price)\n\tplayer.Leaderstats.Gold.Value -= price\nend)\n",
            }],
            "includeStudio": False,
        })
        self.assertTrue(net_res["ok"])
        self.assertGreaterEqual(net_res["errorCount"], 1)
        self.assertLess(net_res["securityScore"], 100)

        cut_res = self.call_tool("build_cutscene_sequence", {"preset": "boss_awakening", "buildInStudio": True})
        self.assertTrue(cut_res["ok"])
        self.assertEqual(cut_res["keyframeCount"], 3)
        self.assertTrue(cut_res["builtInStudio"])

        theme_res = self.call_tool("generate_ui_theme_pack", {"theme": "HorrorBloodMoon", "installInStudio": True})
        self.assertTrue(theme_res["ok"])
        self.assertTrue(theme_res["installedInStudio"])

        web_ui_res = self.call_tool("import_ui_from_web", {
            "html": '<div class="bg-slate-900 rounded-xl p-3 flex"><h2>ARCANE HUD — LEVEL 5</h2><button>[Q] DASH</button></div>',
            "guiName": "WebHudImported",
            "deSlop": True,
            "installInStudio": True,
        })
        self.assertTrue(web_ui_res["ok"])
        self.assertTrue(web_ui_res["installedInStudio"])
        self.assertGreaterEqual(web_ui_res["slopArtifactsFixed"], 1)

        vfx_res = self.call_tool("build_vfx_emitter", {
            "preset": "portal_swirl",
            "burstCount": 32,
            "buildInStudio": True,
        })
        self.assertTrue(vfx_res["ok"])
        self.assertTrue(vfx_res["builtInStudio"])

        mesh_spawn_res = self.call_tool("spawn_kit_mesh", {
            "query": "crystal",
            "scale": 1.25,
            "spawnInStudio": True,
        })
        self.assertTrue(mesh_spawn_res["ok"])
        self.assertTrue(mesh_spawn_res["meshPrioritized"])
        self.assertTrue(mesh_spawn_res["spawnedInStudio"])

        deslop_res = self.call_tool("de_slop_audit", {
            "texts": ["Furthermore — delve into this seamless realm!"],
            "includeStudio": True,
            "autoFix": False,
        })
        self.assertTrue(deslop_res["ok"])
        self.assertGreaterEqual(deslop_res["findingsCount"], 1)
        self.assertIn("meshTelemetry", deslop_res)

        chat_res = self.call_tool("operator_chat", {
            "action": "reply",
            "message": "Ready and prioritizing 3D meshes + Web UI import.",
        })
        self.assertTrue(chat_res["ok"])
        self.assertEqual(chat_res["channel"], "direct_operator_live_chat")

        kb_res = self.call_tool("kitbash_mesh_prop", {
            "preset": "runic_obelisk",
            "scale": 1.2,
            "interactive": True,
            "spawnInStudio": True,
        })
        self.assertTrue(kb_res["ok"])
        self.assertEqual(kb_res["meshPartsCount"], 3)
        self.assertEqual(kb_res["primitiveBlocksCount"], 0)
        self.assertTrue(kb_res["spawnedInStudio"])

        hb_res = self.call_tool("build_combat_hitbox", {
            "preset": "melee_combo",
            "baseDamage": 32,
            "maxReach": 14.0,
            "installInStudio": True,
        })
        self.assertTrue(hb_res["ok"])
        self.assertTrue(hb_res["installedInStudio"])

        cam_rig_res = self.call_tool("configure_camera_rig", {
            "preset": "over_shoulder_action",
            "fov": 74.0,
            "installInStudio": True,
        })
        self.assertTrue(cam_rig_res["ok"])
        self.assertTrue(cam_rig_res["installedInStudio"])

        sfx_bank_res = self.call_tool("generate_sfx_soundbank", {
            "pack": "combat_pack",
            "installInStudio": True,
        })
        self.assertTrue(sfx_bank_res["ok"])
        self.assertTrue(sfx_bank_res["installedInStudio"])

        exp_web_res = self.call_tool("export_ui_to_web", {
            "path": "StarterGui",
            "deSlop": True,
        })
        self.assertTrue(exp_web_res["ok"])
        self.assertIn("<div", exp_web_res["html"])

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
                {"jsonrpc": "2.0", "id": 17, "method": "tools/call", "params": {"name": "diagnose_connection", "arguments": {}}},
                {"jsonrpc": "2.0", "id": 18, "method": "tools/call", "params": {"name": "audit_onboarding_plan", "arguments": {"milestones": [{"id": "first", "objective": "Try", "completionEvidence": "server", "recovery": "retry"}]}}},
                {"jsonrpc": "2.0", "id": 19, "method": "tools/call", "params": {"name": "skill_references", "arguments": {"name": "roblox-world-streaming", "action": "read", "path": "references/streaming-test-matrix.md"}}},
                {"jsonrpc": "2.0", "id": 16, "method": "tools/call", "params": {"name": "search_tools", "arguments": {"query": "write_script"}}},
                {"jsonrpc": "2.0", "id": 15, "method": "tools/call", "params": {"name": "audit_localization", "arguments": {"entries": [{"key": "hello", "source": "{1:num}", "translations": {"fr": "{1:int}"}}], "locales": ["fr"]}}},
                {"jsonrpc": "2.0", "id": 14, "method": "tools/call", "params": {"name": "search_skills", "arguments": {"query": "failed datastore load", "includeReferences": True}}},
                {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "skill_references", "arguments": {"name": "roblox-lessons", "action": "read", "path": "references/ui.md"}}},
                {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {
                    "name": "manage_attributes",
                    "arguments": {"action": "set", "paths": ["Workspace.Door"], "attributes": {"Hack": 1}},
                }},
            ]
            for r in reqs:
                proc.stdin.write((json.dumps(r) + "\n").encode("utf-8"))
            proc.stdin.flush()

            responses = {}
            for _ in range(10):
                line = proc.stdout.readline().decode("utf-8").strip()
                msg = json.loads(line)
                responses[msg["id"]] = msg

            self.assertIn("Read-Only Inspector", responses[10]["result"]["instructions"])
            self.assertFalse(responses[11]["result"]["isError"])
            self.assertFalse(responses[13]["result"]["isError"])
            self.assertFalse(responses[14]["result"]["isError"])
            self.assertFalse(responses[15]["result"]["isError"])
            self.assertFalse(responses[16]["result"]["isError"])
            self.assertFalse(responses[17]["result"]["isError"])
            self.assertTrue(responses[18]["result"]["structuredContent"]["ok"])
            self.assertIn("Two clients", responses[19]["result"]["structuredContent"]["content"])
            self.assertTrue(responses[17]["result"]["structuredContent"]["bridge"]["inspectorMode"])
            self.assertNotIn("write_script", [x["name"] for x in responses[16]["result"]["structuredContent"]["results"]])
            self.assertEqual(responses[15]["result"]["structuredContent"]["issues"][0]["code"], "format_mismatch")
            self.assertIn("roblox-data-reliability", [item["name"] for item in responses[14]["result"]["structuredContent"]["results"]])
            self.assertTrue(responses[14]["result"]["structuredContent"]["referenceSearch"]["complete"])
            self.assertIn("TextScaled", responses[13]["result"]["structuredContent"]["content"])
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
        # v6.1.0 (P0-S1): a client's own `inputResponses` no longer satisfies the
        # gate unless the operator opts in — the elicitation capability arrives in
        # per-request `_meta` that the caller writes, so accepting it by default
        # let an agent approve its own destructive call. This class tests the
        # in-band MRTR mechanism itself, so it opts in. The default (refuse) and
        # the reason the caller sees are pinned by
        # TestV61SecurityHardening.test_a_client_cannot_answer_its_own_confirmation.
        cls.state.confirm_gate.trust_client_answers = True
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

    def test_mixed_mutations_require_confirmation(self):
        for i, (name, arguments) in enumerate((
                ("agent_bus", {"action": "post", "body": "hello"}),
                ("agent_bus", {"action": "export", "path": "output.md"}),
                ("self_test", {}), ("require_fresh", {"path": "game.X"}),
                ("render_offline", {"tree": {}, "saveFile": "output.png"}),
                ("audit_ui_layout", {"deviceSweep": True}))):
            result = self.call(f"mixed-{i}", "tools/call", {"name": name, "arguments": arguments})["result"]
            self.assertEqual(result.get("resultType"), "input_required", name)
        read = self.call("bus-read", "tools/call", {"name": "agent_bus", "arguments": {"action": "stats"}})["result"]
        self.assertNotEqual(read.get("resultType"), "input_required")

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


class TestV55DiscoveryTunnelAndSecurity(unittest.TestCase):
    """v5.5.0: .well-known Server Cards & RFC 9728, SEP-2243 Base64 headers,
    completions, pagination, resource_link, Origin & body-size guards, and
    Cloudflare QUIC->HTTP/2 tunnel resilience."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arena-v55-")
        self.token = "v55-owner-secret"
        self.httpd, self.state = create_http_server(
            host="127.0.0.1",
            port=0,
            token=self.token,
            state_dir=self.tmp,
            allowed_origins=["https://allowed.example"],
            max_body_bytes=8192,
        )
        self.port = self.httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        read_mint = self.state.tokens.create("reader", role="read")
        self.read_token = read_mint["token"]

    def tearDown(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass
        self.state.tunnel.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _raw_http(self, path, method="GET", body=None, headers=None):
        hdrs = dict(headers or {})
        data = body if isinstance(body, (bytes, type(None))) else body.encode("utf-8")
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers)

    def test_well_known_server_card_and_oauth_protected_resource_metadata(self):
        for card_path in ("/.well-known/mcp/server-card.json", "/.well-known/mcp.json"):
            st, raw, hdrs = self._raw_http(card_path)
            self.assertEqual(st, 200)
            self.assertEqual(hdrs.get("X-Content-Type-Options"), "nosniff")
            self.assertEqual(hdrs.get("Access-Control-Allow-Origin"), "*")
            card = json.loads(raw)
            self.assertEqual(card["serverInfo"]["version"], "6.1.0")
            self.assertIn("2026-07-28", card["supportedVersions"])
            self.assertTrue(card["authentication"]["required"])
            self.assertTrue(card["endpoints"]["streamableHttp"].endswith("/mcp"))

        st_oa, raw_oa, _ = self._raw_http("/.well-known/oauth-protected-resource/mcp")
        self.assertEqual(st_oa, 200)
        oa = json.loads(raw_oa)
        self.assertEqual(oa["resource"], f"{self.base_url}/mcp")
        self.assertIn("mcp:read", oa["scopes_supported"])
        self.assertEqual(oa["bearer_methods_supported"], ["header"])

        # Unauthenticated /mcp emits RFC 9728 WWW-Authenticate challenge
        st_401, _, hdrs_401 = self._raw_http("/mcp")
        self.assertEqual(st_401, 401)
        self.assertIn("resource_metadata=", hdrs_401.get("WWW-Authenticate", ""))

        # Insufficient scope on admin endpoint emits 403 insufficient_scope challenge
        st_403, _, hdrs_403 = self._raw_http(
            "/api/tokens",
            headers={"Authorization": f"Bearer {self.read_token}"},
        )
        self.assertEqual(st_403, 403)
        self.assertIn('error="insufficient_scope"', hdrs_403.get("WWW-Authenticate", ""))

    def test_origin_guard_body_limits_and_parse_error_regression(self):
        # Cross-origin browser request to /mcp is rejected (DNS-rebinding defense)
        st_bad, _, _ = self._raw_http(
            "/mcp",
            method="POST",
            body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "Origin": "https://evil.attacker.example",
            },
        )
        self.assertEqual(st_bad, 403)

        # Explicitly allowlisted Origin succeeds and reflects CORS Origin
        st_ok, raw_ok, hdrs_ok = self._raw_http(
            "/mcp",
            method="POST",
            body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "Origin": "https://allowed.example",
            },
        )
        self.assertEqual(st_ok, 200)
        self.assertEqual(hdrs_ok.get("Access-Control-Allow-Origin"), "https://allowed.example")
        self.assertIn("WWW-Authenticate", hdrs_ok.get("Access-Control-Expose-Headers", ""))
        self.assertIn("result", json.loads(raw_ok))

        # Regression test: malformed JSON on POST /mcp returns 400 + -32700 (no UnboundLocalError)
        st_parse, raw_parse, hdrs_parse = self._raw_http(
            "/mcp",
            method="POST",
            body=b"{not-valid-json",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "MCP-Protocol-Version": "2026-07-28",
            },
        )
        self.assertEqual(st_parse, 400)
        self.assertEqual(hdrs_parse.get("MCP-Protocol-Version"), "2026-07-28")
        self.assertEqual(json.loads(raw_parse)["error"]["code"], -32700)

        # Oversized body triggers HTTP 413
        oversized = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "ping", "pad": "x" * 9000})
        st_413, _, _ = self._raw_http(
            "/mcp",
            method="POST",
            body=oversized,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_413, 413)

    def test_chunked_and_expect_100_continue_bodies_are_refused_not_emptied(self):
        """P1-B5: `_read_body_raw` returned `b""` whenever Content-Length was
        missing, so a chunked POST reached the routes as `{}` — a confusing 400 on
        some routes and a *silent no-op* on others. And `Expect: 100-continue` was
        answered `100` unconditionally, so an oversized body was uploaded in full
        before the cap rejected it."""
        def raw_request(head: bytes, body: bytes = b"") -> str:
            with socket.create_connection(("127.0.0.1", self.port), timeout=15) as sock:
                sock.sendall(head + body)
                blobs = []
                while True:
                    try:
                        blob = sock.recv(65536)
                    except (TimeoutError, OSError):
                        break
                    if not blob:
                        break
                    blobs.append(blob)
                return b"".join(blobs).decode("utf-8", "replace")

        auth = f"Authorization: Bearer {self.token}\r\n".encode("ascii")

        # 1. A chunked body is refused with 411 and a message that names it.
        head = (b"POST /api/jobs HTTP/1.1\r\nHost: 127.0.0.1\r\n" + auth +
                b"Content-Type: application/json\r\nTransfer-Encoding: chunked\r\n"
                b"Connection: close\r\n\r\n")
        chunk = b'{"type":"ping","note":"chunked-probe"}'
        reply = raw_request(head, b"%x\r\n%s\r\n0\r\n\r\n" % (len(chunk), chunk))
        self.assertIn("411", reply.split("\r\n", 1)[0], reply[:240])
        self.assertIn("chunked", reply)
        with self.state.lock:
            self.assertEqual([j for j in self.state.jobs.values()
                              if j.get("note") == "chunked-probe"], [],
                             "a chunked body must never become a silent no-op job")

        # 2. `Expect: 100-continue` over the cap is refused before the body.
        oversized = json.dumps({"jsonrpc": "2.0", "id": "big", "method": "ping",
                                "pad": "x" * 9000}).encode("utf-8")
        head = (b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\n" + auth +
                b"Content-Type: application/json\r\nExpect: 100-continue\r\n" +
                b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(oversized))
        reply = raw_request(head, oversized)
        self.assertIn("413", reply.split("\r\n", 1)[0], reply[:240])

        # 3. Under the cap the interim 100 still arrives, then the real answer.
        payload = json.dumps({"jsonrpc": "2.0", "id": "e100", "method": "ping"}).encode("utf-8")
        head = (b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\n" + auth +
                b"Content-Type: application/json\r\nExpect: 100-continue\r\n" +
                b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(payload))
        reply = raw_request(head, payload)
        self.assertIn("100 Continue", reply.split("\r\n", 1)[0], reply[:240])
        self.assertIn("HTTP/1.1 200", reply, reply[:300])

        # 4. A malformed Content-Length with Expect is a 400, not a guess.
        head = (b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\n" + auth +
                b"Expect: 100-continue\r\nContent-Length: twelve\r\n"
                b"Connection: close\r\n\r\n")
        reply = raw_request(head)
        self.assertIn("400", reply.split("\r\n", 1)[0], reply[:240])

    def test_concurrent_requests_are_bounded_and_refused_with_503(self):
        """P1-B5: `ThreadingHTTPServer` spawned a thread per connection with no
        ceiling. Saturation is now refused immediately instead of growing without
        bound — and the live server really is the bounded one."""
        httpd = self.httpd
        self.assertIsInstance(httpd, bridge_server.BoundedThreadingHTTPServer)
        self.assertGreaterEqual(httpd.max_concurrent, 4)

        class FakeSocket:
            """Stands in for an accepted connection: `shutdown_request` closes it."""

            def __init__(self):
                self.sent = b""
                self.closed = False

            def sendall(self, blob):
                self.sent += blob

            def shutdown(self, _how):
                pass

            def close(self):
                self.closed = True

        drained = 0
        try:
            for _ in range(httpd.max_concurrent):
                self.assertTrue(httpd._inflight.acquire(blocking=False),
                                "the permit count does not match max_concurrent")
                drained += 1
            sock = FakeSocket()
            before = httpd.rejected_connections
            httpd.process_request(sock, ("203.0.113.7", 4242))
            self.assertEqual(httpd.rejected_connections, before + 1)
            self.assertIn(b"503 Service Unavailable", sock.sent)
            self.assertIn(b"Retry-After: 1", sock.sent)
            self.assertIn(b"concurrent-request limit", sock.sent)
            self.assertTrue(sock.closed, "a refused connection must be closed, not left half-open")
            kinds = [e["kind"] for e in self.state.telemetry.events.since(0, 200).get("events") or []]
            self.assertIn("http.saturated", kinds)
        finally:
            for _ in range(drained):
                httpd._inflight.release()

        # And with a permit free, a real request is served again.
        st, _, _ = self._raw_http("/api/health")
        self.assertEqual(st, 200)

    def test_sep2243_base64_headers_stateless_405_and_version_errors(self):
        from mcp_core import decode_mcp_header_value, encode_mcp_header_value

        non_ascii = "studio://scripts/Café"
        encoded = encode_mcp_header_value(non_ascii)
        self.assertTrue(encoded.startswith("=?base64?"))
        self.assertEqual(decode_mcp_header_value(encoded), non_ascii)
        self.assertEqual(encode_mcp_header_value("mcp://server-card.json"), "mcp://server-card.json")

        # Valid SEP-2243 headers on resources/read for mcp://server-card.json
        st_card, raw_card, _ = self._raw_http(
            "/mcp",
            method="POST",
            body=json.dumps({
                "jsonrpc": "2.0",
                "id": 10,
                "method": "resources/read",
                "params": {"uri": "mcp://server-card.json"},
            }),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "resources/read",
                "Mcp-Name": "mcp://server-card.json",
            },
        )
        self.assertEqual(st_card, 200)
        card_body = json.loads(json.loads(raw_card)["result"]["contents"][0]["text"])
        self.assertEqual(card_body["serverInfo"]["version"], "6.1.0")

        # Malformed =?base64?...?= sentinel -> 400 + -32020
        st_bad_b64, raw_bad_b64, _ = self._raw_http(
            "/mcp",
            method="POST",
            body=json.dumps({"jsonrpc": "2.0", "id": 11, "method": "ping"}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "Mcp-Name": "=?base64?%%%not-base64%%%?=",
            },
        )
        self.assertEqual(st_bad_b64, 400)
        self.assertEqual(json.loads(raw_bad_b64)["error"]["code"], -32020)

        # Header MCP-Protocol-Version vs _meta protocolVersion mismatch -> 400 + -32020
        st_ver_mis, raw_ver_mis, _ = self._raw_http(
            "/mcp",
            method="POST",
            body=json.dumps({
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/list",
                "_meta": {"io.modelcontextprotocol/protocolVersion": "2025-06-18"},
            }),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "MCP-Protocol-Version": "2026-07-28",
            },
        )
        self.assertEqual(st_ver_mis, 400)
        self.assertEqual(json.loads(raw_ver_mis)["error"]["code"], -32020)

        # Unsupported protocol version -> HTTP 400 + -32022 with requested & supported
        st_unsup, raw_unsup, _ = self._raw_http(
            "/mcp",
            method="POST",
            body=json.dumps({"jsonrpc": "2.0", "id": 13, "method": "tools/list"}),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.token}",
                "MCP-Protocol-Version": "2099-01-01",
            },
        )
        self.assertEqual(st_unsup, 400)
        err_unsup = json.loads(raw_unsup)["error"]
        self.assertEqual(err_unsup["code"], -32022)
        self.assertEqual(err_unsup["data"]["requested"], "2099-01-01")
        self.assertIn("2026-07-28", err_unsup["data"]["supported"])

        # DELETE /mcp and GET /mcp SSE return 405 in 2026-07-28, while legacy GET /mcp SSE flushes : connected
        st_del, _, _ = self._raw_http("/mcp", method="DELETE", headers={"MCP-Protocol-Version": "2026-07-28"})
        self.assertEqual(st_del, 405)
        st_sse_mod, _, _ = self._raw_http(
            "/mcp",
            headers={
                "Authorization": f"Bearer {self.token}",
                "MCP-Protocol-Version": "2026-07-28",
                "Accept": "text/event-stream",
            },
        )
        self.assertEqual(st_sse_mod, 405)
        st_sse_leg, raw_sse_leg, _ = self._raw_http(
            "/mcp?maxSeconds=0.2",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "text/event-stream",
            },
        )
        self.assertEqual(st_sse_leg, 200)
        self.assertIn(": connected", raw_sse_leg)

    def test_completions_cursor_pagination_titles_and_resource_links(self):
        # Seed a bus agent so completion/complete returns live values
        self.state.bus.handle({"action": "register", "name": "CompAgent", "role": "qa"})

        def rpc(method, params=None):
            st, raw, _ = self._raw_http(
                "/mcp",
                method="POST",
                body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.token}",
                    "MCP-Protocol-Version": "2026-07-28",
                },
            )
            self.assertEqual(st, 200, raw)
            return json.loads(raw)

        comp_agent = rpc("completion/complete", {
            "ref": {"type": "ref/prompt", "name": "multi_agent_handoff"},
            "argument": {"name": "agentName", "value": "Comp"},
        })
        self.assertEqual(comp_agent["result"]["resultType"], "complete")
        self.assertIn("CompAgent", comp_agent["result"]["completion"]["values"])

        comp_goal = rpc("completion/complete", {
            "ref": {"type": "ref/prompt", "name": "safe_module_edit"},
            "argument": {"name": "goal", "value": "strict"},
        })
        self.assertTrue(any("strict" in v for v in comp_goal["result"]["completion"]["values"]))

        comp_skill = rpc("completion/complete", {
            "ref": {"type": "ref/resource", "uri": "skill://{name}"},
            "argument": {"name": "name", "value": "roblox-stud"},
        })
        self.assertIn("roblox-stud-ui-design", comp_skill["result"]["completion"]["values"])

        bad_comp = rpc("completion/complete", {
            "ref": {"type": "ref/prompt", "name": "no_such_prompt"},
            "argument": {"name": "x", "value": ""},
        })
        self.assertEqual(bad_comp["error"]["code"], -32602)

        # Pagination + titles on resources/list
        page1 = rpc("resources/list", {"limit": 5})["result"]
        self.assertEqual(len(page1["resources"]), 5)
        self.assertTrue(all(r.get("title") for r in page1["resources"]))
        self.assertIn("nextCursor", page1)
        page2 = rpc("resources/list", {"limit": 5, "cursor": page1["nextCursor"]})["result"]
        self.assertEqual(len(page2["resources"]), 5)
        self.assertTrue({r["uri"] for r in page1["resources"]}.isdisjoint({r["uri"] for r in page2["resources"]}))

        # apply_skill appends resource_link after content[0]
        applied = rpc("tools/call", {"name": "apply_skill", "arguments": {"name": "roblox-stud-ui-design"}})["result"]
        self.assertGreaterEqual(len(applied["content"]), 2)
        self.assertEqual(applied["content"][0]["type"], "text")
        self.assertEqual(applied["content"][1]["type"], "resource_link")
        self.assertEqual(applied["content"][1]["uri"], "skill://roblox-stud-ui-design")

    def test_tunnel_quic_to_http2_fallback_named_edge_and_strict_probe(self):
        from tunnel_manager import TunnelManager

        # 1. Stopped tunnel keeps lastKnownUrl separate from publicUrl
        state_dir = Path(self.tmp) / "tun-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "tunnel.json").write_text(json.dumps({
            "lastPublicUrl": "https://old-dead-url.trycloudflare.com",
            "flavor": "cloudflared",
        }), encoding="utf-8")
        mgr = TunnelManager(port=self.port, state_dir=state_dir)
        initial = mgr.status()
        self.assertIsNone(initial["publicUrl"])
        self.assertEqual(initial["lastKnownUrl"], "https://old-dead-url.trycloudflare.com")
        self.assertIsNone(initial["mcpUrl"])

        # 2. Automatic QUIC -> http2 fallback when UDP is blocked
        args_log = state_dir / "cf-args.log"
        fake_cf = state_dir / "fake-cf"
        fake_cf.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, time\n"
            f"open({str(args_log)!r}, 'a', encoding='utf-8').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "if 'http2' not in sys.argv:\n"
            "    print('ERR failed to dial to edge with quic: timeout: no recent network activity', flush=True)\n"
            "    sys.exit(1)\n"
            f"print('INF |  http://127.0.0.1:{self.port}  |', flush=True)\n"
            "print('INF |  https://fallback-http2-test.trycloudflare.com  |', flush=True)\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        os.chmod(fake_cf, 0o755)

        mgr_fb = TunnelManager(port=self.port, binary=str(fake_cf), flavor="cloudflared", protocol="auto", state_dir=state_dir)
        try:
            mgr_fb.start()
            deadline = time.time() + 8.0
            while time.time() < deadline and mgr_fb.public_url != "https://fallback-http2-test.trycloudflare.com":
                time.sleep(0.1)
            st_fb = mgr_fb.status()
            self.assertEqual(st_fb["state"], "online")
            self.assertEqual(st_fb["activeProtocol"], "http2")
            self.assertEqual(st_fb["wellKnownMcp"], "https://fallback-http2-test.trycloudflare.com/.well-known/mcp/server-card.json")
            recorded = args_log.read_text(encoding="utf-8").splitlines()
            self.assertGreaterEqual(len(recorded), 2)
            self.assertNotIn("--protocol http2", recorded[0])
            self.assertIn("--protocol http2", recorded[-1])
        finally:
            mgr_fb.stop()
            self.assertIsNone(mgr_fb.status()["publicUrl"])
            self.assertEqual(mgr_fb.status()["lastKnownUrl"], "https://fallback-http2-test.trycloudflare.com")

        # 3. Strict JSON health probe rejects HTML 200 error pages & accepts real bridge /api/health
        mgr_probe = TunnelManager(port=self.port, public_url=self.base_url, state_dir=state_dir)
        good_probe = mgr_probe.probe()
        self.assertTrue(good_probe["ok"])
        self.assertEqual(good_probe["bridgeVersion"], "6.1.0")

    def test_setup_token_chmod_http_mcp_config_and_operator_scripts(self):
        import setup as bridge_setup
        import importlib.util

        # Verify 0o600 permissions on recovered bridge.token and HTTP .mcp.json writer
        sub_tmp = Path(self.tmp) / "setup-check"
        plugins = sub_tmp / "Plugins"
        tok_file = sub_tmp / "bridge.token"
        orig_tok = bridge_setup.TOKEN_FILE
        try:
            bridge_setup.TOKEN_FILE = tok_file
            bridge_setup.install_plugin("chmodsecret112233", 8077, explicit_dir=plugins)
            recovered = bridge_setup.get_token(rotate=False, explicit_dir=plugins)
            self.assertEqual(recovered, "chmodsecret112233")
            if os.name != "nt":
                mode = tok_file.stat().st_mode & 0o777
                self.assertEqual(mode, 0o600)
            mcp_http_file = bridge_setup.write_mcp_config(
                sub_tmp,
                port=self.port,
                read_only=False,
                transport="http",
                public_url="https://mcp.example.com",
                token="chmodsecret112233",
            )
            cfg = json.loads(mcp_http_file.read_text(encoding="utf-8"))
            self.assertEqual(cfg["mcpServers"]["roblox-studio"]["type"], "http")
            self.assertEqual(cfg["mcpServers"]["roblox-studio"]["url"], "https://mcp.example.com/mcp")
            self.assertEqual(
                cfg["mcpServers"]["roblox-studio"]["headers"]["Authorization"],
                "Bearer chmodsecret112233",
            )
        finally:
            bridge_setup.TOKEN_FILE = orig_tok

        # Verify drill_studio_kill.reaped_total handles list-typed /api/metrics counters
        self.state.telemetry.metrics.inc("bridge_jobs_reaped_total", 3)
        drill_path = HERE.parent / "jobs" / "drill_studio_kill.py"
        spec = importlib.util.spec_from_file_location("drill_studio_kill", drill_path)
        drill_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(drill_mod)
        self.assertEqual(drill_mod.reaped_total(self.base_url, self.token), 3)

        # Verify measure_tunnel_latency.py --transport mcp benchmarks real /mcp calls
        lat_script = HERE.parent / "jobs" / "measure_tunnel_latency.py"
        lat_run = subprocess.run(
            [
                sys.executable,
                str(lat_script),
                "--url",
                f"local={self.base_url}",
                "--token",
                self.token,
                "--transport",
                "mcp",
                "--tools",
                "get_studio_state",
                "--runs",
                "2",
                "--warmup",
                "0",
                "--markdown",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(lat_run.returncode, 0, lat_run.stderr)
        self.assertIn("get_studio_state", lat_run.stdout)

    def test_viewport_3d_string_properties_zbuffer_and_preview_endpoint(self):
        from image_pipeline import decode_any_to_rgba
        from scene_auditor import audit_3d_hierarchy
        from viewport_renderer import _compute_auto_camera, collect_parts_from_tree, render_scene_png

        # 1. String-only properties (as emitted by ArenaBridge.lua descendants without structuredProperties)
        string_tree = {
            "name": "Workspace",
            "className": "Workspace",
            "children": [
                {
                    "name": "Baseplate",
                    "className": "Part",
                    "properties": {
                        "Size": "2048, 16, 2048",
                        "Position": "0, -8, 0",
                        "CFrame": "0, -8, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1",
                        "Color": "0.22, 0.25, 0.30",
                        "Anchored": "true",
                    },
                },
                {
                    "name": "RedBarn",
                    "className": "Model",
                    "children": [
                        {
                            "name": "BarnWall",
                            "className": "Part",
                            "properties": {
                                "Size": "18, 12, 18",
                                "Position": "-6, 6, -8",
                                "CFrame": "-6, 6, -8, 1, 0, 0, 0, 1, 0, 0, 0, 1",
                                "Color": "0.92, 0.16, 0.14",
                                "Anchored": "true",
                            },
                        },
                        {
                            "name": "BarnRoof",
                            "className": "WedgePart",
                            "properties": {
                                "Size": "20, 6, 20",
                                "Position": "-6, 15, -8",
                                "Orientation": "0, 90, 0",
                                "BrickColor": "Bright yellow",
                                "Anchored": "true",
                            },
                        },
                    ],
                },
                {
                    "name": "ArchivedPalette",
                    "className": "Part",
                    "properties": {
                        "Size": "10, 2, 10",
                        "Position": "1600, -400, 1600",
                        "Color": "0.5, 0.5, 0.5",
                        "Anchored": "false",
                    },
                },
            ],
        }
        parts = collect_parts_from_tree(string_tree)
        self.assertEqual(len(parts), 4)
        barn = next(p for p in parts if p["name"] == "BarnWall")
        self.assertEqual(barn["size"], (18.0, 12.0, 18.0))
        self.assertEqual(barn["pos"], (-6.0, 6.0, -8.0))
        self.assertGreater(barn["color"][0], 200)
        self.assertEqual(barn["modelName"], "RedBarn")

        # 2. Outlier-resistant auto camera frames main build instead of 1600-stud ArchivedPalette
        for extra_i in range(5):
            parts.append({
                "name": f"Plot{extra_i}",
                "path": f"Workspace.Plot{extra_i}",
                "className": "Part",
                "modelName": None,
                "pos": (float(extra_i * 6), 1.0, 4.0),
                "size": (5.0, 1.0, 5.0),
                "axes": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
                "color": (90, 180, 90),
                "transparency": 0.0,
                "neon": False,
                "shape": "Block",
                "isGround": False,
            })
        _, look_at, _ = _compute_auto_camera(parts, "perspective")
        self.assertLess(abs(look_at[0]), 60.0)
        self.assertLess(abs(look_at[2]), 60.0)

        # 3. Z-buffer ensures giant Baseplate never paints over the RedBarn standing on top of it
        png_bytes, meta = render_scene_png(string_tree, width=480, height=270, include_hud=False, show_grid=True)
        w, h, rgba = decode_any_to_rgba(png_bytes)
        self.assertEqual((w, h), (480, 270))
        red_pixels = sum(
            1 for i in range(0, len(rgba), 4)
            if rgba[i] > 150 and rgba[i + 1] < 80 and rgba[i + 2] < 80
        )
        self.assertGreater(red_pixels, 150, "RedBarn must not be occluded by giant Baseplate")

        # 4. 3D scene auditor parses string properties accurately
        audit_rep = audit_3d_hierarchy(string_tree)
        self.assertEqual(audit_rep["totalPartsAudited"], 4)
        self.assertTrue(any(iss["code"] == "UNANCHORED_PART" for iss in audit_rep["issues"]))

        # 5. Dashboard /api/kit/viewport-preview endpoint serves live & showcase 3D viewports
        for src in ("studio:Workspace", "showcase:starter_village", "showcase:horror_nightloop", "dungeon:seeded", "prefab:world_safe_room_shelter"):
            code, raw_body, _ = self._raw_http(f"/api/kit/viewport-preview?source={src}&preset=iso_ne&width=360&height=220")
            body = json.loads(raw_body)
            self.assertEqual(code, 200, src)
            self.assertTrue(body["ok"], src)
            self.assertTrue(body["imageBase64"], src)
            self.assertGreater(body["meta"]["partsRendered"], 0, src)
            self.assertIn("audit", body, src)

    def test_v6_plugin_dashboard_and_playtest_stop_reliability(self):
        # 1. Verify /api/plugin/download serves pre-baked ArenaBridge.lua v6.1.0 with a
        #    *dedicated* token (never the owner credential) & DockWidget UI.
        st_dl, raw_dl, hdrs_dl = self._raw_http(
            "/api/plugin/download",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_dl, 200)
        self.assertIn("ArenaBridge.lua", hdrs_dl.get("Content-Disposition", ""))
        self.assertIn('local PLUGIN_VERSION = "6.1.0"', raw_dl)
        self.assertIn("ArenaBridgeDashboardV6", raw_dl)
        # v6.1.0 (P0-S2): the owner token must not appear anywhere in the download.
        self.assertNotIn(self.token, raw_dl)
        baked = re.search(r'local BRIDGE_TOKEN = "([^"]*)"', raw_dl)
        self.assertIsNotNone(baked, "the plugin must still carry a usable credential")
        self.assertTrue(baked.group(1).startswith("arb_"), baked.group(1)[:12])
        # ...and that credential is a real, separately-revocable write token.
        st_probe, _, _ = self._raw_http(
            "/api/poll?peer=probe-peer&dm=edit&place=TestPlace&placeId=99&pv=6.1.0&wait=0",
            headers={"Authorization": f"Bearer {baked.group(1)}"},
        )
        self.assertEqual(st_probe, 200, "the minted plugin token must authenticate")
        minted = [r for r in self.state.tokens.list()["tokens"]
                  if r["label"].startswith("plugin-download:")]
        self.assertEqual(len(minted), 1)
        self.assertEqual(minted[0]["role"], "write")
        # A read-scoped token cannot download the plugin at all.
        st_ro, raw_ro, _ = self._raw_http(
            "/api/plugin/download", headers={"Authorization": f"Bearer {self.read_token}"}
        )
        self.assertEqual(st_ro, 403)
        self.assertNotIn(self.token, raw_ro)

        # 2. Simulate an outdated v4.0.0 edit peer + a live server play peer, verify /api/plugin-status detects it
        self._raw_http(
            "/api/poll?peer=edit-legacy-peer&dm=edit&place=TestPlace&placeId=99&pv=4.0.0&wait=0",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self._raw_http(
            "/api/poll?peer=srv-ghost-peer&dm=server&place=TestPlace&placeId=99&pv=6.0.0&hasClient=1&players=1&wait=0",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        st_ps, raw_ps, _ = self._raw_http(
            "/api/plugin-status",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_ps, 200)
        ps = json.loads(raw_ps)
        self.assertTrue(ps["ok"])
        self.assertEqual(ps["bundledPluginVersion"], "6.1.0")
        self.assertGreaterEqual(ps["outdatedPeerCount"], 1)
        self.assertTrue(ps["playtestState"]["playtestActive"])

        # 3. Verify /api/poll?goodbye=1 immediately evicts a unloading playtest peer
        st_gb, raw_gb, _ = self._raw_http(
            "/api/poll?peer=srv-ghost-peer&dm=server&place=TestPlace&placeId=99&pv=6.0.0&goodbye=1",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_gb, 200)
        self.assertTrue(json.loads(raw_gb)["goodbye"])

        st_peers, raw_peers, _ = self._raw_http(
            "/api/peers",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        peers_after_gb = json.loads(raw_peers)["peers"]
        srv_row = next(p for p in peers_after_gb if p["peerId"] == "srv-ghost-peer")
        self.assertFalse(srv_row["online"], "goodbye=1 must immediately mark runtime peer offline")

        # 4. Verify POST /api/playtest/force-stop evicts any remaining runtime peers & cancels queued server jobs
        self._raw_http(
            "/api/poll?peer=srv-stuck-peer&dm=server&place=TestPlace&placeId=99&pv=6.0.0&wait=0",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        st_fs, raw_fs, _ = self._raw_http(
            "/api/playtest/force-stop",
            method="POST",
            body=json.dumps({"action": "evict_only", "reason": "unit_test_force_stop"}),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_fs, 200)
        fs_body = json.loads(raw_fs)
        self.assertTrue(fs_body["ok"])
        self.assertGreaterEqual(fs_body["evictedPeers"], 1)

    def test_direct_live_chat_web_ui_deslop_and_mesh_priority(self):
        from mcp_core import search_skills_helper, apply_skill_helper, skill_references_helper
        # 1. Verify all 10 new skills and their reference docs are discoverable & readable
        for sk_name, ref_path in (
            ("vfx-and-particles", "references/vfx-libraries-and-flipbooks.md"),
            ("sfx-and-spatial-audio", "references/sfx-libraries-and-layered-audio.md"),
            ("ui-web-and-dashboard-import", "references/web-html-svg-to-roblox-ui.md"),
            ("de-slop-and-visual-clarity", "references/anti-slop-checklist.md"),
            ("mesh-first-3d-building", "references/mesh-catalog-and-kitbashing.md"),
            ("combat-and-hitboxes", "references/spatial-hitbox-and-combo-patterns.md"),
            ("npc-ai-and-pathfinding", "references/pathfinding-and-state-machines.md"),
            ("camera-and-cinematics", "references/camera-rigs-and-springs.md"),
            ("procedural-world-and-kitbashing", "references/modular-kitbashing-and-terrain.md"),
            ("multiplayer-replication-and-netguard", "references/remote-security-and-replication.md"),
        ):
            sk = apply_skill_helper(sk_name)
            self.assertIn("instructions", sk)
            self.assertGreater(len(sk["instructions"]), 200)
            ref = skill_references_helper(sk_name, "read", ref_path)
            self.assertGreater(len(ref["content"]), 100)

        # 2. Operator posts a Direct Live Chat directive via POST /api/chat
        st_chat, raw_chat, _ = self._raw_http(
            "/api/chat",
            method="POST",
            body=json.dumps({
                "action": "send",
                "role": "operator",
                "sender": "HumanDirector",
                "text": "Prioritize 3D meshes for the portal shrine and import the HUD from the web.",
                "pinned": True,
            }),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_chat, 200)
        chat_post = json.loads(raw_chat)
        self.assertTrue(chat_post["ok"])
        op_msg_id = chat_post["message"]["id"]
        self.assertFalse(chat_post["message"]["deliveredToAgent"])

        # 3. Next MCP tool call (e.g. get_studio_state) automatically receives the operator message!
        st_mcp, raw_mcp, _ = self._raw_http(
            "/mcp",
            method="POST",
            body=json.dumps({
                "jsonrpc": "2.0",
                "id": "live-chat-piggyback",
                "method": "tools/call",
                "params": {"name": "get_studio_state", "arguments": {}},
            }),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_mcp, 200)
        mcp_resp = json.loads(raw_mcp)
        result_obj = mcp_resp["result"]
        self.assertIn("LIVE OPERATOR DIRECT CHAT", result_obj["content"][0]["text"])
        structured = result_obj["structuredContent"]
        self.assertIn("operatorLiveChat", structured)
        self.assertEqual(structured["operatorLiveChat"]["unreadMessages"][0]["id"], op_msg_id)

        # 4. Agent replies via operator_chat(action='reply') and GET /api/chat confirms repliedByAgent
        st_rep, raw_rep, _ = self._raw_http(
            "/mcp",
            method="POST",
            body=json.dumps({
                "jsonrpc": "2.0",
                "id": "live-chat-reply",
                "method": "tools/call",
                "params": {
                    "name": "operator_chat",
                    "arguments": {
                        "action": "reply",
                        "message": "Acknowledged! Spawning 3D mesh shrine and importing Web HUD now.",
                        "replyTo": op_msg_id,
                    },
                },
            }),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_rep, 200)
        reply_out = json.loads(raw_rep)["result"]["structuredContent"]
        self.assertTrue(reply_out["ok"])

        st_hist, raw_hist, _ = self._raw_http(
            "/api/chat?limit=20",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_hist, 200)
        hist = json.loads(raw_hist)
        op_entry = next(m for m in hist["messages"] if m["id"] == op_msg_id)
        self.assertTrue(op_entry["deliveredToAgent"])
        self.assertTrue(op_entry["repliedByAgent"])

        # 5. Verify POST /api/ui/import-web endpoint works for Dashboard Web UI import
        st_ui, raw_ui, _ = self._raw_http(
            "/api/ui/import-web",
            method="POST",
            body=json.dumps({
                "preset": "shop_grid",
                "guiName": "DashboardImportedShop",
                "deSlop": True,
                "installInStudio": False,
            }),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_ui, 200)
        ui_body = json.loads(raw_ui)
        self.assertTrue(ui_body["ok"])
        self.assertGreaterEqual(ui_body["elementsConverted"], 1)

        # 6. Verify POST /api/ui/export-web round-trip exports ScreenGui tree to Web HTML/Tailwind
        st_exp, raw_exp, _ = self._raw_http(
            "/api/ui/export-web",
            method="POST",
            body=json.dumps({
                "tree": ui_body["screenGuiTree"][0],
                "deSlop": True,
            }),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        self.assertEqual(st_exp, 200)
        exp_body = json.loads(raw_exp)
        self.assertTrue(exp_body["ok"])
        self.assertIn("<button", exp_body["html"])


class TestV61SecurityHardening(unittest.TestCase):
    """v6.1.0 P0: the six findings from `handoff/NEXT-AGENT-6.1.md` §2.

    Every assertion here was written against a *reproduced* failure (the §11
    snippets), so each one names the bug it closes. Several are paired with a
    negative control — the same check run against the pre-6.1.0 behaviour — so a
    passing test cannot be vacuous.
    """

    OWNER = "owner-secret-6.1.0"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="arena-v61-")
        cls.httpd, cls.state = create_http_server(
            host="127.0.0.1", port=0, token=cls.OWNER, state_dir=cls.tmp,
            require_confirm="all", confirm_ttl_seconds=120,
        )
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.read_token = cls.state.tokens.create("reader", role="read", rate_limit=500, burst=500)["token"]
        cls.write_token = cls.state.tokens.create("writer", role="write", rate_limit=500, burst=500)["token"]
        cls.admin_token = cls.state.tokens.create("admin", role="admin", rate_limit=500, burst=500)["token"]

    @classmethod
    def tearDownClass(cls):
        cls.state._reaper_stop.set()
        cls.state.tunnel.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        gate = self.state.confirm_gate
        with gate._lock:
            gate._pending.clear()
            gate._allowances.clear()
            gate._decisions.clear()
            gate._consumed.clear()

    # -- helpers --------------------------------------------------------------

    def raw(self, path, method="GET", body=None, token=None, extra_headers=None, timeout=15):
        """(status, raw_text, headers) — never raises on an HTTP error status."""
        headers = dict(extra_headers or {})
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace"), dict(exc.headers)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            return -1, f"transport error: {exc}", {}

    #: What a hostile web page looks like to `_is_local_convenience_request()`.
    #: Sending it switches off the loopback read allowance, so whatever comes
    #: back was granted by a credential — never by "you are on localhost".
    CROSS_SITE = {"Sec-Fetch-Site": "cross-site"}

    @staticmethod
    def jsonrpc_envelope(body):
        """Parse an MCP response, whether the server chose JSON or SSE.

        `/mcp` answers `application/json` for a single `tools/call`, but the
        Streamable HTTP transport is allowed to answer the same request as
        `text/event-stream`, and a real client sends an Accept header that
        permits both. Read the verdict either way instead of pinning the wire
        format, so this suite tests the gate and not the framing.
        """
        text = (body or "").lstrip()
        if text.startswith("{"):
            return json.loads(text)
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        return json.loads(payload)
                    except ValueError:
                        continue
        raise AssertionError(f"no JSON-RPC envelope in response: {body[:240]!r}")

    def approve(self, request_id):
        """Answer a pending confirmation the way an operator does."""
        status, body, _ = self.raw(
            "/api/confirmations", method="POST", token=self.admin_token,
            body={"action": "approve", "requestId": request_id, "note": "test-operator"},
        )
        self.assertEqual(status, 200, body)
        self.assertTrue(json.loads(body)["ok"], body)

    # -----------------------------------------------------------------------
    # P0-S1 / P3-T1 — the gate covers both transports, identically
    # -----------------------------------------------------------------------

    #: (job kind, payload) pairs that are destructive or arbitrary-code
    #: execution, and the MCP tool that must reach the same verdict.
    GATED_WORK = [
        ("run_luau", {"code": "print('pwned over REST')"}),
        ("delete", {"paths": ["Workspace.Baseplate"]}),
        ("write_script", {"parent": "ServerStorage", "name": "X", "className": "Script",
                          "source": "return 1"}),
        # `build_tree` takes an *array* of nodes — a bare dict is a -32602 at the
        # MCP door, which would make the two-transport comparison vacuous.
        ("build", {"tree": [{"name": "X", "className": "Folder"}]}),
        ("playtest", {"action": "start_play"}),
        ("virtual_input", {"steps": [{"action": "keydown", "key": "e"}]}),
    ]

    def test_rest_enqueue_cannot_bypass_the_confirmation_gate(self):
        """Pre-6.1.0: `POST /api/jobs {"type":"run_luau"}` returned 201 with the
        gate on, because `--require-confirm` only wrapped `tools/call`."""
        for kind, payload in self.GATED_WORK:
            status, body, _ = self.raw("/api/jobs", method="POST", token=self.write_token,
                                       body={"type": kind, "payload": payload})
            self.assertEqual(status, 403, f"{kind} was enqueued over REST: {body[:200]}")
            detail = json.loads(body)
            self.assertTrue(str(detail.get("requestId", "")).startswith("cfm-"),
                            f"{kind}: 403 must carry the pending requestId, got {detail}")
            self.assertIn("confirmation", detail["error"], detail["error"])
            with self.state.lock:
                self.assertEqual(len(self.state.jobs), 0, f"{kind} reached the queue")

    def test_both_transports_reach_the_same_verdict_for_the_same_work(self):
        """One decision, two doors: `/mcp` `tools/call` and `POST /api/jobs` must
        agree for every gated tool, and both must unblock on one approval."""
        from mcp_core import TOOL_MAP, job_tool_name, job_kind_is_read_only

        modern = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
        pairs = 0
        for kind, payload in self.GATED_WORK:
            tool = job_tool_name(kind)
            self.assertIsNotNone(tool, f"{kind} has no tool mapping — it would fail closed silently")
            self.assertIn(tool, TOOL_MAP, f"{tool} is not a registered tool")
            self.assertFalse(job_kind_is_read_only(kind, payload), f"{kind} must count as a write")

            # Door 1: MCP.
            rpc = {"jsonrpc": "2.0", "id": f"mcp-{kind}", "method": "tools/call", "_meta": dict(modern),
                   "params": {"name": tool, "arguments": payload}}
            mcp_status, mcp_body, _ = self.raw("/mcp", method="POST", token=self.write_token,
                                               body=rpc,
                                               extra_headers={"Accept": "application/json, text/event-stream"})
            self.assertEqual(mcp_status, 200, mcp_body[:300])
            envelope = self.jsonrpc_envelope(mcp_body)
            # A JSON-RPC `error` means the payload never reached the gate (bad
            # arguments, unknown tool), which would make this comparison vacuous.
            self.assertIn("result", envelope,
                          f"{kind}: /mcp answered {envelope.get('error') or envelope}")
            result = envelope["result"]
            self.assertEqual(result.get("resultType"), "input_required", f"{tool}: {str(result)[:220]}")
            self.assertEqual(result["structuredContent"]["reason"], "confirmation_required")
            mcp_request = result["structuredContent"]["requestId"]

            # Door 2: REST. Same kind+payload ⇒ an independent request, but the
            # same verdict and the same tool name in the message.
            rest_status, rest_body, _ = self.raw("/api/jobs", method="POST", token=self.write_token,
                                                 body={"type": kind, "payload": payload})
            self.assertEqual(rest_status, 403, rest_body[:300])
            rest_detail = json.loads(rest_body)
            self.assertIn(tool, rest_detail["error"], rest_detail["error"])
            pairs += 1

            # One operator approval unblocks the REST retry...
            self.approve(rest_detail["requestId"])
            status, body, _ = self.raw(
                "/api/jobs", method="POST", token=self.write_token,
                body={"type": kind, "payload": payload,
                      "confirmation": {"requestId": rest_detail["requestId"]}},
            )
            self.assertIn(status, (200, 201), f"{kind} did not enqueue after approval: {body[:220]}")
            with self.state.lock:
                self.state.jobs.clear()
                self.state.pending.clear()
                self.state.history.clear()

            # ...and the MCP call carries its own signed state, which the gate
            # honours without a second operator round-trip.
            retry = dict(rpc)
            retry["id"] = f"mcp-{kind}-retry"
            retry["params"] = dict(rpc["params"], requestState=result["requestState"],
                                   inputResponses={"confirm": {"action": "accept",
                                                               "content": {"decision": "approve"}}})
            self.approve(mcp_request)
            st2, body2, _ = self.raw("/mcp", method="POST", token=self.write_token, body=retry,
                                     extra_headers={"Accept": "application/json, text/event-stream"})
            self.assertEqual(st2, 200, body2[:300])
            res2 = json.loads(body2)["result"]
            self.assertNotEqual(res2.get("resultType"), "input_required", f"{tool} stayed gated: {str(res2)[:220]}")
            with self.state.lock:
                self.state.jobs.clear()
                self.state.pending.clear()
                self.state.history.clear()

        self.assertEqual(pairs, len(self.GATED_WORK))

    def test_arbitrary_code_execution_is_in_the_curated_destructive_set(self):
        """`execute_luau` and `probe` carry `destructiveHint: true` but were not
        members of `CONFIRM_GATED_TOOLS`, so mode `destructive` let them through."""
        from mcp_core import CONFIRM_GATED_TOOLS, TOOL_MAP, job_tool_name
        for name in ("run_luau", "execute_luau", "probe"):
            self.assertIn(name, CONFIRM_GATED_TOOLS, name)
        for name in ("execute_luau", "probe"):
            self.assertTrue(TOOL_MAP[name]["annotations"]["destructiveHint"], name)
        # Negative control in the mode the curated set actually governs. The
        # fixture server runs `--require-confirm=all`, where `requires()` answers
        # True for everything, so asserting "a read is free" against it would be
        # vacuous — build the destructive gate the way `BridgeState.__init__`
        # does and check that one.
        from confirm_gate import ConfirmGate
        destructive = ConfirmGate(mode="destructive", secret="unit-test-secret",
                                  destructive_tools=CONFIRM_GATED_TOOLS)
        for name in ("run_luau", "execute_luau", "probe"):
            # `run_luau` is a job *kind*; the tool it dispatches to is
            # `execute_luau`, and the gate keys on tool names.
            tool = job_tool_name(name) or name
            self.assertTrue(destructive.requires(tool, TOOL_MAP[tool]["annotations"], {"code": "print(1)"}),
                            f"{name} slips through `--require-confirm=destructive`")
        for name in ("ping_studio", "get_studio_state", "script_read"):
            self.assertFalse(destructive.requires(name, TOOL_MAP[name]["annotations"], {}),
                             f"{name} is a read and must not need a human in `destructive` mode")

    def test_read_only_oracle_stays_the_single_source_of_truth(self):
        """The REST path must ask `is_tool_call_read_only`, not a job-type list,
        so an argument-level read (`action: "get"`) stays free in `write` mode."""
        from mcp_core import is_tool_call_read_only, job_kind_is_read_only, job_tool_name
        cases = [
            ("attributes", {"action": "get", "paths": ["Workspace"]}, True),
            ("attributes", {"action": "set", "paths": ["Workspace"], "attributes": {"a": 1}}, False),
            ("camera", {"action": "get"}, True),
            ("camera", {"action": "set", "fov": 80}, False),
            ("history", {"action": "status"}, True),
            ("history", {"action": "undo"}, False),
            ("playtest", {"action": "status"}, True),
            ("playtest", {"action": "start_play"}, False),
            ("tags", {"action": "list_all"}, True),
            ("tags", {"action": "add", "paths": ["Workspace"], "tag": "T"}, False),
            ("device_simulator", {"action": "status"}, True),
            ("device_simulator", {"action": "set_device", "device": "iPhone"}, False),
            ("run_luau", {"code": "return 1"}, False),
            ("ping", {}, True),
            ("studio_state", {}, True),
            ("get_tree", {"path": "StarterGui"}, True),
            ("hash_scripts", {"root": "game"}, True),
        ]
        for kind, payload, expected in cases:
            self.assertEqual(job_kind_is_read_only(kind, payload), expected, (kind, payload))
            tool = job_tool_name(kind)
            if tool:
                self.assertEqual(is_tool_call_read_only(tool, payload), expected, (tool, payload))
        # Unmapped kinds fail closed: a brand-new job type is a write until
        # somebody says otherwise.
        self.assertFalse(job_kind_is_read_only("brand_new_kind", {}))
        self.assertIsNone(job_tool_name("brand_new_kind"))

    def test_a_rest_caller_cannot_claim_the_dispatcher_cleared_the_gate(self):
        """`gateCleared` is set only from the in-process call context; a body
        field of the same name must not be able to skip the gate."""
        status, body, _ = self.raw("/api/jobs", method="POST", token=self.write_token,
                                   body={"type": "run_luau", "payload": {"code": "print(1)"},
                                         "gateCleared": True, "principal": "owner"})
        self.assertEqual(status, 403, body[:220])
        self.assertIn("requestId", json.loads(body))
        with self.state.lock:
            self.assertEqual(len(self.state.jobs), 0)

    def test_a_client_cannot_answer_its_own_confirmation(self):
        """A human in the loop means a human, not the caller.

        `elicitation_capable` is derived from per-request `_meta`
        (`io.modelcontextprotocol/clientCapabilities`) — a field the *caller*
        writes. Accepting `inputResponses` from a caller that declared the
        capability let a prompt-injected agent approve its own `execute_luau`
        with two extra JSON fields, which made the whole gate decoration.
        """
        from mcp_core import META_CLIENT_CAPABILITIES
        modern = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
                  META_CLIENT_CAPABILITIES: {"elicitation": {}}}
        code = "print('self-approved')"

        def call(msg_id, **extra_params):
            rpc = {"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "_meta": dict(modern),
                   "params": dict({"name": "execute_luau", "arguments": {"code": code}}, **extra_params)}
            status, body, _ = self.raw("/mcp", method="POST", token=self.write_token, body=rpc)
            self.assertEqual(status, 200, body[:240])
            envelope = self.jsonrpc_envelope(body)
            self.assertIn("result", envelope, envelope.get("error"))
            return envelope["result"]

        first = call("self-1")
        self.assertEqual(first["resultType"], "input_required", str(first)[:240])
        state_token = first.get("requestState")
        self.assertTrue(state_token, "a modern client is handed a requestState to echo")
        request_id = first["structuredContent"]["requestId"]

        # The bypass attempt: echo the state, declare elicitation, answer yes.
        second = call("self-2", requestState=state_token,
                      inputResponses={"confirm": {"decision": "approve"}})
        self.assertEqual(second["resultType"], "input_required",
                         f"the client approved its own call: {str(second)[:240]}")
        self.assertEqual(second["structuredContent"]["reason"], "client_answers_disabled")
        self.assertEqual(second["structuredContent"]["requestId"], request_id,
                         "the original request must stay pending, not be re-minted")
        self.assertIn("operator", json.dumps(second).lower())
        with self.state.lock:
            self.assertEqual(len(self.state.jobs), 0, "self-approved work reached the queue")

        # The same request unblocks the moment a human answers it out-of-band.
        self.approve(request_id)
        status, body, _ = self.raw("/api/jobs", method="POST", token=self.write_token,
                                   body={"type": "run_luau", "payload": {"code": code},
                                         "confirmation": {"requestId": request_id}})
        self.assertEqual(status, 201, body[:240])

        # And the opt-in is real: an operator who runs a host that genuinely
        # elicits keeps the in-band flow by setting the documented variable.
        with self.state.lock:
            self.state.jobs.clear()
            self.state.history.clear()
        with mock.patch.object(self.state.confirm_gate, "trust_client_answers", True):
            trusted_first = call("self-3")
            self.assertEqual(trusted_first["resultType"], "input_required")
            trusted_second = call("self-4", requestState=trusted_first["requestState"],
                                  inputResponses={"confirm": {"decision": "approve"}})
            self.assertNotEqual(trusted_second.get("resultType"), "input_required",
                                f"the opt-in did not restore the in-band flow: {str(trusted_second)[:240]}")
            self.assertNotIn("confirmation_required", json.dumps(trusted_second))

    # -----------------------------------------------------------------------
    # P0-S2 / P3-T2 — no credential disclosure to a limited caller
    # -----------------------------------------------------------------------

    #: Every route the bridge serves, with a method and a body that reaches the
    #: handler. Table-driven so a route added later is covered by construction —
    #: `test_route_table_matches_the_server_source` fails if one is missing.
    ROUTES = [
        ("/", "GET", None),
        ("/api/agent-guide", "GET", None),
        ("/api/audit", "GET", None),
        ("/api/audit?verify=1", "GET", None),
        ("/api/bus", "GET", None),
        ("/api/chat", "GET", None),
        ("/api/confirmations", "GET", None),
        ("/api/events?once=1", "GET", None),
        ("/api/health", "GET", None),
        ("/api/jobs", "GET", None),
        ("/api/kit/assets", "GET", None),
        ("/api/kit/catalog?category=modules", "GET", None),
        ("/api/kit/catalog?category=modules&item=NetworkGuard", "GET", None),
        ("/api/kit/cloud-icons?query=shield&limit=3", "GET", None),
        # Smallest render the handler accepts: the sweep visits every route as
        # anonymous, read and write, and this one draws a PNG each time.
        ("/api/kit/viewport-preview?width=240&height=160", "GET", None),
        ("/api/local-boot", "GET", None),
        ("/api/metrics", "GET", None),
        ("/api/ops", "GET", None),
        ("/api/peers", "GET", None),
        ("/api/plugin-status", "GET", None),
        ("/api/plugin/download", "GET", None),
        # The plugin's long-poll. `wait=0` keeps the sweep from blocking and
        # `goodbye=1` makes the write-scoped visit unregister its probe peer
        # instead of leaving a fake Studio behind for the next test.
        ("/api/poll?peer=route-sweep&dm=edit&place=Sweep&placeId=1&pv=6.1.0&wait=0&goodbye=1",
         "GET", None),
        ("/api/self-test?includeStudioSmoke=false", "GET", None),
        ("/api/snapshots", "GET", None),
        ("/api/sse-ticket", "GET", None),
        ("/api/state", "GET", None),
        ("/api/studios", "GET", None),
        ("/api/subscriptions", "GET", None),
        ("/api/tunnel", "GET", None),
        ("/.well-known/mcp/server-card.json", "GET", None),
        ("/static/dashboard.js", "GET", None),
        ("/static/index.html", "GET", None),
        ("/api/bus", "POST", {"action": "status"}),
        ("/api/chat", "POST", {"action": "poll"}),
        ("/api/confirmations", "POST", {"action": "status"}),
        ("/api/jobs", "POST", {"type": "ping"}),
        ("/api/mcp/events", "POST", {"kind": "route_sweep", "detail": {"probe": True}}),
        ("/api/playtest/force-stop", "POST", {"action": "evict_only", "reason": "route_sweep"}),
        ("/api/queue/clear", "POST", {}),
        ("/api/result", "POST", {"id": "route-sweep-nope", "ok": False, "error": "probe"}),
        ("/api/snapshots", "POST", {"action": "list"}),
        ("/api/studios", "POST", {"action": "list"}),
        ("/api/subscriptions/close", "POST", {"id": "sub-none"}),
        ("/api/tokens", "POST", {"action": "list"}),
        ("/api/tunnel", "POST", {"action": "status"}),
        ("/api/ui/export-web", "POST", {"tree": {"name": "Workspace"}}),
        ("/api/ui/import-web", "POST", {"preset": "rpg_hud", "installInStudio": False}),
        ("/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    ]

    def test_route_table_matches_the_server_source(self):
        """Guard for the sweep below: every `/api/*` and `/static/*` route the
        server dispatches must appear in `ROUTES`, so a new route cannot escape
        the disclosure check by being unlisted."""
        src = (HERE / "server.py").read_text(encoding="utf-8")
        literal = set(re.findall(r'if path == "(/[A-Za-z0-9._/\-]+)"', src))
        literal |= set(re.findall(r'if path\.startswith\("(/[A-Za-z0-9._/\-]+)"\)', src))
        # Prefix routes are covered by one representative member each.
        prefix_roots = {"/api/jobs/", "/api/snapshots/", "/api/kit/image/", "/api/kit/audio/",
                        "/api/kit/model/", "/api/kit/thumbnail/", "/api/kit/procedural/",
                        "/api/kit/synth-audio/", "/static/"}
        covered = set()
        for path, _method, _body in self.ROUTES:
            clean = path.split("?", 1)[0]
            covered.add(clean)
            for root in prefix_roots:
                if clean.startswith(root):
                    covered.add(root)
        missing = sorted(p for p in literal
                         if p not in covered
                         and not any(p.startswith(root) for root in prefix_roots)
                         and p not in prefix_roots)
        self.assertEqual(missing, [], f"add these routes to TestV61SecurityHardening.ROUTES: {missing}")

    def test_no_limited_caller_ever_sees_the_owner_credential(self):
        """Sweep every route as (a) nobody, (b) read, (c) write. The owner token
        must not appear in a body or a header for any of them. Pre-6.1.0,
        `/api/plugin/download` and `/api/local-boot` both leaked it."""
        offenders = []
        for path, method, body in self.ROUTES:
            for label, token in (("anonymous", None), ("read", self.read_token),
                                 ("write", self.write_token)):
                status, text, headers = self.raw(path, method=method, body=body, token=token)
                if status == -1:
                    continue  # refused at the transport level: nothing leaked
                haystacks = [text] + [f"{k}: {v}" for k, v in headers.items()]
                for hay in haystacks:
                    if self.OWNER in hay:
                        offenders.append((label, method, path, hay[:160]))
        self.assertEqual(offenders, [], f"owner credential disclosed: {offenders}")

    def test_plugin_download_is_admin_only_and_bakes_a_dedicated_token(self):
        """Pre-6.1.0: read-scoped, and it baked `state.token` into the Lua."""
        for label, token, want in (("anonymous", None, 401), ("read", self.read_token, 403),
                                   ("write", self.write_token, 403), ("admin", self.admin_token, 200)):
            status, body, headers = self.raw("/api/plugin/download", token=token)
            self.assertEqual(status, want, f"{label}: {body[:160]}")
            if want != 200:
                self.assertNotIn(self.OWNER, body)
                continue
            self.assertIn("ArenaBridge.lua", headers.get("Content-Disposition", ""))
            baked = re.search(r'local BRIDGE_TOKEN = "([^"]*)"', body)
            self.assertIsNotNone(baked, "the download must still carry a usable credential")
            self.assertNotEqual(baked.group(1), self.OWNER, "the owner token was baked into the plugin")
            self.assertTrue(baked.group(1).startswith("arb_"), baked.group(1)[:12])
            # It is a real, separately revocable write token.
            rows = [r for r in self.state.tokens.list()["tokens"]
                    if r["label"].startswith("plugin-download:")]
            self.assertTrue(rows, "the mint must be recorded")
            self.assertEqual(rows[-1]["role"], "write")
            self.assertIsNotNone(rows[-1]["expires"], "the minted token must expire")
            st, _, _ = self.raw("/api/poll?peer=mint-probe&dm=edit&place=P&placeId=1&pv=6.1.0&wait=0",
                                token=baked.group(1))
            self.assertEqual(st, 200, "the baked token must actually authenticate")
            audit = json.dumps(self.state.telemetry.audit.tail(40))
            self.assertIn("plugin-download", audit, "the mint must be auditable")

    def test_local_boot_withholds_the_credential_from_a_limited_caller(self):
        """Pre-6.1.0: any loopback request got `"token": "<owner>"`."""
        for label, token in (("anonymous", None), ("read", self.read_token), ("write", self.write_token)):
            status, body, _ = self.raw("/api/local-boot", token=token)
            self.assertEqual(status, 200, f"{label}: the boot payload itself is not secret")
            data = json.loads(body)
            self.assertEqual(data["token"], "", f"{label} was handed a credential")
            self.assertTrue(data["tokenRequired"])
            self.assertTrue(data["tokenWithheld"], f"{label} should be told why")
            self.assertEqual(len(data["tools"]), len(TOOL_SPECS), "the tool list must still render")
        status, body, _ = self.raw("/api/local-boot", token=self.admin_token)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["token"], self.OWNER,
                         "an admin caller may still self-fill the dashboard")

    def test_query_string_credentials_are_refused_outside_the_plugin_routes(self):
        """`?token=` leaked into proxy logs, browser history and Referer. It is
        now accepted only where a header is impossible (the plugin's poll/result
        and the ticketed SSE stream).

        The loopback read allowance is switched off first, or a 200 proves
        nothing about the query token: `_is_local_convenience_request()` hands an
        anonymous localhost caller `read` on discovery routes. `Sec-Fetch-Site:
        cross-site` is precisely how that helper recognises a hostile web page
        talking to localhost, so with it sent, any 200 below can only have come
        from the credential in the URL.
        """
        for path in ("/api/state", "/api/ops", "/api/jobs", "/api/tokens"):
            status, body, _ = self.raw(f"{path}?token={urllib.parse.quote(self.OWNER)}",
                                       extra_headers=self.CROSS_SITE)
            self.assertIn(status, (401, 403), f"{path} still accepts ?token=: {status} {body[:140]}")
            self.assertNotIn(self.OWNER, body)
            # The same credential in a header still works from the same origin,
            # so the rejection above is about *where* the secret travelled and
            # not about the secret being wrong.
            ok_status, ok_body, _ = self.raw(path, token=self.OWNER, extra_headers=self.CROSS_SITE)
            self.assertEqual(ok_status, 200, f"{path} must accept a header credential: {ok_body[:160]}")

        # `/api/local-boot` answers 200 for everybody by design — the dashboard
        # has to render before it has a token — so the assertion is that the
        # credential in the URL buys nothing.
        status, body, _ = self.raw(f"/api/local-boot?token={urllib.parse.quote(self.OWNER)}",
                                   extra_headers=self.CROSS_SITE)
        self.assertEqual(status, 200, body[:160])
        boot = json.loads(body)
        self.assertEqual(boot["token"], "", "a URL credential must not unlock the owner token")
        self.assertFalse(boot["authenticated"], "the query token was accepted")
        self.assertTrue(boot["tokenWithheld"])

        rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        status, body, _ = self.raw(f"/mcp?token={urllib.parse.quote(self.OWNER)}", method="POST", body=rpc,
                                   extra_headers={"Accept": "application/json, text/event-stream",
                                                  **self.CROSS_SITE})
        self.assertEqual(status, 401, f"/mcp still accepts ?token=: {body[:160]}")
        # The plugin routes keep working with a query token (RequestAsync cannot
        # be relied on to send headers across Studio versions).
        for path in ("/api/poll?peer=qt-probe&dm=edit&place=P&placeId=1&pv=6.1.0&wait=0",
                     "/api/result"):
            status, body, _ = self.raw(f"{path}&token={urllib.parse.quote(self.write_token)}"
                                       if "?" in path else f"{path}?token={urllib.parse.quote(self.write_token)}",
                                       method="POST" if path == "/api/result" else "GET",
                                       body={"id": "nope", "ok": False, "error": "probe"}
                                       if path == "/api/result" else None)
            self.assertNotIn(status, (401, 403), f"{path} must keep accepting the plugin's query token: {body[:160]}")

    def test_sse_ticket_is_single_use_and_cannot_reach_another_route(self):
        """The dashboard's `EventSource` cannot set a header, so it redeems a
        one-shot ticket instead of putting the owner token in the URL."""
        status, body, _ = self.raw("/api/sse-ticket", token=self.read_token)
        self.assertEqual(status, 200, body[:200])
        ticket = json.loads(body)["ticket"]
        self.assertGreaterEqual(len(ticket), 24, "the ticket must be unguessable")
        st1, _, _ = self.raw(f"/api/events?once=1&ticket={urllib.parse.quote(ticket)}")
        self.assertEqual(st1, 200, "the ticket must authenticate the stream once")
        st2, _, _ = self.raw(f"/api/events?once=1&ticket={urllib.parse.quote(ticket)}")
        self.assertEqual(st2, 401, "the ticket must be single-use")
        for path in ("/api/state", "/api/ops", "/api/tokens", "/api/plugin/download"):
            st, _, _ = self.raw(f"{path}?ticket={urllib.parse.quote(ticket)}")
            self.assertIn(st, (401, 403), f"{path} accepted an SSE ticket")
        # Minting a ticket needs a credential in the first place.
        st, _, _ = self.raw("/api/sse-ticket")
        self.assertIn(st, (401, 403), "an anonymous caller must not be able to mint a ticket")
        self.assertFalse(self.state.consume_sse_ticket(""), "an empty ticket must not validate")
        self.assertFalse(self.state.consume_sse_ticket("not-a-ticket"))

    # -----------------------------------------------------------------------
    # P0-S3 — dashboard XSS and clickjacking
    # -----------------------------------------------------------------------

    def test_dashboard_and_static_assets_send_a_strict_csp(self):
        for path, ctype in (("/", "text/html"), ("/static/index.html", "text/html"),
                            ("/static/dashboard.js", "application/javascript"),
                            ("/static/dashboard.css", "text/css")):
            status, _body, headers = self.raw(path)
            self.assertEqual(status, 200, path)
            csp = headers.get("Content-Security-Policy", "")
            self.assertTrue(csp, f"{path} sent no Content-Security-Policy")
            self.assertIn("default-src 'self'", csp)
            self.assertIn("script-src 'self'", csp)
            self.assertNotIn("unsafe-inline", csp.split("script-src")[1].split(";")[0],
                             f"{path}: script-src must not allow inline script")
            self.assertIn("frame-ancestors 'none'", csp)
            self.assertEqual(headers.get("X-Frame-Options"), "DENY", path)
            self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff", path)
            self.assertEqual(headers.get("Referrer-Policy"), "no-referrer", path)
            self.assertEqual(ctype, headers.get("Content-Type", "").split(";")[0].strip(), path)

    def test_served_page_has_no_inline_script_or_handler(self):
        """The CSP above is only honest if the page obeys it. Pre-6.1.0 the page
        carried ~133 `onclick="fn('${esc(x)}')"` sites and one inline <script>."""
        status, html, _ = self.raw("/")
        self.assertEqual(status, 200)
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)", "an inline <script> would be CSP-blocked")
        self.assertNotRegex(html, r"\son[a-z]+\s*=", "an inline handler would be CSP-blocked")
        self.assertIn('<link rel="stylesheet" href="/static/dashboard.css?v=', html)
        self.assertIn('<script src="/static/dashboard.js?v=', html)
        self.assertIn(f"v{bridge_server.VERSION}", html)
        self.assertNotIn("__BRIDGE_VERSION__", html)
        self.assertGreater(html.count("data-act="), 100, "the delegated surface should be intact")

    def test_static_route_serves_only_whitelisted_assets(self):
        for path in ("/static/dashboard.js", "/static/dashboard.css", "/static/index.html"):
            status, body, headers = self.raw(path)
            self.assertEqual(status, 200, path)
            self.assertGreater(len(body), 1000, path)
            self.assertNotIn("__BRIDGE_VERSION__", body, f"{path} must be version-stamped")
            self.assertIn("max-age", headers.get("Cache-Control", ""))
        for path in ("/static/../../server.py", "/static/..%2fserver.py", "/static/bridge.token",
                     "/static/nope.js", "/static/", "/static/index.html/../../mcp_core.py"):
            status, body, _ = self.raw(path)
            self.assertEqual(status, 404, f"{path} returned {status}")
            self.assertNotIn("def make_http_handler", body, f"{path} served Python source")
            self.assertNotIn(self.OWNER, body)

    def test_dashboard_harness_proves_escaping_and_delegation(self):
        """`tools/dashboard_harness.js` loads the real dashboard module in a stub
        DOM and asserts `esc()`/`studioCard()`/`actData()` behaviour. Run with a
        negative control so a green harness cannot be vacuous."""
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed; the harness cannot run")
        good = subprocess.run([node, str(HERE / "tools" / "dashboard_harness.js"), "--json"],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(good.returncode, 0, good.stdout[-3000:] + good.stderr[-1500:])
        report = json.loads(good.stdout)
        self.assertTrue(report["ok"])
        names = {row["name"] for row in report["checks"]}
        for needle in ("esc escapes the full attribute-breaking set",
                       "studioCard renders hostile peer/place data as inert markup",
                       "static/index.html has no inline handlers and no inline script",
                       "every data-act resolves and every data-args parses"):
            self.assertIn(needle, names)

        # Negative control: restore the pre-6.1.0 `esc` and inline pin button in a
        # throwaway copy and confirm the harness fails on it.
        js = (HERE / "static" / "dashboard.js").read_text(encoding="utf-8")
        # Swap the escaper's character class rather than a whole line, so the
        # control keeps applying if the line is ever reformatted.
        hardened_class = '/[&<>"\'`]/g'
        self.assertIn(hardened_class, js,
                      "the hardened esc() character class changed; update this negative control")
        vulnerable = js.replace(hardened_class, "/[&<>]/g", 1)
        vulnerable = vulnerable.replace(
            "c => ESC_MAP[c]", "c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])", 1)
        delegated_pin = "${actData('pinStudio', s.peerId)}"
        self.assertIn(delegated_pin, vulnerable,
                      "the pin button is no longer delegated; update this negative control")
        vulnerable = vulnerable.replace(
            delegated_pin, "onclick='pinStudio(${esc(JSON.stringify(s.peerId))})'", 1)
        self.assertNotEqual(vulnerable, js, "the negative control did not apply; the harness is untested")
        with tempfile.TemporaryDirectory(prefix="arena-v61-negctl-") as neg:
            neg = Path(neg)
            (neg / "static").mkdir()
            (neg / "tools").mkdir()
            (neg / "static" / "dashboard.js").write_text(vulnerable, encoding="utf-8")
            shutil.copy(HERE / "static" / "index.html", neg / "static" / "index.html")
            shutil.copy(HERE / "static" / "dashboard.css", neg / "static" / "dashboard.css")
            shutil.copy(HERE / "tools" / "dashboard_harness.js", neg / "tools" / "dashboard_harness.js")
            bad = subprocess.run([node, str(neg / "tools" / "dashboard_harness.js"), "--json"],
                                 capture_output=True, text=True, timeout=120, cwd=str(neg))
            self.assertEqual(bad.returncode, 1, "the harness passed on vulnerable code — it proves nothing")
            neg_report = json.loads(bad.stdout)
            failed = {row["name"] for row in neg_report["checks"] if row["status"] == "fail"}
            self.assertIn("esc escapes the full attribute-breaking set", failed)
            self.assertIn("studioCard renders hostile peer/place data as inert markup", failed)

    # -----------------------------------------------------------------------
    # P0-S4 / P3-T3 — path traversal, arbitrary writes, SSRF
    # -----------------------------------------------------------------------

    def test_repo_asset_and_save_paths_cannot_leave_their_root(self):
        """Pre-6.1.0: `repoAsset: "../registry.json"` read 3.7 MB from outside
        `assets/images/`, and `saveFile: "~/.arena_write_probe.png"` wrote to $HOME."""
        from mcp_core import UnsafePathError, safe_import_path, safe_local_path
        with tempfile.TemporaryDirectory(prefix="arena-v61-paths-") as tmp:
            root = Path(tmp) / "images"
            (root / "ui").mkdir(parents=True)
            (root / "ui" / "ok.png").write_bytes(b"png")
            (Path(tmp) / "registry.json").write_text('{"secret": true}', encoding="utf-8")
            outside = Path(tmp) / "escape.png"

            self.assertEqual(safe_local_path(root, "ui/ok.png"), (root / "ui" / "ok.png").resolve())
            self.assertEqual(safe_local_path(root, "ui"), (root / "ui").resolve())
            for bad in ("../registry.json", "../../etc/passwd", "/etc/passwd", "~/.arena_probe.png",
                        "C:\\Windows\\win.ini", "\\?\\GLOBALROOT\\Device\\HarddiskVolume1\\x",
                        "ui/../../registry.json", "", "   ", "ui\\x\x00.png"):
                with self.assertRaises(UnsafePathError, msg=f"{bad!r} was accepted"):
                    safe_local_path(root, bad)
            with self.assertRaises(UnsafePathError):
                safe_local_path(root, "ui/missing.png", must_exist=True, allow_dir=False)
            # A symlink inside the root that points outside it must be refused.
            try:
                (root / "link.json").symlink_to(Path(tmp) / "registry.json")
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaises(UnsafePathError, msg="a symlink escaped the root"):
                    safe_local_path(root, "link.json", must_exist=True, allow_dir=False)
            self.assertFalse(outside.exists())

            # Write-side: `saveFile`/`outputFile` are confined to import roots.
            home_probe = Path.home() / ".arena_write_probe_v61.png"
            for bad in (str(home_probe), "/etc/arena_probe.png", "~/.arena_probe.png",
                        "/proc/self/environ"):
                with self.assertRaises(UnsafePathError, msg=f"{bad!r} was accepted for writing"):
                    safe_import_path(bad, must_exist=False, suffixes=(".png",))
            self.assertFalse(home_probe.exists(), "import_image wrote outside the repo")
            allowed = Path(tmp) / "render.png"
            with mock.patch("mcp_core.import_roots", return_value=(Path(tmp).resolve(),)):
                self.assertEqual(safe_import_path(str(allowed), must_exist=False,
                                                  suffixes=(".png",)), allowed.resolve())
                with self.assertRaises(UnsafePathError):
                    safe_import_path(str(Path(tmp) / "x.exe"), must_exist=False, suffixes=(".png",))
                with self.assertRaises(UnsafePathError):
                    safe_import_path("/etc/passwd", must_exist=False, suffixes=(".png",))

    def test_import_image_refuses_traversal_and_file_urls_end_to_end(self):
        """The §11 repro, driven through the tool itself rather than the helper."""
        from mcp_core import import_image_helper
        client = mock.Mock()
        client.health.return_value = {"studio_connected": False}
        client.submit_job.return_value = {"ok": True, "result": {}}
        with tempfile.TemporaryDirectory(prefix="arena-v61-kit-") as tmp:
            kit = Path(tmp)
            (kit / "assets" / "images").mkdir(parents=True)
            (kit / "registry.json").write_text('{"rows": []}', encoding="utf-8")
            home_probe = Path.home() / ".arena_write_probe_v61b.png"
            with mock.patch("image_pipeline.find_kit_root", return_value=kit), \
                    mock.patch("mcp_core.find_kit_root", return_value=kit, create=True):
                for args in ({"repoAsset": "../registry.json"},
                             {"repoAsset": "../../etc/passwd"},
                             {"imageUrl": "file:///etc/hostname"},
                             {"imageUrl": "http://127.0.0.1:8077/api/state"},
                             {"imageUrl": "http://169.254.169.254/latest/meta-data/"},
                             {"imageUrl": "data:text/plain,hello"},
                             {"imageUrl": "ftp://example.com/x.png"},
                             {"filePath": "/etc/passwd"}):
                    with self.assertRaises(ValueError, msg=f"import_image accepted {args}"):
                        import_image_helper(client, dict(args, saveToRepo=False, name="probe"))
                self.assertFalse(home_probe.exists(), "import_image wrote to $HOME")
                self.assertFalse((kit.parent / "arena_probe.png").exists())

                # The write side is contained rather than refused: `saveSubdir`'s
                # character filter drops `.`, so a traversal attempt degenerates
                # to the default subfolder, and `safe_local_path` is the second
                # line of defence. Either way the bytes must land inside
                # assets/images/ and nowhere else.
                images_dir = (kit / "assets" / "images").resolve()
                for subdir in ("../../..", "../../../../etc", "..\\..\\windows", "ui/../../.."):
                    out = import_image_helper(client, {"base64Data": "aGVsbG8=", "saveToRepo": True,
                                                       "saveSubdir": subdir, "name": "probe"})
                    saved = Path(out["savedToRepo"])
                    self.assertIn(images_dir, saved.parents,
                                  f"saveSubdir={subdir!r} wrote to {saved}")
                    for manifest in (out.get("manifestUpdated") or "",):
                        if manifest:
                            self.assertIn(kit.resolve(), Path(manifest).parents, manifest)
                escaped = [p for p in kit.parent.glob("*") if p.name.startswith("probe")]
                self.assertEqual(escaped, [], f"import_image wrote outside the kit: {escaped}")
                self.assertFalse(home_probe.exists())
                # An argument the schema does not define must stay inert: there is
                # no `saveFile`/`outputFile` sink for an agent to discover by
                # guessing, and inventing one does not move the write elsewhere.
                import_image_helper(client, {"proceduralPreset": "icon_coin", "name": "probe",
                                             "saveToRepo": False, "saveFile": str(home_probe),
                                             "outputFile": str(home_probe)})
                self.assertFalse(home_probe.exists(), "an unknown argument became a write target")

    def test_every_operator_supplied_file_argument_is_confined(self):
        """P0-S4 is only real if it covers *every* file-shaped argument.

        These six were the ones the first pass missed, and each is a different
        kind of primitive: write anywhere (`agent_bus` export, `drift_and_sync`
        pull), read anywhere (`luau_check`, `audit_network_security`,
        `import_images_batch`), and read-then-exfiltrate (`open_cloud_action`
        publish_place uploads whatever file it is handed).
        """
        home = Path.home()
        client = mock.Mock()
        client.health.return_value = {"studio_connected": False}
        client.submit_job.return_value = {"ok": True, "result": {}}
        write_probe = home / ".arena_write_probe_v61d.md"
        read_probes = ("/etc/passwd", "/etc/hostname", str(home / ".bashrc"))

        calls = [
            ("agent_bus export", lambda p: AgentBus().handle({"action": "export", "path": p})),
            ("luau_check", lambda p: run_luau_check(path=p)),
            ("open_cloud_action", lambda p: execute_tool(
                client, "open_cloud_action",
                {"action": "publish_place", "universeId": 1, "placeId": 1, "apiKey": "k", "placeFile": p})),
            ("import_images_batch", lambda p: execute_tool(
                client, "import_images_batch", {"files": [p]})),
            ("import_images_batch dir", lambda p: execute_tool(
                client, "import_images_batch", {"directory": p})),
            ("audit_network_security", lambda p: execute_tool(
                client, "audit_network_security", {"localDir": p, "includeStudio": False})),
            ("drift_and_sync", lambda p: execute_tool(
                client, "drift_and_sync",
                {"mode": "pull", "localDir": p, "studioRoot": "ServerScriptService.NightLoop"})),
        ]
        for label, call in calls:
            for probe in read_probes + (str(write_probe), str(home)):
                with self.assertRaises(ValueError, msg=f"{label} accepted {probe}"):
                    call(probe)
        self.assertFalse(write_probe.exists(), "agent_bus export wrote outside the import roots")

        # And the legitimate use still works: an export into a temp dir, which is
        # an import root, must land on disk exactly as before.
        with tempfile.TemporaryDirectory(prefix="arena-v61-export-") as tmp:
            target = Path(tmp) / "handoff.md"
            out = AgentBus().handle({"action": "export", "path": str(target)})
            self.assertEqual(Path(out["path"]), target)
            self.assertTrue(target.is_file())
            self.assertIn("Agent handoff brief", target.read_text(encoding="utf-8"))

    def test_safe_url_blocks_every_non_http_scheme_and_local_target(self):
        from mcp_core import UnsafeURLError, safe_url
        for bad in ("file:///etc/hostname", "file://localhost/etc/passwd", "data:text/plain,x",
                    "ftp://example.com/a.png", "gopher://127.0.0.1:6379/_INFO",
                    "http://127.0.0.1:8077/api/state", "http://localhost:8077/",
                    "http://[::1]:8077/", "http://169.254.169.254/latest/meta-data/",
                    "http://10.0.0.5/internal", "http://192.168.1.1/", "http://metadata.google.internal/",
                    "http://foo.localhost/", "https://", "//example.com/x", "", "   ",
                    "C:\\Windows\\win.ini", "\\\\server\\share\\x.png",
                    "http://example.com/\x00", "http://example.com/a\nb"):
            with self.assertRaises(UnsafeURLError, msg=f"{bad!r} was accepted"):
                safe_url(bad)
        for good in ("https://example.com/a.png", "http://cdn.example.com/x/y.jpg?q=1",
                     "https://tr.rbxcdn.com/abc.png"):
            self.assertEqual(safe_url(good), good)
        # Internal callers that legitimately talk to the bridge opt out explicitly.
        self.assertEqual(safe_url("http://127.0.0.1:8077/api/state", block_private=False),
                         "http://127.0.0.1:8077/api/state")

    # -----------------------------------------------------------------------
    # P0-S5 — safe defaults
    # -----------------------------------------------------------------------

    def test_default_bind_is_loopback_and_public_binds_warn(self):
        import inspect
        from server import _bind_is_public, create_http_server
        signature = inspect.signature(create_http_server)
        self.assertEqual(signature.parameters["host"].default, "127.0.0.1",
                         "create_http_server must default to loopback")
        src = (HERE / "server.py").read_text(encoding="utf-8")
        self.assertIn('ap.add_argument("--host", default=os.environ.get("BRIDGE_HOST", "127.0.0.1")', src)
        for host in ("0.0.0.0", "::", "*", "", "10.0.0.5", "bridge.example.com"):
            self.assertTrue(_bind_is_public(host), host)
        for host in ("127.0.0.1", "::1", "localhost"):
            self.assertFalse(_bind_is_public(host), host)
        # A live server really is bound where we asked, and refuses a wider bind
        # only by explicit request.
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")
        with mock.patch("server.print") as shout:
            srv2, st2 = create_http_server(host="0.0.0.0", port=0, token="x" * 24,
                                           state_dir=tempfile.mkdtemp(prefix="arena-v61-bind-"))
            try:
                warnings = " ".join(str(c.args[0]) for c in shout.call_args_list)
                self.assertIn("WARNING: exposed on all interfaces", warnings)
            finally:
                st2._reaper_stop.set()
                st2.tunnel.stop()
                srv2.server_close()

    def test_placeholder_token_is_refused_everywhere(self):
        """Pre-6.1.0: `arena-demo-7f3a` shipped in four files and authenticated."""
        import auth_core
        from auth_core import PLACEHOLDER_TOKEN, TokenStore
        from server import load_token_default
        self.assertEqual(PLACEHOLDER_TOKEN, "arena-demo-7f3a")
        store = TokenStore(Path(self.tmp) / "placeholder-tokens.json", primary_token=PLACEHOLDER_TOKEN)
        self.assertEqual(store.authenticate(PLACEHOLDER_TOKEN), (None, None),
                         "the demo literal must never authenticate")
        self.assertEqual(store.authenticate(f" {PLACEHOLDER_TOKEN} "), (None, None))
        self.assertIsNone(store.authenticate(PLACEHOLDER_TOKEN)[0])
        for value in (PLACEHOLDER_TOKEN, f"BRIDGE_TOKEN={PLACEHOLDER_TOKEN}"):
            with self.assertRaises(ValueError):
                if value.startswith("BRIDGE_TOKEN="):
                    with mock.patch.dict(os.environ, {"BRIDGE_TOKEN": PLACEHOLDER_TOKEN}, clear=False):
                        load_token_default(None, allow_generate=False)
                else:
                    load_token_default(value, allow_generate=False)
        with mock.patch.dict(os.environ, {"BRIDGE_TOKEN": PLACEHOLDER_TOKEN}, clear=False):
            with self.assertRaises(ValueError):
                load_token_default(None, allow_generate=False)
        # One definition, no copies: the other modules import it.
        for module in ("setup.py", "mock_studio.py", "arena_studio.py"):
            text = (HERE / module).read_text(encoding="utf-8")
            self.assertNotIn('PLACEHOLDER_TOKEN = "arena-demo-7f3a"', text,
                             f"{module} re-defined the placeholder literal")
            self.assertIn("PLACEHOLDER_TOKEN", text, f"{module} must import the shared constant")
        self.assertEqual(len(re.findall(r'"arena-demo-7f3a"', auth_core.__file__ and
                                        (HERE / "auth_core.py").read_text(encoding="utf-8"))), 1)
        # setup.py refuses to bake a plugin with it.
        import setup as bridge_setup
        for bad in ("", PLACEHOLDER_TOKEN):
            with self.assertRaises(SystemExit):
                bridge_setup.install_plugin(bad, 8077, explicit_dir=self.tmp)

    def test_first_run_generates_a_token_instead_of_open_admin(self):
        """Pre-6.1.0: no `bridge.token` ⇒ `authenticate()` returned open *admin*."""
        from server import load_token_default, mint_owner_token
        with tempfile.TemporaryDirectory(prefix="arena-v61-token-") as tmp:
            target = Path(tmp) / "bridge.token"
            generated = mint_owner_token(target, verbose=False)
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_text(encoding="utf-8").strip(), generated)
            self.assertGreaterEqual(len(generated), 32, "the generated secret is too short")
            mode = target.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600, f"bridge.token is {oct(mode)}, expected 0600")
            # Idempotent-by-file: a second call with the file present reuses it.
            with mock.patch("server.TOKEN_FILE", target):
                self.assertEqual(load_token_default(None, allow_generate=False), generated)
                self.assertEqual(load_token_default(None, allow_generate=True), generated)
            missing = Path(tmp) / "nope" / "bridge.token"
            with mock.patch("server.TOKEN_FILE", missing):
                self.assertEqual(load_token_default(None, allow_generate=False), "",
                                 "generation must be opt-in for embedded callers")
                fresh = load_token_default(None, allow_generate=True, verbose=False)
            self.assertGreaterEqual(len(fresh), 32)
            self.assertTrue(missing.is_file())
            self.assertEqual(missing.stat().st_mode & 0o777, 0o600)

        # And the server that results from it is not open admin.
        ctx, err = self.state.authenticate(None, is_loopback=True, required="admin")
        self.assertIsNone(ctx)
        self.assertIsNotNone(err, "an unauthenticated caller must not resolve to a context")
        self.assertEqual(err["code"], 401)
        # Open admin is reachable only by *asking* for it (`--no-auth`), which
        # `create_http_server` implements as `resolved_token = ""` on a state dir
        # with no token records. Note the fixture's own state dir is not that
        # case: it has minted read/write/admin records, and a stale registry must
        # not turn into an anonymous bypass either way.
        with tempfile.TemporaryDirectory(prefix="arena-v61-noauth-") as bare:
            open_state = bridge_server.BridgeState(token="", state_dir=Path(bare))
            ctx_open, err_open = open_state.authenticate(None, is_loopback=True, required="admin")
            self.assertIsNone(err_open)
            self.assertEqual(ctx_open["id"], "open", "explicit --no-auth still works, and says so")
            self.assertEqual(ctx_open["label"], "auth-disabled",
                             "the open context must be identifiable in the audit log")

        stale = bridge_server.BridgeState(token="", state_dir=self.tmp)
        self.assertTrue(stale.tokens.records, "the fixture state dir should hold minted tokens")
        ctx_stale, err_stale = stale.authenticate(None, is_loopback=True, required="read")
        self.assertIsNone(ctx_stale)
        self.assertEqual(err_stale["code"], 401,
                         "leftover token records must not re-open the bridge")

    # -----------------------------------------------------------------------
    # P0-S6 — kit catalogue policy, and the policy sets are live
    # -----------------------------------------------------------------------

    def test_kit_catalog_metadata_is_public_but_source_needs_read(self):
        """Pre-6.1.0: `?item=NetworkGuard` returned 5,396 chars of module source
        with no token, while every neighbouring `/api/*` route called `_require`.

        The fix is a *split*, not a blanket gate: the kit is CC0 and the
        dashboard gallery has to render before it holds a token, so a category
        listing stays public — but a listing is name/path/bytes only. Source
        comes out of `item=`/`section=`, and that needs `read`.
        """
        status, body, _ = self.raw("/api/kit/catalog?category=modules")
        self.assertEqual(status, 200, "kit metadata is CC0 and stays public")
        listing = json.loads(body)
        modules = listing.get("modules") or []
        self.assertTrue(modules, f"the public listing came back empty: {str(listing)[:200]}")
        for row in modules[:5]:
            self.assertEqual(sorted(k for k in row if k in ("content", "source", "code", "body")), [],
                             f"a public listing row carried source: {sorted(row)}")
        self.assertNotIn("local NetworkGuard", body)
        self.assertNotIn("--!strict", body, "Luau source leaked into the public listing")

        # `format=full` on a *category* is still a listing: no item, no source.
        status, body, _ = self.raw("/api/kit/catalog?category=all&format=full")
        self.assertEqual(status, 200, f"the inventory listing is metadata: {body[:160]}")
        self.assertNotIn("--!strict", body)

        status, body, _ = self.raw("/api/kit/catalog?category=modules&item=NetworkGuard")
        self.assertEqual(status, 401, f"module source was served anonymously: {body[:160]}")
        self.assertNotIn("NetworkGuard", body)

        status, body, _ = self.raw("/api/kit/catalog?category=all&section=modules")
        self.assertEqual(status, 401, f"a kit section was served anonymously: {body[:160]}")

        status, body, _ = self.raw("/api/kit/catalog?category=modules&item=NetworkGuard",
                                   token=self.read_token)
        self.assertEqual(status, 200, body[:200])
        data = json.loads(body)
        self.assertEqual(data.get("name"), "NetworkGuard")
        self.assertGreater(len(data.get("content") or ""), 500,
                           f"a read caller must get the real source: {sorted(data)}")

        status, body, _ = self.raw("/api/kit/catalog?category=all&section=modules",
                                   token=self.read_token)
        self.assertEqual(status, 200, body[:200])
        self.assertEqual(json.loads(body).get("section"), "modules")

    def test_declared_route_policy_is_enforced_and_not_dead_code(self):
        """`READ_ONLY_PATHS`/`ADMIN_PATHS` were dead: nothing read them, and
        `/api/plugin/download` sat in `READ_ONLY_PATHS` while handing out the
        owner token. They are now a floor every request is raised to."""
        import auth_core
        from auth_core import path_scope
        self.assertEqual(path_scope("/api/plugin/download"), "admin")
        self.assertEqual(path_scope("/api/tokens"), "admin")
        self.assertEqual(path_scope("/api/tokens/anything"), "admin")
        self.assertEqual(path_scope("/api/state"), "read")
        self.assertEqual(path_scope("/api/jobs/abc123"), "read")
        self.assertIsNone(path_scope("/mcp"), "routes with no declaration keep their handler's scope")
        self.assertNotIn("/api/plugin/download", auth_core.READ_ONLY_PATHS,
                         "the download route mints a credential; it must not be declared read-only")
        self.assertIn("/api/plugin/download", auth_core.ADMIN_PATHS)
        src = (HERE / "server.py").read_text(encoding="utf-8")
        self.assertIn("path_scope(", src, "server.py must consult the declared policy")
        self.assertIn("def _route_scope", src)

        # Behavioural proof: the declared admin floor wins over a handler that
        # asks for less. `/api/plugin/download`'s handler asks for admin and
        # `/api/tokens` is declared admin, so either path must refuse a write
        # token — and `/api/state`, declared read, must refuse nobody at all.
        for path, token, want in (("/api/plugin/download", self.write_token, 403),
                                  ("/api/plugin/download", self.read_token, 403),
                                  ("/api/tokens", self.write_token, 403),
                                  ("/api/tokens", self.admin_token, 200),
                                  ("/api/state", self.read_token, 200),
                                  ("/api/state", None, 401)):
            status, body, _ = self.raw(path, token=token)
            self.assertEqual(status, want, f"{path}: got {status}, want {want} — {body[:160]}")

        # Whole-surface proof, in the direction that matters. One rule covers
        # declared and undeclared routes alike: anything outside the two written-
        # down exception sets must reject an anonymous caller. The caller is
        # marked cross-site so the loopback read allowance cannot mask a handler
        # that forgot to call `_require` — that allowance is real and deliberate,
        # but it is granted by a handler, never by this table.
        public_ok = auth_core.PUBLIC_PATHS | auth_core.ROUTE_DECIDES_PATHS
        drift = []
        gated = 0
        for route_path, method, body in self.ROUTES:
            clean = route_path.split("?", 1)[0]
            if any(clean == exc or clean.startswith(exc.rstrip("/") + "/")
                   for exc in public_ok if exc != "/"):
                continue
            if clean == "/":
                continue
            gated += 1
            status, text, _ = self.raw(route_path, method=method, body=body,
                                       extra_headers=self.CROSS_SITE)
            if status not in (401, 403, 429):
                drift.append((method, route_path, path_scope(clean), status, text[:140]))
        self.assertEqual(drift, [], "reachable anonymously without a credential")
        self.assertGreaterEqual(gated, 30,
                                f"only {gated} routes were checked — the exception lists grew")

        # …and the exception lists are honest: they really are credential-free.
        for public_path in ("/", "/api/health", "/api/local-boot", "/api/kit/catalog?category=modules"):
            status, text, _ = self.raw(public_path, extra_headers=self.CROSS_SITE)
            self.assertEqual(status, 200, f"{public_path} should stay public: {text[:140]}")
            self.assertNotIn(self.OWNER, text)



class TestV61ManifestIntegrity(unittest.TestCase):
    """P1-B3 / P3-T4 / P3-T6 — the asset manifest is regenerable, idempotent,
    non-mutating under `check=True`, and agrees with the committed tree.

    `update_repo_asset_manifest` used to write both of its files in place and to
    raise `KeyError: 'relPath'` halfway through when a lock row had no path, which
    left a partially applied manifest behind *after* the image was already on
    disk. Both files are now replaced atomically, a pathless row is emitted with
    an empty `relPath`, and every interpolated value is escaped as a Luau literal.
    """

    @staticmethod
    def _png(size=4, rgba=(200, 30, 30, 255)):
        from image_pipeline import encode_png_rgba
        return encode_png_rgba(size, size, bytes(rgba) * (size * size))

    def _kit(self, tmp):
        kit = Path(tmp)
        (kit / "assets" / "images" / "ui").mkdir(parents=True)
        (kit / "modules").mkdir(parents=True)
        return kit

    def test_manifest_regeneration_is_byte_identical_and_only_writes_on_change(self):
        from image_pipeline import update_repo_asset_manifest
        with tempfile.TemporaryDirectory(prefix="arena-v61-manifest-") as tmp:
            kit = self._kit(tmp)
            (kit / "assets" / "images" / "ui" / "icon_a.png").write_bytes(self._png())
            # A name with a space and a duplicate basename in another folder:
            # both used to be able to alias one key and silently drop an asset.
            (kit / "assets" / "images" / "vfx").mkdir()
            (kit / "assets" / "images" / "vfx" / "icon a.png").write_bytes(self._png(6, (10, 200, 10, 255)))
            lock = kit / "assets" / "asset-lock.json"
            lua = kit / "modules" / "Assets.lua"

            with mock.patch("image_pipeline.find_kit_root", return_value=kit):
                first = update_repo_asset_manifest()
                self.assertTrue(first["checked"] is False)
                self.assertEqual(sorted(first["drift"]), ["Assets.lua", "asset-lock.json"])
                self.assertEqual(first["written"], first["drift"], "the first run creates both files")
                lock_bytes, lua_bytes = lock.read_bytes(), lua.read_bytes()
                lock_mtime, lua_mtime = lock.stat().st_mtime_ns, lua.stat().st_mtime_ns

                second = update_repo_asset_manifest()
                self.assertEqual(second["drift"], [], "a second run with no change must be a no-op")
                self.assertEqual(second["written"], [])
                self.assertEqual(lock.read_bytes(), lock_bytes, "asset-lock.json is byte-identical")
                self.assertEqual(lua.read_bytes(), lua_bytes, "Assets.lua is byte-identical")
                self.assertEqual(lock.stat().st_mtime_ns, lock_mtime, "an unchanged file must not be rewritten")
                self.assertEqual(lua.stat().st_mtime_ns, lua_mtime)
                self.assertGreaterEqual(second["count"], 2)
                # Two files that would alias on basename keep distinct keys.
                self.assertEqual(len(second["images"]), second["count"])

                # A new asset is drift, and `check=True` reports it without
                # touching either file — which is what CI needs.
                (kit / "assets" / "images" / "ui" / "icon_c.png").write_bytes(self._png(8, (20, 20, 220, 255)))
                probe = update_repo_asset_manifest(check=True)
                self.assertFalse(probe["ok"])
                self.assertEqual(sorted(probe["drift"]), ["Assets.lua", "asset-lock.json"])
                self.assertEqual(probe["written"], [], "check mode must not write")
                self.assertTrue(probe["checked"])
                self.assertEqual(lock.read_bytes(), lock_bytes, "check mode wrote to asset-lock.json")
                self.assertEqual(lua.read_bytes(), lua_bytes, "check mode wrote to Assets.lua")
                self.assertEqual(lock.stat().st_mtime_ns, lock_mtime)

                applied = update_repo_asset_manifest()
                self.assertEqual(sorted(applied["drift"]), ["Assets.lua", "asset-lock.json"])
                self.assertGreaterEqual(applied["count"], 3)
                self.assertTrue(update_repo_asset_manifest(check=True)["ok"],
                                "after a real run the tree and the manifest agree")

    def test_a_pathless_or_hostile_lock_row_cannot_break_regeneration(self):
        from image_pipeline import update_repo_asset_manifest
        with tempfile.TemporaryDirectory(prefix="arena-v61-manifest-") as tmp:
            kit = self._kit(tmp)
            (kit / "assets" / "images" / "ui" / "real.png").write_bytes(self._png())
            lock = kit / "assets" / "asset-lock.json"
            lock.write_text(json.dumps({
                "version": 1,
                "images": {
                    # A row written before `relPath` existed: this is the KeyError.
                    "legacy_row": {"key": "legacy_row", "bytes": 12, "width": 2, "height": 2},
                    # A row whose key and URI would break out of a Luau literal.
                    'bad"key': {"key": 'bad"key', "relPath": 'x", evil = "y',
                                "localUri": 'rbxasset://a"b'},
                },
                "audio": {},
            }, indent=2), encoding="utf-8")

            with mock.patch("image_pipeline.find_kit_root", return_value=kit):
                out = update_repo_asset_manifest()  # must not raise
                lua = (kit / "modules" / "Assets.lua").read_text(encoding="utf-8")
                self.assertIn("real =", lua)
                # A row with no usable URI is left out rather than emitted as "".
                self.assertNotIn("legacy_row", lua)
                # The injected Luau must not survive as code, and no raw quote may
                # break a literal open.
                self.assertNotIn('evil = "y"', lua)
                self.assertNotIn('bad"key', lua)
                for line in lua.splitlines():
                    body = line.strip()
                    if not body.endswith('",') and not body.endswith('"'):
                        continue  # a table row (`key = { ... }`), not a string literal
                    value = body.rstrip(",").split("= ", 1)[1]
                    self.assertTrue(len(value) >= 2 and value[0] == value[-1] == '"', body)
                    # The only quote allowed inside a Luau literal is an escaped one.
                    inner = value[1:-1]
                    self.assertNotIn('"', inner.replace(chr(92) + chr(34), ""), body)
                # The lock keeps the legacy row (it is data, not an error)…
                after = json.loads(lock.read_text(encoding="utf-8"))
                self.assertIn("legacy_row", after["images"])
                self.assertEqual(after["images"].get("legacy_row", {}).get("bytes"), 12)
                # …and a second run is still a no-op, so the odd row is stable.
                self.assertEqual(update_repo_asset_manifest()["drift"], [])
                self.assertEqual(out["count"], len(after["images"]))

    def test_the_committed_kit_manifest_matches_the_tree(self):
        """The reason `check=True` exists: a stale `asset-lock.json`/`Assets.lua`
        is a CI failure, not something an operator discovers in Studio. This also
        catches a test that writes into the real kit and forgets to clean up."""
        from image_pipeline import update_repo_asset_manifest
        result = update_repo_asset_manifest(check=True)
        self.assertTrue(result["checked"])
        self.assertEqual(result["drift"], [],
                         f"committed manifest drifted from the tree: {result['drift']} "
                         f"(run update_repo_asset_manifest() and commit, or remove the stray file)")
        self.assertGreaterEqual(result["count"], 3700)
        self.assertGreaterEqual(result["audioCount"], 45)


class TestV61SnapshotRestore(unittest.TestCase):
    """P1-B2 — `manage_snapshots(action='restore')` rebuilt every target by
    splitting the snapshot's dotted path, so a script whose *own name* contains a
    dot ("A.B", "Part 1.5", "UI.Shell") was written under an invented parent with
    a truncated name, and the bare loop reported nothing when a write failed
    mid-way. Restore now uses the parent/name the peer reported at snapshot time,
    collects a per-script report, and honours `stopOnError`/`dryRun`.
    """

    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls.token = "snap-restore-secret"
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.state_dir = tempfile.mkdtemp(prefix="arena-v61-snap-")
        cls.httpd, cls.state = create_http_server(
            host="127.0.0.1", port=cls.port, token=cls.token, state_dir=cls.state_dir)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.stop_mock = threading.Event()
        cls.mock_thread = threading.Thread(
            target=run_mock_loop, args=(cls.base_url, cls.token, cls.stop_mock), daemon=True)
        cls.mock_thread.start()
        for _ in range(80):
            if cls.state.direct_dispatch("health", {})["studio_connected"]:
                break
            threading.Event().wait(0.05)
        cls.client = BridgeClient(base_url=cls.base_url, token=cls.token)

    @classmethod
    def tearDownClass(cls):
        cls.stop_mock.set()
        cls.state._reaper_stop.set()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        shutil.rmtree(cls.state_dir, ignore_errors=True)

    def tool(self, name: str, args: dict) -> dict:
        return execute_tool(self.client, name, args)

    def live_scripts(self, root: str) -> dict:
        """`{full path: entry}` straight from the peer's own traversal — reading by
        dotted path would be as ambiguous as the bug under test."""
        job = self.client.submit_job("hash_scripts", {"root": root}, wait=30.0, dm="edit")
        self.assertEqual(job.get("status"), "done", msg=json.dumps(job)[:600])
        return {e["path"]: e for e in job["result"]["scripts"]}

    def test_restore_keeps_dotted_names_and_their_real_parents(self):
        built = self.tool("build_tree", {"parent": "ServerScriptService", "tree": [{
            "name": "DotCase", "className": "Folder", "replaceExisting": True, "children": [
                {"name": "A.B", "className": "ModuleScript", "source": "-- v1 dotted\nreturn 1\n"},
                {"name": "X", "className": "Folder", "children": [
                    {"name": "Part 1.5", "className": "ModuleScript", "source": "-- v1 spaced\nreturn 2\n"},
                ]},
            ],
        }]})
        self.assertEqual(built["count"], 1)

        snap = self.tool("manage_snapshots", {
            "action": "create", "root": "ServerScriptService.DotCase", "label": "dotcase"})
        sid = snap["id"]

        # 1. The snapshot itself records the name and parent the peer reported,
        #    which is the only unambiguous form of a dotted full name.
        got = self.tool("manage_snapshots", {"action": "get", "snapshotId": sid})
        by_path = {e["path"]: e for e in got["scripts"]}
        self.assertEqual(sorted(by_path), [
            "ServerScriptService.DotCase.A.B",
            "ServerScriptService.DotCase.X.Part 1.5",
        ])
        self.assertEqual(by_path["ServerScriptService.DotCase.A.B"]["name"], "A.B")
        self.assertEqual(by_path["ServerScriptService.DotCase.A.B"]["parent"], "ServerScriptService.DotCase")
        self.assertEqual(by_path["ServerScriptService.DotCase.X.Part 1.5"]["name"], "Part 1.5")
        self.assertEqual(by_path["ServerScriptService.DotCase.X.Part 1.5"]["parent"],
                         "ServerScriptService.DotCase.X")
        v1_hashes = {p: e["hash"] for p, e in by_path.items()}

        # 2. Break both scripts, then restore.
        for parent, name in (("ServerScriptService.DotCase", "A.B"),
                             ("ServerScriptService.DotCase.X", "Part 1.5")):
            self.tool("write_script", {"parent": parent, "name": name, "className": "ModuleScript",
                                       "source": "-- v2 broken\n", "validateSyntax": False})
        broken = self.live_scripts("ServerScriptService.DotCase")
        self.assertNotEqual(broken["ServerScriptService.DotCase.A.B"]["hash"],
                            v1_hashes["ServerScriptService.DotCase.A.B"])

        res = self.tool("manage_snapshots", {"action": "restore", "snapshotId": sid})
        self.assertTrue(res["ok"], msg=json.dumps(res)[:800])
        self.assertEqual(res["restoredCount"], 2)
        self.assertEqual(res["failedCount"], 0)
        self.assertEqual(res["notAttemptedCount"], 0)
        self.assertEqual([(r["parent"], r["name"], r["legacySplit"]) for r in res["results"]],
                         [("ServerScriptService.DotCase", "A.B", False),
                          ("ServerScriptService.DotCase.X", "Part 1.5", False)])

        # 3. Exact names, exact parents, v1 sources back — and no invented "A"/"B".
        after = self.live_scripts("ServerScriptService.DotCase")
        self.assertEqual(sorted(after), sorted(v1_hashes))
        self.assertEqual({p: e["hash"] for p, e in after.items()}, v1_hashes)
        self.assertEqual([e["name"] for e in after.values()], ["A.B", "Part 1.5"])
        diff = self.tool("manage_snapshots", {"action": "diff", "snapshotId": sid})
        self.assertEqual(diff["changes"], [], msg=json.dumps(diff)[:600])

    def test_a_mid_restore_failure_is_reported_and_stopOnError_halts(self):
        tree = [{"name": f"F{i}", "className": "Folder", "children": [
            {"name": f"S{i}", "className": "ModuleScript", "source": f"-- v1 S{i}\nreturn {i}\n"}]}
            for i in range(1, 6)]
        self.tool("build_tree", {"parent": "ServerScriptService", "tree": [
            {"name": "FailCase", "className": "Folder", "replaceExisting": True, "children": tree}]})
        sid = self.tool("manage_snapshots", {
            "action": "create", "root": "ServerScriptService.FailCase", "label": "failcase"})["id"]
        order = [e["path"] for e in self.tool("manage_snapshots", {"action": "get", "snapshotId": sid})["scripts"]]
        self.assertEqual(order, [f"ServerScriptService.FailCase.F{i}.S{i}" for i in range(1, 6)])

        # Item 3 of 5 cannot be written: its parent folder is gone.
        self.tool("delete_instance", {"paths": ["ServerScriptService.FailCase.F3"]})

        res = self.tool("manage_snapshots", {"action": "restore", "snapshotId": sid})
        self.assertFalse(res["ok"])
        self.assertEqual(res["restoredCount"], 2, msg=json.dumps(res)[:800])
        self.assertEqual(res["failedCount"], 1)
        self.assertEqual(res["notAttemptedCount"], 2)
        self.assertEqual(len(res["results"]), 3, "nothing after the failure was attempted")
        self.assertEqual([r["path"] for r in res["results"]], order[:3])
        self.assertFalse(res["results"][2]["ok"])
        self.assertIn("path not found", res["results"][2]["error"])
        self.assertEqual(res["failed"], [order[2]])
        self.assertEqual(res["notAttempted"], order[3:])
        self.assertTrue(any("stopOnError" in w for w in res["warnings"]), res["warnings"])
        live = self.live_scripts("ServerScriptService.FailCase")
        self.assertNotIn(order[2], live)
        self.assertIn(order[3], live, "S4 must be untouched, not half-written")

        # stopOnError=false attempts the rest and still names the failure.
        res_all = self.tool("manage_snapshots", {"action": "restore", "snapshotId": sid,
                                                 "stopOnError": False})
        self.assertEqual(res_all["restoredCount"], 4)
        self.assertEqual(res_all["failedCount"], 1)
        self.assertEqual(res_all["notAttemptedCount"], 0)
        self.assertEqual(len(res_all["results"]), 5)
        self.assertEqual([r["path"] for r in res_all["results"] if not r["ok"]], [order[2]])

    def test_dry_run_restore_plans_without_writing_and_legacy_entries_are_flagged(self):
        self.tool("build_tree", {"parent": "ServerScriptService", "tree": [
            {"name": "DryCase", "className": "Folder", "replaceExisting": True, "children": [
                {"name": "D1", "className": "ModuleScript", "source": "-- v1 dry\nreturn 1\n"}]}]})
        sid = self.tool("manage_snapshots", {
            "action": "create", "root": "ServerScriptService.DryCase", "label": "drycase"})["id"]
        self.tool("write_script", {"parent": "ServerScriptService.DryCase", "name": "D1",
                                   "className": "ModuleScript", "source": "-- v2 dry\n",
                                   "validateSyntax": False})
        v2 = self.live_scripts("ServerScriptService.DryCase")["ServerScriptService.DryCase.D1"]["hash"]

        dry = self.tool("manage_snapshots", {"action": "restore", "snapshotId": sid, "dryRun": True})
        self.assertTrue(dry["dryRun"])
        self.assertEqual(dry["restoredCount"], 1)
        self.assertEqual(dry["failedCount"], 0)
        self.assertTrue(all(r["dryRun"] and r["ok"] for r in dry["results"]))
        self.assertTrue(any("dryRun" in w for w in dry["warnings"]), dry["warnings"])
        still = self.live_scripts("ServerScriptService.DryCase")["ServerScriptService.DryCase.D1"]["hash"]
        self.assertEqual(still, v2, "dryRun wrote to Studio")

        # A snapshot written before 6.1.0 carries no parent/name: the dotted split
        # is still used, but the caller is told instead of finding out later.
        legacy = self.client.snapshots_action("save", {
            "root": "ServerScriptService.DryCase",
            "label": "legacy",
            "scripts": [{"path": "ServerScriptService.DryCase.D1", "className": "ModuleScript",
                         "hash": "00000000", "bytes": 12, "source": "-- v1 dry\nreturn 1\n"}],
        })
        res = self.tool("manage_snapshots", {"action": "restore", "snapshotId": legacy["id"]})
        self.assertTrue(res["ok"], msg=json.dumps(res)[:600])
        self.assertEqual(res["restoredCount"], 1)
        self.assertTrue(res["results"][0]["legacySplit"])
        self.assertEqual((res["results"][0]["parent"], res["results"][0]["name"]),
                         ("ServerScriptService.DryCase", "D1"))
        self.assertTrue(any("predate 6.1.0" in w for w in res["warnings"]), res["warnings"])
        back = self.live_scripts("ServerScriptService.DryCase")["ServerScriptService.DryCase.D1"]
        self.assertNotEqual(back["hash"], v2, "the legacy restore did not write")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if os.environ.get("ARENA_SELF_TEST_CHILD"):
        print("ARENA_TOOL_METRICS=" + json.dumps(TOOL_TEST_METRICS, sort_keys=True))
    raise SystemExit(0 if result.wasSuccessful() else 1)
