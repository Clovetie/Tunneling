#!/usr/bin/env python3
"""
poll_local.py  -  the Arena relay client for YOUR PC (no PowerShell involved).

Same job as relay.ps1, in Python: poll Arena's relay, run each job against your
local bridge, post the answer back. Python is already on your machine (it runs
server.py), and this file is plain ASCII, so there is nothing for Windows
PowerShell 5.1 to mis-decode, no quoting rules, and no execution policy.

    # from a repo checkout (finds roblox-bridge/bridge.token by itself):
    python .\relay\poll_local.py --url https://8787-<sandbox>.e2b.app

    # or from the bridge folder, next to bridge.token:
    python .\poll_local.py --url https://8787-<sandbox>.e2b.app

    # if the preview URL is token-gated (HTTP 403), get the value from the
    # relay page: Troubleshooting box -> "Copy preview token", then:
    python poll_local.py --url https://8787-<sandbox>.e2b.app --traffic-token <value>

    # you only do that once: the value is cached next to this script
    # (.arena-traffic-token) and reused automatically. --forget-traffic-token
    # clears it.

It finds the token by itself, in this order (the first hit wins):
    --token / --bridge-token, $BRIDGE_TOKEN, <script dir>\bridge.token,
    <script dir>\..\roblox-bridge\bridge.token (the repo layout),
    <cwd>\bridge.token, <cwd>\roblox-bridge\bridge.token
So running it from a repo checkout works without copying anything:
    python relay\poll_local.py --url https://8787-<sandbox>.e2b.app

Options:
    --url URL             the relay (Arena preview) URL. Required.
    --token TOKEN         relay token (default: found as above).
    --bridge URL          local bridge. Default http://127.0.0.1:8077
    --bridge-token TOKEN  local bridge token (default: found as above).
    --traffic-token TOK   the preview's e2b-traffic-access-token, when gated.
    --interval SECONDS    poll interval (default 2).
    --once                check both hops and exit (no polling).
    --quiet               only print job lines.

Ctrl+C stops it. Nothing is written to disk.
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


def log(msg):
    print(f"[relay] {msg}", flush=True)


def token_candidates():
    """Every place a bridge.token can sensibly live, best guess first.

    The user works out of a ZIP of the repo (relay/poll_local.py,
    roblox-bridge/bridge.token) and may or may not run this from a folder that
    has the token beside it, so guessing is the feature, not laziness.
    """
    cwd = Path.cwd()
    return [
        HERE / "bridge.token",
        HERE.parent / "roblox-bridge" / "bridge.token",
        HERE.parent / "bridge.token",
        cwd / "bridge.token",
        cwd / "roblox-bridge" / "bridge.token",
        HERE / "relay.token",
    ]


TRAFFIC_CACHE = HERE / ".arena-traffic-token"


def load_traffic_token():
    try:
        if TRAFFIC_CACHE.is_file():
            text = TRAFFIC_CACHE.read_text(encoding="utf-8").strip()
            if text:
                return text, str(TRAFFIC_CACHE)
    except OSError:
        pass
    return "", ""


def save_traffic_token(value):
    try:
        TRAFFIC_CACHE.write_text(value.strip(), encoding="utf-8")
        try:
            TRAFFIC_CACHE.chmod(0o600)
        except OSError:
            pass
        return True
    except OSError as exc:
        log(f"could not cache the preview token: {exc}")
        return False


def find_token_file():
    for candidate in token_candidates():
        try:
            if candidate.is_file():
                text = candidate.read_text().strip()
                if text:
                    return text, str(candidate)
        except OSError:
            continue
    return "", ""


def http(url, method="GET", payload=None, timeout=140):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body or "{}")


class Relay:
    def __init__(self, url, token, traffic=None):
        self.base = url.rstrip("/")
        self.token = token
        self.traffic = traffic

    def call(self, path, method="GET", payload=None, timeout=140):
        url = f"{self.base}{path}"
        sep = "&" if "?" in url else "?"
        url += f"{sep}token={urllib.parse.quote(self.token)}"
        if self.traffic:
            url += "&e2b-traffic-access-token=" + urllib.parse.quote(self.traffic)
        return http(url, method, payload, timeout)


class Bridge:
    def __init__(self, url, token):
        self.base = url.rstrip("/")
        self.token = token

    def health(self):
        url = f"{self.base}/api/health?token={urllib.parse.quote(self.token)}"
        return http(url, timeout=15)

    def job(self, body):
        url = f"{self.base}/api/jobs?token={urllib.parse.quote(self.token)}"
        return http(url, "POST", body, timeout=140)


def explain(exc, url):
    code = getattr(exc, "code", None)
    if code == 403:
        log("403 - the preview URL is token-gated (this is E2B's proxy, not the relay).")
        log("Get the value from the relay page: open the preview, expand")
        log("'Troubleshooting - and using the bridge without Arena', click")
        log("'Copy preview token', then re-run this command with")
        log("  --traffic-token <paste>")
        log("It is cached next to this script afterwards, so that is a one-time step.")
    elif code == 401:
        log("401 - the relay rejected the token. It wants the 24-hex token Arena")
        log("showed you; check bridge.token (it is usually the same value).")
    elif isinstance(exc, urllib.error.URLError):
        log(f"cannot reach {url}: {exc.reason}")
        log("Check the URL, or use the browser page instead.")
    else:
        log(f"{type(exc).__name__}: {exc}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("RELAY_URL"))
    ap.add_argument("--token", default=None)
    ap.add_argument("--bridge", default=os.environ.get("BRIDGE_URL", "http://127.0.0.1:8077"))
    ap.add_argument("--bridge-token", default=None)
    ap.add_argument("--traffic-token", default=os.environ.get("E2B_TRAFFIC_TOKEN"))
    ap.add_argument("--forget-traffic-token", action="store_true",
                    help="drop the cached preview token and exit")
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.forget_traffic_token:
        try:
            TRAFFIC_CACHE.unlink()
            print(f"removed {TRAFFIC_CACHE}")
        except OSError:
            print(f"nothing to remove at {TRAFFIC_CACHE}")
        return 0

    if not args.url:
        print("no --url: pass the relay URL Arena gave you (https://<port>-<sandbox>.e2b.app)")
        return 1

    traffic_cached, traffic_source = load_traffic_token()
    if args.traffic_token:
        traffic_source = "--traffic-token"
        if not traffic_cached or traffic_cached != args.traffic_token.strip():
            if save_traffic_token(args.traffic_token):
                log(f"cached the preview token in {TRAFFIC_CACHE} (reused next time)")
    else:
        args.traffic_token = traffic_cached

    file_token, token_source = find_token_file()
    relay_token = args.token or os.environ.get("BRIDGE_TOKEN") or file_token or ""
    bridge_token = args.bridge_token or file_token or relay_token
    if not relay_token:
        print("no token found. Looked at:")
        for candidate in token_candidates():
            print(f"  {candidate}")
        print("Pass --token <24-hex token>, set $BRIDGE_TOKEN, or keep bridge.token")
        print("next to this script / next to a roblox-bridge folder beside it.")
        return 1
    if not args.quiet:
        where = token_source or ("--token/--bridge-token" if args.token or args.bridge_token
                                 else "$BRIDGE_TOKEN")
        log(f"using token from {where}")

    relay = Relay(args.url, relay_token, args.traffic_token)
    bridge = Bridge(args.bridge, bridge_token)

    # 1. relay reachable?
    if args.traffic_token and traffic_source and not args.quiet:
        log(f"using preview token from {traffic_source}")
    try:
        state = relay.call("/api/state", timeout=30)
    except Exception as exc:                                 # noqa: BLE001
        log(f"CANNOT reach the relay at {args.url}")
        explain(exc, args.url)
        return 2
    log(f"relay v{state.get('version')} reachable - {state.get('sse_clients', 0)} browser client(s) attached")

    # 2. local bridge + Studio
    health = {}
    try:
        health = bridge.health()
        studio = health.get("studio") or {}
        if health.get("studio_connected"):
            log(f"local bridge ok - Studio CONNECTED, place \"{studio.get('place')}\", "
                f"plugin {studio.get('client')}")
        else:
            log("local bridge ok, but Studio has not polled (Studio closed, or plugin off)")
        relay.call("/api/report", "POST",
                   {"where": "python", "ok": True, "health": health}, timeout=20)
    except Exception as exc:                                 # noqa: BLE001
        log(f"local bridge NOT reachable at {args.bridge}: {exc}")
        log("start it with:  python server.py")
        try:
            relay.call("/api/report", "POST",
                       {"where": "python", "ok": False, "error": str(exc)}, timeout=20)
        except Exception:                                    # noqa: BLE001
            pass

    if args.once:
        return 0

    # 3. the loop
    log("attached. Leave this window open; Ctrl+C to stop.")
    done = failed = polls = ticks = 0
    client = "py-%d" % (os.getpid())
    while True:
        polls += 1
        ticks += 1
        try:
            batch = relay.call(f"/api/jobs?client={client}&kind=powershell", timeout=30)
        except KeyboardInterrupt:
            raise
        except Exception as exc:                             # noqa: BLE001
            log(f"poll failed: {exc}")
            time.sleep(5)
            continue

        for job in batch.get("jobs") or []:
            t0 = time.time()
            log(f"job {job['id']} {job['type']} ...")
            result, error = None, None
            try:
                result = bridge.job(json.loads(job["body"]))
            except Exception as exc:                         # noqa: BLE001
                error = str(exc)
            ms = int((time.time() - t0) * 1000)
            if error is None and (result or {}).get("status") == "done":
                done += 1
                log(f"job {job['id']} done in {ms} ms")
            else:
                failed += 1
                log(f"job {job['id']} FAILED: {error or (result or {}).get('error') or result}")
            try:
                relay.call("/api/result", "POST",
                           {"id": job["id"], "response": result, "error": error, "ms": ms},
                           timeout=30)
            except Exception as exc:                         # noqa: BLE001
                log(f"could not return the result: {exc}")

        if ticks >= 8:
            ticks = 0
            if not args.quiet:
                log(f"watching - {polls} poll(s), {done} job(s) ok, {failed} failed")
            try:                                             # keep the sandbox's view fresh
                relay.call("/api/report", "POST",
                           {"where": "python", "ok": True, "health": bridge.health()}, timeout=20)
            except Exception:                                # noqa: BLE001
                pass

        if not (batch.get("jobs") or []):
            time.sleep(args.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[relay] stopped.", flush=True)
        sys.exit(0)
