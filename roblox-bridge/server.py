#!/usr/bin/env python3
"""
server.py — Multi-Peer HTTP Bridge + Native Streamable HTTP MCP Server (v6.1.0)

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
import ipaddress
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse, urlsplit
import uuid

from confirm_gate import (  # noqa: E402
    ConfirmGate,
    GateError,
    canonical_digest,
    danger_keys_in,
    mode_from_env,
)
from mcp_core import (
    CONFIRM_GATED_TOOLS,
    BridgeClient,
    job_kind_is_read_only,
    job_tool_name,
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
from auth_core import PLACEHOLDER_TOKEN, ROLE_RANK, TokenStore, path_scope
from bus_core import AgentBus
from telemetry import Telemetry
from tunnel_manager import TunnelManager

VERSION = "6.1.0"
PEER_ONLINE_WINDOW = 5.0  # play DataModel peers are transient; do not route work to stale peers
DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024  # 32 MB ceiling for .rbxlx / batch image uploads
HERE = Path(__file__).resolve().parent

#: The only routes that still accept `?token=` (v6.1.0, P0-S2). These are the
#: three the Studio plugin itself calls, and `HttpService:RequestAsync` cannot be
#: relied on to send an Authorization header across every Studio build. The
#: credential baked into `ArenaBridge.lua` is a *dedicated write token* minted by
#: `setup.py` — not the owner credential — so a leaked plugin URL is revocable
#: from `/api/tokens` without rotating the whole bridge.
PLUGIN_QUERY_TOKEN_PATHS = frozenset({"/api/poll", "/api/result"})
SNAPSHOT_DIR = HERE / "snapshots"
SNAPSHOT_RETENTION = 30


class BodyLimitError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class ConfirmationRequired(PermissionError):
    """A REST enqueue was blocked by the human-in-the-loop gate (v6.1.0).

    Carries the machine-readable detail (`requestId`, `expiresAt`) so
    `POST /api/jobs` can answer `403` with the pending request id instead of
    enqueuing the job — the same decision `tools/call` makes in `_gate_pre`.
    """

    def __init__(self, detail: dict):
        detail = dict(detail or {})
        super().__init__(detail.get("error") or "confirmation required")
        self.detail = detail
        self.request_id = detail.get("requestId")


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
            "direct_chat": f"{origin}/api/chat",
            "import_web_ui": f"{origin}/api/ui/import-web",
            "export_web_ui": f"{origin}/api/ui/export-web",
            "agent_guide": f"{origin}/api/agent-guide",
            "self_test": f"{origin}/api/self-test",
        },
        "runtimeRequirements": {
            "editPeer": "Required for live inspection, writes, import, snapshots and persistent-place edits.",
            "serverPeer": "Required for live server probes, playtest state and server console.",
            "clientPeer": "Required for client-only runtime probes; some server peers can forward when a player client is attached.",
            "offlineTools": [
                "diagnose_connection (bridge registry only)", "search_tools", "audit_localization",
                "audit_onboarding_plan", "search_skills", "apply_skill", "skill_references",
                "browse_kit", "search_assets", "search_cloud_icons",
                "luau_check (when host binaries are installed)",
                "render_offline with an exported hierarchy tree",
                "import_ui_from_web", "export_ui_to_web", "build_vfx_emitter", "de_slop_audit",
                "spawn_kit_mesh", "kitbash_mesh_prop", "build_combat_hitbox",
                "configure_camera_rig", "generate_sfx_soundbank", "operator_chat",
            ],
            "peerFreshnessSeconds": 5,
        },
        "toolCounts": {
            "total": len(TOOL_SPECS),
            "readOnly": len(categories.get("read_only", [])),
            "mutating": len(categories.get("mutating", [])),
        },
        "toolsByMode": categories,
        "agentCapabilities": {
            "webAndDashboardUiImport": (
                "UI can be imported directly from the Web (HTML/CSS/Tailwind/SVG/JSON) and from the Control Center "
                "Dashboard using `import_ui_from_web` or `POST /api/ui/import-web`."
            ),
            "meshFirst3DBuilding": (
                "3D Meshes (`spawn_kit_mesh`, `import_rbxmx`, `MeshPart`, `SpecialMesh`, 520-model CC0 catalog) "
                "must be prioritized over basic blocky `Part` primitives."
            ),
            "deSlopAndVisualClarity": (
                "Divert from AI-slop artifacts (`—` em-dashes, robotic filler connectors like Furthermore/Moreover/Delve, "
                "and wall-of-text dumps). Enforce visual clarity with iconography and `de_slop_audit`."
            ),
            "directOperatorLiveChat": (
                "True Direct Live Chat (`operator_chat` and `/api/chat`) delivers human operator messages directly "
                "into the agent's active MCP tool responses (`operatorLiveChat`), never relying on ignored broadcasts."
            ),
        },
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
            "Import UI from the Web & Dashboard: use `import_ui_from_web` (or the Control Center Web UI Importer) to convert HTML/CSS/Tailwind/SVG/JSON directly into responsive Roblox `StarterGui` hierarchies.",
            "Prioritize 3D Meshes over blocky primitives: use `spawn_kit_mesh`, `import_rbxmx`, `MeshPart`, `SpecialMesh`, and the 520-model CC0 catalog instead of plain `Part` blocks.",
            "De-Slop & Visual Clarity: never emit em-dashes (`—`), robotic filler connectors (`Furthermore`, `Moreover`, `Delve`, `Tapestry`), or wall-of-text dumps; run `de_slop_audit` and pair HUD labels with icons.",
            "Direct Operator Live Chat: unread messages from the human operator are automatically injected into every MCP tool result (`operatorLiveChat`); always reply immediately via `operator_chat(action='reply', message='...')`.",
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
        # v6.1.0 (P0-S2): one-shot, short-lived tickets so `EventSource` (which
        # cannot set an Authorization header) can stream without the owner token
        # ever appearing in a URL. ticket -> {"exp": float, "principal": str}
        self._sse_tickets = {}
        self._sse_ticket_lock = threading.Lock()
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

    # -- SSE tickets (v6.1.0, P0-S2) ------------------------------------------

    def issue_sse_ticket(self, principal: str = "dashboard", ttl_seconds: float = 30.0) -> str:
        """Mint a single-use ticket for the event stream.

        `EventSource` cannot send an Authorization header, so the dashboard used
        to put the owner token in the query string — which then leaked into
        proxies, access logs and browser history. A 30-second, one-shot ticket
        bound to the requesting principal fixes that without changing the UI.
        """
        ticket = secrets.token_urlsafe(24)
        now = time.time()
        with self._sse_ticket_lock:
            for key in [k for k, v in self._sse_tickets.items() if v["exp"] < now]:
                self._sse_tickets.pop(key, None)
            self._sse_tickets[ticket] = {"exp": now + max(5.0, float(ttl_seconds)),
                                         "principal": str(principal or "dashboard")}
        return ticket

    def consume_sse_ticket(self, ticket: str) -> bool:
        """Redeem a ticket exactly once (constant-ish work; no timing oracle)."""
        if not ticket:
            return False
        now = time.time()
        with self._sse_ticket_lock:
            entry = self._sse_tickets.pop(ticket, None)
            return bool(entry) and entry["exp"] >= now

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

    def _rest_confirmation_block(self, kind: str, payload: dict, principal: str,
                                 confirmation: dict) -> dict:
        """Enforce the MRTR gate on the REST enqueue path (v6.1.0).

        `--require-confirm` used to wrap only the MCP `tools/call` path, so a
        write-scoped token could enqueue `run_luau` over `POST /api/jobs`. This
        mirrors `MCPDispatcher._gate_pre`: same tool mapping (`job_tool_name`),
        same read-only oracle (`is_tool_call_read_only`), same modes.

        Returns None when the job may proceed, otherwise an error dict that
        carries the pending `requestId` so the caller can approve out-of-band
        and retry.
        """
        gate = self.confirm_gate
        if not gate.enabled:
            return None
        tool = job_tool_name(kind)
        if not tool:
            # Internal job kinds with no tool of their own: fail closed unless
            # the kind is a known read. `get_tree`/`hash_scripts` are reads.
            if gate.mode != "all" and job_kind_is_read_only(kind, payload):
                return None
            tool = kind
        spec = TOOL_MAP.get(tool) or {}
        annotations = spec.get("annotations") or {}
        # Modes `write`/`destructive`: a read-only action of a write tool passes,
        # but the operator-curated destructive set still applies. Mode `all`
        # gates everything, reads included — same as `_gate_pre`.
        if gate.mode != "all" and job_kind_is_read_only(kind, payload):
            if tool not in CONFIRM_GATED_TOOLS and not annotations.get("destructiveHint"):
                return None
        if not gate.requires(tool, annotations, payload):
            return None
        principal = principal or "rest"
        if gate.allowance_active(tool, principal):
            return None

        confirmation = confirmation if isinstance(confirmation, dict) else {}
        state_token = confirmation.get("stateToken")
        request_id = confirmation.get("requestId")
        digest = canonical_digest(tool, payload)

        if state_token:
            try:
                parsed = gate.parse_state(state_token)
            except GateError as exc:
                return {"error": f"{exc.reason}: {exc.message}", "requestId": request_id}
            if parsed.get("dig") != digest:
                return {
                    "error": "digest_mismatch: the confirmation was issued for a different call.",
                    "requestId": parsed.get("id"),
                }
            request_id = parsed.get("id")
            rec = gate.pending(request_id)
            if rec is None:
                return {"error": "unknown_request: that confirmation is no longer pending.",
                        "requestId": request_id}
            if not self._gate_principal_ok(rec.principal, principal):
                return {"error": "principal_mismatch: that confirmation belongs to a different caller.",
                        "requestId": request_id}
            try:
                decided = gate.take_decision(request_id, principal)
            except GateError as exc:
                return {"error": f"{exc.reason}: {exc.message}", "requestId": request_id}
            if decided is None:
                return {
                    "error": (
                        f"confirmation {request_id} is still pending. An operator must approve it "
                        "(`arena_studio.py approve <id>`, POST /api/confirmations, or the Ops console) "
                        "before this job can be enqueued."
                    ),
                    "requestId": request_id,
                    "expiresAt": rec.public().get("expiresAt"),
                }
            if not decided.get("approved"):
                return {"error": "denied: an operator declined this confirmation.",
                        "requestId": request_id}
            return None

        if request_id:
            try:
                decided = gate.take_decision(request_id, principal)
            except GateError as exc:
                return {"error": f"{exc.reason}: {exc.message}", "requestId": request_id}
            if decided is None:
                rec = gate.pending(request_id)
                if rec is None:
                    return {"error": "unknown_request: no such confirmation is pending.",
                            "requestId": request_id}
                return {
                    "error": f"confirmation {request_id} is still pending; an operator must answer it first.",
                    "requestId": request_id,
                    "expiresAt": rec.public().get("expiresAt"),
                }
            if not decided.get("approved"):
                return {"error": "denied: an operator declined this confirmation.", "requestId": request_id}
            return None

        rec = gate.request(tool, payload, principal, danger_keys_in(payload))
        return {
            "error": (
                f"confirmation required for '{tool}'. Approve it with "
                f"`arena_studio.py approve {rec.id}` (or POST /api/confirmations), then retry "
                "this request with the same body plus `\"confirmation\": {\"requestId\": ...}`."
            ),
            "requestId": rec.id,
            "expiresAt": rec.public().get("expiresAt"),
        }

    @staticmethod
    def _gate_principal_ok(recorded_principal: str, principal: str) -> bool:
        """A REST caller may only spend its own confirmation (self-approval ban).

        v6.1.0: this used to `from confirm_gate import same_principal`, which is
        a `ConfirmGate` *staticmethod*, not a module-level name — so the branch
        that compares principals (a `confirmation.stateToken` retry) raised
        ImportError instead of enforcing the self-approval ban.
        """
        return ConfirmGate.same_principal(recorded_principal, principal)

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
        confirmation: dict = None,
        principal: str = None,
        gate_cleared: bool = False,
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

            # v6.1.0 (P0-S1): the human-in-the-loop gate is enforced here, at the
            # single shared chokepoint, so `POST /api/jobs` cannot bypass what
            # `/mcp` `tools/call` enforces. Deduplicated retries skip the gate, and
            # so does an MCP call the dispatcher has already gated (`gate_cleared`
            # is set from the thread-local call context, never from a request body).
            if not gate_cleared:
                block = self._rest_confirmation_block(kind, payload_dict, principal, confirmation)
                if block:
                    raise ConfirmationRequired(block)

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
                confirmation=body.get("confirmation") if isinstance(body.get("confirmation"), dict) else None,
                principal=body.get("principal"),
                gate_cleared=bool(body.get("gateCleared")),
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
            # v6.1.0 (P0-S3): every document/script the bridge serves is framed
            # by a strict CSP with no `'unsafe-inline'` for script, plus
            # `X-Frame-Options: DENY` for browsers that predate `frame-ancestors`.
            if str(ctype).split(";")[0].strip().lower() in (
                "text/html", "application/javascript", "text/javascript", "text/css"
            ):
                for hk, hv in SECURITY_HEADERS.items():
                    if hk == "X-Content-Type-Options":
                        continue
                    self.send_header(hk, hv)
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

        def _supplied_token(self, qs: dict, allow_query: bool = False):
            # v6.1.0 (P0-S2): `?token=` is accepted on exactly one route — the
            # SSE stream, which cannot set headers — so a credential cannot ride
            # in a bookmarked/shared URL everywhere else. Use X-Arena-Token /
            # Authorization elsewhere.
            bearer = self.headers.get("Authorization", "")
            bearer_tok = bearer[7:].strip() if bearer.lower().startswith("bearer ") else None
            if allow_query:
                return (
                    qs.get("token", [None])[0]
                    or self.headers.get("X-Arena-Token")
                    or bearer_tok
                )
            return self.headers.get("X-Arena-Token") or bearer_tok

        def _ticket_ok(self, qs: dict) -> bool:
            ticket = (qs.get("ticket") or [None])[0]
            return bool(ticket) and state.consume_sse_ticket(str(ticket))

        def _route_scope(self, required: str) -> str:
            """Raise `required` to the floor declared in `auth_core` for this path.

            v6.1.0 (P0-S6): `READ_ONLY_PATHS`/`ADMIN_PATHS` used to be dead code —
            nothing read them, so `/api/plugin/download` sat in `READ_ONLY_PATHS`
            while the route handed out the owner token. They are now the written-down
            policy and a handler can require *more* than the floor, never less.
            """
            declared = path_scope(urlparse(self.path).path)
            if not declared:
                return required
            if ROLE_RANK.get(declared, 0) > ROLE_RANK.get(required, 0):
                return declared
            return required

        def _auth_ctx(self, qs: dict, required: str = "read", allow_loopback: bool = False,
                      allow_query_token: bool = False):
            """Return (context, error). Auth is a no-op when no token is configured."""
            required = self._route_scope(required)
            # The Studio plugin cannot set an Authorization header reliably across
            # `HttpService:RequestAsync` versions, so its three routes keep the
            # query token. Everything else (dashboard, CLI, MCP) is header-only.
            if not allow_query_token:
                allow_query_token = urlparse(self.path).path in PLUGIN_QUERY_TOKEN_PATHS
            if allow_query_token and self._ticket_ok(qs):
                return {"role": "read", "id": "sse-ticket", "label": "sse-ticket"}, None
            supplied = self._supplied_token(qs, allow_query=allow_query_token)
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

        def _require(self, qs: dict, required: str = "read", allow_loopback: bool = False,
                     allow_query_token: bool = False):
            """Scope check that writes the error response itself. Returns ctx or None."""
            required = self._route_scope(required)
            ctx, err = self._auth_ctx(qs, required, allow_loopback=allow_loopback,
                                      allow_query_token=allow_query_token)
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
                return self._send(200, dashboard_html(), "text/html; charset=utf-8",
                                  extra_headers={"Cache-Control": "no-store"})

            # v6.1.0 (P4-D1): the dashboard's CSS/JS now live in static/ so the
            # page can be served with a script-src that excludes 'unsafe-inline'.
            # The name is looked up in a whitelist dict — never joined to a path.
            if path.startswith("/static/"):
                asset = static_asset(path[len("/static/"):].split("?")[0])
                if not asset:
                    return self._send(404, {"error": "asset not found"})
                body, ctype = asset
                return self._send(200, body, ctype,
                                  extra_headers={"Cache-Control": "public, max-age=3600"})

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
                # v6.1.0 (P0-S6): this route bypassed `_require` entirely, so an
                # unauthenticated caller could pull full module source
                # (`?item=NetworkGuard` -> 5,396 chars of Luau).
                # Policy, now written down in `auth_core.ROUTE_DECIDES_PATHS`:
                # kit *metadata* is public (the kit is CC0 and the dashboard
                # gallery renders before any token exists), kit *source content*
                # requires `read`. `format=full` alone does NOT carry source --
                # a category listing returns name/path/bytes only -- so it must
                # not be part of this test, or the public gallery 401s.
                wants_source = bool(item_q) or bool(sec)
                if wants_source and not self._require(qs, "read"):
                    return
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
                # v6.1.0 (P0-S2): this endpoint used to hand the *owner* token to
                # any loopback request and to any read-scoped token, which turned
                # a demo/CI credential into full admin. The boot payload itself is
                # harmless and the dashboard needs it to render, so it stays 200;
                # only the credential is withheld. An admin caller (the owner
                # token, or a minted admin token that could rotate it anyway)
                # still gets it, which keeps the loopback dashboard self-filling.
                ctx, err = self._auth_ctx(qs, "read")
                is_local = self._is_local_convenience_request()
                may_disclose = err is None and ctx.get("role") == "admin"
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
                disclose = may_disclose and is_local
                return self._send(
                    200,
                    {
                        "local": is_local,
                        "token": state.token if disclose else "",
                        "tokenRequired": bool(state.token),
                        # Tells the dashboard *why* it has no credential, so the
                        # auth banner can say "paste the token from bridge.token"
                        # instead of looking broken.
                        "tokenWithheld": bool(state.token) and not disclose,
                        "authenticated": err is None,
                        "role": (ctx or {}).get("role") if err is None else None,
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

            if path == "/api/sse-ticket":
                # v6.1.0 (P0-S2): the dashboard redeems this once, then streams
                # with `?ticket=` instead of putting a real token in the URL.
                ctx = self._require(qs, "read")
                if not ctx:
                    return
                return self._send(200, {"ticket": state.issue_sse_ticket(f"token:{ctx['id']}"),
                                        "ttlSeconds": 30})

            if path == "/api/events":
                if not self._require(qs, "read", allow_query_token=True):
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

            if path == "/api/chat":
                if not self._require(qs, "read", allow_loopback=True):
                    return
                limit = self._int_qs(qs, "limit", 80, 1, 500)
                since = self._int_qs(qs, "since", 0, 0, 10_000_000)
                unread_only = (qs.get("unreadOnly") or ["0"])[0] in ("1", "true", "yes")
                return self._send(200, state.bus.chat_history(limit=limit, since_seq=since, unread_for_agent=unread_only))

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
                # v6.1.0 (P0-S2): this route bakes a credential into the plugin,
                # so it is admin-only, and the baked credential is a short-TTL
                # write-scoped token minted for this download — never the owner
                # `bridge.token`, which would let any read caller escalate.
                ctx = self._require(qs, "admin")
                if not ctx:
                    return
                import re as _re_dl
                plugin_src = (HERE / "ArenaBridge.lua").read_text(encoding="utf-8")
                target_url = state.tunnel.public_url or f"http://127.0.0.1:{self.server.server_port}"
                plugin_token = ""
                if state.token:
                    try:
                        minted = state.tokens.create(
                            label=f"plugin-download:{ctx['label']}", role="write",
                            ttl_seconds=int(os.environ.get("BRIDGE_PLUGIN_TOKEN_TTL") or 86400),
                        )
                        plugin_token = minted["token"]
                        state.telemetry.audit.append(
                            "token.create", actor=ctx["label"], target=minted["id"],
                            detail={"role": "write", "purpose": "plugin-download"},
                        )
                    except Exception as exc:  # noqa: BLE001 - never leak the owner token on failure
                        state.telemetry.events.emit(
                            "plugin.token_mint_failed", {"error": str(exc)}, level="warn"
                        )
                        return self._send(500, {"error": "could not mint a plugin token"})
                plugin_src = _re_dl.sub(
                    r'local BRIDGE_URL\s*=\s*"[^"]*"',
                    f'local BRIDGE_URL = "{target_url}"',
                    plugin_src,
                )
                plugin_src = _re_dl.sub(
                    r'local BRIDGE_TOKEN\s*=\s*"[^"]*"',
                    f'local BRIDGE_TOKEN = "{plugin_token}"',
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
                        # v6.1.0 (P0-S1): `gate_cleared` is deliberately NOT
                        # readable from the request body — only the in-process
                        # dispatcher can set it, so a REST caller cannot skip the
                        # confirmation gate by asking to.
                        confirmation=body.get("confirmation"),
                        principal=f"token:{ctx['id']}",
                    )
                except ConfirmationRequired as exc:
                    payload = {"error": str(exc)}
                    payload.update({k: v for k, v in exc.detail.items() if k != "error" and v is not None})
                    return self._send(403, payload)
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

            # --- v5/v6 collaboration, direct live chat & web UI import --------
            if path == "/api/chat":
                act = str(body.get("action") or "send").strip().lower()
                is_ro = act in ("history", "list", "poll") and not body.get("markDelivered")
                ctx = self._require(qs, "read" if is_ro else "write", allow_loopback=True)
                if not ctx:
                    return
                try:
                    if act in ("send", "reply"):
                        role = body.get("role") or ("agent" if act == "reply" else "operator")
                        res = state.bus.chat_send(
                            sender=body.get("sender") or ("Operator" if role == "operator" else "Agent"),
                            text=body.get("text") or body.get("message") or body.get("body") or "",
                            role=role,
                            priority=body.get("priority") or ("high" if role == "operator" else "normal"),
                            pinned=bool(body.get("pinned", False)),
                            reply_to=body.get("replyTo"),
                        )
                        state.telemetry.events.emit("chat.message", {
                            "id": res["id"], "role": res["role"], "sender": res["sender"], "text": res["text"][:200],
                        })
                        return self._send(200, {"ok": True, "message": res, "history": state.bus.chat_history(limit=50)})
                    if act == "poll":
                        return self._send(200, state.bus.chat_pop_unread_for_agent(
                            delivered_via=str(body.get("deliveredVia") or "http_api"),
                            mark_delivered=bool(body.get("markDelivered", True)),
                        ))
                    if act == "ack":
                        return self._send(200, state.bus.chat_ack(
                            message_ids=body.get("messageIds"),
                            clear_pins=bool(body.get("clearPins", False)),
                            unpin_id=body.get("unpinId"),
                        ))
                    return self._send(200, state.bus.chat_history(
                        limit=int(body.get("limit") or 80),
                        since_seq=int(body.get("since") or 0),
                    ))
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})

            if path == "/api/ui/import-web":
                ctx = self._require(qs, "write" if body.get("installInStudio") else "read", allow_loopback=True)
                if not ctx:
                    return
                from mcp_core import import_ui_from_web_helper
                try:
                    return self._send(200, import_ui_from_web_helper(state.mcp_client, body))
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})

            if path == "/api/ui/export-web":
                ctx = self._require(qs, "read", allow_loopback=True)
                if not ctx:
                    return
                from mcp_core import export_ui_to_web_helper
                try:
                    return self._send(200, export_ui_to_web_helper(state.mcp_client, body))
                except ValueError as exc:
                    return self._send(400, {"error": str(exc)})

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

STATIC_DIR = HERE / "static"

#: Cache the composed dashboard. The static files ship with the bridge and are
#: read once per process; `_static_mtime()` invalidates it in dev.
_DASHBOARD_CACHE: dict = {"key": None, "html": None}

#: CSP for every HTML document the bridge serves (v6.1.0, P0-S3). Scripts come
#: from `/static/` only, so there is no `'unsafe-inline'` for script: an escaped
#: attribute can no longer execute. Styles keep `'unsafe-inline'` because the
#: dashboard sets a handful of inline `style` properties at runtime.
DASHBOARD_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

#: Security headers sent with the dashboard and its static assets.
SECURITY_HEADERS = {
    "Content-Security-Policy": DASHBOARD_CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}

#: The only files `/static/` will serve. A whitelist, not a path join, so a
#: traversal in the URL cannot reach anything else on disk (v6.1.0, P0-S4).
STATIC_ASSETS = {
    "index.html": ("text/html; charset=utf-8", True),
    "dashboard.css": ("text/css; charset=utf-8", True),
    "dashboard.js": ("application/javascript; charset=utf-8", True),
}


def _static_mtime() -> tuple:
    try:
        return tuple(round(p.stat().st_mtime, 3) for p in sorted(STATIC_DIR.glob("*")))
    except OSError:
        return ()


def dashboard_html() -> str:
    """Compose `static/index.html` with its stylesheet and script.

    The dashboard used to be ~3,000 lines of HTML/CSS/JS embedded in a Python
    raw string, which made it untestable and forced `'unsafe-inline'` into any
    script CSP. It now lives in `roblox-bridge/static/` (P4-D1) and Python only
    substitutes the version stamp.
    """
    key = _static_mtime()
    if _DASHBOARD_CACHE["key"] == key and _DASHBOARD_CACHE["html"]:
        return _DASHBOARD_CACHE["html"]
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    page = page.replace("__BRIDGE_VERSION__", VERSION)
    _DASHBOARD_CACHE["key"] = key
    _DASHBOARD_CACHE["html"] = page
    return page


def static_asset(name: str):
    """Return (body, content_type) for a whitelisted `/static/` file, else None."""
    entry = STATIC_ASSETS.get(name)
    if not entry:
        return None
    content_type, _substitute = entry
    path = STATIC_DIR / name
    try:
        body = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if _substitute:
        body = body.replace("__BRIDGE_VERSION__", VERSION)
    return body, content_type


# ----------------------------------------------------------------------------
# Server runner helpers (standalone CLI & embedded daemon thread)
# ----------------------------------------------------------------------------

TOKEN_FILE = HERE / "bridge.token"

#: Bind addresses that put the socket on an interface other than loopback.
PUBLIC_BIND_HOSTS = frozenset({"0.0.0.0", "::", "::0", "*", "all", ""})


def _bind_is_public(host: str) -> bool:
    """True when `host` would accept connections from somewhere other than this machine."""
    text = str(host or "").strip().lower()
    if text in PUBLIC_BIND_HOSTS:
        return True
    try:
        addr = ipaddress.ip_address(text)
    except ValueError:
        # A hostname: anything that is not obviously loopback counts as exposed.
        return text not in ("localhost", "localhost.localdomain")
    return not addr.is_loopback


def mint_owner_token(path: Path = None, verbose: bool = True) -> str:
    """Create `bridge.token` at mode 0600 and return the new secret.

    v6.1.0 (P0-S5): a first run with no token used to come up in *open admin* —
    `authenticate()` hands role `admin` to id `open` whenever nothing is
    configured, and the default bind was `0.0.0.0`. Anyone on the LAN got the
    whole bridge. The token is now generated on first run instead.
    """
    target = Path(path) if path else TOKEN_FILE
    raw = "arb_" + secrets.token_urlsafe(30)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Write through a temp file + replace so a crash cannot leave a half-written
    # credential, then tighten the mode (best effort on Windows).
    tmp = target.with_name(target.name + f".tmp-{os.getpid()}")
    tmp.write_text(raw + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    if verbose:
        print(f"[bridge] generated a new owner token and wrote it to {target} (mode 0600).", flush=True)
        print(f"[bridge] Studio plugin + CLI read it automatically; to print it: python3 setup.py --token", flush=True)
    return raw


def load_token_default(explicit_token: str = None, allow_generate: bool = True,
                       verbose: bool = True) -> str:
    """Resolve the owner credential: explicit → `BRIDGE_TOKEN` → `bridge.token`.

    With `allow_generate` (the default for the standalone CLI) a first run with
    no credential anywhere mints one instead of coming up in open-admin mode.
    Embedded callers (`setup.py`, tests) pass `allow_generate=False` and decide
    for themselves.
    """
    if explicit_token is not None and explicit_token != "":
        candidate = explicit_token.strip()
        if candidate == PLACEHOLDER_TOKEN:
            raise ValueError(
                f"refusing to start with the demo placeholder token '{PLACEHOLDER_TOKEN}'. "
                "Run `python3 setup.py --rotate` to mint a real one."
            )
        return candidate
    env_tok = os.environ.get("BRIDGE_TOKEN")
    if env_tok and env_tok.strip():
        if env_tok.strip() == PLACEHOLDER_TOKEN:
            raise ValueError(
                f"BRIDGE_TOKEN is set to the demo placeholder '{PLACEHOLDER_TOKEN}'; pick a real secret."
            )
        return env_tok.strip()
    if TOKEN_FILE.is_file():
        txt = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if txt and txt != PLACEHOLDER_TOKEN:
            return txt
    if allow_generate:
        return mint_owner_token(verbose=verbose)
    return ""


def create_http_server(
    host: str = "127.0.0.1",
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
    no_auth: bool = False,
    generate_token: bool = False,
):
    """Build the HTTP server + `BridgeState`.

    `host` defaults to loopback: binding `0.0.0.0` used to be the default, which
    combined with a missing `bridge.token` produced an open admin bridge on the
    LAN (v6.1.0, P0-S5). `no_auth=True` is the only way to get the old
    credential-free behaviour, and it is announced loudly.
    """
    resolved_token = "" if no_auth else load_token_default(token, allow_generate=generate_token)
    if _bind_is_public(host):
        print(
            f"[bridge] WARNING: exposed on all interfaces ({host}:{port}). "
            "Anyone who can route to this machine can reach the bridge. "
            "Prefer --host 127.0.0.1 plus --tunnel (authenticated, TLS, revocable).",
            flush=True,
        )
    if no_auth or not resolved_token:
        print(
            "[bridge] WARNING: running WITHOUT authentication — every route is open admin. "
            "This is only safe behind a trusted reverse proxy or for a throwaway test.",
            flush=True,
        )
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
    ap.add_argument("--host", default=os.environ.get("BRIDGE_HOST", "127.0.0.1"),
                    help="Bind address. Defaults to loopback; pass 0.0.0.0 (or set BRIDGE_HOST) "
                         "to expose on every interface — that prints a warning, and you should "
                         "prefer --tunnel instead.")
    ap.add_argument("--token", default=None, help="Shared secret (auto-reads bridge.token if present)")
    ap.add_argument("--no-auth", action="store_true",
                    default=os.environ.get("BRIDGE_NO_AUTH", "") in ("1", "true", "yes"),
                    help="DANGEROUS: run without any credential. Every route becomes open admin. "
                         "Without this flag a first run generates bridge.token (mode 0600) instead.")
    ap.add_argument("--no-generate-token", action="store_true",
                    help="Do not auto-create bridge.token on first run; start unauthenticated "
                         "(same effect as --no-auth) and say so.")
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
        no_auth=bool(args.no_auth or args.no_generate_token),
        generate_token=not args.no_generate_token,
    )
    ro_str = " [READ-ONLY INSPECTOR]" if state.read_only else ""
    bound_port = srv.server_address[1]
    if not state.token:
        # v6.1.0 (P0-S5): open mode is an explicit choice now, so it is recorded
        # in the audit chain, not just printed once at startup.
        try:
            state.telemetry.audit.append(
                "bridge.open_auth", actor="server", target=f"{args.host}:{bound_port}",
                detail={"reason": "--no-auth" if args.no_auth else "--no-generate-token",
                        "publicBind": _bind_is_public(args.host)},
                outcome="warn",
            )
            state.publish_mcp_event("bridge.open_auth",
                                    {"host": args.host, "port": bound_port}, level="warn")
        except Exception:  # noqa: BLE001 - telemetry must never stop startup
            pass
    print(
        f"[bridge] v{VERSION}{ro_str} listening on {args.host}:{bound_port} "
        f"(token {'set' if state.token else 'DISABLED (open admin)'}, "
        f"Control Center http://127.0.0.1:{bound_port}/, "
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
