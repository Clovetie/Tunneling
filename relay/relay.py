#!/usr/bin/env python3
"""
relay.py — agent-side CLI for the Arena <-> Roblox Studio relay.

Runs *inside the sandbox* against the local relay server (default
http://127.0.0.1:8787), reading the shared token from .relay-state/relay.json
(written by relay_server.py).

    python3 relay/relay.py state
    python3 relay/relay.py ping
    python3 relay/relay.py runfile jobs/baseline_survey.lua --wait 90 --note "inventory"
    python3 relay/relay.py run "return game.Name"
    python3 relay/relay.py job '{"type":"survey","payload":{"depth":2},"wait":60}'
    python3 relay/relay.py wait <job-id>
    python3 relay/relay.py tail            # follow the relay's event log

The value returned by run_luau is `result.result.returned` — a string; if the
Luau returned JSON, it is parsed here automatically.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parent / ".relay-state" / "relay.json"
DEFAULT_PORT = 8787


def load_config():
    cfg = {"token": os.environ.get("RELAY_TOKEN", ""), "port": DEFAULT_PORT}
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text()))
        except (OSError, json.JSONDecodeError):
            pass
    if os.environ.get("RELAY_URL"):
        cfg["url"] = os.environ["RELAY_URL"]
    return cfg


def base_url(cfg):
    url = os.environ.get("RELAY_BASE")
    if url:
        return url.rstrip("/")
    return f"http://127.0.0.1:{cfg.get('port', DEFAULT_PORT)}"


def call(cfg, method, path, payload=None, timeout=170):
    url = base_url(cfg) + path
    url += ("&" if "?" in url else "?") + "token=" + urllib.parse.quote(cfg["token"])
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "body": exc.read().decode()[:400]}
    except urllib.error.URLError as exc:
        return {"error": f"relay not reachable at {base_url(cfg)} ({exc.reason})"}


def submit(cfg, kind, payload, note=None, wait=60):
    return call(cfg, "POST", "/api/jobs",
                {"type": kind, "payload": payload, "note": note, "wait": wait})


def show_result(job):
    """Print the local bridge's answer the way arena_studio.py would."""
    if "error" in job and not job.get("response"):
        print(json.dumps(job, indent=2)[:2000])
        return 1
    resp = job.get("response") or {}
    status = resp.get("status") or job.get("status")
    if status == "accepted":
        print(json.dumps({"id": job["id"], "status": "accepted",
                          "note": "fire-and-forget (wait=0): the job was queued in Studio, "
                                  "but the result was not awaited"},
                         indent=2))
        return 0
    if status == "done":
        result = resp.get("result") or {}
        returned = result.get("returned")
        if isinstance(returned, str):
            try:
                returned = json.loads(returned)
            except json.JSONDecodeError:
                pass
        print(json.dumps({"id": job["id"], "status": "done",
                          "ms": job.get("ms"), "returned": returned,
                          "result": {k: v for k, v in result.items() if k != "returned"}},
                         indent=2, ensure_ascii=False)[:4000])
        return 0
    print(json.dumps({"id": job.get("id"), "status": resp.get("status"),
                      "error": resp.get("error") or job.get("error"),
                      "response": resp}, indent=2, ensure_ascii=False)[:4000])
    return 1


def cmd_state(cfg, args):
    st = call(cfg, "GET", "/api/state")
    report = st.get("last_report") or {}
    health = report.get("health") or {}
    studio = health.get("studio") or {}
    print(json.dumps({
        "relay": {"url": st.get("relay_url"), "sse_clients": st.get("sse_clients"),
                  "counts": st.get("counts")},
        "clients": st.get("clients"),
        "last_report": {"ago_s": round(time.time() - report["at"], 1) if report.get("at") else None,
                        "where": report.get("where"), "ok": report.get("ok"),
                        "error": report.get("error")},
        "studio": {"connected": health.get("studio_connected"),
                   "place": studio.get("place"),
                   "plugin": studio.get("client"),
                   "last_seen_ago_s": round(time.time() - studio["last_seen"], 1)
                   if studio.get("last_seen") else None},
        "jobs": st.get("jobs", [])[:8],
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_run(cfg, args, code=None, file=None):
    if file:
        code = Path(file).read_text(encoding="utf-8")
    job = submit(cfg, "run_luau", {"code": code}, args.note, args.wait)
    return show_result(job)


def cmd_raw(cfg, args):
    envelope = json.loads(args.json) if args.json.strip().startswith("{") else \
        json.loads(Path(args.json).read_text(encoding="utf-8"))
    envelope.setdefault("wait", args.wait)
    envelope.setdefault("note", "relay cli")
    job = call(cfg, "POST", "/api/jobs", envelope)
    return show_result(job)


def cmd_wait(cfg, args):
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        job = call(cfg, "GET", f"/api/result/{args.id}")
        if job.get("status") in ("done", "error"):
            return show_result(job)
        time.sleep(1)
    print(json.dumps({"error": "timeout waiting for " + args.id}, indent=2))
    return 1


def cmd_tail(cfg, args):
    path = HERE.parent / ".relay-state" / "events.jsonl"
    if not path.exists():
        print("no event log yet")
        return 1
    with path.open(encoding="utf-8") as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if not line:
                time.sleep(0.5)
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            print(f"{time.strftime('%H:%M:%S', time.localtime(ev['t']))}  "
                  f"{ev['kind']:8} {json.dumps(ev['payload'], ensure_ascii=False)[:220]}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("state").set_defaults(fn=cmd_state)
    sub.add_parser("tail").set_defaults(fn=cmd_tail)

    p = sub.add_parser("ping")
    p.add_argument("--wait", type=float, default=30)
    p.set_defaults(fn=lambda cfg, a: show_result(submit(cfg, "ping", {}, "relay ping", a.wait)))

    p = sub.add_parser("run")
    p.add_argument("code")
    p.add_argument("--wait", type=float, default=60)
    p.add_argument("--note", default="relay cli")
    p.set_defaults(fn=lambda cfg, a: cmd_run(cfg, a, code=a.code))

    p = sub.add_parser("runfile")
    p.add_argument("file")
    p.add_argument("--wait", type=float, default=90)
    p.add_argument("--note", default=None)
    p.set_defaults(fn=lambda cfg, a: cmd_run(cfg, a, file=a.file))

    p = sub.add_parser("job")
    p.add_argument("json")
    p.add_argument("--wait", type=float, default=60)
    p.set_defaults(fn=cmd_raw)

    p = sub.add_parser("survey")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--wait", type=float, default=90)
    p.set_defaults(fn=lambda cfg, a: show_result(
        submit(cfg, "survey", {"depth": a.depth}, "relay survey", a.wait)))

    p = sub.add_parser("wait")
    p.add_argument("id")
    p.add_argument("--timeout", type=float, default=120)
    p.set_defaults(fn=cmd_wait)

    args = ap.parse_args()
    cfg = load_config()
    if not cfg.get("token"):
        print("no relay token: .relay-state/relay.json missing and RELAY_TOKEN unset")
        return 2
    if args.cmd == "tail":
        return args.fn(cfg, args)
    if args.cmd == "state":
        return args.fn(cfg, args)
    return args.fn(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
