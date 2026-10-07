#!/usr/bin/env python3
"""
arena_studio.py — tiny CLI for the Arena <-> Roblox Studio bridge.

Both you and the Arena agent use this (or plain curl) to push work into Studio.

    python3 arena_studio.py health
    python3 arena_studio.py ping
    python3 arena_studio.py run "workspace.Baseplate.BrickColor = BrickColor.new('Bright red')"
    python3 arena_studio.py runfile scripts/spawn_coins.lua
    python3 arena_studio.py build examples/coin.json
    python3 arena_studio.py script ServerScriptService CoinHandler examples/coin_handler.lua
    python3 arena_studio.py inspect Workspace --depth 2
    python3 arena_studio.py watch

Env: BRIDGE_URL (default http://127.0.0.1:8077), BRIDGE_TOKEN
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BRIDGE_URL", "http://127.0.0.1:8077").rstrip("/")
TOKEN = os.environ.get("BRIDGE_TOKEN", "arena-demo-7f3a")


def call(method, path, payload=None, timeout=140):
    url = f"{BASE}{path}"
    if TOKEN:
        url += ("&" if "?" in url else "?") + f"token={TOKEN}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "body": exc.read().decode()[:300]}
    except urllib.error.URLError as exc:
        return {"error": f"cannot reach bridge at {BASE} ({exc.reason}). "
                         f"Is server.py running?"}


def submit(kind, payload, note=None, wait=60):
    out = call("POST", "/api/jobs",
               {"type": kind, "payload": payload, "note": note, "wait": wait})
    return out


def show(job):
    if "error" in job and "status" not in job:
        print(f"!! {job['error']}")
        return 1
    status = job.get("status")
    if status == "done":
        print(f"[ok] {job['type']} ({job['id']})")
        result = job.get("result")
        if result is not None:
            print(json.dumps(result, indent=2)[:4000])
    elif status == "error":
        print(f"[fail] {job['type']} ({job['id']})\n  {job.get('error')}")
        return 1
    else:
        print(f"[{status}] {job.get('id')} — Studio has not picked it up yet. "
              f"Is the plugin connected?")
        return 2
    for line in job.get("logs") or []:
        print(f"  | {line}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health")
    sub.add_parser("ping")
    sub.add_parser("watch")

    p = sub.add_parser("survey", help="read the whole place in one job")
    p.add_argument("--out", default="survey.json")
    p.add_argument("--max-bytes", type=int, default=120000)
    p.add_argument("--no-source", action="store_true")
    p = sub.add_parser("console"); p.add_argument("--limit", type=int, default=120)
    p = sub.add_parser("reads"); p.add_argument("paths", nargs="+")
    p.add_argument("--out", default=None)

    p = sub.add_parser("run");      p.add_argument("code")
    p = sub.add_parser("runfile");  p.add_argument("file")
    p = sub.add_parser("build");    p.add_argument("file")
    p = sub.add_parser("inspect")
    p.add_argument("path", nargs="?", default="game")
    p.add_argument("--depth", type=int, default=2)
    p = sub.add_parser("read");     p.add_argument("path")
    p = sub.add_parser("script")
    p.add_argument("parent"); p.add_argument("name"); p.add_argument("file")
    p.add_argument("--class", dest="cls", default="Script")
    p = sub.add_parser("delete");   p.add_argument("path")

    args = ap.parse_args()

    if args.cmd == "health":
        print(json.dumps(call("GET", "/api/health"), indent=2))
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
                    print(f"[{mark}] {job['type']:<14} {job.get('note') or ''} "
                          f"{job.get('error') or ''}")
            print(f"\r studio: {link}  queued: {state.get('queued', 0)}   ",
                  end="", flush=True)
            time.sleep(2)

    if args.cmd == "survey":
        job = submit("survey", {
            "maxScriptBytes": args.max_bytes,
            "includeSource": not args.no_source,
        }, "survey place", wait=120)
        if job.get("status") != "done":
            return show(job)
        result = job["result"]
        with open(args.out, "w") as fh:
            json.dump(result, fh, indent=2)
        place = result.get("place", {})
        totals = result.get("totals", {})
        print(f"[ok] surveyed '{place.get('name')}' (placeId {place.get('placeId')})")
        print(f"     {totals.get('instancesVisited')} instances, "
              f"{totals.get('scripts')} scripts, "
              f"{totals.get('sourceBytesReturned')} bytes of source")
        if totals.get("scriptsWithheld"):
            print(f"     {totals['scriptsWithheld']} scripts over budget — "
                  f"pull them with: arena_studio.py reads <path> ...")
        if totals.get("visitCapHit"):
            print("     NOTE: hit the instance visit cap; place is very large")
        census = result.get("census", {})
        top = sorted(census.items(), key=lambda kv: -kv[1])[:8]
        print("     top classes: " + ", ".join(f"{k}x{v}" for k, v in top))
        print(f"     full JSON -> {args.out}")
        return 0

    if args.cmd == "console":
        job = submit("console", {"limit": args.limit}, "console", wait=30)
        if job.get("status") != "done":
            return show(job)
        for line in job["result"].get("lines", []):
            print(f"  {line['type'][:4]:<4} {line['message']}")
        return 0

    if args.cmd == "reads":
        job = submit("read_scripts", {"paths": args.paths}, "read scripts", wait=90)
        if job.get("status") != "done":
            return show(job)
        if args.out:
            with open(args.out, "w") as fh:
                json.dump(job["result"], fh, indent=2)
            print(f"[ok] {len(job['result']['scripts'])} scripts -> {args.out}")
        else:
            for entry in job["result"]["scripts"]:
                print(f"----- {entry['path']} -----")
                print(entry.get("source") or entry.get("error"))
        return 0

    if args.cmd == "ping":
        return show(submit("ping", {}, "ping", wait=30))
    if args.cmd == "run":
        return show(submit("run_luau", {"code": args.code}, "inline luau"))
    if args.cmd == "runfile":
        code = open(args.file).read()
        return show(submit("run_luau", {"code": code}, os.path.basename(args.file)))
    if args.cmd == "build":
        spec = json.load(open(args.file))
        return show(submit("build", spec, os.path.basename(args.file)))
    if args.cmd == "inspect":
        return show(submit("inspect", {"path": args.path, "depth": args.depth},
                           f"inspect {args.path}"))
    if args.cmd == "read":
        return show(submit("read_script", {"path": args.path}, f"read {args.path}"))
    if args.cmd == "script":
        source = open(args.file).read()
        return show(submit("write_script", {
            "parent": args.parent, "name": args.name,
            "className": args.cls, "source": source}, f"write {args.name}"))
    if args.cmd == "delete":
        return show(submit("delete", {"path": args.path}, f"delete {args.path}"))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
