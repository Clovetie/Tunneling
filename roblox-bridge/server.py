#!/usr/bin/env python3
"""
server.py — Multi-Peer HTTP Bridge + Native Streamable HTTP MCP Server (v6.0.0)

Runs locally on your PC (127.0.0.1:8077 by default) with ZERO pip dependencies.
Simultaneously serves:
  1. Roblox Studio local plugin peers (Edit + Play Server + Play Client bridge)
     via fast long-polling (`GET /api/poll`, `POST /api/result`).
  2. Native MCP Streamable HTTP endpoint (`POST /mcp`, `GET /mcp`, `DELETE /mcp`)
     implementing MCP 2026-07-28 + 2025-11-25 / 2025-06-18 / 2024-11-05 JSON-RPC 2.0
     (tools, live resources, resource templates, completions, prompts, tasks,
     subscriptions, MRTR confirmations, SEP-2243 routing headers, RFC 9728 OAuth
     protected-resource metadata, and SEP-1649 well-known Server Cards).
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
from urllib.parse import parse_qs, urlparse, urlsplit
import uuid

from confirm_gate import ConfirmGate, GateError, mode_from_env  # noqa: E402
from mcp_core import (
    CONFIRM_GATED_TOOLS,
    BridgeClient,
    META_PROTOCOL_VERSION,
    MCPDispatcher,
    MODERN_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    TOOL_MAP,
    TOOL_SPECS,
    READ_ONLY_JOB_TYPES,
    build_mcp_server_card,
    decode_mcp_header_value,
    is_tool_call_read_only,
    is_bus_action_read_only,
    run_bridge_self_test,
    take_subscription_stream,
)
from auth_core import ROLE_RANK, TokenStore
from bus_core import AgentBus
from telemetry import Telemetry
from tunnel_manager import TunnelManager

VERSION = "6.0.0"
PEER_ONLINE_WINDOW = 5.0  # play DataModel peers are transient; do not route work to stale peers
DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024  # 32 MB ceiling for .rbxlx / batch image uploads
HERE = Path(__file__).resolve().parent
SNAPSHOT_DIR = HERE / "snapshots"
SNAPSHOT_RETENTION = 30


class BodyLimitError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


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
            "well_known_mcp": f"{origin}/.well-known/mcp.json",
            "well_known_server_card": f"{origin}/.well-known/mcp/server-card.json",
            "oauth_protected_resource": f"{origin}/.well-known/oauth-protected-resource/mcp",
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
            "offlineTools": ["diagnose_connection (bridge registry only)", "search_tools", "audit_localization", "audit_onboarding_plan", "search_skills", "apply_skill", "skill_references", "browse_kit", "search_assets", "search_cloud_icons", "luau_check (when host binaries are installed)", "render_offline with an exported hierarchy tree"],
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
            "`render_offline` can render a previously exported hierarchy tree without a Studio peer; use `screenshot_gui` with `dm='client', path='PlayerGui'` when live absolute UI measurements are needed.",
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
    def __init__(
        self,
        token: str = "",
        read_only: bool = False,
        guard_place_id: int = None,
        state_dir: str = None,
        auto_tunnel: bool = False,
        tunnel_flavor: str = "auto",
        tunnel_protocol: str = "auto",
        tunnel_token: str = None,
        tunnel_name: str = None,
        public_url: str = None,
        tunnel_binary: str = None,
        sweep_interval: float = 300.0,
        sweep_offline_seconds: float = 900.0,
        require_confirm: str = "off",
        confirm_ttl_seconds: float = 180.0,
        confirm_allowance_seconds: float = 600.0,
        confirm_wait_seconds: float = 45.0,
        auto_task_ms: float = None,
        allowed_origins=None,
        max_body_bytes: int = None,
    ):
        self.token = token or ""
        self.read_only = bool(read_only)
        self.guard_place_id = int(guard_place_id) if guard_place_id else None
        self.started = _now()
        self.state_dir = Path(state_dir) if state_dir else (HERE / "bridge_state")
        self.state_dir.mkdir(parents=True, exist_ok=True)
        raw_origins = allowed_origins if allowed_origins is not None else os.environ.get("BRIDGE_ALLOWED_ORIGINS", "")
        if isinstance(raw_origins, (list, tuple, set)):
            self.allowed_origins = {str(o).strip().rstrip("/") for o in raw_origins if str(o).strip()}
        else:
            self.allowed_origins = {o.strip().rstrip("/") for o in str(raw_origins).split(",") if o.strip()}
        raw_max_body = max_body_bytes if max_body_bytes is not None else os.environ.get("BRIDGE_MAX_BODY_BYTES")
        try:
            self.max_body_bytes = max(4096, int(raw_max_body)) if raw_max_body else DEFAULT_MAX_BODY_BYTES
        except (TypeError, ValueError):
            self.max_body_bytes = DEFAULT_MAX_BODY_BYTES

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
        # v5.4.0: the confirmation gate. It lives next to the dispatcher because
        # `requestState` is signed with this bridge's secret and the pending
        # registry is what the Ops console reads and answers.
        self.confirm_mode = str(require_confirm or "off").strip().lower() or "off"
        self.confirm_gate = ConfirmGate(
            mode=self.confirm_mode,
            secret=self.token or None,
            ttl_seconds=float(confirm_ttl_seconds or 180.0),
            allowance_seconds=float(confirm_allowance_seconds or 600.0),
            destructive_tools=CONFIRM_GATED_TOOLS,
            hook=lambda kind, detail: self.publish_mcp_event(kind, detail),
        )
        self.mcp_dispatcher = MCPDispatcher(
            self.mcp_client,
            gate=self.confirm_gate,
            auto_task_ms=auto_task_ms,
            legacy_wait_seconds=confirm_wait_seconds,
        )

        # --- v5 ops platform -------------------------------------------------
        self.telemetry = Telemetry(self.state_dir)
        self.bus = AgentBus(self.state_dir / "bus.json")
        self.tokens = TokenStore(self.state_dir / "tokens.json", primary_token=self.token)
        self.tunnel = TunnelManager(
            port=int(os.environ.get("BRIDGE_PORT", 8077)),
            binary=tunnel_binary,
            flavor=tunnel_flavor,
            protocol=tunnel_protocol,
            named_token=tunnel_token,
            name=tunnel_name,
            public_url=public_url,
            state_dir=self.state_dir,
            auto_start=bool(auto_tunnel),
        )
        # Automatic stale-agent sweeping (0 disables the background sweep).
        self.sweep_interval = max(0.0, float(sweep_interval or 0))
        self.sweep_offline_seconds = max(60.0, float(sweep_offline_seconds or 900))
        self._last_sweep = 0.0
        self._sweeps = 0
        # Multi-studio fleet: pinned peer (the "active studio"), labels, and the
        # persisted fleet file. Routing prefers the pin, then falls back to auto
        # so a closed Studio cannot silently stall the queue.
        self._fleet_path = self.state_dir / "fleet.json"
        self.studio_pin = None
        self.peer_labels = {}
        self._load_fleet()
        # Maintenance switch: keep accepting work, stop handing it to Studio.
        self.queue_paused = False
        self.queue_paused_reason = None
        self.queue_paused_at = None
        # Job dedupe + retry bookkeeping
        self.idempotency = {}
        self._reaper_stop = threading.Event()
        self._reaper = threading.Thread(target=self._reap_loop, name="job-reaper", daemon=True)
        self._reaper.start()

        self.telemetry.events.emit("bridge.start", {
            "version": VERSION,
            "readOnly": self.read_only,
            "tools": len(TOOL_SPECS),
            "authMode": "scoped" if self.token else "open",
        })
        self.telemetry.audit.append(
            "bridge.start",
            actor="system",
            detail={"version": VERSION, "readOnly": self.read_only, "authMode": "scoped" if self.token else "open"},
        )

    # -- auth ----------------------------------------------------------------

    def authenticate(self, supplied: str = None, is_loopback: bool = False, required: str = "read"):
        """Resolve a raw token into an auth context.

        Returns (context, error). `error` is a dict with `code`/`error` when the
        request must be rejected. Auth is disabled entirely when no token is set,
        which preserves the historical zero-config localhost experience.
        """
        if not self.token and not self.tokens.records:
            return {"role": "admin", "id": "open", "label": "auth-disabled"}, None
        record, rate = self.tokens.authenticate(supplied)
        if record is None:
            # No anonymous bypass: a configured token is required for every path
            # that is not explicitly public (dashboard HTML and /api/health).
            return None, {"code": 401, "error": "bad token"}
        if rate and not rate.get("allowed"):
            return None, {
                "code": 429,
                "error": "rate limit exceeded for this token",
                "retryAfter": rate.get("retryAfter", 1),
            }
        if not TokenStore.role_allows(record.role, required):
            return None, {
                "code": 403,
                "error": f"token role '{record.role}' cannot perform '{required}' actions",
            }
        ctx = {
            "role": record.role,
            "id": record.id,
            "label": record.label,
            "remaining": (rate or {}).get("remaining"),
        }
        if record.id != "primary":
            self.telemetry.metrics.inc("bridge_token_requests_total", 1, {"token": record.id, "role": record.role})
        return ctx, None

    # -- background reaper ---------------------------------------------------

    def _reap_loop(self):
        while not self._reaper_stop.wait(2.0):
            try:
                self.reap_jobs()
            except Exception:  # noqa: BLE001 - the reaper must never die
                continue
            if self.sweep_interval <= 0:
                continue
            try:
                if _now() - self._last_sweep >= self.sweep_interval:
                    self.auto_sweep()
            except Exception:  # noqa: BLE001 - the sweeper must never kill the reaper
                continue
            try:
                # Unanswered confirmations must not linger: expire them so the
                # Ops console and /api/confirmations only ever show live ones.
                self.confirm_gate.expire_stale()
            except Exception:  # noqa: BLE001
                continue

    def auto_sweep(self) -> dict:
        """Release tasks held by agents that stopped heart-beating.

        Runs on the reaper thread every `sweep_interval` seconds. A no-op sweep is
        cheap and deliberately silent; a sweep that releases work is broadcast on
        the bus, recorded in the audit log and counted in metrics.
        """
        result = self.bus.sweep_stale(self.sweep_offline_seconds)
        self._last_sweep = _now()
        self._sweeps += 1
        if result.get("tasksReaped"):
            self.telemetry.metrics.inc("bridge_agents_reaped_total", len(result.get("agents") or []))
            self.telemetry.metrics.inc("bridge_tasks_released_total", result["tasksReaped"])
            self.telemetry.events.emit("bus.sweep", {
                "released": result["tasksReaped"], "agents": result.get("agents", []),
            }, level="warn")
            self.telemetry.audit.append("bus.sweep", actor="sweeper",
                                        detail={"released": result["tasksReaped"],
                                                "agents": result.get("agents", [])})
        return result

    def pause_queue(self, reason: str = None) -> dict:
        """Stop handing jobs to Studio; new work still queues (nothing is lost)."""
        with self.lock:
            self.queue_paused = True
            self.queue_paused_reason = (reason or "paused by operator")[:200]
            self.queue_paused_at = _now()
            queued = len(self.pending)
        self.telemetry.events.emit("queue.paused", {"reason": self.queue_paused_reason, "queued": queued}, level="warn")
        self.telemetry.audit.append("queue.pause", actor="operator", detail={"reason": self.queue_paused_reason, "queued": queued})
        return {"paused": True, "reason": self.queue_paused_reason, "queued": queued}

    def resume_queue(self) -> dict:
        with self.lock:
            was_paused = self.queue_paused
            paused_for = round(_now() - self.queue_paused_at, 1) if self.queue_paused_at else 0
            self.queue_paused = False
            self.queue_paused_reason = None
            self.queue_paused_at = None
            queued = len(self.pending)
        with self.poll_cv:
            self.poll_cv.notify_all()
        if was_paused:
            self.telemetry.events.emit("queue.resumed", {"queued": queued})
            self.telemetry.audit.append("queue.resume", actor="operator", detail={"queued": queued, "pausedSeconds": paused_for})
        return {"paused": False, "queued": queued, "pausedSeconds": paused_for}

    def reap_jobs(self) -> dict:
        """Fail timed-out jobs and re-queue work orphaned by a dead/absent peer."""
        now = _now()
        recovered = 0
        timed_out = 0
        with self.lock:
            online = {p["peerId"] for p in self._active_peers_unlocked() if p["online"]}
            for job in list(self.jobs.values()):
                if job.get("status") != "running":
                    continue
                claimed = job.get("claimed") or now
                timeout = float(job.get("timeout") or 120.0)
                owner = job.get("claimedBy")
                orphaned = bool(owner) and owner not in online and (now - claimed) > 20.0
                if orphaned:
                    job["status"] = "queued"
                    job["claimedBy"] = None
                    job["claimed"] = None
                    job["attempts"] = int(job.get("attempts") or 0) + 1
                    job["note"] = (job.get("note") or "") + f" [requeued: peer {owner} went away]"
                    if job["id"] not in self.pending:
                        self.pending.append(job["id"])
                    recovered += 1
                    self.poll_cv.notify_all()
                    self.telemetry.events.emit("job.requeued_orphan", {"id": job["id"], "type": job["type"], "peer": owner}, level="warn")
                    continue
                if (now - claimed) > timeout:
                    job["status"] = "error"
                    job["ok"] = False
                    job["error"] = f"timeout after {timeout:.0f}s in {'peer ' + owner if owner else 'queue'}"
                    job["finished"] = now
                    job["duration_ms"] = round((now - claimed) * 1000, 1)
                    timed_out += 1
                    evt = self.job_events.get(job["id"])
                    if evt:
                        evt.set()
                    self.telemetry.record_job(job)
                    self.telemetry.audit.append("job.timeout", actor="reaper", target=job["id"],
                                                detail={"type": job["type"], "peer": owner}, outcome="error")
        self.telemetry.metrics.inc("bridge_jobs_reaped_total", recovered + timed_out)
        return {"recovered": recovered, "timedOut": timed_out}

    # -- MCP observability & confirmations ------------------------------------

    def publish_mcp_event(self, kind: str, detail: dict = None, level: str = "info"):
        """One place for MCP-side events: dashboard stream + audit chain + counts."""
        self.telemetry.events.emit(kind, detail or {}, level=level)
        audit_action = str(kind)
        if audit_action.startswith("mcp."):
            outcome = "denied" if ("denied" in audit_action or "expired" in audit_action) else "ok"
            self.telemetry.audit.append(
                audit_action, actor="mcp", target=str((detail or {}).get("requestId")
                                                       or (detail or {}).get("subscriptionId") or ""),
                detail=detail or {}, outcome=outcome,
            )
        self.telemetry.metrics.inc("bridge_mcp_events_total", 1, {"kind": audit_action})
        return {"ok": True, "kind": kind}

    def mcp_dispatcher_take_stream(self, rpc_out) -> dict:
        """See `mcp_core.take_subscription_stream` (shared with the stdio transport)."""
        return take_subscription_stream(rpc_out)

    def confirm_gate_summary(self) -> dict:
        """Gate posture for the *unauthenticated* health endpoint: numbers only,
        never the pending rows (they carry argument previews)."""
        if self.confirm_gate is None:
            return {"mode": "off", "pendingCount": 0, "counters": {}}
        status = self.confirm_gate.status()
        return {
            "version": status["version"],
            "mode": status["mode"],
            "pendingCount": status["pendingCount"],
            "ttlSeconds": status["ttlSeconds"],
            "allowanceSeconds": status["allowanceSeconds"],
            "counters": status["counters"],
        }

    def confirmations_report(self, body: dict = None) -> dict:
        """Status plus the MCP-side answers the dispatcher still owes a client."""
        body = dict(body or {})
        action = (body.get("action") or "status").strip().lower()
        if action in ("approve", "deny"):
            request_id = body.get("requestId") or body.get("id")
            if not request_id:
                raise ValueError(f"{action} needs a requestId")
            responses = {"approve": action == "approve", "note": body.get("note")}
            if action == "approve" and body.get("remember"):
                responses["remember"] = True
            try:
                decision = self.confirm_gate.resolve(
                    request_id, responses,
                    principal=body.get("principal"), source=body.get("source") or "operator",
                )
            except GateError as exc:
                raise ValueError(f"{exc.reason}: {exc.message}") from exc
            self.publish_mcp_event("mcp.confirm." + ("approved" if decision.get("state") == "approved"
                                                     else "denied"),
                                   {**decision, "answeredBy": body.get("answeredBy") or "rest"})
            return {"ok": True, **decision}
        if action == "expire":
            return {"ok": True, "swept": self.confirm_gate.expire_stale()}
        report = self.confirm_gate.status()
        report["ok"] = True
        report["subscriptions"] = self.mcp_dispatcher.subscription_status()
        return report

    # -- multi-studio fleet ---------------------------------------------------

    def _load_fleet(self):
        try:
            raw = json.loads(self._fleet_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self.studio_pin = raw.get("pinned") or None
            labels = raw.get("labels")
            if isinstance(labels, dict):
                self.peer_labels = {str(k): str(v)[:60] for k, v in labels.items()}

    def _save_fleet(self):
        try:
            self._fleet_path.write_text(
                json.dumps({"pinned": self.studio_pin, "labels": self.peer_labels},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def fleet_snapshot(self) -> dict:
        """One entry per Studio DataModel the bridge can talk to."""
        with self.lock:
            peers = self._active_peers_unlocked()
            queued_by_peer = {}
            for job_id in self.pending:
                job = self.jobs.get(job_id) or {}
                target = job.get("target_peer")
                if target:
                    queued_by_peer[target] = queued_by_peer.get(target, 0) + 1
            pin = self.studio_pin
            labels = dict(self.peer_labels)
            online_count = sum(1 for p in peers if p["online"])
        studios = []
        pin_online = False
        for p in peers:
            entry = {
                "peerId": p["peerId"],
                "studioId": p["peerId"],
                "label": labels.get(p["peerId"]) or f"{p['place']} ({p['dm']})",
                "dm": p["dm"],
                "place": p["place"],
                "placeId": p["placeId"],
                "gameId": p["gameId"],
                "online": p["online"],
                "ageSeconds": p["ageSeconds"],
                "client": p["client"],
                "playerCount": p["playerCount"],
                "pluginVersion": p["pluginVersion"],
                "studioVersion": p.get("studioVersion") or None,
                "pinned": p["peerId"] == pin,
                "queuedJobs": queued_by_peer.get(p["peerId"], 0),
                "defaultTarget": False,
            }
            if entry["pinned"] and p["online"]:
                pin_online = True
            studios.append(entry)
        # Mark which peer an untargeted job would actually reach.
        if pin_online:
            for entry in studios:
                entry["defaultTarget"] = entry["pinned"] and entry["online"]
        else:
            for entry in studios:
                entry["defaultTarget"] = bool(entry["online"] and entry["dm"] == "edit")
                if entry["defaultTarget"]:
                    break
        return {
            "ok": True,
            "count": len(studios),
            "online": online_count,
            "pinned": pin,
            "pinOnline": pin_online,
            "routing": "pinned" if pin_online else "auto",
            "studios": studios,
            "hint": (
                "Target one with peerId on any tool, or POST /api/studios "
                "{\"action\":\"set_active\",\"peerId\":\"...\"} to make it the default."
            ),
        }

    def set_active_peer(self, peer_id: str = None, place_id: int = None) -> dict:
        with self.lock:
            peers = self._active_peers_unlocked()
            chosen = None
            if peer_id:
                chosen = next((p for p in peers if p["peerId"] == peer_id), None)
                if not chosen:
                    raise KeyError(f"No connected Studio with peerId {peer_id!r}")
            elif place_id:
                chosen = next((p for p in peers if p["placeId"] == int(place_id) and p["online"]), None) \
                    or next((p for p in peers if p["placeId"] == int(place_id)), None)
                if not chosen:
                    raise KeyError(f"No connected Studio on placeId {place_id}")
            else:
                raise ValueError("set_active needs peerId or placeId")
            self.studio_pin = chosen["peerId"]
            self._save_fleet()
        self.telemetry.audit.append("fleet.set_active", actor="operator",
                                    detail={"peerId": chosen["peerId"], "place": chosen["place"]})
        return {"ok": True, "active": chosen["peerId"], "place": chosen["place"], "dm": chosen["dm"]}

    def clear_active_peer(self) -> dict:
        with self.lock:
            previous = self.studio_pin
            self.studio_pin = None
            self._save_fleet()
        if previous:
            self.telemetry.audit.append("fleet.clear_active", actor="operator", detail={"previous": previous})
        return {"ok": True, "active": None, "previous": previous}

    def label_peer(self, peer_id: str, label: str = None) -> dict:
        with self.lock:
            peers = self._active_peers_unlocked()
            if not any(p["peerId"] == peer_id for p in peers):
                raise KeyError(f"No connected Studio with peerId {peer_id!r}")
            if label:
                self.peer_labels[peer_id] = str(label)[:60]
            else:
                self.peer_labels.pop(peer_id, None)
            self._save_fleet()
            saved = self.peer_labels.get(peer_id)
        return {"ok": True, "peerId": peer_id, "label": saved}

    def fleet_action(self, payload: dict = None) -> dict:
        body = dict(payload or {})
        action = (body.get("action") or "list").lower()
        if action in ("list", "status"):
            return self.fleet_snapshot()
        if self.read_only:
            raise PermissionError("Blocked by Read-Only Inspector mode: fleet changes are disabled.")
        if action in ("set_active", "pin", "activate"):
            return self.set_active_peer(body.get("peerId") or body.get("peer_id"),
                                        body.get("placeId") or body.get("place_id"))
        if action in ("clear_active", "unpin"):
            return self.clear_active_peer()
        if action == "label":
            return self.label_peer(body.get("peerId") or body.get("peer_id"), body.get("label"))
        raise ValueError(f"Unknown fleet action: {action}")

    BROADCAST_TOOL_KINDS = {
        "ping_studio": ("ping", {}),
        "get_studio_state": ("studio_state", {}),
        "survey_place": ("survey", {"includeSource": False}),
        "inspect_instance": ("inspect", {"depth": 2}),
        "script_read": ("read_script", {}),
        "get_console_output": ("get_console", {}),
    }

    def broadcast(self, kind: str = "ping", payload: dict = None, timeout: float = 20.0,
                  dm: str = None, tool: str = None) -> dict:
        """Fan one read-only job out to every online Studio and collect per-peer results."""
        if tool:
            mapped = self.BROADCAST_TOOL_KINDS.get(tool)
            if not mapped:
                raise ValueError(f"broadcast does not know tool {tool!r}; use one of "
                                 f"{', '.join(self.BROADCAST_TOOL_KINDS)}")
            kind, defaults = mapped
            payload = {**defaults, **(payload or {})}
        if kind not in self.BROADCAST_KINDS:
            raise ValueError(
                f"broadcast only runs read-only kinds ({', '.join(self.BROADCAST_KINDS)}); "
                f"refusing {kind!r}"
            )
        with self.lock:
            targets = [p for p in self._active_peers_unlocked()
                       if p["online"] and (not dm or dm == "auto" or p["dm"] == dm)]
        if not targets:
            return {"ok": False, "reason": "no_peer", "error": "No online Studio to broadcast to.",
                    "kind": kind, "results": []}

        started = _now()
        submitted = []
        for peer in targets:
            try:
                job, event = self.enqueue_job(kind=kind, payload=payload, note=f"broadcast:{kind}",
                                              peer_id=peer["peerId"], timeout=max(10.0, timeout))
            except Exception as exc:  # noqa: BLE001 - one bad peer must not kill the fan-out
                submitted.append({"peer": peer, "job": None, "event": None, "error": str(exc)})
                continue
            submitted.append({"peer": peer, "job": job, "event": event, "error": None})

        deadline = time.monotonic() + max(1.0, timeout)
        results = []
        for item in submitted:
            peer, job, event, error = item["peer"], item["job"], item["event"], item["error"]
            row = {
                "peerId": peer["peerId"],
                "place": peer["place"],
                "placeId": peer["placeId"],
                "dm": peer["dm"],
                "label": self.peer_labels.get(peer["peerId"]) or f"{peer['place']} ({peer['dm']})",
            }
            if job is None:
                row.update({"ok": False, "status": "error", "error": error})
                results.append(row)
                continue
            event.wait(timeout=max(0.1, deadline - time.monotonic()))
            with self.lock:
                finished = dict(self.jobs.get(job["id"]) or job)
            row.update({
                "jobId": finished.get("id"),
                "status": finished.get("status"),
                "ok": finished.get("status") == "done" and bool(finished.get("ok", True)),
                "durationMs": finished.get("duration_ms"),
                "result": finished.get("result"),
                "error": finished.get("error"),
            })
            results.append(row)

        done = sum(1 for r in results if r.get("ok"))
        summary = {
            "ok": done > 0,
            "kind": kind,
            "peers": len(results),
            "succeeded": done,
            "failed": len(results) - done,
            "elapsedMs": round((_now() - started) * 1000.0, 1),
            "results": results,
        }
        self.telemetry.events.emit("fleet.broadcast", {"kind": kind, "peers": len(results), "ok": done})
        return summary

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

        goodbye = qs.get("goodbye", ["0"])[0] in ("1", "true", "yes")
        with self.poll_cv:
            existing = self.peers.get(peer_id, {})
            if goodbye:
                if peer_id in self.peers:
                    self.peers[peer_id]["lastPoll"] = now - (PEER_ONLINE_WINDOW + 1.0)
                    self.peers[peer_id]["evicted"] = True
                self.poll_cv.notify_all()
                return {
                    "peerId": peer_id,
                    "dm": dm,
                    "place": place,
                    "placeId": place_id,
                    "goodbye": True,
                }
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
                "evicted": False,
            }
            self.peers[peer_id] = info
            return dict(info)

    def evict_playtest_peers(self, reason: str = "playtest_stopped") -> dict:
        """Immediately mark runtime (`server` / `client`) peers offline and cancel queued runtime jobs."""
        evicted_peers = 0
        cancelled_jobs = 0
        with self.poll_cv:
            now = _now()
            for pid, pinfo in list(self.peers.items()):
                if pinfo.get("dm") in ("server", "client"):
                    if (now - pinfo.get("lastPoll", 0.0)) < PEER_ONLINE_WINDOW and not pinfo.get("evicted"):
                        evicted_peers += 1
                    pinfo["lastPoll"] = now - (PEER_ONLINE_WINDOW + 1.0)
                    pinfo["evicted"] = True
            for jid in list(self.pending):
                job = self.jobs.get(jid)
                if job and job.get("status") == "queued" and job.get("target_dm") in ("server", "client"):
                    try:
                        self.pending.remove(jid)
                    except ValueError:
                        pass
                    job["status"] = "cancelled"
                    job["ok"] = False
                    job["error"] = f"Playtest stopped ({reason})"
                    job["finished"] = now
                    evt = self.job_events.get(jid)
                    if evt:
                        evt.set()
                    cancelled_jobs += 1
            self.poll_cv.notify_all()
        if evicted_peers or cancelled_jobs:
            self.telemetry.events.emit("playtest.evicted", {
                "evictedPeers": evicted_peers,
                "cancelledJobs": cancelled_jobs,
                "reason": reason,
            })
        return {
            "ok": True,
            "evictedPeers": evicted_peers,
            "cancelledJobs": cancelled_jobs,
            "reason": reason,
        }

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

    # Read-only job kinds that are safe to fan out across every open Studio.
    BROADCAST_KINDS = (
        "ping", "studio_state", "survey", "inspect", "search_tree", "get_properties",
        "compare_instances", "read_script", "read_scripts", "get_console", "snapshot_list",
    )

    def _peer_matches_job_unlocked(self, peer: dict, job: dict) -> bool:
        req_peer = job.get("target_peer")
        if req_peer and peer["peerId"] != req_peer:
            return False
        if not req_peer and job.get("target_dm") in (None, "", "auto") and not job.get("place_id"):
            # An explicitly pinned Studio wins over "any edit peer", but only
            # while it is actually online — a closed Studio must not stall work.
            pin = self.studio_pin
            if pin:
                pinned = next((p for p in self._active_peers_unlocked()
                               if p["peerId"] == pin and p["online"]), None)
                if pinned and peer["peerId"] != pin:
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
                p_entry = self.peers.get(peer_info["peerId"])
                if not p_entry or p_entry.get("evicted"):
                    return None
                if self.queue_paused:
                    # Keep the peer alive (lastPoll still updates) but hand out nothing.
                    p_entry["lastPoll"] = _now()
                    if self.poll_cv.wait(timeout=max(0.1, min(0.5, deadline - time.monotonic()))):
                        continue
                    if time.monotonic() >= deadline:
                        return None
                    continue
                p_entry["lastPoll"] = _now()
                best = None
                for job_id in list(self.pending):
                    job = self.jobs.get(job_id)
                    if not job or job["status"] != "queued":
                        self.pending.remove(job_id)
                        continue
                    if not self._peer_matches_job_unlocked(peer_info, job):
                        continue
                    # Priority first, then FIFO, so a critical agent probe is not
                    # stuck behind a long bulk-build job from another agent.
                    rank = (int(job.get("priority", 5)), job.get("created", 0))
                    if best is None or rank < best[0]:
                        best = (rank, job_id)
                if best is not None:
                    job_id = best[1]
                    job = self.jobs[job_id]
                    self.pending.remove(job_id)
                    job["status"] = "running"
                    job["claimed"] = _now()
                    job["claimedBy"] = peer_info["peerId"]
                    job["dm"] = peer_info["dm"]
                    job["attempts"] = int(job.get("attempts") or 0) + 1
                    self.telemetry.events.emit("job.claim", {
                        "id": job["id"], "type": job["type"], "peer": peer_info["peerId"],
                        "priority": job.get("priority", 5),
                    })
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
        "run_luau", "require_fresh", "build", "write_script", "batch_write", "multi_edit",
        "scatter_props",
        "find_replace_scripts", "set_properties", "mass_set_properties",
        "clone_instance", "reparent_instance", "delete", "attributes",
        "tags", "selection", "camera", "history", "terrain",
        "insert_asset", "import_image", "playtest", "open_script",
        # v5.4 playtest surface: synthetic input drives the live game and the
        # native test runner writes a temporary Script, so both are mutations.
        "virtual_input", "device_simulator", "studio_tests",
    }

    def enqueue_job(
        self,
        kind: str,
        payload=None,
        note: str = None,
        dm: str = None,
        peer_id: str = None,
        place_id: int = None,
        idempotency_key: str = None,
        priority: int = 5,
        max_retries: int = 0,
        timeout: float = None,
        trace: dict = None,
        actor: dict = None,
    ):
        payload_dict = dict(payload or {}) if isinstance(payload, dict) else {}

        if self.read_only:
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
                or (kind == "device_simulator" and payload_dict.get("action") in ("status", "list"))
            )
            if not (isinstance(kind, str) and kind in READ_ONLY_JOB_TYPES) and not read_sub_ok:
                raise PermissionError(
                    f"Bridge is running in --read-only Inspector mode; job '{kind}' is blocked."
                )

        effective_dm = (dm or payload_dict.get("_targetDm") or "auto").lower()
        if effective_dm == "client":
            payload_dict["_targetDm"] = "client"

        effective_place = place_id if place_id is not None else self.guard_place_id

        priority = max(0, min(int(priority if priority is not None else 5), 9))
        max_retries = max(0, min(int(max_retries or 0), 5))
        idempotency_key = (idempotency_key or "").strip()[:200] or None

        with self.poll_cv:
            now = _now()
            # Purge idempotency keys older than an hour so the map stays bounded.
            if len(self.idempotency) > 512:
                for key in [k for k, v in self.idempotency.items() if now - v.get("ts", 0) > 3600]:
                    self.idempotency.pop(key, None)

            if idempotency_key:
                prior = self.idempotency.get(idempotency_key)
                prior_job = self.jobs.get(prior["id"]) if prior else None
                if prior_job and prior_job.get("status") not in ("error", "cancelled"):
                    prior_job["deduplicated"] = int(prior_job.get("deduplicated") or 0) + 1
                    self.telemetry.metrics.inc("bridge_job_dedupe_total", 1, {"type": kind})
                    self.telemetry.events.emit("job.dedupe", {
                        "id": prior_job["id"], "type": kind, "key": idempotency_key,
                    })
                    return prior_job, self.job_events.setdefault(prior_job["id"], threading.Event())

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
                "created": now,
                "claimed": None,
                "finished": None,
                "duration_ms": None,
                "ok": None,
                "result": None,
                "error": None,
                "logs": [],
                "priority": priority,
                "maxRetries": max_retries,
                "retries": 0,
                "attempts": 0,
                "timeout": float(timeout) if timeout else None,
                "idempotencyKey": idempotency_key,
                "deduplicated": 0,
                # v5.4.0 provenance: which MCP caller (and which of its spans)
                # caused this job. Absent for REST/CLI callers.
                "trace": dict(trace) if isinstance(trace, dict) and trace else None,
                "actor": dict(actor) if isinstance(actor, dict) and actor else None,
            }
            self.jobs[jid] = job
            self.job_events[jid] = done_evt
            self.pending.append(jid)
            self.history.append(jid)
            if idempotency_key:
                self.idempotency[idempotency_key] = {"id": jid, "ts": now}
            while len(self.jobs) > 600:
                oldest = next(iter(self.jobs))
                if oldest == jid:
                    break
                self.jobs.pop(oldest, None)
                self.job_events.pop(oldest, None)
            self.telemetry.metrics.inc("bridge_jobs_enqueued_total", 1, {"type": kind})
            self.telemetry.events.emit("job.enqueue", {
                "id": jid, "type": kind, "dm": effective_dm, "priority": priority,
                "note": note, "idempotencyKey": idempotency_key,
            })
            if kind in self.MUTATING_JOB_TYPES:
                self.telemetry.audit.append(
                    "job.enqueue", actor=note or "agent", target=jid,
                    detail={"type": kind, "dm": effective_dm, "priority": priority},
                )
            self.poll_cv.notify_all()
            return job, done_evt

    TRANSIENT_ERROR_HINTS = (
        "timeout", "timed out", "time-out", "peer", "disconnect", "connection",
        "econn", "temporarily", "busy", "try again", "socket", "network",
        "unknown job type", "not ready",
    )

    def _is_transient_error(self, body: dict, job: dict) -> bool:
        if body.get("retryable") is True:
            return True
        message = str(body.get("error") or "").lower()
        if not message:
            return False
        return any(hint in message for hint in self.TRANSIENT_ERROR_HINTS)

    def complete_job(self, body: dict) -> bool:
        jid = body.get("id")
        with self.poll_cv:
            job = self.jobs.get(jid)
            if not job:
                return False
            now = _now()
            ok = bool(body.get("ok"))
            if not ok and int(job.get("retries") or 0) < int(job.get("maxRetries") or 0) \
                    and self._is_transient_error(body, job):
                job["retries"] = int(job.get("retries") or 0) + 1
                job["attempts"] = int(job.get("attempts") or 0)
                job["status"] = "queued"
                job["claimedBy"] = None
                job["claimed"] = None
                job["error"] = None
                job["note"] = (job.get("note") or "") + f" [retry {job['retries']}: {str(body.get('error'))[:120]}]"
                if jid not in self.pending:
                    self.pending.append(jid)
                self.telemetry.metrics.inc("bridge_job_retries_total", 1, {"type": job["type"]})
                self.telemetry.events.emit("job.retry", {
                    "id": jid, "type": job["type"], "retry": job["retries"],
                    "error": str(body.get("error"))[:200],
                }, level="warn")
                self.poll_cv.notify_all()
                return True

            job["status"] = "done" if ok else "error"
            job["ok"] = ok
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
            if job.get("type") == "playtest" and isinstance(job.get("payload"), dict):
                if job["payload"].get("action") in ("stop", "force_stop", "cleanup"):
                    self.evict_playtest_peers(reason="playtest_job_completed")
            self.telemetry.record_job(job)
            if not ok:
                self.telemetry.audit.append(
                    "job.error", actor=job.get("claimedBy") or "studio", target=jid,
                    detail={"type": job["type"], "error": str(job.get("error"))[:300]}, outcome="error",
                )
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
        if self.read_only and action == "save":
            raise PermissionError("Blocked by Read-Only Inspector mode: snapshot saving is disabled.")
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
                tunnel_state = self.tunnel.state
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
                    "uptimeSeconds": round(_now() - self.started, 1),
                    "authMode": "scoped" if self.token else "open",
                    "confirmGate": self.confirm_gate_summary(),
                    "tunnel": {
                        "state": tunnel_state,
                        "publicUrl": self.tunnel.public_url,
                        "localUrl": self.tunnel.local_url,
                    },
                    "agents": self.bus.stats()["agents"],
                    "agentsOnline": self.bus.stats()["agentsOnline"],
                    "studios": {
                        "count": len(peers),
                        "online": sum(1 for p in peers if p["online"]),
                        "active": self.studio_pin,
                        "routing": "pinned" if self.studio_pin else "auto",
                    },
                    "queuePaused": self.queue_paused,
                    "queuePausedReason": self.queue_paused_reason,
                    "sweeper": {
                        "intervalSeconds": self.sweep_interval,
                        "offlineSeconds": self.sweep_offline_seconds,
                        "lastSweep": self._last_sweep or None,
                        "sweeps": self._sweeps,
                    },
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
                    "uptimeSeconds": round(_now() - self.started, 1),
                    "bus": self.bus.stats(),
                    "tunnel": {
                        "state": self.tunnel.state,
                        "publicUrl": self.tunnel.public_url,
                    },
                }
        if op == "studios":
            return self.fleet_snapshot()
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
                idempotency_key=body.get("idempotencyKey") or body.get("idempotency_key"),
                priority=body.get("priority", 5),
                max_retries=body.get("maxRetries", body.get("max_retries", 0)),
                timeout=body.get("timeout"),
                trace=body.get("trace") if isinstance(body.get("trace"), dict) else None,
                actor=body.get("actor") if isinstance(body.get("actor"), dict) else None,
            )
            wait = float(body.get("wait") or 0)
            if wait > 0:
                evt.wait(timeout=min(wait, 120.0))
            with self.lock:
                return dict(self.jobs[job["id"]])
        if op == "confirmations":
            return self.confirmations_report(body)
        if op == "subscriptions":
            return self.mcp_dispatcher.subscription_status()
        if op == "mcp_event":
            return self.publish_mcp_event(body.get("kind"), body.get("detail"),
                                          body.get("level") or "info")
        if op == "bus":
            return self.bus.handle(body)
        if op == "fleet":
            return self.fleet_action(body)
        if op == "broadcast":
            return self.broadcast(
                kind=body.get("type") or body.get("kind") or "ping",
                payload=body.get("payload"),
                timeout=float(body.get("timeout") or 20.0),
                dm=body.get("dm"),
                tool=body.get("tool"),
            )
        if op == "tunnel":
            return self.tunnel_action(body)
        if op == "tokens":
            return self.tokens.handle(body)
        if op == "metrics":
            return self.metrics_snapshot()
        if op == "audit":
            if body.get("verify"):
                return self.telemetry.audit.verify()
            return self.telemetry.audit.tail(int(body.get("limit") or 100), body.get("action"))
        if op == "events":
            if body.get("wait") is not None:
                return self.telemetry.events.tail(int(body.get("cursor") or 0), float(body.get("wait") or 0), int(body.get("limit") or 100))
            return self.telemetry.events.since(int(body.get("cursor") or 0), int(body.get("limit") or 200), body.get("kinds"))
        if op in ("ops", "ops_report"):
            return self.ops_report()
        if op == "queue_pause":
            return self.pause_queue(body.get("reason"))
        if op == "queue_resume":
            return self.resume_queue()
        if op == "sweep":
            return self.auto_sweep()
        if op == "queue_clear":
            cancelled = self.clear_queue()
            self.telemetry.audit.append("queue.clear", actor="internal", detail={"cancelled": cancelled})
            return {"cancelled": cancelled}
        if op == "reap":
            return self.reap_jobs()
        if op == "evict_playtest":
            return self.evict_playtest_peers(reason=(body or {}).get("reason") or "direct_evict")
        raise ValueError(f"Unknown internal op: {op}")

    # -- tunnel control ------------------------------------------------------

    def tunnel_action(self, payload: dict) -> dict:
        """Refuse to publish an unauthenticated bridge, then delegate."""
        action = ((payload or {}).get("action") or "status").lower()
        if action in ("start", "restart") and not self.token:
            status = self.tunnel.status()
            status.update({
                "state": "blocked",
                "running": False,
                "lastError": (
                    "Refusing to expose the bridge publicly without a token. "
                    "Run `python setup.py` (it writes bridge.token) or start server.py with --token."
                ),
                "security": "blocked_no_token",
            })
            self.telemetry.events.emit("tunnel.blocked", {"reason": "no_token"}, level="warn")
            self.telemetry.audit.append("tunnel.blocked", actor="system", detail={"reason": "no_token"}, outcome="denied")
            return status
        return self.tunnel.handle(payload)

    # -- ops reporting -------------------------------------------------------

    def metrics_snapshot(self) -> dict:
        with self.lock:
            online, _ = self.legacy_studio_summary_unlocked()
            peers_online = sum(1 for p in self._active_peers_unlocked() if p["online"])
            queued = len(self.pending)
            running = sum(1 for j in self.jobs.values() if j.get("status") == "running")
            jobs_total = len(self.jobs)
            online_flag = online
        bus_stats = self.bus.stats()
        t = self.telemetry.metrics
        t.gauge("bridge_peers_online", peers_online)
        t.gauge("bridge_studio_connected", 1 if online_flag else 0)
        t.gauge("bridge_queue_depth", queued)
        t.gauge("bridge_jobs_running", running)
        t.gauge("bridge_jobs_tracked", jobs_total)
        t.gauge("bridge_agents_online", bus_stats["agentsOnline"])
        t.gauge("bridge_tasks_open", sum(
            v for k, v in bus_stats["taskCounts"].items() if k in ("todo", "in_progress", "blocked")
        ))
        t.gauge("bridge_uptime_seconds", round(_now() - self.started, 1))
        t.gauge("bridge_tunnel_online", 1 if self.tunnel.state == "online" else 0)
        t.gauge("bridge_queue_paused", 1 if self.queue_paused else 0)
        t.gauge("bridge_sweeps_total", self._sweeps)
        return t.snapshot(uptime=_now() - self.started)

    def ops_report(self) -> dict:
        health = self.direct_dispatch("health", {})
        metrics = self.metrics_snapshot()
        bus_stats = self.bus.stats()
        tasks = self.bus.list_tasks(include_done=False)
        audit = self.telemetry.audit.tail(10)
        with self.lock:
            recent = [summary(self.jobs[i]) for i in list(self.history)[-12:]][::-1]
        gate_status = self.confirm_gate.status()
        confirm_block = {
            "mode": gate_status["mode"],
            "enabled": gate_status["mode"] != "off",
            "ttlSeconds": gate_status["ttlSeconds"],
            "allowanceSeconds": gate_status["allowanceSeconds"],
            "pending": gate_status["pending"],
            "pendingCount": gate_status["pendingCount"],
            "allowances": gate_status["allowances"],
            "counters": gate_status["counters"],
            "recentDecisions": gate_status["recentDecisions"][:10],
        }
        try:
            subscriptions_block = self.mcp_dispatcher.subscription_status()
        except Exception:  # noqa: BLE001
            subscriptions_block = {"count": 0, "subscriptions": []}
        self.telemetry.metrics.gauge("bridge_confirmations_pending", gate_status["pendingCount"])
        self.telemetry.metrics.gauge("bridge_mcp_subscriptions", subscriptions_block.get("count", 0))
        return {
            "ok": True,
            "version": VERSION,
            "uptimeSeconds": round(_now() - self.started, 1),
            "health": health,
            "tunnel": self.tunnel.status(),
            "metrics": {
                "counters": {f"{c['name']}{json.dumps(c['labels'], sort_keys=True) if c['labels'] else ''}": c["value"] for c in metrics["counters"][:80]},
                "histograms": metrics["histograms"][:20],
            },
            "bus": bus_stats,
            "openTasks": tasks["tasks"][:20],
            "audit": audit["entries"],
            "recentJobs": recent,
            "queue": {
                "paused": self.queue_paused,
                "reason": self.queue_paused_reason,
                "pausedAt": self.queue_paused_at,
                "depth": health.get("queued"),
            },
            "sweeper": {
                "intervalSeconds": self.sweep_interval,
                "offlineSeconds": self.sweep_offline_seconds,
                "lastSweep": self._last_sweep or None,
                "sweeps": self._sweeps,
                "tasksReaped": bus_stats.get("tasksReaped"),
                "agentsReaped": bus_stats.get("agentsReaped"),
            },
            "jobLatency": self._job_latency_summary(),
            "fleet": self.fleet_snapshot(),
            "mcp": {
                "protocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                "modernProtocol": MODERN_PROTOCOL_VERSION,
                "tasks": len(getattr(self.mcp_dispatcher, "tasks", {}) or {}),
                "confirmGate": confirm_block,
                "subscriptions": subscriptions_block,
                "autoTaskMs": getattr(self.mcp_dispatcher, "auto_task_ms", 0.0),
                "legacyConfirmWaitSeconds": getattr(self.mcp_dispatcher, "legacy_wait_seconds", 0.0),
            },
            "servedAt": _now(),
        }

    def _job_latency_summary(self, limit: int = 300) -> dict:
        """p50/p95 wall time per tool over recent completed jobs (no external deps)."""
        with self.lock:
            samples = {}
            for job_id in list(self.history)[-limit:]:
                job = self.jobs.get(job_id) or {}
                duration = job.get("duration_ms")
                if not isinstance(duration, (int, float)) or duration <= 0:
                    continue
                samples.setdefault(job.get("type") or "unknown", []).append(float(duration))
        out = {}
        for tool, values in samples.items():
            values.sort()
            count = len(values)
            if not count:
                continue
            out[tool] = {
                "count": count,
                "p50": round(values[min(count - 1, int(0.50 * count))], 1),
                "p95": round(values[min(count - 1, int(0.95 * count))], 1),
                "max": round(values[-1], 1),
                "mean": round(sum(values) / count, 1),
            }
        return dict(sorted(out.items(), key=lambda kv: -kv[1]["count"])[:20])


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
        "trace": job.get("trace"),
        "actor": job.get("actor"),
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

        def _is_local_convenience_request(self) -> bool:
            """Only the local dashboard/CLI gets token bootstrap or anonymous reads.

            Socket loopback alone is insufficient: hostile web pages can request
            localhost, and reverse proxies may connect from loopback as well.
            """
            if not self._is_loopback():
                return False
            if self.headers.get("Sec-Fetch-Site", "").lower() not in ("", "none", "same-origin"):
                return False
            try:
                host = urlsplit("http://" + (self.headers.get("Host") or ""))
                if (host.hostname not in ("localhost", "127.0.0.1", "::1") or
                    (host.port or 80) != self.server.server_port or host.username is not None or
                    host.password is not None or host.path or host.query or host.fragment):
                    return False
                origin = self.headers.get("Origin")
                if origin:
                    parsed = urlsplit(origin)
                    if (parsed.scheme != "http" or parsed.hostname != host.hostname or
                        (parsed.port or 80) != (host.port or 80) or parsed.username is not None or
                        parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
                        return False
            except ValueError:
                return False
            return True

        def _request_origin(self) -> str:
            host_hdr = (self.headers.get("Host") or f"127.0.0.1:{self.server.server_port}").strip()
            forwarded_proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
            proto = "https" if (self.headers.get("CF-Connecting-IP") or forwarded_proto == "https") else "http"
            return f"{proto}://{host_hdr}".rstrip("/")

        def _is_allowed_origin(self) -> bool:
            """DNS-rebinding and cross-origin browser request guard (MCP security spec).

            Non-browser clients (CLI, curl, Python SDKs, Roblox Studio HttpService)
            do not send `Origin` and pass through. When `Origin` IS present, it must
            match the server's own Host, the active public tunnel URL, or an
            explicit `BRIDGE_ALLOWED_ORIGINS` entry.
            """
            raw_origin = self.headers.get("Origin")
            if raw_origin is None:
                return True
            origin = raw_origin.strip().rstrip("/")
            if not origin or origin.lower() == "null":
                return "*" in state.allowed_origins
            if "*" in state.allowed_origins or origin in state.allowed_origins:
                return True
            try:
                parsed = urlsplit(origin)
            except ValueError:
                return False
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                return False
            if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
                return False

            # Match against the active or configured tunnel public URL.
            for candidate in (state.tunnel.public_url, state.tunnel.user_public_url):
                if candidate and origin.lower() == candidate.rstrip("/").lower():
                    return True

            # Match against the incoming Host header (normalizing default ports 80/443).
            try:
                host = urlsplit(self._request_origin())
            except ValueError:
                return False
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            host_port = host.port or (443 if host.scheme == "https" else 80)
            if parsed.hostname.lower() == (host.hostname or "").lower() and origin_port == host_port:
                return True
            loopbacks = {"localhost", "127.0.0.1", "::1"}
            if (
                parsed.hostname.lower() in loopbacks
                and (host.hostname or "").lower() in loopbacks
                and origin_port == self.server.server_port
            ):
                return True
            return False

        def _check_origin(self) -> bool:
            if self._is_allowed_origin():
                return True
            state.telemetry.metrics.inc("bridge_origin_rejections_total", 1)
            self._send(403, {"error": "Origin not allowed", "origin": self.headers.get("Origin")})
            return False

        def _oauth_protected_resource_metadata(self, path_scoped: bool = True) -> dict:
            """RFC 9728 OAuth 2.0 Protected Resource Metadata for MCP clients."""
            origin = self._request_origin()
            meta = {
                "resource": f"{origin}/mcp" if path_scoped else origin,
                "bearer_methods_supported": ["header"],
                "scopes_supported": ["mcp:read", "mcp:write", "mcp:admin"],
                "resource_name": "Arena Roblox Studio MCP Bridge",
                "resource_documentation": f"{origin}/api/agent-guide",
            }
            issuer = (os.environ.get("BRIDGE_OAUTH_ISSUER") or "").strip()
            if issuer:
                meta["authorization_servers"] = [issuer]
            return meta

        def _www_authenticate_header(self, code: int, required: str = "read") -> str:
            origin = self._request_origin()
            res_meta = f"{origin}/.well-known/oauth-protected-resource/mcp"
            scope = f"mcp:{required}"
            if code == 403:
                return (
                    f'Bearer realm="arena-roblox-studio-mcp", error="insufficient_scope", '
                    f'scope="{scope}", resource_metadata="{res_meta}"'
                )
            return f'Bearer realm="arena-roblox-studio-mcp", resource_metadata="{res_meta}", scope="{scope}"'

        def _mcp_headers(self, protocol_version: str = None) -> dict:
            """`Mcp-Session-Id` is a legacy-era artefact: the 2026-07-28 core
            removed protocol sessions, so modern answers do not advertise one."""
            if (protocol_version or "") == MODERN_PROTOCOL_VERSION:
                return {"MCP-Protocol-Version": MODERN_PROTOCOL_VERSION}
            return {"Mcp-Session-Id": state.session_id}

        def _cors_origin(self, public_discovery: bool = False) -> str:
            if public_discovery:
                return "*"
            req_origin = self.headers.get("Origin")
            if not req_origin:
                return "*"
            return req_origin.strip() if self._is_allowed_origin() else "null"

        def _send(
            self,
            code: int,
            body,
            ctype: str = "application/json; charset=utf-8",
            extra_headers: dict = None,
            public_discovery: bool = False,
        ):
            if isinstance(body, (dict, list)):
                body = json.dumps(body)
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", self._cors_origin(public_discovery=public_discovery))
            self.send_header(
                "Access-Control-Allow-Headers",
                "Authorization, Content-Type, X-Arena-Token, MCP-Protocol-Version, "
                "Mcp-Session-Id, Mcp-Method, Mcp-Name, Last-Event-ID, Idempotency-Key",
            )
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header(
                "Access-Control-Expose-Headers",
                "Mcp-Session-Id, MCP-Protocol-Version, X-Arena-Subscription, X-Arena-Task, WWW-Authenticate",
            )
            has_cache = extra_headers and any(k.lower() == "cache-control" for k in extra_headers)
            if not has_cache:
                self.send_header("Cache-Control", "public, max-age=300" if public_discovery else "no-store")
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
            raw_len = self.headers.get("Content-Length")
            if not raw_len:
                return b""
            try:
                length = int(raw_len)
            except ValueError as exc:
                raise BodyLimitError(400, f"Invalid Content-Length header: {raw_len!r}") from exc
            if length < 0:
                raise BodyLimitError(400, "Negative Content-Length header")
            if length > state.max_body_bytes:
                raise BodyLimitError(
                    413,
                    f"Request body ({length} bytes) exceeds maximum allowed ({state.max_body_bytes} bytes)",
                )
            if length == 0:
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

        @staticmethod
        def _int_qs(qs: dict, key: str, default: int = 0, minimum: int = 0, maximum: int = 10000) -> int:
            try:
                value = int((qs.get(key) or [default])[0])
            except (TypeError, ValueError):
                return default
            return max(minimum, min(value, maximum))

        def _supplied_token(self, qs: dict):
            bearer = self.headers.get("Authorization", "")
            bearer_tok = bearer[7:].strip() if bearer.lower().startswith("bearer ") else None
            return (
                qs.get("token", [None])[0]
                or self.headers.get("X-Arena-Token")
                or bearer_tok
            )

        def _auth_ctx(self, qs: dict, required: str = "read", allow_loopback: bool = False):
            """Return (context, error). Auth is a no-op when no token is configured."""
            supplied = self._supplied_token(qs)
            ctx, err = state.authenticate(
                supplied,
                is_loopback=self._is_loopback(),
                required=required,
            )
            if err and allow_loopback and required == "read" and not supplied and self._is_local_convenience_request():
                # Backwards-compatible convenience for local dashboards/CLIs on
                # read-only discovery endpoints. Never applied to /mcp or writes.
                return {"role": "read", "id": "loopback", "label": "loopback"}, None
            return ctx, err

        def _authorized(self, qs: dict) -> bool:
            ctx, err = self._auth_ctx(qs, "read")
            return err is None

        def _require(self, qs: dict, required: str = "read", allow_loopback: bool = False):
            """Scope check that writes the error response itself. Returns ctx or None."""
            ctx, err = self._auth_ctx(qs, required, allow_loopback=allow_loopback)
            if err:
                code = err.get("code", 401)
                headers = {}
                if code == 429:
                    headers["Retry-After"] = str(err.get("retryAfter", 1))
                elif code in (401, 403):
                    headers["WWW-Authenticate"] = self._www_authenticate_header(code, required=required)
                payload = {"error": err.get("error")}
                if err.get("retryAfter"):
                    payload["retryAfter"] = err["retryAfter"]
                self._send(code, payload, extra_headers=headers or None)
                state.telemetry.metrics.inc(
                    "bridge_auth_rejections_total", 1,
                    {"code": str(code), "path": urlparse(self.path).path},
                )
                return None
            return ctx

        # -- Server-Sent Events -------------------------------------------------

        def _stream_events(self, qs: dict):
            last_event_hdr = (self.headers.get("Last-Event-ID") or "").strip()
            hdr_cursor = int(last_event_hdr) if last_event_hdr.isdigit() else 0
            cursor = self._int_qs(qs, "since", 0) or self._int_qs(qs, "cursor", 0) or hdr_cursor
            kinds = (qs.get("kinds") or [None])[0]
            try:
                max_seconds = min(float((qs.get("maxSeconds") or ["300"])[0] or 300), 900)
            except (TypeError, ValueError):
                max_seconds = 300.0
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", self._cors_origin())
            self.end_headers()

            def _write(chunk: str):
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()

            started = time.monotonic()
            # Emit immediate comment bytes so Cloudflare Tunnel flushes 200 headers
            # right away instead of holding the stream open until the first event.
            _write(": connected\n\n")
            try:
                if cursor == 0:
                    seed = state.telemetry.events.since(0, 50, kinds)
                    for event in seed["events"]:
                        _write(f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n")
                    cursor = seed["cursor"]
                while time.monotonic() - started < max_seconds:
                    batch = state.telemetry.events.tail(cursor, timeout=15.0)
                    for event in batch["events"]:
                        _write(f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n")
                        cursor = event["seq"]
                    if batch.get("timeout"):
                        _write(": keepalive\n\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def do_OPTIONS(self):
            url = urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            is_well_known = path.startswith("/.well-known/")
            if not is_well_known and not self._is_allowed_origin():
                return self._send(403, {"error": "Origin not allowed"})
            self._send(204, b"", public_discovery=is_well_known)

        def do_DELETE(self):
            if not self._check_origin():
                return
            url = urlparse(self.path)
            path = url.path.rstrip("/") or "/"
            if path == "/mcp":
                header_version = (self.headers.get("MCP-Protocol-Version") or "").strip()
                if header_version == MODERN_PROTOCOL_VERSION:
                    # SEP-2567: 2026-07-28 removed protocol sessions and DELETE /mcp.
                    return self._send(
                        405,
                        {"error": "Protocol version 2026-07-28 is stateless; DELETE /mcp is not used."},
                        extra_headers={"Allow": "POST"},
                    )
                return self._send(200, {"closed": True})
            return self._send(404, {"error": "not found"})

        def do_GET(self):
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path.rstrip("/") or "/"

            # --- Public RFC 8615 /.well-known discovery endpoints ----------------
            if path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
                return self._send(
                    200,
                    self._oauth_protected_resource_metadata(path_scoped=path.endswith("/mcp")),
                    public_discovery=True,
                )

            if path in ("/.well-known/mcp/server-card.json", "/.well-known/mcp.json"):
                card = build_mcp_server_card(
                    origin=self._request_origin(),
                    auth_required=bool(state.token or state.tokens.records),
                    read_only=state.read_only,
                    confirm_mode=state.confirm_mode,
                )
                return self._send(200, card, public_discovery=True)

            if path == "/":
                return self._send(200, dashboard_html(), "text/html; charset=utf-8")

            if path.startswith("/api/kit/image/"):
                from image_pipeline import find_kit_root
                rel_img = path[len("/api/kit/image/") :].lstrip("/")
                images_root = (find_kit_root() / "assets" / "images").resolve()
                target_img = (images_root / rel_img).resolve()
                if not target_img.is_relative_to(images_root) or not target_img.is_file():
                    return self._send(404, {"error": "image not found"})
                mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".bmp": "image/bmp"}
                return self._send(200, target_img.read_bytes(), mime_map.get(target_img.suffix.lower(), "application/octet-stream"))

            if path.startswith("/api/kit/audio/"):
                from image_pipeline import find_kit_root
                rel_aud = path[len("/api/kit/audio/") :].lstrip("/")
                audio_root = (find_kit_root() / "assets" / "audio").resolve()
                target_aud = (audio_root / rel_aud).resolve()
                if not target_aud.is_relative_to(audio_root) or not target_aud.is_file():
                    return self._send(404, {"error": "audio not found"})
                amime = {".wav": "audio/wav", ".ogg": "audio/ogg", ".mp3": "audio/mpeg"}
                return self._send(200, target_aud.read_bytes(), amime.get(target_aud.suffix.lower(), "application/octet-stream"))

            if path.startswith("/api/kit/thumbnail/"):
                from image_pipeline import find_kit_root
                rel_thumb = path[len("/api/kit/thumbnail/") :].lstrip("/")
                thumb_root = (find_kit_root() / "assets" / "thumbnails").resolve()
                target_thumb = (thumb_root / rel_thumb).resolve()
                if not target_thumb.is_relative_to(thumb_root) or not target_thumb.is_file():
                    return self._send(404, {"error": "thumbnail not found"})
                return self._send(200, target_thumb.read_bytes(), "image/png")

            if path.startswith("/api/kit/procedural/"):
                from image_pipeline import ALL_PROCEDURAL_PRESETS, encode_png_rgba, generate_procedural_rgba
                preset_stem = path[len("/api/kit/procedural/") :].lstrip("/")
                if preset_stem.endswith(".png"):
                    preset_stem = preset_stem[:-4]
                if preset_stem not in ALL_PROCEDURAL_PRESETS:
                    return self._send(404, {"error": f"unknown procedural preset: {preset_stem}"})
                w, h, rgba = generate_procedural_rgba(preset_stem)
                return self._send(200, encode_png_rgba(w, h, rgba), "image/png")

            if path.startswith("/api/kit/synth-audio/"):
                from image_pipeline import AUDIO_SYNTH_PRESETS, synthesize_wav_bytes
                preset_stem = path[len("/api/kit/synth-audio/") :].lstrip("/")
                if preset_stem.endswith(".wav"):
                    preset_stem = preset_stem[:-4]
                if preset_stem not in AUDIO_SYNTH_PRESETS:
                    return self._send(404, {"error": f"unknown audio synth preset: {preset_stem}"})
                return self._send(200, synthesize_wav_bytes(preset=preset_stem), "audio/wav")

            if path == "/api/kit/catalog":
                from image_pipeline import AUDIO_SYNTH_PRESETS
                from mcp_core import ATMOSPHERE_FX_PRESETS, MECHANIC_BUNDLES, browse_kit_helper
                cat = (qs.get("category") or ["all"])[0]
                item_q = (qs.get("item") or [None])[0]
                fmt = (qs.get("format") or ["full"])[0]
                sec = (qs.get("section") or [None])[0]
                cur = self._int_qs(qs, "cursor", 0, 0, 100000)
                lim = self._int_qs(qs, "limit", 120, 1, 500)
                try:
                    data = browse_kit_helper(
                        category=cat,
                        item=item_q,
                        output_format=fmt,
                        section=sec,
                        cursor=cur,
                        limit=lim,
                    )
                    if not item_q and not sec:
                        data["audioSynthSpecs"] = [
                            {"name": k, **v, "previewUrl": f"/api/kit/synth-audio/{k}.wav"}
                            for k, v in AUDIO_SYNTH_PRESETS.items()
                        ]
                        data["mechanicBundles"] = [
                            {"name": k, **v}
                            for k, v in MECHANIC_BUNDLES.items()
                        ]
                        data["atmospherePresets"] = list(ATMOSPHERE_FX_PRESETS.keys())
                    return self._send(200, data)
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})

            if path == "/api/kit/assets":
                from mcp_core import search_assets_helper
                q = (qs.get("query") or [""])[0]
                atype = (qs.get("assetType") or ["any"])[0]
                cat = (qs.get("category") or [None])[0]
                src_id = (qs.get("sourceId") or [None])[0]
                lic = (qs.get("license") or [None])[0]
                prov = (qs.get("provenance") or ["any"])[0]
                cur = self._int_qs(qs, "cursor", 0, 0, 100000)
                lim = self._int_qs(qs, "limit", 60, 1, 200)
                return self._send(
                    200,
                    search_assets_helper(
                        query=q,
                        asset_type=atype,
                        category=cat,
                        source_id=src_id,
                        license_id=lic,
                        provenance=prov,
                        cursor=cur,
                        limit=lim,
                    ),
                )

            if path == "/api/kit/cloud-icons":
                from mcp_core import search_cloud_icons_helper
                q = (qs.get("query") or [""])[0]
                pack = (qs.get("pack") or ["all"])[0]
                cat = (qs.get("category") or [None])[0]
                var = (qs.get("variant") or ["any"])[0]
                lim = self._int_qs(qs, "limit", 60, 1, 250)
                return self._send(
                    200,
                    search_cloud_icons_helper(
                        query=q,
                        pack=pack,
                        category=cat,
                        variant=var,
                        limit=lim,
                    ),
                )

            if path == "/api/kit/viewport-preview":
                import base64 as _b64_vp
                from mcp_core import (
                    audit_3d_scene_helper,
                    build_offline_3d_showcase_scene,
                    capture_viewport_helper,
                )
                from scene_auditor import audit_3d_hierarchy
                from viewport_renderer import render_scene_png

                src = (qs.get("source") or ["studio:Workspace"])[0]
                preset = (qs.get("preset") or ["perspective"])[0]
                inc_hud = (qs.get("includeHud") or ["0"])[0] in ("1", "true", "yes")
                show_labels = (qs.get("showLabels") or ["1"])[0] not in ("0", "false", "no")
                show_grid = (qs.get("showGrid") or ["1"])[0] not in ("0", "false", "no")
                w = self._int_qs(qs, "width", 680, 240, 1280)
                h = self._int_qs(qs, "height", 360, 160, 800)
                seed = self._int_qs(qs, "seed", 2026, 1, 999999)
                theme = (qs.get("theme") or ["horror_mansion"])[0]
                try:
                    yaw = float((qs.get("yaw") or ["0"])[0] or 0.0)
                    pitch = float((qs.get("pitch") or ["0"])[0] or 0.0)
                    zoom = float((qs.get("zoom") or ["1"])[0] or 1.0)
                except (TypeError, ValueError):
                    yaw, pitch, zoom = 0.0, 0.0, 1.0

                if src.startswith("studio:"):
                    studio_path = src.split(":", 1)[1].strip() or "Workspace"
                    with state.lock:
                        studio_online, _ = state.legacy_studio_summary_unlocked()
                    if studio_online:
                        try:
                            vp = capture_viewport_helper(
                                state.mcp_client,
                                {
                                    "path": studio_path,
                                    "preset": preset,
                                    "includeHud": inc_hud,
                                    "showLabels": show_labels,
                                    "overlays": {"grid": show_grid, "labels": show_labels},
                                    "width": w,
                                    "height": h,
                                    "yaw": yaw,
                                    "pitch": pitch,
                                    "zoom": zoom,
                                    "returnInline": True,
                                },
                            )
                            b64 = vp.pop("_mcp_image_png_b64", "")
                            audit_rep = audit_3d_scene_helper(state.mcp_client, {"path": studio_path})
                            return self._send(
                                200,
                                {
                                    "ok": True,
                                    "mode": "live_studio",
                                    "isFallback": False,
                                    "source": src,
                                    "sourceLabel": f"Live Studio ({studio_path})",
                                    "imageBase64": b64,
                                    "meta": vp,
                                    "audit": audit_rep,
                                },
                            )
                        except Exception:
                            pass
                    is_fallback = True
                    eff_src = "showcase:starter_village"
                else:
                    is_fallback = False
                    eff_src = src

                ws_tree, gui_tree, l_props, label = build_offline_3d_showcase_scene(
                    source=eff_src,
                    seed=seed,
                    theme=theme,
                )
                png_bytes, meta = render_scene_png(
                    workspace_tree=ws_tree,
                    gui_tree=gui_tree,
                    lighting_props=l_props,
                    width=w,
                    height=h,
                    camera_preset=preset,
                    include_hud=inc_hud,
                    show_labels=show_labels,
                    show_grid=show_grid,
                    yaw_offset_deg=yaw,
                    pitch_offset_deg=pitch,
                    zoom=zoom,
                )
                audit_rep = audit_3d_hierarchy(ws_tree)
                audit_rep["path"] = eff_src
                return self._send(
                    200,
                    {
                        "ok": True,
                        "mode": "offline_fallback" if is_fallback else "offline_showcase",
                        "isFallback": is_fallback,
                        "source": eff_src,
                        "sourceLabel": f"{label} (Offline Preview — connect Studio for live Workspace)" if is_fallback else label,
                        "imageBase64": _b64_vp.b64encode(png_bytes).decode("ascii"),
                        "meta": meta,
                        "audit": audit_rep,
                    },
                )

            # Loopback-only bootstrap so localhost:8077 auto-fills token, paths & tools safely
            if path == "/api/local-boot":
                is_local = self._is_local_convenience_request()
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
                        "version": VERSION,
                        "toolCount": len(TOOL_SPECS),
                        "authRoles": ["read", "write", "admin"],
                        "tools": tools_lite,
                    },
                )

            if path == "/mcp":
                if not self._check_origin():
                    return
                ctx = self._require(qs, "read")
                if not ctx:
                    return
                header_version = (self.headers.get("MCP-Protocol-Version") or "").strip()
                accept = (self.headers.get("Accept") or "").lower()
                wants_sse = (
                    ("text/event-stream" in accept and "application/json" not in accept)
                    or (qs.get("stream") or ["0"])[0] in ("1", "true", "yes")
                )
                if header_version == MODERN_PROTOCOL_VERSION and wants_sse:
                    # SEP-2575: 2026-07-28 removed GET /mcp SSE; use POST subscriptions/listen.
                    return self._send(
                        405,
                        {"error": "GET /mcp SSE stream was removed in 2026-07-28; use POST /mcp with subscriptions/listen."},
                        extra_headers={"Allow": "POST"},
                    )
                if wants_sse:
                    try:
                        max_sec = float((qs.get("maxSeconds") or ["60"])[0] or 60)
                    except (TypeError, ValueError):
                        max_sec = 60.0
                    ack = state.mcp_dispatcher.subscribe(
                        {"types": ["toolsListChanged", "resourceSubscriptions"], "maxSeconds": max_sec},
                        {"principal": f"token:{ctx.get('id') or 'unknown'}"},
                    )
                    stream = state.mcp_dispatcher_take_stream({"id": None, "result": ack})
                    if stream:
                        stream["suppressAck"] = True
                        return self._stream_mcp_subscription(stream, header_version)
                return self._send(
                    200,
                    {
                        "name": "arena-roblox-studio-mcp",
                        "version": VERSION,
                        "transport": "streamable-http",
                        "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                        "tools": len(TOOL_SPECS),
                        "readOnly": state.read_only,
                        "role": ctx["role"],
                    },
                    extra_headers=self._mcp_headers(header_version),
                )

            # Public, unauthenticated liveness probe used by the tunnel supervisor
            # and uptime monitors. Contains no place data or secrets.
            if path == "/api/health":
                return self._send(200, state.direct_dispatch("health", {}))

            if path == "/api/state":
                if not self._require(qs, "read"):
                    return
                return self._send(200, state.direct_dispatch("state", {}))

            if path == "/api/self-test":
                ctx = self._require(qs, "read")
                if not ctx:
                    return
                # A read token or Inspector must never initiate the live write/delete cycle.
                smoke_allowed = not state.read_only and ctx.get("role") in ("write", "admin")
                return self._send(200, run_bridge_self_test(state.mcp_client, include_studio_smoke=smoke_allowed, timeout=180))

            if path == "/api/agent-guide":
                if not self._require(qs, "read", allow_loopback=True):
                    return
                return self._send(
                    200,
                    build_agent_guide(state.direct_dispatch("state", {}), origin=self._request_origin()),
                )

            if path == "/api/peers":
                if not self._require(qs, "read"):
                    return
                return self._send(200, state.direct_dispatch("peers", {}))

            if path == "/api/studios":
                if not self._require(qs, "read"):
                    return
                return self._send(200, state.fleet_snapshot())

            # --- v5 observability & collaboration --------------------------------
            if path == "/api/ops":
                if not self._require(qs, "read"):
                    return
                return self._send(200, state.ops_report())

            if path == "/api/metrics":
                if not self._require(qs, "read"):
                    return
                fmt = (qs.get("format") or ["json"])[0].lower()
                if fmt in ("prometheus", "prom", "text"):
                    body = state.telemetry.metrics.prometheus()
                    return self._send(200, body, "text/plain; version=0.0.4; charset=utf-8")
                return self._send(200, state.metrics_snapshot())

            if path == "/api/events":
                if not self._require(qs, "read"):
                    return
                if (qs.get("once") or qs.get("snapshot") or ["0"])[0] in ("1", "true", "yes"):
                    cursor = self._int_qs(qs, "since", 0) or self._int_qs(qs, "cursor", 0)
                    limit = self._int_qs(qs, "limit", 100, 1, 1000)
                    return self._send(200, state.telemetry.events.since(cursor, limit, (qs.get("kinds") or [None])[0]))
                return self._stream_events(qs)

            if path == "/api/audit":
                if not self._require(qs, "read"):
                    return
                if (qs.get("verify") or ["0"])[0] in ("1", "true", "yes"):
                    return self._send(200, state.telemetry.audit.verify())
                limit = self._int_qs(qs, "limit", 100, 1, 2000)
                return self._send(200, state.telemetry.audit.tail(limit, (qs.get("action") or [None])[0]))

            if path == "/api/bus":
                payload = {k: v[0] for k, v in qs.items() if k != "token"}
                payload.setdefault("action", "stats")
                read_only = is_bus_action_read_only(payload)
                if not self._require(qs, "read" if read_only else "write"):
                    return
                if state.read_only and not read_only:
                    return self._send(403, {"error": "Blocked by Read-Only Inspector mode"})
                try:
                    return self._send(200, state.bus.handle(payload))
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})

            if path == "/api/tunnel":
                if not self._require(qs, "read"):
                    return
                return self._send(200, state.tunnel.status())

            if path == "/api/confirmations":
                if not self._require(qs, "read"):
                    return
                return self._send(200, state.confirmations_report({"action": "status"}))

            if path == "/api/subscriptions":
                if not self._require(qs, "read"):
                    return
                return self._send(200, state.mcp_dispatcher.subscription_status())

            if path == "/api/tokens":
                if not self._require(qs, "admin"):
                    return
                return self._send(200, state.tokens.list())

            if path == "/api/snapshots":
                if not self._require(qs, "read"):
                    return
                return self._send(200, state.handle_snapshots({"action": "list"}))

            if path.startswith("/api/snapshots/"):
                if not self._require(qs, "read"):
                    return
                sid = path.rsplit("/", 1)[-1]
                try:
                    return self._send(200, state.handle_snapshots({"action": "get", "id": sid}))
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})

            if path == "/api/plugin-status":
                if not self._require(qs, "read", allow_loopback=True):
                    return
                from mcp_core import manage_plugin_and_playtest_helper
                return self._send(200, manage_plugin_and_playtest_helper(state.mcp_client, {"action": "status"}))

            if path == "/api/plugin/download":
                if not self._require(qs, "read", allow_loopback=True):
                    return
                import re as _re_dl
                plugin_src = (HERE / "ArenaBridge.lua").read_text(encoding="utf-8")
                target_url = state.tunnel.public_url or f"http://127.0.0.1:{self.server.server_port}"
                plugin_src = _re_dl.sub(
                    r'local BRIDGE_URL\s*=\s*"[^"]*"',
                    f'local BRIDGE_URL = "{target_url}"',
                    plugin_src,
                )
                plugin_src = _re_dl.sub(
                    r'local BRIDGE_TOKEN\s*=\s*"[^"]*"',
                    f'local BRIDGE_TOKEN = "{state.token}"',
                    plugin_src,
                )
                return self._send(
                    200,
                    plugin_src,
                    "text/plain; charset=utf-8",
                    extra_headers={"Content-Disposition": 'attachment; filename="ArenaBridge.lua"'},
                )

            # Poll registers a peer and claims jobs; it is not a passive read.
            if path == "/api/poll":
                if not self._require(qs, "write"):
                    return
                peer_info = state.record_poll(qs)
                if peer_info.get("goodbye"):
                    return self._send(200, {"ok": True, "goodbye": True, "job": None, "paused": state.queue_paused})
                try:
                    wait_sec = max(0.0, min(float((qs.get("wait") or ["8.0"])[0] or 8.0), 15.0))
                except (TypeError, ValueError):
                    wait_sec = 8.0
                job = state.poll_for_job(peer_info, wait_seconds=wait_sec) if wait_sec > 0 else None
                return self._send(200, {"job": job, "paused": state.queue_paused})

            if path.startswith("/api/jobs/"):
                if not self._require(qs, "read", allow_loopback=True):
                    return
                job_id = path.rsplit("/", 1)[-1]
                with state.lock:
                    job = state.jobs.get(job_id)
                if not job:
                    return self._send(404, {"error": "no such job"})
                return self._send(200, job)

            if path == "/api/jobs":
                if not self._require(qs, "read", allow_loopback=True):
                    return
                with state.lock:
                    return self._send(200, {"jobs": [summary(state.jobs[i]) for i in state.history]})

            return self._send(404, {"error": "not found"})

        def _stream_mcp_subscription(self, stream: dict, protocol_version: str = None):
            """Hold this response open and forward MCP notifications as SSE frames."""
            sub_id = stream.get("subscriptionId")
            max_seconds = stream.get("maxSeconds")
            poll_seconds = stream.get("pollSeconds") or 0.75
            try:
                max_seconds = float(max_seconds) if max_seconds is not None else None
            except (TypeError, ValueError):
                max_seconds = None
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", self._cors_origin())
            if (protocol_version or "") == MODERN_PROTOCOL_VERSION:
                self.send_header("MCP-Protocol-Version", MODERN_PROTOCOL_VERSION)
            else:
                self.send_header("Mcp-Session-Id", state.session_id)
            if sub_id:
                self.send_header("X-Arena-Subscription", str(sub_id))
            self.end_headers()

            stop = threading.Event()
            write_lock = threading.Lock()

            def _raw_write(text: str):
                with write_lock:
                    self.wfile.write(text.encode("utf-8"))
                    self.wfile.flush()

            def _write(payload):
                frame = json.dumps(payload, ensure_ascii=False, default=str)
                _raw_write(f"event: message\ndata: {frame}\n\n")

            try:
                # Emit immediate comment bytes so Cloudflare Tunnel flushes 200
                # headers right away rather than holding until the first event.
                _raw_write(": connected\n\n")
                if not stream.get("suppressAck") and stream.get("ack") is not None:
                    _write(stream["ack"])
                state.mcp_dispatcher.pump_subscription(
                    sub_id,
                    _write,
                    stop_event=stop,
                    max_seconds=max_seconds,
                    poll_seconds=poll_seconds,
                    keepalive=lambda: _raw_write(": keepalive\n\n"),
                    keepalive_seconds=15.0,
                )
            except (BrokenPipeError, ConnectionResetError, OSError):
                stop.set()
                if sub_id:
                    state.mcp_dispatcher.drop_subscription(sub_id)
            except Exception:  # noqa: BLE001 - never leak a traceback into the socket
                stop.set()

        def do_POST(self):
            if not self._check_origin():
                return
            url = urlparse(self.path)
            qs = parse_qs(url.query)
            path = url.path.rstrip("/") or "/"

            # Native MCP Streamable HTTP endpoint (JSON-RPC 2.0). The token role
            # determines whether mutating tools are advertised/callable.
            if path == "/api/mcp/events":
                if not self._require(qs, "admin"):
                    return
                try:
                    body = self._body()
                except BodyLimitError as exc:
                    return self._send(exc.status_code, {"error": exc.message})
                return self._send(200, state.publish_mcp_event(
                    body.get("kind"), body.get("detail"), body.get("level") or "info"))

            if path == "/api/confirmations":
                if not self._require(qs, "write"):
                    return
                try:
                    report = state.confirmations_report(self._body())
                except BodyLimitError as exc:
                    return self._send(exc.status_code, {"error": exc.message})
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                return self._send(200, report)

            if path == "/api/subscriptions/close":
                if not self._require(qs, "admin"):
                    return
                try:
                    sub_id = (self._body() or {}).get("subscriptionId")
                except BodyLimitError as exc:
                    return self._send(exc.status_code, {"error": exc.message})
                return self._send(200, state.mcp_dispatcher.drop_subscription(sub_id))

            if path == "/mcp":
                ctx = self._require(qs, "read")
                if not ctx:
                    return
                header_version = (self.headers.get("MCP-Protocol-Version") or "").strip()
                raw_header_method = self.headers.get("Mcp-Method")
                raw_header_name = self.headers.get("Mcp-Name")
                try:
                    raw = self._read_body_raw()
                except BodyLimitError as exc:
                    return self._send(
                        exc.status_code,
                        {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": exc.message}},
                        extra_headers=self._mcp_headers(header_version),
                    )
                try:
                    rpc_in = json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    return self._send(
                        400,
                        {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}},
                        extra_headers=self._mcp_headers(header_version),
                    )

                req_id = rpc_in.get("id") if isinstance(rpc_in, dict) else None
                try:
                    header_method = decode_mcp_header_value(raw_header_method) if raw_header_method is not None else ""
                    header_name = decode_mcp_header_value(raw_header_name) if raw_header_name is not None else ""
                except ValueError as exc:
                    return self._send(
                        400,
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32020, "message": str(exc)},
                        },
                        extra_headers=self._mcp_headers(header_version),
                    )

                if raw_header_method is not None and not header_method:
                    return self._send(
                        400,
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {"code": -32020, "message": "Mcp-Method header must not be empty"},
                        },
                        extra_headers=self._mcp_headers(header_version),
                    )

                # Dual-era MCP over Streamable HTTP (SEP-2575 / SEP-2243):
                #  * MCP-Protocol-Version must agree with _meta protocolVersion when both are present (-32020)
                #  * Mcp-Method / Mcp-Name (including =?base64?...?= sentinels) must agree with body (-32020)
                if isinstance(rpc_in, dict):
                    params_obj = rpc_in.get("params") if isinstance(rpc_in.get("params"), dict) else {}
                    top_meta = rpc_in.get("_meta") if isinstance(rpc_in.get("_meta"), dict) else {}
                    nested_meta = params_obj.get("_meta") if isinstance(params_obj.get("_meta"), dict) else {}
                    body_version = top_meta.get(META_PROTOCOL_VERSION) or nested_meta.get(META_PROTOCOL_VERSION)
                    if header_version and body_version and header_version != body_version:
                        return self._send(
                            400,
                            {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "error": {
                                    "code": -32020,
                                    "message": "MCP-Protocol-Version header does not match _meta protocolVersion",
                                    "data": {"header": header_version, "body": body_version},
                                },
                            },
                            extra_headers=self._mcp_headers(header_version),
                        )
                    if header_version:
                        meta = rpc_in.get("_meta")
                        if not isinstance(meta, dict):
                            meta = {}
                            rpc_in["_meta"] = meta
                        meta.setdefault(META_PROTOCOL_VERSION, header_version)
                    body_method = (rpc_in.get("method") or "").strip()
                    if header_method and body_method != header_method:
                        return self._send(
                            400,
                            {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "error": {
                                    "code": -32020,
                                    "message": "Mcp-Method header does not match the JSON-RPC method",
                                    "data": {"header": header_method, "body": body_method},
                                },
                            },
                            extra_headers=self._mcp_headers(header_version),
                        )
                    if raw_header_name is not None and body_method in ("tools/call", "resources/read", "prompts/get"):
                        body_name = str(params_obj.get("name") or params_obj.get("uri") or "")
                        if not header_name or (body_name and header_name != body_name):
                            return self._send(
                                400,
                                {
                                    "jsonrpc": "2.0",
                                    "id": req_id,
                                    "error": {
                                        "code": -32020,
                                        "message": "Mcp-Name header does not match the request name",
                                        "data": {"header": header_name, "body": body_name},
                                    },
                                },
                                extra_headers=self._mcp_headers(header_version),
                            )

                # `_meta` is authoritative in the 2026-07-28 era: a client that
                # negotiates per request (without an MCP-Protocol-Version header)
                # still gets era-correct response headers.
                negotiated_version = header_version
                if isinstance(rpc_in, dict):
                    params_obj = rpc_in.get("params") if isinstance(rpc_in.get("params"), dict) else {}
                    nested_meta = params_obj.get("_meta") if isinstance(params_obj.get("_meta"), dict) else {}
                    top_meta = rpc_in.get("_meta") if isinstance(rpc_in.get("_meta"), dict) else {}
                    negotiated_version = (
                        top_meta.get(META_PROTOCOL_VERSION)
                        or nested_meta.get(META_PROTOCOL_VERSION)
                        or header_version
                    )

                # Authentication, not client-provided metadata or a non-unique
                # label, determines the caller for every request in a batch.
                requests = rpc_in if isinstance(rpc_in, list) else [rpc_in]
                for request in requests:
                    if not isinstance(request, dict):
                        continue
                    meta = request.get("_meta")
                    if not isinstance(meta, dict):
                        meta = {}
                        request["_meta"] = meta
                    meta["x-arena-principal"] = f"token:{ctx.get('id') or 'unknown'}"
                    if header_version:
                        meta.setdefault(META_PROTOCOL_VERSION, header_version)

                rpc_in, scope_error = self._enforce_mcp_scope(rpc_in, ctx)
                if scope_error is not None:
                    return self._send(200, scope_error, extra_headers=self._mcp_headers(negotiated_version))
                rpc_out = state.mcp_dispatcher.handle_payload(rpc_in)
                if rpc_out is None:
                    return self._send(202, b"", "text/plain; charset=utf-8",
                                     self._mcp_headers(negotiated_version))

                # SEP-2575: Unsupported protocol version (-32022) returns HTTP 400 on Streamable HTTP.
                if (
                    isinstance(rpc_out, dict)
                    and isinstance(rpc_out.get("error"), dict)
                    and rpc_out["error"].get("code") == -32022
                ):
                    return self._send(400, rpc_out, extra_headers=self._mcp_headers(negotiated_version))

                # subscriptions/listen answers on a long-lived response stream: the
                # acknowledgement goes out first, then one SSE frame per notification.
                stream = state.mcp_dispatcher_take_stream(rpc_out)
                if stream:
                    return self._stream_mcp_subscription(stream, negotiated_version)
                extra = self._mcp_headers(negotiated_version)
                if stream is None and isinstance(rpc_out, dict) and (rpc_out.get("result") or {}).get("taskId"):
                    # A task handle is a promise to poll, so tell the client where.
                    extra["X-Arena-Task"] = str(rpc_out["result"]["taskId"])
                return self._send(200, rpc_out, extra_headers=extra)

            try:
                body = self._body()
            except BodyLimitError as exc:
                return self._send(exc.status_code, {"error": exc.message})

            # Enqueue job for Studio (requires write scope; admin when read-only
            # inspector mode is active, since that setting is a safety control).
            if path == "/api/jobs":
                ctx = self._require(qs, "write")
                if not ctx:
                    return
                kind = body.get("type")
                if not kind:
                    return self._send(400, {"error": "type is required"})
                try:
                    job, evt = state.enqueue_job(
                        kind=kind,
                        payload=body.get("payload"),
                        note=body.get("note") or f"api:{ctx['label']}",
                        dm=body.get("dm"),
                        peer_id=body.get("peer_id"),
                        place_id=body.get("place_id"),
                        idempotency_key=body.get("idempotencyKey") or self.headers.get("Idempotency-Key"),
                        priority=body.get("priority", 5),
                        max_retries=body.get("maxRetries", 0),
                        timeout=body.get("timeout"),
                    )
                except PermissionError as exc:
                    return self._send(403, {"error": str(exc)})

                wait = float(body.get("wait") or 0)
                if wait > 0:
                    evt.wait(timeout=min(wait, 120.0))
                    with state.lock:
                        return self._send(200, state.jobs[job["id"]])
                return self._send(201, {
                    "id": job["id"],
                    "status": job["status"],
                    "priority": job.get("priority"),
                    "deduplicated": job.get("deduplicated", 0),
                })

            if path.startswith("/api/jobs/") and path.endswith("/cancel"):
                if not self._require(qs, "write"):
                    return
                parts = path.split("/")
                job_id = parts[3] if len(parts) >= 5 else ""
                try:
                    return self._send(200, state.cancel_job(job_id))
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})

            if path == "/api/queue/clear":
                ctx = self._require(qs, "admin")
                if not ctx:
                    return
                cancelled = state.clear_queue()
                state.telemetry.audit.append("queue.clear", actor=ctx["label"], detail={"cancelled": cancelled})
                return self._send(200, {"cancelled": cancelled})

            if path in ("/api/queue/pause", "/api/queue/resume"):
                ctx = self._require(qs, "admin")
                if not ctx:
                    return
                if path.endswith("pause"):
                    return self._send(200, state.pause_queue(body.get("reason")))
                return self._send(200, state.resume_queue())

            if path == "/api/snapshots":
                if not self._require(qs, "write"):
                    return
                try:
                    return self._send(200, state.handle_snapshots(body))
                except PermissionError as exc:
                    return self._send(403, {"error": str(exc)})
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})

            # --- v5 collaboration & ops control ---------------------------------
            if path == "/api/bus":
                action = (body.get("action") or "stats")
                read_only = is_bus_action_read_only(body)
                ctx = self._require(qs, "read" if read_only else "write")
                if not ctx:
                    return
                if state.read_only and not read_only:
                    return self._send(403, {"error": "Blocked by Read-Only Inspector mode"})
                try:
                    result = state.bus.handle(body)
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                state.telemetry.metrics.inc("bridge_bus_actions_total", 1, {"action": action})
                return self._send(200, result)

            if path == "/api/studios":
                action = (body.get("action") or "list").lower()
                ctx = self._require(qs, "read" if action in ("list", "status") else "write")
                if not ctx:
                    return
                try:
                    if action == "broadcast":
                        return self._send(200, state.broadcast(
                            kind=body.get("type") or body.get("kind") or "ping",
                            payload=body.get("payload") if body.get("payload") else {"path": body["path"]} if body.get("path") else None,
                            timeout=float(body.get("timeout") or 20.0),
                            dm=body.get("dm"),
                            tool=body.get("tool"),
                        ))
                    return self._send(200, state.fleet_action(body))
                except PermissionError as exc:
                    return self._send(403, {"error": str(exc)})
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})

            if path == "/api/ops":
                action = (body.get("action") or "status").lower()
                required = "read" if action in ("status", "report") else "admin"
                ctx = self._require(qs, required)
                if not ctx:
                    return
                if action in ("status", "report"):
                    return self._send(200, state.ops_report())
                try:
                    result = state.direct_dispatch(action, body)
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                state.telemetry.audit.append(f"ops.{action}", actor=ctx["label"], outcome="ok")
                return self._send(200, result)

            if path == "/api/tunnel":
                action = (body.get("action") or "status").lower()
                required = "admin" if action in ("start", "stop", "restart") else "read"
                ctx = self._require(qs, required)
                if not ctx:
                    return
                try:
                    result = state.tunnel_action(body)
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                if action in ("start", "stop", "restart"):
                    state.telemetry.audit.append(f"tunnel.{action}", actor=ctx["label"], outcome="ok")
                    state.telemetry.events.emit("tunnel.action", {"action": action, "actor": ctx["label"]})
                return self._send(200, result)

            if path == "/api/tokens":
                ctx = self._require(qs, "admin")
                if not ctx:
                    return
                action = (body.get("action") or "list")
                try:
                    result = state.tokens.handle(body)
                except KeyError as exc:
                    return self._send(404, {"error": str(exc)})
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})
                if action in ("create", "revoke"):
                    state.telemetry.audit.append(
                        f"token.{action}", actor=ctx["label"],
                        target=result.get("id") or body.get("id") or "",
                        detail={"role": result.get("role")} if action == "create" else None,
                    )
                return self._send(200, result)

            if path == "/api/playtest/force-stop":
                if not self._require(qs, "write"):
                    return
                if (body or {}).get("action") == "evict_only":
                    return self._send(200, state.evict_playtest_peers(reason=(body or {}).get("reason") or "api_evict"))
                from mcp_core import manage_plugin_and_playtest_helper
                return self._send(200, manage_plugin_and_playtest_helper(state.mcp_client, {
                    "action": "force_stop_playtest",
                    "reason": (body or {}).get("reason") or "api_force_stop",
                }))

            if path == "/api/plugin-status":
                action = ((body or {}).get("action") or "status").lower()
                req_role = "read" if action in ("status", "version_matrix") else "write"
                if not self._require(qs, req_role):
                    return
                from mcp_core import manage_plugin_and_playtest_helper
                return self._send(200, manage_plugin_and_playtest_helper(state.mcp_client, body or {"action": "status"}))

            # Studio plugin posts completed job result
            if path == "/api/result":
                if not self._require(qs, "write"):
                    return
                if not state.complete_job(body):
                    return self._send(404, {"error": "no such job"})
                return self._send(200, {"ack": True})

            return self._send(404, {"error": "not found"})

        def _enforce_mcp_scope(self, rpc_in, ctx):
            """Block mutating MCP tool calls for read-scoped tokens (batch aware)."""
            if ctx.get("role") == "admin" or ctx.get("id") in ("open", "loopback"):
                return rpc_in, None

            def blocked(item):
                if not isinstance(item, dict) or item.get("method") != "tools/call":
                    return None
                params = item.get("params") or {}
                if not isinstance(params, dict):
                    return None  # Let the dispatcher report malformed requests.
                name = params.get("name")
                args = params.get("arguments") or {}
                if not isinstance(name, str) or not isinstance(args, dict):
                    return None
                spec = TOOL_MAP.get(name)
                if not spec:
                    return None
                if is_tool_call_read_only(name, args):
                    return None
                # Operator controls are admin-only on REST and remain so on MCP.
                if ctx.get("role") == "write" and name != "bridge_ops":
                    return None
                required = "admin" if name == "bridge_ops" else "write"
                message = (
                    f"Blocked by token scope: token '{ctx.get('label')}' has role "
                    f"'{ctx.get('role')}', which cannot call mutating tool '{name}'. "
                    f"Requires {required} scope."
                )
                return {
                    "jsonrpc": "2.0",
                    "id": item.get("id"),
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": message}],
                        "structuredContent": {"ok": False, "reason": "insufficient_scope", "tool": name},
                    },
                }

            if isinstance(rpc_in, list):
                for item in rpc_in:
                    err = blocked(item)
                    if err is not None:
                        return rpc_in, err
                return rpc_in, None
            err = blocked(rpc_in)
            if err is not None:
                return rpc_in, err
            return rpc_in, None

    return Handler


# ----------------------------------------------------------------------------
# Self-Contained Control Center & Next-Agent Handoff Hub (GET /)
# ----------------------------------------------------------------------------

def dashboard_html() -> str:
    html = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Roblox Studio MCP, 3D Viewport Vision &amp; Open-Source Kit (v__BRIDGE_VERSION__)</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root{color-scheme:dark;--bg:#070b12;--bg2:#0a1018;--panel:rgba(19,25,34,.82);--panel2:#0d131c;
       --border:#22303f;--border2:#2c3f55;--txt:#e8eff7;--muted:#8fa0b4;--accent:#5cc8ff;
       --accent2:#a78bfa;--green:#45e0a0;--red:#ff6b6b;--amber:#ffc857;--radius:14px;
       --shadow:0 18px 40px -24px rgba(0,0,0,.9)}
 *{box-sizing:border-box}
 html{scroll-behavior:smooth}
 body{margin:0;background:var(--bg);color:var(--txt);
      font:13.5px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
      -webkit-font-smoothing:antialiased;position:relative;min-height:100vh}
 body::before{content:"";position:fixed;inset:-30% -10% auto -10%;height:75vh;z-index:-1;pointer-events:none;
      background:
        radial-gradient(46% 60% at 18% 20%,rgba(92,200,255,.16),transparent 70%),
        radial-gradient(40% 55% at 78% 10%,rgba(167,139,250,.16),transparent 70%),
        radial-gradient(52% 60% at 55% 78%,rgba(69,224,160,.09),transparent 72%);
      filter:blur(6px);animation:aura 22s ease-in-out infinite alternate}
 body::after{content:"";position:fixed;inset:0;z-index:-2;pointer-events:none;opacity:.35;
      background-image:linear-gradient(rgba(255,255,255,.028) 1px,transparent 1px),
                       linear-gradient(90deg,rgba(255,255,255,.028) 1px,transparent 1px);
      background-size:46px 46px;
      -webkit-mask-image:radial-gradient(ellipse at 50% 0%,#000 20%,transparent 78%);
      mask-image:radial-gradient(ellipse at 50% 0%,#000 20%,transparent 78%)}
 @keyframes aura{0%{transform:translate3d(-2%,0,0) scale(1)}50%{transform:translate3d(2%,2%,0) scale(1.06)}100%{transform:translate3d(-1%,3%,0) scale(1.02)}}
 ::selection{background:rgba(92,200,255,.28);color:#fff}
 *::-webkit-scrollbar{width:10px;height:10px}
 *::-webkit-scrollbar-track{background:transparent}
 *::-webkit-scrollbar-thumb{background:#1e2a38;border-radius:99px;border:2px solid transparent;background-clip:content-box}
 *::-webkit-scrollbar-thumb:hover{background:#2c3f55;background-clip:content-box}
 code,pre,textarea,input,select{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
 header{padding:16px 26px;background:linear-gradient(180deg,rgba(12,18,26,.94),rgba(12,18,26,.72));
        -webkit-backdrop-filter:blur(14px) saturate(140%);backdrop-filter:blur(14px) saturate(140%);
        border-bottom:1px solid var(--border);display:flex;align-items:center;gap:12px;flex-wrap:wrap;
        position:sticky;top:0;z-index:30}
 header::after{content:"";position:absolute;left:0;right:0;bottom:-1px;height:1px;
        background:linear-gradient(90deg,transparent,rgba(92,200,255,.55),rgba(167,139,250,.55),transparent);
        opacity:.7}
 h1{font-size:16.5px;font-weight:750;margin:0;letter-spacing:.2px;display:flex;align-items:center;gap:9px}
 h1 .brand{background:linear-gradient(92deg,#e8eff7 10%,#9ddcff 55%,#c3b3ff 95%);
        -webkit-background-clip:text;background-clip:text;color:transparent}
 h1 .logo{width:22px;height:22px;border-radius:7px;flex:0 0 auto;
        background:conic-gradient(from 210deg,#5cc8ff,#a78bfa,#45e0a0,#5cc8ff);
        box-shadow:0 0 18px rgba(92,200,255,.42);animation:spin 14s linear infinite}
 @keyframes spin{to{transform:rotate(360deg)}}
 h2{font-size:15px;font-weight:700;margin:0 0 8px}
 h3{font-size:13.5px;font-weight:700;margin:0 0 6px;color:#d7e3f1}
 .sub{color:var(--muted);font-size:12px}
 .spacer{flex:1}
 .pill{padding:4px 11px;border-radius:999px;font-size:11.5px;font-weight:650;border:1px solid transparent;
       display:inline-flex;align-items:center;gap:6px;transition:.18s}
 .on{background:rgba(69,224,160,.12);color:var(--green);border-color:rgba(69,224,160,.4)}
 .off{background:rgba(255,107,107,.12);color:var(--red);border-color:rgba(255,107,107,.36)}
 .ro{background:rgba(255,200,87,.12);color:var(--amber);border-color:rgba(255,200,87,.38)}
 .info-pill{background:rgba(92,200,255,.1);color:var(--accent);border-color:rgba(92,200,255,.34)}
 .pill.live::before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor;
       box-shadow:0 0 0 0 currentColor;animation:pulse 2.1s ease-out infinite}
 @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(69,224,160,.55)}70%{box-shadow:0 0 0 7px rgba(69,224,160,0)}100%{box-shadow:0 0 0 0 rgba(69,224,160,0)}}
 nav{display:flex;gap:6px;padding:10px 22px;background:rgba(10,16,24,.72);
     -webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);
     border-bottom:1px solid var(--border);overflow-x:auto;position:relative;z-index:25}
 .tab-btn{background:transparent;color:var(--muted);border:1px solid transparent;padding:7px 14px;
          border-radius:10px;cursor:pointer;font-size:12.8px;font-weight:650;white-space:nowrap;
          position:relative;transition:color .18s,background .18s,transform .18s,box-shadow .18s}
 .tab-btn:hover{color:var(--txt);background:rgba(255,255,255,.05);transform:translateY(-1px)}
 .tab-btn.active{color:#fff;background:linear-gradient(180deg,rgba(92,200,255,.2),rgba(92,200,255,.07));
          border-color:rgba(92,200,255,.38);box-shadow:0 6px 18px -12px rgba(92,200,255,.9),inset 0 1px 0 rgba(255,255,255,.08)}
 .tab-btn.active::after{content:"";position:absolute;left:14px;right:14px;bottom:-6px;height:2px;border-radius:2px;
          background:linear-gradient(90deg,transparent,var(--accent),transparent)}
 main{padding:22px 26px 60px;max-width:1300px;margin:0 auto;animation:fadeUp .4s ease both}
 @keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
 .tab-pane{display:none}
 .tab-pane.active{display:block;animation:paneIn .3s cubic-bezier(.2,.7,.3,1) both}
 @keyframes paneIn{from{opacity:0;transform:translateY(10px) scale(.996)}to{opacity:1;transform:none}}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:18px}
 .card{background:linear-gradient(180deg,rgba(24,32,44,.9),rgba(15,21,29,.9));border:1px solid var(--border);
       border-radius:var(--radius);padding:15px;position:relative;overflow:hidden;
       transition:transform .22s cubic-bezier(.2,.7,.3,1),border-color .22s,box-shadow .22s}
 .card::before{content:"";position:absolute;inset:0 0 auto 0;height:2px;opacity:0;transition:opacity .25s;
       background:linear-gradient(90deg,transparent,var(--accent),var(--accent2),transparent)}
 .card:hover{transform:translateY(-3px);border-color:var(--border2);box-shadow:var(--shadow)}
 .card:hover::before{opacity:.9}
 .card .k{font-size:10.5px;color:var(--muted);text-transform:uppercase;letter-spacing:.9px;font-weight:650}
 .card .v{font:650 20px/1.25 ui-monospace,monospace;margin-top:6px;letter-spacing:-.3px}
 .toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px;
          padding:12px;background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);
          -webkit-backdrop-filter:blur(8px);backdrop-filter:blur(8px)}
 button,.btn{background:linear-gradient(180deg,#1e2a38,#1a2330);color:var(--txt);border:1px solid #31465c;
             border-radius:9px;padding:7px 13px;font-size:12.5px;font-weight:650;cursor:pointer;
             transition:transform .14s,box-shadow .18s,background .18s,border-color .18s;
             box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}
 button:hover{background:linear-gradient(180deg,#26374b,#1e2a38);border-color:#44618a;transform:translateY(-1px);
             box-shadow:0 10px 22px -16px rgba(92,200,255,.9)}
 button:active{transform:translateY(1px) scale(.99)}
 button:focus-visible,select:focus-visible,input:focus-visible,textarea:focus-visible{
             outline:2px solid rgba(92,200,255,.7);outline-offset:2px}
 button.primary{background:linear-gradient(180deg,#2f86f6,#1f6feb);border-color:#4f9dff;color:#fff}
 button.primary:hover{background:linear-gradient(180deg,#3d95ff,#2a7bf0);box-shadow:0 12px 26px -16px rgba(56,139,253,1)}
 button.danger{background:linear-gradient(180deg,#3a181c,#2c1216);border-color:#6e2b31;color:#ff9d97}
 button.sm{padding:4px 10px;font-size:11.5px;border-radius:8px}
 button.ghost{background:transparent;border-color:var(--border)}
 input,select,textarea{background:var(--panel2);color:var(--txt);border:1px solid var(--border);
                       border-radius:9px;padding:8px 11px;font-size:12.5px;width:100%;transition:border-color .18s,box-shadow .18s}
 input:hover,select:hover,textarea:hover{border-color:var(--border2)}
 textarea{min-height:110px;resize:vertical;line-height:1.5}
 table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--border);
       border-radius:var(--radius);overflow:hidden;margin-bottom:18px}
 th{font-size:10.5px;text-transform:uppercase;letter-spacing:.9px;color:var(--muted);
    text-align:left;padding:10px 12px;background:rgba(13,19,28,.9);border-bottom:1px solid var(--border);font-weight:650}
 td{padding:9px 12px;border-bottom:1px solid rgba(27,34,46,.8);vertical-align:top;font-size:12.5px}
 tbody tr{transition:background .16s}
 tbody tr:nth-child(even) td{background:rgba(255,255,255,.012)}
 tr:last-child td{border-bottom:none}
 tr.clickable:hover td{background:rgba(92,200,255,.06);cursor:pointer}
 .s-done{color:var(--green)}.s-error,.s-cancelled{color:var(--red)}.s-running{color:var(--amber)}
 .s-queued{color:var(--muted)}
 code{background:rgba(255,255,255,.05);border:1px solid var(--border);border-radius:6px;padding:1px 6px;font-size:12px}
 pre{background:rgba(9,14,21,.9);border:1px solid var(--border);border-radius:11px;padding:12px;
     overflow:auto;margin:8px 0 0;font-size:12px;line-height:1.55;white-space:pre-wrap;word-break:break-word}
 .empty{padding:22px;text-align:center;color:var(--muted)}
 .two-col{display:grid;grid-template-columns:repeat(auto-fit,minmax(460px,1fr));gap:14px;margin-bottom:16px}
 .prompt-card{background:linear-gradient(180deg,rgba(21,29,40,.92),rgba(14,20,28,.92));
              border:1px solid var(--border);border-radius:var(--radius);padding:16px;
              display:flex;flex-direction:column;gap:9px;transition:border-color .2s,box-shadow .2s}
 .prompt-card:hover{border-color:var(--border2);box-shadow:var(--shadow)}
 .prompt-head{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
 .cmd-row{display:flex;align-items:center;gap:10px;padding:9px 11px;background:rgba(255,255,255,.03);
          border:1px solid var(--border);border-radius:10px;margin-bottom:7px;transition:border-color .16s,background .16s}
 .cmd-row:hover{border-color:var(--border2);background:rgba(92,200,255,.05)}
 .cmd-row code{background:none;border:none;padding:0;flex:1;overflow-x:auto;white-space:nowrap}
 .cmd-desc{color:var(--muted);font-size:11.5px;min-width:190px;text-align:right}
 .toast{position:fixed;bottom:22px;right:22px;background:linear-gradient(180deg,#2f86f6,#1f6feb);color:#fff;
        padding:10px 17px;border-radius:11px;font-weight:650;font-size:12.5px;opacity:0;pointer-events:none;
        transform:translateY(14px) scale(.97);transition:opacity .24s,transform .24s;z-index:99;
        box-shadow:0 18px 40px -18px rgba(31,111,235,.9)}
 .toast.show{opacity:1;transform:none}
 /* ---- connection / auth banners ---- */
 .banner{display:none;gap:11px;align-items:center;flex-wrap:wrap;padding:11px 24px;
        border-bottom:1px solid rgba(255,200,87,.35);
        background:linear-gradient(90deg,rgba(255,200,87,.16),rgba(255,107,107,.07));
        animation:slideDown .28s ease both}
 .banner.show{display:flex}
 .banner.bad{border-bottom-color:rgba(255,107,107,.45);
        background:linear-gradient(90deg,rgba(255,107,107,.16),rgba(167,139,250,.08))}
 .banner .grow{flex:1;min-width:220px}
 .banner input{max-width:330px}
 @keyframes slideDown{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}
 /* ---- multi-studio fleet ---- */
 .fleet-bar{display:flex;align-items:center;gap:10px;padding:9px 24px;background:rgba(10,16,24,.82);
        border-bottom:1px solid var(--border);overflow-x:auto;position:relative;z-index:22;
        -webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px)}
 .fleet-label{font-size:11px;letter-spacing:.9px;text-transform:uppercase;color:var(--muted);
        font-weight:700;display:flex;align-items:center;gap:7px;flex:0 0 auto}
 .dot-live{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 12px rgba(69,224,160,.9);
        animation:blink 2.4s ease-in-out infinite}
 .dot-dead{background:var(--red);box-shadow:0 0 10px rgba(255,107,107,.7);animation:none}
 @keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
 .fleet-chips{display:flex;gap:8px;align-items:center;flex:1;min-width:0}
 .studio-card{display:flex;align-items:center;gap:9px;padding:7px 11px;border-radius:11px;flex:0 0 auto;
        background:linear-gradient(180deg,rgba(24,33,45,.95),rgba(14,20,28,.95));
        border:1px solid var(--border);transition:transform .18s,border-color .18s,box-shadow .18s;
        animation:chipIn .32s cubic-bezier(.2,.7,.3,1) both}
 .studio-card:hover{transform:translateY(-2px);border-color:var(--border2);box-shadow:var(--shadow)}
 .studio-card.pinned{border-color:rgba(92,200,255,.6);
        box-shadow:0 0 0 1px rgba(92,200,255,.25),0 14px 28px -22px rgba(92,200,255,.9)}
 .studio-card.active-target{border-color:rgba(69,224,160,.55)}
 .studio-card .place{font-weight:650;font-size:12.3px;max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .studio-card .meta{font-size:10.5px;color:var(--muted);display:flex;gap:6px;align-items:center}
 .studio-card .pin{background:transparent;border:1px solid var(--border);border-radius:7px;padding:2px 7px;
        font-size:10.5px;color:var(--muted)}
 .studio-card .pin:hover{color:var(--accent);border-color:rgba(92,200,255,.55)}
 @keyframes chipIn{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}
 .badge-dm{font-size:10px;padding:1px 6px;border-radius:999px;border:1px solid var(--border);
        background:rgba(255,255,255,.04);color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
 #fleet-detail{display:flex;flex-wrap:wrap;gap:10px;min-height:34px}
 #fleet-detail .studio-card{min-width:240px}
 @media(prefers-reduced-motion:reduce){
   *,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}
 }
 @media(max-width:720px){
   .two-col{grid-template-columns:1fr}
   .cmd-desc{display:none}
   .grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
   .card .v{font-size:17px;word-break:break-all;overflow-wrap:anywhere}
   .prompt-card{padding:12px}
   header{flex-wrap:wrap;gap:6px;padding:12px 14px}
   nav{padding:8px 12px}
   .fleet-bar{padding:8px 12px}
   main{padding:14px 12px 48px}
   #tok-input{width:130px!important}
   pre{max-height:190px}
   .wrap{padding:0 10px}
   h1{font-size:15px}
 }
 @media(max-width:420px){
   .grid{grid-template-columns:repeat(2,minmax(0,1fr))}
   .card{padding:10px}
   .card .k{font-size:10px}
   h1{font-size:14.5px}
   .studio-card .place{max-width:130px}
 }
 html,body{max-width:100%;overflow-x:hidden}
 img,pre,input,textarea,select{max-width:100%}
</style></head><body>

<header>
  <h1><span class="logo" aria-hidden="true"></span><span class="brand">Roblox Studio MCP &amp; Bridge</span> <span class="sub">v__BRIDGE_VERSION__</span></h1>
  <span id="status" class="pill off live">checking&hellip;</span>
  <span id="fleet-badge" class="pill info-pill" style="display:none">0 studios</span>
  <span id="ro-badge" class="pill ro" style="display:none">Read-Only Inspector</span>
  <span id="place" class="pill info-pill" style="display:none"></span>
  <button class="sm" onclick="runSelfTest()">Run Self-Test</button>
  <div class="spacer"></div>
  <span class="sub">Token:</span>
  <input id="tok-input" type="password" placeholder="auto-loaded on localhost" onchange="applyHeaderToken()" style="width:185px;padding:4px 8px">
  <button class="sm" onclick="toggleToken()">Show</button>
</header>

<div class="fleet-bar" id="fleet-bar">
  <span class="fleet-label"><span class="dot-live" id="fleet-dot"></span> Studios</span>
  <div class="fleet-chips" id="fleet-chips"><span class="sub">Looking for connected Studio windows&hellip;</span></div>
  <button class="sm danger" onclick="forceStopPlaytestUI()" title="Force-stop any active or stuck Play Solo / Server Run session and evict ghost runtime peers">&#9209; Force Stop Playtest</button>
  <button class="sm primary" onclick="downloadPluginFile()" title="Download pre-configured ArenaBridge.lua v6.0.0">&#11015; Plugin v6.0.0</button>
  <button class="sm" onclick="broadcastPing()" title="Ping every online Studio and show per-peer latency">Broadcast ping</button>
</div>

<div id="auth-banner" class="banner">
  <div class="grow">
    <strong id="auth-title">This dashboard needs the bridge token</strong>
    <div class="sub" id="auth-msg">Every <code>/api</code> call is authenticated. Opening the console from a
      tunnel or LAN address cannot auto-load it — paste <code>bridge.token</code> (printed by setup.py).</div>
  </div>
  <input id="auth-token" type="password" placeholder="paste the token" autocomplete="off">
  <button class="primary sm" onclick="saveToken()">Use token</button>
  <button class="sm" onclick="hideAuthBanner()">Dismiss</button>
</div>

<div id="stale-banner" class="banner bad">
  <div class="grow"><strong>This tab is from an older bridge version</strong>
    <div class="sub" id="stale-msg">The bridge restarted with new code; reload to get the current UI.</div>
  </div>
  <button class="primary sm" onclick="location.reload()">Reload</button>
</div>

<nav>
  <button class="tab-btn active" onclick="showTab('monitor', this)">1. Live Monitor</button>
  <button class="tab-btn" onclick="showTab('kit', this)">2. Image Importer &amp; Studio Kit</button>
  <button class="tab-btn" onclick="showTab('prompts', this)">3. Next-Agent Handoff &amp; Prompts</button>
  <button class="tab-btn" onclick="showTab('commands', this)">4. Commands &amp; MCP Config</button>
  <button class="tab-btn" onclick="showTab('playground', this)">5. Live Tool &amp; Luau Runner</button>
  <button class="tab-btn" onclick="showTab('tips', this)">6. Helpful Tips &amp; Gotchas</button>
  <button class="tab-btn" onclick="showTab('ops', this)">7. Ops Console &amp; Agent Bus</button>
  <button class="tab-btn" onclick="showTab('plugin', this)">8. Studio Plugin &amp; Playtest v6</button>
</nav>

<main>
  <!-- ==================== TAB 1: LIVE MONITOR ==================== -->
  <section id="tab-monitor" class="tab-pane active">
    <div class="grid">
      <div class="card"><div class="k">Studio Peers</div><div class="v" id="c-peers">0 online</div></div>
      <div class="card"><div class="k">MCP Tools</div><div class="v" id="c-tools">—</div></div>
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
      <button class="sm danger" onclick="forceStopPlaytestUI()">&#9209; Stop Playtest</button>
      <button class="sm" onclick="quickTool('export_place_rbxlx', {outputFile:'roblox-kit/models/StarterPlace.rbxlx'})">Export .rbxlx Place</button>
      <div class="spacer"></div>
      <button class="danger sm" onclick="clearQueue()">Clear Queue</button>
    </div>

    <div class="two-col" style="margin-bottom:16px">
      <div class="card">
        <div class="prompt-head" style="flex-wrap:wrap;gap:8px;margin-bottom:8px">
          <div>
            <h2>Live 3D Place + HUD Viewport Vision</h2>
            <div class="sub">Real-time Z-buffered 3D software rasterizer &amp; HUD compositor (exact same <code>image/png</code> vision stream multimodal AI agents receive from <code>capture_viewport</code>). Drag image to orbit, scroll to zoom.</div>
          </div>
          <div style="display:flex;gap:5px;flex-wrap:wrap">
            <button class="sm primary" id="cam-perspective" onclick="refresh3DViewport('perspective')">Perspective</button>
            <button class="sm" id="cam-iso_ne" onclick="refresh3DViewport('iso_ne')">Iso NE</button>
            <button class="sm" id="cam-iso_nw" onclick="refresh3DViewport('iso_nw')">Iso NW</button>
            <button class="sm" id="cam-top_blueprint" onclick="refresh3DViewport('top_blueprint')">Top Blueprint</button>
            <button class="sm" id="cam-front" onclick="refresh3DViewport('front')">Front</button>
            <button class="sm" onclick="toggle3DHud()" id="btn-hud-toggle">HUD: OFF</button>
            <button class="sm primary" onclick="toggle3DLabels()" id="btn-labels-toggle">Labels: ON</button>
            <button class="sm primary" onclick="toggle3DGrid()" id="btn-grid-toggle">Grid: ON</button>
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:8px">
          <select id="vp3d-source" onchange="onChange3DSource()" style="flex:1;min-width:210px">
            <option value="studio:Workspace">Live Studio: Workspace (auto-fallback if offline)</option>
            <option value="showcase:starter_village">3D Showcase: Croftie Village, Farm Plots, Watchtower &amp; Shrine</option>
            <option value="showcase:horror_nightloop">3D Showcase: NightLoop Horror Cabin, FuseBox, SafeRoom &amp; Generator</option>
            <option value="dungeon:seeded">3D Procedural Dungeon (MST + Escape Loops)</option>
            <option value="prefab:world_safe_room_shelter">3D Prefab: Safe-Room Sanctuary Shelter</option>
            <option value="prefab:world_loot_chest_shrine">3D Prefab: Ancient Loot Chest Shrine</option>
            <option value="prefab:horror_fuse_breaker_station">3D Prefab: Wall FuseBox Breaker Station</option>
            <option value="prefab:horror_task_generator_machine">3D Prefab: Co-op Task Generator Machine</option>
            <option value="prefab:world_obby_stage_pack">3D Prefab: Obby Stage Pack</option>
            <option value="prefab:world_shop_pedestal_stall">3D Prefab: Shop Pedestal Stall</option>
            <option value="prefab:world_quest_giver_npc">3D Prefab: Quest Giver NPC</option>
          </select>
          <div style="display:flex;gap:4px;flex-wrap:wrap">
            <button class="sm" onclick="orbit3DCamera(-25, 0, 1)" title="Orbit Left">&#10226;</button>
            <button class="sm" onclick="orbit3DCamera(25, 0, 1)" title="Orbit Right">&#10227;</button>
            <button class="sm" onclick="orbit3DCamera(0, 12, 1)" title="Pitch Up">&#8593;</button>
            <button class="sm" onclick="orbit3DCamera(0, -12, 1)" title="Pitch Down">&#8595;</button>
            <button class="sm" onclick="orbit3DCamera(0, 0, 0.8)" title="Zoom In">&#65291;</button>
            <button class="sm" onclick="orbit3DCamera(0, 0, 1.25)" title="Zoom Out">&#65293;</button>
            <button class="sm" onclick="reset3DCamera()" title="Reset Orbit/Zoom">Reset</button>
          </div>
        </div>
        <div id="live-viewport-stage" style="background:#070a10;border:1px solid var(--border);border-radius:8px;overflow:hidden;text-align:center;position:relative;cursor:grab;user-select:none">
          <img id="live-viewport-img" alt="Live 3D Place Viewport" draggable="false" style="width:100%;max-height:320px;object-fit:contain;display:block">
        </div>
        <div id="live-viewport-meta" class="sub" style="margin-top:6px">Rendering 3D viewport&hellip;</div>
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
    <div class="grid" style="margin-bottom:16px">
      <div class="card"><div class="k">Licensed Registry</div><div class="v" id="kit-cnt-assets">4,320</div></div>
      <div class="card"><div class="k">Cloud Icons</div><div class="v" id="kit-cnt-icons">4,811</div></div>
      <div class="card"><div class="k">Luau Modules</div><div class="v" id="kit-cnt-modules">25</div></div>
      <div class="card"><div class="k">Drop-In Systems</div><div class="v" id="kit-cnt-systems">20</div></div>
      <div class="card"><div class="k">Prefabs</div><div class="v" id="kit-cnt-prefabs">26</div></div>
      <div class="card"><div class="k">3D .rbxmx Models</div><div class="v" id="kit-cnt-models">520</div></div>
    </div>

    <!-- GAMEPLAY MECHANICS, PROCEDURAL DUNGEON & ATMOSPHERE WORKBENCH -->
    <div class="card" style="margin-bottom:16px">
      <div class="prompt-head" style="flex-wrap:wrap;gap:10px;margin-bottom:10px">
        <div>
          <h2>Gameplay Mechanics, Procedural 3D Dungeon &amp; Atmosphere Workbench</h2>
          <div class="sub">Assemble complete Server/Client gameplay mechanics, generate seeded 3D dungeons with MST + loop escape routes, tune multi-layer horror/weather atmospheres, and synthesize procedural PCM <code>.wav</code> sound effects.</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="sm primary" onclick="previewDungeonLayout(false)">Generate Dungeon Map</button>
          <button class="sm" onclick="previewDungeonLayout(true)">Build Dungeon in Studio</button>
        </div>
      </div>

      <div class="two-col" style="margin-bottom:0">
        <div style="display:flex;flex-direction:column;gap:10px">
          <div style="background:rgba(9,13,20,.65);border:1px solid var(--border);border-radius:10px;padding:10px">
            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">
              <strong style="font-size:12.5px">1-Click Gameplay Mechanic Builder (<code>build_gameplay_mechanic</code>)</strong>
              <div style="display:flex;gap:6px">
                <button class="sm" onclick="runMechanicBuilder(false)">Inspect Bundle</button>
                <button class="sm primary" onclick="runMechanicBuilder(true)">Install in Studio</button>
              </div>
            </div>
            <select id="mechanic-select" onchange="runMechanicBuilder(false)" style="width:100%;margin-bottom:6px">
              <option value="horror-director">horror-director — 5-Phase AI Director + Strikes + NightModifiers + Sanity HUD</option>
              <option value="round-survival">round-survival — Co-op Generator Repair &amp; Extraction Survival Loop</option>
              <option value="stamina-movement">stamina-movement — Sprint Stamina Pool + Stealth Crouch Noise Controller</option>
              <option value="inventory-loot">inventory-loot — Slot Hotbar Inventory + Weighted Rarity Loot Chests</option>
              <option value="combat-abilities">combat-abilities — Cooldown Abilities + Shield/Armor Mitigation + Status FX</option>
              <option value="weather-cycle">weather-cycle — Dynamic Weather State Machine (Rain, BloodMoon, Blizzard)</option>
              <option value="fuse-breaker">fuse-breaker — Interactive Wall FuseBox Blackout Breaker Station</option>
              <option value="task-generator">task-generator — Hold-to-Repair Co-op Generator Objective</option>
              <option value="loot-chest">loot-chest — Ancient Reliquary Weighted Loot Chest Shrine</option>
              <option value="safe-room">safe-room — Reinforced Horror Safe-Room Sanctuary Booth</option>
            </select>
            <div id="mechanic-meta" class="sub">Select any mechanic above to inspect its Server/Client/Shared Luau scripts, reusable modules, and prefab tree.</div>
          </div>

          <div style="background:rgba(9,13,20,.65);border:1px solid var(--border);border-radius:10px;padding:10px">
            <strong style="font-size:12.5px;display:block;margin-bottom:6px">Atmosphere &amp; Weather Post-FX Studio (<code>configure_atmosphere_fx</code>)</strong>
            <div style="display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px" id="atmo-preset-btns">
              <button class="sm primary" onclick="applyAtmospherePreset('BloodMoon', false)">BloodMoon</button>
              <button class="sm" onclick="applyAtmospherePreset('DenseFog', false)">DenseFog</button>
              <button class="sm" onclick="applyAtmospherePreset('CyberpunkRain', false)">CyberpunkRain</button>
              <button class="sm" onclick="applyAtmospherePreset('Thunderstorm', false)">Thunderstorm</button>
              <button class="sm" onclick="applyAtmospherePreset('GoldenHour', false)">GoldenHour</button>
              <button class="sm" onclick="applyAtmospherePreset('DeepAbyss', false)">DeepAbyss</button>
              <button class="sm" onclick="applyAtmospherePreset('Blizzard', false)">Blizzard</button>
              <button class="sm" onclick="applyAtmospherePreset('SolarEclipse', false)">SolarEclipse</button>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
              <span class="sub" id="atmo-meta">Preset: BloodMoon (Atmosphere + ColorCorrection + Bloom)</span>
              <button class="sm primary" onclick="applyAtmospherePreset(ACTIVE_ATMO_PRESET, true)">Apply to Studio Lighting</button>
            </div>
          </div>
        </div>

        <div style="background:rgba(9,13,20,.65);border:1px solid var(--border);border-radius:10px;padding:10px;display:flex;flex-direction:column;gap:8px">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px">
            <strong style="font-size:12.5px">Seeded 3D Dungeon / Mansion Graph (<code>generate_dungeon_layout</code>)</strong>
            <div style="display:flex;gap:6px;align-items:center">
              <input id="dungeon-seed" type="number" value="2026" style="width:78px;padding:4px 6px" title="Seed">
              <input id="dungeon-rooms" type="number" min="4" max="24" value="10" style="width:62px;padding:4px 6px" title="Room Count">
              <select id="dungeon-theme" onchange="previewDungeonLayout(false)" style="width:140px;padding:4px 6px">
                <option value="horror_mansion">horror_mansion</option>
                <option value="crypt_catacombs">crypt_catacombs</option>
                <option value="scifi_facility">scifi_facility</option>
                <option value="ancient_ruins">ancient_ruins</option>
              </select>
            </div>
          </div>
          <canvas id="dungeon-canvas" width="520" height="185" style="width:100%;height:185px;background:#070a10;border:1px solid var(--border);border-radius:8px"></canvas>
          <div id="dungeon-meta" class="sub">Legend: <span style="color:#45e0a0">&#9632; spawn</span> · <span style="color:#8dc6af">&#9632; saferoom</span> · <span style="color:#ffc857">&#9632; treasure</span> · <span style="color:#5cc8ff">&#9632; puzzle</span> · <span style="color:#eb4b4b">&#9632; boss</span> · dashed = loop escape corridor</div>
        </div>
      </div>
    </div>

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
          <button class="primary sm" onclick="installAllModules()">Install All 25 Luau Modules</button>
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
            <option value="prefab:ui_horror_sanity_vitals_hud">Prefab: ui_horror_sanity_vitals_hud</option>
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
        <h2>45 CC0 Sound Effects + 12 Procedural Horror/Gameplay Synth SFX</h2>
        <div class="sub" style="margin-bottom:8px">Click <strong>&#9654;</strong> to audition in browser or <strong>Send</strong> to install in <code>SoundService</code>.</div>
        <audio id="sfx-player" style="display:none"></audio>
        <div id="synth-audio-list" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px;margin-bottom:10px"></div>
        <div id="audio-list" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:6px;max-height:260px;overflow:auto"></div>
      </div>

      <div class="card">
        <h2>20 Drop-In Server/Client Game Systems (<code>roblox-kit/systems/</code>)</h2>
        <div class="sub" style="margin-bottom:8px">Horror Director, Co-op Round Survival, Stamina/Stealth, Slot Inventory, Ability Combat, Dynamic Weather, LiveOps, Monetization &amp; ProfileStore Data.</div>
        <div id="dropin-system-list" style="display:flex;flex-direction:column;gap:6px;max-height:360px;overflow:auto"></div>
      </div>
    </div>

    <div class="card" style="margin-bottom:16px">
      <div class="prompt-head" style="flex-wrap:wrap;gap:10px">
        <div>
          <h2>Licensed Asset Library (4,320 registered · 3,705 images · 50 skies · 520 models · 45 audio)</h2>
          <div class="sub">Browse source groups, local Studio URIs, SPDX licence, source, and provenance. Kenney assets are pinned CC0; generated skies are 8-bit LDR (not HDRI). GLB thumbnails are neutral placeholders, not model renders. Use <code>search_assets</code> for model/audio/provenance filters; Send imports the selected image directly into Studio.</div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          <button class="sm primary" id="atype-any" onclick="setRegistryAssetType('any')">All (4,320)</button>
          <button class="sm" id="atype-image" onclick="setRegistryAssetType('image')">Images (3,705)</button>
          <button class="sm" id="atype-sky" onclick="setRegistryAssetType('sky')">Skies (50)</button>
          <button class="sm" id="atype-model" onclick="setRegistryAssetType('model')">3D Models (520)</button>
          <button class="sm" id="atype-audio" onclick="setRegistryAssetType('audio')">Audio (45)</button>
          <input id="asset-search" placeholder="Filter licensed local assets..." oninput="renderFilteredAssets()" style="width:210px">
          <button class="sm" onclick="loadKitGallery()">Refresh</button>
        </div>
      </div>
      <div id="asset-pack-pills" style="display:flex;flex-wrap:wrap;gap:6px;margin:10px 0"></div>
      <div style="display:flex;justify-content:space-between;align-items:center;margin:6px 0">
        <span class="sub" id="asset-page-meta">Loading asset catalog&hellip;</span>
        <div style="display:flex;gap:6px">
          <button class="sm" id="asset-prev-btn" onclick="stepAssetPage(-1)">&larr; Prev</button>
          <button class="sm" id="asset-next-btn" onclick="stepAssetPage(1)">Next &rarr;</button>
        </div>
      </div>
      <div id="asset-gallery" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-top:10px"></div>
    </div>

    <div class="two-col" style="margin-bottom:16px">
      <div class="card">
        <h2>38 Procedural Texture, VFX &amp; UI Icon Shaders (<code>image_pipeline.py</code>)</h2>
        <div class="sub" style="margin-bottom:8px">Zero-dependency mathematical PNG shaders rendered on demand via <code>/api/kit/procedural/&lt;preset&gt;.png</code>.</div>
        <div id="procedural-preset-list" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(125px,1fr));gap:8px;max-height:320px;overflow:auto"></div>
      </div>

      <div class="card">
        <h2>25 Reusable Luau Gameplay &amp; UI Modules (<code>roblox-kit/modules/</code>)</h2>
        <div class="sub" style="margin-bottom:8px">StateMachine, CooldownManager, LootTable, QuestTracker, CombatPipeline, DungeonGenerator, StudUI, ModernUI, Maid, Signal &amp; more.</div>
        <div id="module-list" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(195px,1fr));gap:6px;max-height:320px;overflow:auto"></div>
      </div>
    </div>

    <div class="card">
      <div class="prompt-head">
        <div>
          <h2>26 Prefabs &amp; Drag-and-Drop <code>.rbxmx</code> Models (<code>roblox-kit/prefabs/</code> &amp; <code>models/</code>)</h2>
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
          <div><h3><code>.mcp.json</code> / Cursor / Claude Desktop (Full Access)</h3><div class="sub">Full tool access (subject to authorization and peer availability) (multi-agent bus + tunnel ops + 3D viewport vision + 3D physics QA + .rbxlx + UI linter + LiveOps + sync)</div></div>
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
        <h3>Invoke MCP Tools Directly</h3>
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
  <!-- ==================== TAB 7: OPS CONSOLE & AGENT BUS ==================== -->
  <section id="tab-ops" class="tab-pane">
    <div class="grid">
      <div class="card"><div class="k">Tunnel</div><div class="v" id="ops-tunnel-state">stopped</div></div>
      <div class="card"><div class="k">Public URL</div><div class="v" id="ops-tunnel-url" style="font-size:12.5px;word-break:break-all">&mdash;</div></div>
      <div class="card"><div class="k">Agents Online</div><div class="v" id="ops-agents">0</div></div>
      <div class="card"><div class="k">Open Tasks</div><div class="v" id="ops-tasks">0</div></div>
      <div class="card"><div class="k">Queue Depth</div><div class="v" id="ops-queue">0</div></div>
      <div class="card"><div class="k">Audit Entries</div><div class="v" id="ops-audit-count">0</div></div>
    </div>

    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:-6px 0 14px">
      <button class="sm" id="queue-toggle" onclick="toggleQueue()">Pause queue</button>
      <span class="sub" id="queue-state">Queue live</span>
      <span class="sub" id="sweeper-state"></span>
    </div>

    <div class="prompt-card" style="margin-bottom:14px">
      <div class="prompt-head">
        <div><h3>Studio Fleet</h3>
          <div class="sub">Every connected Studio window. <b>Pin</b> makes one the default target for
            untargeted jobs (skipped automatically if that Studio closes); <b>Broadcast</b> runs a read-only
            ping on all of them and reports per-peer latency. Any MCP tool also takes a <code>peerId</code>.</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="sm" onclick="broadcastPing()">Broadcast ping</button>
          <button class="sm" onclick="unpinStudio()">Clear pin</button>
        </div>
      </div>
      <div id="fleet-detail"><span class="sub">No Studio windows connected yet.</span></div>
      <div class="sub" id="fleet-note"></div>
      <pre id="fleet-broadcast" style="max-height:170px;display:none"></pre>
    </div>

    <div class="two-col">
      <div class="prompt-card">
        <div class="prompt-head">
          <div><h3>Public Tunnel (Supervised)</h3>
            <div class="sub">Auto-restarts, scrapes the public URL, health-probes <code>/api/health</code>, and reconnects after failures. Works with <code>cloudflared</code> or <code>ngrok</code>.</div>
          </div>
          <span class="pill off" id="ops-tunnel-pill">stopped</span>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="primary sm" onclick="tunnelAction('start')">Start Tunnel</button>
          <button class="sm" onclick="tunnelAction('restart')">Restart</button>
          <button class="sm danger" onclick="tunnelAction('stop')">Stop</button>
          <button class="sm" onclick="copyText(document.getElementById('ops-tunnel-url').textContent)">Copy URL</button>
          <button class="sm" onclick="copyMcpConfig()">Copy MCP config (tunnel)</button>
        </div>
        <div class="sub" id="ops-tunnel-meta" style="margin-top:6px">Checking tunnel status&hellip;</div>
        <div class="sub" id="ops-tunnel-lan" style="margin-top:4px"></div>
        <pre id="ops-tunnel-log" style="max-height:150px">Tunnel log will appear here.</pre>
      </div>

      <div class="prompt-card">
        <div class="prompt-head"><div><h3>Join the Agent Bus</h3>
          <div class="sub">Register this session so other agents (and humans) can see you, message you, and hand work over without collisions.</div></div></div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <input id="bus-name" placeholder="Agent name (e.g. Gemini 4 Argon)" style="flex:1;min-width:190px">
          <input id="bus-role" placeholder="role (builder / qa / reviewer)" style="width:190px">
          <button class="primary sm" onclick="busRegister()">Register</button>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px">
          <input id="bus-to" placeholder="to (blank = broadcast)" style="width:180px">
          <input id="bus-subject" placeholder="subject" style="width:180px">
        </div>
        <textarea id="bus-body" placeholder="Message body&hellip; use this to coordinate edits, hand off work, or report a blocker." style="min-height:64px"></textarea>
        <div style="display:flex;gap:6px">
          <button class="sm primary" onclick="busPost()">Post Message</button>
          <button class="sm" onclick="refreshBus()">Refresh Bus</button>
          <button class="sm" onclick="busExport()">Export Handoff</button>
        </div>
        <pre id="bus-out" style="max-height:110px">Bus output appears here.</pre>
      </div>
    </div>

    <div class="two-col">
      <div class="prompt-card">
        <h3>Roster</h3>
        <div id="bus-agents" class="sub">No agents registered yet.</div>
      </div>
      <div class="prompt-card">
        <h3>Task Board</h3>
        <div style="display:flex;gap:6px;margin-bottom:8px">
          <input id="task-title" placeholder="New task title…" style="flex:1">
          <select id="task-priority" style="width:110px">
            <option value="normal">normal</option>
            <option value="high">high</option>
            <option value="critical">critical</option>
            <option value="low">low</option>
          </select>
          <button class="primary sm" onclick="createTask()">Add</button>
          <button class="sm" onclick="sweepAgents()" title="Release tasks held by agents that stopped reporting">Sweep</button>
        </div>
        <div id="bus-tasks" style="display:flex;flex-direction:column;gap:6px;max-height:280px;overflow:auto"></div>
      </div>
    </div>

    <div class="two-col">
      <div class="prompt-card">
        <div class="prompt-head"><h3>Live Event Stream</h3>
          <button class="sm" onclick="toggleEventStream()" id="ev-btn">Connect</button></div>
        <div class="sub">Server-Sent Events from every agent, job, tunnel action and audit write.</div>
        <pre id="ev-feed" style="max-height:240px;overflow:auto">Not connected.</pre>
      </div>
      <div class="prompt-card">
        <div class="prompt-head"><h3>Metrics &amp; Audit</h3>
          <div style="display:flex;gap:6px">
            <button class="sm" onclick="refreshMetrics()">Metrics</button>
            <button class="sm" onclick="verifyAudit()">Verify Chain</button>
          </div>
        </div>
        <pre id="ops-metrics" style="max-height:180px;overflow:auto">Metrics not loaded.</pre>
        <div class="sub" id="ops-latency"></div>
        <div class="sub" id="ops-audit-note"></div>
        <pre id="ops-audit" style="max-height:150px;overflow:auto">Audit log not loaded.</pre>
      </div>
    </div>
  </section>

  <section id="tab-tips" class="tab-pane">
    <div class="two-col" id="tips-grid"></div>
  </section>

  <!-- ==================== TAB 8: STUDIO PLUGIN & PLAYTEST V6 ==================== -->
  <section id="tab-plugin" class="tab-pane">
    <div class="grid" style="margin-bottom:14px">
      <div class="card"><div class="k">Bundled Plugin</div><div class="v" id="plg-bundled-ver">v6.0.0</div></div>
      <div class="card"><div class="k">Connected Peers</div><div class="v" id="plg-peer-cnt">0 online</div></div>
      <div class="card"><div class="k">Version Status</div><div class="v" id="plg-compat-state">Checking&hellip;</div></div>
      <div class="card"><div class="k">Playtest State</div><div class="v" id="plg-playtest-state">Idle</div></div>
      <div class="card"><div class="k">In-Studio UI</div><div class="v" id="plg-dockwidget">DockWidget v6</div></div>
      <div class="card"><div class="k">Plugin Jobs Run</div><div class="v" id="plg-jobs-run">0</div></div>
    </div>

    <div class="two-col" style="margin-bottom:16px">
      <div class="prompt-card">
        <div class="prompt-head">
          <div>
            <h3>Studio Plugin Version &amp; Fleet Upgrade Center (<code>ArenaBridge.lua v6.0.0</code>)</h3>
            <div class="sub">Compares every connected Studio peer&rsquo;s <code>pluginVersion</code> against bundled <code>v6.0.0</code>. Includes the new in-Studio <code>ArenaBridgeDashboardV6</code> DockWidget panel, <code>plugin_info</code> telemetry, and <code>/api/poll?goodbye=1</code> instant peer eviction.</div>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="sm primary" onclick="downloadPluginFile()">&#11015; Download ArenaBridge.lua v6.0.0</button>
            <button class="sm" onclick="installLocalPluginUI()">&#9881; Install to Local Studio</button>
            <button class="sm" onclick="loadPluginDashboard()">&#8635; Refresh</button>
          </div>
        </div>
        <table>
          <thead><tr><th>Peer ID</th><th>DM</th><th>Place</th><th>Reported Plugin</th><th>Status</th></tr></thead>
          <tbody id="plg-peer-table"><tr><td colspan="5" class="empty">Loading peer plugin compatibility&hellip;</td></tr></tbody>
        </table>
        <div id="plg-telemetry-box" class="sub" style="margin-top:8px">In-Studio telemetry will appear here when an Edit peer is connected.</div>
      </div>

      <div class="prompt-card">
        <div class="prompt-head">
          <div>
            <h3>Playtest Lifecycle &amp; Emergency Stop Controller</h3>
            <div class="sub">Solves stuck Play Solo / Server Run sessions: dispatches dual <code>edit</code> + <code>server</code> stop commands, runs <code>StudioTestService:EndTest</code> with <code>LeaveTest</code> fallback, purges leftover <code>__ArenaTestRunner_*</code> scripts, and immediately evicts ghost <code>server</code>/<code>client</code> peers.</div>
          </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin:10px 0">
          <button class="sm primary" onclick="quickTool('start_stop_play', {action:'start_play', waitForPeer:true})">&#9654; Start Play Solo (F5)</button>
          <button class="sm primary" onclick="quickTool('start_stop_play', {action:'start_run', waitForPeer:true})">&#9654; Start Server Run (F8)</button>
          <button class="sm danger" onclick="forceStopPlaytestUI()">&#9209; Force Stop Playtest &amp; Clean Runners</button>
          <button class="sm" onclick="evictGhostPeersUI()">&#129529; Evict Ghost Play Peers</button>
        </div>
        <pre id="plg-action-out" style="max-height:190px">Ready. Click "Force Stop Playtest" anytime an automated playtest or Studio session stays open.</pre>
      </div>
    </div>

    <div class="prompt-card">
      <div class="prompt-head">
        <div>
          <h3>V6 Systems &amp; Security Quick-Launcher (90 Total MCP Tools)</h3>
          <div class="sub">Test-drive the 4 new V6 tools (<code>audit_network_security</code>, <code>build_cutscene_sequence</code>, <code>generate_ui_theme_pack</code>, <code>manage_plugin_and_playtest</code>) + new <code>CutsceneDirector</code>, <code>NetworkGuard</code>, and <code>DialogueSystem</code> kit modules.</div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="sm primary" onclick="runV6ToolPreview('audit_network_security', {includeStudio:true})">&#128737; Audit Network Security</button>
          <button class="sm primary" onclick="runV6ToolPreview('build_cutscene_sequence', {preset:'boss_awakening', buildInStudio:false})">&#127916; Build Cutscene Sequence</button>
          <button class="sm primary" onclick="runV6ToolPreview('generate_ui_theme_pack', {theme:'HorrorBloodMoon', installInStudio:false})">&#127912; Generate UI Theme Pack</button>
          <button class="sm" onclick="runV6ToolPreview('manage_plugin_and_playtest', {action:'version_matrix'})">&#128268; Plugin Version Matrix</button>
        </div>
      </div>
      <pre id="v6-tool-out" style="max-height:260px">Select any V6 tool above to inspect its structured output.</pre>
    </div>
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

const PAGE_VERSION = "__BRIDGE_VERSION__";
let TOKEN = '';
function readStoredToken(){ try { return localStorage.getItem('arena_bridge_token') || ''; } catch(_) { return ''; } }
function getToken(){ return TOKEN || (document.getElementById('tok-input').value || '').trim(); }
function setToken(t){
  TOKEN = (t || '').trim();
  const box = document.getElementById('tok-input');
  if(box) box.value = TOKEN;
}
function storeToken(t){ try { localStorage.setItem('arena_bridge_token', t); } catch(_) {} }
function authHeaders(){
  const h = {'Content-Type':'application/json'};
  const t = getToken();
  if(t) h['Authorization'] = 'Bearer ' + t;
  return h;
}
function showAuthBanner(title, message){
  const b = document.getElementById('auth-banner');
  if(!b) return;
  if(title) document.getElementById('auth-title').textContent = title;
  if(message) document.getElementById('auth-msg').textContent = message;
  b.classList.add('show');
  const st = document.getElementById('status');
  st.textContent = 'token required';
  st.className = 'pill ro';
}
function hideAuthBanner(){
  const b = document.getElementById('auth-banner');
  if(b) b.classList.remove('show');
}
function saveToken(){
  const box = document.getElementById('auth-token');
  const value = (box && box.value || '').trim();
  if(!value) return toast('Paste the bridge token first.');
  storeToken(value); setToken(value); box.value = '';
  hideAuthBanner();
  toast('Token saved for this browser — reloading data…');
  tick(); refreshOps(); refreshFleet(); refreshBus(); loadKitGallery(); refresh3DViewport(); run3DSceneAudit(false, false);
}
function applyHeaderToken(){
  const box = document.getElementById('tok-input');
  const value = (box && box.value || '').trim();
  if(!value) return;
  storeToken(value); setToken(value);
  hideAuthBanner();
  toast('Header token applied — reloading data…');
  tick(); refreshOps(); refreshFleet(); refreshBus(); loadKitGallery(); refresh3DViewport(); run3DSceneAudit(false, false);
}
function showStaleBanner(runningVersion){
  const b = document.getElementById('stale-banner');
  if(!b) return;
  document.getElementById('stale-msg').textContent =
    `This tab is v${PAGE_VERSION}; the bridge is running v${runningVersion}. Reload for the current console.`;
  b.classList.add('show');
}
async function apiFetch(path, opts){
  let res;
  try {
    res = await fetch(path, Object.assign({cache:'no-store', headers:authHeaders()}, opts || {}));
  } catch(err){
    const st = document.getElementById('status');
    st.textContent = 'bridge unreachable'; st.className = 'pill off';
    throw err;
  }
  if(res.status === 401 || res.status === 403){
    showAuthBanner('This dashboard needs the bridge token',
      `The bridge rejected the request (HTTP ${res.status}). Paste bridge.token — opening the console from a tunnel or LAN address cannot auto-load it.`);
    throw new Error('unauthorized');
  }
  return res;
}

async function runSelfTest(){
  toast('Running isolated bridge suite and Studio scratch test…');
  const response = await fetch('/api/self-test', {headers: authHeaders()});
  const result = await response.json();
  if(!response.ok) throw new Error(result.error || ('HTTP ' + response.status));
  const title = document.getElementById('quick-out-title');
  const wrap = document.getElementById('quick-out-wrap');
  const pre = document.getElementById('quick-out');
  title.textContent = 'v__BRIDGE_VERSION__ Self-Test — ' + (result.ok ? 'PASS' : 'FAIL');
  pre.textContent = result.humanTable + '\n\n' + JSON.stringify({suite:result.suite,studioSmoke:result.studioSmoke,toolCount:(result.toolMatrix||[]).length}, null, 2);
  wrap.style.display = 'block';
  toast(result.ok ? 'Self-test passed.' : 'Self-test reported failures or skipped Studio checks.');
}

function showTab(name, btn){
  document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  if(name === 'plugin') loadPluginDashboard();
}

async function callMcp(toolName, argsObj){
  const hdrs = Object.assign({}, authHeaders(), {
    'MCP-Protocol-Version': '2026-07-28',
    'Mcp-Method': 'tools/call',
    'Mcp-Name': String(toolName || '')
  });
  const r = await fetch('/mcp', {
    method: 'POST',
    headers: hdrs,
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 'web-' + Date.now(),
      method: 'tools/call',
      params: { name: toolName, arguments: argsObj || {} }
    })
  });
  const data = await r.json();
  if(!r.ok) throw new Error((data.error && data.error.message) || data.error || ('HTTP ' + r.status));
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
  manage_snapshots: { action: 'list' },
  generate_dungeon_layout: { seed: 2026, roomCount: 10, theme: 'horror_mansion', buildInStudio: false },
  build_gameplay_mechanic: { mechanic: 'horror-director', installInStudio: false },
  configure_atmosphere_fx: { preset: 'BloodMoon', intensity: 1.0, applyToStudio: false },
  synthesize_sound_effect: { preset: 'horror_heartbeat', saveToRepo: false },
  manage_plugin_and_playtest: { action: 'status' },
  audit_network_security: { includeStudio: true },
  build_cutscene_sequence: { preset: 'boss_awakening', letterbox: true, buildInStudio: false },
  generate_ui_theme_pack: { theme: 'HorrorBloodMoon', installInStudio: false }
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
Roblox Studio MCP Bridge v__BRIDGE_VERSION__ is already running locally on my machine at ${window.location.origin} (directory: \`${dir}\`).

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
- Native MCP Streamable HTTP Endpoint: \`POST ${pubUrl}/mcp\` (MCP tools, bundled skills, multimodal 3D viewport vision, 3D physics auditor, runtime mesh capability probe, .rbxlx place export, 14 LiveOps/Monetization systems, 520 3D models, 3,705 raster images + 50 LDR skies (300 cube faces), 45 CC0 WAVs, 4,811 cloud icons)
- Agent Onboarding & Live State Endpoint: \`GET ${pubUrl}/api/agent-guide\`
- Built-in Repo Kit: call \`browse_kit\` for bundled skills, 19 modules, 6 templates, 20 prefabs, 3,755 image/sky-face files, 50 LDR sky panoramas, 520 models, and 45 CC0 audio files. Use \`search_assets\` for paginated type/category/source/license/provenance filters; exact Git blob matches and pack-level attestations are distinguished. Send supported images/audio/models into Studio with \`import_image\`, \`import_audio\`, and \`import_rbxmx\`.

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
     "ArenaBridge v4.1.0 uses `ScriptEditorService:GetEditorSource` and `UpdateSourceAsync` with CRLF -> LF normalization instead of raw `.Source` assignment — safely supporting scripts over 200 KB and keeping open Studio tabs synchronized without cursor corruption."],
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
let ALL_KIT_MODELS = [];
let ALL_KIT_AUDIO = [];
let ACTIVE_PACK = 'all';
let ACTIVE_ASSET_TYPE = 'any';
let ASSET_PAGE = 0;
const ASSET_PAGE_SIZE = 72;
let ACTIVE_CLOUD_CAT = '';
let ACTIVE_ATMO_PRESET = 'BloodMoon';

async function installAllModules(){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing all 25 Luau modules -> ReplicatedStorage.Shared...';
  try {
    const res = await callMcp('install_kit_module', { module: 'all', parent: 'ReplicatedStorage.Shared' });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function installSingleModule(mname){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing Luau module ' + mname + ' -> ReplicatedStorage.Shared...';
  try {
    const res = await callMcp('install_kit_module', { module: mname, parent: 'ReplicatedStorage.Shared' });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Installed module ' + mname);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function inspectSingleModule(mname){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Loading module ' + mname + '.lua...';
  try {
    const r = await fetch('/api/kit/catalog?category=modules&item=' + encodeURIComponent(mname), { cache: 'no-store' });
    const d = await r.json();
    out.textContent = d.content || JSON.stringify(d, null, 2);
  } catch (e) { out.textContent = String(e); }
}

async function runMechanicBuilder(installInStudio){
  const mech = (document.getElementById('mechanic-select') && document.getElementById('mechanic-select').value) || 'horror-director';
  const meta = document.getElementById('mechanic-meta');
  const out = document.getElementById('kit-action-out');
  try {
    if(installInStudio){
      out.textContent = 'Installing gameplay mechanic ' + mech + ' into Studio...';
      const res = await callMcp('build_gameplay_mechanic', { mechanic: mech, installInStudio: true });
      const d = res.structuredContent || res;
      out.textContent = JSON.stringify(d, null, 2);
      toast('Installed gameplay mechanic: ' + mech);
      tick();
      return;
    }
    const r = await fetch('/api/kit/catalog?format=full', { cache: 'no-store' });
    const cat = await r.json();
    const b = (cat.mechanicBundles || []).find(x => x.name === mech);
    if(b && meta){
      meta.innerHTML = `<strong>${esc(b.name)}</strong>: ${esc(b.description)} &bull; System: <code>${esc(b.system||'none')}</code> &bull; Prefab: <code>${esc(b.prefab||'none')}</code> &bull; Modules: <code>${esc((b.modules||[]).join(', '))}</code>`;
    }
  } catch (e) {
    if(meta) meta.textContent = String(e);
  }
}

async function applyAtmospherePreset(presetName, sendToStudio){
  ACTIVE_ATMO_PRESET = presetName || 'BloodMoon';
  document.querySelectorAll('#atmo-preset-btns button').forEach(b => {
    b.classList.toggle('primary', b.textContent.trim() === ACTIVE_ATMO_PRESET);
  });
  const meta = document.getElementById('atmo-meta');
  if(meta) meta.textContent = `Preset: ${ACTIVE_ATMO_PRESET} (Atmosphere + ColorCorrectionEffect + BloomEffect)`;
  if(sendToStudio){
    const out = document.getElementById('kit-action-out');
    out.textContent = 'Applying atmosphere preset ' + ACTIVE_ATMO_PRESET + ' to Studio Lighting...';
    try {
      const res = await callMcp('configure_atmosphere_fx', { preset: ACTIVE_ATMO_PRESET, intensity: 1.0, applyToStudio: true });
      out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
      toast('Applied ' + ACTIVE_ATMO_PRESET + ' to Lighting');
      tick();
    } catch (e) { out.textContent = String(e); }
  }
}

function drawDungeonCanvas(rooms, corridors){
  const cv = document.getElementById('dungeon-canvas');
  if(!cv || !cv.getContext) return;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);
  if(!rooms || !rooms.length) return;
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  rooms.forEach(r => {
    const [cx, , cz] = r.center;
    const [w, , d] = r.size;
    minX = Math.min(minX, cx - w); maxX = Math.max(maxX, cx + w);
    minZ = Math.min(minZ, cz - d); maxZ = Math.max(maxZ, cz + d);
  });
  const spanX = Math.max(40, maxX - minX);
  const spanZ = Math.max(40, maxZ - minZ);
  const pad = 22;
  const scale = Math.min((cv.width - pad * 2) / spanX, (cv.height - pad * 2) / spanZ);
  const toScreen = (x, z) => [
    pad + (x - minX) * scale,
    pad + (z - minZ) * scale
  ];
  const roleColors = {
    spawn: '#45e0a0', normal: '#5e6e82', treasure: '#ffc857',
    puzzle: '#5cc8ff', saferoom: '#8dc6af', boss: '#eb4b4b'
  };
  (corridors || []).forEach(c => {
    const ra = rooms[c.from - 1];
    const rb = rooms[c.to - 1];
    if(!ra || !rb) return;
    const [x1, y1] = toScreen(ra.center[0], ra.center[2]);
    const [x2, y2] = toScreen(rb.center[0], rb.center[2]);
    ctx.save();
    ctx.strokeStyle = c.isLoop ? 'rgba(255,200,87,0.75)' : 'rgba(92,200,255,0.65)';
    ctx.lineWidth = c.isLoop ? 1.8 : 2.6;
    if(c.isLoop) ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    ctx.restore();
  });
  rooms.forEach(r => {
    const [sx, sy] = toScreen(r.center[0], r.center[2]);
    const rw = Math.max(14, r.size[0] * scale);
    const rd = Math.max(14, r.size[2] * scale);
    ctx.fillStyle = 'rgba(15, 22, 34, 0.92)';
    ctx.strokeStyle = roleColors[r.role] || '#5e6e82';
    ctx.lineWidth = 2;
    ctx.fillRect(sx - rw / 2, sy - rd / 2, rw, rd);
    ctx.strokeRect(sx - rw / 2, sy - rd / 2, rw, rd);
    ctx.fillStyle = roleColors[r.role] || '#e2e8f0';
    ctx.font = 'bold 10px ui-monospace, monospace';
    ctx.textAlign = 'center';
    ctx.fillText(String(r.id) + ':' + r.role.slice(0, 4), sx, sy + 3);
  });
}

async function previewDungeonLayout(buildInStudio){
  const seed = parseInt((document.getElementById('dungeon-seed') && document.getElementById('dungeon-seed').value) || '2026', 10) || 2026;
  const roomCount = Math.max(3, Math.min(28, parseInt((document.getElementById('dungeon-rooms') && document.getElementById('dungeon-rooms').value) || '10', 10) || 10));
  const theme = (document.getElementById('dungeon-theme') && document.getElementById('dungeon-theme').value) || 'horror_mansion';
  try {
    const res = await callMcp('generate_dungeon_layout', { seed, roomCount, theme, buildInStudio: Boolean(buildInStudio) });
    const d = res.structuredContent || res;
    drawDungeonCanvas(d.rooms || [], d.corridors || []);
    const meta = document.getElementById('dungeon-meta');
    if(meta){
      meta.innerHTML = `Seed <code>${d.seed}</code> · <strong>${d.roomCount}</strong> rooms · <strong>${d.corridorCount}</strong> corridors (MST + escape loops) · Reachable: <strong>${d.allRoomsReachable ? '100%' : 'partial'}</strong>`;
    }
    if(buildInStudio){
      document.getElementById('kit-action-out').textContent = JSON.stringify(d, null, 2);
      toast('Generated 3D dungeon in Studio!');
      tick();
    }
  } catch (e) {
    // Deterministic client-side fallback preview if offline/unauthenticated
    const rooms = [];
    const corridors = [];
    for(let i = 0; i < roomCount; i++){
      const angle = (i / roomCount) * Math.PI * 2;
      const rad = 45 + (i % 2) * 24;
      const roles = ['spawn', 'normal', 'treasure', 'puzzle', 'saferoom', 'boss'];
      const role = i === 0 ? 'spawn' : (i === roomCount - 1 ? 'boss' : roles[(i % 4) + 1]);
      rooms.push({ id: i + 1, role, center: [Math.cos(angle) * rad, 0, Math.sin(angle) * rad], size: [24, 14, 24] });
      if(i > 0) corridors.push({ from: i, to: i + 1, isLoop: false });
    }
    if(roomCount > 3) corridors.push({ from: 1, to: roomCount, isLoop: true });
    drawDungeonCanvas(rooms, corridors);
  }
}

function playSynthAudio(preset){
  const player = document.getElementById('sfx-player');
  player.src = '/api/kit/synth-audio/' + encodeURIComponent(preset) + '.wav';
  player.currentTime = 0;
  player.play().catch(_ => {});
}

async function sendSynthAudioToStudio(preset){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Synthesizing procedural WAV ' + preset + ' -> SoundService...';
  try {
    const res = await callMcp('synthesize_sound_effect', { preset, targetPath: 'SoundService', saveToRepo: false });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Synthesized & installed ' + preset);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function sendProceduralPresetToStudio(preset){
  toast('Generating procedural shader ' + preset + ' -> StarterGui...');
  await quickTool('generate_procedural_image', {
    preset,
    name: preset,
    saveToRepo: false,
    targetPath: 'StarterGui',
    applyMode: 'screen_gui'
  });
}

async function importRbxmxModelToStudio(relModel){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Importing 3D .rbxmx model ' + relModel + ' -> Workspace...';
  try {
    const res = await callMcp('import_rbxmx', { repoModel: relModel, parent: 'Workspace' });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Imported ' + relModel + ' into Workspace!');
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

function setRegistryAssetType(atype){
  ACTIVE_ASSET_TYPE = atype || 'any';
  ASSET_PAGE = 0;
  ['any', 'image', 'sky', 'model', 'audio'].forEach(k => {
    const b = document.getElementById('atype-' + k);
    if(b) b.classList.toggle('primary', k === ACTIVE_ASSET_TYPE);
  });
  renderFilteredAssets();
}

function stepAssetPage(delta){
  ASSET_PAGE = Math.max(0, ASSET_PAGE + delta);
  renderFilteredAssets();
}

function selectPack(pack){
  ACTIVE_PACK = pack;
  ASSET_PAGE = 0;
  document.querySelectorAll('#asset-pack-pills button').forEach(b => {
    b.classList.toggle('primary', b.dataset.pack === pack);
  });
  renderFilteredAssets();
}

function renderFilteredAssets(){
  const q = (document.getElementById('asset-search').value || '').trim().toLowerCase();
  const combined = [];

  if(ACTIVE_ASSET_TYPE === 'any' || ACTIVE_ASSET_TYPE === 'image' || ACTIVE_ASSET_TYPE === 'sky'){
    ALL_KIT_IMAGES.forEach(im => {
      const isSky = im.pack === 'arena_skies' || im.thumbnailKind === 'equirectangular-panorama';
      if(ACTIVE_ASSET_TYPE === 'sky' && !isSky) return;
      if(ACTIVE_ASSET_TYPE === 'image' && isSky) return;
      if(ACTIVE_PACK !== 'all' && im.pack !== ACTIVE_PACK) return;
      if(q && !(im.relPath.toLowerCase().includes(q) || im.name.toLowerCase().includes(q) || String(im.category||'').toLowerCase().includes(q))) return;
      combined.push({ kind: isSky ? 'sky' : 'image', data: im });
    });
  }
  if(ACTIVE_ASSET_TYPE === 'any' || ACTIVE_ASSET_TYPE === 'model'){
    ALL_KIT_MODELS.forEach(m => {
      const packName = 'model_' + (m.pack || 'featured');
      if(ACTIVE_PACK !== 'all' && ACTIVE_PACK !== packName && ACTIVE_PACK !== m.pack) return;
      if(q && !(m.fileName.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))) return;
      combined.push({ kind: 'model', data: m });
    });
  }
  if(ACTIVE_ASSET_TYPE === 'any' || ACTIVE_ASSET_TYPE === 'audio'){
    ALL_KIT_AUDIO.forEach(a => {
      if(ACTIVE_PACK !== 'all' && ACTIVE_PACK !== 'audio_cc0') return;
      if(q && !(a.fileName.toLowerCase().includes(q) || a.name.toLowerCase().includes(q))) return;
      combined.push({ kind: 'audio', data: a });
    });
  }

  const maxPage = Math.max(0, Math.ceil(combined.length / ASSET_PAGE_SIZE) - 1);
  if(ASSET_PAGE > maxPage) ASSET_PAGE = maxPage;
  const start = ASSET_PAGE * ASSET_PAGE_SIZE;
  const pageItems = combined.slice(start, start + ASSET_PAGE_SIZE);

  const pageMeta = document.getElementById('asset-page-meta');
  if(pageMeta){
    pageMeta.textContent = combined.length
      ? `Showing ${start + 1}–${Math.min(combined.length, start + pageItems.length)} of ${combined.length.toLocaleString()} matching licensed assets (Page ${ASSET_PAGE + 1} of ${maxPage + 1})`
      : 'No assets match the current filter.';
  }
  const prevBtn = document.getElementById('asset-prev-btn');
  const nextBtn = document.getElementById('asset-next-btn');
  if(prevBtn) prevBtn.disabled = ASSET_PAGE <= 0;
  if(nextBtn) nextBtn.disabled = ASSET_PAGE >= maxPage;

  document.getElementById('asset-gallery').innerHTML = pageItems.map(entry => {
    if(entry.kind === 'model'){
      const m = entry.data;
      const thumbUrl = m.thumbnailPath ? `/api/kit/thumbnail/${esc(m.thumbnailPath)}` : '';
      return `
        <div class="card" style="padding:10px;display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center">
          <div style="width:76px;height:76px;display:flex;align-items:center;justify-content:center;background:#0d131d;border:1px solid var(--border);border-radius:8px;padding:4px;overflow:hidden">
            ${thumbUrl ? `<img src="${thumbUrl}" alt="${esc(m.name)}" loading="lazy" style="max-width:66px;max-height:66px;object-fit:contain" onerror="this.style.display='none'">` : `<span class="sub">3D .rbxmx</span>`}
          </div>
          <div style="font-size:10px;color:var(--accent)">3D Model &bull; ${esc(m.pack||'featured')}</div>
          <div style="font-size:9px;color:var(--muted)">CC0-1.0 &bull; ${(m.bytes/1024).toFixed(1)} KB</div>
          <code style="font-size:11px;word-break:break-all">${esc(m.name)}</code>
          <div style="display:flex;gap:4px">
            <button class="sm" onclick="copyText('${esc(m.fileName)}')">Path</button>
            <button class="sm primary" onclick="importRbxmxModelToStudio('${esc(m.fileName)}')">Spawn</button>
          </div>
        </div>`;
    }
    if(entry.kind === 'audio'){
      const a = entry.data;
      const thumbUrl = a.thumbnailPath ? `/api/kit/thumbnail/${esc(a.thumbnailPath)}` : '';
      return `
        <div class="card" style="padding:10px;display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center">
          <div style="width:110px;height:52px;display:flex;align-items:center;justify-content:center;background:#0d131d;border:1px solid var(--border);border-radius:8px;padding:4px;overflow:hidden">
            ${thumbUrl ? `<img src="${thumbUrl}" alt="${esc(a.name)}" loading="lazy" style="width:100%;height:100%;object-fit:contain">` : `<span class="sub">WAV</span>`}
          </div>
          <div style="font-size:10px;color:var(--green)">CC0 Audio &bull; ${(a.bytes/1024).toFixed(1)} KB</div>
          <code style="font-size:11px;word-break:break-all">${esc(a.name)}</code>
          <div style="display:flex;gap:4px">
            <button class="sm" onclick="playBrowserAudio('${esc(a.fileName)}')">&#9654;</button>
            <button class="sm" onclick="copyText('${esc(a.localUri)}')">URI</button>
            <button class="sm primary" onclick="sendAudioToStudio('${esc(a.fileName)}')">Send</button>
          </div>
        </div>`;
    }
    const im = entry.data;
    return `
      <div class="card" style="padding:10px;display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center">
        <div style="width:76px;height:76px;display:flex;align-items:center;justify-content:center;background:repeating-conic-gradient(#161d29 0% 25%, #0d121a 0% 50%) 50% / 14px 14px;border-radius:8px;padding:6px">
          <img src="/api/kit/image/${esc(im.relPath)}" alt="${esc(im.name)}" loading="lazy" style="max-width:62px;max-height:62px;image-rendering:auto">
        </div>
        <div style="font-size:10px;color:var(--muted)">${esc(im.pack)} &middot; ${esc(im.category||im.pack)}</div>
        <div style="font-size:9px;color:var(--muted);max-width:100%;overflow-wrap:anywhere">${esc(im.license||'CC0-1.0')} &middot; ${esc(im.provenance||'verified')}</div>
        <code style="font-size:11px;word-break:break-all">${esc(im.name)}</code>
        <div style="display:flex;gap:4px">
          <button class="sm" onclick="copyText('${esc(im.localUri)}')">URI</button>
          <button class="sm primary" onclick="importRepoAssetToStudio('${esc(im.relPath)}')">Send</button>
        </div>
      </div>`;
  }).join('');
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
let ACTIVE_3D_HUD = false;
let ACTIVE_3D_LABELS = true;
let ACTIVE_3D_GRID = true;
let ACTIVE_3D_YAW = 0;
let ACTIVE_3D_PITCH = 0;
let ACTIVE_3D_ZOOM = 1.0;

function renderAuditReportUI(d, labelPrefix){
  if(!d) return;
  const prefix = labelPrefix ? (labelPrefix + ' • ') : '';
  document.getElementById('scene-audit-summary').textContent =
    `${prefix}Audited ${d.totalPartsAudited||0} parts — Errors: ${d.errorCount||0}, Warnings: ${d.warningCount||0}` +
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
}

async function refresh3DViewport(preset){
  if(preset) ACTIVE_3D_PRESET = preset;
  ['perspective', 'iso_ne', 'iso_nw', 'top_blueprint', 'front'].forEach(k => {
    const b = document.getElementById('cam-' + k);
    if(b) b.classList.toggle('primary', k === ACTIVE_3D_PRESET);
  });
  const srcSel = document.getElementById('vp3d-source');
  const source = (srcSel && srcSel.value) || 'studio:Workspace';
  const seed = parseInt((document.getElementById('dungeon-seed') && document.getElementById('dungeon-seed').value) || '2026', 10) || 2026;
  const theme = (document.getElementById('dungeon-theme') && document.getElementById('dungeon-theme').value) || 'horror_mansion';

  // 1. If targeting live Studio and a Studio peer is online, try MCP capture_viewport first
  if(source.startswith ? source.startswith('studio:') : source.indexOf('studio:') === 0){
    const studioPath = source.slice('studio:'.length) || 'Workspace';
    try {
      const res = await callMcp('capture_viewport', {
        path: studioPath,
        preset: ACTIVE_3D_PRESET,
        includeHud: ACTIVE_3D_HUD,
        showLabels: ACTIVE_3D_LABELS,
        overlays: { grid: ACTIVE_3D_GRID, labels: ACTIVE_3D_LABELS },
        yaw: ACTIVE_3D_YAW,
        pitch: ACTIVE_3D_PITCH,
        zoom: ACTIVE_3D_ZOOM,
        width: 680,
        height: 360
      });
      const imgItem = (res && res.content || []).find(c => c.type === 'image');
      const sc = (res && res.structuredContent) || {};
      if(!res.isError && imgItem && imgItem.data){
        document.getElementById('live-viewport-img').src = 'data:image/png;base64,' + imgItem.data;
        document.getElementById('live-viewport-meta').textContent =
          `Live Studio (${studioPath}) • Camera: ${sc.cameraPreset} [${(sc.cameraPosition||[]).join(', ')}] • Parts rendered: ${sc.partsRendered||0} • Faces: ${sc.facesRasterized||0} • HUD widgets: ${sc.hudElementsComposited||0}`;
        return;
      }
    } catch(_){}
  }

  // 2. Render via /api/kit/viewport-preview (live Studio or deterministic 3D showcase/prefab/dungeon fallback)
  try {
    const qs = new URLSearchParams({
      source,
      preset: ACTIVE_3D_PRESET,
      includeHud: ACTIVE_3D_HUD ? '1' : '0',
      showLabels: ACTIVE_3D_LABELS ? '1' : '0',
      showGrid: ACTIVE_3D_GRID ? '1' : '0',
      yaw: String(ACTIVE_3D_YAW),
      pitch: String(ACTIVE_3D_PITCH),
      zoom: String(ACTIVE_3D_ZOOM),
      seed: String(seed),
      theme,
      width: '680',
      height: '360'
    });
    const r = await fetch('/api/kit/viewport-preview?' + qs.toString(), { cache: 'no-store' });
    const d = await r.json();
    if(d && d.imageBase64){
      document.getElementById('live-viewport-img').src = 'data:image/png;base64,' + d.imageBase64;
      const sc = d.meta || {};
      document.getElementById('live-viewport-meta').textContent =
        `${d.sourceLabel || source} • Camera: ${sc.cameraPreset} [${(sc.cameraPosition||[]).join(', ')}] • Parts rendered: ${sc.partsRendered||0} • Faces: ${sc.facesRasterized||0} • HUD widgets: ${sc.hudElementsComposited||0}`;
      if(d.audit){
        renderAuditReportUI(d.audit, d.isFallback ? 'Offline Showcase' : '');
      }
    }
  } catch(e){
    document.getElementById('live-viewport-meta').textContent = String(e);
  }
}

function onChange3DSource(){
  ACTIVE_3D_YAW = 0;
  ACTIVE_3D_PITCH = 0;
  ACTIVE_3D_ZOOM = 1.0;
  refresh3DViewport();
}

function orbit3DCamera(dYaw, dPitch, zoomMult){
  ACTIVE_3D_YAW = (ACTIVE_3D_YAW + (dYaw || 0)) % 360;
  ACTIVE_3D_PITCH = Math.max(-65, Math.min(65, ACTIVE_3D_PITCH + (dPitch || 0)));
  if(zoomMult && zoomMult !== 1){
    ACTIVE_3D_ZOOM = Math.max(0.3, Math.min(3.0, +(ACTIVE_3D_ZOOM * zoomMult).toFixed(2)));
  }
  refresh3DViewport();
}

function reset3DCamera(){
  ACTIVE_3D_YAW = 0;
  ACTIVE_3D_PITCH = 0;
  ACTIVE_3D_ZOOM = 1.0;
  refresh3DViewport('perspective');
}

function toggle3DHud(){
  ACTIVE_3D_HUD = !ACTIVE_3D_HUD;
  const b = document.getElementById('btn-hud-toggle');
  if(b){
    b.textContent = 'HUD: ' + (ACTIVE_3D_HUD ? 'ON' : 'OFF');
    b.classList.toggle('primary', ACTIVE_3D_HUD);
  }
  refresh3DViewport();
}

function toggle3DLabels(){
  ACTIVE_3D_LABELS = !ACTIVE_3D_LABELS;
  const b = document.getElementById('btn-labels-toggle');
  if(b){
    b.textContent = 'Labels: ' + (ACTIVE_3D_LABELS ? 'ON' : 'OFF');
    b.classList.toggle('primary', ACTIVE_3D_LABELS);
  }
  refresh3DViewport();
}

function toggle3DGrid(){
  ACTIVE_3D_GRID = !ACTIVE_3D_GRID;
  const b = document.getElementById('btn-grid-toggle');
  if(b){
    b.textContent = 'Grid: ' + (ACTIVE_3D_GRID ? 'ON' : 'OFF');
    b.classList.toggle('primary', ACTIVE_3D_GRID);
  }
  refresh3DViewport();
}

async function run3DSceneAudit(fixUnanchored, snapFloating){
  const srcSel = document.getElementById('vp3d-source');
  const source = (srcSel && srcSel.value) || 'studio:Workspace';
  try {
    if(source.indexOf('studio:') === 0){
      const studioPath = source.slice('studio:'.length) || 'Workspace';
      const res = await callMcp('audit_3d_scene', {
        path: studioPath,
        autoFixUnanchored: Boolean(fixUnanchored),
        snapFloating: Boolean(snapFloating)
      });
      if(!res.isError && res.structuredContent){
        renderAuditReportUI(res.structuredContent, `Live Studio (${studioPath})`);
        if(fixUnanchored || snapFloating) refresh3DViewport();
        return;
      }
    }
    const r = await fetch('/api/kit/viewport-preview?source=' + encodeURIComponent(source), { cache: 'no-store' });
    const d = await r.json();
    if(d && d.audit){
      renderAuditReportUI(d.audit, d.sourceLabel || source);
    }
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
    const qs = new URLSearchParams({ query: q, pack: ACTIVE_ICON_PACK, limit: '36' });
    if(ACTIVE_CLOUD_CAT) qs.set('category', ACTIVE_CLOUD_CAT);
    const r = await fetch('/api/kit/cloud-icons?' + qs.toString(), { cache: 'no-store' });
    const d = await r.json();
    const cats = d.categories || {};
    document.getElementById('cloud-icon-cats').innerHTML =
      `<button class="sm ${ACTIVE_CLOUD_CAT===''?'primary':''}" onclick="searchCloudIconsUI('')">All (${d.totalCatalogIcons||4811})</button>` +
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
    let data = {};
    try {
      const r = await fetch('/api/kit/catalog?category=all&format=full', { cache: 'no-store' });
      if(r.ok) data = await r.json();
    } catch(_){}
    if(!data.images){
      const res = await callMcp('browse_kit', { category: 'all', format: 'full' });
      data = res.structuredContent || {};
    }

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

    const synthSpecs = data.audioSynthSpecs || [];
    const synthBox = document.getElementById('synth-audio-list');
    if(synthBox){
      synthBox.innerHTML = synthSpecs.map(s => `
        <div class="cmd-row" style="justify-content:space-between;padding:6px 8px;border-color:rgba(92,200,255,.3)">
          <div style="min-width:0">
            <code style="font-size:11px;color:var(--accent)">[Synth] ${esc(s.name)}</code>
            <div class="sub" style="font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(s.description||'')}</div>
          </div>
          <div style="display:flex;gap:4px;flex-shrink:0">
            <button class="sm" onclick="playSynthAudio('${esc(s.name)}')">&#9654;</button>
            <button class="sm primary" onclick="sendSynthAudioToStudio('${esc(s.name)}')">Send</button>
          </div>
        </div>`).join('');
    }

    const audios = data.audio || [];
    ALL_KIT_AUDIO = audios;
    document.getElementById('audio-list').innerHTML = audios.map(a => `
      <div class="cmd-row" style="justify-content:space-between;padding:6px 8px">
        <code style="font-size:11px">${esc(a.name)}</code>
        <div style="display:flex;gap:4px">
          <button class="sm" onclick="playBrowserAudio('${esc(a.fileName)}')">&#9654;</button>
          <button class="sm primary" onclick="sendAudioToStudio('${esc(a.fileName)}')">Send</button>
        </div>
      </div>`).join('');

    const presets = data.proceduralPresets || [];
    const presetBox = document.getElementById('procedural-preset-list');
    if(presetBox){
      presetBox.innerHTML = presets.map(p => `
        <div class="card" style="padding:8px;display:flex;flex-direction:column;align-items:center;gap:5px;text-align:center">
          <img src="/api/kit/procedural/${esc(p)}.png" alt="${esc(p)}" loading="lazy" style="width:48px;height:48px;border-radius:6px;background:#0d121a">
          <code style="font-size:10px;word-break:break-all">${esc(p)}</code>
          <button class="sm primary" onclick="sendProceduralPresetToStudio('${esc(p)}')">Send</button>
        </div>`).join('');
    }

    const modules = data.modules || [];
    const modBox = document.getElementById('module-list');
    if(modBox){
      modBox.innerHTML = modules.map(m => `
        <div class="cmd-row" style="justify-content:space-between;padding:6px 8px">
          <div><strong style="font-size:12px">${esc(m.name)}</strong> <span class="sub">${(m.bytes/1024).toFixed(1)}K</span></div>
          <div style="display:flex;gap:4px">
            <button class="sm" onclick="inspectSingleModule('${esc(m.name)}')">Code</button>
            <button class="sm primary" onclick="installSingleModule('${esc(m.name)}')">Install</button>
          </div>
        </div>`).join('');
    }

    const prefabs = data.prefabs || [];
    document.getElementById('prefab-list').innerHTML = prefabs.map(p => `
      <div class="cmd-row">
        <div><strong>${esc(p.name)}</strong> <span class="pill" style="font-size:10px;padding:1px 6px">${esc(p.category)}</span><div class="sub">${esc(p.description)}</div></div>
        <div class="spacer"></div>
        <button class="sm primary" onclick="spawnKitPrefab('${esc(p.name)}')">Spawn</button>
      </div>`).join('');

    ALL_KIT_IMAGES = data.images || [];
    ALL_KIT_MODELS = data.models || [];
    const totalLicensed = ALL_KIT_IMAGES.length + ALL_KIT_MODELS.length + ALL_KIT_AUDIO.length;
    const setKpi = (id, v) => { const el = document.getElementById(id); if(el) el.textContent = v.toLocaleString(); };
    setKpi('kit-cnt-assets', totalLicensed || 4320);
    setKpi('kit-cnt-icons', data.cloudIconsCount || 4811);
    setKpi('kit-cnt-modules', modules.length || 25);
    setKpi('kit-cnt-systems', systems.length || 20);
    setKpi('kit-cnt-prefabs', prefabs.length || 26);
    setKpi('kit-cnt-models', ALL_KIT_MODELS.length || 520);

    const catCounts = data.imageCategories || {};
    document.getElementById('asset-pack-pills').innerHTML =
      `<button class="sm ${ACTIVE_PACK==='all'?'primary':''}" data-pack="all" onclick="selectPack('all')">All Sources (${totalLicensed})</button>` +
      Object.entries(catCounts).sort().map(([pack, cnt]) =>
        `<button class="sm ${ACTIVE_PACK===pack?'primary':''}" data-pack="${esc(pack)}" onclick="selectPack('${esc(pack)}')">${esc(pack)} (${cnt})</button>`
      ).join('') +
      `<button class="sm ${ACTIVE_PACK==='audio_cc0'?'primary':''}" data-pack="audio_cc0" onclick="selectPack('audio_cc0')">audio_cc0 (${ALL_KIT_AUDIO.length})</button>`;

    renderFilteredAssets();
    searchCloudIconsUI('');
    runMechanicBuilder(false);
    previewDungeonLayout(false);
    runUiPreviewAndAudit();
    refresh3DViewport('perspective');
    run3DSceneAudit(false, false);
  } catch(_){}
}

function bindViewportInteractiveControls(){
  const stage = document.getElementById('live-viewport-stage');
  if(!stage || stage.__boundOrbit) return;
  stage.__boundOrbit = true;
  let dragging = false, startX = 0, startY = 0, baseYaw = 0, basePitch = 0;
  stage.addEventListener('pointerdown', e => {
    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    baseYaw = ACTIVE_3D_YAW;
    basePitch = ACTIVE_3D_PITCH;
    stage.style.cursor = 'grabbing';
    try { stage.setPointerCapture(e.pointerId); } catch(_){}
  });
  stage.addEventListener('pointermove', e => {
    if(!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    ACTIVE_3D_YAW = Math.round(baseYaw - dx * 0.55) % 360;
    ACTIVE_3D_PITCH = Math.max(-65, Math.min(65, Math.round(basePitch + dy * 0.45)));
  });
  const finishDrag = e => {
    if(!dragging) return;
    dragging = false;
    stage.style.cursor = 'grab';
    if(Math.abs(e.clientX - startX) > 3 || Math.abs(e.clientY - startY) > 3){
      refresh3DViewport();
    }
  };
  stage.addEventListener('pointerup', finishDrag);
  stage.addEventListener('pointercancel', finishDrag);
  stage.addEventListener('wheel', e => {
    e.preventDefault();
    const mult = e.deltaY > 0 ? 1.12 : 0.88;
    orbit3DCamera(0, 0, mult);
  }, { passive: false });
}

async function initBoot(){
  // Token precedence: ?token= / #token= in the URL, then this browser's saved
  // token, then the loopback-only /api/local-boot value.
  try {
    const url = new URL(location.href);
    const fromQuery = url.searchParams.get('token') || (url.hash.indexOf('#token=') === 0 ? url.hash.slice(7) : '');
    if(fromQuery){
      setToken(fromQuery); storeToken(getToken());
      url.searchParams.delete('token');
      history.replaceState({}, '', url.pathname + (url.search || ''));
    }
  } catch(_){}
  if(!getToken()) setToken(readStoredToken());
  try {
    const r = await fetch('/api/local-boot', { cache:'no-store' });
    BOOT = await r.json();
    if(BOOT.token && !getToken()){
      setToken(BOOT.token);
      storeToken(BOOT.token);
    }
    if(BOOT.tokenRequired && !getToken() && !BOOT.local){
      showAuthBanner('Opened from a tunnel or LAN address',
        'The bridge will not hand out its token over the network. Paste bridge.token (printed by setup.py) — it is remembered in this browser afterwards.');
    }
    const sel = document.getElementById('tool-select');
    sel.innerHTML = BOOT.tools.map(t =>
      `<option value="${esc(t.name)}">${esc(t.name)}${t.readOnly ? ' (read-only)' : ''}</option>`
    ).join('');
    onSelectTool();
  } catch(_){}
  bindViewportInteractiveControls();
  renderTips();
  renderPrompts();
  refresh3DViewport('perspective');
  run3DSceneAudit(false, false);
  loadKitGallery();
  loadPluginDashboard();
}

async function loadPluginDashboard(){
  try {
    const r = await apiFetch('/api/plugin-status');
    const d = await r.json();
    const setEl = (id, v) => { const el = document.getElementById(id); if(el) el.textContent = v; };
    setEl('plg-bundled-ver', 'v' + (d.bundledPluginVersion || '6.0.0'));
    setEl('plg-peer-cnt', (d.onlinePeerCount || 0) + ' online');
    setEl('plg-compat-state', d.outdatedPeerCount > 0 ? (d.outdatedPeerCount + ' outdated') : 'All v6 Up-to-Date');
    const ptActive = d.playtestState && d.playtestState.playtestActive;
    setEl('plg-playtest-state', ptActive ? 'RUNNING (' + (d.playtestState.serverPeersOnline || 0) + ' srv)' : 'Idle / Clean');
    const tel = d.livePluginTelemetry || {};
    setEl('plg-dockwidget', tel.dashboardAvailable ? 'DockWidget v6 Ready' : (d.onlinePeerCount ? 'Toolbar Active' : 'Connect Studio'));
    setEl('plg-jobs-run', String(tel.jobsProcessed != null ? tel.jobsProcessed : 0));

    const ptb = document.getElementById('plg-peer-table');
    if(ptb){
      const peers = d.peers || [];
      if(!peers.length){
        ptb.innerHTML = '<tr><td colspan="5" class="empty">No Studio peers currently online. Open Roblox Studio with <code>ArenaBridge.lua</code> installed.</td></tr>';
      } else {
        ptb.innerHTML = peers.map(p => `<tr>
          <td><code>${esc(p.peerId)}</code></td>
          <td><code>${esc(p.dm)}</code></td>
          <td>${esc(p.place || '')}</td>
          <td><code>v${esc(p.pluginVersion || '?')}</code></td>
          <td class="${p.upToDate ? 's-done' : 's-error'}">${p.upToDate ? '&#10003; v6 Up-to-Date' : '&#9888; Update to v' + esc(d.bundledPluginVersion || '6.0.0')}</td>
        </tr>`).join('');
      }
    }
    const tbox = document.getElementById('plg-telemetry-box');
    if(tbox){
      if(d.livePluginTelemetry){
        tbox.innerHTML = `Live In-Studio Telemetry: <strong>${d.livePluginTelemetry.jobsProcessed || 0}</strong> jobs processed (${d.livePluginTelemetry.jobsFailed || 0} errors) &bull; Last job: <code>${esc(d.livePluginTelemetry.lastJobType || 'none')}</code> (${d.livePluginTelemetry.lastJobMs || 0}ms) &bull; DockWidget UI: <strong>${d.livePluginTelemetry.dashboardAvailable ? 'Available' : 'N/A'}</strong>`;
      } else if(d.outdatedPeerCount > 0){
        tbox.innerHTML = `<span style="color:var(--warn)">Connected Studio is running an older plugin version. Click <strong>Download ArenaBridge.lua v6.0.0</strong> or <strong>Install to Local Studio</strong> and reload plugins in Roblox Studio.</span>`;
      } else {
        tbox.textContent = 'Connect a Roblox Studio Edit session to view live in-Studio plugin telemetry.';
      }
    }
  } catch(_){}
}

function downloadPluginFile(){
  const tok = getToken();
  const url = '/api/plugin/download' + (tok ? ('?token=' + encodeURIComponent(tok)) : '');
  window.open(url, '_blank');
  toast('Downloading pre-configured ArenaBridge.lua v6.0.0…');
}

async function installLocalPluginUI(){
  const out = document.getElementById('plg-action-out');
  if(out) out.textContent = 'Installing ArenaBridge.lua v6.0.0 into local Studio Plugins folder…';
  try {
    const r = await apiFetch('/api/plugin-status', {
      method: 'POST',
      body: JSON.stringify({ action: 'install_local' })
    });
    const d = await r.json();
    if(out) out.textContent = JSON.stringify(d, null, 2);
    toast(d.installedToOsPlugins ? 'Installed ArenaBridge.lua v6.0.0 to local Studio Plugins!' : 'Local Studio Plugins folder not found on this host — use Download button instead.');
    loadPluginDashboard();
  } catch(e){
    if(out) out.textContent = String(e);
  }
}

async function forceStopPlaytestUI(){
  toast('Force-stopping active playtest & evicting ghost runtime peers…');
  const out = document.getElementById('plg-action-out');
  if(out) out.textContent = 'Dispatching dual edit + server force_stop + runner cleanup + ghost peer eviction…';
  try {
    const r = await apiFetch('/api/playtest/force-stop', {
      method: 'POST',
      body: JSON.stringify({ reason: 'dashboard_force_stop' })
    });
    const d = await r.json();
    if(out) out.textContent = JSON.stringify(d, null, 2);
    const wrap = document.getElementById('quick-out-wrap');
    const pre = document.getElementById('quick-out');
    if(wrap && pre){
      document.getElementById('quick-out-title').textContent = 'Playtest Force-Stop & Cleanup [OK]';
      pre.textContent = JSON.stringify(d, null, 2);
      wrap.style.display = 'block';
    }
    toast(`Playtest stopped cleanly (${d.evictedPlaytestPeers || 0} runtime peers evicted).`);
    tick();
    refreshFleet();
    loadPluginDashboard();
  } catch(e){
    if(out) out.textContent = String(e);
    toast('Force-stop error: ' + e.message);
  }
}

async function evictGhostPeersUI(){
  const out = document.getElementById('plg-action-out');
  try {
    const r = await apiFetch('/api/playtest/force-stop', {
      method: 'POST',
      body: JSON.stringify({ action: 'evict_only', reason: 'manual_ghost_peer_evict' })
    });
    const d = await r.json();
    if(out) out.textContent = JSON.stringify(d, null, 2);
    toast(`Evicted ${d.evictedPeers || 0} runtime peer(s).`);
    tick();
    refreshFleet();
    loadPluginDashboard();
  } catch(e){
    if(out) out.textContent = String(e);
  }
}

async function runV6ToolPreview(toolName, argsObj){
  const out = document.getElementById('v6-tool-out');
  if(out) out.textContent = `Running ${toolName}…`;
  try {
    const res = await callMcp(toolName, argsObj);
    if(out) out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast(`Completed ${toolName}`);
    tick();
  } catch(e){
    if(out) out.textContent = String(e);
  }
}

async function tick(){
  try {
    const r = await apiFetch('/api/state');
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const s = await r.json();
    if(s && s.version && PAGE_VERSION && s.version !== PAGE_VERSION){
      showStaleBanner(s.version);
    }
    const prevPlace = LAST_STATE.studio && LAST_STATE.studio.place;
    const wasConnected = Boolean(LAST_STATE.studio_connected);
    LAST_STATE = s;
    if((!wasConnected && s.studio_connected) || (s.studio && s.studio.place && s.studio.place !== prevPlace)){
      refresh3DViewport();
      run3DSceneAudit(false, false);
    }

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
    document.getElementById('c-tools').textContent = s.mcp_tools ?? '—';
    document.getElementById('c-queued').textContent = s.queued || 0;
    document.getElementById('c-snaps').textContent = s.snapshots || 0;
    const rows = s.recent || [];
    document.getElementById('c-done').textContent = rows.filter(j => j.status === 'done').length;
    document.getElementById('c-fail').textContent = rows.filter(j => j.status === 'error').length;

    const ptb = document.getElementById('peer-rows');
    if(!peers.length){
      ptb.innerHTML = '<tr><td colspan="6" class="empty">Waiting for Roblox Studio plugin to poll <code>/api/poll</code>&hellip;</td></tr>';
    } else {
      ptb.innerHTML = peers.map(p => {
        const pv = String(p.pluginVersion || '?');
        const isV6 = pv.startsWith('6.');
        const pvBadge = isV6
          ? `<span class="s-done">v${esc(pv)} &#10003;</span>`
          : `<span class="s-error" title="Update ArenaBridge.lua to v6.0.0 in Tab 8">v${esc(pv)} &#9888;</span>`;
        return `<tr>
          <td><code>${esc(p.peerId)}</code></td>
          <td><code>${esc(p.dm)}</code></td>
          <td>${esc(p.place || '')}</td>
          <td>${pvBadge}</td>
          <td>${p.hasClient ? 'ready (' + p.playerCount + 'p)' : '&mdash;'}</td>
          <td class="${p.online ? 's-done' : 's-queued'}">${p.online ? 'online' : 'stale'}</td>
        </tr>`;
      }).join('');
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

// ---------------------------- v5 Ops Console ----------------------------
let EV_SOURCE = null, EV_RETRY = null, EV_DELAY = 1000, EV_WANT = false;
let QUEUE_PAUSED = false;
async function opsJson(path, opts){
  const r = await apiFetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if(!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}
const REDUCED_MOTION = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function animateValue(el, to){
  if(!el) return;
  const target = Number(to);
  if(!isFinite(target)){ el.textContent = to; return; }
  if(REDUCED_MOTION || document.hidden){ el.textContent = target; return; }
  const from = Number(String(el.textContent).replace(/[^0-9.-]/g, '')) || 0;
  if(from === target){ el.textContent = target; return; }
  const t0 = performance.now(), dur = 420;
  const step = (t) => {
    const p = Math.min(1, (t - t0) / dur);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(from + (target - from) * eased);
    if(p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
let FLEET = {studios: [], count: 0, online: 0, pinned: null, routing: 'auto'};
function studioCard(s, detailed){
  const dot = s.online ? 'dot-live' : 'dot-live dot-dead';
  const cls = 'studio-card' + (s.pinned ? ' pinned' : '') + (s.defaultTarget ? ' active-target' : '');
  const age = s.online ? (s.ageSeconds < 2 ? 'live' : Math.round(s.ageSeconds) + 's ago') : 'offline';
  const pin = s.pinned
    ? `<button class="pin" onclick="unpinStudio()" title="Unpin">pinned</button>`
    : `<button class="pin" onclick='pinStudio(${esc(JSON.stringify(s.peerId))})' title="Make this the default Studio">pin</button>`;
  return `<div class="${cls}">
    <span class="${dot}"></span>
    <div>
      <div class="place" title="${esc(s.place)} · ${esc(s.peerId)}">${esc(s.label || s.place)}</div>
      <div class="meta"><span class="badge-dm">${esc(s.dm)}</span>
        <span>${esc(age)}</span>
        ${s.queuedJobs ? `<span>· ${s.queuedJobs} queued</span>` : ''}
        ${detailed && s.placeId ? `<span>· id ${esc(String(s.placeId))}</span>` : ''}
        ${s.defaultTarget ? '<span>· default</span>' : ''}
      </div>
    </div>
    ${pin}
  </div>`;
}
function renderFleet(d){
  FLEET = d || FLEET;
  const studios = FLEET.studios || [];
  const chips = document.getElementById('fleet-chips');
  const detail = document.getElementById('fleet-detail');
  const badge = document.getElementById('fleet-badge');
  const dot = document.getElementById('fleet-dot');
  if(chips){
    chips.innerHTML = studios.length
      ? studios.map(s => studioCard(s, false)).join('')
      : '<span class="sub">No Studio peers yet — open Roblox Studio with the ArenaBridge plugin.</span>';
  }
  if(detail){
    detail.innerHTML = studios.length
      ? studios.map(s => studioCard(s, true)).join('')
      : '<span class="sub">No Studio windows connected yet.</span>';
  }
  if(badge){
    badge.style.display = studios.length ? 'inline-flex' : 'none';
    badge.textContent = `${FLEET.online || 0}/${studios.length} studios`;
  }
  if(dot) dot.classList.toggle('dot-dead', !(FLEET.online > 0));
  const note = document.getElementById('fleet-note');
  if(note){
    note.textContent = !studios.length ? '' :
      (FLEET.pinned
        ? `Pinned: ${FLEET.pinOnline ? 'routing untargeted jobs to the pinned Studio' : 'pinned Studio is offline — falling back to auto edit-first routing'}`
        : 'Routing: auto (edit DataModels first). Pin a Studio to make it the default target.');
  }
}
async function refreshFleet(){
  try { renderFleet(await opsJson('/api/studios')); } catch(_){}
}
async function pinStudio(peerId){
  try {
    const res = await opsJson('/api/studios', {method:'POST', body:JSON.stringify({action:'set_active', peerId})});
    toast(`Pinned ${res.place || peerId} as the default Studio.`);
    refreshFleet(); refreshOps();
  } catch(err){ toast(err.message); }
}
async function unpinStudio(){
  try {
    await opsJson('/api/studios', {method:'POST', body:JSON.stringify({action:'clear_active'})});
    toast('Pin cleared — back to auto routing.');
    refreshFleet(); refreshOps();
  } catch(err){ toast(err.message); }
}
async function broadcastPing(){
  const box = document.getElementById('fleet-broadcast');
  if(box){ box.style.display = 'block'; box.textContent = 'Broadcasting to every online Studio…'; }
  try {
    const res = await opsJson('/api/studios', {method:'POST', body:JSON.stringify({action:'broadcast', type:'ping', timeout:12})});
    const rows = (res.results || []).map(r =>
      `${r.ok ? '✓' : '✗'} ${r.label || r.peerId} · ${r.status}${r.durationMs != null ? ' · ' + Math.round(r.durationMs) + 'ms' : ''}${r.error ? ' · ' + r.error : ''}`);
    if(box) box.textContent = `${res.succeeded}/${res.peers} studios responded in ${res.elapsedMs}ms\n` + (rows.join('\n') || res.error || 'no peers');
    toast(res.peers ? `${res.succeeded}/${res.peers} studios answered the broadcast.` : (res.error || 'No Studio peers.'));
    refreshFleet();
  } catch(err){
    if(box) box.textContent = err.message;
    toast(err.message);
  }
}
async function refreshOps(){
  try {
    const ops = await opsJson('/api/ops');
    const t = ops.tunnel || {};
    document.getElementById('ops-tunnel-state').textContent = t.state || 'unknown';
    document.getElementById('ops-tunnel-url').textContent = t.publicUrl || '—';
    const pill = document.getElementById('ops-tunnel-pill');
    pill.textContent = t.state || 'unknown';
    pill.className = 'pill ' + (t.state === 'online' ? 'on' : (t.state === 'degraded' ? 'ro' : 'off'));
    document.getElementById('ops-agents').textContent = (ops.bus && ops.bus.agentsOnline) || 0;
    document.getElementById('ops-tasks').textContent = (ops.openTasks || []).length;
    animateValue(document.getElementById('ops-queue'), (ops.health && ops.health.queued) || 0);
    if(ops.fleet) renderFleet(ops.fleet);
    document.getElementById('ops-audit-count').textContent = (ops.audit || []).length;
    const health = t.health || {};
    document.getElementById('ops-tunnel-meta').textContent =
      `flavor=${t.flavor || '—'} · restarts=${t.restarts || 0} · health=${health.ok === null || health.ok === undefined ? 'n/a' : (health.ok ? 'ok' : 'failing')}` +
      (health.latencyMs != null ? ` · ${health.latencyMs}ms` : '') + (t.lastError ? ' · ' + t.lastError : '');
    document.getElementById('ops-tunnel-lan').textContent = (t.lanUrls || []).length ? 'LAN: ' + t.lanUrls.join('  ·  ') : '';
    document.getElementById('ops-tunnel-log').textContent = (t.logTail || []).join('\n') || 'No tunnel output yet.';
    if((ops.audit || []).length){
      document.getElementById('ops-audit').textContent = ops.audit.slice(0,8).map(e => `${e.seq} ${e.action} · ${e.actor} · ${e.outcome}`).join('\n');
    }
    renderBus(ops.openTasks || [], null);
    // Queue pause switch + sweeper status.
    const queue = ops.queue || {};
    QUEUE_PAUSED = !!queue.paused;
    const qBtn = document.getElementById('queue-toggle');
    if(qBtn) qBtn.textContent = QUEUE_PAUSED ? 'Resume queue' : 'Pause queue';
    const qState = document.getElementById('queue-state');
    if(qState) qState.textContent = QUEUE_PAUSED
      ? `Queue paused${queue.reason ? ' — ' + queue.reason : ''} · ${queue.depth == null ? '?' : queue.depth} held`
      : `Queue live · ${queue.depth == null ? 0 : queue.depth} queued`;
    const sweeper = ops.sweeper || {};
    const sState = document.getElementById('sweeper-state');
    if(sState) sState.textContent = sweeper.intervalSeconds
      ? `Sweeper every ${Math.round(sweeper.intervalSeconds)}s (silent > ${Math.round(sweeper.offlineSeconds || 0)}s) · reaped ${sweeper.tasksReaped || 0}`
      : 'Sweeper disabled';
    const latency = ops.jobLatency || {};
    const row = document.getElementById('ops-latency');
    if(row){
      const top = Object.entries(latency).slice(0, 4)
        .map(([tool, d]) => `${tool} p50 ${d.p50}ms / p95 ${d.p95}ms (n=${d.count})`);
      row.textContent = top.length ? 'Latency: ' + top.join(' · ') : '';
    }
  } catch(_){}
}
async function tunnelAction(action){
  try {
    const r = await fetch('/api/tunnel', {method:'POST', headers:authHeaders(), body:JSON.stringify({action})});
    const data = await r.json();
    if(!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    toast('Tunnel ' + action + ' → ' + (data.state || 'ok'));
    refreshOps();
  } catch(err){ toast('Tunnel ' + action + ' failed: ' + err.message); }
}
async function busRegister(){
  const name = document.getElementById('bus-name').value.trim();
  const role = document.getElementById('bus-role').value.trim() || 'agent';
  if(!name) return toast('Enter an agent name.');
  try {
    const reg = await opsJson('/api/bus', {method:'POST', body:JSON.stringify({action:'register', name, role})});
    localStorage.setItem('arena_agent_id', reg.id);
    localStorage.setItem('arena_agent_name', reg.name);
    document.getElementById('bus-out').textContent = `Registered ${reg.name} as ${reg.id} (${reg.role})`;
    refreshBus();
  } catch(err){ toast(err.message); }
}
function currentAgent(){ return localStorage.getItem('arena_agent_id') || 'web-operator'; }
async function busPost(){
  const to = document.getElementById('bus-to').value.trim() || null;
  const subject = document.getElementById('bus-subject').value.trim();
  const body = document.getElementById('bus-body').value.trim();
  if(!subject && !body) return toast('Write a subject or body first.');
  try {
    const msg = await opsJson('/api/bus', {method:'POST', body:JSON.stringify({
      action:'post', agentId: currentAgent(), to, subject, body: body || subject, kind:'note', priority:'normal'
    })});
    document.getElementById('bus-out').textContent = 'Posted ' + msg.id;
    document.getElementById('bus-body').value = '';
    refreshBus();
  } catch(err){ toast(err.message); }
}
async function busExport(){
  try {
    const r = await opsJson('/api/bus', {method:'POST', body:JSON.stringify({action:'export', agentId:currentAgent()})});
    const note = document.getElementById('bus-out');
    note.textContent = r.markdown;
    copyText(r.markdown);
    toast('Handoff brief copied to clipboard.');
  } catch(err){ toast(err.message); }
}
function renderBus(tasks, agents){
  const taskBox = document.getElementById('bus-tasks');
  if(!tasks.length){
    taskBox.innerHTML = '<div class="sub">No open tasks. Create one from any agent with <code>agent_bus {action:"task_create"}</code>.</div>';
  } else {
    taskBox.innerHTML = tasks.map(t => {
      const lease = t.leaseRemainingSeconds != null ? Math.max(0, Math.round(t.leaseRemainingSeconds)) + 's lease' : 'unclaimed';
      return `<div class="cmd-row" style="align-items:flex-start">
        <div style="flex:1">
          <div><strong>${esc(t.title)}</strong> <span class="sub">· ${esc(t.priority)} · ${esc(t.status)} · ${lease}</span></div>
          <div class="sub">${esc(t.assignee || 'unassigned')} ${t.blockedBy && t.blockedBy.length ? '· blocked by ' + esc(t.blockedBy.join(', ')) : ''}</div>
        </div>
        <button class="sm" onclick="claimTask('${esc(t.id)}')">Claim</button>
        <button class="sm" onclick="completeTask('${esc(t.id)}')">Done</button>
      </div>`;
    }).join('');
  }
  if(agents){
    const box = document.getElementById('bus-agents');
    box.innerHTML = agents.length ? agents.map(a =>
      `<div style="display:flex;justify-content:space-between;gap:8px;padding:3px 0">
         <span><strong>${esc(a.name)}</strong> <span class="sub">${esc(a.role)}</span></span>
         <span class="sub">${esc(a.status)} · ${a.openTasks} tasks · ${a.inboxUnread} unread</span>
       </div>`).join('') : 'No agents registered yet.';
  }
}
async function refreshBus(){
  try {
    const agents = await opsJson('/api/bus?action=agents');
    const tasks = await opsJson('/api/bus?action=tasks&includeDone=0');
    renderBus(tasks.tasks || [], agents.agents || []);
    document.getElementById('ops-agents').textContent = agents.onlineCount || 0;
    document.getElementById('ops-tasks').textContent = (tasks.tasks || []).length;
  } catch(_){}
}
async function createTask(){
  const title = document.getElementById('task-title').value.trim();
  if(!title) return toast('Enter a task title first.');
  try {
    await opsJson('/api/bus', {method:'POST', body:JSON.stringify({
      action:'task_create', title, priority:document.getElementById('task-priority').value,
      agentId: currentAgent()
    })});
    document.getElementById('task-title').value = '';
    toast('Task added to the board.');
    refreshBus();
  } catch(err){ toast(err.message); }
}
async function toggleQueue(){
  const wantPause = !QUEUE_PAUSED;
  try {
    const res = await opsJson(wantPause ? '/api/queue/pause' : '/api/queue/resume', {
      method:'POST',
      body: JSON.stringify(wantPause ? {reason: 'paused from the Ops Console'} : {})
    });
    QUEUE_PAUSED = !!res.paused;
    toast(wantPause ? `Queue paused (${res.queued} job(s) held).` : 'Queue resumed — Studio will pick work up.');
    refreshOps();
  } catch(err){ toast(err.message); }
}
async function sweepAgents(){
  try {
    const res = await opsJson('/api/bus', {method:'POST', body:JSON.stringify({
      action:'sweep', offlineSeconds:900, agentId: currentAgent()
    })});
    toast(res.tasksReaped
      ? `Released ${res.tasksReaped} task(s) from ${(res.agents || []).join(', ') || 'offline agents'}.`
      : 'No stale agents — nothing to release.');
    refreshBus();
  } catch(err){ toast(err.message); }
}
async function claimTask(id){
  try {
    await opsJson('/api/bus', {method:'POST', body:JSON.stringify({action:'task_claim', taskId:id, agentId:currentAgent()})});
    toast('Task claimed.');
    refreshBus();
  } catch(err){ toast(err.message); }
}
async function completeTask(id){
  try {
    await opsJson('/api/bus', {method:'POST', body:JSON.stringify({action:'task_complete', taskId:id, agentId:currentAgent(), result:'Completed from the Ops Console'})});
    toast('Task completed.');
    refreshBus();
  } catch(err){ toast(err.message); }
}
async function refreshMetrics(){
  try {
    const m = await opsJson('/api/metrics');
    const counters = (m.counters || []).map(c => `${c.name} = ${c.value}${Object.keys(c.labels||{}).length ? ' ' + JSON.stringify(c.labels) : ''}`);
    const hists = (m.histograms || []).map(h => `${h.name} p50=${h.p50} p95=${h.p95} n=${h.count}`);
    document.getElementById('ops-metrics').textContent = 'uptime ' + m.uptimeSeconds + 's\n' + counters.concat(hists).join('\n');
  } catch(err){ document.getElementById('ops-metrics').textContent = err.message; }
}
async function verifyAudit(){
  try {
    const v = await opsJson('/api/audit?verify=1');
    document.getElementById('ops-audit-note').textContent = v.ok
      ? `Chain intact: ${v.checked} entries, head ${String(v.head).slice(0,12)}…`
      : `CHAIN BROKEN at #${v.brokenAt}: ${v.reason}`;
    const a = await opsJson('/api/audit?limit=8');
    document.getElementById('ops-audit').textContent = (a.entries || []).map(e => `${e.seq} ${e.action} · ${e.actor} · ${e.outcome}`).join('\n');
  } catch(err){ document.getElementById('ops-audit-note').textContent = err.message; }
}
function evFeedLine(text){
  const feed = document.getElementById('ev-feed');
  feed.textContent += text + '\n';
  feed.scrollTop = feed.scrollHeight;
}
function connectEvents(){
  const btn = document.getElementById('ev-btn');
  clearTimeout(EV_RETRY);
  if(EV_SOURCE){ try { EV_SOURCE.close(); } catch(_){} EV_SOURCE = null; }
  if(!EV_WANT){ btn.textContent = 'Connect'; return; }
  const token = getToken();
  const src = new EventSource('/api/events' + (token ? '?token=' + encodeURIComponent(token) : ''));
  EV_SOURCE = src;
  btn.textContent = 'Disconnect';
  src.onopen = () => {
    EV_DELAY = 1000;
    btn.textContent = 'Disconnect';
    evFeedLine('[stream live]');
  };
  src.onmessage = ev => {
    try {
      const e = JSON.parse(ev.data);
      evFeedLine(`${new Date((e.ts||0)*1000).toLocaleTimeString()}  ${e.kind}  ${JSON.stringify(e.data)}`);
    } catch(_){}
  };
  src.onerror = () => {
    try { src.close(); } catch(_){}
    if(EV_SOURCE === src) EV_SOURCE = null;
    if(!EV_WANT){ btn.textContent = 'Connect'; return; }
    const wait = Math.max(1, Math.min(EV_DELAY, 30000));
    btn.textContent = `Reconnecting (${Math.round(wait/1000)}s)`;
    evFeedLine(`[stream error — retrying in ${Math.round(wait/1000)}s]`);
    EV_DELAY = Math.min(EV_DELAY * 2, 30000);
    EV_RETRY = setTimeout(connectEvents, wait);
  };
}
function toggleEventStream(){
  const btn = document.getElementById('ev-btn');
  if(EV_WANT){
    EV_WANT = false;
    clearTimeout(EV_RETRY);
    if(EV_SOURCE){ try { EV_SOURCE.close(); } catch(_){} EV_SOURCE = null; }
    btn.textContent = 'Connect';
    evFeedLine('[disconnected]');
    return;
  }
  EV_WANT = true;
  EV_DELAY = 1000;
  connectEvents();
}
async function copyMcpConfig(){
  try {
    const ops = await opsJson('/api/ops');
    const url = (ops.tunnel || {}).publicUrl;
    if(!url) throw new Error('Tunnel is not online yet — start it, then copy the config.');
    const token = getToken() || '<bridge-token>';
    const cfg = { mcpServers: { 'roblox-studio': {
      url: String(url).replace(/\/$/, '') + '/mcp',
      headers: { Authorization: 'Bearer ' + token }
    } } };
    await copyText(JSON.stringify(cfg, null, 2));
  } catch(err){ toast(err.message || 'Cannot build MCP config'); }
}

initBoot().then(() => { tick(); refreshOps(); refreshBus(); refreshFleet();
  setInterval(tick, 1500);
  setInterval(refreshFleet, 3000);
  setInterval(() => {
    const active = document.getElementById('tab-ops').classList.contains('active');
    if(active){ refreshOps(); refreshBus(); }
  }, 4000);
});
</script></body></html>"""
    return html.replace("__BRIDGE_VERSION__", VERSION)


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
    state_dir: str = None,
    auto_tunnel: bool = False,
    tunnel_flavor: str = "auto",
    tunnel_protocol: str = "auto",
    tunnel_token: str = None,
    tunnel_name: str = None,
    public_url: str = None,
    tunnel_binary: str = None,
    sweep_interval: float = 300.0,
    sweep_offline_seconds: float = 900.0,
    require_confirm: str = "off",
    confirm_ttl_seconds: float = 180.0,
    confirm_allowance_seconds: float = 600.0,
    confirm_wait_seconds: float = 45.0,
    auto_task_ms: float = None,
    allowed_origins=None,
    max_body_bytes: int = None,
):
    resolved_token = load_token_default(token)
    state = BridgeState(
        token=resolved_token,
        read_only=read_only,
        guard_place_id=guard_place_id,
        state_dir=state_dir,
        auto_tunnel=False,  # started after the socket is bound, so the port is known
        tunnel_flavor=tunnel_flavor,
        tunnel_protocol=tunnel_protocol,
        tunnel_token=tunnel_token,
        tunnel_name=tunnel_name,
        public_url=public_url,
        tunnel_binary=tunnel_binary,
        sweep_interval=sweep_interval,
        sweep_offline_seconds=sweep_offline_seconds,
        require_confirm=require_confirm,
        confirm_ttl_seconds=confirm_ttl_seconds,
        confirm_allowance_seconds=confirm_allowance_seconds,
        confirm_wait_seconds=confirm_wait_seconds,
        auto_task_ms=auto_task_ms,
        allowed_origins=allowed_origins,
        max_body_bytes=max_body_bytes,
    )
    httpd = ThreadingHTTPServer((host, port), make_http_handler(state))
    httpd.daemon_threads = True
    httpd.bridge_state = state
    bound_port = httpd.server_address[1]
    state.tunnel.port = int(bound_port)
    state.tunnel.local_url = f"http://127.0.0.1:{bound_port}"
    if auto_tunnel:
        state.tunnel_action({"action": "start"})
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
    ap.add_argument("--tunnel", action="store_true",
                    help="Supervise a public cloudflared/ngrok tunnel for this bridge (auto-restart, health probes)")
    ap.add_argument("--tunnel-flavor", default=os.environ.get("BRIDGE_TUNNEL_FLAVOR", "auto"),
                    choices=["auto", "cloudflared", "ngrok"], help="Tunnel provider")
    ap.add_argument("--tunnel-protocol", default=os.environ.get("BRIDGE_TUNNEL_PROTOCOL", "auto"),
                    choices=["auto", "http2", "quic"],
                    help="Cloudflare tunnel edge transport (use http2 behind UDP-blocking firewalls; auto falls back to http2)")
    ap.add_argument("--tunnel-token", default=os.environ.get("TUNNEL_TOKEN"),
                    help="Cloudflare named-tunnel token (https://one.dash.cloudflare.com)")
    ap.add_argument("--tunnel-name", default=os.environ.get("BRIDGE_TUNNEL_NAME"),
                    help="Optional hostname label for a named tunnel")
    ap.add_argument("--public-url", default=os.environ.get("BRIDGE_PUBLIC_URL"),
                    help="Report a tunnel URL you run yourself (disables supervision)")
    ap.add_argument("--sweep-interval", type=float,
                    default=float(os.environ.get("BRIDGE_SWEEP_INTERVAL", "300")),
                    help="Seconds between automatic stale-agent sweeps (0 disables)")
    ap.add_argument("--sweep-offline", type=float,
                    default=float(os.environ.get("BRIDGE_SWEEP_OFFLINE", "900")),
                    help="An agent must be silent this long before its tasks are released")
    ap.add_argument("--require-confirm",
                    default=os.environ.get("BRIDGE_REQUIRE_CONFIRM", "off"),
                    help="Human-in-the-loop gate for dangerous MCP tool calls: off | destructive | write | all "
                         "(destructive also accepts 1/true). Modern clients get an input_required round-trip, "
                         "everyone else waits for an operator answer on /api/confirmations.")
    ap.add_argument("--confirm-ttl", type=float,
                    default=float(os.environ.get("BRIDGE_CONFIRM_TTL") or 180),
                    help="Seconds an unanswered confirmation stays answerable (default 180, max 900)")
    ap.add_argument("--confirm-allowance", type=float,
                    default=float(os.environ.get("BRIDGE_CONFIRM_ALLOWANCE") or 600),
                    help="Seconds a 'remember this' approval lasts for the same tool (default 600, 0 disables)")
    ap.add_argument("--confirm-wait", type=float,
                    default=float(os.environ.get("BRIDGE_CONFIRM_WAIT_SECONDS") or 45),
                    help="How long a legacy-era (pre-2026-07-28) call blocks waiting for an operator "
                         "before it fails with confirmation_timeout (default 45, max 300)")
    ap.add_argument("--auto-task-ms", type=float,
                    default=(float(os.environ["BRIDGE_MCP_AUTO_TASK_MS"])
                             if os.environ.get("BRIDGE_MCP_AUTO_TASK_MS") else 0.0),
                    help="Promote a tool call to an MCP task handle when it runs longer than this many "
                         "milliseconds (0 = never; needs the client to declare the tasks extension)")
    ap.add_argument("--state-dir", default=os.environ.get("BRIDGE_STATE_DIR"),
                    help="Directory for bus/token/audit/tunnel state (default: roblox-bridge/bridge_state)")
    args = ap.parse_args()

    srv, state = create_http_server(
        host=args.host,
        port=args.port,
        token=args.token,
        read_only=args.read_only,
        guard_place_id=args.place_id,
        state_dir=args.state_dir,
        auto_tunnel=args.tunnel,
        tunnel_flavor=args.tunnel_flavor,
        tunnel_protocol=args.tunnel_protocol,
        tunnel_token=args.tunnel_token,
        tunnel_name=args.tunnel_name,
        public_url=args.public_url,
        sweep_interval=args.sweep_interval,
        sweep_offline_seconds=args.sweep_offline,
        require_confirm=args.require_confirm,
        confirm_ttl_seconds=args.confirm_ttl,
        confirm_allowance_seconds=args.confirm_allowance,
        confirm_wait_seconds=args.confirm_wait,
        auto_task_ms=args.auto_task_ms,
    )
    ro_str = " [READ-ONLY INSPECTOR]" if state.read_only else ""
    bound_port = srv.server_address[1]
    print(
        f"[bridge] v{VERSION}{ro_str} listening on {args.host}:{bound_port} "
        f"(token {'set' if state.token else 'disabled'}, Control Center http://127.0.0.1:{bound_port}/, "
        f"MCP http://127.0.0.1:{bound_port}/mcp, ops http://127.0.0.1:{bound_port}/#ops)",
        flush=True,
    )
    if args.tunnel:
        print("[bridge] tunnel supervisor starting (cloudflared/ngrok)…", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            state._reaper_stop.set()
            state.tunnel.stop()
        finally:
            srv.shutdown()


if __name__ == "__main__":
    main()
