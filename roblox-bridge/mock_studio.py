#!/usr/bin/env python3
"""
mock_studio.py — stands in for Roblox Studio so the bridge can be tested
without Studio open. It speaks exactly the same protocol as ArenaBridge.lua:
poll -> execute -> post result. It does NOT build anything real; it just
reports what the plugin would have done.

    python3 mock_studio.py            # polls forever
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8077").rstrip("/")
TOKEN = os.environ.get("BRIDGE_TOKEN", "arena-demo-7f3a")
PLACE = os.environ.get("MOCK_PLACE", "DemoPlace (simulated Studio)")


def req(method, path, payload=None, timeout=20):
    url = f"{BASE}{path}"
    url += ("&" if "?" in url else "?") + f"token={TOKEN}"
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def count_nodes(tree):
    total = 0
    for node in tree:
        total += 1 + count_nodes(node.get("children", []))
    return total


def execute(job):
    kind, p = job["type"], job.get("payload", {})
    if kind == "ping":
        return {"place": PLACE, "placeId": 0, "studio": "mock"}
    if kind == "build":
        tree = p.get("tree") or [p]
        names = [n.get("name", n.get("className", "?")) for n in tree]
        return {"created": [f"{p.get('parent','Workspace')}.{n}" for n in names],
                "instances": count_nodes(tree)}
    if kind == "write_script":
        return {"path": f"{p.get('parent')}.{p.get('name')}",
                "bytes": len(p.get("source", ""))}
    if kind == "run_luau":
        return {"returned": "nil", "ranLines": p.get("code", "").count("\n") + 1}
    if kind == "survey":
        return {
            "place": {"name": "Mock Obby", "placeId": 123456789, "gameId": 987654},
            "services": [{"name": "Workspace", "descendants": 412, "children": 9},
                         {"name": "ServerScriptService", "descendants": 4, "children": 3}],
            "census": {"Part": 380, "Model": 14, "Script": 3, "LocalScript": 1,
                       "RemoteEvent": 2, "ScreenGui": 1},
            "scripts": [
                {"path": "ServerScriptService.CheckpointService", "className": "Script",
                 "bytes": 1840, "lines": 72, "runContext": "Legacy",
                 "source": "-- mock checkpoint logic\nlocal Players = game:GetService('Players')\n"},
                {"path": "StarterPlayer.StarterPlayerScripts.HudController",
                 "className": "LocalScript", "bytes": 920, "lines": 41,
                 "source": "-- mock hud\n"}],
            "remotes": [{"path": "ReplicatedStorage.Events.CheckpointReached",
                         "className": "RemoteEvent"}],
            "workspaceTopLevel": [{"name": "Obby", "className": "Model",
                                   "parts": 370, "size": "512 x 90 x 64"}],
            "gui": [{"name": "MainHud", "className": "ScreenGui", "descendants": 11}],
            "tags": {"Checkpoint": 12, "KillBrick": 28},
            "settings": {"gravity": "196.2", "streamingEnabled": "false",
                         "clockTime": "14", "technology": "Future", "hasTerrain": True},
            "totals": {"instancesVisited": 430, "scripts": 4,
                       "sourceBytesReturned": 2760, "scriptsWithheld": 0,
                       "visitCapHit": False},
        }
    if kind == "console":
        return {"lines": [{"type": "Output", "message": "mock: server started"},
                          {"type": "Warning", "message": "mock: slow frame"}], "total": 2}
    if kind == "read_scripts":
        return {"scripts": [{"path": q, "bytes": 42, "source": "-- mock source\n",
                             "truncated": False} for q in p.get("paths", [])],
                "bytesReturned": 42}
    if kind == "inspect":
        return {"name": p.get("path", "game"), "className": "DataModel",
                "children": [{"name": "Workspace", "className": "Workspace"},
                             {"name": "ServerScriptService",
                              "className": "ServerScriptService"}]}
    raise RuntimeError(f"unknown job type: {kind}")


def main():
    print(f"mock Studio -> {BASE} (place: {PLACE})", flush=True)
    while True:
        try:
            out = req("GET", f"/api/poll?place={urllib.parse.quote(PLACE)}&client=mock")
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"poll failed: {exc}", flush=True)
            time.sleep(3)
            continue
        job = out.get("job")
        if not job:
            continue
        try:
            result = execute(job)
            req("POST", "/api/result",
                {"id": job["id"], "ok": True, "result": result,
                 "logs": [f"mock executed {job['type']}"]})
            print(f"[ok] {job['type']} {job['id']}", flush=True)
        except Exception as exc:  # noqa: BLE001
            req("POST", "/api/result",
                {"id": job["id"], "ok": False, "error": str(exc), "logs": []})
            print(f"[fail] {job['type']} {job['id']}: {exc}", flush=True)


if __name__ == "__main__":
    main()
