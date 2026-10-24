#!/usr/bin/env python3
"""
mock_studio.py — Stateful Roblox Studio v3.0 simulator for testing the bridge,
CLI, and all 37 MCP tools without Roblox Studio open.

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
PLACE = os.environ.get("MOCK_PLACE", "DemoPlace (simulated Studio v3.0)")
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
            "build", "write_script", "multi_edit", "set_properties",
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
                "pluginVersion": "3.0",
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
                "pluginVersion": "3.0",
                "dm": effective_dm,
                "peerId": self.peer_id,
                "isEdit": effective_dm == "edit",
                "isRunning": effective_dm != "edit" or self.play_peer_stop is not None,
                "playtestActive": self.play_peer_stop is not None,
                "players": [{"name": "Player1", "displayName": "Player1", "userId": 1, "hasCharacter": True}] if effective_dm != "edit" else [],
                "selectionCount": len(self.selection),
                "canUndo": bool(self.undo_stack),
                "canRedo": bool(self.redo_stack),
                "memoryMb": 412.5,
                "physicsFps": 60.0,
                "camera": self.camera,
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
                "pluginVersion": "3.0",
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
            deleted = []
            for path in targets:
                inst = self.must_resolve(path)
                full_p = inst.full_name()
                if inst.parent:
                    inst.parent.children.remove(inst)
                deleted.append(full_p)
            return {"deleted": deleted[0] if len(deleted) == 1 else deleted, "paths": deleted, "count": len(deleted)}

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
                if self.play_peer_stop is None and bridge_req_fn is not None:
                    stop_evt = threading.Event()
                    self.play_peer_stop = stop_evt
                    play_world = MockWorld(dm="server", peer_id="server-mockplay")
                    play_world.root = self.snapshot_tree()

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
                                    "pv": "3.0",
                                })
                                out = bridge_req_fn("GET", f"/api/poll?{qs}", timeout=3.0)
                                j = out.get("job")
                                if not j:
                                    continue
                                if j["type"] == "playtest" and j.get("payload", {}).get("action") == "stop":
                                    stop_evt.set()
                                    self.play_peer_stop = None
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
                return {"stopped": True, "detail": "EndTest called", "dm": self.dm}
            return {"dm": self.dm, "isRunning": self.play_peer_stop is not None, "playtestActive": self.play_peer_stop is not None}

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

        raise RuntimeError(f"unknown job type: {kind}")


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
        "pv": "3.0",
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
    print(f"mock Studio v3.0 -> {BASE} (place: {PLACE})", flush=True)
    run_mock_loop(BASE, TOKEN)


if __name__ == "__main__":
    main()
