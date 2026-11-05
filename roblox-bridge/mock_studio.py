#!/usr/bin/env python3
"""
mock_studio.py — Stateful Roblox Studio v4.1.0 simulator for testing the bridge,
CLI, and all 68 registered MCP tools without Roblox Studio open.

Simulates:
  - Full DataModel hierarchy (Services, Parts, Models, Folders, Scripts, Remotes, GUI)
  - Editor-safe script CRUD, line-range reads, multi_edit, script_search, script_grep,
    find_replace_scripts, exact 32-bit FNV-1a hashes, and require_fresh
  - Attributes, CollectionService tags, selection, camera, terrain, safe insert_asset
  - ChangeHistoryService undo/redo stack across mutations
  - Live Playtest peers: calling playtest(start_play / start_run) spins up a simulated
    `dm=server` peer (plus `dm=client` RPC forwarding) until `stop` is called!

Run:
    python3 mock_studio.py
"""

import copy
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from mcp_core import fnv1a_32, fnv1a_hex, normalize_newlines

BASE = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8077").rstrip("/")
TOKEN = os.environ.get("BRIDGE_TOKEN", "arena-demo-7f3a")
#: The plugin version the mock claims. It must match `local PLUGIN_VERSION` in
#: ArenaBridge.lua: the mock stands in for that plugin, reports this as its
#: `pluginVersion` in every info result, and registers it as `pv` like the real
#: one does — which is what /api/peers and the studio strip display.
PLUGIN_VERSION = os.environ.get("MOCK_PLUGIN_VERSION", "4.1.0")

PLACE = os.environ.get("MOCK_PLACE", "DemoPlace (simulated Studio v%s)" % PLUGIN_VERSION)
# The engine build the mock pretends to be, reported by playtest_caps. Real
# Studio reports `version()`; the mock reports this so capability code paths
# can be exercised without a Studio install.
STUDIO_VERSION = os.environ.get("MOCK_STUDIO_VERSION", "0.721.0000-live-e17f (mock)")
DEVICE_BUILTIN_PREFIX = "builtin:"
INPUT_ACTIONS_LUA = {
    "keydown": "SendKey(true)", "keyup": "SendKey(false)", "press": "SendKey(true/false)",
    "type": "SendTextInput", "move": "SendMousePosition", "wheel": "SendMouseWheel",
}
PLACE_ID = int(os.environ.get("MOCK_PLACE_ID", "110457957229133"))

SCRIPT_CLASSES = {"Script", "LocalScript", "ModuleScript"}


class MockNode:
    def __init__(self, name: str, class_name: str, props=None, source: str = None, attrs=None, tags=None):
        self.name = name
        self.class_name = class_name
        self.props = dict(props or {})
        self.source = source
        self.attrs = dict(attrs or {})
        self.tags = list(tags or [])
        self.children = []
        self.parent = None

    def add(self, child: "MockNode") -> "MockNode":
        child.parent = self
        self.children.append(child)
        return child

    def find_child(self, name: str):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def full_name(self) -> str:
        if self.parent is None:
            return "game"
        parts = []
        cur = self
        while cur is not None and cur.parent is not None:
            parts.append(cur.name)
            cur = cur.parent
        parts.reverse()
        return ".".join(parts)

    def descendants(self):
        out = []
        stack = list(reversed(self.children))
        while stack:
            n = stack.pop()
            out.append(n)
            stack.extend(reversed(n.children))
        return out

    def clone_detached(self) -> "MockNode":
        cp = MockNode(
            self.name,
            self.class_name,
            props=copy.deepcopy(self.props),
            source=self.source,
            attrs=copy.deepcopy(self.attrs),
            tags=list(self.tags),
        )
        for ch in self.children:
            cp.add(ch.clone_detached())
        return cp


class MockWorld:
    def __init__(self, dm: str = "edit", peer_id: str = "edit-mock0001"):
        self.dm = dm
        self.peer_id = peer_id
        self.root = MockNode("game", "DataModel")
        self.selection = []
        self.camera = {
            "cframe": {"__t": "CFrame", "v": [0, 20, 30, 1, 0, 0, 0, 1, 0, 0, 0, 1]},
            "focus": {"__t": "CFrame", "v": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]},
            "fov": 70.0,
            "viewportSize": {"__t": "Vector2", "v": [1920, 1080]},
        }
        self.logs = [
            {"type": "Output", "message": "mock: Studio session initialized", "timestamp": time.time()},
            {"type": "Warning", "message": "mock: sample startup warning", "timestamp": time.time()},
        ]
        self.terrain_ops = []
        self.open_tabs = []
        self.undo_stack = []
        self.redo_stack = []
        self.play_peer_stop = None
        self.play_peer_thread = None
        #: True between `start_play`/`start_run` and `stop`. The peer thread only
        #: exists when the mock can open its own poll loop, so this flag is the
        #: fallback that keeps `isRunning` honest in single-world mode.
        self.playtest_running = False
        # StudioDeviceSimulatorService state (Edit mode only, like the real one).
        self.device_id = "default"
        self.device_orientation = "LandscapeRight"
        self.device_resolution = [1920, 1080]
        self.device_density = 96.0
        self.device_scaling = "FitToWindow"
        self.device_profiles = {}
        # simulate_input receipts, newest first, for assertions and probes.
        self.input_state = {"delivered": [], "focused": None}
        self.studio_test_runs = []
        self.device_catalog = ["iPhone15Pro", "iPhoneSE", "iPadPro12.9", "AndroidPhone", "SurfaceTop"]

        # Seed default services & sample content
        ws = self.root.add(MockNode("Workspace", "Workspace", {"Gravity": 196.2, "StreamingEnabled": False}))
        ws.add(MockNode("Baseplate", "Part", {"Size": [512, 16, 512], "Position": [0, -8, 0], "Anchored": True}, tags=["Ground"]))
        door = ws.add(MockNode("Door", "Model", attrs={"Locked": True}, tags=["Interactable"]))
        door.add(MockNode("Panel", "Part", {"Size": [4, 8, 0.5], "Position": [0, 4, -12], "Anchored": True}))

        sss = self.root.add(MockNode("ServerScriptService", "ServerScriptService"))
        sss.add(MockNode(
            "CheckpointService",
            "Script",
            source="-- Checkpoint server logic\nlocal Players = game:GetService('Players')\nprint('Ready')\n",
        ))
        pkg = sss.add(MockNode("GameSystems", "Folder"))
        pkg.add(MockNode(
            "Config",
            "ModuleScript",
            source="local Config = {\n\tDebugHud = true,\n\tMoveSpeed = 16.0,\n}\nreturn Config\n",
        ))

        rs = self.root.add(MockNode("ReplicatedStorage", "ReplicatedStorage"))
        ev = rs.add(MockNode("Events", "Folder"))
        ev.add(MockNode("CheckpointReached", "RemoteEvent"))

        for svc in ("Players", "Lighting", "ServerStorage", "StarterGui", "StarterPack", "StarterPlayer", "SoundService"):
            self.root.add(MockNode(svc, svc))
        sp_svc = self.resolve("StarterPlayer")
        sp_svc.add(MockNode("StarterPlayerScripts", "StarterPlayerScripts"))
        sp_svc.add(MockNode("StarterCharacterScripts", "StarterCharacterScripts"))
        self.resolve("Lighting").props.update({"ClockTime": 14.0, "Ambient": [0.5, 0.5, 0.5]})

    def snapshot_tree(self):
        return self.root.clone_detached()

    def record_undo(self):
        if self.dm == "edit":
            self.undo_stack.append(self.snapshot_tree())
            if len(self.undo_stack) > 25:
                self.undo_stack.pop(0)
            self.redo_stack.clear()

    def resolve(self, path: str):
        if not path or path == "game":
            return self.root
        clean = path.replace("/", ".")
        parts = [p for p in clean.split(".") if p]
        cur = self.root
        for idx, seg in enumerate(parts):
            if idx == 0 and seg == "game":
                continue
            if cur is self.root and seg == "workspace":
                seg = "Workspace"
            nxt = cur.find_child(seg)
            if nxt is None and cur is self.root:
                # Auto-create requested service like game:GetService()
                nxt = cur.add(MockNode(seg, seg))
            if nxt is None:
                return None
            cur = nxt
        return cur

    def must_resolve(self, path: str) -> MockNode:
        inst = self.resolve(path)
        if inst is None:
            raise RuntimeError(f"path not found: {path}")
        return inst

    def describe_node(self, node: MockNode, depth: int, desc_summary: bool = False) -> dict:
        out = {
            "name": node.name,
            "className": node.class_name,
            "path": node.full_name(),
            "childCount": len(node.children),
        }
        if node.props:
            out["properties"] = {k: str(v) for k, v in node.props.items()}
            out["structuredProperties"] = copy.deepcopy(node.props)
        if node.attrs:
            out["attributes"] = copy.deepcopy(node.attrs)
        if node.tags:
            out["tags"] = list(node.tags)
        if node.class_name in SCRIPT_CLASSES:
            src = node.source or ""
            out["sourceLines"] = src.count("\n") + 1
            out["sourceBytes"] = len(src.encode("utf-8"))
            out["hash"] = fnv1a_hex(src)
        if depth > 0 and node.children:
            out["children"] = [self.describe_node(c, depth - 1, False) for c in node.children[:100]]
        if desc_summary:
            counts = {}
            all_d = node.descendants()
            for d in all_d:
                counts[d.class_name] = counts.get(d.class_name, 0) + 1
            out["totalDescendants"] = len(all_d)
            out["descendantClasses"] = counts
        return out

    def build_spec(self, spec: dict, parent: MockNode) -> MockNode:
        cls = spec.get("className") or spec.get("class") or "Part"
        name = spec.get("name") or cls
        if spec.get("replaceExisting"):
            ex = parent.find_child(name)
            if ex:
                parent.children.remove(ex)
        node = MockNode(
            name=name,
            class_name=cls,
            props=spec.get("properties") or {},
            source=normalize_newlines(spec["source"]) if spec.get("source") is not None else ("-- script\n" if cls in SCRIPT_CLASSES else None),
            attrs=spec.get("attributes") or {},
            tags=spec.get("tags") or [],
        )
        parent.add(node)
        for ch_spec in spec.get("children") or []:
            self.build_spec(ch_spec, node)
        return node

    def execute(self, job: dict, bridge_req_fn=None) -> dict:
        kind = job["type"]
        p = job.get("payload") or {}
        effective_dm = "client" if p.get("_targetDm") == "client" else self.dm

        mutating = kind in {
            "build", "write_script", "batch_write", "multi_edit", "set_properties",
            "mass_set_properties", "clone_instance", "reparent_instance", "delete",
            "terrain", "insert_asset", "import_image",
        } or (kind == "find_replace_scripts" and not p.get("dryRun")) \
          or (kind == "attributes" and p.get("action") in ("set", "delete")) \
          or (kind == "tags" and p.get("action") in ("add", "remove"))

        if mutating:
            self.record_undo()

        if kind == "ping":
            return {
                "place": PLACE,
                "placeId": PLACE_ID,
                "gameId": 987654,
                "studio": "mock-3.0",
                "pluginVersion": PLUGIN_VERSION,
                "dm": effective_dm,
                "peerId": self.peer_id,
                "hasClient": self.dm == "server",
                "playerCount": 1 if self.dm == "server" else 0,
                "canUndo": bool(self.undo_stack),
                "canRedo": bool(self.redo_stack),
            }

        if kind == "studio_state":
            return {
                "place": {"name": PLACE, "placeId": PLACE_ID, "gameId": 987654},
                "studio": "mock-3.0",
                "pluginVersion": PLUGIN_VERSION,
                "dm": effective_dm,
                "peerId": self.peer_id,
                "isEdit": effective_dm == "edit",
                "isRunning": effective_dm != "edit" or self.playtest_running
                             or self.play_peer_stop is not None,
                "playtestActive": self.playtest_running or self.play_peer_stop is not None,
                "players": [{"name": "Player1", "displayName": "Player1", "userId": 1, "hasCharacter": True}] if effective_dm != "edit" else [],
                "selectionCount": len(self.selection),
                "canUndo": bool(self.undo_stack),
                "canRedo": bool(self.redo_stack),
                "memoryMb": 412.5,
                "physicsFps": 60.0,
                "camera": self.camera,
            }

        if kind == "mesh_capabilities":
            return {
                "ok": True,
                "studioVersion": "mock-3.0",
                "peerId": self.peer_id,
                "meshPartClass": {"available": True},
                "editableMesh": {
                    "apiPresent": False,
                    "creationAttempted": False,
                    "creationSucceeded": None,
                    "status": "not_probed_by_mock",
                    "detail": "Mock peer does not verify Roblox Studio runtime APIs.",
                },
                "verifiedAtUnix": 0,
                "warnings": ["Mock result only; no Studio capability is claimed."],
            }

        if kind == "survey":
            all_nodes = self.root.descendants()
            census = {}
            scripts_out = []
            remotes_out = []
            tags_map = {}
            for n in all_nodes:
                census[n.class_name] = census.get(n.class_name, 0) + 1
                for t in n.tags:
                    tags_map[t] = tags_map.get(t, 0) + 1
                if n.class_name in SCRIPT_CLASSES:
                    src = n.source or ""
                    entry = {
                        "path": n.full_name(),
                        "className": n.class_name,
                        "bytes": len(src.encode("utf-8")),
                        "lines": src.count("\n") + 1,
                        "hash": fnv1a_hex(src),
                    }
                    if p.get("includeSource", True):
                        entry["source"] = src[: p.get("maxBytesPerScript", 14000)]
                    scripts_out.append(entry)
                elif "Remote" in n.class_name or "Bindable" in n.class_name:
                    remotes_out.append({"path": n.full_name(), "className": n.class_name})
            return {
                "place": {"name": PLACE, "placeId": PLACE_ID, "gameId": 987654},
                "dm": effective_dm,
                "pluginVersion": PLUGIN_VERSION,
                "services": [{"name": c.name, "descendants": len(c.descendants()), "children": len(c.children)} for c in self.root.children],
                "census": census,
                "scripts": scripts_out,
                "remotes": remotes_out,
                "workspaceTopLevel": [{"name": c.name, "className": c.class_name, "parts": 1} for c in self.resolve("Workspace").children],
                "gui": [],
                "tags": tags_map,
                "settings": {"gravity": "196.2", "streamingEnabled": "false", "clockTime": "14", "technology": "Future", "hasTerrain": True},
                "totals": {
                    "instancesVisited": len(all_nodes),
                    "scripts": len(scripts_out),
                    "sourceBytesReturned": sum(len(s.get("source", "")) for s in scripts_out),
                    "scriptsWithheld": 0,
                    "visitCapHit": False,
                },
            }

        if kind == "inspect":
            node = self.must_resolve(p.get("path", "game"))
            return self.describe_node(node, int(p.get("depth", 2)), bool(p.get("descendantSummary")))

        if kind == "search_tree":
            root_node = self.must_resolve(p.get("root", "game"))
            q = (p.get("query") or "").lower()
            cls = p.get("className")
            is_a = p.get("isA")
            tag = p.get("tag")
            attr = p.get("attribute")
            limit = int(p.get("limit", 60))
            matches = []
            for n in root_node.descendants():
                if cls and n.class_name != cls:
                    continue
                if is_a:
                    if is_a == "LuaSourceContainer" and n.class_name not in SCRIPT_CLASSES:
                        continue
                    elif is_a == "BasePart" and "Part" not in n.class_name:
                        continue
                    elif is_a not in ("LuaSourceContainer", "BasePart") and n.class_name != is_a:
                        continue
                if q and q not in n.name.lower() and q not in n.class_name.lower():
                    continue
                if tag and tag not in n.tags:
                    continue
                if attr and attr not in n.attrs:
                    continue
                matches.append({
                    "name": n.name,
                    "className": n.class_name,
                    "path": n.full_name(),
                    "childCount": len(n.children),
                })
                if len(matches) >= limit:
                    break
            return {"matches": matches, "count": len(matches), "visited": len(root_node.descendants()), "limitHit": len(matches) >= limit}

        if kind == "compare_instances":
            a = self.must_resolve(p["pathA"])
            b = self.must_resolve(p["pathB"])
            pdiffs = [
                {"property": k, "a": str(a.props.get(k)), "b": str(b.props.get(k))}
                for k in sorted(set(a.props) | set(b.props))
                if a.props.get(k) != b.props.get(k)
            ]
            scomp = None
            if a.class_name in SCRIPT_CLASSES and b.class_name in SCRIPT_CLASSES:
                sa, sb = a.source or "", b.source or ""
                scomp = {
                    "hashA": fnv1a_hex(sa),
                    "hashB": fnv1a_hex(sb),
                    "bytesA": len(sa.encode("utf-8")),
                    "bytesB": len(sb.encode("utf-8")),
                    "identical": sa == sb,
                }
            return {
                "pathA": a.full_name(),
                "pathB": b.full_name(),
                "classA": a.class_name,
                "classB": b.class_name,
                "sameClass": a.class_name == b.class_name,
                "propertyDiffs": pdiffs,
                "attributeDiffs": [],
                "scriptComparison": scomp,
                "childrenOnlyInA": [c.name for c in a.children if not b.find_child(c.name)],
                "childrenOnlyInB": [c.name for c in b.children if not a.find_child(c.name)],
            }

        if kind == "get_properties":
            out = []
            for path in p.get("paths") or []:
                n = self.resolve(path)
                if n:
                    out.append({
                        "path": n.full_name(),
                        "name": n.name,
                        "className": n.class_name,
                        "properties": {k: str(v) for k, v in n.props.items()},
                        "structured": copy.deepcopy(n.props),
                    })
                else:
                    out.append({"path": path, "error": "not found"})
            return {"instances": out, "count": len(out)}

        if kind == "set_properties":
            n = self.must_resolve(p["path"])
            applied = []
            for k, v in (p.get("properties") or {}).items():
                n.props[k] = v
                applied.append(k)
            return {"path": n.full_name(), "applied": applied}

        if kind == "mass_set_properties":
            updated = []
            for path in p.get("paths") or []:
                n = self.resolve(path)
                if n:
                    applied = []
                    for k, v in (p.get("properties") or {}).items():
                        n.props[k] = v
                        applied.append(k)
                    for k, delta in (p.get("relative") or {}).items():
                        cur = n.props.get(k, [0, 0, 0] if isinstance(delta, list) else 0)
                        if isinstance(cur, list) and isinstance(delta, list):
                            n.props[k] = [a + b for a, b in zip(cur, delta)]
                        else:
                            n.props[k] = cur + delta
                        applied.append(k)
                    updated.append({"path": n.full_name(), "applied": applied})
            return {"updated": updated, "count": len(updated)}

        if kind == "read_script":
            n = self.must_resolve(p["path"])
            if n.class_name not in SCRIPT_CLASSES:
                raise RuntimeError(f"not a script: {n.full_name()}")
            src = n.source or ""
            lines = src.split("\n")
            s_ln = max(1, int(p.get("startLine") or 1))
            e_ln = min(len(lines), int(p.get("endLine") or len(lines)))
            sliced = lines[s_ln - 1 : e_ln]
            if p.get("withLineNumbers"):
                sliced = [f"{i:4d} | {txt}" for i, txt in enumerate(sliced, start=s_ln)]
            return {
                "path": n.full_name(),
                "className": n.class_name,
                "bytes": len(src.encode("utf-8")),
                "totalLines": len(lines),
                "startLine": s_ln,
                "endLine": e_ln,
                "hash": fnv1a_hex(src),
                "source": "\n".join(sliced),
            }

        if kind == "read_scripts":
            out = []
            spent = 0
            for path in p.get("paths") or []:
                n = self.resolve(path)
                if n and n.class_name in SCRIPT_CLASSES:
                    src = n.source or ""
                    spent += len(src)
                    out.append({
                        "path": n.full_name(),
                        "className": n.class_name,
                        "bytes": len(src.encode("utf-8")),
                        "lines": src.count("\n") + 1,
                        "hash": fnv1a_hex(src),
                        "source": src,
                        "truncated": False,
                    })
                else:
                    out.append({"path": path, "error": "not found or not a script"})
            return {"scripts": out, "bytesReturned": spent}

        if kind == "write_script":
            parent = self.must_resolve(p.get("parent", "ServerScriptService"))
            name = p.get("name", "ArenaScript")
            cls = p.get("className", "Script")
            clean = normalize_newlines(p.get("source", ""))
            existing = parent.find_child(name)
            prev_hash = None
            if existing and existing.class_name in SCRIPT_CLASSES:
                prev_hash = fnv1a_hex(existing.source or "")
                if p.get("expectedHash") and p["expectedHash"] != prev_hash:
                    raise RuntimeError(f"hash mismatch on {existing.full_name()}: expected {p['expectedHash']}, live has {prev_hash}")
                existing.source = clean
                existing.class_name = cls
                target = existing
            else:
                if existing:
                    parent.children.remove(existing)
                target = parent.add(MockNode(name, cls, source=clean))
            return {
                "path": target.full_name(),
                "bytes": len(clean.encode("utf-8")),
                "lines": clean.count("\n") + 1,
                "previousHash": prev_hash,
                "hash": fnv1a_hex(clean),
                "replacedInstance": bool(p.get("replaceInstance")),
            }

        if kind == "batch_write":
            backup = self.snapshot_tree()
            written = []
            try:
                for spec in p.get("files") or []:
                    parent = self.must_resolve(spec.get("parent", "ServerScriptService"))
                    name = spec["name"]
                    cls = spec.get("className", "ModuleScript")
                    src = normalize_newlines(spec["source"])
                    old = parent.find_child(name)
                    if old and spec.get("expectedHash") and fnv1a_hex(old.source or "") != spec["expectedHash"]:
                        raise RuntimeError(f"hash mismatch on {old.full_name()}")
                    if old: parent.children.remove(old)
                    target = parent.add(MockNode(name, cls, source=src))
                    written.append({"path": target.full_name(), "hash": fnv1a_hex(src), "bytes": len(src.encode("utf-8"))})
            except Exception:
                self.root = backup
                raise
            return {"ok": True, "written": written, "count": len(written)}

        if kind == "multi_edit":
            target = self.resolve(p["path"])
            if not target:
                if p.get("createIfMissing"):
                    parts = p["path"].replace("/", ".").split(".")
                    sname = parts.pop()
                    parent = self.must_resolve(".".join(parts) or "ServerScriptService")
                    target = parent.add(MockNode(sname, p.get("className", "ModuleScript"), source=""))
                else:
                    raise RuntimeError(f"script not found: {p['path']}")
            working = normalize_newlines(target.source or "")
            prev_hash = fnv1a_hex(working)
            if p.get("expectedHash") and p["expectedHash"] != prev_hash:
                raise RuntimeError(f"hash mismatch on {target.full_name()}: expected {p['expectedHash']}, live has {prev_hash}")
            applied = 0
            for idx, ed in enumerate(p.get("edits") or [], start=1):
                if ed.get("startLine") is not None and ed.get("endLine") is not None:
                    lines = working.split("\n")
                    sl = max(1, int(ed["startLine"]))
                    el = int(ed["endLine"])
                    rebuilt = lines[: sl - 1] + normalize_newlines(ed.get("newText", "")).split("\n") + lines[el:]
                    working = "\n".join(rebuilt)
                    applied += 1
                else:
                    needle = normalize_newlines(ed.get("oldText", ""))
                    repl = normalize_newlines(ed.get("newText", ""))
                    cnt = working.count(needle)
                    if cnt == 0:
                        raise RuntimeError(f"edit #{idx} oldText not found in {target.full_name()}")
                    if cnt > 1 and not ed.get("replaceAll"):
                        raise RuntimeError(f"edit #{idx} oldText is ambiguous in {target.full_name()}")
                    working = working.replace(needle, repl) if ed.get("replaceAll") else working.replace(needle, repl, 1)
                    applied += cnt if ed.get("replaceAll") else 1
            target.source = working
            return {
                "path": target.full_name(),
                "editsApplied": applied,
                "bytes": len(working.encode("utf-8")),
                "lines": working.count("\n") + 1,
                "previousHash": prev_hash,
                "hash": fnv1a_hex(working),
                "replacedInstance": bool(p.get("replaceInstance")),
            }

        if kind == "hash_scripts":
            root_node = self.must_resolve(p.get("root") or "game")
            nodes = ([root_node] if root_node.class_name in SCRIPT_CLASSES else []) + [
                d for d in root_node.descendants() if d.class_name in SCRIPT_CLASSES
            ]
            entries = sorted(
                [
                    {
                        "path": n.full_name(),
                        "name": n.name,
                        "className": n.class_name,
                        "bytes": len((n.source or "").encode("utf-8")),
                        "lines": (n.source or "").count("\n") + 1,
                        "hash": fnv1a_hex(n.source or ""),
                        "hashInt": fnv1a_32(n.source or ""),
                    }
                    for n in nodes
                ],
                key=lambda x: x["path"],
            )
            return {"root": p.get("root") or "game", "count": len(entries), "scripts": entries}

        if kind == "script_search":
            root_node = self.must_resolve(p.get("root") or "game")
            q = (p.get("query") or "").lower()
            matches = []
            for n in root_node.descendants():
                if n.class_name in SCRIPT_CLASSES:
                    full_p = n.full_name()
                    if not q or q in n.name.lower() or q in full_p.lower():
                        src = n.source or ""
                        matches.append({
                            "score": 100 if n.name.lower() == q else 60,
                            "path": full_p,
                            "name": n.name,
                            "className": n.class_name,
                            "lines": src.count("\n") + 1,
                            "bytes": len(src.encode("utf-8")),
                            "hash": fnv1a_hex(src),
                        })
            return {"matches": matches[: int(p.get("limit", 25))], "totalMatched": len(matches)}

        if kind == "script_grep":
            root_node = self.must_resolve(p.get("root") or "game")
            pat = p["pattern"]
            cs = p.get("caseSensitive", True)
            needle = pat if cs else pat.lower()
            ctx_n = int(p.get("contextLines", 1))
            hits = []
            scanned = 0
            for n in root_node.descendants():
                if n.class_name in SCRIPT_CLASSES:
                    scanned += 1
                    lines = (n.source or "").split("\n")
                    for idx, lt in enumerate(lines, start=1):
                        hay = lt if cs else lt.lower()
                        if needle in hay:
                            c_start = max(1, idx - ctx_n)
                            c_end = min(len(lines), idx + ctx_n)
                            hits.append({
                                "path": n.full_name(),
                                "className": n.class_name,
                                "line": idx,
                                "text": lt,
                                "context": [{"line": ci, "text": lines[ci - 1]} for ci in range(c_start, c_end + 1)],
                            })
            return {"pattern": pat, "matches": hits[: int(p.get("maxMatches", 50))], "count": len(hits), "truncated": False, "scriptsScanned": scanned}

        if kind == "find_replace_scripts":
            root_node = self.must_resolve(p.get("root") or "game")
            find_s = normalize_newlines(p["find"])
            repl_s = normalize_newlines(p["replace"])
            dry = bool(p.get("dryRun"))
            affected = []
            total = 0
            for n in root_node.descendants():
                if n.class_name in SCRIPT_CLASSES:
                    src = normalize_newlines(n.source or "")
                    cnt = src.count(find_s)
                    if cnt > 0:
                        updated = src.replace(find_s, repl_s)
                        if not dry:
                            n.source = updated
                        total += cnt
                        affected.append({
                            "path": n.full_name(),
                            "replacements": cnt,
                            "previousHash": fnv1a_hex(src),
                            "newHash": fnv1a_hex(updated),
                        })
            return {"dryRun": dry, "scriptsModified": len(affected), "totalReplacements": total, "files": affected}

        if kind == "open_script":
            if p.get("listOnly"):
                return {"openScripts": self.open_tabs, "count": len(self.open_tabs)}
            n = self.must_resolve(p["path"])
            entry = {"name": n.name, "path": n.full_name(), "lineCount": (n.source or "").count("\n") + 1}
            if entry not in self.open_tabs:
                self.open_tabs.append(entry)
            return {"opened": n.full_name(), "line": p.get("line", 1)}

        if kind == "require_fresh":
            n = self.must_resolve(p["path"])
            if n.class_name != "ModuleScript":
                raise RuntimeError(f"not a ModuleScript: {n.full_name()}")
            return {
                "path": n.full_name(),
                "packageRoot": n.parent.full_name() if n.parent else "game",
                "returned": json.dumps({"freshHash": fnv1a_hex(n.source or ""), "bytes": len(n.source or "")}),
                "value": {"freshHash": fnv1a_hex(n.source or ""), "bytes": len(n.source or "")},
            }

        if kind == "run_luau":
            code = p.get("code", "")
            return {
                "returned": json.dumps({"ok": True, "dm": effective_dm, "lines": code.count("\n") + 1}),
                "value": {"ok": True, "dm": effective_dm, "lines": code.count("\n") + 1},
                "dm": effective_dm,
            }

        if kind == "build":
            parent = self.must_resolve(p.get("parent", "Workspace"))
            created = []
            for spec in p.get("tree") or [p]:
                n = self.build_spec(spec, parent)
                created.append(n.full_name())
            return {"created": created, "count": len(created)}

        if kind == "clone_instance":
            src_node = self.must_resolve(p["path"])
            parent = self.must_resolve(p["parent"]) if p.get("parent") else src_node.parent
            count = int(p.get("count", 1))
            offset = p.get("offset")
            created = []
            for i in range(1, count + 1):
                cp = src_node.clone_detached()
                if p.get("newName"):
                    cp.name = p["newName"].replace("{i}", str(i)) if count > 1 and "{i}" in p["newName"] else (
                        f"{p['newName']}_{i}" if count > 1 else p["newName"]
                    )
                if offset and "Position" in cp.props:
                    pos = cp.props["Position"]
                    cp.props["Position"] = [pos[0] + offset[0] * i, pos[1] + offset[1] * i, pos[2] + offset[2] * i]
                parent.add(cp)
                created.append(cp.full_name())
            return {"created": created, "count": len(created)}

        if kind == "scatter_props":
            source = self.must_resolve(p["sourcePath"])
            if source.class_name not in {"Part", "MeshPart", "WedgePart", "CornerWedgePart", "UnionOperation", "Model"}:
                raise ValueError("scatter_props source must be a BasePart or Model")
            parent = self.must_resolve(p.get("parent", "Workspace"))
            made = []
            paths = []
            strip_scripts = p.get("stripScripts", True)
            for idx, point in enumerate(p.get("points") or [], start=1):
                cp = source.clone_detached()
                cp.name = f"{p.get('namePrefix', 'Scatter')}_{idx:03d}"
                if len(point) < 4:
                    raise ValueError(f"invalid scatter point at index {idx}")
                cp.props["Position"] = [float(x) for x in point[:3]]
                cp.props["Orientation"] = [0, float(point[3]), 0]
                if strip_scripts:
                    def prune(node):
                        kept = []
                        for ch in node.children:
                            if ch.class_name in SCRIPT_CLASSES:
                                continue
                            prune(ch)
                            kept.append(ch)
                        node.children = kept
                    prune(cp)
                parent.add(cp)
                made.append(cp)
                paths.append(cp.full_name())
            return {"ok": True, "created": paths, "count": len(paths), "scriptsStripped": bool(strip_scripts), "warnings": []}

        if kind == "reparent_instance":
            moved = []
            new_parent = self.must_resolve(p["newParent"]) if p.get("newParent") else None
            paths = p.get("paths") or ([p["path"]] if p.get("path") else [])
            for path in paths:
                inst = self.must_resolve(path)
                if p.get("newName") and len(paths) == 1:
                    inst.name = p["newName"]
                if new_parent and inst.parent:
                    inst.parent.children.remove(inst)
                    new_parent.add(inst)
                moved.append(inst.full_name())
            return {"moved": moved, "count": len(moved)}

        if kind == "delete":
            targets = p.get("paths") or ([p["path"]] if p.get("path") else [])
            deleted, missing = [], []
            for path in targets:
                inst = self.resolve(path)
                if not inst:
                    missing.append(path)
                    continue
                full_p = inst.full_name()
                if inst.parent: inst.parent.children.remove(inst)
                deleted.append(full_p)
            return {"ok": len(missing) == 0, "deleted": deleted, "paths": deleted, "missing": missing, "count": len(deleted)}

        if kind == "attributes":
            act = p.get("action", "get")
            res = []
            for path in p.get("paths") or ([p["path"]] if p.get("path") else []):
                inst = self.must_resolve(path)
                if act == "set":
                    inst.attrs.update(p.get("attributes") or {})
                elif act == "delete":
                    for k in p.get("keys") or []:
                        inst.attrs.pop(k, None)
                res.append({"path": inst.full_name(), "attributes": copy.deepcopy(inst.attrs)})
            return {"results": res, "count": len(res)}

        if kind == "tags":
            act = p.get("action", "list_all")
            if act == "list_all":
                all_tags = {}
                for n in self.root.descendants():
                    for t in n.tags:
                        all_tags[t] = all_tags.get(t, 0) + 1
                return {"tags": all_tags}
            if act == "find_tagged":
                tag = p.get("tag", "")
                matched = [
                    {"path": n.full_name(), "className": n.class_name, "name": n.name}
                    for n in self.root.descendants() if tag in n.tags
                ]
                return {"tag": tag, "instances": matched, "total": len(matched)}
            tags_in = p.get("tags") or ([p["tag"]] if p.get("tag") else [])
            res = []
            for path in p.get("paths") or []:
                inst = self.must_resolve(path)
                if act == "add":
                    for t in tags_in:
                        if t not in inst.tags:
                            inst.tags.append(t)
                elif act == "remove":
                    inst.tags = [t for t in inst.tags if t not in tags_in]
                res.append({"path": inst.full_name(), "tags": list(inst.tags)})
            return {"results": res}

        if kind == "selection":
            act = p.get("action", "get")
            if act == "clear":
                self.selection = []
            elif act == "set":
                self.selection = [self.resolve(x) for x in (p.get("paths") or []) if self.resolve(x)]
            elif act == "add":
                for x in p.get("paths") or []:
                    inst = self.resolve(x)
                    if inst and inst not in self.selection:
                        self.selection.append(inst)
            return {
                "selection": [{"path": s.full_name(), "name": s.name, "className": s.class_name} for s in self.selection],
                "count": len(self.selection),
            }

        if kind == "camera":
            act = p.get("action", "get")
            if act in ("focus", "set"):
                if p.get("fov"):
                    self.camera["fov"] = float(p["fov"])
                if p.get("position"):
                    pos = p["position"]
                    self.camera["cframe"] = {"__t": "CFrame", "v": [pos[0], pos[1], pos[2], 1, 0, 0, 0, 1, 0, 0, 0, 1]}
            return copy.deepcopy(self.camera)

        if kind == "history":
            act = p.get("action", "status")
            if act == "undo" and self.undo_stack:
                self.redo_stack.append(self.snapshot_tree())
                self.root = self.undo_stack.pop()
            elif act == "redo" and self.redo_stack:
                self.undo_stack.append(self.snapshot_tree())
                self.root = self.redo_stack.pop()
            return {
                "action": act,
                "canUndo": bool(self.undo_stack),
                "canRedo": bool(self.redo_stack),
            }

        if kind == "terrain":
            act = p.get("action", "materials")
            if act == "materials":
                return {"materials": ["Air", "Grass", "Ground", "Mud", "Rock", "Sand", "Slate", "Water"], "count": 8}
            if act == "clear" and not p.get("region") and not p.get("confirmClearAll"):
                raise RuntimeError("set confirmClearAll=true to wipe all Workspace.Terrain")
            self.terrain_ops.append(copy.deepcopy(p))
            return {"action": act, "material": p.get("material", "Grass")}

        if kind == "insert_asset":
            parent = self.must_resolve(p.get("parent", "Workspace"))
            strip = p.get("stripScripts", True)
            model = parent.add(MockNode(p.get("name") or f"Asset_{p['assetId']}", "Model"))
            model.add(MockNode("Mesh", "Part"))
            embedded = [{"name": "SuspiciousLoader", "className": "Script", "bytes": 96, "stripped": strip}]
            if not strip:
                model.add(MockNode("SuspiciousLoader", "Script", source="require(12345)()"))
            return {
                "assetId": p["assetId"],
                "inserted": [model.full_name()],
                "embeddedScriptsFound": 1,
                "scriptsStripped": strip,
                "embeddedScripts": embedded,
            }

        if kind == "import_image":
            final_uri = p.get("uri") or ""
            editable_created = bool(p.get("rgbaBase64"))
            pub_id = 777000111 if (editable_created and p.get("publishStudioAsset")) else None
            if pub_id:
                final_uri = f"rbxassetid://{pub_id}"
            applied_to = None
            if p.get("targetPath"):
                target = self.must_resolve(p["targetPath"])
                mode = p.get("applyMode", "auto")
                inst_name = p.get("instanceName") or "ImportedImage"
                if mode == "billboard":
                    bb = target.add(MockNode(inst_name, "BillboardGui"))
                    lbl = bb.add(MockNode("Icon", "ImageLabel", {"Image": final_uri}))
                    applied_to = lbl.full_name()
                elif mode == "surface_gui":
                    sg = target.add(MockNode(inst_name, "SurfaceGui"))
                    lbl = sg.add(MockNode("Poster", "ImageLabel", {"Image": final_uri}))
                    applied_to = lbl.full_name()
                elif mode == "screen_gui" or (mode == "auto" and target.name == "StarterGui"):
                    gui = target.find_child("ImportedImagesGui") or target.add(MockNode("ImportedImagesGui", "ScreenGui"))
                    lbl = gui.add(MockNode(inst_name, "ImageLabel", {"Image": final_uri}))
                    applied_to = lbl.full_name()
                elif mode in ("decal", "texture") or (mode == "auto" and "Part" in target.class_name):
                    cls_t = "Texture" if mode == "texture" else "Decal"
                    dec = target.add(MockNode(inst_name, cls_t, {"Texture": final_uri, "Face": p.get("face", "Front")}))
                    applied_to = dec.full_name()
                else:
                    target.props["Image"] = final_uri
                    applied_to = target.full_name()
            return {
                "uri": final_uri,
                "editableCreated": editable_created,
                "publishedAssetId": pub_id,
                "appliedTo": applied_to,
            }

        if kind == "playtest":
            act = p.get("action", "status")
            if act in ("start_play", "start_run"):
                self.playtest_running = True
                if self.play_peer_stop is None and bridge_req_fn is not None:
                    stop_evt = threading.Event()
                    self.play_peer_stop = stop_evt
                    play_world = MockWorld(dm="server", peer_id="server-mockplay")
                    play_world.root = self.snapshot_tree()
                    # This world *is* the play session, so jobs it claims (a
                    # `dm="client"` input job, for instance) are live by definition.
                    play_world.playtest_running = True

                    def _run_play_peer():
                        while not stop_evt.is_set():
                            try:
                                qs = urllib.parse.urlencode({
                                    "place": PLACE,
                                    "placeId": PLACE_ID,
                                    "client": "mock",
                                    "peer": play_world.peer_id,
                                    "dm": "server",
                                    "hasClient": "1" if act == "start_play" else "0",
                                    "players": "1" if act == "start_play" else "0",
                                    "pv": PLUGIN_VERSION,
                                })
                                out = bridge_req_fn("GET", f"/api/poll?{qs}", timeout=3.0)
                                j = out.get("job")
                                if not j:
                                    continue
                                if j["type"] == "playtest" and j.get("payload", {}).get("action") == "stop":
                                    stop_evt.set()
                                    self.play_peer_stop = None
                                    self.playtest_running = False
                                    bridge_req_fn("POST", "/api/result", {
                                        "id": j["id"], "ok": True,
                                        "result": {"stopped": True, "detail": "EndTest called", "dm": "server"},
                                        "peerId": play_world.peer_id, "dm": "server",
                                    })
                                    break
                                res = play_world.execute(j)
                                bridge_req_fn("POST", "/api/result", {
                                    "id": j["id"], "ok": True, "result": res,
                                    "logs": [f"mock play-server executed {j['type']}"],
                                    "peerId": play_world.peer_id, "dm": "server",
                                })
                            except Exception:
                                time.sleep(0.1)

                    t = threading.Thread(target=_run_play_peer, daemon=True)
                    t.start()
                    self.play_peer_thread = t
                return {"started": True, "mode": "play" if act == "start_play" else "run", "dm": self.dm}
            if act == "stop":
                if self.play_peer_stop is not None:
                    self.play_peer_stop.set()
                    self.play_peer_stop = None
                self.play_peer_thread = None
                self.playtest_running = False
                return {"stopped": True, "detail": "EndTest called", "dm": self.dm}
            running = self.playtest_running or self.play_peer_stop is not None
            return {"dm": self.dm, "isRunning": running, "playtestActive": running}

        if kind == "console":
            since = int(p.get("sinceIndex") or 0)
            lvl = (p.get("level") or "all").lower()
            srch = (p.get("search") or "").lower()
            matched = []
            for idx, entry in enumerate(self.logs[since:], start=since + 1):
                if lvl != "all" and lvl not in entry["type"].lower():
                    continue
                if srch and srch not in entry["message"].lower():
                    continue
                matched.append({
                    "index": idx,
                    "type": entry["type"],
                    "message": entry["message"],
                    "timestamp": entry["timestamp"],
                })
            limit = int(p.get("limit", 120))
            return {
                "lines": matched[-limit:],
                "returned": len(matched[-limit:]),
                "total": len(self.logs),
                "nextIndex": len(self.logs),
                "dm": effective_dm,
            }



        # -- v5.4.0 playtest surface: input, device simulation, native tests ----

        if kind == "playtest_caps":
            return {
                "studioVersion": STUDIO_VERSION,
                "dm": effective_dm,
                "isStudio": True,
                "virtualInput": {
                    "available": True, "service": "UserInputService",
                    "method": "CreateVirtualInput", "context": "client",
                },
                "deviceSimulator": {
                    "available": True, "service": "StudioDeviceSimulatorService",
                    "method": "GetDeviceListAsync", "context": "edit|playClient",
                },
                "studioTestService": {
                    "available": True, "service": "StudioTestService",
                    "method": "ExecuteRunModeAsync", "context": "edit",
                },
                "multiplayerTest": {
                    "available": True, "service": "StudioTestService",
                    "method": "ExecuteMultiplayerTestAsync", "maxPlayers": 8,
                },
                "playtest": {
                    "running": self._playtest_live(),
                    "editModeActive": self.dm == "edit",
                    "canLeaveTest": True,
                },
                "baselineExpected": p.get("expect") or {},
            }

        if kind == "virtual_input":
            return self.job_virtual_input(p, effective_dm)

        if kind == "device_simulator":
            if effective_dm not in ("edit", "client"):
                raise RuntimeError(
                    f"StudioDeviceSimulatorService mutations are blocked in {effective_dm} mode "
                    "(route this job to the Edit peer)"
                )
            return self.job_device_simulator(p)

        if kind == "studio_tests":
            return self.job_studio_tests(p)

        raise RuntimeError(f"unknown job type: {kind}")

    # -- job implementations for the v5.4.0 playtest surface --------------------

    @staticmethod
    def _model_anchor(node):
        """Position to project a Model by: its PrimaryPart, else its first Part."""
        primary = node.props.get("PrimaryPart")
        named = None
        if isinstance(primary, str):
            named = next((c for c in node.children if c.name == primary), None)
        if named is None:
            stack = list(node.children)
            while stack:
                child = stack.pop(0)
                if child.class_name.endswith("Part") and isinstance(child.props.get("Position"),
                                                                    (list, tuple)):
                    return child.props["Position"]
                stack.extend(child.children)
            return None
        return named.props.get("Position")

    def _screen_point(self, step: dict):
        """Resolve a step's screen anchor the way the plugin does: explicit pixels
        win, otherwise project the target instance (GUI centre or 3D viewport)."""
        if isinstance(step.get("position"), (list, tuple)) and len(step["position"]) == 2:
            return [float(step["position"][0]), float(step["position"][1])], None
        target = step.get("target")
        if not target:
            # Two-point gestures carry `from`/`to` instead of an anchor.
            for key in ("from", "to"):
                edge = step.get(key)
                if isinstance(edge, (list, tuple)) and len(edge) == 2:
                    return [float(edge[0]), float(edge[1])], None
            return None, "no position or target"
        node = self.resolve(target)
        if node is None:
            return None, f"target not found: {target}"
        if node.class_name in {"TextBox", "TextButton", "TextLabel", "Frame", "ImageLabel",
                              "ImageButton", "ViewportFrame", "ScrollingFrame"}:
            size = node.props.get("Size") or [0, 0]
            return [float(size[0]) / 2.0, float(size[1]) / 2.0], None
        pos = node.props.get("Position")
        if not isinstance(pos, (list, tuple)) or len(pos) != 3:
            # A Model has no Position of its own: project a Part inside it, exactly
            # as the plugin does (PrimaryPart first, then any descendant Part).
            pos = self._model_anchor(node)
        if not isinstance(pos, (list, tuple)) or len(pos) != 3:
            return None, f"{node.full_name()} has no projected position (class {node.class_name})"
        cam = self.camera.get("viewportSize", {}).get("v", [1920, 1080])
        return [float(cam[0]) / 2.0 + float(pos[0]), float(cam[1]) / 2.0 - float(pos[1])], None

    def _playtest_live(self) -> bool:
        """True while a play session exists: this world started one, or the
        simulated play-client thread it spawned is still alive."""
        if self.playtest_running:
            return True
        thread = self.play_peer_thread
        return thread is not None and thread.is_alive()

    def job_virtual_input(self, p: dict, effective_dm: str) -> dict:
        steps_out = []
        hold_ms = float(p.get("holdMs") or 60.0)
        # VirtualInput only exists in a running client: refuse rather than
        # pretend. The *requested* dm is not evidence a client exists, but a
        # world that is itself a play client (or has one attached) is.
        live = self._playtest_live()
        for index, step in enumerate(p.get("steps") or [], start=1):
            rec = {"index": index, "action": step.get("action"), "delivered": False}
            if not live:
                rec["reason"] = "not_playing"
                steps_out.append(rec)
                continue
            anchor, why = self._screen_point(step)
            action = step.get("action")
            if action in INPUT_ACTIONS_LUA:
                rec["delivered"] = True
                rec["via"] = INPUT_ACTIONS_LUA[action]
                if action in ("keydown", "keyup", "press"):
                    rec["key"] = step.get("key")
                if action == "type":
                    rec["chars"] = len(step.get("text") or "")
                    self.input_state["focused"] = step.get("target") or self.input_state.get("focused")
            elif action == "wait":
                rec["delivered"] = True
                rec["via"] = "task.wait"
                rec["ms"] = float(step.get("ms") or 100.0)
            elif action == "pinch":
                rec["error"] = ("pinch needs two simultaneous pointers; CreateVirtualInput() exposes "
                                "one, so a pinch is scripted as two drags")
            elif anchor is None:
                rec["error"] = why
            else:
                rec["delivered"] = True
                rec["position"] = [round(anchor[0], 1), round(anchor[1], 1)]
                rec["via"] = "SendPointerAction" if step.get("pointerType") == "touch" else "SendMouseButton"
                if action == "press":
                    rec["holdMs"] = hold_ms
                if action in ("drag", "swipe"):
                    fr, fr_why = self._screen_point({"position": step.get("from")})
                    to, to_why = self._screen_point({"position": step.get("to")})
                    if fr is None or to is None:
                        rec["delivered"] = False
                        rec["error"] = fr_why or to_why or "drag endpoints did not resolve"
                    else:
                        rec["from"] = [round(fr[0], 1), round(fr[1], 1)]
                        rec["to"] = [round(to[0], 1), round(to[1], 1)]
                        rec["pathPoints"] = 8
                if action == "wait":
                    rec["via"] = "task.wait"
                    rec["delivered"] = True
            steps_out.append(rec)
        for rec in steps_out:
            if rec.get("delivered"):
                self.input_state["delivered"].append(rec)
                self.logs.append({
                    "type": "Output",
                    "message": f"mock: input {rec['action']} via {rec.get('via')} delivered",
                    "timestamp": time.time(),
                })
        return {
            "steps": steps_out,
            "dm": effective_dm,
            "viewport": {"width": self.camera["viewportSize"]["v"][0],
                         "height": self.camera["viewportSize"]["v"][1]},
            "focused": self.input_state.get("focused"),
            "holdMs": hold_ms,
            "settleMs": float(p.get("settleMs") or 150.0),
        }

    def _device_snapshot(self) -> dict:
        return {
            "deviceId": self.device_id,
            "orientation": self.device_orientation,
            "resolution": list(self.device_resolution),
            "pixelDensity": self.device_density,
            "scalingMode": self.device_scaling,
            "simulating": self.device_id != "default",
        }

    def job_device_simulator(self, p: dict) -> dict:
        action = p.get("action") or "status"
        before = self._device_snapshot()
        out = {"action": action, "before": before, "applied": False}
        need_device = {"set_orientation", "set_resolution", "set_pixel_density", "set_scaling_mode"}
        if action in need_device and self.device_id == "default":
            # The engine raises here; translating it into a structured refusal keeps
            # the tool callable so the agent can `set_device` and retry.
            return {
                "action": action, "before": before, "applied": False, "ok": False,
                "current": before, "changed": False, "reason": "no_active_device",
                "error": ("resolution, DPI, orientation and scaling overrides need an active device; "
                          "call manage_device_simulator(action='set_device') first"),
            }

        def profile_fields(source: dict):
            name = str(source.get("Name") or "").strip()
            if not (1 <= len(name) <= 200):
                raise ValueError("DeviceConfiguration.Name must be 1-200 characters")
            if name.lower() == "default":
                raise ValueError("DeviceConfiguration.Name cannot be 'default'")
            width = source.get("Width")
            height = source.get("Height")
            dpi = source.get("PixelDensity")
            if not (isinstance(width, (int, float)) and 1 <= width <= 7680):
                raise ValueError("DeviceConfiguration.Width must be 1-7680")
            if not (isinstance(height, (int, float)) and 1 <= height <= 4320):
                raise ValueError("DeviceConfiguration.Height must be 1-4320")
            if not (isinstance(dpi, (int, float)) and 72 <= dpi <= 10000):
                raise ValueError("DeviceConfiguration.PixelDensity must be 72-10000")
            scale = source.get("ResolutionScale", 1.0)
            if not (isinstance(scale, (int, float)) and 0 < scale <= 10):
                raise ValueError("DeviceConfiguration.ResolutionScale must be > 0 and <= 10")
            return {"Name": name, "Width": int(width), "Height": int(height),
                    "PixelDensity": int(dpi), "ResolutionScale": float(scale),
                    "DeviceForm": str(source.get("DeviceForm") or "Phone"),
                    "IsCustom": True, "DeviceId": f"{name.strip().lower().replace(' ', '-')}"}

        if action == "status":
            out["current"] = before
            out["applied"] = False
        elif action == "list":
            out["devices"] = [{"DeviceId": dev, "Name": dev, "IsCustom": False} for dev in self.device_catalog]
            out["devices"] += [dict(v, DeviceId=key) for key, v in self.device_profiles.items()]
            out["currentDeviceId"] = self.device_id
        elif action in ("set_device", "set"):
            wanted = p.get("deviceId")
            if action == "set" and wanted is None:
                out["applied"] = True
            elif wanted in (None, "", "default"):
                self.device_id = "default"
                out["applied"] = True
                out["stopped"] = True
            else:
                profile = next((d for d in self.device_catalog if d == wanted), None)
                custom = self.device_profiles.get(wanted)
                if profile is None and custom is None:
                    return {
                        "action": action, "before": before, "applied": False, "ok": False,
                        "current": before, "changed": False, "reason": "unknown_device",
                        "error": f"device id '{wanted}' is not in the Device Simulator list",
                        "knownDevices": list(self.device_catalog) + sorted(self.device_profiles),
                    }
                config = custom or {"Width": 1179, "Height": 2556, "PixelDensity": 460}
                self.device_id = wanted
                # Switching devices clears session-level overrides, like the engine.
                self.device_resolution = [config["Width"], config["Height"]]
                self.device_density = float(config["PixelDensity"])
                out["applied"] = True
                out["clearedOverrides"] = True
        if action == "set_orientation" or (action == "set" and p.get("orientation")):
            self.device_orientation = p["orientation"]
            out["applied"] = True
        if action == "set_resolution" or (action == "set" and p.get("width")):
            self.device_resolution = [int(p["width"]), int(p["height"])]
            out["applied"] = True
        if action == "set_pixel_density" or (action == "set" and p.get("density")):
            self.device_density = float(p["density"])
            out["applied"] = True
        if action == "set_scaling_mode" or (action == "set" and p.get("scalingMode")):
            self.device_scaling = p["scalingMode"]
            out["applied"] = True
        if action == "stop":
            self.device_id = "default"
            out["applied"] = True
            out["stopped"] = True
        if action == "create_profile":
            config = profile_fields(p.get("profile") or {})
            key = config["DeviceId"]
            if key in self.device_profiles:
                raise RuntimeError(f"a custom device named {config['Name']} already exists")
            self.device_profiles[key] = config
            out["applied"] = True
            out["profile"] = dict(config, DeviceId=key)
        if action == "update_profile":
            key = p.get("deviceId")
            if key in self.device_catalog:
                raise RuntimeError("built-in presets are immutable; create a custom profile instead")
            if key not in self.device_profiles:
                raise RuntimeError(f"no custom device '{key}' to update")
            merged = dict(self.device_profiles[key])
            for name, value in (p.get("profile") or {}).items():
                merged[name] = value
            self.device_profiles[key] = profile_fields(merged)
            out["applied"] = True
            out["profile"] = self.device_profiles[key]
        if action == "remove_profile":
            key = p.get("deviceId")
            if key in self.device_catalog:
                raise RuntimeError("built-in presets are immutable; nothing was removed")
            if key not in self.device_profiles:
                raise RuntimeError(f"no custom device '{key}' to remove")
            del self.device_profiles[key]
            out["applied"] = True
        out["current"] = self._device_snapshot()
        out["changed"] = out["current"] != before
        self.logs.append({
            "type": "Output",
            "message": f"mock: device simulator {action} -> {out['current']['deviceId']} "
                       f"@ {out['current']['resolution'][0]}x{out['current']['resolution'][1]}",
            "timestamp": time.time(),
        })
        return out

    def job_studio_tests(self, p: dict) -> dict:
        mode = p.get("mode") or "run"
        module = (p.get("module") or "").strip()
        players = int(p.get("players") or 2)
        runner = p.get("runnerName") or "__ArenaTestRunner"
        out = {
            "mode": mode, "ok": True, "passed": 0, "failed": 0, "total": 0,
            "suites": [], "timedOut": False, "runner": runner,
            "cleanedUp": True, "keepOpen": bool(p.get("keepOpen")),
            "engineCall": {
                "run": "StudioTestService:ExecuteRunModeAsync(args)",
                "play": "StudioTestService:ExecutePlayModeAsync(args)",
                "multiplayer": f"StudioTestService:ExecuteMultiplayerTestAsync({players}, args)",
            }.get(mode, "unknown"),
        }
        if mode == "multiplayer" and not (1 <= players <= 8):
            raise RuntimeError("ExecuteMultiplayerTestAsync accepts 1-8 simulated clients")
        if module:
            node = self.resolve(module)
            if node is None:
                raise RuntimeError(f"test module not found: {module}")
            if node.class_name not in {"ModuleScript", "Script", "LocalScript"}:
                raise RuntimeError(f"{module} is a {node.class_name}, not a script that can be required")
            source = node.source or ""
            if "EndTest" not in source and "return" not in source:
                raise RuntimeError(
                    f"{module} neither returns a report nor calls StudioTestService:EndTest — "
                    "the session would hang until the watchdog fired"
                )
            passed = source.count("expect(")
            out["suites"] = [{"name": node.full_name(), "passed": max(passed, 1), "failed": 0}]
            out["passed"] = max(passed, 1)
            out["total"] = out["passed"]
        else:
            # Studio's own discovery: the mock reports the same shape it would get back.
            out["suites"] = [{"name": "MockPlace", "passed": 3, "failed": 0}]
            out["passed"], out["total"] = 3, 3
        if p.get("collectClientLogs") and mode == "multiplayer":
            out["clientLogs"] = [
                {"player": f"MockPlayer{i + 1}", "lines": [f"mock client {i + 1}: booted"]}
                for i in range(players)
            ]
        out["durationSec"] = 0.42 if mode != "multiplayer" else round(0.6 + 0.1 * players, 2)
        out["failed"] = out["total"] - out["passed"]
        out["ok"] = out["failed"] == 0
        self.studio_test_runs.append(out)
        self.logs.append({
            "type": "Output" if out["ok"] else "Error",
            "message": f"mock: studio tests ({mode}) {out['passed']}/{out['total']} passed",
            "timestamp": time.time(),
        })
        return out



def make_http_requester(base_url: str, token: str):
    def req(method: str, path: str, payload=None, timeout: float = 20.0):
        url = f"{base_url.rstrip('/')}{path}"
        if token:
            url += ("&" if "?" in url else "?") + f"token={token}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        r = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            r.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    return req


def run_mock_loop(base_url: str = BASE, token: str = TOKEN, stop_event: threading.Event = None):
    world = MockWorld(dm="edit", peer_id="edit-mock0001")
    req = make_http_requester(base_url, token)
    qs = urllib.parse.urlencode({
        "place": PLACE,
        "placeId": PLACE_ID,
        "client": "mock",
        "peer": world.peer_id,
        "dm": "edit",
        "hasClient": "0",
        "players": "0",
        "pv": PLUGIN_VERSION,
        "sv": "mock-3.0",
    })
    while stop_event is None or not stop_event.is_set():
        try:
            out = req("GET", f"/api/poll?{qs}", timeout=12.0)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if stop_event and stop_event.is_set():
                break
            time.sleep(0.5)
            continue
        job = out.get("job")
        if not job:
            continue
        try:
            result = world.execute(job, bridge_req_fn=req)
            req("POST", "/api/result", {
                "id": job["id"],
                "ok": True,
                "result": result,
                "logs": [f"mock executed {job['type']}"],
                "peerId": world.peer_id,
                "dm": world.dm,
            })
        except Exception as exc:  # noqa: BLE001
            req("POST", "/api/result", {
                "id": job["id"],
                "ok": False,
                "error": str(exc),
                "logs": [],
                "peerId": world.peer_id,
                "dm": world.dm,
            })
    if world.play_peer_stop is not None:
        world.play_peer_stop.set()


def main():
    print(f"mock Studio v4.1.0 -> {BASE} (place: {PLACE})", flush=True)
    run_mock_loop(BASE, TOKEN)


if __name__ == "__main__":
    main()
