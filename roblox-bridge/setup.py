#!/usr/bin/env python3
"""
setup.py — One command to install, configure, and run the Arena <-> Roblox
Studio Bridge & MCP Server (v5.3.1).

What it does:
  1. Creates or reuses a shared secret in bridge.token — and if bridge.token is
     missing (e.g. a freshly extracted branch ZIP!), automatically recovers the
     active token from an already-installed ArenaBridge.lua in your Studio
     Plugins directory so you NEVER hit HTTP 401 token mismatches!
  2. Writes ArenaBridge.lua v4.0.0 into your OS Roblox Studio Plugins folder with
     the port + token baked in (leaving the tracked repo source file clean).
  3. Starts server.py v5.3.1 on :8077 (serving Studio polling, REST CLI, Web
     Dashboard, AND Streamable HTTP MCP at http://127.0.0.1:8077/mcp).
  4. Optionally writes .mcp.json (`--write-mcp`) for Claude Code / Cursor / VS Code
     and/or opens a supervised cloudflared/ngrok tunnel (`--tunnel`).

Usage:
    python3 setup.py                        # install plugin + run server & HTTP MCP
    python3 setup.py --write-mcp            # also write .mcp.json config
    python3 setup.py --tunnel               # also expose via cloudflared tunnel
    python3 setup.py --install-only         # install/sync Studio plugin & exit
    python3 setup.py --rotate               # mint fresh token & reinstall plugin
    python3 setup.py --read-only            # run in Read-Only Inspector mode
"""

import argparse
import json
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
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / "bridge.token"
PLUGIN_SRC = HERE / "ArenaBridge.lua"
DIST = HERE / "dist"
PLACEHOLDER_TOKEN = "arena-demo-7f3a"

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
BAKED_TOKEN_RE = re.compile(r'local BRIDGE_TOKEN = "([^"]*)"')

CYAN, GREEN, YELLOW, RED, DIM, BOLD, OFF = (
    "\033[36m", "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
)
if platform.system() == "Windows" and not os.environ.get("WT_SESSION"):
    CYAN = GREEN = YELLOW = RED = DIM = BOLD = OFF = ""


def say(msg: str, colour: str = ""):
    print(f"{colour}{msg}{OFF}", flush=True)


# ---------------------------------------------------------------------------
# OS Plugin Directory Discovery
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


# ---------------------------------------------------------------------------
# Token Management (with automatic recovery from installed Studio plugin!)
# ---------------------------------------------------------------------------

def _token_works_against_running_server(token: str, port: int) -> bool:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/peers?token={token}", method="GET"
        )
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def recover_existing_installed_token(explicit_dir=None) -> str:
    """Prevent the HTTP 401 token mismatch trap when running in a fresh folder:
    inspect any already-installed ArenaBridge.lua in Studio's Plugins folder.
    """
    target_dir = Path(explicit_dir) if explicit_dir else plugins_dir()
    if not target_dir:
        return ""
    installed = target_dir / "ArenaBridge.lua"
    if not installed.is_file():
        return ""
    try:
        txt = installed.read_text(encoding="utf-8", errors="replace")
        m = BAKED_TOKEN_RE.search(txt)
        if m:
            tok = m.group(1).strip()
            if tok and tok != PLACEHOLDER_TOKEN:
                return tok
    except OSError:
        pass
    return ""


def get_token(rotate: bool = False, explicit_dir=None, preferred_port: int = 8077) -> str:
    if TOKEN_FILE.is_file() and not rotate:
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if token:
            return token

    if not rotate:
        env_tok = os.environ.get("BRIDGE_TOKEN", "").strip()
        if env_tok and env_tok != PLACEHOLDER_TOKEN:
            TOKEN_FILE.write_text(env_tok, encoding="utf-8")
            return env_tok

        recovered = recover_existing_installed_token(explicit_dir=explicit_dir)
        if recovered:
            TOKEN_FILE.write_text(recovered, encoding="utf-8")
            live = _token_works_against_running_server(recovered, preferred_port)
            extra = " (verified against running server)" if live else ""
            say(f"  recovered active token from installed ArenaBridge.lua -> bridge.token{extra}", GREEN)
            return recovered

    token = secrets.token_hex(12)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return token


# ---------------------------------------------------------------------------
# Plugin Install & MCP Client Configuration Generator
# ---------------------------------------------------------------------------

def install_plugin(token: str, port: int, explicit_dir=None) -> bool:
    source = PLUGIN_SRC.read_text(encoding="utf-8")
    source = re.sub(
        r'local BRIDGE_URL = "[^"]*"',
        f'local BRIDGE_URL = "http://127.0.0.1:{port}"',
        source,
        count=1,
    )
    source = re.sub(
        r'local BRIDGE_TOKEN = "[^"]*"',
        f'local BRIDGE_TOKEN = "{token}"',
        source,
        count=1,
    )

    target_dir = Path(explicit_dir) if explicit_dir else plugins_dir()
    if target_dir:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / "ArenaBridge.lua"
            target.write_text(source, encoding="utf-8")
            say(f"  plugin v4.0.0 installed -> {target}", GREEN)
            return True
        except OSError as exc:
            say(f"  could not write to {target_dir} ({exc})", YELLOW)

    DIST.mkdir(exist_ok=True)
    target = DIST / "ArenaBridge.lua"
    target.write_text(source, encoding="utf-8")
    say(f"  plugin v4.0.0 written -> {target}", YELLOW)
    say("  copy it into Studio's plugins folder:", YELLOW)
    say("    Studio -> Plugins tab -> Plugins Folder", DIM)
    return False


def build_mcp_json_config(port: int, read_only: bool = False) -> dict:
    mcp_script = str((HERE / "mcp_server.py").resolve())
    py_exe = sys.executable or "python"
    args = [mcp_script, "--port", str(port)]
    if read_only:
        args.append("--read-only")
    server_key = "roblox-studio-inspector" if read_only else "roblox-studio"
    return {
        "mcpServers": {
            server_key: {
                "command": py_exe,
                "args": args,
                "env": {
                    "BRIDGE_URL": f"http://127.0.0.1:{port}",
                },
            }
        }
    }


def write_mcp_config(dest_path: Path, port: int, read_only: bool = False) -> Path:
    cfg = build_mcp_json_config(port=port, read_only=read_only)
    if dest_path.is_dir():
        dest_file = dest_path / ".mcp.json"
    else:
        dest_file = dest_path
    existing = {}
    if dest_file.is_file():
        try:
            existing = json.loads(dest_file.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.setdefault("mcpServers", {}).update(cfg["mcpServers"])
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    dest_file.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    say(f"  MCP config written -> {dest_file}", GREEN)
    return dest_file


# ---------------------------------------------------------------------------
# Process Spawning & Tunnel Discovery
# ---------------------------------------------------------------------------

def find_binary(name: str):
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


def spawn(cmd, name: str, pattern=None, on_match=None):
    say(f"  starting {name}: {DIM}{' '.join(cmd)}{OFF}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
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

def describe_launch(server_cmd, supervised_tunnel: bool, tunnel_flavor: str,
                    public_url: str, port: int):
    """The `--dry-run` plan, as (line, colour) pairs — also unit-tested directly."""
    tunnel_state = (
        f"supervised {tunnel_flavor}" if supervised_tunnel
        else (f"external {public_url}" if public_url
              else "not requested (add --tunnel or --public-url)")
    )
    return [
        ("  Dry run — nothing was started.", BOLD),
        (f"    server   : {' '.join(server_cmd)}", DIM),
        (f"    tunnel   : {tunnel_state}", DIM),
        (f"    dashboard: http://127.0.0.1:{port}", DIM),
        ("    A local dashboard authenticates itself; a tunnel/LAN page needs the token above.", DIM),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8077)
    ap.add_argument("--tunnel", action="store_true", help="Expose bridge + /mcp via a supervised cloudflared/ngrok tunnel")
    ap.add_argument("--tunnel-flavor", default="auto", choices=["auto", "cloudflared", "ngrok"],
                    help="Tunnel provider (default: auto-detect)")
    ap.add_argument("--tunnel-token", default=None,
                    help="Cloudflare named-tunnel token (stable hostname instead of a random quick-tunnel URL)")
    ap.add_argument("--tunnel-name", default=None,
                    help="Display-only label for a named tunnel; routing still comes from Cloudflare ingress")
    ap.add_argument("--public-url", default=None,
                    help="Report a tunnel URL you already run yourself (bridge will not manage it)")
    ap.add_argument("--install-only", action="store_true", help="Install plugin (and optional --write-mcp) then exit")
    ap.add_argument("--rotate", action="store_true", help="Mint a fresh shared secret token")
    ap.add_argument("--read-only", action="store_true", help="Run server & MCP in Read-Only Inspector mode")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the resolved launch plan (token, server command, tunnel intent) and exit without starting")
    ap.add_argument("--place-id", type=int, default=None, help="Optional PlaceId write guard")
    ap.add_argument("--plugins-dir", default=None, help="Explicit Studio Plugins folder override")
    ap.add_argument(
        "--write-mcp",
        nargs="?",
        const=".",
        default=None,
        help="Write MCP client config (.mcp.json) into directory or file path",
    )
    args = ap.parse_args()

    signal.signal(signal.SIGINT, shutdown)

    say(f"\n{BOLD}Arena <-> Roblox Studio MCP Bridge v5.3.1{OFF}")
    token = get_token(rotate=args.rotate, explicit_dir=args.plugins_dir, preferred_port=args.port)
    say(f"  token: {CYAN}{token}{OFF}  {DIM}(stored in bridge.token){OFF}")

    install_plugin(token, args.port, args.plugins_dir)

    if args.write_mcp is not None:
        write_mcp_config(Path(args.write_mcp).resolve(), port=args.port, read_only=args.read_only)

    if args.install_only:
        say("\nPlugin v4.0.0 installed. Restart Roblox Studio once if updating or rotating token.", GREEN)
        say(f"MCP stdio command:  python \"{HERE / 'mcp_server.py'}\"\n", DIM)
        return

    server_cmd = [
        sys.executable,
        str(HERE / "server.py"),
        "--port",
        str(args.port),
        "--token",
        token,
    ]
    if args.read_only:
        server_cmd.append("--read-only")
    if args.place_id is not None:
        server_cmd.extend(["--place-id", str(args.place_id)])

    supervised_tunnel = bool(args.tunnel) and not args.public_url
    if supervised_tunnel:
        import shutil as _shutil
        have_cloudflared = _shutil.which("cloudflared") is not None
        have_account_ngrok = _shutil.which("ngrok") is not None
        flavor = args.tunnel_flavor
        wanted = ["cloudflared", "ngrok"] if flavor == "auto" else [flavor]
        found = {"cloudflared": have_cloudflared, "ngrok": have_account_ngrok}
        if not any(found.get(w) for w in wanted):
            say("\n  Tunnel requested, but no tunnel binary is on PATH.", YELLOW)
            say(f"    looked for: {', '.join(wanted)}", DIM)
            say("    cloudflared: winget install --id Cloudflare.cloudflared", DIM)
            say("    ngrok:       winget install --id Ngrok.Ngrok", DIM)
            say("    The bridge still starts and serves 127.0.0.1 — no public URL will appear.", DIM)
        else:
            say(f"\n  Tunnel: starting supervised {', '.join(w for w in wanted if found.get(w))} tunnel…", GREEN)
    if args.tunnel:
        server_cmd.append("--tunnel")
        server_cmd.extend(["--tunnel-flavor", args.tunnel_flavor])
        if args.tunnel_token:
            server_cmd.extend(["--tunnel-token", args.tunnel_token])
        if args.tunnel_name:
            server_cmd.extend(["--tunnel-name", args.tunnel_name])
    if args.public_url:
        server_cmd.extend(["--public-url", args.public_url])

    if args.dry_run:
        print()
        for line, color in describe_launch(server_cmd, supervised_tunnel, args.tunnel_flavor,
                                           args.public_url, args.port):
            say(line, color)
        return 0

    spawn(server_cmd, "bridge")
    time.sleep(0.8)

    if args.public_url:
        say("\n" + "=" * 70, GREEN)
        say("  Public URL (externally managed tunnel):", BOLD)
        say(f"    URL:      {args.public_url}", CYAN)
        say(f"    MCP URL:  {args.public_url}/mcp", CYAN)
        say(f"    TOKEN:    {token}", CYAN)
        say("=" * 70, GREEN)
    elif supervised_tunnel:
        def watch_tunnel():
            import urllib.request
            announced = None
            unavailable_reported = False
            url = f"http://127.0.0.1:{args.port}/api/tunnel"
            while True:
                time.sleep(2.0)
                try:
                    req = urllib.request.Request(url, headers={"X-Arena-Token": token})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        info = json.loads(resp.read().decode("utf-8"))
                except Exception:
                    continue
                state = info.get("state")
                public = info.get("publicUrl")
                if public and public != announced:
                    announced = public
                    print()
                    say("=" * 70, GREEN)
                    say("  Tunnel is live — paste these lines to Arena (or any MCP client):", BOLD)
                    say(f"    URL:      {public}", CYAN)
                    say(f"    MCP URL:  {public}/mcp", CYAN)
                    say(f"    TOKEN:    {token}", CYAN)
                    say("  The supervisor auto-restarts the tunnel and re-prints a new URL if it changes.", DIM)
                    say("=" * 70, GREEN)
                    print()
                if state in ("error", "blocked") and not unavailable_reported:
                    unavailable_reported = True
                    say(f"\n  Tunnel could not start (state={state}):", YELLOW)
                    say(f"    {info.get('lastError') or 'no error reported'}", DIM)
                    for line in (info.get("logTail") or [])[-4:]:
                        say(f"    {line}", DIM)
                    say("  The bridge keeps running locally on 127.0.0.1.", DIM)
                if state == "unavailable" and not unavailable_reported:
                    unavailable_reported = True
                    say("\n  No tunnel binary found. Install one of:", YELLOW)
                    for hint in (info.get("installHints") or []):
                        say(f"    {hint}", DIM)
                    say(f"  Or expose manually with:  ngrok http {args.port}", DIM)
                    say("  The bridge keeps running locally on 127.0.0.1.", DIM)

        threading.Thread(target=watch_tunnel, daemon=True).start()

    if not args.tunnel and not args.public_url:
        print()
        say("  Tunnel: not requested — this run is local only (no public URL).", YELLOW)
        say("    Add --tunnel to publish it (needs cloudflared or ngrok),", DIM)
        say("    or --public-url https://your-host to report a tunnel you run yourself.", DIM)

    print()
    say(f"  Web Dashboard:       http://127.0.0.1:{args.port}", GREEN)
    if args.tunnel or args.public_url:
        say(f"    (opened from a tunnel/LAN address? paste this token when the console asks: {token})", DIM)
    say(f"  MCP Streamable HTTP: http://127.0.0.1:{args.port}/mcp  (Bearer {token})", GREEN)
    say(f"  MCP stdio server:    python \"{HERE / 'mcp_server.py'}\"", GREEN)
    say(f"  CLI quick check:     .\\ab.ps1 ping", DIM)
    say("\n  Studio auto-connects on place load (restart Studio once if newly installed).", BOLD)
    say("  Press Ctrl+C to stop.\n", DIM)

    while True:
        time.sleep(1)
        for proc in CHILDREN:
            if proc.poll() is not None:
                say(f"child process exited with code {proc.returncode}", RED)
                shutdown()


if __name__ == "__main__":
    main()
