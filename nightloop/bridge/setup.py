#!/usr/bin/env python3
"""
setup.py — one command to get the Arena <-> Roblox Studio bridge running.

What it does:
  1. creates (or reuses) a shared secret in bridge.token
  2. writes ArenaBridge.lua into your Roblox Studio plugins folder, with the
     token already baked in — no file editing
  3. starts the bridge server on :8077
  4. with --tunnel, starts cloudflared and prints the public URL to hand to Arena

Usage:
    python3 setup.py                 # install plugin + run server (local only)
    python3 setup.py --tunnel        # ... and expose it so Arena can reach it
    python3 setup.py --install-only  # just place the plugin, don't run anything
    python3 setup.py --rotate        # new token (re-installs the plugin)

Stop everything with Ctrl+C.
"""

import argparse
import os
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / "bridge.token"
PLUGIN_SRC = HERE / "ArenaBridge.lua"
DIST = HERE / "dist"

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
NGROK_RE = re.compile(r"https://[a-z0-9-]+\.ngrok[a-z0-9.-]*\.(?:app|io)")

CYAN, GREEN, YELLOW, RED, DIM, BOLD, OFF = (
    "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
)
if platform.system() == "Windows" and not os.environ.get("WT_SESSION"):
    CYAN = GREEN = YELLOW = RED = DIM = BOLD = OFF = ""


def say(msg, colour=""):
    print(f"{colour}{msg}{OFF}", flush=True)


# ---------------------------------------------------------------------------
# token
# ---------------------------------------------------------------------------

def get_token(rotate=False):
    if TOKEN_FILE.exists() and not rotate:
        token = TOKEN_FILE.read_text().strip()
        if token:
            return token
    token = secrets.token_hex(12)
    TOKEN_FILE.write_text(token)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return token


# ---------------------------------------------------------------------------
# plugin install
# ---------------------------------------------------------------------------

def plugins_dir():
    system = platform.system()
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "Roblox" / "Plugins"
    elif system == "Darwin":
        candidates = [
            Path.home() / "Documents" / "Roblox" / "Plugins",
            Path.home() / "Library" / "Application Support" / "Roblox" / "Plugins",
        ]
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]
    return None


def install_plugin(token, port, explicit_dir=None):
    source = PLUGIN_SRC.read_text(encoding="utf-8")
    source = re.sub(r'local BRIDGE_URL = "[^"]*"',
                    f'local BRIDGE_URL = "http://127.0.0.1:{port}"', source, count=1)
    source = re.sub(r'local BRIDGE_TOKEN = "[^"]*"',
                    f'local BRIDGE_TOKEN = "{token}"', source, count=1)

    target_dir = Path(explicit_dir) if explicit_dir else plugins_dir()
    if target_dir:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "ArenaBridge.lua"
            target.write_text(source, encoding="utf-8")
            say(f"  plugin installed -> {target}", GREEN)
            return True
        except OSError as exc:
            say(f"  could not write to {target_dir} ({exc})", YELLOW)

    DIST.mkdir(exist_ok=True)
    target = DIST / "ArenaBridge.lua"
    target.write_text(source, encoding="utf-8")
    say(f"  plugin written -> {target}", YELLOW)
    say("  copy it into Studio's plugins folder:", YELLOW)
    say("    Studio -> Plugins tab -> Plugins Folder", DIM)
    return False


# ---------------------------------------------------------------------------
# processes
# ---------------------------------------------------------------------------

def find_binary(name):
    """shutil.which, then the places Windows installers actually put things.

    winget broadcasts a PATH update that existing shells never see, so a
    freshly-installed cloudflared is invisible to the terminal that ran winget.
    """
    found = shutil.which(name)
    if found:
        return found
    if platform.system() != "Windows":
        return None

    exe = f"{name}.exe"
    candidates = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            candidates.append(Path(base) / name / exe)
            candidates.append(Path(base) / "Cloudflare" / name / exe)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Microsoft" / "WinGet" / "Links" / exe)
        candidates.append(Path(local) / "Microsoft" / "WindowsApps" / exe)
    for path in candidates:
        if path.exists():
            return str(path)

    # last resort: shallow scan of Program Files
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if not base:
            continue
        try:
            for child in Path(base).iterdir():
                if name in child.name.lower() or "cloudflare" in child.name.lower():
                    hit = child / exe
                    if hit.exists():
                        return str(hit)
        except OSError:
            pass
    return None


CHILDREN = []


def spawn(cmd, name, pattern=None, on_match=None):
    say(f"  starting {name}: {DIM}{' '.join(cmd)}{OFF}")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    CHILDREN.append(proc)

    def pump():
        for line in proc.stdout:
            line = line.rstrip()
            if pattern and on_match:
                found = pattern.search(line)
                if found:
                    on_match(found.group(0))
            if line:
                print(f"{DIM}[{name}]{OFF} {line}", flush=True)

    threading.Thread(target=pump, daemon=True).start()
    return proc


def shutdown(*_):
    say("\nshutting down…", YELLOW)
    for proc in CHILDREN:
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
    time.sleep(0.6)
    for proc in CHILDREN:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
    sys.exit(0)


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument("--tunnel", action="store_true",
                    help="expose the bridge publicly via cloudflared")
    ap.add_argument("--install-only", action="store_true")
    ap.add_argument("--rotate", action="store_true", help="generate a new token")
    ap.add_argument("--plugins-dir", default=None)
    args = ap.parse_args()

    signal.signal(signal.SIGINT, shutdown)

    say(f"\n{BOLD}Arena <-> Roblox Studio bridge{OFF}")
    token = get_token(args.rotate)
    say(f"  token: {CYAN}{token}{OFF}  {DIM}(stored in bridge.token){OFF}")

    install_plugin(token, args.port, args.plugins_dir)

    if args.install_only:
        say("\nNow restart Studio and click the Arena Bridge button.", GREEN)
        say(f"Then run: python3 setup.py --tunnel\n")
        return

    spawn([sys.executable, str(HERE / "server.py"),
           "--port", str(args.port), "--token", token], "bridge")
    time.sleep(1.0)

    public_url = {"value": None}

    if args.tunnel:
        exe = find_binary("cloudflared")
        if exe:
            def got_url(url):
                if public_url["value"]:
                    return
                public_url["value"] = url
                print()
                say("=" * 68, GREEN)
                say("  Paste these two lines to Arena:", BOLD)
                say(f"    URL:   {url}", CYAN)
                say(f"    TOKEN: {token}", CYAN)
                say("=" * 68, GREEN)
                print()

            spawn([exe, "tunnel", "--url", f"http://localhost:{args.port}"],
                  "tunnel", TUNNEL_RE, got_url)
        elif find_binary("ngrok"):
            say("\n  cloudflared not found, but ngrok is installed. Run this", YELLOW)
            say("  in a SECOND terminal, then send Arena the https URL:", YELLOW)
            say(f"    ngrok http {args.port}", DIM)
        else:
            say("\n  cloudflared not found. Install it:", YELLOW)
            say("    Windows:  winget install --id Cloudflare.cloudflared", DIM)
            say("    macOS:    brew install cloudflared", DIM)
            say("  …or use ngrok instead, in a second terminal:", YELLOW)
            say(f"    ngrok http {args.port}", DIM)
            say("  then paste the https URL + the token above to Arena.", YELLOW)

    print()
    say(f"  dashboard:  http://localhost:{args.port}", GREEN)
    if platform.system() == "Windows":
        say(f'  self-test:  $env:BRIDGE_TOKEN="{token}"; '
            f'python arena_studio.py ping', DIM)
    else:
        say(f"  self-test:  BRIDGE_TOKEN={token} python3 arena_studio.py ping", DIM)
    say("\n  1. restart Roblox Studio", BOLD)
    say("  2. click the 'Arena Bridge' toolbar button", BOLD)
    say("  3. the dashboard should flip to 'Studio connected'", BOLD)
    say("\n  Ctrl+C to stop everything.\n", DIM)

    while True:
        time.sleep(1)
        for proc in CHILDREN:
            if proc.poll() is not None:
                say(f"a child process exited with {proc.returncode}", RED)
                shutdown()


if __name__ == "__main__":
    main()
