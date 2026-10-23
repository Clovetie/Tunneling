#!/usr/bin/env python3
"""
arena_studio.py — Full-featured CLI for the Arena <-> Roblox Studio MCP Bridge (v3.0).

Supports every MCP tool and bridge operation from your terminal or HANDS MODE (`ab.ps1`),
with strict UTF-8 handling on Windows, macOS, and Linux.

Core commands:
    python3 arena_studio.py health | peers | state | ping | watch
    python3 arena_studio.py survey [--out survey.json]
    python3 arena_studio.py inspect [Workspace] [--depth 2] [--summary]
    python3 arena_studio.py search [query] [--root game] [--is-a BasePart] [--tag Spot]
    python3 arena_studio.py read ServerScriptService.NightLoop.Config [--start 1 --end 50]
    python3 arena_studio.py script ServerScriptService.NightLoop Config Config.lua --class ModuleScript --replace
    python3 arena_studio.py edit ServerScriptService.NightLoop.Config --old "ShowPhase = true" --new "ShowPhase = false" --replace
    python3 arena_studio.py grep "TurnSpeed" [--root ServerScriptService]
    python3 arena_studio.py require-fresh ServerScriptService.NightLoop.Config --code "return MODULE.Hud"
    python3 arena_studio.py check ../nightloop/src
    python3 arena_studio.py drift ../nightloop/src ServerScriptService.NightLoop
    python3 arena_studio.py sync  ../nightloop/src ServerScriptService.NightLoop
    python3 arena_studio.py run "return {place = game.Name}" [--dm edit|server|client]
    python3 arena_studio.py runfile ../jobs/baseline_survey.lua
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from mcp_core import BridgeClient, TOOL_SPECS, execute_tool, run_luau_check

# Ensure Windows consoles never corrupt UTF-8 output with cp1252
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _resolve_token() -> str:
    env_tok = os.environ.get("BRIDGE_TOKEN", "").strip()
    if env_tok:
        return env_tok
    local_tok = Path(__file__).resolve().parent / "bridge.token"
    if local_tok.is_file():
        txt = local_tok.read_text(encoding="utf-8").strip()
        if txt:
            return txt
    return "arena-demo-7f3a"


BASE = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8077").rstrip("/")
TOKEN = _resolve_token()


def call(method: str, path: str, payload=None, timeout: float = 140.0):
    url = f"{BASE}{path}"
    headers = {}
    if TOKEN:
        headers["X-Arena-Token"] = TOKEN
        url += ("&" if "?" in url else "?") + f"token={TOKEN}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "body": exc.read().decode("utf-8", "replace")[:400]}
    except urllib.error.URLError as exc:
        return {"error": f"cannot reach bridge at {BASE} ({exc.reason}). Is server.py running?"}


def submit(kind: str, payload: dict, note: str = None, wait: float = 60.0, dm: str = "auto", peer_id: str = None):
    body = {"type": kind, "payload": payload, "note": note, "wait": wait}
    if dm and dm != "auto":
        body["dm"] = dm
    if peer_id:
        body["peer_id"] = peer_id
    return call("POST", "/api/jobs", body, timeout=max(wait + 20.0, 35.0))


def show(job: dict, full: bool = False, raw_json: bool = False) -> int:
    if raw_json:
        print(json.dumps(job, indent=2, ensure_ascii=False))
        return 0 if job.get("status") in (None, "done") and "error" not in job else 1
    if "error" in job and "status" not in job:
        print(f"!! {job['error']}")
        if job.get("body"):
            print(f"   {job['body']}")
        return 1
    status = job.get("status")
    if status == "done":
        lat = f" [{job['duration_ms']}ms]" if job.get("duration_ms") is not None else ""
        dm_tag = f" dm={job['dm']}" if job.get("dm") else ""
        print(f"[ok] {job['type']} ({job['id']}){dm_tag}{lat}")
        result = job.get("result")
        if result is not None:
            formatted = json.dumps(result, indent=2, ensure_ascii=False)
            print(formatted if full else formatted[:4000])
    elif status == "error":
        print(f"[fail] {job['type']} ({job['id']})\n  {job.get('error')}")
        return 1
    else:
        print(f"[{status}] {job.get('id')} — Studio has not picked it up yet. Is the plugin connected?")
        return 2
    for line in job.get("logs") or []:
        print(f"  | {line}")
    return 0


def show_tool_output(label: str, client: BridgeClient, tool_name: str, tool_args: dict, full: bool = False, raw_json: bool = False) -> int:
    try:
        out = execute_tool(client, tool_name, tool_args)
    except Exception as exc:
        if raw_json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, ensure_ascii=False))
        else:
            print(f"[fail] {label}: {exc}")
        return 1
    if raw_json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0
    formatted = json.dumps(out, indent=2, ensure_ascii=False)
    print(f"[ok] {label}")
    print(formatted if full else formatted[:6000])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dm", default="auto", choices=["auto", "edit", "server", "client"], help="Target DataModel context")
    ap.add_argument("--peer", default=None, help="Target specific Studio peer ID")
    ap.add_argument("--full", action="store_true", help="Do not truncate long JSON results")
    ap.add_argument("--json", dest="raw_json", action="store_true", help="Print pure JSON output only")

    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health")
    sub.add_parser("peers")
    sub.add_parser("state")
    sub.add_parser("ping")
    sub.add_parser("watch")
    sub.add_parser("mcp-tools")
    sub.add_parser("clear-queue")

    p = sub.add_parser("cancel")
    p.add_argument("job_id")

    p = sub.add_parser("survey", help="read the whole place in one job")
    p.add_argument("--out", default="survey.json")
    p.add_argument("--max-bytes", type=int, default=120000)
    p.add_argument("--no-source", action="store_true")

    p = sub.add_parser("console")
    p.add_argument("--limit", type=int, default=120)
    p.add_argument("--level", default="all", choices=["all", "Error", "Warning", "Output", "Info"])
    p.add_argument("--search", default=None)
    p.add_argument("--since", type=int, default=0)

    p = sub.add_parser("reads")
    p.add_argument("paths", nargs="+")
    p.add_argument("--out", default=None)

    p = sub.add_parser("run")
    p.add_argument("code")

    p = sub.add_parser("runfile")
    p.add_argument("file")

    p = sub.add_parser("build")
    p.add_argument("file")

    p = sub.add_parser("inspect")
    p.add_argument("path", nargs="?", default="game")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--summary", action="store_true")

    p = sub.add_parser("search")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--root", default="game")
    p.add_argument("--class", dest="cls", default=None)
    p.add_argument("--is-a", dest="is_a", default=None)
    p.add_argument("--tag", default=None)
    p.add_argument("--attr", default=None)
    p.add_argument("--limit", type=int, default=60)

    p = sub.add_parser("compare")
    p.add_argument("path_a")
    p.add_argument("path_b")

    p = sub.add_parser("props")
    p.add_argument("paths", nargs="+")

    p = sub.add_parser("set")
    p.add_argument("path")
    p.add_argument("json_props", help='JSON object e.g. \'{"Anchored":true}\'')

    p = sub.add_parser("mass-set")
    p.add_argument("json_props")
    p.add_argument("paths", nargs="+")

    p = sub.add_parser("read")
    p.add_argument("path")
    p.add_argument("--start", type=int, default=None)
    p.add_argument("--end", type=int, default=None)
    p.add_argument("-n", "--numbers", action="store_true")

    p = sub.add_parser("script")
    p.add_argument("parent")
    p.add_argument("name")
    p.add_argument("file")
    p.add_argument("--class", dest="cls", default="Script")
    p.add_argument("--replace", action="store_true", help="Recreate script instance to bust require() cache")

    p = sub.add_parser("edit")
    p.add_argument("path")
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--all", action="store_true")
    p.add_argument("--replace", action="store_true", help="Recreate script instance to bust require() cache")

    p = sub.add_parser("ssearch")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--root", default=None)

    p = sub.add_parser("grep")
    p.add_argument("pattern")
    p.add_argument("--root", default=None)
    p.add_argument("-i", "--ignore-case", action="store_true")
    p.add_argument("--lua-pattern", action="store_true")
    p.add_argument("-C", "--context", type=int, default=1)

    p = sub.add_parser("replace")
    p.add_argument("find")
    p.add_argument("replacement")
    p.add_argument("--root", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--replace-instance", action="store_true")

    p = sub.add_parser("open")
    p.add_argument("path", nargs="?", default=None)
    p.add_argument("--line", type=int, default=1)
    p.add_argument("--list", action="store_true")

    p = sub.add_parser("require-fresh")
    p.add_argument("path")
    p.add_argument("--code", default=None)
    p.add_argument("--package-root", default=None)

    p = sub.add_parser("clone")
    p.add_argument("path")
    p.add_argument("--parent", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--offset", type=float, nargs=3, default=None)

    p = sub.add_parser("move")
    p.add_argument("paths", nargs="+")
    p.add_argument("--parent", default=None)
    p.add_argument("--name", default=None)
    p.add_argument("--keep-world", action="store_true")

    p = sub.add_parser("delete")
    p.add_argument("paths", nargs="+")

    p = sub.add_parser("attrs")
    p.add_argument("action", choices=["get", "set", "delete"])
    p.add_argument("path")
    p.add_argument("--data", default=None, help="JSON map for set or comma-separated keys for delete")

    p = sub.add_parser("tags")
    p.add_argument("action", choices=["list_all", "find_tagged", "get", "add", "remove"])
    p.add_argument("--tag", default=None)
    p.add_argument("--paths", nargs="*", default=None)

    p = sub.add_parser("selection")
    p.add_argument("action", nargs="?", default="get", choices=["get", "set", "add", "clear"])
    p.add_argument("paths", nargs="*", default=[])

    p = sub.add_parser("camera")
    p.add_argument("action", nargs="?", default="get", choices=["get", "focus", "set"])
    p.add_argument("--path", default=None)
    p.add_argument("--pos", type=float, nargs=3, default=None)
    p.add_argument("--look", type=float, nargs=3, default=None)
    p.add_argument("--fov", type=float, default=None)

    p = sub.add_parser("undo")
    p.add_argument("--steps", type=int, default=1)
    p = sub.add_parser("redo")
    p.add_argument("--steps", type=int, default=1)

    p = sub.add_parser("terrain")
    p.add_argument("action", choices=["materials", "fill_block", "fill_ball", "fill_cylinder", "fill_wedge", "replace", "clear"])
    p.add_argument("--material", default="Grass")
    p.add_argument("--pos", type=float, nargs=3, default=[0, 0, 0])
    p.add_argument("--size", type=float, nargs=3, default=[32, 8, 32])
    p.add_argument("--radius", type=float, default=16)
    p.add_argument("--confirm-clear-all", action="store_true")

    p = sub.add_parser("insert-asset")
    p.add_argument("asset_id", type=int)
    p.add_argument("--parent", default="Workspace")
    p.add_argument("--keep-scripts", action="store_true")

    p = sub.add_parser("playtest")
    p.add_argument("action", choices=["start_play", "start_run", "stop", "status"])

    p = sub.add_parser("scenario")
    p.add_argument("--mode", default="run", choices=["run", "play"])
    p.add_argument("--warmup", type=float, default=1.5)
    p.add_argument("--server-code", default=None)
    p.add_argument("--client-code", default=None)

    p = sub.add_parser("check", help="local offline Luau compiler + undefined-global check")
    p.add_argument("target")

    p = sub.add_parser("drift", help="exact 32-bit FNV-1a drift check between local dir and Studio")
    p.add_argument("local_dir")
    p.add_argument("studio_root")

    p = sub.add_parser("sync", help="validate, snapshot, and push local Luau dir to Studio in one undo step")
    p.add_argument("local_dir")
    p.add_argument("studio_root")
    p.add_argument("--skip-check", action="store_true")

    p = sub.add_parser("pull", help="export Studio script subtree into local dir")
    p.add_argument("studio_root")
    p.add_argument("local_dir")

    p = sub.add_parser("snapshots")
    p.add_argument("action", nargs="?", default="list", choices=["list", "create", "get", "restore"])
    p.add_argument("--root", default="game")
    p.add_argument("--label", default=None)
    p.add_argument("--id", dest="snap_id", default=None)

    p = sub.add_parser("import-image", help="import PNG/JPG/BMP/URL/repo image directly into Studio & repo")
    p.add_argument("source", help="local file path, https:// URL, or repo asset (e.g. ui/icon_coin.png)")
    p.add_argument("--name", default=None)
    p.add_argument("--subdir", default="imported")
    p.add_argument("--target", default=None, help="optional Studio Instance path to apply onto")
    p.add_argument("--apply-mode", default="auto", choices=["auto", "decal", "texture", "billboard", "surface_gui", "screen_gui"])
    p.add_argument("--face", default="Front")
    p.add_argument("--editable", action="store_true", help="hydrate live EditableImage RGBA buffer")
    p.add_argument("--publish", default="none", choices=["none", "studio", "open_cloud"])
    p.add_argument("--sync-assets", action="store_true")

    p = sub.add_parser("gen-image", help="generate crisp procedural icon/sprite/texture PNG into repo & Studio")
    p.add_argument("preset")
    p.add_argument("--name", default=None)
    p.add_argument("--size", type=int, default=128)
    p.add_argument("--primary", default=None)
    p.add_argument("--secondary", default=None)
    p.add_argument("--target", default=None)
    p.add_argument("--apply-mode", default="auto", choices=["auto", "decal", "texture", "billboard", "surface_gui", "screen_gui"])

    p = sub.add_parser("kit", help="browse built-in skills, Luau modules, prefabs, and repo image assets")
    p.add_argument("category", nargs="?", default="all", choices=["all", "skills", "modules", "templates", "prefabs", "images", "presets", "cloud_icons"])
    p.add_argument("--item", default=None)

    p = sub.add_parser("install-module", help="install one or 'all' 12 Luau modules from roblox-kit/modules into Studio")
    p.add_argument("module", nargs="?", default="all")
    p.add_argument("--parent", default="ReplicatedStorage.Shared")

    p = sub.add_parser("prefab", help="spawn a 3D / UI / VFX / Lighting prefab into Roblox Studio")
    p.add_argument("name")
    p.add_argument("--parent", default=None)
    p.add_argument("--pos", nargs=3, type=float, default=None)
    p.add_argument("--as", dest="override_name", default=None)

    p = sub.add_parser("template", help="install a full UI screen or multi-script gameplay system template into Studio")
    p.add_argument("name", help="full_stud_ui_suite | stud_shop_window | stud_hud_shell | stud_daily_rewards | stud_rebirth_window | stud_rarity_index")

    p = sub.add_parser("icons", help="search 1,022 verified cloud rbxassetid:// UI icons from CheckUIIcons")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--category", default=None)
    p.add_argument("--limit", type=int, default=40)

    args = ap.parse_args()
    client = BridgeClient(base_url=BASE, token=TOKEN)

    if args.cmd == "mcp-tools":
        summary_list = [
            {
                "name": t["name"],
                "title": t["annotations"]["title"],
                "readOnly": t["annotations"]["readOnlyHint"],
                "description": t["description"],
            }
            for t in TOOL_SPECS
        ]
        print(json.dumps({"count": len(summary_list), "tools": summary_list}, indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "check":
        res = run_luau_check(path=args.target)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res["ok"] else 1

    if args.cmd == "health":
        print(json.dumps(call("GET", "/api/health"), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "peers":
        print(json.dumps(call("GET", "/api/peers"), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "state":
        return show_tool_output("get_studio_state", client, "get_studio_state", {"dm": args.dm, "peerId": args.peer}, args.full, args.raw_json)

    if args.cmd == "cancel":
        print(json.dumps(call("POST", f"/api/jobs/{args.job_id}/cancel"), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "clear-queue":
        print(json.dumps(call("POST", "/api/queue/clear"), indent=2, ensure_ascii=False))
        return 0

    if args.cmd == "watch":
        print(f"watching {BASE} — ctrl-c to stop")
        seen = set()
        while True:
            state = call("GET", "/api/state", timeout=15)
            link = "connected" if state.get("studio_connected") else "waiting"
            for job in reversed(state.get("recent", [])):
                key = (job["id"], job["status"])
                if key not in seen and job["status"] in ("done", "error"):
                    seen.add(key)
                    mark = "ok  " if job["status"] == "done" else "FAIL"
                    print(f"[{mark}] {job['type']:<16} {job.get('note') or ''} {job.get('error') or ''}")
            print(f"\r studio: {link}  queued: {state.get('queued', 0)}   ", end="", flush=True)
            time.sleep(2)

    if args.cmd == "survey":
        job = submit("survey", {
            "maxScriptBytes": args.max_bytes,
            "includeSource": not args.no_source,
        }, "survey place", wait=120, dm=args.dm, peer_id=args.peer)
        if job.get("status") != "done":
            return show(job, args.full, args.raw_json)
        result = job["result"]
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
        if args.raw_json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        place = result.get("place", {})
        totals = result.get("totals", {})
        print(f"[ok] surveyed '{place.get('name')}' (placeId {place.get('placeId')})")
        print(f"     {totals.get('instancesVisited')} instances, {totals.get('scripts')} scripts, {totals.get('sourceBytesReturned')} bytes of source")
        if totals.get("scriptsWithheld"):
            print(f"     {totals['scriptsWithheld']} scripts over budget — pull them with: arena_studio.py reads <path> ...")
        if totals.get("visitCapHit"):
            print("     NOTE: hit the instance visit cap; place is very large")
        census = result.get("census", {})
        top = sorted(census.items(), key=lambda kv: -kv[1])[:8]
        print("     top classes: " + ", ".join(f"{k}x{v}" for k, v in top))
        print(f"     full JSON -> {args.out}")
        return 0

    if args.cmd == "console":
        job = submit("console", {
            "limit": args.limit,
            "level": args.level,
            "search": args.search,
            "sinceIndex": args.since,
        }, "console", wait=30, dm=args.dm, peer_id=args.peer)
        if job.get("status") != "done" or args.raw_json:
            return show(job, args.full, args.raw_json)
        for line in job["result"].get("lines", []):
            print(f"  {line['type'][:4]:<4} {line['message']}")
        return 0

    if args.cmd == "reads":
        job = submit("read_scripts", {"paths": args.paths}, "read scripts", wait=90, dm=args.dm, peer_id=args.peer)
        if job.get("status") != "done" or args.raw_json:
            return show(job, args.full, args.raw_json)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                json.dump(job["result"], fh, indent=2, ensure_ascii=False)
            print(f"[ok] {len(job['result']['scripts'])} scripts -> {args.out}")
        else:
            for entry in job["result"]["scripts"]:
                print(f"----- {entry['path']} (hash={entry.get('hash','?')}) -----")
                print(entry.get("source") or entry.get("error"))
        return 0

    if args.cmd == "ping":
        return show(submit("ping", {}, "ping", wait=30, dm=args.dm, peer_id=args.peer), args.full, args.raw_json)

    if args.cmd == "run":
        return show(submit("run_luau", {"code": args.code}, "inline luau", dm=args.dm, peer_id=args.peer), args.full, args.raw_json)

    if args.cmd == "runfile":
        code = Path(args.file).read_text(encoding="utf-8")
        return show(submit("run_luau", {"code": code}, os.path.basename(args.file), dm=args.dm, peer_id=args.peer), args.full, args.raw_json)

    if args.cmd == "build":
        spec = json.loads(Path(args.file).read_text(encoding="utf-8"))
        return show(submit("build", spec, os.path.basename(args.file), dm=args.dm, peer_id=args.peer), args.full, args.raw_json)

    if args.cmd == "inspect":
        return show(submit("inspect", {
            "path": args.path,
            "depth": args.depth,
            "descendantSummary": args.summary,
        }, f"inspect {args.path}", dm=args.dm, peer_id=args.peer), args.full, args.raw_json)

    if args.cmd == "search":
        targs = {"query": args.query, "root": args.root, "limit": args.limit, "dm": args.dm, "peerId": args.peer}
        if args.cls:
            targs["className"] = args.cls
        if args.is_a:
            targs["isA"] = args.is_a
        if args.tag:
            targs["tag"] = args.tag
        if args.attr:
            targs["attribute"] = args.attr
        return show_tool_output("search_game_tree", client, "search_game_tree", targs, args.full, args.raw_json)

    if args.cmd == "compare":
        return show_tool_output("compare_instances", client, "compare_instances", {
            "pathA": args.path_a, "pathB": args.path_b, "dm": args.dm, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "props":
        return show_tool_output("get_properties", client, "get_properties", {
            "paths": args.paths, "dm": args.dm, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "set":
        return show_tool_output("set_properties", client, "set_properties", {
            "path": args.path, "properties": json.loads(args.json_props), "dm": args.dm, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "mass-set":
        return show_tool_output("mass_set_properties", client, "mass_set_properties", {
            "paths": args.paths, "properties": json.loads(args.json_props), "dm": args.dm, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "read":
        payload = {"path": args.path, "withLineNumbers": args.numbers}
        if args.start is not None:
            payload["startLine"] = args.start
        if args.end is not None:
            payload["endLine"] = args.end
        return show(submit("read_script", payload, f"read {args.path}", dm=args.dm, peer_id=args.peer), args.full, args.raw_json)

    if args.cmd == "script":
        source = Path(args.file).read_text(encoding="utf-8")
        return show_tool_output(f"write {args.name}", client, "write_script", {
            "parent": args.parent,
            "name": args.name,
            "className": args.cls,
            "source": source,
            "replaceInstance": args.replace,
            "dm": args.dm,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "edit":
        return show_tool_output(f"multi_edit {args.path}", client, "multi_edit", {
            "path": args.path,
            "edits": [{"oldText": args.old, "newText": args.new, "replaceAll": args.all}],
            "replaceInstance": args.replace,
            "dm": args.dm,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "ssearch":
        return show_tool_output("script_search", client, "script_search", {
            "query": args.query, "root": args.root, "dm": args.dm, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "grep":
        return show_tool_output("script_grep", client, "script_grep", {
            "pattern": args.pattern,
            "root": args.root,
            "caseSensitive": not args.ignore_case,
            "isPattern": args.lua_pattern,
            "contextLines": args.context,
            "dm": args.dm,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "replace":
        return show_tool_output("find_and_replace_scripts", client, "find_and_replace_scripts", {
            "find": args.find,
            "replace": args.replacement,
            "root": args.root,
            "dryRun": args.dry_run,
            "replaceInstance": args.replace_instance,
            "dm": args.dm,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "open":
        return show_tool_output("open_script_in_editor", client, "open_script_in_editor", {
            "path": args.path, "line": args.line, "listOnly": args.list or not args.path, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "require-fresh":
        return show_tool_output("require_fresh", client, "require_fresh", {
            "path": args.path, "code": args.code, "packageRoot": args.package_root, "dm": args.dm, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "clone":
        targs = {"path": args.path, "count": args.count, "dm": args.dm, "peerId": args.peer}
        if args.parent:
            targs["parent"] = args.parent
        if args.name:
            targs["newName"] = args.name
        if args.offset:
            targs["offset"] = args.offset
        return show_tool_output("clone_instance", client, "clone_instance", targs, args.full, args.raw_json)

    if args.cmd == "move":
        targs = {"paths": args.paths, "preserveWorldPosition": args.keep_world, "dm": args.dm, "peerId": args.peer}
        if args.parent:
            targs["newParent"] = args.parent
        if args.name:
            targs["newName"] = args.name
        return show_tool_output("reparent_or_rename", client, "reparent_or_rename", targs, args.full, args.raw_json)

    if args.cmd == "delete":
        payload = {"path": args.paths[0]} if len(args.paths) == 1 else {"paths": args.paths}
        return show(submit("delete", payload, f"delete {','.join(args.paths)}", dm=args.dm, peer_id=args.peer), args.full, args.raw_json)

    if args.cmd == "attrs":
        targs = {"action": args.action, "paths": [args.path], "dm": args.dm, "peerId": args.peer}
        if args.action == "set" and args.data:
            targs["attributes"] = json.loads(args.data)
        elif args.action == "delete" and args.data:
            targs["keys"] = [k.strip() for k in args.data.split(",") if k.strip()]
        return show_tool_output("manage_attributes", client, "manage_attributes", targs, args.full, args.raw_json)

    if args.cmd == "tags":
        return show_tool_output("manage_tags", client, "manage_tags", {
            "action": args.action, "tag": args.tag, "paths": args.paths, "dm": args.dm, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "selection":
        return show_tool_output("manage_selection", client, "manage_selection", {
            "action": args.action, "paths": args.paths, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "camera":
        targs = {"action": args.action, "dm": args.dm, "peerId": args.peer}
        if args.path:
            targs["path"] = args.path
        if args.pos:
            targs["position"] = args.pos
        if args.look:
            targs["lookAt"] = args.look
        if args.fov:
            targs["fov"] = args.fov
        return show_tool_output("manage_camera", client, "manage_camera", targs, args.full, args.raw_json)

    if args.cmd == "undo":
        return show_tool_output("undo", client, "undo_redo", {"action": "undo", "steps": args.steps, "peerId": args.peer}, args.full, args.raw_json)

    if args.cmd == "redo":
        return show_tool_output("redo", client, "undo_redo", {"action": "redo", "steps": args.steps, "peerId": args.peer}, args.full, args.raw_json)

    if args.cmd == "terrain":
        return show_tool_output("edit_terrain", client, "edit_terrain", {
            "action": args.action,
            "material": args.material,
            "position": args.pos,
            "size": args.size,
            "radius": args.radius,
            "confirmClearAll": args.confirm_clear_all,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "insert-asset":
        return show_tool_output("insert_asset", client, "insert_asset", {
            "assetId": args.asset_id,
            "parent": args.parent,
            "stripScripts": not args.keep_scripts,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "playtest":
        return show_tool_output("start_stop_play", client, "start_stop_play", {
            "action": args.action, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "scenario":
        return show_tool_output("run_playtest_scenario", client, "run_playtest_scenario", {
            "mode": args.mode,
            "warmupSeconds": args.warmup,
            "serverCode": args.server_code,
            "clientCode": args.client_code,
        }, args.full, args.raw_json)

    if args.cmd == "drift":
        return show_tool_output("drift_check", client, "drift_and_sync", {
            "mode": "check", "localDir": args.local_dir, "studioRoot": args.studio_root, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "sync":
        return show_tool_output("sync_push", client, "drift_and_sync", {
            "mode": "push",
            "localDir": args.local_dir,
            "studioRoot": args.studio_root,
            "skipCheck": args.skip_check,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "pull":
        return show_tool_output("sync_pull", client, "drift_and_sync", {
            "mode": "pull", "localDir": args.local_dir, "studioRoot": args.studio_root, "peerId": args.peer
        }, args.full, args.raw_json)

    if args.cmd == "snapshots":
        return show_tool_output("manage_snapshots", client, "manage_snapshots", {
            "action": args.action,
            "root": args.root,
            "label": args.label,
            "snapshotId": args.snap_id,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "kit":
        return show_tool_output("browse_kit", client, "browse_kit", {
            "category": args.category,
            "item": args.item,
        }, True, args.raw_json)

    if args.cmd == "import-image":
        src = args.source
        targs = {
            "name": args.name,
            "saveSubdir": args.subdir,
            "targetPath": args.target,
            "applyMode": args.apply_mode,
            "face": args.face,
            "useEditableImage": args.editable,
            "publishVia": args.publish,
            "syncAssetsModule": args.sync_assets,
            "peerId": args.peer,
        }
        if src.startswith("http://") or src.startswith("https://"):
            targs["imageUrl"] = src
        elif Path(src).is_file():
            targs["filePath"] = str(Path(src).resolve())
        else:
            targs["repoAsset"] = src
        return show_tool_output("import_image", client, "import_image", targs, args.full, args.raw_json)

    if args.cmd == "gen-image":
        return show_tool_output("generate_procedural_image", client, "generate_procedural_image", {
            "preset": args.preset,
            "name": args.name,
            "width": args.size,
            "height": args.size,
            "primaryColor": args.primary,
            "secondaryColor": args.secondary,
            "targetPath": args.target,
            "applyMode": args.apply_mode,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "install-module":
        return show_tool_output("install_kit_module", client, "install_kit_module", {
            "module": args.module,
            "parent": args.parent,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "prefab":
        return show_tool_output("spawn_prefab", client, "spawn_prefab", {
            "prefab": args.name,
            "parent": args.parent,
            "position": args.pos,
            "name": args.override_name,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "template":
        return show_tool_output("install_template", client, "install_template", {
            "template": args.name,
            "peerId": args.peer,
        }, args.full, args.raw_json)

    if args.cmd == "icons":
        return show_tool_output("search_cloud_icons", client, "search_cloud_icons", {
            "query": args.query,
            "category": args.category,
            "limit": args.limit,
        }, True, args.raw_json)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
