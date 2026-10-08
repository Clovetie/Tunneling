#!/usr/bin/env python3
"""
selftest.py — stand-in for the user's machine, used to prove the relay works
end to end inside the sandbox before asking the user to run anything.

It plays the role of `relay.ps1` / the browser page: poll the relay, POST each
job into a *local* bridge, POST the answer back. Point --bridge at a real
`server.py` (with `mock_studio.py` polling it) to exercise the full chain with
no user involved:

    python3 roblox-bridge/server.py --port 8077 --token dev &
    BRIDGE_URL=http://127.0.0.1:8077 BRIDGE_TOKEN=dev python3 roblox-bridge/mock_studio.py &
    python3 relay/relay_server.py --port 8787 --token dev &
    python3 relay/selftest.py --relay http://127.0.0.1:8787 --token dev \
        --bridge http://127.0.0.1:8077 --bridge-token dev
"""

import argparse
import json
import time
import urllib.error
import urllib.request

CLIENT = "selftest"


def call(url, method="GET", payload=None, timeout=140, raw=False):
    data = None
    if payload is not None:
        data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode()
    return body if raw else json.loads(body or "{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--relay", default="http://127.0.0.1:8787")
    ap.add_argument("--token", required=True)
    ap.add_argument("--bridge", default="http://127.0.0.1:8077")
    ap.add_argument("--bridge-token", default=None)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()
    bridge_token = args.bridge_token or args.token
    relay = args.relay.rstrip("/")

    try:                       # report the "local" bridge health, like the real client
        health = call(f"{args.bridge}/api/health?token={bridge_token}", timeout=10)
        call(f"{relay}/api/report?token={args.token}", "POST",
             {"where": "selftest", "ok": True, "health": health})
        connected = health.get("studio_connected")
        print(f"[selftest] local bridge ok (studio_connected={connected})")
    except Exception as exc:                       # noqa: BLE001
        print(f"[selftest] local bridge unreachable: {exc}")

    print(f"[selftest] attached to {relay} as {CLIENT}")
    while True:
        try:
            jobs = call(f"{relay}/api/jobs?token={args.token}&client={CLIENT}&kind=powershell",
                        timeout=20)["jobs"]
        except urllib.error.URLError as exc:
            print(f"[selftest] relay unreachable: {exc}")
            if args.once:
                return 1
            time.sleep(2)
            continue

        for job in jobs:
            t0 = time.time()
            print(f"[selftest] job {job['id']} {job['type']} …")
            result = None
            error = None
            try:
                result = call(f"{args.bridge}/api/jobs?token={bridge_token}",
                              "POST", job["body"])
            except Exception as exc:                      # noqa: BLE001
                error = str(exc)
            ms = int((time.time() - t0) * 1000)
            payload = {"id": job["id"], "response": result, "error": error, "ms": ms}
            call(f"{relay}/api/result?token={args.token}", "POST", payload)
            state = (result or {}).get("status")
            print(f"[selftest] job {job['id']} -> {state or error} ({ms} ms)")

        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
