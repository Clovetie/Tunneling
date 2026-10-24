#!/usr/bin/env python3
"""
server.py — Multi-Peer HTTP Bridge + Native Streamable HTTP MCP Server (v4.0.0)

Runs locally on your PC (127.0.0.1:8077 by default) with ZERO pip dependencies.
Simultaneously serves:
  1. Roblox Studio local plugin peers (Edit + Play Server + Play Client bridge)
     via fast long-polling (`GET /api/poll`, `POST /api/result`).
  2. Native MCP Streamable HTTP endpoint (`POST /mcp`, `GET /mcp`, `DELETE /mcp`)
     implementing MCP 2025-06-18 / 2024-11-05 JSON-RPC 2.0 (68 tools, 5 live
     resources, 2 resource templates, 4 workflow prompts).
  3. REST API for CLI (`arena_studio.py` / `ab.ps1`) & remote cloud agents
     (`GET /api/health`, `GET /api/state`, `GET /api/agent-guide`,
      `GET /api/peers`, `POST /api/jobs`, `GET /api/jobs/<id>`,
      `POST /api/jobs/<id>/cancel`, `POST /api/queue/clear`,
      `GET|POST /api/snapshots`).
  4. Interactive Control Center & Next-Agent Handoff Hub (`GET /`) with live
     peer & job telemetry, quick actions, 1-click handoff prompts, generic
     starter prompts, MCP client config generators, live tool runner, and tips.
"""

import argparse
import gzip
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid

from mcp_core import BridgeClient, MCPDispatcher, TOOL_SPECS, run_bridge_self_test

VERSION = "4.0.0"
PEER_ONLINE_WINDOW = 5.0  # play DataModel peers are transient; do not route work to stale peers
HERE = Path(__file__).resolve().parent
SNAPSHOT_DIR = HERE / "snapshots"
SNAPSHOT_RETENTION = 30


def _now() -> float:
    return time.time()


def build_agent_guide(state_snapshot: dict, origin: str = "http://127.0.0.1:8077") -> dict:
    """Structured onboarding guide for any human operator or next AI agent."""
    categories = {}
    for spec in TOOL_SPECS:
        cat = "read_only" if spec.get("annotations", {}).get("readOnlyHint") else "mutating"
        categories.setdefault(cat, []).append(spec["name"])
    return {
        "name": "arena-roblox-studio-mcp",
        "version": VERSION,
        "origin": origin,
        "endpoints": {
            "dashboard": f"{origin}/",
            "mcp_streamable_http": f"{origin}/mcp",
            "mcp_stdio": "python mcp_server.py",
            "health": f"{origin}/api/health",
            "state": f"{origin}/api/state",
            "peers": f"{origin}/api/peers",
            "jobs": f"{origin}/api/jobs",
            "snapshots": f"{origin}/api/snapshots",
            "agent_guide": f"{origin}/api/agent-guide",
            "self_test": f"{origin}/api/self-test",
        },
        "runtimeRequirements": {
            "editPeer": "Required for live inspection, writes, import, snapshots and persistent-place edits.",
            "serverPeer": "Required for live server probes, playtest state and server console.",
            "clientPeer": "Required for client-only runtime probes; some server peers can forward when a player client is attached.",
            "offlineTools": ["browse_kit", "search_assets", "search_cloud_icons", "luau_check (when host binaries are installed)", "render_offline with an exported hierarchy tree"],
            "peerFreshnessSeconds": 5,
        },
        "toolCounts": {
            "total": len(TOOL_SPECS),
            "readOnly": len(categories.get("read_only", [])),
            "mutating": len(categories.get("mutating", [])),
        },
        "toolsByMode": categories,
        "cliQuickReference": [
            r".\ab.ps1 health",
            r".\ab.ps1 ping",
            r".\ab.ps1 state",
            r".\ab.ps1 survey",
            r".\ab.ps1 tree Workspace --depth 3",
            r".\ab.ps1 search SpawnLocation --root Workspace",
            r".\ab.ps1 grep RemoteEvent --root ReplicatedStorage",
            r".\ab.ps1 read ServerScriptService.Main --start 1 --end 120 --numbers",
            r".\ab.ps1 edit ServerScriptService.Main --old 'DEBUG = false' --new 'DEBUG = true' --replace",
            r".\ab.ps1 check ./src",
            r".\ab.ps1 drift ./src ServerScriptService.App",
            r".\ab.ps1 sync  ./src ServerScriptService.App",
            r".\ab.ps1 import-image ./art/banner.png --target Workspace --apply-mode surface_gui",
            r".\ab.ps1 gen-image icon_coin --size 128 --target StarterGui --apply-mode screen_gui",
            r".\ab.ps1 icons 'golden cat'",
            r".\ab.ps1 template full_stud_ui_suite",
            r".\ab.ps1 install-module all --parent ReplicatedStorage.Shared",
            r".\ab.ps1 prefab ui_kenney_shop_window",
            r".\ab.ps1 prefab lighting_cinematic_sunset",
            r".\ab.ps1 scenario --mode play --server-code 'return {players = #game.Players:GetPlayers()}'",
        ],
        "keyTips": [
            "Survey before building: run `survey_place` (includeSource=false on large places, then `script_read` / `script_grep` selectively).",
            "Validate Luau offline first: `luau_check` runs `luau-compile` + `luau-analyze` to catch syntax errors and non-strict undefined globals before touching Studio.",
            "Target the right DataModel: use `dm='edit'` for persistent place changes, `dm='server'` for live Play/Run server state, and `dm='client'` for live player client state. Runtime tools fail fast when the required peer is absent.",
            "`run_playtest_scenario` is successful only with a live server peer, successful requested probes, and a confirmed retained/stopped session; use `keepAlive` and the console cursor/slice fields deliberately.",
            "`render_offline` can render a previously exported hierarchy tree without a Studio peer; use `screenshot_gui` with `dm='client', path='PlayerGui'` when live absolute UI measurements are needed."
            "Bust cached ModuleScripts when editing live packages: pass `replaceInstance=true` (or `--replace` in CLI) or evaluate via `require_fresh`.",
            "Protect refactors: pass `expectedHash` on edits and create a rollback point with `manage_snapshots(action='create')` before multi-script updates.",
            "All mutating operations wrap inside `ChangeHistoryService` recordings so a single Ctrl+Z in Studio cleanly undoes an entire build or sync.",
        ],
        "live": state_snapshot,
    }


# ----------------------------------------------------------------------------
# Thread-safe Multi-Peer Bridge State
# ----------------------------------------------------------------------------

class BridgeState:
    def __init__(self, token: str = "", read_only: bool = False, guard_place_id: int = None):
        self.token = token or ""
        self.read_only = bool(read_only)
        self.guard_place_id = int(guard_place_id) if guard_place_id else None

        self.lock = threading.RLock()
        self.poll_cv = threading.Condition(self.lock)

        # peer_id -> peer metadata dict
        self.peers = {}
        # job_id -> job dict
        self.jobs = {}
        # job_id -> threading.Event signaled immediately on job completion/cancellation
        self.job_events = {}
        # Ordered deque of queued job IDs waiting to be claimed by a matching peer
        self.pending = deque()
        # Bounded history of recent job IDs (max 500)
        self.history = deque(maxlen=500)
        # Durable, compressed script snapshots with a bounded retention policy.
        self.snapshots = deque(maxlen=SNAPSHOT_RETENTION)
        self._load_snapshots()
        # Active Streamable HTTP session IDs
        self.session_id = "mcp-" + uuid.uuid4().hex[:16]

        self.mcp_client = BridgeClient(
            direct_dispatch=self.direct_dispatch,
            read_only=self.read_only,
            default_place_id=self.guard_place_id,
        )
        self.mcp_dispatcher = MCPDispatcher(self.mcp_client)

    # -- peer tracking -------------------------------------------------------

    def record_poll(self, qs: dict) -> dict:
        now = _now()
        peer_id = (qs.get("peer", [""])[0] or "").strip()
        dm = (qs.get("dm", ["edit"])[0] or "edit").strip().lower()
        place = qs.get("place", ["unknown"])[0]
        raw_pid = qs.get("placeId", ["0"])[0]
        try:
            place_id = int(raw_pid)
        except ValueError:
            place_id = 0
        raw_gid = qs.get("gameId", ["0"])[0]
        try:
            game_id = int(raw_gid)
        except ValueError:
            game_id = 0
        plugin_ver = qs.get("pv", ["2.0"])[0]
        studio_ver = qs.get("sv", [""])[0]
        client_kind = qs.get("client", ["studio"])[0]
        has_client = qs.get("hasClient", ["0"])[0] in ("1", "true", "yes")
        try:
            players = int(qs.get("players", ["0"])[0])
        except ValueError:
            players = 0

        if not peer_id:
            peer_id = f"{dm}-legacy-{place_id}"

        with self.lock:
            existing = self.peers.get(peer_id, {})
            info = {
                "peerId": peer_id,
                "dm": dm,
                "place": place,
                "placeId": place_id,
                "gameId": game_id,
                "pluginVersion": plugin_ver,
                "studioVersion": studio_ver,
                "client": client_kind,
                "hasClient": has_client,
                "playerCount": players,
                "polls": existing.get("polls", 0) + 1,
                "firstSeen": existing.get("firstSeen", now),
                "lastPoll": now,
            }
            self.peers[peer_id] = info
            return dict(info)

    def _active_peers_unlocked(self) -> list:
        now = _now()
        stale_ids = [
            pid for pid, p in self.peers.items()
            if (now - p["lastPoll"]) > 120.0
        ]
        for pid in stale_ids:
            self.peers.pop(pid, None)

        out = []
        for p in self.peers.values():
            age = round(now - p["lastPoll"], 2)
            entry = dict(p)
            entry["ageSeconds"] = age
            entry["online"] = age < PEER_ONLINE_WINDOW
            out.append(entry)
        order = {"edit": 0, "server": 1, "client": 2}
        out.sort(key=lambda x: (not x["online"], order.get(x["dm"], 9), x["peerId"]))
        return out

    def legacy_studio_summary_unlocked(self):
        peers = self._active_peers_unlocked()
        online_peers = [p for p in peers if p["online"]]
        primary = online_peers[0] if online_peers else (peers[0] if peers else None)
        if not primary:
            return False, {
                "last_poll": 0,
                "place": None,
                "place_id": None,
                "client": None,
                "polls": 0,
                "plugin_version": None,
            }
        return bool(online_peers), {
            "last_poll": primary["lastPoll"],
            "place": primary["place"],
            "place_id": primary["placeId"],
            "client": primary["client"],
            "polls": sum(p["polls"] for p in peers),
            "plugin_version": primary["pluginVersion"],
            "dm": primary["dm"],
            "peer_id": primary["peerId"],
        }

    # -- job routing & matching ----------------------------------------------

    def _peer_matches_job_unlocked(self, peer: dict, job: dict) -> bool:
        req_peer = job.get("target_peer")
        if req_peer and peer["peerId"] != req_peer:
            return False

        req_place = job.get("place_id")
        if req_place and peer["placeId"] != int(req_place):
            return False

        target_dm = job.get("target_dm") or "auto"
        peer_dm = peer["dm"]

        if target_dm == "auto":
            online = [p for p in self._active_peers_unlocked() if p["online"]]
            has_edit = any(p["dm"] == "edit" for p in online)
            if has_edit:
                return peer_dm == "edit"
            return True

        if target_dm == "edit":
            return peer_dm == "edit"

        if target_dm == "server":
            return peer_dm == "server"

        if target_dm == "client":
            if peer_dm == "client":
                return True
            if peer_dm == "server":
                return True
            return False

        return True

    def poll_for_job(self, peer_info: dict, wait_seconds: float = 8.0):
        deadline = time.monotonic() + wait_seconds
        with self.poll_cv:
            while True:
                self.peers[peer_info["peerId"]]["lastPoll"] = _now()
                for job_id in list(self.pending):
                    job = self.jobs.get(job_id)
                    if not job or job["status"] != "queued":
                        self.pending.remove(job_id)
                        continue
                    if self._peer_matches_job_unlocked(peer_info, job):
                        self.pending.remove(job_id)
                        job["status"] = "running"
                        job["claimed"] = _now()
                        job["claimedBy"] = peer_info["peerId"]
                        job["dm"] = peer_info["dm"]
                        return {
                            "id": job["id"],
                            "type": job["type"],
                            "payload": job["payload"],
                        }

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.poll_cv.wait(timeout=min(remaining, 0.5))

    # -- enqueue / complete / cancel -----------------------------------------

    MUTATING_JOB_TYPES = {
        "run_luau", "build", "write_script", "multi_edit",
        "find_replace_scripts", "set_properties", "mass_set_properties",
        "clone_instance", "reparent_instance", "delete", "attributes",
        "tags", "selection", "camera", "history", "terrain",
        "insert_asset", "import_image", "playtest", "open_script",
    }

    def enqueue_job(
        self,
        kind: str,
        payload=None,
        note: str = None,
        dm: str = None,
        peer_id: str = None,
        place_id: int = None,
    ):
        payload_dict = dict(payload or {}) if isinstance(payload, dict) else {}

        if self.read_only and kind in self.MUTATING_JOB_TYPES:
            read_sub_ok = (
                (kind == "attributes" and payload_dict.get("action") == "get")
                or (kind == "tags" and payload_dict.get("action") in ("list_all", "find_tagged", "get"))
                or (kind == "selection" and payload_dict.get("action") == "get")
                or (kind == "camera" and payload_dict.get("action") == "get")
                or (kind == "history" and payload_dict.get("action") == "status")
                or (kind == "terrain" and payload_dict.get("action") == "materials")
                or (kind == "playtest" and payload_dict.get("action") == "status")
                or (kind == "open_script" and payload_dict.get("listOnly") is True)
                or (kind == "find_replace_scripts" and payload_dict.get("dryRun") is True)
            )
            if not read_sub_ok:
                raise PermissionError(
                    f"Bridge is running in --read-only Inspector mode; job '{kind}' is blocked."
                )

        effective_dm = (dm or payload_dict.get("_targetDm") or "auto").lower()
        if effective_dm == "client":
            payload_dict["_targetDm"] = "client"

        effective_place = place_id if place_id is not None else self.guard_place_id

        with self.poll_cv:
            jid = uuid.uuid4().hex[:12]
            done_evt = threading.Event()
            job = {
                "id": jid,
                "type": kind,
                "payload": payload_dict,
                "note": note,
                "target_dm": effective_dm,
                "target_peer": peer_id,
                "place_id": effective_place,
                "dm": None,
                "claimedBy": None,
                "status": "queued",
                "created": _now(),
                "claimed": None,
                "finished": None,
                "duration_ms": None,
                "ok": None,
                "result": None,
                "error": None,
                "logs": [],
            }
            self.jobs[jid] = job
            self.job_events[jid] = done_evt
            self.pending.append(jid)
            self.history.append(jid)
            while len(self.jobs) > 600:
                oldest = next(iter(self.jobs))
                if oldest == jid:
                    break
                self.jobs.pop(oldest, None)
                self.job_events.pop(oldest, None)
            self.poll_cv.notify_all()
            return job, done_evt

    def complete_job(self, body: dict) -> bool:
        jid = body.get("id")
        with self.lock:
            job = self.jobs.get(jid)
            if not job:
                return False
            now = _now()
            job["status"] = "done" if body.get("ok") else "error"
            job["ok"] = bool(body.get("ok"))
            job["result"] = body.get("result")
            job["error"] = body.get("error")
            job["logs"] = body.get("logs") or []
            if body.get("peerId"):
                job["claimedBy"] = body["peerId"]
            if body.get("dm"):
                job["dm"] = body["dm"]
            job["finished"] = now
            start_t = job.get("claimed") or job["created"]
            job["duration_ms"] = round((now - start_t) * 1000, 1)
            evt = self.job_events.get(jid)
            if evt:
                evt.set()
            return True

    def cancel_job(self, jid: str) -> dict:
        with self.lock:
            job = self.jobs.get(jid)
            if not job:
                raise KeyError(f"Unknown job id: {jid}")
            if job["status"] == "queued":
                try:
                    self.pending.remove(jid)
                except ValueError:
                    pass
                job["status"] = "cancelled"
                job["ok"] = False
                job["error"] = "Cancelled before execution"
                job["finished"] = _now()
                evt = self.job_events.get(jid)
                if evt:
                    evt.set()
            return dict(job)

    def clear_queue(self) -> int:
        count = 0
        with self.lock:
            while self.pending:
                jid = self.pending.popleft()
                job = self.jobs.get(jid)
                if job and job["status"] == "queued":
                    job["status"] = "cancelled"
                    job["ok"] = False
                    job["error"] = "Queue cleared"
                    job["finished"] = _now()
                    evt = self.job_events.get(jid)
                    if evt:
                        evt.set()
                    count += 1
        return count

    # -- snapshots -----------------------------------------------------------

    def _load_snapshots(self):
        try:
            files = sorted(SNAPSHOT_DIR.glob("snap-*.json.gz"), key=lambda p: p.stat().st_mtime)
            loaded = []
            for path in files[-SNAPSHOT_RETENTION:]:
                try:
                    with gzip.open(path, "rt", encoding="utf-8") as stream:
                        snap = json.load(stream)
                    if isinstance(snap, dict) and isinstance(snap.get("scripts"), list):
                        loaded.append(snap)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
            self.snapshots.extend(loaded)
        except OSError:
            pass

    def _persist_snapshot(self, snap: dict):
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        target = SNAPSHOT_DIR / f"{snap['id']}.json.gz"
        temp = target.with_suffix(".tmp")
        with gzip.open(temp, "wt", encoding="utf-8") as stream:
            json.dump(snap, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp, target)
        files = sorted(SNAPSHOT_DIR.glob("snap-*.json.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[SNAPSHOT_RETENTION:]:
            try:
                old.unlink()
            except OSError:
                pass

    def handle_snapshots(self, payload: dict) -> dict:
        action = payload.get("action", "list")
        with self.lock:
            if action == "save":
                snap = {
                    "id": "snap-" + uuid.uuid4().hex[:8],
                    "label": payload.get("label") or "snapshot",
                    "root": payload.get("root") or "game",
                    "created": _now(),
                    "scriptCount": len(payload.get("scripts") or []),
                    "scripts": payload.get("scripts") or [],
                }
                self.snapshots.append(snap)
                self._persist_snapshot(snap)
                return {k: v for k, v in snap.items() if k != "scripts"}
            if action == "list":
                items = [
                    {k: v for k, v in s.items() if k != "scripts"}
                    for s in reversed(self.snapshots)
                ]
                return {"snapshots": items, "count": len(items)}
            if action == "get":
                sid = payload.get("id")
                for s in self.snapshots:
                    if s["id"] == sid:
                        return dict(s)
                raise KeyError(f"Snapshot not found: {sid}")
        raise ValueError(f"Unknown snapshot action: {action}")

    # -- direct in-process dispatch for MCP engine ---------------------------

    def direct_dispatch(self, op: str, body: dict):
        if op == "health":
            with self.lock:
                online, st = self.legacy_studio_summary_unlocked()
                peers = self._active_peers_unlocked()
                return {
                    "ok": True,
                    "version": VERSION,
                    "read_only": self.read_only,
                    "mcp_tools": len(TOOL_SPECS),
                    "studio_connected": online,
                    "studio": st,
                    "peers": peers,
                    "queued": len(self.pending),
                    "jobs": len(self.jobs),
                }
        if op == "state":
            with self.lock:
                online, st = self.legacy_studio_summary_unlocked()
                peers = self._active_peers_unlocked()
                recent = [summary(self.jobs[i]) for i in list(self.history)[-30:]][::-1]
                return {
                    "version": VERSION,
                    "read_only": self.read_only,
                    "mcp_tools": len(TOOL_SPECS),
                    "studio_connected": online,
                    "studio": st,
                    "peers": peers,
                    "queued": len(self.pending),
                    "snapshots": len(self.snapshots),
                    "recent": recent,
                    "server_time": _now(),
                }
        if op == "peers":
            with self.lock:
                peers = self._active_peers_unlocked()
                return {
                    "peers": peers,
                    "onlineCount": sum(1 for p in peers if p["online"]),
                }
        if op == "snapshots":
            return self.handle_snapshots(body)
        if op == "get_job":
            with self.lock:
                jid = body.get("id")
                if jid not in self.jobs: raise KeyError(f"Unknown job id: {jid}")
                return dict(self.jobs[jid])
        if op == "submit_job":
            job, evt = self.enqueue_job(
                kind=body["type"],
                payload=body.get("payload"),
                note=body.get("note"),
                dm=body.get("dm"),
                peer_id=body.get("peer_id"),
                place_id=body.get("place_id"),
            )
            wait = float(body.get("wait") or 0)
            if wait > 0:
                evt.wait(timeout=min(wait, 120.0))
            with self.lock:
                return dict(self.jobs[job["id"]])
        raise ValueError(f"Unknown internal op: {op}")


def summary(job: dict) -> dict:
    out = job.get("result")
    if isinstance(out, str) and len(out) > 400:
        out = out[:400] + " ..."
    return {
        "id": job["id"],
        "type": job["type"],
        "dm": job.get("dm") or job.get("target_dm"),
        "status": job["status"],
        "note": job["note"],
        "ok": job["ok"],
        "error": job["error"],
        "result": out,
        "duration_ms": job.get("duration_ms"),
        "created": job["created"],
        "finished": job["finished"],
    }


# ----------------------------------------------------------------------------
# HTTP Handler Factory
# ----------------------------------------------------------------------------

def make_http_handler(state: BridgeState):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"ArenaRobloxMCPBridge/{VERSION}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            if os.environ.get("BRIDGE_VERBOSE"):
                super().log_message(fmt, *args)

        def _is_loopback(self) -> bool:
            ip = (self.client_address[0] or "").strip()
            forwarded = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Forwarded-For")
            if forwarded:
                return False
            return ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

        def _send(self, code: int, body, ctype: str = "application/json; charset=utf-8", extra_headers: dict = None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body)
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")
            self.send_header("Cache-Control", "no-store")
            if extra_headers:
                for hk, hv in extra_headers.items():
                    self.send_header(hk, hv)
            self.end_headers()
            try:
                if data:
                    self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _read_body_raw(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return b""
            return self.rfile.read(length)

        def _body(self):
            raw = self._read_body_raw()
            if not raw:
                return {}
            try:
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {"_raw": raw.decode("utf-8", "replace")}

        def _authorized(self, qs: dict) -> bool:
            if not state.token:
                return True
            bearer = self.headers.get("Authorization", "")
            bearer_tok = bearer[7:].strip() if bearer.lower().startswith("bearer ") else None
            supplied = (
                qs.get("token", [None])[0]
                or self.headers.get("X-Arena-Token")
                or bearer_tok
            )
            return supplied == state.token

        def do_OPTIONS(self):
            self._send(204, b"")

        def do_DELETE(self):
            url = urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            if path == "/mcp":
                return self._send(200, {"closed": True})
            return self._send(404, {"error": "not found"})

        def do_GET(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path.rstrip("/") or "/"

            if path == "/":
                return self._send(200, dashboard_html(), "text/html; charset=utf-8")

            if path.startswith("/api/kit/image/"):
                from image_pipeline import find_kit_root
                rel_img = path[len("/api/kit/image/") :].lstrip("/")
                images_root = (find_kit_root() / "assets" / "images").resolve()
                target_img = (images_root / rel_img).resolve()
                if not str(target_img).startswith(str(images_root)) or not target_img.is_file():
                    return self._send(404, {"error": "image not found"})
                mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".bmp": "image/bmp"}
                return self._send(200, target_img.read_bytes(), mime_map.get(target_img.suffix.lower(), "application/octet-stream"))

            if path.startswith("/api/kit/audio/"):
                from image_pipeline import find_kit_root
                rel_aud = path[len("/api/kit/audio/") :].lstrip("/")
                audio_root = (find_kit_root() / "assets" / "audio").resolve()
                target_aud = (audio_root / rel_aud).resolve()
                if not str(target_aud).startswith(str(audio_root)) or not target_aud.is_file():
                    return self._send(404, {"error": "audio not found"})
                amime = {".wav": "audio/wav", ".ogg": "audio/ogg", ".mp3": "audio/mpeg"}
                return self._send(200, target_aud.read_bytes(), amime.get(target_aud.suffix.lower(), "application/octet-stream"))

            # Loopback-only bootstrap so localhost:8077 auto-fills token, paths & tools safely
            if path == "/api/local-boot":
                is_local = self._is_loopback()
                tools_lite = [
                    {
                        "name": t["name"],
                        "description": t["description"],
                        "readOnly": bool(t.get("annotations", {}).get("readOnlyHint")),
                        "required": t.get("inputSchema", {}).get("required", []),
                        "properties": list((t.get("inputSchema", {}).get("properties") or {}).keys()),
                    }
                    for t in TOOL_SPECS
                ]
                return self._send(
                    200,
                    {
                        "local": is_local,
                        "token": state.token if is_local else "",
                        "tokenRequired": bool(state.token),
                        "bridgeDir": str(HERE) if is_local else "",
                        "mcpServerPath": str(HERE / "mcp_server.py") if is_local else "roblox-bridge/mcp_server.py",
                        "cliPath": str(HERE / "arena_studio.py") if is_local else "roblox-bridge/arena_studio.py",
                        "tools": tools_lite,
                    },
                )

            if path == "/mcp":
                if not self._authorized(qs):
                    return self._send(401, {"error": "unauthorized"})
                return self._send(
                    200,
                    {
                        "name": "arena-roblox-studio-mcp",
                        "version": VERSION,
                        "transport": "streamable-http",
                        "tools": len(TOOL_SPECS),
                        "readOnly": state.read_only,
                    },
                    extra_headers={"Mcp-Session-Id": state.session_id},
                )

            if path == "/api/health":
                return self._send(200, state.direct_dispatch("health", {}))

            if path == "/api/self-test":
                if not self._authorized(qs):
                    return self._send(401, {"error": "bad token"})
                return self._send(200, run_bridge_self_test(state.mcp_client, include_studio_smoke=True, timeout=180))

            if path == "/api/state":
                return self._send(200, state.direct_dispatch("state", {}))

            if path == "/api/agent-guide":
                if not self._authorized(qs) and not self._is_loopback():
                    return self._send(401, {"error": "bad token"})
                host_hdr = self.headers.get("Host") or "127.0.0.1:8077"
                proto = "https" if self.headers.get("CF-Connecting-IP") else "http"
                return self._send(
                    200,
                    build_agent_guide(state.direct_dispatch("state", {}), origin=f"{proto}://{host_hdr}"),
                )

            if path == "/api/peers":
                if not self._authorized(qs):
                    return self._send(401, {"error": "bad token"})
                return self._send(200, state.direct_dispatch("peers", {}))

            if path == "/api/snapshots":
                if not self._authorized(qs):
                    return self._send(401, {"error": "bad token"})
                return self._send(200, state.handle_snapshots({"action": "list"}))

            if path.startswith("/api/snapshots/"):
                if not self._authorized(qs):
                    return self._send(401, {"error": "bad token"})
                sid = path.rsplit("/", 1)[-1]
                try:
                    return self._send(200, state.handle_snapshots({"action": "get", "id": sid}))
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})

            # Studio plugin long-polls here
            if path == "/api/poll":
                if not self._authorized(qs):
                    return self._send(401, {"error": "bad token"})
                peer_info = state.record_poll(qs)
                job = state.poll_for_job(peer_info, wait_seconds=8.0)
                return self._send(200, {"job": job})

            if path.startswith("/api/jobs/"):
                if not self._authorized(qs) and not self._is_loopback():
                    return self._send(401, {"error": "bad token"})
                job_id = path.rsplit("/", 1)[-1]
                with state.lock:
                    job = state.jobs.get(job_id)
                if not job:
                    return self._send(404, {"error": "no such job"})
                return self._send(200, job)

            if path == "/api/jobs":
                if not self._authorized(qs) and not self._is_loopback():
                    return self._send(401, {"error": "bad token"})
                with state.lock:
                    return self._send(200, {"jobs": [summary(state.jobs[i]) for i in state.history]})

            return self._send(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path.rstrip("/") or "/"

            if not self._authorized(qs):
                return self._send(401, {"error": "bad token"})

            # Native MCP Streamable HTTP endpoint (JSON-RPC 2.0)
            if path == "/mcp":
                raw = self._read_body_raw()
                try:
                    rpc_in = json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    return self._send(
                        400,
                        {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}},
                        extra_headers={"Mcp-Session-Id": state.session_id},
                    )
                rpc_out = state.mcp_dispatcher.handle_payload(rpc_in)
                if rpc_out is None:
                    return self._send(202, b"", "text/plain; charset=utf-8", {"Mcp-Session-Id": state.session_id})
                return self._send(200, rpc_out, extra_headers={"Mcp-Session-Id": state.session_id})

            body = self._body()

            # Enqueue job for Studio
            if path == "/api/jobs":
                kind = body.get("type")
                if not kind:
                    return self._send(400, {"error": "type is required"})
                try:
                    job, evt = state.enqueue_job(
                        kind=kind,
                        payload=body.get("payload"),
                        note=body.get("note"),
                        dm=body.get("dm"),
                        peer_id=body.get("peer_id"),
                        place_id=body.get("place_id"),
                    )
                except PermissionError as exc:
                    return self._send(403, {"error": str(exc)})

                wait = float(body.get("wait") or 0)
                if wait > 0:
                    evt.wait(timeout=min(wait, 120.0))
                    with state.lock:
                        return self._send(200, state.jobs[job["id"]])
                return self._send(201, {"id": job["id"], "status": job["status"]})

            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                parts = path.split("/")
                job_id = parts[3] if len(parts) >= 5 else ""
                try:
                    return self._send(200, state.cancel_job(job_id))
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})

            if path == "/api/queue/clear":
                return self._send(200, {"cancelled": state.clear_queue()})

            if path == "/api/snapshots":
                try:
                    return self._send(200, state.handle_snapshots(body))
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})

            # Studio plugin posts completed job result
            if path == "/api/result":
                if not state.complete_job(body):
                    return self._send(404, {"error": "no such job"})
                return self._send(200, {"ack": True})

            return self._send(404, {"error": "not found"})

    return Handler


# ----------------------------------------------------------------------------
# Self-Contained Control Center & Next-Agent Handoff Hub (GET /)
# ----------------------------------------------------------------------------

def dashboard_html() -> str:
    return r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Roblox Studio MCP, 3D Viewport Vision &amp; Open-Source Kit (v4.0)</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root{color-scheme:dark;--bg:#0b0f16;--panel:#131922;--panel2:#0e131b;--border:#232c3a;
       --txt:#e6edf3;--muted:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--amber:#d29922}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--txt);
      font:13.5px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
 code,pre,textarea,input,select{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
 header{padding:14px 24px;background:var(--panel);border-bottom:1px solid var(--border);
        display:flex;align-items:center;gap:12px;flex-wrap:wrap;position:sticky;top:0;z-index:20}
 h1{font-size:16px;font-weight:700;margin:0;letter-spacing:.2px}
 h2{font-size:15px;font-weight:700;margin:0 0 8px}
 h3{font-size:13.5px;font-weight:700;margin:0 0 6px;color:#c9d1d9}
 .sub{color:var(--muted);font-size:12px}
 .spacer{flex:1}
 .pill{padding:4px 11px;border-radius:999px;font-size:11.5px;font-weight:600;border:1px solid transparent}
 .on{background:#0f2f1b;color:var(--green);border-color:#23562f}
 .off{background:#2d1618;color:var(--red);border-color:#5c2326}
 .ro{background:#2b220c;color:var(--amber);border-color:#5a4414}
 .info-pill{background:#122338;color:var(--accent);border-color:#244870}
 nav{display:flex;gap:6px;padding:10px 24px;background:var(--panel2);border-bottom:1px solid var(--border);
     overflow-x:auto}
 .tab-btn{background:transparent;color:var(--muted);border:1px solid transparent;padding:7px 14px;
          border-radius:8px;cursor:pointer;font-size:13px;font-weight:600;white-space:nowrap}
 .tab-btn:hover{color:var(--txt);background:#17202c}
 .tab-btn.active{color:#fff;background:#1b2838;border-color:#2f4663}
 main{padding:20px 24px;max-width:1280px;margin:0 auto}
 .tab-pane{display:none}
 .tab-pane.active{display:block}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:18px}
 .card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px}
 .card .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
 .card .v{font:600 19px/1.25 ui-monospace,monospace;margin-top:5px}
 .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px;
          padding:12px;background:var(--panel);border:1px solid var(--border);border-radius:10px}
 button,.btn{background:#1f2937;color:var(--txt);border:1px solid #334155;border-radius:7px;
             padding:6px 12px;font-size:12.5px;font-weight:600;cursor:pointer;transition:.12s}
 button:hover{background:#283548;border-color:#4b5f7a}
 button.primary{background:#1f6feb;border-color:#388bfd;color:#fff}
 button.primary:hover{background:#2977f5}
 button.danger{background:#3a181c;border-color:#6e2b31;color:#ff8b85}
 button.sm{padding:4px 9px;font-size:11.5px}
 input,select,textarea{background:var(--panel2);color:var(--txt);border:1px solid var(--border);
                       border-radius:7px;padding:7px 10px;font-size:12.5px;width:100%}
 textarea{min-height:110px;resize:vertical;line-height:1.45}
 table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--border);
       border-radius:10px;overflow:hidden;margin-bottom:18px}
 th{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);
    text-align:left;padding:10px 12px;background:var(--panel2);border-bottom:1px solid var(--border)}
 td{padding:9px 12px;border-bottom:1px solid #1b222e;vertical-align:top;font-size:12.5px}
 tr:last-child td{border-bottom:none}
 tr.clickable:hover td{background:#17202d;cursor:pointer}
 .s-done{color:var(--green)}.s-error,.s-cancelled{color:var(--red)}.s-running{color:var(--amber)}
 .s-queued{color:var(--muted)}
 code{background:var(--panel2);border:1px solid var(--border);border-radius:5px;padding:1px 6px;font-size:12px}
 pre{background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:12px;
     overflow:auto;margin:8px 0 0;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
 .empty{padding:22px;text-align:center;color:var(--muted)}
 .two-col{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:14px;margin-bottom:16px}
 .prompt-card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:15px;
              display:flex;flex-direction:column;gap:8px}
 .prompt-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
 .cmd-row{display:flex;align-items:center;gap:10px;padding:8px 10px;background:var(--panel2);
          border:1px solid var(--border);border-radius:8px;margin-bottom:7px}
 .cmd-row code{background:none;border:none;padding:0;flex:1;overflow-x:auto;white-space:nowrap}
 .cmd-desc{color:var(--muted);font-size:11.5px;min-width:190px;text-align:right}
 .toast{position:fixed;bottom:20px;right:20px;background:#1f6feb;color:#fff;padding:9px 16px;
        border-radius:8px;font-weight:600;font-size:12.5px;opacity:0;pointer-events:none;transition:.2s;z-index:99}
 .toast.show{opacity:1}
 @media(max-width:720px){.two-col{grid-template-columns:1fr}.cmd-desc{display:none}}
</style></head><body>

<header>
  <h1>Roblox Studio MCP &amp; Bridge <span class="sub">v4.0</span></h1>
  <span id="status" class="pill off">checking&hellip;</span>
  <span id="ro-badge" class="pill ro" style="display:none">Read-Only Inspector</span>
  <span id="place" class="pill info-pill" style="display:none"></span>
  <button class="sm" onclick="runSelfTest()">Run Self-Test</button>
  <div class="spacer"></div>
  <span class="sub">Token:</span>
  <input id="tok-input" type="password" placeholder="auto-loaded on localhost" style="width:185px;padding:4px 8px">
  <button class="sm" onclick="toggleToken()">Show</button>
</header>

<nav>
  <button class="tab-btn active" onclick="showTab('monitor', this)">1. Live Monitor</button>
  <button class="tab-btn" onclick="showTab('kit', this)">2. Image Importer &amp; Studio Kit</button>
  <button class="tab-btn" onclick="showTab('prompts', this)">3. Next-Agent Handoff &amp; Prompts</button>
  <button class="tab-btn" onclick="showTab('commands', this)">4. Commands &amp; MCP Config</button>
  <button class="tab-btn" onclick="showTab('playground', this)">5. Live Tool &amp; Luau Runner</button>
  <button class="tab-btn" onclick="showTab('tips', this)">6. Helpful Tips &amp; Gotchas</button>
</nav>

<main>
  <!-- ==================== TAB 1: LIVE MONITOR ==================== -->
  <section id="tab-monitor" class="tab-pane active">
    <div class="grid">
      <div class="card"><div class="k">Studio Peers</div><div class="v" id="c-peers">0 online</div></div>
      <div class="card"><div class="k">MCP Tools</div><div class="v" id="c-tools">68</div></div>
      <div class="card"><div class="k">Queued</div><div class="v" id="c-queued">0</div></div>
      <div class="card"><div class="k">Completed</div><div class="v" id="c-done">0</div></div>
      <div class="card"><div class="k">Failed</div><div class="v" id="c-fail">0</div></div>
      <div class="card"><div class="k">Saved Snapshots</div><div class="v" id="c-snaps">0</div></div>
    </div>

    <div class="toolbar">
      <strong style="font-size:12.5px;margin-right:4px">Quick Actions:</strong>
      <button class="primary sm" onclick="quickTool('ping_studio', {})">Ping Studio</button>
      <button class="sm" onclick="quickTool('get_studio_state', {})">Studio State</button>
      <button class="sm" onclick="quickTool('survey_place', {includeSource:false})">Survey Place</button>
      <button class="sm" onclick="quickTool('get_console_output', {limit:40})">Console Output</button>
      <button class="sm" onclick="quickTool('list_studios', {})">List Peers</button>
      <button class="sm" onclick="quickTool('undo_redo', {action:'undo'})">Undo (Ctrl+Z)</button>
      <button class="sm" onclick="quickTool('undo_redo', {action:'redo'})">Redo</button>
      <button class="sm" onclick="quickTool('export_place_rbxlx', {outputFile:'roblox-kit/models/StarterPlace.rbxlx'})">Export .rbxlx Place</button>
      <div class="spacer"></div>
      <button class="danger sm" onclick="clearQueue()">Clear Queue</button>
    </div>

    <div class="two-col" style="margin-bottom:16px">
      <div class="card">
        <div class="prompt-head" style="flex-wrap:wrap;gap:8px;margin-bottom:8px">
          <div>
            <h2>Live 3D Place + HUD Viewport Vision</h2>
            <div class="sub">Real-time software rasterizer &amp; HUD compositor (exact same <code>image/png</code> vision stream multimodal AI agents receive from <code>capture_viewport</code>).</div>
          </div>
          <div style="display:flex;gap:5px;flex-wrap:wrap">
            <button class="sm primary" id="cam-perspective" onclick="refresh3DViewport('perspective')">Perspective</button>
            <button class="sm" id="cam-iso_ne" onclick="refresh3DViewport('iso_ne')">Iso NE</button>
            <button class="sm" id="cam-top_blueprint" onclick="refresh3DViewport('top_blueprint')">Top Blueprint</button>
            <button class="sm" id="cam-front" onclick="refresh3DViewport('front')">Front</button>
            <button class="sm" onclick="toggle3DHud()" id="btn-hud-toggle">HUD: ON</button>
          </div>
        </div>
        <div style="background:#070a10;border:1px solid var(--border);border-radius:8px;overflow:hidden;text-align:center">
          <img id="live-viewport-img" alt="Live 3D Place Viewport" style="width:100%;max-height:300px;object-fit:contain;display:block">
        </div>
        <div id="live-viewport-meta" class="sub" style="margin-top:6px">Click any camera preset above to re-render the live Studio Workspace + HUD.</div>
      </div>

      <div class="card">
        <div class="prompt-head" style="flex-wrap:wrap;gap:8px;margin-bottom:8px">
          <div>
            <h2>3D Scene, Physics &amp; Grounding Auditor</h2>
            <div class="sub">Detects unanchored parts, floating geometry gaps, blocked spawns &amp; Z-fighting coplanar surfaces.</div>
          </div>
          <div style="display:flex;gap:5px;flex-wrap:wrap">
            <button class="sm primary" onclick="run3DSceneAudit(false, false)">Audit Scene</button>
            <button class="sm" onclick="run3DSceneAudit(true, true)">Auto-Anchor &amp; Snap Fix</button>
          </div>
        </div>
        <div id="scene-audit-summary" class="sub" style="margin-bottom:8px">Ready to audit <code>Workspace</code> geometry &amp; physics.</div>
        <div id="scene-audit-list" style="display:flex;flex-direction:column;gap:6px;max-height:265px;overflow:auto"></div>
      </div>
    </div>

    <div id="quick-out-wrap" class="card" style="display:none;margin-bottom:18px">
      <div class="prompt-head">
        <strong id="quick-out-title">Result</strong>
        <div>
          <button class="sm" onclick="copyText(document.getElementById('quick-out').textContent)">Copy JSON</button>
          <button class="sm" onclick="document.getElementById('quick-out-wrap').style.display='none'">Close</button>
        </div>
      </div>
      <pre id="quick-out" style="max-height:300px"></pre>
    </div>

    <h3>Connected Studio DataModel Peers</h3>
    <table>
      <thead><tr><th>Peer ID</th><th>DataModel</th><th>Place</th><th>Plugin</th><th>Client Bridge</th><th>State</th></tr></thead>
      <tbody id="peer-rows"><tr><td colspan="6" class="empty">Waiting for Roblox Studio plugin to poll <code>/api/poll</code>&hellip;</td></tr></tbody>
    </table>

    <h3>Recent Jobs <span class="sub">(click any row to inspect full JSON result)</span></h3>
    <table>
      <thead><tr><th>Job ID</th><th>Type</th><th>DM</th><th>Latency</th><th>Note</th><th>Status</th><th>Preview</th></tr></thead>
      <tbody id="rows"><tr><td colspan="7" class="empty">No jobs executed yet.</td></tr></tbody>
    </table>
  </section>

  <!-- ==================== TAB 2: IMAGE IMPORTER & STUDIO KIT ==================== -->
  <section id="tab-kit" class="tab-pane">
    <div class="two-col">
      <div class="prompt-card">
        <h3>Direct Image Importer (Local File &rarr; Repo + Roblox Studio)</h3>
        <div class="sub">Pick any <code>.png</code>, <code>.jpg</code>, or <code>.bmp</code>. Saves to <code>roblox-kit/assets/images/</code>, installs into Studio <code>content/arena_assets/</code> (<code>rbxasset://arena_assets/...</code>) + live <code>EditableImage</code>, updates <code>Assets.lua</code>, and applies to your target instance.</div>
        <input type="file" id="img-file" accept=".png,.jpg,.jpeg,.bmp">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <input id="img-name" placeholder="Asset name (optional, e.g. shop_banner)">
          <input id="img-target" placeholder="Target path (e.g. StarterGui or Workspace.Baseplate)" value="StarterGui">
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <select id="img-mode">
            <option value="screen_gui">Apply: StarterGui Preview (ScreenGui)</option>
            <option value="decal">Apply: Decal on Part</option>
            <option value="texture">Apply: Tiled Texture on Part</option>
            <option value="billboard">Apply: Floating 3D BillboardGui</option>
            <option value="surface_gui">Apply: 3D Poster SurfaceGui</option>
            <option value="auto">Apply: Auto-detect from Target Class</option>
          </select>
          <button class="primary" onclick="uploadBrowserImage()" style="white-space:nowrap">Import Image</button>
        </div>
        <pre id="img-import-out">Ready to import any image file directly into Roblox Studio.</pre>
      </div>

      <div class="prompt-card">
        <div class="prompt-head">
          <div>
            <h3>1-Click Full Stud UI &amp; Gameplay System Templates</h3>
            <div class="sub">Installs <code>StudUI</code> / <code>ModernUI</code> + icons, builds live editable <code>ScreenGui</code>s in <code>StarterGui</code>, and wires controllers &amp; server economy.</div>
          </div>
          <button class="primary sm" onclick="installAllModules()">Install All 19 Luau Modules</button>
        </div>
        <div id="template-list" style="display:flex;flex-direction:column;gap:7px"></div>
        <pre id="kit-action-out">Click any template, system, prefab, audio clip, or module button to install straight into Studio.</pre>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <div class="prompt-head" style="flex-wrap:wrap;gap:10px">
        <div>
          <h2>Multi-Device Live UI Preview &amp; Layout Linter (Desktop / Tablet / Mobile)</h2>
          <div class="sub">Inspect exact pixel bounds, real PNG sprites, overflow warnings, <code>UIScale</code> responsiveness, and touch targets across <strong>1920&times;1080</strong>, <strong>1024&times;768</strong>, and <strong>844&times;390</strong>.</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <select id="ui-audit-src" onchange="runUiPreviewAndAudit()" style="width:240px">
            <option value="prefab:ui_kenney_shop_window">Prefab: ui_kenney_shop_window</option>
            <option value="prefab:ui_minimap_radar">Prefab: ui_minimap_radar</option>
            <option value="prefab:ui_hotbar_abilities">Prefab: ui_hotbar_abilities</option>
            <option value="prefab:ui_mobile_touch_pad">Prefab: ui_mobile_touch_pad</option>
            <option value="prefab:ui_rank_up_banner">Prefab: ui_rank_up_banner</option>
            <option value="prefab:ui_fantasy_quest_dialog">Prefab: ui_fantasy_quest_dialog</option>
            <option value="prefab:ui_scifi_hud_visor">Prefab: ui_scifi_hud_visor</option>
            <option value="prefab:ui_input_prompt_bar">Prefab: ui_input_prompt_bar</option>
            <option value="prefab:hud_modern_shell">Prefab: hud_modern_shell</option>
            <option value="studio:StarterGui">Live Studio: StarterGui</option>
          </select>
          <button class="sm primary" id="vp-btn-desktop" onclick="switchPreviewViewport('desktop')">Desktop 1920&times;1080</button>
          <button class="sm" id="vp-btn-tablet" onclick="switchPreviewViewport('tablet')">Tablet 1024&times;768</button>
          <button class="sm" id="vp-btn-mobile" onclick="switchPreviewViewport('mobile')">Mobile 844&times;390</button>
          <button class="sm" onclick="runUiPreviewAndAudit()">Re-Audit</button>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1.5fr 1fr;gap:14px;margin-top:12px;align-items:start">
        <div style="background:#070a10;border:1px solid var(--border);border-radius:10px;padding:12px;overflow:hidden">
          <div id="ui-stage-meta" class="sub" style="margin-bottom:8px">Hover any rendered widget to inspect its path &amp; pixel bounds.</div>
          <div id="ui-preview-stage" style="position:relative;margin:0 auto;background:radial-gradient(circle at 50% 35%, #1a2436, #0b0f17);border:1px dashed #334155;overflow:hidden;border-radius:6px"></div>
        </div>
        <div>
          <h3 id="ui-issues-title">Layout Lint Report</h3>
          <div id="ui-issues-list" style="display:flex;flex-direction:column;gap:6px;max-height:330px;overflow:auto"></div>
        </div>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <div class="prompt-head" style="flex-wrap:wrap;gap:10px">
        <div>
          <h2>4,811 Cloud <code>rbxassetid://</code> Icons (1,022 Glossy 3D Stud + 1,894 Lucide Outline + 1,895 Lucide Filled)</h2>
          <div class="sub">Zero-upload cloud icons for chunky simulator UIs (<code>Stud</code>) and sleek dark/minimalist UIs (<code>Lucide Outline</code> + <code>Lucide Filled</code> active states).</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;min-width:360px">
          <button class="sm primary" id="pk-all" onclick="setIconPack('all')">All (4,811)</button>
          <button class="sm" id="pk-stud" onclick="setIconPack('stud')">3D Stud (1,022)</button>
          <button class="sm" id="pk-lucide" onclick="setIconPack('lucide')">Lucide Outline (1,894)</button>
          <button class="sm" id="pk-lucide_fill" onclick="setIconPack('lucide_fill')">Lucide Filled (1,895)</button>
          <input id="cloud-icon-q" placeholder="Search icons (shield, sword, heart, settings, coin, cat)..." oninput="searchCloudIconsUI()" style="width:230px">
        </div>
      </div>
      <div id="cloud-icon-cats" style="display:flex;flex-wrap:wrap;gap:6px;margin:10px 0"></div>
      <div id="cloud-icon-results" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px;margin-top:8px"></div>
    </div>

    <div class="two-col" style="margin-bottom:16px">
      <div class="card">
        <h2>45 Curated CC0 Sound Effects Soundboard (<code>.wav</code> Preview + Send to Studio)</h2>
        <div class="sub" style="margin-bottom:8px">Genuine public-domain Kenney UI &amp; Interface SFX. Click <strong>&#9654;</strong> to audition in browser or <strong>Send</strong> to install in <code>SoundService</code>.</div>
        <audio id="sfx-player" style="display:none"></audio>
        <div id="audio-list" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:6px;max-height:320px;overflow:auto"></div>
      </div>

      <div class="card">
        <h2>14 Drop-In Server/Client Game Systems (<code>roblox-kit/systems/</code>)</h2>
        <div class="sub" style="margin-bottom:8px">Production LiveOps, Monetization (<code>PolicyService</code> compliant), <code>ProfileStore</code> Data, &amp; Cartoon UI systems.</div>
        <div id="dropin-system-list" style="display:flex;flex-direction:column;gap:6px;max-height:320px;overflow:auto"></div>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <div class="prompt-head" style="flex-wrap:wrap;gap:10px">
        <div>
          <h2>Licensed Asset Library (4,320 registered · 3,705 images · 50 skies · 520 models · 45 audio)</h2>
          <div class="sub">Browse source groups, local Studio URIs, SPDX licence, source, and provenance. Kenney assets are pinned CC0; generated skies are 8-bit LDR (not HDRI). GLB thumbnails are neutral placeholders, not model renders. Use <code>search_assets</code> for model/audio/provenance filters; Send imports the selected image directly into Studio.</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <input id="asset-search" placeholder="Filter licensed local images..." oninput="renderFilteredAssets()" style="width:210px">
          <button class="sm" onclick="loadKitGallery()">Refresh</button>
        </div>
      </div>
      <div id="asset-pack-pills" style="display:flex;flex-wrap:wrap;gap:6px;margin:10px 0"></div>
      <div id="asset-gallery" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-top:10px"></div>
    </div>

    <div class="card">
      <div class="prompt-head">
        <div>
          <h2>20 Prefabs &amp; Drag-and-Drop <code>.rbxmx</code> Models (<code>roblox-kit/prefabs/</code> &amp; <code>models/</code>)</h2>
          <div class="sub">Spawn directly into Studio OR export any live Studio path to a standalone <code>.rbxmx</code> model file.</div>
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <input id="export-rbxmx-path" value="StarterGui" placeholder="Studio path to export" style="width:170px">
          <button class="sm primary" onclick="exportLiveRbxmx()">Export .rbxmx</button>
        </div>
      </div>
      <div id="prefab-list" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:8px;margin-top:10px"></div>
    </div>
  </section>

  <!-- ==================== TAB 3: NEXT-AGENT PROMPTS ==================== -->
  <section id="tab-prompts" class="tab-pane">
    <div class="card" style="margin-bottom:16px">
      <div class="prompt-head" style="align-items:center;flex-wrap:wrap">
        <div>
          <h2 style="margin-bottom:2px">Next-Agent Handoff &amp; Generic Starter Prompts</h2>
          <div class="sub">Auto-customized with your live Place name, local paths, and optional tunnel URL. Click <strong>Copy Prompt</strong> and paste straight into your next AI session.</div>
        </div>
        <div style="min-width:300px">
          <input id="tunnel-url" placeholder="Optional public tunnel URL (e.g. https://some-random-words.trycloudflare.com)" oninput="renderPrompts()">
        </div>
      </div>
    </div>

    <div class="two-col" id="prompt-grid"></div>
  </section>

  <!-- ==================== TAB 3: COMMANDS & MCP CONFIG ==================== -->
  <section id="tab-commands" class="tab-pane">
    <div class="two-col">
      <div class="prompt-card">
        <div class="prompt-head">
          <div><h3>Claude Code / Codex CLI (One-Liner)</h3><div class="sub">Registers stdio MCP server with auto-attach to running bridge</div></div>
          <button class="primary sm" onclick="copyById('cfg-cli')">Copy</button>
        </div>
        <pre id="cfg-cli"></pre>
      </div>

      <div class="prompt-card">
        <div class="prompt-head">
          <div><h3>Streamable HTTP MCP Config (Local or Tunnel)</h3><div class="sub">Direct HTTP JSON-RPC 2.0 endpoint on <code>POST /mcp</code></div></div>
          <button class="primary sm" onclick="copyById('cfg-http')">Copy</button>
        </div>
        <pre id="cfg-http"></pre>
      </div>

      <div class="prompt-card">
        <div class="prompt-head">
          <div><h3><code>.mcp.json</code> / Cursor / Claude Desktop (Full Access)</h3><div class="sub">All 68 tools enabled (3D viewport vision + 3D physics QA + .rbxlx + UI linter + LiveOps + sync)</div></div>
          <button class="primary sm" onclick="copyById('cfg-json')">Copy</button>
        </div>
        <pre id="cfg-json"></pre>
      </div>

      <div class="prompt-card">
        <div class="prompt-head">
          <div><h3><code>.mcp.json</code> Read-Only Inspector Profile</h3><div class="sub">Blocks all mutating calls server-side for zero-risk exploration</div></div>
          <button class="primary sm" onclick="copyById('cfg-ro')">Copy</button>
        </div>
        <pre id="cfg-ro"></pre>
      </div>
    </div>

    <div class="card">
      <h2>Generic CLI Command Cheat Sheet (<code>.\ab.ps1</code> or <code>python arena_studio.py</code>)</h2>
      <p class="sub" style="margin-top:0">Works identically for any Roblox place or repository. Replace sample paths like <code>./src</code> or <code>ServerScriptService.App</code> with your project's paths.</p>
      <div id="cmd-list"></div>
    </div>
  </section>

  <!-- ==================== TAB 4: LIVE TOOL & LUAU RUNNER ==================== -->
  <section id="tab-playground" class="tab-pane">
    <div class="two-col">
      <div class="prompt-card">
        <h3>Run Live Luau in Roblox Studio</h3>
        <div class="sub">Executes inside a single undoable scope (or read-only probe) in your chosen DataModel.</div>
        <div style="display:flex;gap:8px;align-items:center">
          <select id="luau-dm" style="width:210px">
            <option value="edit">DataModel: edit (Place Editor)</option>
            <option value="server">DataModel: server (Play Server)</option>
            <option value="client">DataModel: client (Play Client)</option>
          </select>
          <button class="primary" onclick="runLiveLuau()">Execute Luau</button>
        </div>
        <textarea id="luau-code">return {
  placeName = game.Name,
  placeId = game.PlaceId,
  workspaceChildren = #workspace:GetChildren(),
  services = { "Workspace", "ReplicatedStorage", "ServerScriptService" },
}</textarea>
        <pre id="luau-out">Output will appear here.</pre>
      </div>

      <div class="prompt-card">
        <h3>Invoke Any of the 68 MCP Tools Directly</h3>
        <div class="sub" id="tool-desc">Select any MCP tool to inspect its fields and call it over <code>POST /mcp</code>.</div>
        <div style="display:flex;gap:8px;align-items:center">
          <select id="tool-select" onchange="onSelectTool()"></select>
          <button class="primary" onclick="callSelectedTool()">Call MCP Tool</button>
        </div>
        <textarea id="tool-args">{}</textarea>
        <pre id="tool-out">Structured MCP output will appear here.</pre>
      </div>
    </div>
  </section>

  <!-- ==================== TAB 5: HELPFUL TIPS & GOTCHAS ==================== -->
  <section id="tab-tips" class="tab-pane">
    <div class="two-col" id="tips-grid"></div>
  </section>
</main>

<div id="toast" class="toast">Copied to clipboard</div>

<script>
const esc = v => String(v==null?'':v).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
let BOOT = { local:false, token:'', bridgeDir:'roblox-bridge', mcpServerPath:'roblox-bridge/mcp_server.py', tools:[] };
let LAST_STATE = {};

function toast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg || 'Copied to clipboard';
  t.classList.add('show');
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => t.classList.remove('show'), 1800);
}

async function copyText(txt){
  try { await navigator.clipboard.writeText(txt); }
  catch(_){
    const ta = document.createElement('textarea');
    ta.value = txt; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
  }
  toast('Copied to clipboard');
}

function copyById(id){ copyText(document.getElementById(id).textContent); }

function toggleToken(){
  const inp = document.getElementById('tok-input');
  inp.type = inp.type === 'password' ? 'text' : 'password';
}

function getToken(){ return document.getElementById('tok-input').value.trim(); }
function authHeaders(){
  const h = {'Content-Type':'application/json'};
  const t = getToken();
  if(t) h['Authorization'] = 'Bearer ' + t;
  return h;
}

async function runSelfTest(){
  toast('Running isolated bridge suite and Studio scratch test…');
  const response = await fetch('/api/self-test', {headers: authHeaders()});
  const result = await response.json();
  if(!response.ok) throw new Error(result.error || ('HTTP ' + response.status));
  const title = document.getElementById('quick-out-title');
  const wrap = document.getElementById('quick-out-wrap');
  const pre = document.getElementById('quick-out');
  title.textContent = 'v4.0 Self-Test — ' + (result.ok ? 'PASS' : 'FAIL');
  pre.textContent = result.humanTable + '\n\n' + JSON.stringify({suite:result.suite,studioSmoke:result.studioSmoke,toolCount:(result.toolMatrix||[]).length}, null, 2);
  wrap.style.display = 'block';
  toast(result.ok ? 'Self-test passed.' : 'Self-test reported failures or skipped Studio checks.');
}

function showTab(name, btn){
  document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}

async function callMcp(toolName, argsObj){
  const r = await fetch('/mcp', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 'web-' + Date.now(),
      method: 'tools/call',
      params: { name: toolName, arguments: argsObj || {} }
    })
  });
  const data = await r.json();
  if(!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
  return data.result;
}

async function quickTool(toolName, argsObj){
  const wrap = document.getElementById('quick-out-wrap');
  const pre = document.getElementById('quick-out');
  document.getElementById('quick-out-title').textContent = 'MCP Tool: ' + toolName + ' (running...)';
  wrap.style.display = 'block';
  pre.textContent = 'Waiting for Studio peer response...';
  try {
    const res = await callMcp(toolName, argsObj);
    document.getElementById('quick-out-title').textContent = 'MCP Tool: ' + toolName + (res.isError ? ' [ERROR]' : ' [OK]');
    pre.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    tick();
  } catch (err) {
    document.getElementById('quick-out-title').textContent = 'MCP Tool: ' + toolName + ' [FAILED]';
    pre.textContent = String(err);
  }
}

async function clearQueue(){
  await fetch('/api/queue/clear', { method:'POST', headers:authHeaders(), body:'{}' });
  toast('Queue cleared');
  tick();
}

async function inspectJob(jobId){
  const r = await fetch('/api/jobs/' + encodeURIComponent(jobId), { headers: authHeaders() });
  const j = await r.json();
  const wrap = document.getElementById('quick-out-wrap');
  document.getElementById('quick-out-title').textContent = 'Job #' + jobId + ' (' + (j.type || '') + ')';
  document.getElementById('quick-out').textContent = JSON.stringify(j, null, 2);
  wrap.style.display = 'block';
}

async function runLiveLuau(){
  const dm = document.getElementById('luau-dm').value;
  const code = document.getElementById('luau-code').value;
  const out = document.getElementById('luau-out');
  out.textContent = 'Executing in ' + dm + ' DataModel...';
  try {
    const res = await callMcp('execute_luau', { code, dm });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    tick();
  } catch (e) { out.textContent = String(e); }
}

const SAMPLE_ARGS = {
  survey_place: { includeSource: false },
  inspect_instance: { path: 'Workspace', depth: 2, descendantSummary: true },
  search_game_tree: { query: 'Spawn', root: 'game', limit: 30 },
  get_properties: { paths: ['Workspace'] },
  compare_instances: { pathA: 'Workspace.PartA', pathB: 'Workspace.PartB' },
  script_read: { path: 'ServerScriptService.Main', startLine: 1, endLine: 80, withLineNumbers: true },
  script_search: { query: 'Config' },
  script_grep: { pattern: 'RemoteEvent', root: 'game' },
  get_console_output: { limit: 50, level: 'all' },
  luau_check: { code: 'local x = Vector3.new(1, 2, 3)\nreturn tostring(x)\n' },
  drift_and_sync: { mode: 'check', localDir: './src', studioRoot: 'ServerScriptService.App' },
  manage_snapshots: { action: 'list' }
};

function onSelectTool(){
  const sel = document.getElementById('tool-select').value;
  const meta = BOOT.tools.find(t => t.name === sel);
  if(meta){
    document.getElementById('tool-desc').textContent =
      (meta.readOnly ? '[Read-Only] ' : '[Mutating] ') + meta.description +
      (meta.properties.length ? ' | Fields: ' + meta.properties.join(', ') : '');
  }
  document.getElementById('tool-args').value = JSON.stringify(SAMPLE_ARGS[sel] || {}, null, 2);
}

async function callSelectedTool(){
  const sel = document.getElementById('tool-select').value;
  const out = document.getElementById('tool-out');
  out.textContent = 'Calling ' + sel + '...';
  try {
    const parsed = JSON.parse(document.getElementById('tool-args').value || '{}');
    const res = await callMcp(sel, parsed);
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    tick();
  } catch (e) { out.textContent = String(e); }
}

function renderConfigsAndCommands(){
  const mcpPath = (BOOT.mcpServerPath || 'roblox-bridge/mcp_server.py').replace(/\\/g, '/');
  const tok = getToken() || '<YOUR_BRIDGE_TOKEN>';
  const pubUrl = (document.getElementById('tunnel-url').value.trim() || window.location.origin).replace(/\/+$/, '');

  document.getElementById('cfg-cli').textContent =
`# Claude Code
claude mcp add roblox-studio -- python "${mcpPath}" --auto-install-plugin

# OpenAI Codex CLI
codex mcp add roblox-studio -- python "${mcpPath}" --auto-install-plugin`;

  document.getElementById('cfg-http').textContent = JSON.stringify({
    mcpServers: {
      "roblox-studio-http": {
        type: "http",
        url: pubUrl + "/mcp",
        headers: { Authorization: "Bearer " + tok }
      }
    }
  }, null, 2);

  document.getElementById('cfg-json').textContent = JSON.stringify({
    mcpServers: {
      "roblox-studio": {
        command: "python",
        args: [mcpPath, "--auto-install-plugin"],
        env: { BRIDGE_URL: window.location.origin }
      }
    }
  }, null, 2);

  document.getElementById('cfg-ro').textContent = JSON.stringify({
    mcpServers: {
      "roblox-studio-inspector": {
        command: "python",
        args: [mcpPath, "--read-only"],
        env: { BRIDGE_URL: window.location.origin }
      }
    }
  }, null, 2);

  const cmds = [
    [".\\ab.ps1 health", "Check bridge health, connected peers & MCP status"],
    [".\\ab.ps1 ping", "Round-trip ping to active Roblox Studio peer"],
    [".\\ab.ps1 state", "Full Studio state (PlaceId, memory, FPS, undo state)"],
    [".\\ab.ps1 survey", "Map entire open place to survey.json"],
    [".\\ab.ps1 tree Workspace --depth 3", "Inspect DataModel hierarchy to depth 3"],
    [".\\ab.ps1 search Spawn --root Workspace", "Search instances by name/class/tag/attribute"],
    [".\\ab.ps1 grep RemoteEvent --root ReplicatedStorage", "Search code across all scripts in Studio"],
    [".\\ab.ps1 read ServerScriptService.Main --start 1 --end 90 --numbers", "Read script slice with line numbers"],
    [".\\ab.ps1 edit ServerScriptService.Main --old \"DEBUG = false\" --new \"DEBUG = true\" --replace", "Surgical edit + bust ModuleScript cache"],
    [".\\ab.ps1 check ./src", "Offline Luau bytecode + undefined-global check"],
    [".\\ab.ps1 drift ./src ServerScriptService.App", "Compare local folder vs Studio via exact 32-bit FNV-1a"],
    [".\\ab.ps1 sync ./src ServerScriptService.App", "Validate + snapshot + atomic single-undo sync to Studio"],
    [".\\ab.ps1 import-image ./icon.png --target StarterGui --apply-mode screen_gui", "Import PNG/JPG into repo + Studio directly"],
    [".\\ab.ps1 gen-image icon_coin --size 128 --target StarterGui --apply-mode screen_gui", "Generate crisp procedural icon/texture PNG"],
    [".\\ab.ps1 viewport --preset perspective --out viewport.png", "Render shaded 3D Workspace + HUD PNG frame"],
    [".\\ab.ps1 audit-3d --fix-unanchored --snap-floating", "Audit & auto-fix unanchored/floating 3D geometry & spawns"],
    [".\\ab.ps1 export-place --out StarterPlace.rbxlx", "Export full live Studio place (or offline starter place) to .rbxlx"],
    [".\\ab.ps1 icons \"shield\" --pack lucide_fill", "Search 4,811 cloud icons across Stud, Lucide Outline & Lucide Filled"],
    [".\\ab.ps1 system liveops/codes", "Install any of 14 drop-in LiveOps / Monetization / Data systems"],
    [".\\ab.ps1 import-audio click_001.wav --target SoundService", "Import any of 45 CC0 WAVs or custom audio straight into Studio"],
    [".\\ab.ps1 audit-ui --path StarterGui", "Lint responsive UI bounds across Desktop, Tablet & Mobile"],
    [".\\ab.ps1 export-rbxmx StarterGui --out ./ui.rbxmx", "Export live Studio hierarchy to .rbxmx XML model file"],
    [".\\ab.ps1 template full_stud_ui_suite", "Install full HUD + Shop + Daily + Rebirth + Index + Economy"],
    [".\\ab.ps1 install-module all --parent ReplicatedStorage.Shared", "Install all 19 reusable Luau modules (StudUI, ModernUI, WidgetKit, etc.)"],
    [".\\ab.ps1 prefab ui_minimap_radar", "Spawn Minimap Radar / Ability Hotbar / Quest NPC / Kenney Shop prefab"],
    [".\\ab.ps1 scenario --mode play --server-code \"return {ok=true}\"", "Run automated Play Solo test & stop cleanly"]
  ];
  document.getElementById('cmd-list').innerHTML = cmds.map(([c, d]) => `
    <div class="cmd-row">
      <code>${esc(c)}</code>
      <span class="cmd-desc">${esc(d)}</span>
      <button class="sm" onclick="copyText(${JSON.stringify(c).replace(/"/g,'&quot;')})">Copy</button>
    </div>`).join('');
}

function renderPrompts(){
  const placeName = (LAST_STATE.studio && LAST_STATE.studio.place) ? LAST_STATE.studio.place : 'my open Roblox place';
  const placeId = (LAST_STATE.studio && LAST_STATE.studio.place_id) ? LAST_STATE.studio.place_id : 0;
  const dir = BOOT.bridgeDir || 'roblox-bridge';
  const tok = getToken() || '<PASTE_BRIDGE_TOKEN>';
  const pubUrl = (document.getElementById('tunnel-url').value.trim() || window.location.origin).replace(/\/+$/, '');

  const prompts = [
    {
      title: "1. Next-Agent Handoff — HANDS MODE (Local Terminal Relay, No Tunnel Needed)",
      sub: "Best when the next AI agent is in a cloud sandbox without tunnel access and you paste command outputs.",
      body:
`We are working on a Roblox Studio project (${placeName}${placeId ? ', PlaceId ' + placeId : ''}).
Roblox Studio MCP Bridge v4.0.0 is already running locally on my machine at ${window.location.origin} (directory: \`${dir}\`).

Use **HANDS MODE**: give me exact commands to run in PowerShell from \`${dir}\` using \`.\\ab.ps1 <subcommand>\` (or \`python arena_studio.py <subcommand>\`), and I will paste the output back.

Available high-signal commands:
- \`.\\ab.ps1 state\` / \`.\\ab.ps1 survey\` / \`.\\ab.ps1 tree Workspace --depth 3\`
- \`.\\ab.ps1 grep "<pattern>" --root game\` / \`.\\ab.ps1 read <Studio.Script.Path> --start 1 --end 120 --numbers\`
- \`.\\ab.ps1 check <local_folder_or_lua_file>\` (runs offline luau-compile + luau-analyze undefined-global check — ALWAYS run before pushing edits!)
- \`.\\ab.ps1 drift <local_dir> <Studio.Root>\` & \`.\\ab.ps1 sync <local_dir> <Studio.Root>\` (validates + snapshots + syncs in 1 Ctrl+Z step)
- \`.\\ab.ps1 edit <Studio.Script.Path> --old "..." --new "..." --replace\`
- \`.\\ab.ps1 scenario --mode play --server-code "..." --client-code "..."\`

Start by running a quick recon (\`.\\ab.ps1 state\` and \`.\\ab.ps1 survey\`) so you have ground truth of the open place before making changes.`
    },
    {
      title: "2. Next-Agent Handoff — Direct Tunnel / Streamable HTTP MCP + REST",
      sub: "Best when sharing a Cloudflare Quick Tunnel (`python setup.py --tunnel`) or local HTTP connection with an agent.",
      body:
`Connect directly to my live Roblox Studio Bridge & MCP Server:
- Base URL: ${pubUrl}
- Auth Header: \`Authorization: Bearer ${tok}\` (or \`X-Arena-Token: ${tok}\`)
- Native MCP Streamable HTTP Endpoint: \`POST ${pubUrl}/mcp\` (68 tools, 16 skills, multimodal 3D viewport vision, 3D physics auditor, runtime mesh capability probe, .rbxlx place export, 14 LiveOps/Monetization systems, 520 3D models, 3,705 raster images + 50 LDR skies (300 cube faces), 45 CC0 WAVs, 4,811 cloud icons)
- Agent Onboarding & Live State Endpoint: \`GET ${pubUrl}/api/agent-guide\`
- Built-in Repo Kit: call \`browse_kit\` for 16 skills, 19 modules, 6 templates, 20 prefabs, 3,755 image/sky-face files, 50 LDR sky panoramas, 520 models, and 45 CC0 audio files. Use \`search_assets\` for paginated type/category/source/license/provenance filters; exact Git blob matches and pack-level attestations are distinguished. Send supported images/audio/models into Studio with \`import_image\`, \`import_audio\`, and \`import_rbxmx\`.

Rules of engagement:
1. Inspect live ground truth first (\`get_studio_state\`, \`survey_place\` with \`includeSource=false\`, and \`browse_kit\`).
2. Validate Luau edits with \`luau_check\` before writing, and create a rollback point via \`manage_snapshots(action="create")\` before multi-script changes.
3. Pass \`replaceInstance=true\` when modifying \`ModuleScript\`s that may already be cached by \`require()\`.
4. Verify behaviour via \`run_playtest_scenario\` across \`server\` and \`client\` DataModels and check \`get_console_output\` for zero errors.
5. Run \`self_test\` before completion; Studio-only scratch checks are \`no_peer\` skips when no peer is connected, never passes.
6. Use \`mesh_capabilities\` to probe runtime APIs. A Studio version string is not proof of EditableMesh support; report the actual probe result and limitations.
7. Run \`python3 -B tools/verify_licences.py\` before asset changes are accepted. Do not present pack-level attestations as exact file matches, or LDR skies/placeholders as HDRIs/rendered mesh previews.`
    },
    {
      title: "3. Generic Prompt — Full Place Recon & Architecture Map (Read-Only Safe)",
      sub: "Drop into any existing or unfamiliar Roblox place to get a complete architecture breakdown first.",
      body:
`Survey my currently open Roblox Studio place without modifying anything yet:
1. Call \`get_studio_state\` and \`survey_place(includeSource=false)\`.
2. Inspect the hierarchy of \`Workspace\`, \`ReplicatedStorage\`, \`ServerScriptService\`, \`ServerStorage\`, \`StarterPlayer\`, and \`StarterGui\`.
3. Read key entry-point scripts and shared \`ModuleScript\`s with \`script_read\`, and map every \`RemoteEvent\` / \`RemoteFunction\` / \`CollectionService\` tag.
4. Give me a clear architecture map, entry points, data flow between Server and Client, and top improvement opportunities before we change anything.`
    },
    {
      title: "4. Generic Prompt — Build a Complete Feature + Playtest Verification Loop",
      sub: "End-to-end engineering workflow using 3D instance building, verified Luau scripts, and automated Play Solo checks.",
      body:
`I want to build the following feature in my open Roblox Studio place:
[DESCRIBE FEATURE HERE — e.g. an interactive shop / round system / obby checkpoint / ability tool]

Follow this exact loop:
1. Survey existing services/remotes/tags first so naming and structure match the place.
2. Create a safety snapshot with \`manage_snapshots(action="create")\`.
3. Build any needed 3D models/UI/remotes with \`build_tree\` (using clean hierarchy, anchored parts where appropriate, attributes/tags).
4. Check all Luau code with \`luau_check\`, then write/edit scripts using \`write_script\` or \`multi_edit\` (\`replaceInstance=true\` on ModuleScripts).
5. Run \`run_playtest_scenario(mode="play", serverCode=..., clientCode=...)\` and check \`get_console_output\` to verify everything works cleanly with zero warnings or errors.`
    },
    {
      title: "5. Generic Prompt — Diagnose & Fix a Runtime Bug",
      sub: "Uses console cursors, cross-script grep, fresh module isolation, and surgical edits.",
      body:
`Help me track down and fix a bug in my Roblox Studio place:
Symptom: [DESCRIBE WHAT HAPPENS OR PASTE ERROR MESSAGE]

Workflow:
1. Check recent logs with \`get_console_output\` and search relevant code across the whole place with \`script_grep\` and \`script_search\`.
2. Inspect any referenced instances/properties/attributes/tags in the tree with \`inspect_instance\` and \`get_properties\`.
3. Isolate module logic with \`require_fresh\` or reproduce in live Play mode with \`run_playtest_scenario\`.
4. Explain the exact root cause, apply a minimal surgical fix with \`multi_edit\` (after \`luau_check\`), and re-run the playtest scenario to prove the fix.`
    },
    {
      title: "6. Generic Prompt — Audit Place Security, Performance & Code Quality",
      sub: "Finds unvalidated Remotes, embedded free-model scripts, unanchored physics leaks, and undefined globals.",
      body:
`Run a full technical audit of my open Roblox place (read-only first — do not modify anything until I approve):
1. Security: find every \`RemoteEvent\` / \`RemoteFunction\` and audit all \`OnServerEvent\` / \`OnServerInvoke\` handlers for missing server-side type, range, cooldown, and ownership validation; scan \`Workspace\` for unexpected embedded \`Script\`s.
2. Static quality: run \`luau_check\` on scripts to flag syntax issues, undefined globals, or unused variables.
3. Physics & scene health: check total instance/part counts, unanchored parts without constraints, duplicate scripts via \`compare_instances\`, and live memory/FPS from \`get_studio_state\`.
4. Present a prioritized report grouped into Critical, Medium, and Polish fixes.`
    }
  ];

  const grid = document.getElementById('prompt-grid');
  window.__PROMPTS = prompts.map(p => p.body);
  grid.innerHTML = prompts.map((p, idx) => `
    <div class="prompt-card">
      <div class="prompt-head">
        <div><h3>${esc(p.title)}</h3><div class="sub">${esc(p.sub)}</div></div>
        <button class="primary sm" onclick="copyText(window.__PROMPTS[${idx}])">Copy Prompt</button>
      </div>
      <pre>${esc(p.body)}</pre>
    </div>`).join('');
  renderConfigsAndCommands();
}

function renderTips(){
  const tips = [
    ["Edit vs Server vs Client DataModels",
     "During Play Solo (F5) or Run (F8), BOTH the Edit DataModel and Play Server DataModel stay alive simultaneously. Always target `dm='edit'` for permanent edits to the place, `dm='server'` to inspect/test live server state, and `dm='client'` to inspect local UI, camera, or player state via the silent RemoteFunction relay."],
    ["Busting Cached ModuleScripts (`require()`)",
     "Luau caches `require(moduleScript)` per DataModel lifetime. Editing `.Source` alone won't reload already-required tables. Pass `replaceInstance: true` on `write_script` / `multi_edit` (or `--replace` in CLI), or test edits in a clean sandbox clone with `require_fresh`."],
    ["Always Pre-Flight Luau with `luau_check`",
     "In non-strict Luau, deleting a helper function leaves callers compiling cleanly while crashing at runtime with `attempt to call a nil value`. `luau_check` runs both `luau-compile` and `luau-analyze` to catch undefined globals before code ever reaches Studio."],
    ["Open Script Editor Tabs & >200 KB Scripts",
     "ArenaBridge v4.0.0 uses `ScriptEditorService:GetEditorSource` and `UpdateSourceAsync` with CRLF -> LF normalization instead of raw `.Source` assignment — safely supporting scripts over 200 KB and keeping open Studio tabs synchronized without cursor corruption."],
    ["Single-Step Undo (`Ctrl+Z`) + Multi-Script Snapshots",
     "Every mutating tool wraps inside a `ChangeHistoryService` recording so one `Ctrl+Z` in Studio undoes an entire build or batch sync. Before sweeping refactors, call `manage_snapshots(action='create')` for instant 1-command rollback."],
    ["Safe Toolbox / Asset Insertion",
     "`insert_asset` scans all inserted descendants for embedded `Script` / `LocalScript` / `ModuleScript` instances and strips them by default (`stripScripts: true`), protecting places from malicious free-model scripts."],
    ["Zero-Drift Local Folder <-> Studio Workflow",
     "`drift_and_sync` computes identical 53-bit-safe 32-bit FNV-1a hashes in Python and Luau. Always run `mode='check'` first to inspect modified/local-only/studio-only scripts before running `mode='push'` or `mode='pull'`."],
    ["Why the Bridge Runs on Your Machine (Not Inside Cloud Preview URLs)",
     "Cloud sandbox preview hosts require browser session headers that Roblox Studio `HttpService` cannot attach. Running `server.py` locally on `:8077` works out-of-the-box for all local MCP clients, and adding `--tunnel` exposes `/mcp` + `/api/*` cleanly when a remote cloud agent needs direct access."]
  ];
  document.getElementById('tips-grid').innerHTML = tips.map(([t, b]) => `
    <div class="card"><h3>${esc(t)}</h3><p style="margin:6px 0 0;color:#b6c0cb">${esc(b)}</p></div>`).join('');
}

async function uploadBrowserImage(){
  const fileInput = document.getElementById('img-file');
  const out = document.getElementById('img-import-out');
  if(!fileInput.files || !fileInput.files[0]){
    out.textContent = 'Select a .png, .jpg, or .bmp file first.';
    return;
  }
  const f = fileInput.files[0];
  out.textContent = 'Encoding & importing ' + f.name + '...';
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(f);
  });
  try {
    const res = await callMcp('import_image', {
      base64Data: dataUrl,
      name: document.getElementById('img-name').value.trim() || f.name.replace(/\.[^.]+$/, ''),
      targetPath: document.getElementById('img-target').value.trim() || undefined,
      applyMode: document.getElementById('img-mode').value,
      useEditableImage: true,
      syncAssetsModule: true
    });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    loadKitGallery();
    tick();
  } catch (e) { out.textContent = String(e); }
}

let ALL_KIT_IMAGES = [];
let ACTIVE_PACK = 'all';
let ACTIVE_CLOUD_CAT = '';

async function installAllModules(){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing all 19 Luau modules -> ReplicatedStorage.Shared...';
  try {
    const res = await callMcp('install_kit_module', { module: 'all', parent: 'ReplicatedStorage.Shared' });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function installTemplateUI(tname){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing template ' + tname + ' (modules + ScreenGuis in StarterGui + runtime controllers)...';
  try {
    const res = await callMcp('install_template', { template: tname });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Installed ' + tname + ' into Studio!');
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function spawnKitPrefab(name){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Spawning prefab ' + name + '...';
  try {
    const res = await callMcp('spawn_prefab', { prefab: name });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Spawned ' + name);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function importRepoAssetToStudio(relPath){
  toast('Sending ' + relPath + ' to Studio...');
  await quickTool('import_image', {
    repoAsset: relPath,
    targetPath: 'StarterGui',
    applyMode: 'screen_gui',
    useEditableImage: true
  });
}

async function spawnCloudIconPreview(uri, label){
  toast('Spawning cloud icon ' + label + ' in StarterGui...');
  await quickTool('build_tree', {
    parent: 'StarterGui',
    tree: [{
      name: 'CloudIconPreview',
      className: 'ScreenGui',
      replaceExisting: true,
      properties: { ResetOnSpawn: false },
      children: [{
        name: label.replace(/[^A-Za-z0-9_]/g, '_') || 'Icon',
        className: 'ImageLabel',
        properties: {
          AnchorPoint: { __t: 'Vector2', v: [0.5, 0.5] },
          Position: { __t: 'UDim2', v: [0.5, 0, 0.5, 0] },
          Size: { __t: 'UDim2', v: [0, 128, 0, 128] },
          BackgroundTransparency: 0.25,
          Image: uri
        }
      }]
    }]
  });
}

function selectPack(pack){
  ACTIVE_PACK = pack;
  document.querySelectorAll('#asset-pack-pills button').forEach(b => {
    b.classList.toggle('primary', b.dataset.pack === pack);
  });
  renderFilteredAssets();
}

function renderFilteredAssets(){
  const q = (document.getElementById('asset-search').value || '').trim().toLowerCase();
  const filtered = ALL_KIT_IMAGES.filter(im => {
    if(ACTIVE_PACK !== 'all' && im.pack !== ACTIVE_PACK) return false;
    if(q && !(im.relPath.toLowerCase().includes(q) || im.name.toLowerCase().includes(q))) return false;
    return true;
  });
  document.getElementById('asset-gallery').innerHTML = filtered.slice(0, 96).map(im => `
    <div class="card" style="padding:10px;display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center">
      <div style="width:68px;height:68px;display:flex;align-items:center;justify-content:center;background:repeating-conic-gradient(#161d29 0% 25%, #0d121a 0% 50%) 50% / 14px 14px;border-radius:8px;padding:6px">
        <img src="/api/kit/image/${esc(im.relPath)}" alt="${esc(im.name)}" loading="lazy" style="max-width:54px;max-height:54px;image-rendering:auto">
      </div>
      <div style="font-size:10px;color:var(--muted)">${esc(im.pack)} · ${esc(im.category||im.pack)}</div>
      <div style="font-size:9px;color:var(--muted);max-width:100%;overflow-wrap:anywhere">${esc(im.license||'license undeclared')} · ${esc(im.provenance||'unverified')} · ${esc(im.sourceId||'unknown source')}</div>
      <code style="font-size:11px;word-break:break-all">${esc(im.name)}</code>
      <div style="display:flex;gap:4px">
        <button class="sm" onclick="copyText('${esc(im.localUri)}')">URI</button>
        <button class="sm primary" onclick="importRepoAssetToStudio('${esc(im.relPath)}')">Send</button>
      </div>
    </div>`).join('') + (filtered.length > 96 ? `<div class="sub" style="grid-column:1/-1">Showing 96 of ${filtered.length} matching images — pick a source-group pill or type a filter above to narrow further.</div>` : '');
}

let ACTIVE_ICON_PACK = 'all';
let ACTIVE_PREVIEW_VP = 'desktop';
let LAST_UI_AUDIT = null;

function playBrowserAudio(fname){
  const player = document.getElementById('sfx-player');
  player.src = '/api/kit/audio/' + encodeURIComponent(fname);
  player.currentTime = 0;
  player.play().catch(_ => {});
}

async function sendAudioToStudio(fname){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing CC0 audio ' + fname + ' -> SoundService...';
  try {
    const res = await callMcp('import_audio', { repoAudio: fname, targetPath: 'SoundService' });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Installed Sound ' + fname + ' in SoundService');
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function installDropinSystemUI(sysName){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing drop-in system ' + sysName + ' -> ReplicatedStorage.Shared + ServerScriptService + StarterPlayerScripts...';
  try {
    const res = await callMcp('install_dropin_system', { system: sysName });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Installed system ' + sysName);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function exportLiveRbxmx(){
  const p = (document.getElementById('export-rbxmx-path').value || 'StarterGui').trim();
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Exporting ' + p + ' -> .rbxmx XML model file...';
  try {
    const res = await callMcp('export_rbxmx', { path: p });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Exported ' + p + ' to .rbxmx');
  } catch (e) { out.textContent = String(e); }
}

function switchPreviewViewport(vp){
  ACTIVE_PREVIEW_VP = vp;
  ['desktop', 'tablet', 'mobile'].forEach(k => {
    const b = document.getElementById('vp-btn-' + k);
    if(b) b.classList.toggle('primary', k === vp);
  });
  renderUiPreviewStage();
}

async function runUiPreviewAndAudit(){
  const raw = document.getElementById('ui-audit-src').value || 'prefab:ui_kenney_shop_window';
  const args = raw.startsWith('prefab:')
    ? { prefab: raw.slice('prefab:'.length) }
    : { path: raw.slice('studio:'.length) };
  try {
    const res = await callMcp('audit_ui_layout', args);
    LAST_UI_AUDIT = res.structuredContent || null;
    renderUiPreviewStage();
  } catch (e) {
    document.getElementById('ui-issues-list').innerHTML = `<div class="sub">${esc(String(e))}</div>`;
  }
}

function renderUiPreviewStage(){
  if(!LAST_UI_AUDIT || !LAST_UI_AUDIT.viewports) return;
  const rep = LAST_UI_AUDIT.viewports[ACTIVE_PREVIEW_VP] || LAST_UI_AUDIT.viewports.desktop;
  if(!rep) return;
  const maxStageW = 540;
  const scale = Math.min(1, maxStageW / rep.width);
  const stage = document.getElementById('ui-preview-stage');
  stage.style.width = Math.round(rep.width * scale) + 'px';
  stage.style.height = Math.round(rep.height * scale) + 'px';

  stage.innerHTML = (rep.elements || []).map(el => {
    const [x, y, w, h] = el.rect;
    let imgHtml = '';
    if(el.image && el.image.startsWith('rbxasset://arena_assets/')){
      const rel = el.image.slice('rbxasset://arena_assets/'.length);
      imgHtml = `<img src="/api/kit/image/${esc(rel)}" style="width:100%;height:100%;object-fit:fill;display:block;pointer-events:none">`;
    }
    const txtHtml = el.text != null && el.text !== ''
      ? `<span style="position:relative;z-index:2;color:${esc(el.textColor||'#fff')};font-size:${Math.max(8, Math.round((el.textSize||14)*scale))}px;font-weight:700;padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(el.text)}</span>`
      : '';
    return `<div onmouseenter="document.getElementById('ui-stage-meta').textContent='${esc(el.path)} (' + '${esc(el.className)}' + ') — [${x}, ${y}, ${w}x${h}px]'"
      style="position:absolute;left:${(x*scale).toFixed(1)}px;top:${(y*scale).toFixed(1)}px;width:${Math.max(2, w*scale).toFixed(1)}px;height:${Math.max(2, h*scale).toFixed(1)}px;background:${esc(el.bg||'transparent')};border:${esc(el.border||'1px solid rgba(88,166,255,0.22)')};border-radius:${Math.round((el.radius||0)*scale)}px;display:flex;align-items:center;justify-content:center;overflow:hidden;box-sizing:border-box">
      ${imgHtml ? `<div style="position:absolute;inset:0">${imgHtml}</div>` : ''}${txtHtml}
    </div>`;
  }).join('');

  const allIssues = Object.values(LAST_UI_AUDIT.viewports).flatMap(v => v.issues || []);
  document.getElementById('ui-issues-title').textContent =
    `Layout Lint Report — ${allIssues.length === 0 ? '0 Issues Across All 3 Devices!' : allIssues.length + ' Advisory / Overflow Notes'}`;
  document.getElementById('ui-issues-list').innerHTML = allIssues.length === 0
    ? '<div class="pill on" style="display:inline-block">Clean responsive bounds across Desktop, Tablet &amp; Mobile</div>'
    : allIssues.map(iss => `
      <div class="card" style="padding:8px 10px">
        <div style="display:flex;gap:6px;align-items:center">
          <span class="pill ${iss.severity==='error'?'off':'ro'}" style="font-size:10px;padding:1px 7px">${esc(iss.viewport)} &bull; ${esc(iss.code)}</span>
          <code style="font-size:11px">${esc(iss.path)}</code>
        </div>
        <div class="sub" style="margin-top:4px">${esc(iss.message)}</div>
      </div>`).join('');
}

let ACTIVE_3D_PRESET = 'perspective';
let ACTIVE_3D_HUD = true;

async function refresh3DViewport(preset){
  if(preset) ACTIVE_3D_PRESET = preset;
  ['perspective', 'iso_ne', 'top_blueprint', 'front'].forEach(k => {
    const b = document.getElementById('cam-' + k);
    if(b) b.classList.toggle('primary', k === ACTIVE_3D_PRESET);
  });
  try {
    const res = await callMcp('capture_viewport', {
      preset: ACTIVE_3D_PRESET,
      includeHud: ACTIVE_3D_HUD,
      width: 680,
      height: 360
    });
    const imgItem = (res.content || []).find(c => c.type === 'image');
    const sc = res.structuredContent || {};
    if(imgItem && imgItem.data){
      document.getElementById('live-viewport-img').src = 'data:image/png;base64,' + imgItem.data;
    }
    document.getElementById('live-viewport-meta').textContent =
      `Camera: ${sc.cameraPreset} [${(sc.cameraPosition||[]).join(', ')}] • Parts rendered: ${sc.partsRendered||0} • HUD widgets: ${sc.hudElementsComposited||0}`;
  } catch(e){
    document.getElementById('live-viewport-meta').textContent = String(e);
  }
}

function toggle3DHud(){
  ACTIVE_3D_HUD = !ACTIVE_3D_HUD;
  document.getElementById('btn-hud-toggle').textContent = 'HUD: ' + (ACTIVE_3D_HUD ? 'ON' : 'OFF');
  refresh3DViewport();
}

async function run3DSceneAudit(fixUnanchored, snapFloating){
  try {
    const res = await callMcp('audit_3d_scene', {
      path: 'Workspace',
      autoFixUnanchored: Boolean(fixUnanchored),
      snapFloating: Boolean(snapFloating)
    });
    const d = res.structuredContent || {};
    document.getElementById('scene-audit-summary').textContent =
      `Audited ${d.totalPartsAudited||0} parts — Errors: ${d.errorCount||0}, Warnings: ${d.warningCount||0}` +
      (d.autoFixedUnanchored ? ` (Auto-anchored ${d.autoFixedUnanchored}!)` : '') +
      (d.autoSnappedFloating ? ` (Snapped ${d.autoSnappedFloating}!)` : '');
    const issues = d.issues || [];
    document.getElementById('scene-audit-list').innerHTML = issues.length === 0
      ? '<div class="pill on" style="display:inline-block">All 3D geometry grounded, anchored &amp; spawn-clear!</div>'
      : issues.map(iss => `
        <div class="cmd-row" style="flex-direction:column;align-items:flex-start;gap:3px">
          <div><span class="pill ${iss.severity==='error'?'off':'ro'}" style="font-size:10px;padding:1px 7px">${esc(iss.code)}</span> <code style="font-size:11px">${esc(iss.path)}</code></div>
          <div class="sub">${esc(iss.message)}</div>
        </div>`).join('');
    if(fixUnanchored || snapFloating) refresh3DViewport();
  } catch(e){
    document.getElementById('scene-audit-summary').textContent = String(e);
  }
}

function setIconPack(pk){
  ACTIVE_ICON_PACK = pk;
  ['all', 'stud', 'lucide', 'lucide_fill'].forEach(k => {
    const b = document.getElementById('pk-' + k);
    if(b) b.classList.toggle('primary', k === pk);
  });
  ACTIVE_CLOUD_CAT = '';
  searchCloudIconsUI('');
}

async function searchCloudIconsUI(catOverride){
  if(catOverride !== undefined) ACTIVE_CLOUD_CAT = catOverride;
  const q = (document.getElementById('cloud-icon-q').value || '').trim();
  try {
    const res = await callMcp('search_cloud_icons', {
      query: q,
      pack: ACTIVE_ICON_PACK,
      category: ACTIVE_CLOUD_CAT || undefined,
      limit: 32
    });
    const d = res.structuredContent || {};
    const cats = d.categories || {};
    document.getElementById('cloud-icon-cats').innerHTML =
      `<button class="sm ${ACTIVE_CLOUD_CAT===''?'primary':''}" onclick="searchCloudIconsUI('')">All (${d.totalCatalogIcons||1587})</button>` +
      Object.entries(cats).map(([c, n]) =>
        `<button class="sm ${ACTIVE_CLOUD_CAT===c?'primary':''}" onclick="searchCloudIconsUI('${esc(c)}')">${esc(c)} (${n})</button>`
      ).join('');
    document.getElementById('cloud-icon-results').innerHTML = (d.icons || []).map(ic => `
      <div class="cmd-row" style="align-items:center;justify-content:space-between">
        <div style="min-width:0">
          <strong style="font-size:12px">${esc(ic.name)}</strong> <span class="sub">[${esc(ic.pack)}]</span>
          <div class="sub" style="font-size:11px;overflow:hidden;text-overflow:ellipsis">${esc(ic.path)} &bull; <code>${esc(ic.uri)}</code></div>
        </div>
        <div style="display:flex;gap:4px;flex-shrink:0">
          <button class="sm" onclick="copyText('${esc(ic.uri)}')">Copy</button>
          <button class="sm primary" onclick="spawnCloudIconPreview('${esc(ic.uri)}', '${esc(ic.name)}')">Spawn</button>
        </div>
      </div>`).join('');
  } catch(_){}
}

async function loadKitGallery(){
  try {
    // The MCP default is the lightweight summary (counts only); request full rows for dashboard cards.
    const res = await callMcp('browse_kit', { category: 'all', format: 'full' });
    const data = res.structuredContent || {};

    const templates = data.templates || [];
    document.getElementById('template-list').innerHTML = templates.map(t => `
      <div class="cmd-row">
        <div><strong>${esc(t.title)}</strong> <code style="font-size:11px">${esc(t.name)}</code><div class="sub">${esc(t.description)}</div></div>
        <div class="spacer"></div>
        <button class="sm primary" onclick="installTemplateUI('${esc(t.name)}')">Install</button>
      </div>`).join('');

    const systems = data.systems || [];
    document.getElementById('dropin-system-list').innerHTML = systems.map(s => `
      <div class="cmd-row">
        <div><strong>${esc(s.system)}</strong> <span class="sub">(${s.scriptCount} scripts)</span><div class="sub">${esc(s.summary)}</div></div>
        <div class="spacer"></div>
        <button class="sm primary" onclick="installDropinSystemUI('${esc(s.system)}')">Install</button>
      </div>`).join('');

    const audios = data.audio || [];
    document.getElementById('audio-list').innerHTML = audios.map(a => `
      <div class="cmd-row" style="justify-content:space-between;padding:6px 8px">
        <code style="font-size:11px">${esc(a.name)}</code>
        <div style="display:flex;gap:4px">
          <button class="sm" onclick="playBrowserAudio('${esc(a.fileName)}')">&#9654;</button>
          <button class="sm primary" onclick="sendAudioToStudio('${esc(a.fileName)}')">Send</button>
        </div>
      </div>`).join('');

    const prefabs = data.prefabs || [];
    document.getElementById('prefab-list').innerHTML = prefabs.map(p => `
      <div class="cmd-row">
        <div><strong>${esc(p.name)}</strong> <span class="pill" style="font-size:10px;padding:1px 6px">${esc(p.category)}</span><div class="sub">${esc(p.description)}</div></div>
        <div class="spacer"></div>
        <button class="sm primary" onclick="spawnKitPrefab('${esc(p.name)}')">Spawn</button>
      </div>`).join('');

    ALL_KIT_IMAGES = data.images || [];
    const catCounts = data.imageCategories || {};
    document.getElementById('asset-pack-pills').innerHTML =
      `<button class="sm ${ACTIVE_PACK==='all'?'primary':''}" data-pack="all" onclick="selectPack('all')">All (${ALL_KIT_IMAGES.length})</button>` +
      Object.entries(catCounts).sort().map(([pack, cnt]) =>
        `<button class="sm ${ACTIVE_PACK===pack?'primary':''}" data-pack="${esc(pack)}" onclick="selectPack('${esc(pack)}')">${esc(pack)} (${cnt})</button>`
      ).join('');
    renderFilteredAssets();
    searchCloudIconsUI('');
    runUiPreviewAndAudit();
    refresh3DViewport('perspective');
    run3DSceneAudit(false, false);
  } catch(e){
    const message = 'Kit inventory failed to load: ' + String(e);
    ['template-list', 'dropin-system-list', 'audio-list', 'prefab-list', 'asset-gallery'].forEach(id => {
      const el = document.getElementById(id);
      if(el) el.innerHTML = `<div class="sub">${esc(message)}</div>`;
    });
    const out = document.getElementById('kit-action-out');
    if(out) out.textContent = message;
  }
}

async function initBoot(){
  try {
    const r = await fetch('/api/local-boot', { cache:'no-store' });
    BOOT = await r.json();
    if(BOOT.token){
      document.getElementById('tok-input').value = BOOT.token;
    }
    const sel = document.getElementById('tool-select');
    sel.innerHTML = BOOT.tools.map(t =>
      `<option value="${esc(t.name)}">${esc(t.name)}${t.readOnly ? ' (read-only)' : ''}</option>`
    ).join('');
    onSelectTool();
  } catch(_){}
  renderTips();
  renderPrompts();
  loadKitGallery();
}

async function tick(){
  try {
    const r = await fetch('/api/state', { cache:'no-store' });
    const s = await r.json();
    const prevPlace = LAST_STATE.studio && LAST_STATE.studio.place;
    LAST_STATE = s;

    const st = document.getElementById('status');
    st.textContent = s.studio_connected ? 'Studio connected' : 'Waiting for Studio';
    st.className = 'pill ' + (s.studio_connected ? 'on' : 'off');
    document.getElementById('ro-badge').style.display = s.read_only ? 'inline-block' : 'none';

    const pl = document.getElementById('place');
    if(s.studio && s.studio.place){
      pl.style.display = 'inline-block';
      pl.textContent = s.studio.place + ' (PlaceId ' + (s.studio.place_id || 0) + ')';
      if(s.studio.place !== prevPlace) renderPrompts();
    } else {
      pl.style.display = 'none';
    }

    const peers = s.peers || [];
    const onlinePeers = peers.filter(p => p.online);
    document.getElementById('c-peers').textContent = onlinePeers.length + ' online';
    document.getElementById('c-tools').textContent = s.mcp_tools || 37;
    document.getElementById('c-queued').textContent = s.queued || 0;
    document.getElementById('c-snaps').textContent = s.snapshots || 0;
    const rows = s.recent || [];
    document.getElementById('c-done').textContent = rows.filter(j => j.status === 'done').length;
    document.getElementById('c-fail').textContent = rows.filter(j => j.status === 'error').length;

    const ptb = document.getElementById('peer-rows');
    if(!peers.length){
      ptb.innerHTML = '<tr><td colspan="6" class="empty">Waiting for Roblox Studio plugin to poll <code>/api/poll</code>&hellip;</td></tr>';
    } else {
      ptb.innerHTML = peers.map(p => `<tr>
        <td><code>${esc(p.peerId)}</code></td>
        <td><code>${esc(p.dm)}</code></td>
        <td>${esc(p.place || '')}</td>
        <td>v${esc(p.pluginVersion || '?')}</td>
        <td>${p.hasClient ? 'ready (' + p.playerCount + 'p)' : '&mdash;'}</td>
        <td class="${p.online ? 's-done' : 's-queued'}">${p.online ? 'online' : 'stale'}</td>
      </tr>`).join('');
    }

    const tb = document.getElementById('rows');
    if(!rows.length){
      tb.innerHTML = '<tr><td colspan="7" class="empty">No jobs executed yet.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(j => {
      const out = j.error ? j.error : (j.result == null ? '' : (typeof j.result === 'string' ? j.result : JSON.stringify(j.result)));
      const lat = j.duration_ms != null ? j.duration_ms + 'ms' : '&mdash;';
      return `<tr class="clickable" onclick="inspectJob('${esc(j.id)}')">
        <td><code>${esc(j.id)}</code></td>
        <td>${esc(j.type)}</td>
        <td><code>${esc(j.dm || 'auto')}</code></td>
        <td>${lat}</td>
        <td>${esc(j.note || '')}</td>
        <td class="s-${esc(j.status)}">${esc(j.status)}</td>
        <td>${esc(out).slice(0, 190)}</td>
      </tr>`;
    }).join('');
  } catch(_){}
}

initBoot().then(() => { tick(); setInterval(tick, 1500); });
</script></body></html>"""


# ----------------------------------------------------------------------------
# Server runner helpers (standalone CLI & embedded daemon thread)
# ----------------------------------------------------------------------------

def load_token_default(explicit_token: str = None) -> str:
    if explicit_token is not None and explicit_token != "":
        return explicit_token
    env_tok = os.environ.get("BRIDGE_TOKEN")
    if env_tok:
        return env_tok.strip()
    token_file = HERE / "bridge.token"
    if token_file.is_file():
        txt = token_file.read_text(encoding="utf-8").strip()
        if txt:
            return txt
    return ""


def create_http_server(
    host: str = "0.0.0.0",
    port: int = 8077,
    token: str = None,
    read_only: bool = False,
    guard_place_id: int = None,
):
    resolved_token = load_token_default(token)
    state = BridgeState(
        token=resolved_token,
        read_only=read_only,
        guard_place_id=guard_place_id,
    )
    httpd = ThreadingHTTPServer((host, port), make_http_handler(state))
    httpd.daemon_threads = True
    httpd.bridge_state = state
    return httpd, state


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", 8077)))
    ap.add_argument("--host", default=os.environ.get("BRIDGE_HOST", "0.0.0.0"))
    ap.add_argument("--token", default=None, help="Shared secret (auto-reads bridge.token if present)")
    ap.add_argument(
        "--read-only",
        action="store_true",
        default=os.environ.get("BRIDGE_READ_ONLY", "") in ("1", "true", "yes"),
        help="Run in Read-Only Inspector mode (blocks all mutating jobs & MCP tools)",
    )
    ap.add_argument(
        "--place-id",
        type=int,
        default=int(os.environ["BRIDGE_PLACE_ID"]) if os.environ.get("BRIDGE_PLACE_ID") else None,
        help="Optional PlaceId write guard",
    )
    args = ap.parse_args()

    srv, state = create_http_server(
        host=args.host,
        port=args.port,
        token=args.token,
        read_only=args.read_only,
        guard_place_id=args.place_id,
    )
    ro_str = " [READ-ONLY INSPECTOR]" if state.read_only else ""
    print(
        f"[bridge] v{VERSION}{ro_str} listening on {args.host}:{args.port} "
        f"(token {'set' if state.token else 'disabled'}, Control Center http://127.0.0.1:{args.port}/, MCP http://127.0.0.1:{args.port}/mcp)",
        flush=True,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
