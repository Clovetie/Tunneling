#!/usr/bin/env python3
"""
tunnel_manager.py — Supervised public tunnel for the Arena Roblox Studio bridge.

`python setup.py --tunnel` starts a one-shot Cloudflare quick tunnel and hopes it
stays up. This module makes the tunnel a managed service instead:

  * auto-detects `cloudflared` or `ngrok` on PATH (with install hints if absent),
  * supervises the child process with exponential backoff and automatic restart,
  * scrapes the public URL from the child's output and re-learns it after a restart,
  * probes `<publicUrl>/api/health` and marks the tunnel degraded when it stops
    answering, restarting it after repeated failures,
  * supports token-based named Cloudflare tunnels and a user-managed static URL,
  * reports LAN URLs so phones/tablets on the same Wi-Fi can reach the dashboard,
  * exposes everything as JSON for `GET /api/tunnel` and the Control Center.

Zero dependencies; works on Windows, macOS and Linux.
"""

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

TUNNEL_VERSION = "5.3.0"
DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "bridge_state"

CLOUDFLARE_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-]*\.trycloudflare\.com", re.I)
NGROK_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9-.]*\.ngrok(?:-free)?\.(?:app|io|dev)", re.I)
ANY_URL_RE = re.compile(r"https://[a-z0-9][a-z0-9.-]+\.[a-z]{2,}(?::\d+)?", re.I)

INSTALL_HINTS = {
    "cloudflared": [
        "Windows:  winget install --id Cloudflare.cloudflared",
        "macOS:    brew install cloudflared",
        "Linux:    https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/",
    ],
    "ngrok": [
        "Windows:  winget install --id ngrok.ngrok",
        "macOS:    brew install ngrok/ngrok/ngrok",
        "Any:      https://ngrok.com/download (then `ngrok config add-authtoken <token>`)",
    ],
}


def _now() -> float:
    return time.time()


def local_ip_addresses() -> list:
    """Best-effort LAN IPv4 addresses (no packet is actually sent)."""
    found = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("10.255.255.255", 1))
            found.append(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        pass
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith("127.")]


def find_tunnel_binary(preferred: str = None) -> tuple:
    """Return (flavor, path) for the first tunnel binary found, else (None, None)."""
    candidates = []
    if preferred and preferred not in ("auto", ""):
        candidates.append(preferred)
    candidates += ["cloudflared", "ngrok"]
    for name in candidates:
        found = shutil.which(name)
        if found:
            return ("cloudflared" if "cloudflared" in Path(found).name.lower() else "ngrok"), found
    return None, None


TOKEN_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=_-.")


def validate_named_token(token: str):
    """Check a Cloudflare named-tunnel token before it reaches a subprocess.

    Returns (ok, reason). The token is a base64 blob; the checks are deliberately
    about shape (empty / whitespace / junk characters / pasted URL) rather than
    about decoding Cloudflare's payload, so a valid token is never rejected.
    """
    raw = token if isinstance(token, str) else ""
    if not raw.strip():
        return False, "the token is empty"
    if raw != raw.strip():
        return False, "the token has leading or trailing whitespace (check for a stray quote or newline)"
    if any(ch.isspace() for ch in raw):
        return False, "the token contains whitespace; copy only the tunnel token value"
    if "://" in raw or raw.lower().startswith(("http", "https")):
        return False, "this looks like a URL, not a tunnel token; copy the token from the Cloudflare dashboard"
    if len(raw) < 20:
        return False, f"the token is only {len(raw)} characters, which is too short for a Cloudflare tunnel token"
    bad = sorted({ch for ch in raw if ch not in TOKEN_CHARS})
    if bad:
        return False, "the token contains unexpected characters: " + " ".join(repr(ch) for ch in bad[:5])
    return True, "ok"


class TunnelManager:
    """Starts, watches, restarts and reports a public tunnel for the local bridge."""

    def __init__(
        self,
        port: int = 8077,
        binary: str = None,
        flavor: str = "auto",
        named_token: str = None,
        name: str = None,
        public_url: str = None,
        health_interval: float = 20.0,
        health_failures_before_restart: int = 4,
        max_backoff: float = 30.0,
        state_dir=None,
        auto_start: bool = False,
    ):
        self.port = int(port)
        self.local_url = f"http://127.0.0.1:{self.port}"
        self.requested_flavor = (flavor or "auto").lower()
        self.binary_arg = binary
        self.named_token = named_token or os.environ.get("TUNNEL_TOKEN") or None
        self.name = name
        self.user_public_url = (public_url or os.environ.get("BRIDGE_PUBLIC_URL") or "").strip() or None
        self.health_interval = float(health_interval)
        self.health_failures_before_restart = int(health_failures_before_restart)
        self.max_backoff = float(max_backoff)
        self.state_path = (Path(state_dir) if state_dir else DEFAULT_STATE_DIR) / "tunnel.json"

        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self._thread = None
        self._proc = None
        self.flavor = None
        self.binary = None
        self.state = "stopped"
        self.public_url = self.user_public_url
        self.last_error = None
        self.started_at = None
        self.online_at = None
        self.restarts = 0
        self.url_changes = 0
        self.url_changed_at = None
        self.proc_pid = None
        self.health = {"lastProbe": None, "ok": None, "latencyMs": None, "failures": 0, "consecutiveFailures": 0}
        self.log_tail = deque(maxlen=60)
        self.last_output_at = None
        self._last_health = 0.0
        self._load_last_url()
        if auto_start:
            self.start()

    # -- persistence of last known URL ---------------------------------------

    def _load_last_url(self):
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not self.public_url and raw.get("publicUrl"):
                self.public_url = raw["publicUrl"]
                self.last_error = None
                self._loaded_url = True
        except (OSError, ValueError):
            pass

    def _persist_url(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"publicUrl": self.public_url, "saved": _now(), "version": TUNNEL_VERSION}),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- command construction ------------------------------------------------

    def _command(self) -> list:
        if not self.binary:
            raise RuntimeError("No tunnel binary available")
        if self.flavor == "cloudflared":
            if self.named_token:
                # Named tunnel: the ingress/hostname config lives in Cloudflare;
                # `--url` is only valid for quick tunnels.
                ok, reason = validate_named_token(self.named_token)
                if not ok:
                    raise RuntimeError(f"Invalid Cloudflare tunnel token: {reason}")
                return [self.binary, "tunnel", "--no-autoupdate", "run", "--token", self.named_token]
            return [self.binary, "tunnel", "--url", self.local_url, "--no-autoupdate"]
        if self.flavor == "ngrok":
            cmd = [self.binary, "http", str(self.port), "--log", "stdout", "--log-format", "logfmt"]
            return cmd
        raise RuntimeError(f"Unsupported tunnel flavor: {self.flavor}")

    def _detect(self):
        flavor, path = find_tunnel_binary(self.binary_arg)
        if not flavor and self.requested_flavor in ("cloudflared", "ngrok"):
            # Respect an explicit preference even if the generic detection failed.
            found = shutil.which(self.requested_flavor)
            if found:
                flavor, path = self.requested_flavor, found
        with self.lock:
            self.flavor = flavor if not self.requested_flavor or self.requested_flavor == "auto" else (self.requested_flavor if path else flavor)
            self.binary = path
        return bool(path)

    def available(self) -> bool:
        return self.binary is not None or shutil.which(self.binary_arg or "") is not None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> dict:
        with self.lock:
            if self.user_public_url:
                self.state = "online"
                self.public_url = self.user_public_url
                self.online_at = self.online_at or _now()
                return self.status()
            if self._thread and self._thread.is_alive():
                return self.status()
            if self.named_token and (self.requested_flavor in ("auto", "cloudflared")):
                ok, reason = validate_named_token(self.named_token)
                if not ok:
                    self.state = "error"
                    self.last_error = (
                        f"Invalid Cloudflare tunnel token: {reason}. "
                        "Run `cloudflared tunnel token <name>` (or copy it from Zero Trust > Networks > Tunnels) "
                        "and pass the value with --tunnel-token; the bridge will not start a named tunnel without it."
                    )
                    self._append_log_unlocked("[supervisor] " + self.last_error)
                    return self.status()
            if not self._detect():
                self.state = "unavailable"
                self.last_error = (
                    "No tunnel binary found on PATH. Install cloudflared or ngrok, "
                    "or pass BRIDGE_PUBLIC_URL for a tunnel you run yourself."
                )
                return self.status()
            self.stop_event.clear()
            self.last_error = None
            self.state = "starting"
            self.started_at = self.started_at or _now()
            self._thread = threading.Thread(target=self._supervise, name="tunnel-supervisor", daemon=True)
            self._thread.start()
            return self.status()

    def stop(self) -> dict:
        self.stop_event.set()
        with self.lock:
            proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except OSError:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=6)
        with self.lock:
            self._thread = None
            self._proc = None
            self.proc_pid = None
            self.state = "stopped"
            if not self.user_public_url:
                self.online_at = None
        return self.status()

    def restart(self) -> dict:
        self.stop()
        with self.lock:
            self.restarts += 1
        time.sleep(0.4)
        return self.start()

    # -- supervision ---------------------------------------------------------

    def _supervise(self):
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                cmd = self._command()
            except RuntimeError as exc:
                with self.lock:
                    self.state = "error"
                    self.last_error = str(exc)
                return
            with self.lock:
                self.state = "starting"
                self._append_log_unlocked(f"$ {' '.join(cmd[:3])}{' …' if len(cmd) > 3 else ''}")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=(os.name != "nt"),
                )
            except (OSError, ValueError) as exc:
                with self.lock:
                    self.state = "error"
                    self.last_error = f"Failed to launch {self.flavor}: {exc}"
                    self._append_log_unlocked(self.last_error)
                if self.stop_event.wait(backoff):
                    break
                backoff = min(self.max_backoff, backoff * 2)
                continue
            with self.lock:
                self._proc = proc
                self.proc_pid = proc.pid
            reader = threading.Thread(target=self._read_output, args=(proc,), daemon=True)
            reader.start()
            # Wait for the process to exit, probing health in the meantime.
            while not self.stop_event.is_set() and proc.poll() is None:
                if self.stop_event.wait(timeout=1.0):
                    break
                self._maybe_probe()
            reader.join(timeout=2)
            if self.stop_event.is_set():
                break
            code = proc.poll()
            with self.lock:
                self._proc = None
                self.proc_pid = None
                self._append_log_unlocked(f"[supervisor] {self.flavor} exited with code {code}; reconnecting…")
                self.state = "reconnecting"
                self.restarts += 1
                self.health["ok"] = False
                self.health["consecutiveFailures"] = 0
                # A dead child invalidates its quick-tunnel URL; a new one is
                # scraped from the replacement process.
                self.public_url = None
            if self.stop_event.wait(backoff):
                break
            backoff = min(self.max_backoff, backoff * 2)
        with self.lock:
            if self.state not in ("stopped",):
                self.state = "stopped"

    def _read_output(self, proc):
        stream = proc.stdout
        if not stream:
            return
        for raw_line in iter(stream.readline, ""):
            line = (raw_line or "").rstrip()
            if not line:
                continue
            with self.lock:
                self._append_log_unlocked(line)
                self.last_output_at = _now()
            match = None
            if self.flavor == "ngrok":
                match = NGROK_URL_RE.search(line) or CLOUDFLARE_URL_RE.search(line)
            else:
                match = CLOUDFLARE_URL_RE.search(line) or NGROK_URL_RE.search(line)
            if match:
                self._set_url(match.group(0))
        try:
            stream.close()
        except OSError:
            pass

    def _append_log_unlocked(self, line: str):
        self.log_tail.append(line[:400])

    def _set_url(self, url: str):
        url = url.rstrip("/")
        with self.lock:
            if self.public_url == url:
                return
            changed = self.public_url is not None
            self.public_url = url
            self.url_changed_at = _now()
            self.state = "online"
            self.online_at = _now()
            self.last_error = None
            self.health["ok"] = None
            self.health["failures"] = 0
            self.health["consecutiveFailures"] = 0
            if changed:
                self.url_changes += 1
        self._persist_url()

    def _maybe_probe(self):
        now = _now()
        if now - self._last_health < self.health_interval:
            return
        if not self.public_url:
            self._last_health = now
            if self.last_output_at and now - self.last_output_at > 45 and self.state == "starting":
                with self.lock:
                    self.last_error = "Tunnel process started but has not published a URL yet."
            return
        self._last_health = now
        threading.Thread(target=self._probe_once, name="tunnel-health", daemon=True).start()

    def _probe_once(self):
        url = f"{self.public_url}/api/health"
        started = time.monotonic()
        ok = False
        detail = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"arena-tunnel/{TUNNEL_VERSION}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = 200 <= resp.status < 500
                detail = f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            ok = exc.code < 500
            detail = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001 - network errors are all non-fatal
            detail = str(exc)[:200]
        latency = round((time.monotonic() - started) * 1000, 1)
        restart = False
        with self.lock:
            self.health["lastProbe"] = _now()
            self.health["latencyMs"] = latency
            self.health["ok"] = ok
            self.health["detail"] = detail
            if ok:
                self.health["failures"] = 0
                self.health["consecutiveFailures"] = 0
                if self.state == "degraded":
                    self.state = "online"
            else:
                self.health["failures"] = int(self.health.get("failures") or 0) + 1
                self.health["consecutiveFailures"] = int(self.health.get("consecutiveFailures") or 0) + 1
                if self.health["consecutiveFailures"] >= 2 and self.state == "online":
                    self.state = "degraded"
                if self.health["consecutiveFailures"] >= self.health_failures_before_restart:
                    restart = True
            if restart:
                self._append_log_unlocked("[supervisor] public URL failed repeated health probes; restarting tunnel")
        if restart:
            proc = self._proc
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
            with self.lock:
                self.restarts += 1

    # -- reporting -----------------------------------------------------------

    def status(self) -> dict:
        with self.lock:
            now = _now()
            state = self.state
            if self.user_public_url and state == "stopped":
                state = "online"
            uptime = round(now - self.online_at, 1) if self.online_at and state == "online" else 0
            lan = [
                f"http://{ip}:{self.port}" for ip in local_ip_addresses()
            ]
            return {
                "version": TUNNEL_VERSION,
                "state": state,
                "running": bool(self._thread and self._thread.is_alive()) or bool(self.user_public_url),
                "flavor": self.flavor,
                "binary": self.binary,
                "binaryAvailable": bool(self.binary),
                "managed": not bool(self.user_public_url),
                "named": bool(self.named_token),
                "namedTokenValid": (validate_named_token(self.named_token)[0] if self.named_token else None),
                "name": self.name,
                "localUrl": self.local_url,
                "lanUrls": lan,
                "publicUrl": self.public_url,
                "mcpEndpoint": f"{self.public_url}/mcp" if self.public_url else None,
                "healthEndpoint": f"{self.public_url}/api/health" if self.public_url else None,
                "onlineSeconds": uptime,
                "restarts": self.restarts,
                "urlChanges": self.url_changes,
                "urlChangedAt": self.url_changed_at,
                "pid": self.proc_pid,
                "lastError": self.last_error,
                "health": dict(self.health),
                "logTail": list(self.log_tail)[-25:],
                "installHints": INSTALL_HINTS.get(self.flavor or "cloudflared"),
                "serverTime": now,
            }

    def handle(self, payload: dict) -> dict:
        action = ((payload or {}).get("action") or "status").lower()
        if action == "status":
            return self.status()
        if action == "start":
            return self.start()
        if action == "stop":
            return self.stop()
        if action == "restart":
            return self.restart()
        if action == "lan":
            return {"lanUrls": [f"http://{ip}:{self.port}" for ip in local_ip_addresses()], "port": self.port}
        raise ValueError("Unknown tunnel action. Use status, start, stop, restart or lan.")
