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

Publishing the game code (the reason this exists):

    python3 relay/relay.py drift           # live vs repo, per file, read-only
    python3 relay/relay.py push            # generate the job, run the DRY pass
    python3 relay/relay.py push --apply    # actually publish what differs

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


def run_job(cfg, kind, payload, note=None, wait=90):
    """Submit a job and return its full record once it is finished."""
    job = submit(cfg, kind, payload, note, wait)
    if job.get("status") in ("done", "error", "accepted"):
        return job
    if "id" in job:                       # still queued: wait for it
        return call(cfg, "GET", f"/api/result/{job['id']}")
    return job


def returned_json(job):
    """The `returned` string, parsed if it is JSON."""
    resp = job.get("response") or {}
    value = (resp.get("result") or {}).get("returned")
    if not isinstance(value, str):
        return None, job
    try:
        return json.loads(value), job
    except json.JSONDecodeError:
        return None, job


def cmd_drift(cfg, args):
    """Live place vs this repo, per file - read-only."""
    sys.path.insert(0, str(HERE.parent / "jobs"))
    from repo_hashes import digest, MANIFEST  # noqa: E402

    source = Path(args.file) if args.file else HERE.parent / "jobs" / "drift_audit.lua"
    job = run_job(cfg, "run_luau", {"code": source.read_text(encoding="utf-8")},
                  "drift audit", args.wait)
    data, job = returned_json(job)
    if data is None:
        print(json.dumps(job, indent=2)[:1500])
        return 1

    checks = data.get("checks") or {}
    live = {entry.get("name"): entry for entry in data.get("files") or []}
    repo = {key: digest(rel) for rel, key in MANIFEST}

    rows, same, diff = [], 0, 0
    for key, (want_hash, want_bytes) in repo.items():
        entry = live.pop(key, None)
        if entry is None:
            rows.append(("MISSING", key, "-", f"{want_bytes}"))
            diff += 1
        elif entry.get("error"):
            rows.append(("ERROR", key, entry["error"], f"{want_bytes}"))
            diff += 1
        elif entry.get("hash") == want_hash and entry.get("bytes") == want_bytes:
            same += 1
        else:
            rows.append(("differs", key,
                         f"live {entry.get('bytes')}B/{entry.get('hash')}",
                         f"repo {want_bytes}B/{want_hash}"))
            diff += 1
    for key, entry in live.items():
        rows.append(("EXTRA", key, entry.get("className") or "in the place", "not in the repo"))

    print(f"hash function self-check: {checks}  (both must be true)")
    for status, key, live_s, repo_s in rows:
        print(f"  {status:8} {key:36} {live_s:28} {repo_s}")
    print(f"{len(repo) - diff}/{len(repo)} files identical, {diff} difference(s)")
    if not (checks.get("a") and checks.get("hello")):
        print("WARNING: the job's hash self-check failed - treat these results as noise")
        return 1
    return 0 if diff == 0 else 2


def cmd_push(cfg, args):
    """Publish the repo's game code into the live place."""
    import subprocess
    jobs_dir = HERE.parent / "jobs"
    gen = subprocess.run([sys.executable, str(jobs_dir / "make_push_all.py"), "--both"],
                         capture_output=True, text=True)
    sys.stdout.write(gen.stdout)
    if gen.returncode != 0:
        sys.stderr.write(gen.stderr)
        return gen.returncode

    stage = jobs_dir / ("push_all.lua" if args.apply else "push_all_dry.lua")
    note = ("publish: nightloop -> place" if args.apply
            else "dry run: what would change in the place")
    job = run_job(cfg, "run_luau", {"code": stage.read_text(encoding="utf-8")}, note, args.wait)
    data, job = returned_json(job)
    if data is None:
        print(json.dumps(job, indent=2)[:1500])
        return 1

    summary = data.get("summary") or {}
    print(f"{'APPLIED' if args.apply else 'DRY RUN'} - summary: {summary}")
    for row in data.get("files") or []:
        if not isinstance(row, dict) or row.get("action") in ("unchanged",):
            continue
        detail = f"{row.get('action')}"
        if row.get("oldBytes") is not None:
            detail += f"  {row['oldBytes']}B -> {row.get('liveBytes')}B"
        if row.get("keptDisabled"):
            detail += "  (stayed Disabled)"
        if row.get("error"):
            detail += f"  ! {row['error']}"
        if row.get("bytesOk") is False or row.get("hashOk") is False:
            detail += f"  ! verify bytes={row.get('bytesOk')} hash={row.get('hashOk')}"
        print(f"  {str(row.get('key')):38} {detail}")
    if data.get("tuning"):
        print("live tuning:", json.dumps(data["tuning"], ensure_ascii=False))
    if isinstance(data.get("require"), dict):
        bad = {k: v for k, v in data["require"].items()
               if not (isinstance(v, dict) and v.get("ok"))}
        print("require: all ok" if not bad else f"require FAILED: {bad}")
    if not data.get("ok"):
        print("the job did not report ok - read the detail above before trusting the place")
        return 1
    if not args.apply:
        print("\nthat was the dry run. Re-run with --apply to publish.")
    return 0


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
        "preview_token": st.get("preview_token"),
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

    p = sub.add_parser("drift", help="live place vs repo, per file (read-only)")
    p.add_argument("--file", default=None, help="audit job to run (default jobs/drift_audit.lua)")
    p.add_argument("--wait", type=float, default=90)
    p.set_defaults(fn=cmd_drift)

    p = sub.add_parser("push", help="publish nightloop/** into the live place")
    p.add_argument("--apply", action="store_true",
                   help="actually publish (without it, only the dry pass runs)")
    p.add_argument("--wait", type=float, default=120)
    p.set_defaults(fn=cmd_push)

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
