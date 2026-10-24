#!/usr/bin/env python3
"""
mcp_server.py — Standalone Model Context Protocol (MCP) stdio server for
Roblox Studio (v4.0.0).

Works out-of-the-box with Claude Code, Claude Desktop, Cursor, Codex CLI,
VS Code Copilot, Windsurf, Zed, Cline, and any standard MCP host.

Smart Bridge Lifecycle:
  1. If `server.py` (or another `mcp_server.py`) is ALREADY listening on
     `BRIDGE_URL` (default `http://127.0.0.1:8077`), this process seamlessly
     attaches to it over HTTP — multiple AI clients can share the same open
     Studio session simultaneously without port conflicts!
  2. If nothing is listening on `:8077` yet, this process automatically starts
     an embedded HTTP bridge server in a background thread so Roblox Studio's
     `ArenaBridge.lua` plugin connects automatically with zero manual setup!

Usage:
    python3 mcp_server.py
    python3 mcp_server.py --auto-install-plugin
    python3 mcp_server.py --read-only
    python3 mcp_server.py --url https://<the-tunnel>.trycloudflare.com --token <TOK>
"""

import argparse
import json
import os
import sys
import threading
import urllib.request

# Capture raw binary stdout BEFORE anything can touch print(), and redirect
# all incidental Python print() output strictly to stderr so the MCP stdio
# wire stream stays 100% pure JSON-RPC 2.0.
_RAW_STDIN = sys.stdin.buffer
_RAW_STDOUT = sys.stdout.buffer
sys.stdout = sys.stderr

from mcp_core import BridgeClient, MCPDispatcher  # noqa: E402
from server import create_http_server, load_token_default  # noqa: E402


def _log(msg: str):
    sys.stderr.write(f"[arena-mcp] {msg}\n")
    sys.stderr.flush()


def _probe_existing_bridge(base_url: str) -> bool:
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/api/health", method="GET")
        with urllib.request.urlopen(req, timeout=1.2) as resp:
            return resp.status == 200
    except Exception:
        return False


def build_runtime_client(
    port: int,
    url: str = None,
    token: str = None,
    read_only: bool = False,
    place_id: int = None,
    no_embedded: bool = False,
    auto_install_plugin: bool = False,
):
    resolved_token = load_token_default(token)

    # Auto-install / sync Studio plugin if requested or if token file is missing
    if auto_install_plugin:
        try:
            from setup import get_token, install_plugin
            resolved_token = get_token(rotate=False, preferred_port=port)
            install_plugin(resolved_token, port)
        except Exception as exc:
            _log(f"plugin auto-install warning: {exc}")

    explicit_url = (url or os.environ.get("BRIDGE_URL") or "").strip().rstrip("/")
    local_url = explicit_url or f"http://127.0.0.1:{port}"
    is_remote = explicit_url.startswith("https://") or (
        explicit_url != ""
        and "127.0.0.1" not in explicit_url
        and "localhost" not in explicit_url
    )

    if is_remote or no_embedded or _probe_existing_bridge(local_url):
        _log(f"attaching to active bridge at {local_url} (read_only={read_only})")
        client = BridgeClient(
            base_url=local_url,
            token=resolved_token,
            read_only=read_only,
            default_place_id=place_id,
        )
        return client, None

    # No bridge listening locally yet -> spin up embedded background HTTP server!
    try:
        if not resolved_token:
            from setup import get_token, install_plugin
            resolved_token = get_token(rotate=False, preferred_port=port)
            install_plugin(resolved_token, port)
        httpd, state = create_http_server(
            host="127.0.0.1",
            port=port,
            token=resolved_token,
            read_only=read_only,
            guard_place_id=place_id,
        )
        t = threading.Thread(target=httpd.serve_forever, name="EmbeddedStudioBridge", daemon=True)
        t.start()
        _log(f"started embedded Studio bridge on http://127.0.0.1:{port} (read_only={read_only})")
        return state.mcp_client, httpd
    except OSError as bind_err:
        # Race: another process bound :port milliseconds ahead of us — attach cleanly!
        _log(f"port {port} bound concurrently ({bind_err}); attaching over HTTP")
        client = BridgeClient(
            base_url=local_url,
            token=resolved_token,
            read_only=read_only,
            default_place_id=place_id,
        )
        return client, None


def _read_message(stream):
    """Read one JSON-RPC message supporting BOTH standard MCP newline-delimited
    JSON and LSP-style Content-Length headers. Returns (parsed_obj, framing_mode).
    """
    while True:
        line = stream.readline()
        if not line:
            return None, None
        stripped = line.strip()
        if not stripped:
            continue

        lower = stripped.lower()
        if lower.startswith(b"content-length:"):
            length = int(stripped.split(b":", 1)[1].strip())
            # Consume remaining headers up to blank CRLF line
            while True:
                hdr = stream.readline()
                if not hdr or hdr.strip() == b"":
                    break
            body = stream.read(length)
            if not body:
                return None, None
            return json.loads(body.decode("utf-8")), "lsp"

        return json.loads(stripped.decode("utf-8")), "ndjson"


def _write_message(stream, payload, framing: str = "ndjson"):
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if framing == "lsp":
        header = f"Content-Length: {len(encoded)}\r\n\r\n".encode("ascii")
        stream.write(header)
        stream.write(encoded)
    else:
        stream.write(encoded + b"\n")
    stream.flush()


def serve_stdio(dispatcher: MCPDispatcher):
    write_lock = threading.Lock()

    def handle_async(req_obj, framing_mode: str):
        try:
            resp = dispatcher.handle_payload(req_obj)
        except Exception as exc:
            msg_id = req_obj.get("id") if isinstance(req_obj, dict) else None
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(exc)},
            }
        if resp is not None:
            with write_lock:
                _write_message(_RAW_STDOUT, resp, framing_mode)

    while True:
        try:
            msg, mode = _read_message(_RAW_STDIN)
        except json.JSONDecodeError as exc:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
            with write_lock:
                _write_message(_RAW_STDOUT, err_resp, "ndjson")
            continue

        if msg is None:
            break

        # Dispatch tool calls concurrently so parallel MCP calls don't block ping/list
        threading.Thread(target=handle_async, args=(msg, mode), daemon=True).start()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", 8077)))
    ap.add_argument("--url", default=None, help="Explicit bridge URL (e.g. tunnel URL)")
    ap.add_argument("--token", default=None, help="Explicit bridge token (auto-reads bridge.token)")
    ap.add_argument(
        "--read-only",
        action="store_true",
        default=os.environ.get("BRIDGE_READ_ONLY", "") in ("1", "true", "yes"),
        help="Run in Read-Only Inspector mode",
    )
    ap.add_argument(
        "--place-id",
        type=int,
        default=int(os.environ["BRIDGE_PLACE_ID"]) if os.environ.get("BRIDGE_PLACE_ID") else None,
        help="Optional PlaceId write guard",
    )
    ap.add_argument("--no-embedded", action="store_true", help="Do not start embedded server.py if offline")
    ap.add_argument("--auto-install-plugin", action="store_true", help="Install/sync ArenaBridge.lua into Studio Plugins dir on boot")
    args = ap.parse_args()

    client, httpd = build_runtime_client(
        port=args.port,
        url=args.url,
        token=args.token,
        read_only=args.read_only,
        place_id=args.place_id,
        no_embedded=args.no_embedded,
        auto_install_plugin=args.auto_install_plugin,
    )
    dispatcher = MCPDispatcher(client)
    try:
        serve_stdio(dispatcher)
    finally:
        if httpd is not None:
            httpd.shutdown()


if __name__ == "__main__":
    main()
