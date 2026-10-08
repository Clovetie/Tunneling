#!/usr/bin/env python3
r"""
connect.py - one command to attach this machine to an Arena session.

You should never have to assemble a client command again:

    python .\\connect.py --url https://8787-<sandbox>.e2b.app

What it does, in order, telling you as it goes:

  1. finds the tokens (relay/bridge token, and the preview's traffic token)
  2. checks the relay is reachable - and if the preview gate blocks us, it
     explains exactly which token is missing and where it comes from
  3. checks your local bridge on 127.0.0.1:8077; if it is not running it starts
     `python server.py` for you in a new window
  4. re-downloads relay/poll_local.py through the gate, so the client is always
     the current one (an old copy is what made a previous session look broken)
  5. runs the client and stays attached until you press Ctrl+C

Options:
    --url URL            the relay URL (required unless RELAY_URL is set)
    --token TOKEN        relay/bridge token (default: found on disk)
    --traffic-token TOK  preview gate token (default: cached from last time)
    --no-start           do not start server.py, just report that it is down
    --port N             bridge port (default 8077)
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELAY_DIR = HERE / "relay"
BRIDGE_DIR = HERE / "roblox-bridge"
POLL_LOCAL = RELAY_DIR / "poll_local.py"

TRAFFIC_NAMES = [".arena-traffic-token"]
TOKEN_NAMES = ["bridge.token"]


def say(msg, mark="  "):
    print(f"{mark.ljust(2)}{msg}", flush=True)


def step(title):
    print(f"\n== {title}", flush=True)


def find_first(paths, names):
    for base in paths:
        for name in names:
            candidate = base / name
            try:
                if candidate.is_file():
                    text = candidate.read_text(encoding="utf-8").strip()
                    if text:
                        return text, candidate
            except OSError:
                continue
    return "", None


def token_search_paths():
    cwd = Path.cwd()
    return [RELAY_DIR, HERE, BRIDGE_DIR, cwd, cwd / "relay", cwd / "roblox-bridge",
            Path.home() / "Downloads"]


def http(url, method="GET", payload=None, timeout=30, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "{}")


def relay_headers(traffic):
    return {"E2b-Traffic-Access-Token": traffic} if traffic else {}


def gate_explanation(body, traffic):
    say("the preview gate refused us.", "!")
    for line in (body or "").splitlines():
        if line.strip():
            say("  gate says: " + line.strip()[:200])
    if traffic:
        say("we sent the cached preview token but the gate still said no:", "!")
        say("  it probably belongs to an older sandbox. Ask the agent for")
        say("  'relay.py clientline', or reload the Arena preview page and try again.")
    else:
        say("no preview token available yet. Either:")
        say("  * reload the Arena preview page once (the relay captures it), then ask")
        say("    the agent to run: python3 relay/relay.py clientline")
        say("  * or pass --traffic-token <value>")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("RELAY_URL"))
    ap.add_argument("--token", default=os.environ.get("BRIDGE_TOKEN"))
    ap.add_argument("--traffic-token", default=os.environ.get("E2B_TRAFFIC_TOKEN"))
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--once", action="store_true",
                    help="check the whole chain and exit (do not stay attached)")
    args = ap.parse_args()

    if not args.url:
        print("no --url. The agent prints the whole command for you; it looks like:")
        print("  python .\\connect.py --url https://8787-<sandbox>.e2b.app")
        return 2
    url = args.url.rstrip("/")

    step("tokens")
    token = args.token
    if not token:
        token, token_file = find_first(token_search_paths(), TOKEN_NAMES)
        if token:
            say(f"relay/bridge token from {token_file}")
    if not token:
        say("no relay token found and none passed.", "!")
        say("pass --token <24-hex>, or keep a bridge.token file in this folder,")
        say("in roblox-bridge\\, or in ..\\roblox-bridge\\.")
        return 2

    traffic = args.traffic_token
    if not traffic:
        traffic, traffic_file = find_first(token_search_paths(), TRAFFIC_NAMES)
        if traffic:
            say(f"preview token from {traffic_file}")
    if not traffic:
        say("no preview token cached yet (fine if the preview is not gated).")

    step("relay")
    try:
        state = http(f"{url}/api/state?token={urllib.parse.quote(token)}",
                     headers=relay_headers(traffic), timeout=30)
        say(f"relay v{state.get('version')} reachable, "
            f"{state.get('sse_clients', 0)} browser client(s) attached")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if exc.code == 403:
            gate_explanation(body, traffic)
        elif exc.code == 401:
            say("the relay rejected the token (401). It wants the 24-hex token the", "!")
            say("agent shows you; a stale value usually still lives in bridge.token.", "!")
        else:
            say(f"relay answered HTTP {exc.code}: {body[:200]}", "!")
        return 2
    except urllib.error.URLError as exc:
        say(f"cannot reach {url} ({exc.reason}).", "!")
        say("check the URL, or ask the agent whether the relay is still running.")
        return 2

    step("local bridge")
    bridge = f"http://127.0.0.1:{args.port}"
    healthy = False
    for attempt in range(2):
        try:
            health = http(f"{bridge}/api/health", timeout=10)
            healthy = True
            break
        except Exception:                                     # noqa: BLE001
            if attempt == 0 and not args.no_start:
                server = BRIDGE_DIR / "server.py"
                if server.is_file():
                    say("not running - starting python server.py ...")
                    flags = 0
                    quiet = {}
                    if platform.system() == "Windows":
                        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
                    if not flags:
                        # no separate console: detach completely, or the child
                        # inherits our stdout, keeps the pipe open, and hangs
                        # whatever ran us (bitten while testing this script).
                        quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                                 "stdin": subprocess.DEVNULL}
                    subprocess.Popen([sys.executable, str(server)], cwd=str(server.parent),
                                     creationflags=flags, start_new_session=not flags, **quiet)
                    say("waiting for it to answer ...")
                    for _ in range(10):
                        time.sleep(1)
                        try:
                            http(f"{bridge}/api/health", timeout=3)
                            break
                        except Exception:                          # noqa: BLE001
                            continue
                else:
                    say(f"no server.py at {server} - is this the right folder?", "!")
    if healthy:
        studio = health.get("studio") or {}
        if health.get("studio_connected"):
            say(f"bridge up - Studio CONNECTED, place \"{studio.get('place')}\", "
                f"plugin {studio.get('client')}")
        else:
            say("bridge up, but Studio has not polled: open the place (the plugin", "!")
            say("loads with the DataModel), then run this again.", "!")
            return 3
    else:
        say("bridge still down. Start it yourself in another window:", "!")
        say(f"  cd \"{BRIDGE_DIR}\"")
        say("  python server.py")
        return 3

    step("client")
    try:
        data = urllib.request.urlopen(
            urllib.request.Request(f"{url}/poll_local.py",
                                   headers=relay_headers(traffic)), timeout=30).read()
        if b"E2b-Traffic-Access-Token" in data:
            RELAY_DIR.mkdir(exist_ok=True)
            POLL_LOCAL.write_bytes(data)
            say(f"refreshed relay/poll_local.py ({len(data)} bytes) from the relay")
        else:
            say("the relay served a file that does not look like the client - keeping", "!")
            say("the local copy. Ask the agent to check the relay.")
    except Exception as exc:                                  # noqa: BLE001
        if POLL_LOCAL.is_file():
            say(f"could not refresh the client ({exc}); using the local copy")
        else:
            say(f"no client on disk and could not fetch one: {exc}", "!")
            return 2

    if not POLL_LOCAL.is_file():
        say(f"relay/poll_local.py is missing.", "!")
        return 2

    cmd = [sys.executable, str(POLL_LOCAL), "--url", url, "--token", token]
    if traffic:
        cmd += ["--traffic-token", traffic]
    cmd += ["--bridge", bridge]
    if args.once:
        cmd.append("--once")
    print("\n== attaching (Ctrl+C stops, and leaves your window clean)\n", flush=True)
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped.")
        sys.exit(0)
