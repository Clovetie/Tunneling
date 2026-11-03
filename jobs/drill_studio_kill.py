#!/usr/bin/env python3
"""
drill_studio_kill.py — G8: prove the job reaper survives a Studio crash.

The unit tests exercise the reaper with synthetic peers. This drill does it for
real: it enqueues a job that Studio will sit on, waits for a peer to claim it,
tells you to kill Studio (close the place, kill the plugin, or unplug the network),
and then watches whether the bridge requeues the job instead of leaving it stuck
in `running` forever.

    python3 jobs/drill_studio_kill.py --token "$BRIDGE_TOKEN"
    python3 jobs/drill_studio_kill.py --url https://your-name.trycloudflare.com \
        --token "$BRIDGE_TOKEN" --json-out handoff/kill-switch-drill.json

Exit codes: 0 drill passed, 1 drill failed, 3 no Studio peer to drill with.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DRILL_CODE = "task.wait(25) return 'drill-finished'"


def http_json(base, path, token=None, method="GET", body=None, timeout=30):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def reaped_total(base, token):
    """Read bridge_jobs_reaped_total from the metrics JSON (0 when absent)."""
    try:
        metrics = http_json(base, "/api/metrics", token=token)
    except Exception:  # noqa: BLE001
        return None
    counters = metrics.get("counters") or {}
    for key, value in counters.items():
        if key.startswith("bridge_jobs_reaped_total"):
            return value if isinstance(value, (int, float)) else (value.get("value") if isinstance(value, dict) else None)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8077", help="Bridge base URL")
    parser.add_argument("--token", default=os.environ.get("BRIDGE_TOKEN", ""), help="Bridge token")
    parser.add_argument("--wait", type=float, default=90.0, help="How long to watch for the requeue (default 90s)")
    parser.add_argument("--json-out", default=None, help="Write the drill evidence here")
    args = parser.parse_args(argv)

    base = args.url.rstrip("/")
    timeline = []

    def note(event, **extra):
        entry = {"t": round(time.time() - started, 2), "event": event, **extra}
        timeline.append(entry)
        print(f"  [{entry['t']:>6.1f}s] {event}" + (f" {extra}" if extra else ""))
        return entry

    started = time.time()
    print(f"Arena bridge kill-switch drill · {base} · {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    try:
        peers = http_json(base, "/api/peers", token=args.token)
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot reach {base}/api/peers: {exc}", file=sys.stderr)
        return 3
    if not (peers.get("peers") or []):
        print("error: no Studio peer connected — open the place, then re-run the drill.", file=sys.stderr)
        print("       (the reaper only has something to do when a peer disappears mid-job)", file=sys.stderr)
        return 3
    note("peer-present", peers=len(peers.get("peers") or []))

    before = reaped_total(base, args.token)
    note("metrics-before", reapedTotal=before)

    try:
        job, _ = None, None
        job = http_json(base, "/api/jobs", token=args.token, method="POST", body={
            "type": "execute_luau",
            "payload": {"code": DRILL_CODE, "dm": "edit"},
            "wait": 0,
            "timeout": 600,
            "note": "drill_studio_kill",
        })
    except urllib.error.HTTPError as exc:
        print(f"error: cannot enqueue the drill job: HTTP {exc.code} {exc.read()[:200]!r}", file=sys.stderr)
        return 1
    job_id = job.get("id")
    note("job-enqueued", jobId=job_id)

    print("\n  >>> Stop Roblox Studio now (close the place or disable the plugin).")
    print("      The drill waits for the bridge to notice and requeue the job.\n")

    statuses = []
    last_status = None
    requeued = False
    saw_running = False
    killed_at = None
    while time.time() - started < args.wait:
        try:
            current = http_json(base, f"/api/jobs/{job_id}", token=args.token)
        except Exception as exc:  # noqa: BLE001
            note("poll-error", error=str(exc))
            time.sleep(2)
            continue
        status = current.get("status")
        if status != last_status:
            note("job-status", status=status, attempts=current.get("attempts"))
            statuses.append({"t": round(time.time() - started, 2), "status": status})
            last_status = status
            if status == "running":
                saw_running = True
                killed_at = killed_at or time.time()
        if status == "queued" and saw_running:
            requeued = True
            note("job-requeued", attempts=current.get("attempts"), note=current.get("note"))
            break
        if status in ("done", "error", "cancelled") and saw_running:
            note("job-finished", status=status, error=current.get("error"))
            break
        time.sleep(1.0)

    after = reaped_total(base, args.token)
    delta = (after - before) if (after is not None and before is not None) else None
    note("metrics-after", reapedTotal=after, delta=delta)

    # The job may also have been requeued and then re-claimed by a reconnected peer.
    try:
        final = http_json(base, f"/api/jobs/{job_id}", token=args.token)
    except Exception:  # noqa: BLE001
        final = {}
    final_status = final.get("status")
    attempts = int(final.get("attempts") or 0)

    passed = bool(requeued or (attempts > 0 and final_status in ("queued", "running", "done")) or (delta or 0) > 0)
    verdict = "pass" if passed else "fail"
    print(f"\nverdict: {verdict}  (final status={final_status}, attempts={attempts}, reaped delta={delta})")
    if not passed:
        print("  The job never left `running`. Check that the bridge is still alive, that the reaper is "
              "running (it starts with the server), and that Studio really stopped heart-beating.")

    evidence = {
        "kind": "kill-switch-drill",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "url": base,
        "verdict": verdict,
        "jobId": job_id,
        "finalStatus": final_status,
        "attempts": attempts,
        "reapedTotal": {"before": before, "after": after, "delta": delta},
        "requeued": requeued,
        "timeline": timeline,
        "durationSec": round(time.time() - started, 2),
    }
    if args.json_out:
        path = os.path.abspath(args.json_out)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, indent=2)
        print(f"[written] {path}", file=sys.stderr)
    else:
        print("\nPaste this under 'Kill-switch drill' in docs/OPS.md:\n")
        print(json.dumps(evidence, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
