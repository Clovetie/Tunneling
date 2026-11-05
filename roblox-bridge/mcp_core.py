#!/usr/bin/env python3
"""
mcp_core.py — Zero-dependency Model Context Protocol (MCP) engine & Roblox Studio
tool suite for Arena <-> Roblox Studio (v5.4.0).

Supports:
  - Dual-era MCP: 2026-07-28 (stateless, server/discover, resultType, MRTR,
    subscriptions/listen, tasks extension) and the legacy 2025-11-25 /
    2025-06-18 / 2025-03-26 / 2024-11-05 handshake eras
  - Multi Round-Trip Requests: destructive calls can answer with
    resultType "input_required" + a signed requestState (human-in-the-loop)
  - Task-augmented tool calls (tasks/get · tasks/update · tasks/cancel, plus the
    legacy tasks/result · tasks/list pair for 2025-11-25 clients)
  - subscriptions/listen: one opt-in stream for resource-update notifications
  - Transports: stdio (via mcp_server.py) and Streamable HTTP /mcp (via server.py)
  - 75 curated, annotated Roblox Studio & Luau engineering tools
  - Live MCP Resources + Resource Templates + Workflow Prompts
  - Exact 53-bit-safe 32-bit FNV-1a hashing matching ArenaBridge.lua bit-for-bit
  - Local pre-flight Luau syntax + undefined-global static analysis (luau_check)
  - Repo <-> Studio drift detection, atomic cache-busting sync, & snapshots
  - Read-only Inspector mode enforced on every tool & sub-action
  - Transparent fallback to run_luau if an older v2.0 Studio plugin is still loaded
"""

import json
import math
import os
import random
import re
import threading
import uuid
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from confirm_gate import (  # noqa: E402  (v5.4.0 human-in-the-loop gate)
    ConfirmGate,
    GateError,
    canonical_digest,
    danger_keys_in,
)

MCP_SERVER_NAME = "arena-roblox-studio-mcp"
MCP_SERVER_VERSION = "5.4.0"
SUPPORTED_PROTOCOL_VERSIONS = (
    "2026-07-28",   # stateless era: server/discover, per-request _meta, resultType
    "2025-11-25",   # final legacy era: tasks, elicitation, structured output
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSIONS = tuple(v for v in SUPPORTED_PROTOCOL_VERSIONS if v != MODERN_PROTOCOL_VERSION)
# Beyond this many tracked tasks the oldest finished ones are pruned.
TASK_HISTORY_LIMIT = 200
TASK_DEFAULT_TTL_MS = 3_600_000
TASK_POLL_INTERVAL_MS = 1000

# --- 2026-07-28 era plumbing -------------------------------------------------
#: Namespaced `_meta` keys a self-describing request carries.
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
META_LOG_LEVEL = "io.modelcontextprotocol/logLevel"
META_SUBSCRIPTION_ID = "io.modelcontextprotocol/subscriptionId"
META_RELATED_TASK = "io.modelcontextprotocol/related-task"
#: The official tasks extension (SEP-2663) is announced under `extensions`.
TASKS_EXTENSION_KEY = "io.modelcontextprotocol/tasks"
#: Trace-context keys (SEP-414): forward the caller's spans into the audit chain.
TRACE_META_KEYS = ("traceparent", "tracestate", "baggage")

#: What `subscriptions/listen` can actually deliver from this bridge.
SUBSCRIPTION_TYPES = (
    "toolsListChanged",
    "promptsListChanged",
    "resourcesListChanged",
    "resourceSubscriptions",
)
#: Types whose lists are static in this server, so nothing can ever change.
SUBSCRIPTION_TYPES_IDLE = ("promptsListChanged", "resourcesListChanged")
#: telemetry event kind -> MCP resource URI that a change to it invalidates.
RESOURCE_EVENT_TRIGGERS = {
    "studio://state": ("peer.join", "peer.leave", "peer.stale", "job.claim",
                       "job.done", "job.error", "job.timeout", "fleet.broadcast",
                       "bridge.start"),
    "studio://console": ("console.line", "job.error", "job.done"),
    "studio://ops": ("queue.paused", "queue.resumed", "tunnel.action",
                     "tunnel.blocked", "mcp.confirm.approved", "mcp.confirm.denied"),
    "studio://bus": ("bus.message", "bus.task", "bus.sweep", "bus.agent"),
}

#: `bridge_ops` actions that describe the confirmation queue itself. Reading them
#: is always allowed — an operator (or an agent reporting state) must be able to
#: see what is waiting even while every other call is gated. *Answering* is not:
#: the principal whose call is blocked must never be the one unblocking it.
CONFIRM_CONTROL_ACTIONS = ("confirmations", "subscriptions")
CONFIRM_ANSWER_ACTIONS = ("confirm_approve", "confirm_deny")

#: Reasons a *client* can recover from by answering again. The 2026-07-28 MRTR
#: rules say a missing or unusable answer is not a tool failure: a modern client
#: gets a fresh InputRequiredResult, with the reason spelled out so it can tell
#: the human what went wrong.
REPROMPT_REASONS = frozenset({
    "digest_mismatch", "principal_mismatch", "replayed", "expired",
    "unknown_request", "cancelled",
})

#: Tools `--require-confirm=destructive` gates: anything that can change the
#: place, the live servers, or the published asset data.
CONFIRM_GATED_TOOLS = (
    "delete_instance", "reparent_or_rename", "set_properties", "mass_set_properties",
    "build_tree", "build_biome", "build_skybox", "scatter_props", "edit_terrain",
    "multi_edit", "write_script", "batch_write", "find_and_replace_scripts",
    "insert_asset", "import_image", "import_images_batch", "import_audio",
    "import_rbxmx", "import_place_rbxlx", "install_dropin_system", "install_template",
    "install_kit_module", "spawn_prefab", "drift_and_sync", "manage_snapshots",
    "open_cloud_action", "start_stop_play", "run_studio_tests", "simulate_input",
    "undo_redo",
)

#: `manage_device_simulator` actions, in the order the tool schema advertises
#: them. The schema enum is derived from this tuple so the two cannot drift.
DEVICE_SIMULATOR_ACTIONS = (
    "status", "list", "set_device", "set", "set_orientation", "set_resolution",
    "set_pixel_density", "set_scaling_mode", "create_profile", "update_profile",
    "remove_profile", "stop",
)

#: Accepted `simulate_input` step verbs (mapped onto VirtualInput calls).
INPUT_ACTIONS = (
    "keydown", "keyup", "press", "type", "move", "click", "doubleclick",
    "rightclick", "middleclick", "drag", "wheel", "tap", "longpress", "swipe",
    "pinch", "wait",
)
#: Steps that need a screen position (either `position` or a `target` instance).
INPUT_POSITION_ACTIONS = (
    "move", "click", "doubleclick", "rightclick", "middleclick", "tap",
    "longpress", "swipe", "pinch", "drag",
)
#: Steps that need a `key`.
INPUT_KEY_ACTIONS = ("keydown", "keyup", "press")
MAX_INPUT_STEPS = 128

KNOWN_ROBLOX_GLOBALS = {
    "game", "workspace", "script", "plugin", "version", "loadstring",
    "Enum", "Instance", "CFrame", "Vector3", "Vector2", "Color3", "UDim2",
    "UDim", "TweenInfo", "RaycastParams", "Random", "NumberSequence",
    "NumberSequenceKeypoint", "NumberRange", "ColorSequence",
    "ColorSequenceKeypoint", "BrickColor", "Ray", "Region3", "Rect",
    "Font", "Content", "vector", "CatalogSearchParams", "DockWidgetPluginGuiInfo",
    "PathWaypoint", "FloatCurveKey", "RotationCurveKey", "SharedTable",
    "SecurityCapabilities", "UserSettings", "elapsedTime",
    "task", "warn", "print", "wait", "spawn", "delay", "tick", "time",
    "typeof", "require", "shared", "settings", "DateTime", "PhysicalProperties",
    "OverlapParams", "Axes", "Faces", "debug", "utf8", "bit32", "buffer",
    "os", "math", "string", "table", "coroutine", "select", "unpack",
    "newproxy", "gcinfo",
}

READ_ONLY_JOB_TYPES = {
    "ping", "studio_state", "inspect", "search_tree", "compare_instances",
    "get_properties", "read_script", "read_scripts", "hash_scripts",
    "script_search", "script_grep", "survey", "console", "require_fresh",
    "playtest_caps",
}


# ---------------------------------------------------------------------------
# Exact 32-bit FNV-1a hash (matches ArenaBridge.lua fnv32 / fnvHex bit-for-bit)
# ---------------------------------------------------------------------------

def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def fnv1a_32(data) -> int:
    if isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = bytes(data)
    h = 2166136261
    for b in raw:
        h ^= b
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def fnv1a_hex(data) -> str:
    return f"{fnv1a_32(data):08x}"


# ---------------------------------------------------------------------------
# Local Luau compiler & undefined-global checker (works offline without Studio)
# ---------------------------------------------------------------------------

def _find_tool_binary(name: str):
    here = Path(__file__).resolve().parent
    candidates = [
        here / "tools" / name,
        here / "tools" / f"{name}.exe",
        here.parent / "tools" / name,
        here.parent / "tools" / f"{name}.exe",
        here.parent / "roblox-bridge" / "tools" / name,
        here.parent / "roblox-bridge" / "tools" / f"{name}.exe",
        here.parent.parent / "roblox-bridge" / "tools" / name,
        here.parent.parent / "roblox-bridge" / "tools" / f"{name}.exe",
        here.parent / "nightloop" / ".tools" / "bin" / name,
        here.parent / ".tools" / "bin" / name,
    ]
    for path in candidates:
        if path.is_file():
            if not os.access(path, os.X_OK):
                try:
                    os.chmod(path, path.stat().st_mode | 0o111)
                except OSError:
                    pass
            if os.access(path, os.X_OK):
                return str(path)
    from shutil import which
    return which(name)


def check_luau_source(source: str, filename: str = "input.lua") -> dict:
    """Validate Luau syntax + undefined globals + unused locals on one string."""
    compiler = _find_tool_binary("luau-compile")
    analyzer = _find_tool_binary("luau-analyze")

    result = {
        "file": filename,
        "syntaxOk": True,
        "undefinedGlobals": [],
        "unusedVariables": [],
        "compileErrors": [],
        "toolchainAvailable": bool(compiler or analyzer),
        "backend": "binary" if (compiler or analyzer) else "unavailable",
        "bytes": len(source.encode("utf-8")),
        "hash": fnv1a_hex(normalize_newlines(source)),
    }

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".lua", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(source)
        tmp_path = tmp.name

    try:
        if compiler:
            try:
                proc = subprocess.run(
                    [compiler, "--binary", tmp_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=15,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                compiler = None
                result["toolchainError"] = f"luau-compile unavailable: {exc}"
                proc = None
            if proc is not None and proc.returncode != 0:
                err_txt = proc.stderr.decode("utf-8", "replace").strip()
                err_txt = err_txt.replace(tmp_path, filename)
                result["syntaxOk"] = False
                result["ok"] = False
                result["compileErrors"] = [
                    ln for ln in err_txt.splitlines() if ln.strip()
                ]
                return result

        if analyzer:
            try:
                proc = subprocess.run(
                    [analyzer, tmp_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=15,
                )
                output = proc.stdout.decode("utf-8", "replace").replace(tmp_path, filename)
            except (OSError, subprocess.SubprocessError) as exc:
                result["toolchainError"] = f"luau-analyze unavailable: {exc}"
                analyzer = None
                output = ""
            for line in output.splitlines():
                if "SyntaxError:" in line:
                    result["syntaxOk"] = False
                    result["compileErrors"].append(line)
                elif "Unknown global '" in line:
                    m = re.search(r"Unknown global '([^']+)'", line)
                    sym = m.group(1) if m else ""
                    if sym and sym not in KNOWN_ROBLOX_GLOBALS:
                        result["undefinedGlobals"].append({"symbol": sym, "detail": line})
                elif "LocalUnused:" in line or "FunctionUnused:" in line:
                    result["unusedVariables"].append(line)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    result["toolchainAvailable"] = bool(compiler or analyzer)
    result["ok"] = result["syntaxOk"] and len(result["undefinedGlobals"]) == 0
    if not result["toolchainAvailable"]:
        result["backend"] = "unavailable"
        result["ok"] = False
        result["reason"] = "toolchain_unavailable"
        result["warnings"] = ["No executable Luau compiler/analyzer is available on the bridge host."]
    else:
        result["warnings"] = []
    return result


def _studio_luau_check(client, source: str, filename: str = "inline.lua", dm: str = "edit", peer_id: str = None) -> dict:
    """Use Studio's own Luau parser when host binaries are missing or unusable."""
    level = 2
    while "]" + "=" * level + "]" in source:
        level += 1
    eq = "=" * level
    quoted = f"[{eq}[{source}]{eq}]"
    escaped_name = json.dumps(filename)
    wrapper = (
        f"local src = {quoted}\n"
        f"local f, e = loadstring(src, '@' .. {escaped_name})\n"
        "if f then return {syntaxOk=true, backend='studio-loadstring'} end\n"
        "return {syntaxOk=false, backend='studio-loadstring', error=tostring(e)}"
    )
    try:
        job = client.submit_job("run_luau", {"code": wrapper}, wait=20.0, dm=dm, peer_id=peer_id)
        data = _unwrap_job(job)
        ret = data.get("returned")
        parsed = json.loads(ret) if isinstance(ret, str) and ret.startswith("{") else data.get("value")
        if not isinstance(parsed, dict):
            raise RuntimeError("Studio parser returned no structured result")
        errors = [] if parsed.get("syntaxOk") else [parsed.get("error", "Luau parse failed")]
        return {
            "file": filename, "syntaxOk": bool(parsed.get("syntaxOk")),
            "undefinedGlobals": [], "unusedVariables": [], "compileErrors": errors,
            "toolchainAvailable": False, "backend": "studio-loadstring",
            "ok": bool(parsed.get("syntaxOk")), "warnings": [],
        }
    except Exception as exc:
        return {
            "file": filename, "syntaxOk": False, "undefinedGlobals": [],
            "unusedVariables": [], "compileErrors": [], "toolchainAvailable": False,
            "backend": "unavailable", "ok": False, "reason": "all_backends_failed",
            "warnings": [str(exc)],
        }


def _check_with_fallback(client, source: str, filename: str, dm: str = "edit", peer_id: str = None) -> dict:
    result = check_luau_source(source, filename)
    if result.get("toolchainAvailable") and "toolchainError" not in result:
        return result
    if client is not None:
        return _studio_luau_check(client, source, filename, dm=dm, peer_id=peer_id)
    return result


def run_luau_check(code: str = None, path: str = None) -> dict:
    """Validate inline Luau code, a single .lua/.luau file, or a whole directory."""
    if code is not None:
        single = check_luau_source(code, path or "inline.lua")
        return {
            "ok": single["ok"],
            "toolchainAvailable": single["toolchainAvailable"],
            "filesChecked": 1,
            "filesFailed": 0 if single["ok"] else 1,
            "results": [single],
        }

    if not path:
        raise ValueError("Provide either 'code' (inline string) or 'path' (file/directory).")

    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {target}")

    files = []
    if target.is_file():
        files = [target]
    else:
        for p in sorted(target.rglob("*")):
            if p.is_file() and p.suffix in (".lua", ".luau"):
                files.append(p)

    results = []
    failed = 0
    for f in files:
        src = f.read_text(encoding="utf-8")
        res = check_luau_source(src, str(f.relative_to(target.parent)))
        if not res["ok"]:
            failed += 1
        results.append(res)

    return {
        "ok": failed == 0,
        "toolchainAvailable": results[0]["toolchainAvailable"] if results else bool(_find_tool_binary("luau-compile")),
        "filesChecked": len(results),
        "filesFailed": failed,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Local Directory <-> Studio Script Tree Mapper (Rojo-compatible naming)
# ---------------------------------------------------------------------------

def classify_lua_filename(filename: str):
    """Map disk filename to (instance_name, roblox_class_name, is_init)."""
    for suffix, cls in (
        (".server.luau", "Script"),
        (".server.lua", "Script"),
        (".client.luau", "LocalScript"),
        (".client.lua", "LocalScript"),
        (".luau", "ModuleScript"),
        (".lua", "ModuleScript"),
    ):
        if filename.endswith(suffix):
            stem = filename[: -len(suffix)]
            return stem, cls, (stem == "init")
    return None, None, False


def scan_local_lua_tree(local_dir: Path, studio_root: str):
    """Return (flat_map[studio_path -> info], build_spec_root_node)."""
    local_dir = local_dir.resolve()
    root_name = studio_root.split(".")[-1].split("/")[-1]
    flat = {}

    def build_dir_node(dir_path: Path, cur_studio_path: str, inst_name: str):
        init_file = None
        init_cls = None
        child_specs = []

        entries = sorted(dir_path.iterdir(), key=lambda x: x.name)
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_file():
                stem, cls, is_init = classify_lua_filename(entry.name)
                if not cls:
                    continue
                if is_init:
                    init_file = entry
                    init_cls = cls
                else:
                    src = normalize_newlines(entry.read_text(encoding="utf-8"))
                    sp = f"{cur_studio_path}.{stem}"
                    flat[sp] = {
                        "path": sp,
                        "name": stem,
                        "className": cls,
                        "file": str(entry),
                        "bytes": len(src.encode("utf-8")),
                        "hash": fnv1a_hex(src),
                        "source": src,
                    }
                    child_specs.append({
                        "name": stem,
                        "className": cls,
                        "source": src,
                        "replaceExisting": True,
                    })
            elif entry.is_dir():
                sub_node = build_dir_node(entry, f"{cur_studio_path}.{entry.name}", entry.name)
                if sub_node is not None:
                    child_specs.append(sub_node)

        if init_file is not None:
            src = normalize_newlines(init_file.read_text(encoding="utf-8"))
            flat[cur_studio_path] = {
                "path": cur_studio_path,
                "name": inst_name,
                "className": init_cls,
                "file": str(init_file),
                "bytes": len(src.encode("utf-8")),
                "hash": fnv1a_hex(src),
                "source": src,
            }
            return {
                "name": inst_name,
                "className": init_cls,
                "source": src,
                "replaceExisting": True,
                "children": child_specs,
            }

        if not child_specs and dir_path != local_dir:
            return None

        return {
            "name": inst_name,
            "className": "Folder",
            "replaceExisting": True,
            "children": child_specs,
        }

    tree_node = build_dir_node(local_dir, studio_root, root_name)
    return flat, tree_node


# ---------------------------------------------------------------------------
# Bridge Client Abstraction (HTTP or In-Process Direct Queue)
# ---------------------------------------------------------------------------

class BridgeTransportError(RuntimeError):
    pass


class NoPeerError(RuntimeError):
    def __init__(self, dm: str):
        self.dm = dm
        self.reason = "no_client_peer" if dm == "client" else "no_peer"
        super().__init__(f"{{\"ok\":false,\"reason\":\"{self.reason}\",\"dm\":\"{dm}\"}}")


class BridgeClient:
    """Talks either directly in-process to server.py state OR over HTTP."""

    def __init__(
        self,
        base_url: str = None,
        token: str = None,
        direct_dispatch=None,
        read_only: bool = False,
        default_place_id: int = None,
    ):
        self.base_url = (base_url or os.environ.get("BRIDGE_URL") or "http://127.0.0.1:8077").rstrip("/")
        self.token = token if token is not None else os.environ.get("BRIDGE_TOKEN", "")
        self.direct_dispatch = direct_dispatch
        self.read_only = read_only
        self.default_place_id = default_place_id

    def _http_request(self, method: str, path: str, payload=None, timeout: float = 125.0):
        url = f"{self.base_url}{path}"
        headers = {}
        if self.token:
            headers["X-Arena-Token"] = self.token
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", "replace")[:500]
            raise BridgeTransportError(f"HTTP {exc.code}: {body_text}") from exc
        except urllib.error.URLError as exc:
            raise BridgeTransportError(
                f"Cannot reach Roblox Studio bridge at {self.base_url} ({exc.reason})"
            ) from exc

    def health(self) -> dict:
        if self.direct_dispatch:
            return self.direct_dispatch("health", {})
        return self._http_request("GET", "/api/health", timeout=10.0)

    def state(self) -> dict:
        if self.direct_dispatch:
            return self.direct_dispatch("state", {})
        return self._http_request("GET", "/api/state", timeout=10.0)

    def peers(self) -> dict:
        if self.direct_dispatch:
            return self.direct_dispatch("peers", {})
        return self._http_request("GET", "/api/peers", timeout=10.0)

    # -- v5 coordination & ops helpers ---------------------------------------

    def bus_action(self, payload: dict = None) -> dict:
        payload = dict(payload or {})
        if self.direct_dispatch:
            return self.direct_dispatch("bus", payload)
        return self._http_request("POST", "/api/bus", payload, timeout=30.0)

    def fleet_action(self, payload: dict = None) -> dict:
        payload = dict(payload or {})
        if self.direct_dispatch:
            return self.direct_dispatch("fleet", payload)
        action = (payload.get("action") or "list").lower()
        if action in ("list", "status"):
            return self._http_request("GET", "/api/studios", timeout=15.0)
        return self._http_request("POST", "/api/studios", payload, timeout=30.0)

    def broadcast(self, kind: str = "ping", payload: dict = None, timeout: float = 20.0,
                  dm: str = None) -> dict:
        body = {"action": "broadcast", "type": kind, "payload": payload or {},
                "timeout": timeout, "dm": dm}
        if self.direct_dispatch:
            return self.direct_dispatch("broadcast", body)
        return self._http_request("POST", "/api/studios", body, timeout=timeout + 15.0)

    def ops_action(self, payload: dict = None) -> dict:
        p = dict(payload or {})
        action = (p.get("action") or "status").lower()
        if self.direct_dispatch:
            mapping = {
                "status": ("ops", {}),
                "metrics": ("metrics", {}),
                "audit": ("audit", {"limit": p.get("limit", 50)}),
                "audit_verify": ("audit", {"verify": True}),
                "events": ("events", {"cursor": p.get("cursor", 0), "limit": p.get("limit", 50)}),
                "tunnel": ("tunnel", {"action": "status"}),
                "tunnel_start": ("tunnel", {"action": "start"}),
                "tunnel_stop": ("tunnel", {"action": "stop"}),
                "tunnel_restart": ("tunnel", {"action": "restart"}),
                "queue_clear": ("queue_clear", {}),
                "queue_pause": ("queue_pause", {"reason": p.get("reason")}),
                "queue_resume": ("queue_resume", {}),
                "sweep": ("sweep", {}),
                "reap": ("reap", {}),
                "confirmations": ("confirmations", {"action": "status"}),
                "confirm_approve": ("confirmations", {
                    "action": "approve", "requestId": p.get("requestId"),
                    "remember": p.get("remember"), "note": p.get("note"), "source": "mcp",
                }),
                "confirm_deny": ("confirmations", {
                    "action": "deny", "requestId": p.get("requestId"),
                    "note": p.get("note"), "source": "mcp",
                }),
                "subscriptions": ("subscriptions", {}),
            }
            if action not in mapping:
                raise ValueError(f"Unknown bridge_ops action: {action}")
            op, body = mapping[action]
            return self.direct_dispatch(op, body)

        if action == "status":
            return self._http_request("GET", "/api/ops", timeout=20.0)
        if action == "metrics":
            fmt = p.get("format") or "json"
            if fmt == "prometheus":
                return {"format": "prometheus", "text": self._http_request_text("/api/metrics?format=prometheus")}
            return self._http_request("GET", "/api/metrics", timeout=20.0)
        if action == "audit":
            return self._http_request("GET", f"/api/audit?limit={int(p.get('limit') or 50)}", timeout=20.0)
        if action == "audit_verify":
            return self._http_request("GET", "/api/audit?verify=1", timeout=20.0)
        if action == "events":
            return self._http_request(
                "GET", f"/api/events?once=1&since={int(p.get('cursor') or 0)}&limit={int(p.get('limit') or 50)}", timeout=20.0
            )
        if action in ("tunnel", "tunnel_start", "tunnel_stop", "tunnel_restart"):
            sub = {"tunnel": "status", "tunnel_start": "start", "tunnel_stop": "stop", "tunnel_restart": "restart"}[action]
            if sub == "status":
                return self._http_request("GET", "/api/tunnel", timeout=15.0)
            return self._http_request("POST", "/api/tunnel", {"action": sub}, timeout=90.0)
        if action == "queue_pause":
            return self._http_request("POST", "/api/queue/pause", {"reason": p.get("reason")}, timeout=20.0)
        if action == "queue_resume":
            return self._http_request("POST", "/api/queue/resume", {}, timeout=20.0)
        if action in ("queue_clear", "reap", "sweep"):
            return self._http_request("POST", "/api/ops", {"action": action}, timeout=30.0)
        if action == "confirmations":
            return self._http_request("GET", "/api/confirmations", timeout=15.0)
        if action in ("confirm_approve", "confirm_deny"):
            return self._http_request("POST", "/api/confirmations", {
                "action": "approve" if action == "confirm_approve" else "deny",
                "requestId": p.get("requestId"),
                "remember": p.get("remember"),
                "note": p.get("note"),
                "source": "mcp",
            }, timeout=30.0)
        if action == "subscriptions":
            return self._http_request("GET", "/api/subscriptions", timeout=15.0)
        raise ValueError(f"Unknown bridge_ops action: {action}")

    def publish_event(self, kind: str, detail: dict = None, level: str = "info") -> dict:
        """Push one MCP-side event into the bridge's telemetry + audit chain.

        The dispatcher can live in `server.py` (embedded / direct dispatch) or in
        a standalone `mcp_server.py` attached over HTTP, so both paths exist.
        Observability must never break a tool call, so failures are swallowed.
        """
        body = {"kind": kind, "detail": detail or {}, "level": level}
        try:
            if self.direct_dispatch:
                return self.direct_dispatch("mcp_event", body)
            return self._http_request("POST", "/api/mcp/events", body, timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": "event_sink_unavailable", "error": str(exc)[:180]}

    def confirmations_action(self, payload: dict = None) -> dict:
        """Read or answer the pending-confirmation registry."""
        payload = dict(payload or {})
        action = (payload.get("action") or "status").lower()
        if self.direct_dispatch:
            return self.direct_dispatch("confirmations", payload)
        if action == "status":
            return self._http_request("GET", "/api/confirmations", timeout=15.0)
        return self._http_request("POST", "/api/confirmations", payload, timeout=30.0)

    def subscriptions_status(self) -> dict:
        if self.direct_dispatch:
            return self.direct_dispatch("subscriptions", {})
        return self._http_request("GET", "/api/subscriptions", timeout=15.0)

    def _http_request_text(self, path: str, timeout: float = 20.0) -> str:
        url = f"{self.base_url}{path}"
        headers = {"X-Arena-Token": self.token} if self.token else {}
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.read().decode("utf-8", "replace")[:2000]
        except urllib.error.URLError as exc:
            raise BridgeTransportError(f"Cannot reach bridge at {self.base_url} ({exc.reason})") from exc

    def get_job(self, job_id: str) -> dict:
        if self.direct_dispatch:
            return self.direct_dispatch("get_job", {"id": job_id})
        return self._http_request("GET", f"/api/jobs/{job_id}", timeout=10.0)

    def snapshots_action(self, action: str, payload: dict = None) -> dict:
        if self.direct_dispatch:
            return self.direct_dispatch("snapshots", {"action": action, **(payload or {})})
        if action in ("list", "get"):
            suffix = f"/{payload['id']}" if action == "get" and payload and payload.get("id") else ""
            return self._http_request("GET", f"/api/snapshots{suffix}", timeout=15.0)
        return self._http_request("POST", "/api/snapshots", {"action": action, **(payload or {})}, timeout=30.0)

    def submit_job(
        self,
        job_type: str,
        payload: dict = None,
        note: str = None,
        wait: float = 60.0,
        dm: str = None,
        peer_id: str = None,
        place_id: int = None,
    ) -> dict:
        payload = dict(payload or {})
        effective_place = place_id if place_id is not None else self.default_place_id
        requested_dm = dm or "auto"
        if job_type != "playtest" or payload.get("action") != "stop":
            try:
                peer_rows = self.peers().get("peers", [])
                def is_target(p):
                    if requested_dm in ("auto", ""):
                        return True
                    if p.get("dm") == requested_dm:
                        return True
                    return requested_dm == "client" and p.get("dm") == "server" and (p.get("hasClient") or int(p.get("playerCount", 0)) > 0)
                live = [p for p in peer_rows if is_target(p) and p.get("online") is True
                        and float(p.get("ageSeconds", 0.0)) <= 5.0 and (not peer_id or p.get("peerId") == peer_id)]
            except Exception:
                live = []
            if not live:
                return {"status": "error", "reason": "no_client_peer" if requested_dm == "client" else "no_peer", "dm": requested_dm,
                        "error": f"No live {requested_dm} DataModel peer is connected."}
        body = {
            "type": job_type,
            "payload": payload,
            "note": note or f"mcp:{job_type}",
            "wait": wait,
        }
        # v5.4.0: tag the job with the MCP request that caused it, so a queued
        # job can be traced back to the caller and (when the client forwarded
        # OpenTelemetry context) into that caller's own spans.
        call_ctx = current_call_context()
        if call_ctx:
            if call_ctx.get("trace"):
                body["trace"] = dict(call_ctx["trace"])
            if call_ctx.get("principal"):
                body["actor"] = {
                    "principal": call_ctx["principal"],
                    "protocolVersion": call_ctx.get("requestedVersion"),
                }
        if dm and dm != "auto":
            body["dm"] = dm
        if peer_id:
            body["peer_id"] = peer_id
        if effective_place is not None:
            body["place_id"] = effective_place

        if self.direct_dispatch:
            job = self.direct_dispatch("submit_job", body)
        else:
            job = self._http_request("POST", "/api/jobs", body, timeout=max(wait + 15.0, 30.0))

        # Automatic v2.0 plugin compatibility shim:
        # If an already-open Studio instance still runs v2.0 without restarting,
        # transparently execute newly added handlers via run_luau!
        if (
            job.get("status") == "error"
            and isinstance(job.get("error"), str)
            and job["error"].startswith("unknown job type:")
            and job_type != "run_luau"
        ):
            fallback_lua = _build_legacy_v2_luau_shim(job_type, payload)
            if fallback_lua:
                shim_job = self.submit_job(
                    "run_luau",
                    {"code": fallback_lua},
                    note=f"v2-shim:{job_type}",
                    wait=wait,
                    dm=dm,
                    peer_id=peer_id,
                    place_id=effective_place,
                )
                if shim_job.get("status") == "done":
                    raw_ret = (shim_job.get("result") or {}).get("returned", "{}")
                    try:
                        shim_job["result"] = json.loads(raw_ret)
                    except Exception:
                        shim_job["result"] = {"returned": raw_ret}
                    return shim_job

        return job


def _build_legacy_v2_luau_shim(job_type: str, payload: dict):
    """Fallback Luau generator for Studio instances still running plugin v2.0."""
    p_json = json.dumps(payload)
    if job_type == "studio_state":
        return (
            "local H = game:GetService('HttpService')\n"
            "local RS = game:GetService('RunService')\n"
            "local Sel = game:GetService('Selection')\n"
            "return H:JSONEncode({place={name=game.Name,placeId=game.PlaceId,gameId=game.GameId},"
            "pluginVersion='2.0-compat',isEdit=RS:IsEdit(),isRunning=RS:IsRunning(),"
            "selectionCount=#Sel:Get()})"
        )
    if job_type == "search_tree":
        return (
            f"local H = game:GetService('HttpService')\n"
            f"local p = H:JSONDecode({json.dumps(p_json)})\n"
            "local matches = {}\n"
            "local q = p.query and string.lower(p.query) or nil\n"
            "for _, d in ipairs(game:GetDescendants()) do\n"
            "  local ok = true\n"
            "  if p.className and d.ClassName ~= p.className then ok = false end\n"
            "  if ok and p.isA and not d:IsA(p.isA) then ok = false end\n"
            "  if ok and q and not string.find(string.lower(d.Name), q, 1, true) then ok = false end\n"
            "  if ok then\n"
            "    table.insert(matches, {name=d.Name, className=d.ClassName, path=d:GetFullName(), childCount=#d:GetChildren()})\n"
            "    if #matches >= (p.limit or 60) then break end\n"
            "  end\n"
            "end\n"
            "return H:JSONEncode({matches=matches, count=#matches})"
        )
    if job_type == "script_grep":
        return (
            f"local H = game:GetService('HttpService')\n"
            f"local p = H:JSONDecode({json.dumps(p_json)})\n"
            "local out = {}\n"
            "for _, s in ipairs(game:GetDescendants()) do\n"
            "  if s:IsA('LuaSourceContainer') then\n"
            "    local okS, src = pcall(function() return s.Source end)\n"
            "    if okS and src then\n"
            "      local ln = 0\n"
            "      for line in string.gmatch(src .. '\\n', '(.-)\\n') do\n"
            "        ln = ln + 1\n"
            "        if string.find(line, p.pattern, 1, p.isPattern ~= true) then\n"
            "          table.insert(out, {path=s:GetFullName(), className=s.ClassName, line=ln, text=line})\n"
            "          if #out >= (p.maxMatches or 50) then break end\n"
            "        end\n"
            "      end\n"
            "    end\n"
            "  end\n"
            "  if #out >= (p.maxMatches or 50) then break end\n"
            "end\n"
            "return H:JSONEncode({pattern=p.pattern, matches=out, count=#out})"
        )
    return None


# ---------------------------------------------------------------------------
# Tool Schema & Annotations Helpers
# ---------------------------------------------------------------------------

def _ann(title: str, read_only=False, destructive=False, idempotent=False):
    return {
        "title": title,
        "readOnlyHint": bool(read_only),
        "destructiveHint": bool(destructive),
        "idempotentHint": bool(idempotent),
        "openWorldHint": False,
    }


DM_PROP = {
    "type": "string",
    "enum": ["auto", "edit", "server", "client"],
    "default": "auto",
    "description": "Target Roblox DataModel: 'edit' (persistent place editing), 'server' (live playtest server), 'client' (live playtest player client), or 'auto'.",
}

PEER_PROP = {
    "type": "string",
    "description": "Optional specific Studio peer ID returned by list_studios when multiple Studio windows are open.",
}


TOOL_SPECS = [
    # --- Group A: Session, Peers & Studio State ---
    {
        "name": "get_studio_state",
        "description": "Get comprehensive live Roblox Studio state: connected Edit/Server/Client peers, active place name & PlaceId, running playtest status, players, memory (MB), physics FPS, camera, selection count, and undo/redo availability.",
        "inputSchema": {
            "type": "object",
            "properties": {"dm": DM_PROP, "peerId": PEER_PROP},
        },
        "annotations": _ann("Get Studio State", read_only=True, idempotent=True),
    },
    {
        "name": "list_studios",
        "description": "List all Roblox Studio instances and DataModel peers currently connected to the bridge (including Edit mode and running Play Server / Client peers).",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": _ann("List Connected Studios", read_only=True, idempotent=True),
    },
    {
        "name": "ping_studio",
        "description": "Measure live round-trip latency to Roblox Studio and verify pluginVersion, DataModel context, place name, and PlaceId.",
        "inputSchema": {
            "type": "object",
            "properties": {"dm": DM_PROP, "peerId": PEER_PROP},
        },
        "annotations": _ann("Ping Studio", read_only=True, idempotent=True),
    },

    # --- Group B: Exploration, Search & Comparison ---
    {
        "name": "survey_place",
        "description": "Perform a complete architectural survey of the open Roblox place in one round trip: service stats, class census, every script (with FNV-1a hash, line count, and budgeted source), RemoteEvents/Functions, top-level Workspace models + bounding dimensions, GUI hierarchy, CollectionService tags, and Lighting/Terrain settings.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "includeSource": {"type": "boolean", "default": True, "description": "Include script sources up to maxScriptBytes budget."},
                "maxScriptBytes": {"type": "integer", "default": 120000, "description": "Maximum combined script source bytes returned."},
                "maxBytesPerScript": {"type": "integer", "default": 14000, "description": "Per-script truncation cap during full survey."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Survey Whole Place", read_only=True, idempotent=True),
    },
    {
        "name": "inspect_instance",
        "description": "Inspect a specific Instance by dot/slash path (e.g. 'Workspace.Door' or 'ServerScriptService.NightLoop'). Returns readable properties (raw + typed), custom Attributes, CollectionService Tags, script hash/line count, child hierarchy up to depth, and optional descendant class breakdown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "game", "description": "Instance dot path, e.g. 'Workspace.Model' or 'ServerScriptService.NightLoop'."},
                "depth": {"type": "integer", "default": 2, "minimum": 0, "maximum": 10},
                "maxChildren": {"type": "integer", "default": 100, "minimum": 1, "maximum": 500},
                "descendantSummary": {"type": "boolean", "default": False, "description": "Include total count and class histogram of all descendants."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Inspect Instance", read_only=True, idempotent=True),
    },
    {
        "name": "search_game_tree",
        "description": "Search the DataModel hierarchy with combinable filters: root path, name/class keyword, exact className, inherited class ('isA', e.g. 'BasePart' or 'LuaSourceContainer'), CollectionService tag, custom attribute name, or property=value match.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "root": {"type": "string", "default": "game", "description": "Subtree root path to search within."},
                "query": {"type": "string", "description": "Case-insensitive substring matched against Instance Name or ClassName."},
                "className": {"type": "string", "description": "Exact ClassName filter, e.g. 'RemoteEvent' or 'ModuleScript'."},
                "isA": {"type": "string", "description": "Inheritance filter via Instance:IsA(), e.g. 'BasePart', 'LuaSourceContainer', 'GuiObject'."},
                "tag": {"type": "string", "description": "Only return instances carrying this CollectionService tag."},
                "attribute": {"type": "string", "description": "Only return instances defining this custom attribute."},
                "property": {"type": "string", "description": "Property name to test, e.g. 'Anchored' or 'Material'."},
                "propertyValue": {"type": "string", "description": "Optional stringified value required on 'property'."},
                "maxDepth": {"type": "integer", "default": 12, "minimum": 1, "maximum": 30},
                "limit": {"type": "integer", "default": 60, "minimum": 1, "maximum": 500},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Search Game Tree", read_only=True, idempotent=True),
    },
    {
        "name": "get_properties",
        "description": "Read specific or common readable properties (both string display and typed JSON representations) across one or multiple Instance paths in a single batch.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "Instance dot paths to read."},
                "properties": {"type": "array", "items": {"type": "string"}, "description": "Optional list of property names (defaults to standard readable set)."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["paths"],
        },
        "annotations": _ann("Get Instance Properties", read_only=True, idempotent=True),
    },
    {
        "name": "compare_instances",
        "description": "Diff two live Instances side-by-side: compares readable properties, custom attributes, script hashes/lengths, and missing/extra child names.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pathA": {"type": "string", "description": "First instance path."},
                "pathB": {"type": "string", "description": "Second instance path."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["pathA", "pathB"],
        },
        "annotations": _ann("Compare Two Instances", read_only=True, idempotent=True),
    },

    # --- Group C: Script Reading, Multi-Edit, Search, Grep & Fresh Require ---
    {
        "name": "script_read",
        "description": "Read live Luau source from a Script, LocalScript, or ModuleScript (reads active Script Editor buffer when open). Supports optional startLine/endLine slicing, line numbering, and returns the exact 32-bit FNV-1a hash for optimistic concurrency.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Dot path to script, e.g. 'ServerScriptService.NightLoop.Config'."},
                "startLine": {"type": "integer", "minimum": 1, "description": "Optional 1-based start line."},
                "endLine": {"type": "integer", "minimum": 1, "description": "Optional 1-based inclusive end line."},
                "withLineNumbers": {"type": "boolean", "default": False},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["path"],
        },
        "annotations": _ann("Read Script Source", read_only=True, idempotent=True),
    },
    {
        "name": "read_scripts_batch",
        "description": "Read multiple scripts in one round trip with per-script FNV-1a hashes, line counts, and configurable maxBytes budget.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "maxBytes": {"type": "integer", "default": 200000},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["paths"],
        },
        "annotations": _ann("Read Multiple Scripts", read_only=True, idempotent=True),
    },
    {
        "name": "write_script",
        "description": "Create or overwrite a Script, LocalScript, or ModuleScript using ScriptEditorService:UpdateSourceAsync (safe past 200 KB and live open editor tabs). Runs local luau_check pre-validation by default, supports expectedHash optimistic locking, and supports replaceInstance=true to bust Studio's ModuleScript require() cache.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "parent": {"type": "string", "description": "Parent dot path, e.g. 'ServerScriptService.NightLoop'."},
                "name": {"type": "string", "description": "Script instance name without extension."},
                "className": {"type": "string", "enum": ["Script", "LocalScript", "ModuleScript"], "default": "ModuleScript"},
                "source": {"type": "string", "description": "Complete Luau source code."},
                "expectedHash": {"type": "string", "description": "Optional 8-char FNV-1a hex hash from script_read to prevent stale overwrites."},
                "replaceInstance": {"type": "boolean", "default": False, "description": "Destroy & recreate the script instance so Studio's ModuleScript require() cache refreshes."},
                "validateSyntax": {"type": "boolean", "default": True, "description": "Pre-check syntax & undefined globals via luau_check before writing."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["parent", "name", "source"],
        },
        "annotations": _ann("Write Script", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "multi_edit",
        "description": "Apply multiple ordered edits atomically to a script in one undo step. Each edit specifies either {oldText, newText, replaceAll?} OR {startLine, endLine, newText}. Validates ambiguity & hashes up front so partial broken edits never happen.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Dot path to target script, e.g. 'ServerScriptService.NightLoop.Director'."},
                "edits": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "oldText": {"type": "string"},
                            "newText": {"type": "string"},
                            "replaceAll": {"type": "boolean", "default": False},
                            "startLine": {"type": "integer", "minimum": 1},
                            "endLine": {"type": "integer", "minimum": 0},
                        },
                        "required": ["newText"],
                    },
                },
                "createIfMissing": {"type": "boolean", "default": False},
                "className": {"type": "string", "enum": ["Script", "LocalScript", "ModuleScript"], "default": "ModuleScript"},
                "expectedHash": {"type": "string", "description": "Optional FNV-1a hex hash guard."},
                "replaceInstance": {"type": "boolean", "default": False, "description": "Recreate instance after edit to clear ModuleScript require() cache."},
                "validateSyntax": {"type": "boolean", "default": True, "description": "Verify resulting source passes local Luau compiler check."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["path", "edits"],
        },
        "annotations": _ann("Atomic Multi-Edit Script", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "script_search",
        "description": "Fuzzy and substring search across all scripts by name or path, ranked by relevance score with line counts, byte sizes, and FNV-1a hashes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": "", "description": "Fuzzy script name or path query (empty returns all scripts)."},
                "root": {"type": "string", "description": "Optional root path scope."},
                "limit": {"type": "integer", "default": 25, "minimum": 1, "maximum": 200},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Fuzzy Search Scripts", read_only=True, idempotent=True),
    },
    {
        "name": "script_grep",
        "description": "Search across all live script sources in Studio (literal substring or Lua string pattern) with surrounding context lines.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text or Lua pattern to search for across script sources."},
                "root": {"type": "string", "description": "Optional subtree path filter, e.g. 'ServerScriptService.NightLoop'."},
                "isPattern": {"type": "boolean", "default": False, "description": "Interpret 'pattern' as a Lua string pattern instead of plain literal text."},
                "caseSensitive": {"type": "boolean", "default": True},
                "contextLines": {"type": "integer", "default": 1, "minimum": 0, "maximum": 10},
                "maxMatches": {"type": "integer", "default": 50, "minimum": 1, "maximum": 500},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["pattern"],
        },
        "annotations": _ann("Grep Across Scripts", read_only=True, idempotent=True),
    },
    {
        "name": "find_and_replace_scripts",
        "description": "Search and replace literal text across all scripts under root in a single undoable transaction. Supports dryRun=true to preview affected files & match counts safely first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "find": {"type": "string", "minLength": 1},
                "replace": {"type": "string"},
                "root": {"type": "string", "description": "Optional subtree scope, e.g. 'ServerScriptService.NightLoop'."},
                "dryRun": {"type": "boolean", "default": False, "description": "Preview matches without modifying any scripts."},
                "replaceInstance": {"type": "boolean", "default": False, "description": "Recreate modified ModuleScripts to bust require() cache."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["find", "replace"],
        },
        "annotations": _ann("Find & Replace Across Scripts", read_only=False, destructive=True, idempotent=False),
    },
    {
        "name": "open_script_in_editor",
        "description": "List open tabs in Roblox Studio's Script Editor (listOnly=true) or open a specific script at a target line number.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Script dot path to open (required unless listOnly=true)."},
                "line": {"type": "integer", "default": 1, "minimum": 1},
                "listOnly": {"type": "boolean", "default": False},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Open or List Script Editor Tabs", read_only=True, idempotent=True),
    },
    {
        "name": "require_fresh",
        "description": "Clone a ModuleScript (and its enclosing package tree so relative sibling requires resolve cleanly too) into an unarchivable sandbox, require() the fresh copy bypassing Studio's stale ModuleScript cache, optionally run a probe function(MODULE, MODULE_INSTANCE, PACKAGE_ROOT), return the serialized result, and clean up automatically.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Dot path to ModuleScript, e.g. 'ServerScriptService.NightLoop.Config'."},
                "packageRoot": {"type": "string", "description": "Optional ancestor package root folder to clone together (auto-detected if omitted)."},
                "code": {"type": "string", "description": "Optional Luau probe body receiving (MODULE, MODULE_INSTANCE, PACKAGE_ROOT), e.g. 'return MODULE.Entities.WindowMonster'."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["path"],
        },
        "annotations": _ann("Require Fresh Module (Bypass Cache)", read_only=True, idempotent=True),
    },

    # --- Group D: Luau Execution & Playtest Automation ---
    {
        "name": "execute_luau",
        "description": "Execute arbitrary Luau code inside Studio's Edit, Play Server, or Play Client DataModel. Tables returned from Luau are automatically serialized to structured JSON; print/warn logs are captured.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Luau source code to execute."},
                "dm": DM_PROP,
                "playerName": {"type": "string", "description": "Optional player name when dm='client' in multi-client tests."},
                "timeout": {"type": "number", "default": 60, "minimum": 1, "maximum": 120},
                "validateSyntax": {"type": "boolean", "default": True, "description": "Run local syntax check before executing."},
                "peerId": PEER_PROP,
            },
            "required": ["code"],
        },
        "annotations": _ann("Execute Luau Code", read_only=False, destructive=True, idempotent=False),
    },
    {
        "name": "start_stop_play",
        "description": "Start a non-blocking Studio playtest ('play' = Play Solo F5 with client character, 'run' = Server Simulation F8), 'stop' an active session cleanly via StudioTestService:EndTest, or query 'status'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["start_play", "start_run", "stop", "status"], "default": "status"},
                "waitForPeer": {"type": "boolean", "default": True, "description": "Wait briefly for the Play Server peer to connect on start."},
                "peerId": PEER_PROP,
            },
            "required": ["action"],
        },
        "annotations": _ann("Start or Stop Playtest", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "run_playtest_scenario",
        "description": "Automated end-to-end playtest runner: starts Run or Play Solo mode, waits for the live Server DataModel peer, optionally runs serverCode and/or clientCode assertions over runtime state, captures console errors & warnings, stops the playtest cleanly, and returns a complete verification report.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["run", "play"], "default": "run"},
                "warmupSeconds": {"type": "number", "default": 1.5, "minimum": 0, "maximum": 30},
                "serverCode": {"type": "string", "description": "Optional Luau verification code executed in the live Play Server VM."},
                "clientCode": {"type": "string", "description": "Optional Luau verification code executed in the live Play Client VM (mode='play')."},
                "playerName": {"type": "string", "description": "Optional target player name for the clientCode probe."},
                "inputSteps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Optional real input delivered to the Play Client (same step shapes as `simulate_input`) "
                        "after warmup and before the probes — so a scenario can press E, click a button or type "
                        "into a TextBox instead of only reading state. Failures become warnings, never silence."
                    ),
                },
                "inputHoldMs": {"type": "number", "default": 60, "description": "Down-time per click/key press for inputSteps."},
                "inputSettleMs": {"type": "number", "default": 250, "description": "Pause after inputSteps so handlers run before the probes."},
                "deviceOrientation": {
                    "type": "string",
                    "enum": ["Landscape", "Portrait", "LandscapeLeft", "LandscapeRight"],
                    "description": (
                        "Optional: force the Studio Device Simulator to this orientation for the scenario (and "
                        "restore it afterwards), which is how you catch mobile-orientation layout regressions."
                    ),
                },
                "timeout": {"type": "number", "default": 45, "minimum": 5, "maximum": 120},
                "keepAlive": {"type": "boolean", "default": True, "description": "Keep the play session running after the scenario; call start_stop_play(stop) explicitly to end it."},
                "autoRestart": {"type": "boolean", "default": False, "description": "Restart the session once if the server peer disappears before probes complete."},
                "consoleLimit": {"type": "integer", "default": 200, "minimum": 1, "maximum": 2000, "description": "Maximum game log entries included in the scenario console slice."},
                "consoleSinceIndex": {"type": "integer", "default": 0, "minimum": 0, "description": "Incremental game-log cursor passed to LogService output retrieval."},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Run Automated Playtest Scenario", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "get_console_output",
        "description": "Read Studio / Playtest Output logs via LogService with optional severity filter ('Error', 'Warning', 'Output'), substring filter, DataModel target, and incremental sinceIndex cursor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 120, "minimum": 1, "maximum": 2000},
                "level": {"type": "string", "enum": ["all", "Error", "Warning", "Output", "Info"], "default": "all"},
                "source": {"type": "string", "enum": ["game", "bridge", "all"], "default": "game", "description": "Log origin filter; excludes ArenaBridge diagnostic chatter by default."},
                "search": {"type": "string", "description": "Optional case-insensitive substring filter."},
                "sinceIndex": {"type": "integer", "default": 0, "minimum": 0, "description": "Pass previous nextIndex to fetch only new log entries."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Get Console Output", read_only=True, idempotent=True),
    },

    # --- Group E: Instance Building, Cloning, Moving & Properties ---
    {
        "name": "build_tree",
        "description": "Create or replace an entire hierarchy of Instances (Parts, Models, UI, Remotes, Scripts with sources, custom attributes, tags) under 'parent' in one atomic ChangeHistoryService undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "parent": {"type": "string", "default": "Workspace"},
                "tree": {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "object"},
                    "description": "Array of node specs: {name, className, replaceExisting?, properties?, attributes?, tags?, source?, children?}.",
                },
                "select": {"type": "boolean", "default": True},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["tree"],
        },
        "annotations": _ann("Build Instance Tree", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "set_properties",
        "description": "Set properties on an Instance with automatic coercion for Vector3 ([x,y,z]), Color3 ([r,g,b] or '#RRGGBB'), CFrame, UDim2, Enum ('Material.Neon'), NumberRange, BrickColor, and Instance refs ({'__t':'Instance','v':'Workspace.Part'}). Reports exact applied and failed keys.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "properties": {"type": "object"},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["path", "properties"],
        },
        "annotations": _ann("Set Instance Properties", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "mass_set_properties",
        "description": "Set properties and/or apply relative deltas (e.g. relative={'Position':[0,10,0]}) across many Instances in one single Ctrl+Z undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "properties": {"type": "object", "description": "Absolute property values to apply to every path."},
                "relative": {"type": "object", "description": "Relative additive deltas (numbers or [x,y,z] vectors)."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["paths"],
        },
        "annotations": _ann("Mass Set Properties", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "clone_instance",
        "description": "Deep-clone an Instance once or in a smart multi-copy offset array (count + [x,y,z] offset step + name pattern with '{i}') inside one undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "parent": {"type": "string", "description": "Destination parent path (defaults to source parent)."},
                "newName": {"type": "string", "description": "Optional name or pattern like 'Pillar_{i}'."},
                "count": {"type": "integer", "default": 1, "minimum": 1, "maximum": 500},
                "offset": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "Per-copy world [dx, dy, dz] step."},
                "properties": {"type": "object", "description": "Optional property overrides applied to each clone."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["path"],
        },
        "annotations": _ann("Clone / Smart-Duplicate Instance", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "reparent_or_rename",
        "description": "Move one or more Instances to a new parent cleanly without destroying/recreating them, optionally preserving world Pivot CFrame and/or renaming.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "newParent": {"type": "string", "description": "Destination parent dot path."},
                "newName": {"type": "string", "description": "Optional new Name (when moving/renaming a single instance)."},
                "preserveWorldPosition": {"type": "boolean", "default": False},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["paths"],
        },
        "annotations": _ann("Reparent or Rename Instances", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "delete_instance",
        "description": "Destroy one or more Instances atomically in a single Ctrl+Z undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1, "description": "One or more Instance paths to destroy."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["paths"],
        },
        "annotations": _ann("Delete Instances", read_only=False, destructive=True, idempotent=True),
    },

    # --- Group F: Attributes & Tags ---
    {
        "name": "manage_attributes",
        "description": "Read ('get'), set/coerce ('set'), or remove ('delete') custom Roblox Instance Attributes across one or more Instance paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set", "delete"], "default": "get"},
                "paths": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "attributes": {"type": "object", "description": "Key->value map when action='set'."},
                "keys": {"type": "array", "items": {"type": "string"}, "description": "Attribute names to remove when action='delete'."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["action", "paths"],
        },
        "annotations": _ann("Manage Instance Attributes", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "manage_tags",
        "description": "Manage CollectionService tags: 'list_all' (all tags + counts), 'find_tagged' (all instances carrying tag), 'get' (tags on paths), 'add', or 'remove'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list_all", "find_tagged", "get", "add", "remove"], "default": "list_all"},
                "tag": {"type": "string", "description": "Single tag name for find_tagged/add/remove."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Multiple tag names for add/remove."},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Instance paths for get/add/remove."},
                "limit": {"type": "integer", "default": 100},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["action"],
        },
        "annotations": _ann("Manage CollectionService Tags", read_only=False, destructive=False, idempotent=True),
    },

    # --- Group G: Selection, Camera, Undo/Redo, Terrain & Safe Asset Insert ---
    {
        "name": "manage_selection",
        "description": "Get currently selected Instances in Studio ('get'), replace selection ('set'), append ('add'), or 'clear'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set", "add", "clear"], "default": "get"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Manage Studio Selection", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "manage_camera",
        "description": "Inspect or control Workspace.CurrentCamera: 'get' CFrame/FOV, 'focus' auto-framing a target Model/BasePart in the viewport, or 'set' explicit position/lookAt/fov.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "focus", "set"], "default": "get"},
                "path": {"type": "string", "description": "Target Instance path when action='focus'."},
                "position": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "lookAt": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "fov": {"type": "number", "minimum": 1, "maximum": 120},
                "distanceMultiplier": {"type": "number", "default": 1.6},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Inspect or Move Viewport Camera", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "undo_redo",
        "description": "Query ChangeHistoryService undo/redo stack ('status'), perform 'undo' or 'redo' steps, or set a named 'waypoint'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "undo", "redo", "waypoint"], "default": "status"},
                "steps": {"type": "integer", "default": 1, "minimum": 1, "maximum": 20},
                "name": {"type": "string", "description": "Waypoint label when action='waypoint'."},
                "peerId": PEER_PROP,
            },
            "required": ["action"],
        },
        "annotations": _ann("Undo / Redo History", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "edit_terrain",
        "description": "Sculpt or query Workspace.Terrain: 'materials' (list material names), 'fill_block', 'fill_ball', 'fill_cylinder', 'fill_wedge', 'replace', or 'clear' (region or whole terrain with confirmClearAll=true).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["materials", "fill_block", "fill_ball", "fill_cylinder", "fill_wedge", "replace", "clear"], "default": "materials"},
                "material": {"type": "string", "default": "Grass"},
                "sourceMaterial": {"type": "string", "description": "Source material for action='replace'."},
                "targetMaterial": {"type": "string", "description": "Target material for action='replace'."},
                "position": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "default": [0, 0, 0]},
                "rotation": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "default": [0, 0, 0]},
                "size": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "default": [32, 8, 32]},
                "radius": {"type": "number", "default": 16},
                "height": {"type": "number", "default": 16},
                "region": {"type": "object", "description": "Optional {center:[x,y,z], size:[x,y,z]} for scoped action='clear'."},
                "confirmClearAll": {"type": "boolean", "default": False},
                "peerId": PEER_PROP,
            },
            "required": ["action"],
        },
        "annotations": _ann("Edit Workspace Terrain", read_only=False, destructive=True, idempotent=False),
    },
    {
        "name": "insert_asset",
        "description": "Insert a Roblox asset by numeric assetId into parent. Scans all inserted descendants for embedded scripts and strips them automatically by default (stripScripts=true) to guard against malicious free-model scripts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "assetId": {"type": "integer", "minimum": 1},
                "parent": {"type": "string", "default": "Workspace"},
                "name": {"type": "string"},
                "position": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
                "stripScripts": {"type": "boolean", "default": True, "description": "Strip embedded Script/LocalScript/ModuleScript instances from inserted asset for safety."},
                "peerId": PEER_PROP,
            },
            "required": ["assetId"],
        },
        "annotations": _ann("Insert Roblox Asset Safely", read_only=False, destructive=False, idempotent=False),
    },

    # --- Group H: Local Luau Toolchain, Drift Audit, Package Sync & Snapshots ---
    {
        "name": "luau_check",
        "description": "Offline local pre-push verifier: compiles Luau code via luau-compile AND detects genuine undefined globals (filtering standard Roblox API globals) + unused locals via luau-analyze. Works on inline 'code' or a local file/directory 'path' without needing Studio open.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Inline Luau source string to validate."},
                "path": {"type": "string", "description": "Local file or directory path resolved on the bridge host (not the MCP client)."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Check Luau Syntax & Undefined Globals", read_only=True, idempotent=True),
    },
    {
        "name": "drift_and_sync",
        "description": "Audit or sync a local Luau package folder (e.g. 'nightloop/src') against a live Studio container (e.g. 'ServerScriptService.NightLoop') using exact 53-bit-safe 32-bit FNV-1a hashes. Modes: 'check' (read-only diff report), 'push' (pre-checks syntax/globals, saves rollback snapshot, and replaces package atomically in one Ctrl+Z step busting ModuleScript caches), or 'pull' (exports Studio scripts to disk).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["check", "push", "pull"], "default": "check"},
                "localDir": {"type": "string", "description": "Local directory path, e.g. 'nightloop/src'."},
                "studioRoot": {"type": "string", "description": "Studio container path, e.g. 'ServerScriptService.NightLoop'."},
                "skipCheck": {"type": "boolean", "default": False, "description": "Skip local luau_check gate before mode='push'."},
                "peerId": PEER_PROP,
            },
            "required": ["mode", "localDir", "studioRoot"],
        },
        "annotations": _ann("Audit Drift or Sync Local Package <-> Studio", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "manage_snapshots",
        "description": "Create, list, inspect, or atomically restore in-memory multi-script snapshots stored on the bridge server before risky edits or refactors.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["create", "list", "get", "restore", "diff"], "default": "list"},
                "root": {"type": "string", "default": "game", "description": "Subtree of scripts to snapshot when action='create'."},
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Optional exact script paths or subtree roots to include; if set, root is ignored."},
                "label": {"type": "string", "description": "Human-readable name/label for snapshot."},
                "snapshotId": {"type": "string", "description": "Snapshot ID for 'get' or 'restore'."},
                "peerId": PEER_PROP,
            },
            "required": ["action"],
        },
        "annotations": _ann("Manage Script Snapshots & Rollback", read_only=False, destructive=False, idempotent=False),
    },
    # ------------------------------------------------------------------
    # 9. DIRECT IMAGE IMPORT, PROCEDURAL ASSETS & STUDIO KIT (6 tools)
    # ------------------------------------------------------------------
    {
        "name": "import_image",
        "description": "Import an image directly into Roblox Studio and the repo asset library from a local file path, base64 payload (great over tunnels), HTTPS URL, built-in repo asset, or procedural preset. Supports zero-key local Studio `rbxasset://arena_assets/...` installation, live `EditableImage` RGBA pixel transfer, Studio `CreateAssetAsync` cloud publishing, and headless Open Cloud `rbxassetid://` upload, plus optional 1-step placement onto an ImageLabel, Decal, Texture, BillboardGui, SurfaceGui, or ScreenGui.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Local file path (.png, .jpg, .jpeg, .bmp, etc.)."},
                "base64Data": {"type": "string", "description": "Base64-encoded PNG/JPEG/BMP bytes (accepts raw base64 or data:image/...;base64,... URI)."},
                "imageUrl": {"type": "string", "description": "HTTP/HTTPS URL to fetch image bytes from."},
                "repoAsset": {"type": "string", "description": "Relative path inside roblox-kit/assets/images/ (e.g. 'ui/icon_coin.png')."},
                "proceduralPreset": {"type": "string", "description": "Optional procedural preset name (e.g. 'icon_coin', 'icon_gem', 'particle_spark_star', 'grid_dark_1m')."},
                "name": {"type": "string", "description": "Clean asset name/filename stem (e.g. 'shop_banner' or 'icon_potion')."},
                "saveSubdir": {"type": "string", "default": "imported", "description": "Subfolder under roblox-kit/assets/images/ when saving to repo ('ui', 'vfx', 'textures', 'imported')."},
                "saveToRepo": {"type": "boolean", "default": True, "description": "Save image into roblox-kit/assets/images/ and update Assets.lua."},
                "useEditableImage": {"type": "boolean", "default": False, "description": "Force live RGBA buffer hydration into a Studio EditableImage via Content.fromObject."},
                "publishVia": {"type": "string", "enum": ["none", "studio", "open_cloud"], "default": "none", "description": "Optional cloud publish mode to mint a permanent `rbxassetid://<id>`."},
                "apiKey": {"type": "string", "description": "Optional Open Cloud API key override (defaults to ROBLOX_API_KEY env var)."},
                "creatorId": {"type": "string", "description": "Optional Roblox UserId or GroupId override (defaults to ROBLOX_CREATOR_ID env var)."},
                "creatorType": {"type": "string", "enum": ["user", "group"], "default": "user"},
                "targetPath": {"type": "string", "description": "Optional Studio Instance path to apply or attach the imported image to (e.g. 'Workspace.SignPart', 'StarterGui')."},
                "applyMode": {"type": "string", "enum": ["auto", "decal", "texture", "billboard", "surface_gui", "screen_gui"], "default": "auto"},
                "instanceName": {"type": "string", "description": "Name of child Decal/Texture/BillboardGui/SurfaceGui/ImageLabel created when applying."},
                "face": {"type": "string", "enum": ["Front", "Back", "Top", "Bottom", "Left", "Right"], "default": "Front"},
                "syncAssetsModule": {"type": "boolean", "default": False, "description": "Also write updated Assets.lua into ReplicatedStorage.Shared.Assets in Studio."},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Import Image Directly into Studio & Repo", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "import_images_batch",
        "description": "Batch-import all images in a directory (or explicit file list) into the repo asset vault, local Studio `rbxasset://arena_assets/...` content folder, and `Assets.lua` manifest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory containing .png/.jpg/.jpeg/.bmp files (defaults to roblox-kit/assets/images)."},
                "files": {"type": "array", "items": {"type": "string"}, "description": "Optional explicit file paths instead of scanning directory."},
                "saveSubdir": {"type": "string", "default": "imported"},
                "syncAssetsModule": {"type": "boolean", "default": True, "description": "Push regenerated Assets ModuleScript to ReplicatedStorage.Shared.Assets in Studio."},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Batch Import Image Folder into Studio", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "generate_procedural_image",
        "description": "Synthesize a crisp transparent PNG icon, UI 9-slice panel, crosshair, VFX sprite, or seamless world texture at any resolution up to 512x512, save to repo, and optionally apply in Studio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "description": "Preset name: icon_coin, icon_gem, icon_shield, icon_sword, icon_heart, icon_star, icon_bolt, icon_lock, icon_gear, icon_crown, icon_check, icon_close, panel_glass_9slice, vignette_soft, crosshair_dot, radial_ring, particle_soft_glow, particle_spark_star, particle_smoke_puff, particle_shockwave_ring, beam_energy_streak, grid_dark_1m, grid_accent_orange, hazard_stripes, brushed_metal_noise, scifi_panel_tile.",
                },
                "name": {"type": "string", "description": "Output asset name (defaults to preset name)."},
                "width": {"type": "integer", "default": 128, "minimum": 8, "maximum": 512},
                "height": {"type": "integer", "default": 128, "minimum": 8, "maximum": 512},
                "primaryColor": {"type": "string", "description": "Hex color e.g. '#FFD43B'."},
                "secondaryColor": {"type": "string", "description": "Hex color e.g. '#F08C00'."},
                "saveSubdir": {"type": "string", "default": "generated"},
                "saveToRepo": {"type": "boolean", "default": True, "description": "Save the generated PNG in roblox-kit/assets/images; false is useful for transient previews/tests."},
                "targetPath": {"type": "string", "description": "Optional Studio path to immediately apply image onto."},
                "applyMode": {"type": "string", "enum": ["auto", "decal", "texture", "billboard", "surface_gui", "screen_gui"], "default": "auto"},
                "peerId": PEER_PROP,
            },
            "required": ["preset"],
        },
        "annotations": _ann("Generate Procedural UI/VFX/Surface PNG", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "browse_kit",
        "description": "List or read built-in Roblox Studio Skills (.claude/skills), 15 reusable Luau Modules (StudUI, ModernUI, LucideIcons, CheckUIIcons, SoundFX, etc.), 14 modular Drop-In Server/Client Game Systems (roblox-kit/systems), 6 full Screen Templates, 14 Prefabs, 14 drag-and-drop .rbxmx Models, 249 Repo PNGs, and 45 CC0 WAV Sound Effects.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["all", "skills", "modules", "systems", "templates", "prefabs", "models", "images", "audio", "presets", "cloud_icons"],
                    "default": "all",
                },
                "item": {"type": "string", "description": "Optional specific skill/module/system/template/prefab/model/image/audio name to inspect."},
                "format": {"type": "string", "enum": ["summary", "full"], "default": "summary"},
                "section": {"type": "string", "enum": ["skills", "modules", "systems", "templates", "prefabs", "models", "images", "audio", "proceduralPresets"], "description": "Return one paged section; detail is intentionally opt-in."},
                "cursor": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
        },
        "annotations": _ann("Browse Skills, Modules, Systems, Templates, Prefabs, Models & Assets", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "install_kit_module",
        "description": "Install one or all 15 production-verified Luau ModuleScripts (Maid, Signal, NetGuard, TagBinder, SpringUI, StateStore, Assets, StudUI, StudMotion, CheckUIIcons, ModernUI, LucideIcons, SoundFX, FormatNumber, DataStoreSafe) into `ReplicatedStorage.Shared` (or `ReplicatedStorage`).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "default": "all",
                    "description": "Module stem or 'all'.",
                },
                "parent": {"type": "string", "default": "ReplicatedStorage.Shared", "description": "Target Studio folder/service path."},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Install Reusable Luau Kit Modules", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "spawn_prefab",
        "description": "Spawn a ready-made lighting preset, Kenney 9-slice Shop/Fantasy Dialog/Sci-Fi Visor/Input Legend ScreenGui, warm campfire, portal gate, world sign board, collectible orb VFX, starter plaza, or interaction pad from `roblox-kit/prefabs/` into Roblox Studio (auto-syncing required repo images first).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prefab": {
                    "type": "string",
                    "description": "Prefab name (e.g. 'ui_kenney_shop_window', 'ui_fantasy_quest_dialog', 'ui_scifi_hud_visor', 'ui_input_prompt_bar', 'hud_modern_shell', 'lighting_cinematic_sunset', 'lighting_cyber_night', 'lighting_stylized_day', 'vfx_campfire_warm', 'vfx_portal_gate', 'vfx_collectible_orb', 'interact_pad', 'spawn_plaza', 'world_sign_board') or path to a JSON file.",
                },
                "parent": {"type": "string", "description": "Optional override parent path (defaults to prefab's defaultParent)."},
                "position": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "Optional [x, y, z] world offset applied to spawned 3D parts."},
                "name": {"type": "string", "description": "Optional override root Instance name."},
                "peerId": PEER_PROP,
            },
            "required": ["prefab"],
        },
        "annotations": _ann("Spawn 3D / UI / Lighting Prefab", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "install_template",
        "description": "Install a complete end-to-end Roblox UI screen or full multi-script game system template (`full_stud_ui_suite`, `stud_shop_window`, `stud_hud_shell`, `stud_daily_rewards`, `stud_rebirth_window`, `stud_rarity_index`), automatically installing StudUI/StudMotion/CheckUIIcons modules, generating live editable ScreenGuis in StarterGui, attaching animation controllers, and wiring Server/Client economy scripts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template": {
                    "type": "string",
                    "description": "Template name: 'full_stud_ui_suite', 'stud_shop_window', 'stud_hud_shell', 'stud_daily_rewards', 'stud_rebirth_window', or 'stud_rarity_index'.",
                },
                "peerId": PEER_PROP,
            },
            "required": ["template"],
        },
        "annotations": _ann("Install Full UI / Gameplay System Template", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "search_assets",
        "description": "Offline search across the pinned licensed asset registry. Filters by free-text name/path, asset type, category, source, SPDX licence, and provenance; returns relative paths, hashes, source revisions, local Studio URIs where applicable, and honest thumbnail-kind labels.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "default": ""}, "assetType": {"type": "string", "enum": ["any", "image", "sky", "model", "audio"], "default": "any"}, "category": {"type": "string"}, "sourceId": {"type": "string"}, "license": {"type": "string"}, "provenance": {"type": "string", "enum": ["any", "content-hash-match", "pack-license-attestation", "first-party-generated"], "default": "any"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}, "cursor": {"type": "integer", "minimum": 0, "default": 0}}, "additionalProperties": False},
        "annotations": _ann("Search Licensed Local Asset Registry", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "search_cloud_icons",
        "description": "Search 4,811 verified pre-uploaded Roblox `rbxassetid://` UI icons across 3 packs: `stud` (1,022 glossy 3D cartoon icons), `lucide` (1,894 crisp outline vector icons), and `lucide_fill` (1,895 solid filled vector icons). Works immediately in Studio and published multiplayer servers with zero uploads.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "default": "", "description": "Case-insensitive search keywords, e.g. 'shopping cart', 'shield', 'settings', 'heart', 'golden cat', 'coin', 'swords'."},
                "pack": {"type": "string", "enum": ["all", "stud", "lucide", "lucide_fill"], "default": "all", "description": "Filter icon style pack: 'stud' (glossy 3D cartoon), 'lucide' (outline vector), 'lucide_fill' (solid filled vector), or 'all'."},
                "category": {"type": "string", "description": "Optional exact category filter, e.g. 'Main', 'Item', 'Animal', 'Currency', 'Lucide', 'LucideFill'."},
                "variant": {"type": "string", "enum": ["any", "plain", "outline", "filled"], "default": "any"},
                "limit": {"type": "integer", "default": 40, "minimum": 1, "maximum": 250},
            },
        },
        "annotations": _ann("Search 4,811 Live rbxassetid:// UI Icons (Stud + Lucide + LucideFill)", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "import_audio",
        "description": "Import a `.wav`/`.ogg`/`.mp3` sound file (from local path, base64, or any of the 45 curated CC0 Kenney UI/Interface SFX in `roblox-kit/assets/audio/`) into Studio `content/arena_assets/audio/...` (`rbxasset://arena_assets/audio/...`), update `Assets.lua`, and optionally create a `Sound` instance in Studio.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repoAudio": {"type": "string", "description": "Built-in CC0 sound filename, e.g. 'click_001.wav', 'confirmation_001.wav', 'error_001.wav', 'open_001.wav'."},
                "filePath": {"type": "string", "description": "Local `.wav`/`.ogg`/`.mp3` file path."},
                "base64Data": {"type": "string", "description": "Optional base64 audio bytes."},
                "name": {"type": "string", "description": "Sound asset name."},
                "targetPath": {"type": "string", "default": "SoundService", "description": "Optional Studio parent path to create a `Sound` instance inside (pass '' to skip)."},
                "volume": {"type": "number", "default": 0.6},
                "playbackSpeed": {"type": "number", "default": 1.0},
                "looped": {"type": "boolean", "default": False},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Import Sound Effect into Studio & Repo", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "import_rbxmx",
        "description": "Parse any Roblox `.rbxmx` XML model or UI file (from `filePath`, `repoModel` in `roblox-kit/models/`, or inline `xmlContent`) and build the full Instance tree (with scripts, properties, tags, and optional `[x, y, z]` world offset) directly into Studio in one undo step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Path to a `.rbxmx` file on disk."},
                "repoModel": {"type": "string", "description": "Built-in `.rbxmx` name from `roblox-kit/models/`, e.g. 'ui_kenney_shop_window.rbxmx' or 'vfx_portal_gate'."},
                "xmlContent": {"type": "string", "description": "Inline `.rbxmx` XML string."},
                "parent": {"type": "string", "default": "Workspace", "description": "Target Studio parent path (e.g. 'Workspace' or 'StarterGui')."},
                "position": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "description": "Optional [x, y, z] offset for 3D parts."},
                "stripScripts": {"type": "boolean", "default": False, "description": "Strip embedded Script/LocalScript/ModuleScript items when importing untrusted external models."},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Import Roblox .rbxmx XML Model / UI into Studio", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "export_rbxmx",
        "description": "Export any live Studio subtree (including properties, script sources, tags, and children) OR repo prefab into a standards-compliant Roblox `.rbxmx` XML file ready to drag-and-drop into any `.rbxl` place or commit to Git.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Live Studio dot path to export, e.g. 'Workspace.SpawnPlaza' or 'StarterGui.KenneyShopGui'."},
                "prefab": {"type": "string", "description": "Optional repo prefab stem instead of reading from live Studio."},
                "outputFile": {"type": "string", "description": "Destination `.rbxmx` file path (defaults to `roblox-kit/models/<name>.rbxmx`)."},
                "depth": {"type": "integer", "default": 8, "minimum": 1, "maximum": 16},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Export Studio Instance Subtree to .rbxmx XML", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "audit_ui_layout",
        "description": "Multi-device responsive Roblox UI layout solver & UX linter. Evaluates a live Studio `ScreenGui`/`StarterGui` path OR a repo UI prefab across Desktop (1920x1080), Tablet (1024x768), and Mobile (844x390), computing exact `[x, y, w, h]` bounds and flagging overflow, missing `UIScale`, `ResetOnSpawn=true`, tiny mobile buttons (<36px), sibling overlaps, and stretched 9-slice images. With `deviceSweep` the sizes come from Studio's own Device Simulator (applied, measured back, restored) instead of presets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Live Studio GUI path (e.g. 'StarterGui' or 'StarterGui.KenneyShopGui')."},
                "prefab": {"type": "string", "description": "Optional repo UI prefab name (e.g. 'ui_kenney_shop_window', 'hud_modern_shell', 'ui_fantasy_quest_dialog')."},
                "viewports": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["desktop", "tablet", "mobile"]},
                    "description": "Target viewports to evaluate (defaults to all 3: desktop, tablet, mobile).",
                },
                "deviceSweep": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Measure on the real thing: drive Studio's Device Simulator to each viewport's "
                        "resolution, read the applied size back, audit against that measured size, then "
                        "restore the simulator. Needs Studio 0.717+ and a live peer; falls back to the "
                        "derived audit with a warning when unavailable."
                    ),
                },
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Audit Multi-Device Responsive UI Layout & Bounds", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "install_dropin_system",
        "description": "Install any of the 14 modular server+shared+client production systems from `roblox-kit/systems/` (`core/data` with ProfileStore, `core/economy`, `core/format`, `core/remotes`, `ui/kit`, `liveops/codes`, `liveops/playtime-gifts`, `liveops/group-rewards`, `liveops/social-prompts`, `liveops/badges`, `monetization/gamepasses`, `monetization/offline-earnings`, `monetization/paid-random-items`, `monetization/receipts`) directly into `ReplicatedStorage.Shared`, `ServerScriptService`, and `StarterPlayer.StarterPlayerScripts`.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "system": {
                    "type": "string",
                    "description": "System path such as 'liveops/codes', 'liveops/playtime-gifts', 'monetization/offline-earnings', 'monetization/paid-random-items', 'core/data', 'ui/kit', or 'all'.",
                },
                "peerId": PEER_PROP,
            },
            "required": ["system"],
        },
        "annotations": _ann("Install Modular LiveOps / Monetization / Data System", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "capture_viewport",
        "description": "Capture a shaded visual PNG screenshot of the open Roblox place (3D Workspace geometry + Lighting mood + world grid + optional StarterGui HUD overlay, or native host window capture on Windows/macOS). Returns BOTH a native MCP `image/png` block so multimodal agents can see the scene AND structured camera/scene telemetry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "enum": ["perspective", "iso_ne", "iso_nw", "top_blueprint", "front"],
                    "default": "perspective",
                    "description": "Camera angle preset ('top_blueprint' renders an orthographic architectural floorplan).",
                },
                "includeHud": {"type": "boolean", "default": False, "description": "Composite active StarterGui ScreenGuis on top of the 3D frame. Defaults off for a deterministic world-only render."},
                "showLabels": {"type": "boolean", "default": True, "description": "Draw crisp callout labels on foreground 3D parts."},
                "camera": {"type": "object", "description": "Optional deterministic camera {position:[x,y,z], target:[x,y,z], fov:number, ortho:boolean}."},
                "size": {"type": "object", "description": "Optional output dimensions {w:240..1280,h:160..800}; width/height remain supported."},
                "overlays": {"type": "object", "description": "Optional overlay flags {bounds,labels,grid}. Unknown keys are rejected."},
                "returnInline": {"type": "boolean", "default": True, "description": "Include the PNG as a native MCP image block."},
                "width": {"type": "integer", "default": 800, "minimum": 240, "maximum": 1280},
                "height": {"type": "integer", "default": 450, "minimum": 160, "maximum": 800},
                "mode": {"type": "string", "enum": ["software", "native"], "default": "software", "description": "Use deterministic 3D+HUD software rasterizer ('software', works headless/minimized everywhere) or OS desktop capture ('native' fallback to software)."},
                "saveFile": {"type": "string", "description": "Optional path to save the rendered PNG on disk."},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Capture Visual 3D Viewport + HUD PNG", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "export_geometry",
        "description": "Export a live subtree as flat, JSON-safe part geometry records (CFrame, size, color, material, transparency, shape, tags) for offline rendering and asset analysis.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "default": "Workspace"}, "depth": {"type": "integer", "default": 10, "minimum": 1, "maximum": 30}, "include": {"type": "array", "items": {"type": "string"}, "default": ["cframe", "size", "color", "material", "transparency", "shape", "tags"]}, "dm": DM_PROP, "peerId": PEER_PROP}},
        "annotations": _ann("Export Flat Scene Geometry", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "render_offline",
        "description": "Render live Workspace geometry or a previously exported hierarchy tree into a deterministic PNG. Supply tree/lighting/gui for offline no-peer rendering; supports explicit camera, orthographic projection and HUD toggle.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "default": "Workspace"}, "tree": {"type": "object", "description": "Previously exported hierarchy tree; when supplied, no Studio peer is required."}, "lighting": {"type": "object"}, "gui": {"type": "array", "items": {"type": "object"}}, "width": {"type": "integer", "default": 800, "minimum": 240, "maximum": 1280}, "height": {"type": "integer", "default": 450, "minimum": 160, "maximum": 800}, "camera": {"type": "object"}, "includeHud": {"type": "boolean", "default": False}, "saveFile": {"type": "string"}, "dm": DM_PROP, "peerId": PEER_PROP}},
        "annotations": _ann("Render Offline Scene PNG", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "screenshot_gui",
        "description": "Render a ScreenGui hierarchy into an inline PNG. Use dm='client', path='PlayerGui' for live client absolute measurements; edit-mode StarterGui previews report derived measurements.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "description": "Defaults to StarterGui in edit or PlayerGui in a client DataModel."}, "width": {"type": "integer", "default": 800, "minimum": 240, "maximum": 1280}, "height": {"type": "integer", "default": 450, "minimum": 160, "maximum": 800}, "dm": DM_PROP, "saveFile": {"type": "string"}, "peerId": PEER_PROP}},
        "annotations": _ann("Screenshot GUI to PNG", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "mesh_capabilities",
        "description": "Probe runtime MeshPart and EditableMesh API availability in the connected Studio peer. Reports Studio version, API presence, an in-memory creation attempt, and the raw result; it never assumes EditableMesh support from a version number alone.",
        "inputSchema": {"type": "object", "properties": {"peerId": PEER_PROP}},
        "annotations": _ann("Probe Mesh and EditableMesh Runtime Capabilities", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "perf_audit",
        "description": "Estimate mobile performance risks from part/mesh counts, models, collision flags, unanchored parts, and StreamingEnabled.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "default": "Workspace"}, "peerId": PEER_PROP}},
        "annotations": _ann("Audit Scene Performance", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "batch_write",
        "description": "Validate and write multiple scripts in a single Studio job/ChangeHistory recording. Any validation or write failure cancels the single transaction.",
        "inputSchema": {"type": "object", "properties": {"files": {"type": "array", "minItems": 1, "items": {"type": "object"}}, "validateSyntax": {"type": "boolean", "default": True}, "peerId": PEER_PROP}, "required": ["files"]},
        "annotations": _ann("Atomically Write a Script Batch", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "build_biome",
        "description": "Build a deterministic, undoable tiled biome kit in Workspace from a named palette, seeded height variation, and optional simple rock props. This uses anchored parts, not Terrain voxels.",
        "inputSchema": {"type": "object", "properties": {"preset": {"type": "string", "enum": ["forest", "desert", "tundra", "volcanic", "island"], "default": "forest"}, "center": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3, "default": [0, 0, 0]}, "size": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "default": [96, 96]}, "tileSize": {"type": "integer", "minimum": 8, "maximum": 48, "default": 16}, "seed": {"type": "integer", "default": 2026}, "decorationDensity": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.08}, "name": {"type": "string", "default": "ArenaBiome"}, "parent": {"type": "string", "default": "Workspace"}, "peerId": PEER_PROP}},
        "annotations": _ann("Build Deterministic Biome Tiles", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "build_skybox",
        "description": "Create or replace a Lighting.Sky using explicit Roblox content IDs for all six cube faces; optional Atmosphere values are validated and included in the same build job.",
        "inputSchema": {"type": "object", "properties": {"faces": {"type": "object", "description": "Exactly six strings: bk, dn, ft, lf, rt, up."}, "name": {"type": "string", "default": "ArenaSky"}, "atmosphere": {"type": "object", "description": "Optional Density, Offset, Color, Decay, Glare, Haze numeric/color values."}, "peerId": PEER_PROP}, "required": ["faces"]},
        "annotations": _ann("Build a Skybox and Atmosphere", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "scatter_props",
        "description": "Deterministically scatter cloned BasePart/Model props over a disk using a seeded minimum-spacing sampler. Clones are created as one Studio job and rolled back if any placement fails.",
        "inputSchema": {"type": "object", "properties": {"sourcePath": {"type": "string"}, "parent": {"type": "string", "default": "Workspace"}, "center": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}, "radius": {"type": "number", "minimum": 1}, "count": {"type": "integer", "minimum": 1, "maximum": 500, "default": 25}, "minSpacing": {"type": "number", "minimum": 0, "default": 6}, "seed": {"type": "integer", "default": 2026}, "namePrefix": {"type": "string", "default": "Scatter"}, "stripScripts": {"type": "boolean", "default": True}, "peerId": PEER_PROP}, "required": ["sourcePath", "center", "radius"]},
        "annotations": _ann("Scatter Deterministic Prop Clones", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "probe",
        "description": "Execute Luau in Edit, Play Server, or Play Client. Play-mode execution injects a temporary Script/LocalScript, captures a nonce-tagged JSON result, and cleans up the probe.",
        "inputSchema": {"type": "object", "properties": {"code": {"type": "string"}, "dm": DM_PROP, "playerName": {"type": "string"}, "timeout": {"type": "number", "default": 30, "minimum": 1, "maximum": 120}, "peerId": PEER_PROP}, "required": ["code"]},
        "annotations": _ann("Run Safe DataModel Probe", read_only=False, destructive=True, idempotent=False),
    },
    {
        "name": "wait_for",
        "description": "Wait with bounded polling for a named peer/player condition or a bridge job to reach a terminal state.",
        "inputSchema": {"type": "object", "properties": {"peer": {"type": "string", "enum": ["edit", "server", "client"]}, "condition": {"type": "string", "enum": ["peer_online", "player_joined"]}, "jobId": {"type": "string"}, "timeout": {"type": "number", "default": 60, "minimum": 0.1, "maximum": 120}, "peerId": PEER_PROP}},
        "annotations": _ann("Wait for Peer, Player, or Job", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "self_test",
        "description": "Run the local test suite, syntax/toolchain checks, kit/asset integrity checks, and a Studio scratch write-read-delete smoke test when a live edit peer is available. Returns a per-tool pass/fail/call-count/timing matrix plus a human-readable summary.",
        "inputSchema": {"type": "object", "properties": {"includeStudioSmoke": {"type": "boolean", "default": True}, "timeout": {"type": "integer", "default": 180, "minimum": 10, "maximum": 600}}},
        "annotations": _ann("Run Bridge Self Test", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "audit_3d_scene",
        "description": "Run numerical 3D scene & physics QA on `Workspace` (or a specific Model/Folder): detects unanchored world parts (`UNANCHORED_PART`), mid-air floating geometry (`FLOATING_GEOMETRY` with exact gap studs), blocked `SpawnLocation` headroom (`SPAWN_OBSTRUCTED`), coplanar Z-fighting surfaces (`Z_FIGHTING_COPLANAR`), and extreme/degenerate sizes — with optional 1-step auto-remediation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "Workspace", "description": "Studio hierarchy root to audit."},
                "autoFixUnanchored": {"type": "boolean", "default": False, "description": "Automatically anchor all non-character unanchored parts in a single undo step."},
                "snapFloating": {"type": "boolean", "default": False, "description": "Automatically snap accidentally floating parts down onto their supporting surface."},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Audit 3D Geometry, Grounding, Physics & Spawns", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "export_place_rbxlx",
        "description": "Export the entire connected Studio place (`Workspace`, `Lighting`, `ReplicatedStorage`, `ServerScriptService`, `StarterGui`, `StarterPlayer`, `SoundService`) OR generate an offline standalone playable `.rbxlx` place XML file directly on disk (even before Roblox Studio is open!).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputFile": {"type": "string", "default": "roblox-kit/models/StarterPlace.rbxlx", "description": "Destination `.rbxlx` file path."},
                "offlineStarterPlace": {"type": "boolean", "default": False, "description": "Generate a complete standalone `.rbxlx` place from built-in kit modules, spawn plaza, lighting, and UI even if Studio is offline."},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Export Full .rbxlx Roblox Place File", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "import_place_rbxlx",
        "description": "Parse a `.rbxlx` Roblox Place XML file from disk and synchronize its services (`Workspace`, `Lighting`, `ReplicatedStorage`, `ServerScriptService`, `StarterGui`, `StarterPlayer`, `SoundService`) into the connected Studio session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filePath": {"type": "string", "description": "Path to `.rbxlx` file on disk."},
                "stripScripts": {"type": "boolean", "default": False, "description": "Strip embedded scripts when importing untrusted external place files."},
                "peerId": PEER_PROP,
            },
            "required": ["filePath"],
        },
        "annotations": _ann("Import .rbxlx Place XML into Studio", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "open_cloud_action",
        "description": "Invoke Roblox Open Cloud v1/v2 REST endpoints (`publish_place`, `datastore_list`, `datastore_get`, `datastore_set`, `messaging_publish`) using `ROBLOX_OPEN_CLOUD_API_KEY` (or explicit `apiKey`).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["publish_place", "datastore_list", "datastore_get", "datastore_set", "messaging_publish"],
                },
                "universeId": {"type": "integer", "description": "Target Roblox Universe ID."},
                "placeId": {"type": "integer", "description": "Target Place ID (for `publish_place`)."},
                "placeFile": {"type": "string", "description": "Local `.rbxlx` or `.rbxl` file path to upload for `publish_place`."},
                "versionType": {"type": "string", "enum": ["Saved", "Published"], "default": "Saved"},
                "datastoreName": {"type": "string", "description": "DataStore name."},
                "entryKey": {"type": "string", "description": "DataStore entry key."},
                "value": {"description": "JSON-serializable value for `datastore_set` or message payload for `messaging_publish`."},
                "topic": {"type": "string", "description": "Topic name for `messaging_publish`."},
                "apiKey": {"type": "string", "description": "Optional Open Cloud API key override (defaults to `ROBLOX_OPEN_CLOUD_API_KEY` env var)."},
            },
            "required": ["action", "universeId"],
        },
        "annotations": _ann("Roblox Open Cloud Publish / DataStore / Messaging", read_only=False, destructive=False, idempotent=False),
    },

    # --- Group Z: v5 multi-agent collaboration & bridge operations ----------
    {
        "name": "agent_bus",
        "description": (
            "Shared multi-agent coordination bus for this bridge (does not touch the Roblox place). "
            "Register presence, exchange messages, run a claim-based task board with leases, read the activity feed, "
            "and export a markdown handoff brief. Use it when two or more AI agents (or a human and an agent) share "
            "one Studio session: `action='register'` first, then `task_claim` before working, `task_complete` with a "
            "result, and `post` to report progress. Read actions: stats, agents, inbox, tasks, thread, feed. "
            "Writes: register, heartbeat, post, mark_read, task_create, task_claim, task_renew, task_release, "
            "task_update, task_complete, task_delete, status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "register", "heartbeat", "status", "agents", "post", "inbox", "thread", "mark_read",
                        "tasks", "task_create", "task_claim", "task_renew", "task_release", "task_update",
                        "task_complete", "task_delete", "feed", "export", "stats",
                    ],
                    "description": "Bus operation to perform.",
                },
                "agentId": {"type": "string", "description": "Your agent id (returned by register) or your registered name."},
                "createdBy": {"type": "string", "description": "Alias for agentId on `task_create`."},
                "actor": {"type": "string", "description": "Alias for agentId on `task_update`."},
                "name": {"type": "string", "description": "Agent display name for `register`."},
                "role": {"type": "string", "description": "Free-form role label, e.g. 'builder', 'qa', 'reviewer', 'human'."},
                "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Capability tags for `register`."},
                "status": {"type": "string", "enum": ["online", "busy", "idle", "offline"], "description": "Presence status for `heartbeat`/`status`."},
                "to": {"type": "string", "description": "Recipient agent id/name, or omit for a broadcast."},
                "subject": {"type": "string", "description": "Short message subject."},
                "body": {"type": "string", "description": "Message body for `post`."},
                "kind": {"type": "string", "enum": ["note", "question", "answer", "handoff", "alert", "result"], "default": "note"},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"], "default": "normal"},
                "threadId": {"type": "string", "description": "Optional thread id to group a conversation."},
                "since": {"type": "integer", "description": "Cursor for `inbox`/`feed` (use the `cursor` returned last time)."},
                "limit": {"type": "integer", "description": "Max rows to return (default 50)."},
                "unreadOnly": {"type": "boolean", "description": "Only unread messages for `inbox`."},
                "messageIds": {"type": "array", "items": {"type": "string"}, "description": "Messages to mark read."},
                "all": {"type": "boolean", "description": "Mark every message read."},
                "taskId": {"type": "string", "description": "Task id for claim/renew/release/update/complete/delete."},
                "title": {"type": "string", "description": "Task title for `task_create`."},
                "detail": {"type": "string", "description": "Task detail / acceptance criteria."},
                "assignee": {"type": "string", "description": "Optional pre-assigned agent id for `task_create`/`task_update`."},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Task tags."},
                "dependsOn": {"type": "array", "items": {"type": "string"}, "description": "Task ids that must complete before this task can be claimed."},
                "leaseSeconds": {"type": "integer", "description": "Lease duration for `task_claim`/`task_renew` (default 900)."},
                "result": {"type": "string", "description": "Outcome summary for `task_complete`."},
                "artifacts": {"type": "array", "items": {"type": "string"}, "description": "Files/URLs produced by the task."},
                "path": {"type": "string", "description": "Optional file path for `export` (markdown handoff brief)."},
                "includeDone": {"type": "boolean", "description": "Include finished tasks in `tasks` / `export`."},
            },
        },
        "annotations": _ann("Multi-Agent Coordination Bus", read_only=True, destructive=False, idempotent=False),
    },
    {
        "name": "bridge_ops",
        "description": (
            "Operate and observe the bridge itself: consolidated ops report (peers, queue, tunnel, bus, audit), "
            "Prometheus-style metrics, tamper-evident audit log verification, live event feed, and supervised "
            "public-tunnel control (start/stop/restart). Read actions: status, metrics, audit, audit_verify, events, "
            "tunnel, confirmations, subscriptions. Control: tunnel_start, tunnel_stop, tunnel_restart, queue_pause, "
            "queue_resume, queue_clear, sweep, reap, confirm_approve, confirm_deny. queue_pause stops handing jobs "
            "to Studio without dropping them. `confirmations` lists the pending human-approval requests of *this* "
            "MCP connection (the process answering the call). confirm_approve/confirm_deny answer one — but never "
            "the caller's own parked call: a connection cannot unblock itself, so approving your own request comes "
            "back as `operator_only` with the operator commands to use instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "metrics", "audit", "audit_verify", "events", "tunnel", "tunnel_start",
                             "tunnel_stop", "tunnel_restart", "queue_pause", "queue_resume",
                             "queue_clear", "sweep", "reap",
                             "confirmations", "confirm_approve", "confirm_deny", "subscriptions"],
                    "default": "status",
                },
                "requestId": {"type": "string", "description": "Confirmation requestId for confirm_approve/confirm_deny."},
                "remember": {"type": "boolean", "description": "confirm_approve only: skip the gate for this tool for a bounded window."},
                "subscriptionId": {"type": "string", "description": "subscriptions only: id shown by subscriptions/listen."},
                "note": {"type": "string", "description": "Reason recorded in the audit chain alongside a decision."},
                "limit": {"type": "integer", "description": "Rows for audit/events (default 50)."},
                "cursor": {"type": "integer", "description": "Event cursor for incremental reads."},
                "format": {"type": "string", "enum": ["json", "prometheus"], "default": "json"},
                "reason": {"type": "string", "description": "Why the queue was paused; recorded in the audit log."},
            },
        },
        "annotations": _ann("Bridge Ops, Metrics & Tunnel Control", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "studio_fleet",
        "description": (
            "Work with several Roblox Studio windows at once. `list` returns every connected DataModel with "
            "peerId, place, dm, online age, label and which one untargeted jobs would reach. `set_active` pins "
            "one Studio as the default target (the pin is skipped automatically when that Studio closes, so the "
            "queue never stalls); `clear_active` returns to the automatic edit-first routing; `label` names a "
            "Studio for humans. `broadcast` runs one read-only job on every online Studio and returns per-peer "
            "results — use it to survey or health-check the whole fleet in one call. Any other tool accepts "
            "`peerId` to target a single Studio."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "set_active", "clear_active", "label", "broadcast"],
                    "default": "list",
                },
                "peerId": {"type": "string", "description": "Target Studio peerId (from `list`)."},
                "placeId": {"type": "integer", "description": "Alternative to peerId when pinning by place."},
                "label": {"type": "string", "description": "Human label for a Studio (empty clears it)."},
                "broadcastTool": {
                    "type": "string",
                    "enum": ["ping_studio", "get_studio_state", "survey_place", "inspect_instance",
                             "script_read", "get_console_output"],
                    "default": "ping_studio",
                    "description": "Read-only tool to fan out with `broadcast`.",
                },
                "path": {"type": "string", "description": "Path argument when broadcasting a path-based tool."},
                "includeSource": {"type": "boolean", "description": "survey_place: include script sources."},
                "timeout": {"type": "number", "description": "Per-broadcast budget in seconds (default 20)."},
                "dm": {"type": "string", "enum": ["auto", "edit", "server", "client"], "default": "auto"},
            },
        },
        "annotations": _ann("Multi-Studio Fleet Control", read_only=False, destructive=False, idempotent=True),
    },

    # --- Group H: Live Playtest Control (input, devices, native tests) ------
    {
        "name": "simulate_input",
        "description": (
            "Drive a running playtest with real synthetic input through Roblox's VirtualInput API "
            "(UserInputService:CreateVirtualInput). Key presses, typed text into the focused TextBox, "
            "mouse move / click / drag / wheel, and touch taps, swipes, long-presses and pinches are "
            "delivered to the Play Client DataModel exactly like hardware, so UI hit-testing, "
            "InputBegan/InputEnded handlers, ContextActionService bindings and ProximityPrompts all fire. "
            "Steps run in order and the result is a per-step receipt (delivered / skipped / error), which "
            "makes a failed click visible instead of silent. Requires a live playtest (start_stop_play or "
            "run_playtest_scenario first) and Studio 0.720+; probe with `playtest_capabilities`. Input that "
            "would land on Roblox's own CoreGui (top bar, chat, escape menu) is refused by the engine and "
            "reported as `coregui_refused`, not swallowed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Ordered input steps (max 128). Common shapes: "
                        "{action:'press', key:'E'} | {action:'keydown'|'keyup', key:'LeftShift'} | "
                        "{action:'type', text:'hello'} | {action:'click', target:'Players.LocalPlayer.PlayerGui.Shop.Buy'} | "
                        "{action:'click', position:[640,360]} | {action:'move', position:[x,y]} | "
                        "{action:'drag', from:[x1,y1], to:[x2,y2], durationMs:400} | "
                        "{action:'wheel', amount:3} | {action:'tap', position:[x,y]} | "
                        "{action:'swipe', from:[x1,y1], to:[x2,y2]} | {action:'longpress', position:[x,y], ms:700} | "
                        "{action:'pinch', center:[x,y], from:[x1,y1], to:[x2,y2], ms:300} | {action:'wait', ms:250}."
                    ),
                },
                "holdMs": {
                    "type": "number",
                    "default": 60,
                    "description": "Milliseconds a press/click stays down before it is released (default 60).",
                },
                "settleMs": {
                    "type": "number",
                    "default": 150,
                    "description": "Pause after the last step so the game can react before the receipt is collected.",
                },
                "playerName": {
                    "type": "string",
                    "description": "Which play client to drive in a multiplayer test; omit for the only one.",
                },
                "captureAfter": {
                    "type": "boolean",
                    "default": False,
                    "description": "Attach a viewport PNG captured right after the last step.",
                },
                "timeout": {"type": "number", "default": 45, "description": "Overall budget in seconds."},
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
            "required": ["steps"],
        },
        "annotations": _ann("Simulate Real Playtest Input (VirtualInput)", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "manage_device_simulator",
        "description": (
            "Control Studio's Device Simulator through StudioDeviceSimulatorService: `list` the device "
            "presets, `status` the current one, `set_device` to emulate a phone/tablet/console, "
            "`set_orientation` to force Portrait/LandscapeLeft/LandscapeRight, `set_resolution` / "
            "`set_pixel_density` / `set_scaling_mode` to override the viewport, `create_profile` / "
            "`update_profile` / `remove_profile` to keep custom DeviceConfiguration entries on disk, and "
            "`stop` to go back to the plain Studio viewport. This turns the multi-device UI audit from "
            "geometry you computed into pixels Studio actually drew — read `screenshot_gui` or "
            "`capture_viewport` after a `set_device`, and always `stop` (or restore) when done. Requires "
            "Studio 0.717+; probe with `playtest_capabilities`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(DEVICE_SIMULATOR_ACTIONS),
                    "default": "status",
                    "description": (
                        "Which Device Simulator call to make. `set` applies whichever of "
                        "deviceId/orientation/width/height/density/scalingMode you passed in one "
                        "job, device first, because SetDeviceAsync clears the other overrides."
                    ),
                },
                "deviceId": {
                    "type": "string",
                    "description": (
                        "Device id from `list` for set_device/remove_profile, e.g. 'iPhone15Pro'. "
                        "'default' is accepted by set_device and means the same as `stop`."
                    ),
                },
                "orientation": {
                    "type": "string",
                    "enum": ["Portrait", "LandscapeLeft", "LandscapeRight"],
                    "description": "set_orientation: ScreenOrientation name; 'Landscape' is accepted as LandscapeRight.",
                },
                "width": {"type": "integer", "minimum": 1, "maximum": 7680,
                          "description": "set_resolution: landscape-space width in pixels (1..7680)."},
                "height": {"type": "integer", "minimum": 1, "maximum": 4320,
                           "description": "set_resolution: landscape-space height in pixels (1..4320)."},
                "density": {"type": "number", "minimum": 72, "maximum": 10000,
                            "description": "set_pixel_density: DPI override (72..10000). Session-level; cleared by the next set_device."},
                "scalingMode": {"type": "string", "enum": ["ScaleToPhysicalSize", "ActualResolution", "FitToWindow"],
                                 "description": "set_scaling_mode: how the simulated resolution maps to the Studio viewport."},
                "profile": {
                    "type": "object",
                    "description": (
                        "DeviceConfiguration for create_profile/update_profile. Required: Name (1-200 chars, "
                        "not 'default'), Width (1-7680), Height (1-4320), PixelDensity (72-10000). Optional: "
                        "DeviceForm ('Phone'|'Tablet'|'Desktop'|'Console'|'VR'), ResolutionScale (0-10), "
                        "PortraitKeyboardHeight, LandscapeKeyboardHeight. Example "
                        "{Name:'BudgetPhone', Width:720, Height:360, PixelDensity:160, DeviceForm:'Phone'}."
                    ),
                },
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Drive Studio's Device Simulator", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "run_studio_tests",
        "description": (
            "Run tests through Studio's own runner (StudioTestService): mode `run` = Test > Run (no game loop), "
            "`play` = Test > Play (Play Solo), `multiplayer` = one server + 1-8 simulated clients via "
            "ExecuteMultiplayerTestAsync. With `module` the bridge injects a temporary RobloxScript runner that "
            "requires that ModuleScript, awaits its `run(ctx)` (or a TestEZ/OpenTest suite), collects the report "
            "and calls StudioTestService:EndTest(report) — the runner is always destroyed afterwards, even on "
            "timeout. Without `module` Studio runs whatever its own test service discovers. `args` is forwarded "
            "and readable inside the session via GetTestArgs, `collectClientLogs` returns each client's Output "
            "tail separately, and `keepOpen` leaves the session running for follow-up `execute_luau`. "
            "Requires Studio 0.700+ (0.715+ for multiplayer); probe with `playtest_capabilities`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["run", "play", "multiplayer"], "default": "run"},
                "module": {
                    "type": "string",
                    "description": "Test ModuleScript path (e.g. 'ServerScriptService.Tests.ShopTests'). Omit to let Studio run its own discovered tests.",
                },
                "players": {"type": "integer", "minimum": 1, "maximum": 8, "default": 2, "description": "multiplayer: simulated clients (1-8)."},
                "args": {"type": "object", "description": "Value forwarded into the session (read with StudioTestService:GetTestArgs())."},
                "timeoutSec": {"type": "number", "default": 120, "description": "Hard watchdog: the session is ended and the runner cleaned up after this."},
                "collectClientLogs": {"type": "boolean", "default": True},
                "keepOpen": {"type": "boolean", "default": False, "description": "Leave the test session running after the report is collected."},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Run Native Studio Tests (StudioTestService)", read_only=False, destructive=False, idempotent=False),
    },
    {
        "name": "playtest_capabilities",
        "description": (
            "Probe what this Studio build can actually do for live playtesting instead of guessing from a version "
            "string: VirtualInput (CreateVirtualInput + the six Send* methods), StudioDeviceSimulatorService "
            "(device profiles, orientation, resolution, DPI, scaling), StudioTestService (Run / Play / "
            "AddPlayers / CanLeaveTest / LeaveTest / ExecuteMultiplayerTestAsync), whether a playtest is running, "
            "and the DataModels available to receive input. Each capability carries `available`, `security` and the "
            "exact runtime call that proved it, so `simulate_input`, `manage_device_simulator` and `run_studio_tests` "
            "are either confirmed or reported unsupported before you waste a run on them."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dm": DM_PROP,
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Probe Playtest API Capabilities", read_only=True, destructive=False, idempotent=True),
    },
]

TOOL_MAP = {t["name"]: t for t in TOOL_SPECS}


# ---------------------------------------------------------------------------
# Read-only classification per tool invocation
# ---------------------------------------------------------------------------

def is_tool_call_read_only(name: str, args: dict) -> bool:
    spec = TOOL_MAP.get(name)
    if not spec:
        return True
    if spec["annotations"].get("readOnlyHint"):
        return True
    if name == "find_and_replace_scripts" and args.get("dryRun") is True:
        return True
    if name == "manage_attributes" and args.get("action", "get") == "get":
        return True
    if name == "manage_tags" and args.get("action", "list_all") in ("list_all", "find_tagged", "get"):
        return True
    if name == "manage_selection" and args.get("action", "get") == "get":
        return True
    if name == "manage_camera" and args.get("action", "get") == "get":
        return True
    if name == "undo_redo" and args.get("action", "status") == "status":
        return True
    if name == "edit_terrain" and args.get("action") == "materials":
        return True
    if name == "start_stop_play" and args.get("action") == "status":
        return True
    if name == "drift_and_sync" and args.get("mode", "check") == "check":
        return True
    if name == "manage_snapshots" and args.get("action", "list") in ("list", "get", "diff"):
        return True
    if name == "audit_3d_scene" and not args.get("autoFixUnanchored") and not args.get("snapFloating"):
        return True
    if name == "open_cloud_action" and args.get("action") in ("datastore_list", "datastore_get"):
        return True
    if name == "manage_device_simulator" and args.get("action", "status") in ("status", "list"):
        return True
    if name == "agent_bus" and args.get("action", "stats") in (
        "stats", "agents", "inbox", "tasks", "thread", "feed", "status",
    ):
        return True
    if name == "bridge_ops" and args.get("action", "status") in (
        "status", "metrics", "audit", "audit_verify", "events", "tunnel", "subscriptions",
        "confirmations",
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# Basic JSON Schema validator (catches bad LLM args cleanly before Studio)
# ---------------------------------------------------------------------------

def validate_arguments(schema: dict, args: dict) -> str:
    if not isinstance(args, dict):
        return "Tool arguments must be a JSON object."
    for req_key in schema.get("required", []):
        if req_key not in args or args[req_key] is None:
            return f"Missing required parameter: '{req_key}'."
    props = schema.get("properties", {})
    for key, val in args.items():
        if key not in props:
            return f"Unknown parameter '{key}'. Supported parameters: {', '.join(sorted(props))}."
        if val is None:
            continue
        p = props[key]
        expected_type = p.get("type")
        if expected_type == "string" and not isinstance(val, str):
            return f"Parameter '{key}' must be a string."
        elif expected_type == "boolean" and not isinstance(val, bool):
            return f"Parameter '{key}' must be a boolean."
        elif expected_type == "integer" and (isinstance(val, bool) or not isinstance(val, int)):
            return f"Parameter '{key}' must be an integer."
        elif expected_type == "number" and (isinstance(val, bool) or not isinstance(val, (int, float))):
            return f"Parameter '{key}' must be a number."
        elif expected_type == "array" and not isinstance(val, list):
            return f"Parameter '{key}' must be an array."
        elif expected_type == "object" and not isinstance(val, dict):
            return f"Parameter '{key}' must be an object."
        if "enum" in p and val not in p["enum"]:
            return f"Parameter '{key}' must be one of {p['enum']} (got {val!r})."
    return ""


# ---------------------------------------------------------------------------
# Tool Execution Engine
# ---------------------------------------------------------------------------

def _unwrap_job(job: dict) -> dict:
    status = job.get("status")
    if status == "done":
        out = job.get("result")
        if isinstance(out, dict):
            if job.get("logs"):
                out = dict(out)
                out.setdefault("_logs", job["logs"])
            return out
        return {"result": out, "_logs": job.get("logs") or []}
    if status == "error":
        if job.get("reason") in ("no_peer", "no_client_peer"):
            raise NoPeerError(job.get("dm") or "server")
        raise RuntimeError(job.get("error") or "Studio job failed")
    raise RuntimeError(
        f"Studio did not complete job {job.get('id')} in time (status={status}). "
        "Verify Roblox Studio is open and connected."
    )


def create_snapshot_helper(client: BridgeClient, root: str = "game", label: str = None, peer_id: str = None, paths_filter: list = None) -> dict:
    hashes_job = _unwrap_job(client.submit_job("hash_scripts", {"root": root}, wait=45.0, peer_id=peer_id))
    selected_roots = [str(p).strip(".") for p in (paths_filter or []) if str(p).strip(".")]
    paths = [
        s["path"] for s in hashes_job.get("scripts", [])
        if not selected_roots or any(s["path"] == r or s["path"].startswith(r + ".") for r in selected_roots)
    ]
    scripts_data = []
    # Read in batches so large places never hit per-call truncation
    for i in range(0, len(paths), 25):
        batch = paths[i : i + 25]
        res = _unwrap_job(
            client.submit_job("read_scripts", {"paths": batch, "maxBytes": 2_000_000}, wait=60.0, peer_id=peer_id)
        )
        for item in res.get("scripts", []):
            if "error" not in item:
                scripts_data.append({
                    "path": item["path"],
                    "className": item.get("className", "ModuleScript"),
                    "hash": item.get("hash") or fnv1a_hex(item.get("source", "")),
                    "bytes": item.get("bytes", len(item.get("source", ""))),
                    "source": item.get("source", ""),
                })
    return client.snapshots_action("save", {
        "root": root,
        "label": label or f"snapshot:{root}",
        "scripts": scripts_data,
    })


def wait_for_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    deadline = time.monotonic() + max(0.1, min(float(args.get("timeout", 60)), 120.0))
    if args.get("jobId"):
        jid = args["jobId"]
        while time.monotonic() < deadline:
            job = client.get_job(jid)
            if job.get("status") in ("done", "error", "cancelled"):
                return {"ok": job.get("status") == "done", "condition": "job_terminal", "job": job, "warnings": [] if job.get("status") == "done" else [job.get("error") or job.get("status")]}
            time.sleep(0.2)
        return {"ok": False, "reason": "timeout", "condition": "job_terminal", "jobId": jid, "warnings": []}
    dm = args.get("peer") or "server"
    condition = args.get("condition", "peer_online")
    while time.monotonic() < deadline:
        rows = client.peers().get("peers", [])
        found = next((p for p in rows if p.get("dm") == dm and p.get("online") and float(p.get("ageSeconds", 0)) <= 5 and (not peer_id or p.get("peerId") == peer_id)), None)
        if found and (condition == "peer_online" or int(found.get("playerCount", 0)) > 0):
            return {"ok": True, "condition": condition, "peer": found, "elapsedSec": round(float(args.get("timeout", 60)) - max(0, deadline - time.monotonic()), 2), "warnings": []}
        time.sleep(0.2)
    return {"ok": False, "reason": "timeout", "condition": condition, "peer": dm, "warnings": []}


def run_bridge_self_test(client: BridgeClient, include_studio_smoke: bool = True, timeout: int = 180) -> dict:
    """Run isolated full regression suite plus a tightly-scoped optional Studio smoke test."""
    import sys
    test_file = Path(__file__).resolve().parent / "test_bridge_mcp.py"
    started = time.monotonic()
    try:
        child_env = os.environ.copy()
        child_env["ARENA_SELF_TEST_CHILD"] = "1"
        proc = subprocess.run([sys.executable, "-B", str(test_file)], cwd=str(test_file.parent), env=child_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=max(10, min(int(timeout), 600)))
        suite_ok = proc.returncode == 0
        summary_line = next((ln.strip() for ln in reversed(proc.stdout.splitlines()) if "Ran " in ln or "FAILED" in ln or ln.strip() == "OK"), "no unittest summary found")
        suite = {"status": "pass" if suite_ok else "fail", "returnCode": proc.returncode, "summary": summary_line, "outputTail": proc.stdout[-12000:]}
    except Exception as exc:
        suite_ok = False
        suite = {"status": "fail", "error": str(exc)}
    smoke = {"status": "skipped", "reason": "disabled" if not include_studio_smoke else "no_peer"}
    if include_studio_smoke:
        try:
            peers = client.peers().get("peers", [])
            edit_peer = next((p for p in peers if p.get("dm") == "edit" and p.get("online") and float(p.get("ageSeconds", 0)) <= 5), None)
            if edit_peer:
                name = "__ArenaSelfTest_" + str(int(time.time() * 1000))
                path = f"ServerStorage.{name}"
                try:
                    _unwrap_job(client.submit_job("write_script", {"parent": "ServerStorage", "name": name, "className": "Script", "source": "return 'self_test_ok'", "replaceInstance": True}, wait=15, dm="edit", peer_id=edit_peer["peerId"]))
                    read_back = _unwrap_job(client.submit_job("read_script", {"path": path}, wait=15, dm="edit", peer_id=edit_peer["peerId"]))
                    if "self_test_ok" not in read_back.get("source", ""):
                        raise RuntimeError("scratch script readback mismatch")
                    smoke = {"status": "pass", "cycle": "write-read-delete", "path": path}
                finally:
                    client.submit_job("delete", {"paths": [path]}, wait=10, dm="edit", peer_id=edit_peer["peerId"])
        except Exception as exc:
            smoke = {"status": "fail", "error": str(exc)}

    tested_sources = set(re.findall(r'call_tool\("([^"]+)"', test_file.read_text(encoding="utf-8")))
    test_text = test_file.read_text(encoding="utf-8")
    tested_sources.update(name for name in ("open_cloud_action", "self_test") if name in test_text)
    tool_metrics = {}
    for line in (suite.get("outputTail") or "").splitlines():
        if line.startswith("ARENA_TOOL_METRICS="):
            try:
                tool_metrics = json.loads(line[len("ARENA_TOOL_METRICS="):])
            except Exception:
                tool_metrics = {}
    tool_matrix = []
    for spec in TOOL_SPECS:
        tool_name = spec["name"]
        metric = tool_metrics.get(tool_name, {})
        if tool_name == "self_test" and tool_name in tested_sources:
            metric = {"status": "pass" if suite_ok else "fail", "calls": 1, "durationSec": round(time.monotonic() - started, 4)}
        covered = tool_name in tested_sources and metric.get("calls", 0) > 0
        status = "pass" if suite_ok and covered and metric.get("status") == "pass" else "fail"
        tool_matrix.append({"tool": tool_name, "status": status, "calls": metric.get("calls", 0), "durationSec": metric.get("durationSec", 0.0), "testedBy": "isolated_mock_regression_suite" if covered else "missing_or_unmeasured_end_to_end_fixture"})
    missing = [row["tool"] for row in tool_matrix if row["status"] != "pass"]
    tests = [suite, {"name": "studio_scratch_write_read_delete", **smoke}]
    passed = suite_ok and not missing and smoke["status"] != "fail"
    table = ["CHECK | STATUS | DETAIL", f"mock regression suite | {suite['status']} | {suite.get('summary', suite.get('error',''))}", f"Studio scratch cycle | {smoke['status']} | {smoke.get('reason', smoke.get('path', smoke.get('error','')))}", f"registered MCP tools | {'pass' if not missing else 'fail'} | {len(TOOL_SPECS)} tested; missing: {', '.join(missing) or 'none'}"]
    return {"ok": passed, "warnings": [] if smoke["status"] == "pass" else [f"Studio smoke {smoke['status']}: {smoke.get('reason', '')}"], "durationSec": round(time.monotonic() - started, 2), "suite": suite, "studioSmoke": smoke, "toolMatrix": tool_matrix, "humanTable": chr(10).join(table)}


def execute_tool(client: BridgeClient, name: str, args: dict) -> dict:
    dm = args.get("dm", "auto")
    peer_id = args.get("peerId")

    # Bridge-level tools never depend on a Studio peer.
    if name == "agent_bus":
        return client.bus_action(args)
    if name == "bridge_ops":
        return client.ops_action(args)

    if name == "self_test":
        return run_bridge_self_test(client, include_studio_smoke=args.get("includeStudioSmoke", True), timeout=args.get("timeout", 180))

    if name == "luau_check":
        result = run_luau_check(code=args.get("code"), path=args.get("path"))
        if result.get("ok") or result.get("toolchainAvailable"):
            return result
        if args.get("code") is not None:
            checked = _studio_luau_check(client, args["code"], "inline.lua", dm=dm if dm != "auto" else "edit", peer_id=peer_id)
            return {"ok": checked["ok"], "backend": checked["backend"], "filesChecked": 1, "filesFailed": 0 if checked["ok"] else 1, "results": [checked], "warnings": checked.get("warnings", [])}
        target = Path(args["path"]).expanduser().resolve()
        files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in (".lua", ".luau"))
        checked_files = []
        for file in files:
            chk = _studio_luau_check(client, file.read_text(encoding="utf-8"), str(file), dm=dm if dm != "auto" else "edit", peer_id=peer_id)
            checked_files.append(chk)
        failed = sum(not x.get("ok") for x in checked_files)
        return {"ok": failed == 0, "backend": "studio-loadstring", "filesChecked": len(checked_files), "filesFailed": failed, "results": checked_files, "warnings": [] if checked_files else ["No .lua or .luau files found."]}

    if name == "list_studios":
        return client.peers()

    if name == "studio_fleet":
        action = (args.get("action") or "list").lower()
        if action != "broadcast":
            return client.fleet_action(args)
        mapping = {
            "ping_studio": ("ping", {}),
            "get_studio_state": ("studio_state", {}),
            "survey_place": ("survey", {"includeSource": bool(args.get("includeSource", False))}),
            "inspect_instance": ("inspect", {"path": args.get("path") or "game", "depth": 2}),
            "script_read": ("read_script", {"path": args.get("path") or ""}),
            "get_console_output": ("get_console", {}),
        }
        kind, payload = mapping.get(args.get("broadcastTool") or "ping_studio", ("ping", {}))
        if kind == "read_script" and not payload.get("path"):
            raise ValueError("script_read broadcast needs a `path`")
        result = client.broadcast(kind=kind, payload=payload,
                                  timeout=float(args.get("timeout") or 20.0),
                                  dm=args.get("dm"))
        result["tool"] = args.get("broadcastTool") or "ping_studio"
        return result

    if name == "get_studio_state":
        bridge_health = client.health()
        if not bridge_health.get("studio_connected"):
            return {
                "bridge": bridge_health,
                "studioConnected": False,
                "hint": "Bridge server is live, waiting for Roblox Studio local plugin (ArenaBridge.lua) to connect.",
            }
        state_job = client.submit_job("studio_state", {}, wait=15.0, dm=dm, peer_id=peer_id)
        if state_job.get("status") == "done":
            return {
                "studioConnected": True,
                "bridge": bridge_health,
                "studio": _unwrap_job(state_job),
            }
        return {"studioConnected": bridge_health.get("studio_connected", False), "bridge": bridge_health}

    if name == "ping_studio":
        t0 = time.monotonic()
        res = _unwrap_job(client.submit_job("ping", {}, wait=20.0, dm=dm, peer_id=peer_id))
        res["roundTripMs"] = round((time.monotonic() - t0) * 1000.0, 2)
        return res

    if name == "survey_place":
        return _unwrap_job(client.submit_job("survey", {
            "includeSource": args.get("includeSource", True),
            "maxScriptBytes": args.get("maxScriptBytes", 120000),
            "maxBytesPerScript": args.get("maxBytesPerScript", 14000),
        }, wait=120.0, dm=dm, peer_id=peer_id))

    if name == "inspect_instance":
        return _unwrap_job(client.submit_job("inspect", {
            "path": args.get("path", "game"),
            "depth": args.get("depth", 2),
            "maxChildren": args.get("maxChildren", 100),
            "descendantSummary": args.get("descendantSummary", False),
        }, wait=45.0, dm=dm, peer_id=peer_id))

    if name == "search_game_tree":
        return _unwrap_job(client.submit_job("search_tree", {
            k: args[k] for k in (
                "root", "query", "className", "isA", "tag",
                "attribute", "property", "propertyValue", "maxDepth", "limit"
            ) if k in args
        }, wait=45.0, dm=dm, peer_id=peer_id))

    if name == "get_properties":
        return _unwrap_job(client.submit_job("get_properties", {
            "paths": args["paths"],
            "properties": args.get("properties"),
        }, wait=30.0, dm=dm, peer_id=peer_id))

    if name == "compare_instances":
        return _unwrap_job(client.submit_job("compare_instances", {
            "pathA": args["pathA"],
            "pathB": args["pathB"],
        }, wait=30.0, dm=dm, peer_id=peer_id))

    if name == "script_read":
        return _unwrap_job(client.submit_job("read_script", {
            k: args[k] for k in ("path", "startLine", "endLine", "withLineNumbers") if k in args
        }, wait=30.0, dm=dm, peer_id=peer_id))

    if name == "read_scripts_batch":
        return _unwrap_job(client.submit_job("read_scripts", {
            "paths": args["paths"],
            "maxBytes": args.get("maxBytes", 200000),
        }, wait=60.0, dm=dm, peer_id=peer_id))

    if name == "write_script":
        validation_warning = None
        if args.get("validateSyntax", True):
            chk = _check_with_fallback(client, args["source"], f"{args['parent']}.{args['name']}.lua", dm="edit", peer_id=peer_id)
            if chk.get("syntaxOk") is False and chk.get("reason") != "all_backends_failed":
                raise ValueError(
                    f"Pre-push Luau validation failed for {args['parent']}.{args['name']}: "
                    + json.dumps({"compileErrors": chk.get("compileErrors", []), "backend": chk.get("backend")})
                )
            if not chk.get("ok"):
                validation_warning = {"code": "syntax_validation_unavailable", "message": "All syntax-check backends failed; write proceeded without parser validation.", "detail": chk.get("warnings", [])}
        result = _unwrap_job(client.submit_job("write_script", {
            "parent": args["parent"],
            "name": args["name"],
            "className": args.get("className", "ModuleScript"),
            "source": args["source"],
            "expectedHash": args.get("expectedHash"),
            "replaceInstance": args.get("replaceInstance", False),
        }, wait=45.0, dm=dm if dm != "auto" else "edit", peer_id=peer_id))
        result["ok"] = True
        result["warnings"] = [validation_warning] if validation_warning else []
        result["validationBackend"] = chk.get("backend") if args.get("validateSyntax", True) else "skipped"
        return result

    if name == "multi_edit":
        res = _unwrap_job(client.submit_job("multi_edit", {
            "path": args["path"],
            "edits": args["edits"],
            "createIfMissing": args.get("createIfMissing", False),
            "className": args.get("className", "ModuleScript"),
            "expectedHash": args.get("expectedHash"),
            "replaceInstance": args.get("replaceInstance", False),
        }, wait=45.0, dm=dm if dm != "auto" else "edit", peer_id=peer_id))
        if args.get("validateSyntax", True):
            read_back = _unwrap_job(client.submit_job("read_script", {"path": res["path"]}, wait=20.0, dm=dm, peer_id=peer_id))
            chk = check_luau_source(read_back.get("source", ""), f"{res['path']}.lua")
            res["luauCheck"] = {
                "ok": chk["ok"],
                "compileErrors": chk["compileErrors"],
                "undefinedGlobals": chk["undefinedGlobals"],
            }
        return res

    if name == "script_search":
        return _unwrap_job(client.submit_job("script_search", {
            "query": args.get("query", ""),
            "root": args.get("root"),
            "limit": args.get("limit", 25),
        }, wait=30.0, dm=dm, peer_id=peer_id))

    if name == "script_grep":
        return _unwrap_job(client.submit_job("script_grep", {
            k: args[k] for k in (
                "pattern", "root", "isPattern", "caseSensitive", "contextLines", "maxMatches"
            ) if k in args
        }, wait=45.0, dm=dm, peer_id=peer_id))

    if name == "find_and_replace_scripts":
        dry_run = args.get("dryRun", False)
        snap_info = None
        if not dry_run:
            snap_info = create_snapshot_helper(
                client, root=args.get("root") or "game",
                label=f"auto-before-replace:{args['find'][:24]}",
                peer_id=peer_id,
            )
        out = _unwrap_job(client.submit_job("find_replace_scripts", {
            "find": args["find"],
            "replace": args["replace"],
            "root": args.get("root"),
            "dryRun": dry_run,
            "replaceInstance": args.get("replaceInstance", False),
        }, wait=60.0, dm=dm if dm != "auto" else "edit", peer_id=peer_id))
        if snap_info:
            out["snapshotId"] = snap_info.get("id")
        return out

    if name == "open_script_in_editor":
        return _unwrap_job(client.submit_job("open_script", {
            "path": args.get("path"),
            "line": args.get("line", 1),
            "listOnly": args.get("listOnly", False),
        }, wait=20.0, dm="edit", peer_id=peer_id))

    if name == "require_fresh":
        return _unwrap_job(client.submit_job("require_fresh", {
            "path": args["path"],
            "packageRoot": args.get("packageRoot"),
            "code": args.get("code"),
        }, wait=60.0, dm=dm, peer_id=peer_id))

    if name == "build_biome":
        return build_biome_helper(client, args, peer_id=peer_id)

    if name == "build_skybox":
        return build_skybox_helper(client, args, peer_id=peer_id)

    if name == "scatter_props":
        return scatter_props_helper(client, args, peer_id=peer_id)

    if name == "probe":
        aliased = dict(args)
        aliased.setdefault("validateSyntax", True)
        return execute_tool(client, "execute_luau", aliased)

    if name == "batch_write":
        files = args["files"]
        validation = []
        if args.get("validateSyntax", True):
            for entry in files:
                chk = _check_with_fallback(client, entry.get("source", ""), f"{entry.get('parent','')}.{entry.get('name','')}.lua", dm="edit", peer_id=peer_id)
                validation.append(chk)
                if chk.get("syntaxOk") is False and chk.get("reason") != "all_backends_failed":
                    raise ValueError(f"Batch validation failed before Studio write: {entry.get('name')}: {chk.get('compileErrors', [])}")
        result = _unwrap_job(client.submit_job("batch_write", {"files": files}, note="atomic_batch_write", wait=90.0, dm="edit", peer_id=peer_id))
        result["ok"] = True
        result["warnings"] = ["Validation backend unavailable for one or more files; writes proceeded without parser validation."] if any(not x.get("ok") for x in validation) else []
        result["validation"] = [{"file": x.get("file"), "ok": x.get("ok"), "backend": x.get("backend"), "errors": x.get("compileErrors", [])} for x in validation]
        return result

    if name == "execute_luau":
        validation_warning = None
        if args.get("validateSyntax", True):
            chk = _check_with_fallback(client, args["code"], "execute_luau.lua", dm="edit", peer_id=peer_id)
            if chk.get("syntaxOk") is False and chk.get("reason") != "all_backends_failed":
                raise ValueError("Luau syntax error: " + "; ".join(chk.get("compileErrors", [])))
            if not chk.get("ok"):
                validation_warning = "Syntax validation unavailable; execution proceeded without validation."

        payload = {"code": args["code"], "_timeout": float(args.get("timeout", 60.0))}
        if args.get("playerName"):
            payload["_playerName"] = args["playerName"]
        res = _unwrap_job(client.submit_job(
            "run_luau",
            payload,
            wait=float(args.get("timeout", 60.0)),
            dm=dm,
            peer_id=peer_id,
        ))
        # Parse JSON strings automatically into structured decoded field if valid JSON
        ret_str = res.get("returned")
        if isinstance(ret_str, str) and ret_str and ret_str[0] in ("{", "["):
            try:
                res["decoded"] = json.loads(ret_str)
            except Exception:
                pass
        res["ok"] = True
        res["warnings"] = [validation_warning] if validation_warning else []
        return res

    if name == "start_stop_play":
        act = args["action"]
        target_dm = "edit" if act in ("stop", "start_play", "start_run") else dm
        res = _unwrap_job(client.submit_job("playtest", {"action": act}, wait=25.0, dm=target_dm, peer_id=peer_id))
        if act in ("start_play", "start_run") and args.get("waitForPeer", True):
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                peers = client.peers().get("peers", [])
                if any(p.get("dm") == "server" and p.get("online") for p in peers):
                    res["serverPeerReady"] = True
                    break
                time.sleep(0.25)
        return res

    if name == "run_playtest_scenario":
        mode = args.get("mode", "run")
        start_act = "start_play" if mode == "play" else "start_run"
        timeout = float(args.get("timeout", 45.0))
        t_start = time.monotonic()
        phase_timings = {}
        _unwrap_job(client.submit_job("playtest", {"action": start_act}, wait=20.0, dm="edit", peer_id=peer_id))

        phase_start = time.monotonic()
        server_deadline = time.monotonic() + min(12.0, timeout / 2)
        peer_rows = []
        while time.monotonic() < server_deadline:
            peer_rows = client.peers().get("peers", [])
            if any(p.get("dm") == "server" and p.get("online") for p in peer_rows):
                break
            time.sleep(0.2)
        phase_timings["serverPeerWaitSec"] = round(time.monotonic() - phase_start, 3)

        warmup = float(args.get("warmupSeconds", 1.5))
        if warmup > 0:
            time.sleep(min(warmup, 15.0))
        phase_timings["warmupSec"] = round(min(max(warmup, 0.0), 15.0), 3)
        peer_rows = client.peers().get("peers", [])
        server_peer_seen = any(p.get("dm") == "server" and p.get("online") for p in peer_rows)
        restarted = False
        if not server_peer_seen and args.get("autoRestart", False):
            restarted = True
            _unwrap_job(client.submit_job("playtest", {"action": start_act}, wait=20.0, dm="edit", peer_id=peer_id))
            retry_deadline = time.monotonic() + min(12.0, timeout / 2)
            while time.monotonic() < retry_deadline:
                peer_rows = client.peers().get("peers", [])
                if any(p.get("dm") == "server" and p.get("online") for p in peer_rows):
                    break
                time.sleep(0.2)
        server_peer_seen = any(p.get("dm") == "server" and p.get("online") for p in peer_rows)

        client_wait_sec = 0.0
        if mode == "play" and args.get("clientCode"):
            phase_start = time.monotonic()
            client_deadline = time.monotonic() + min(12.0, timeout / 3)
            while time.monotonic() < client_deadline:
                peer_rows = client.peers().get("peers", [])
                client_seen = any(p.get("dm") == "client" and p.get("online") for p in peer_rows)
                route_seen = any(p.get("dm") == "server" and p.get("online") and (p.get("hasClient") or int(p.get("playerCount", 0)) > 0) for p in peer_rows)
                if client_seen or route_seen:
                    break
                time.sleep(0.2)
            client_wait_sec = round(time.monotonic() - phase_start, 3)
        phase_timings["clientPeerWaitSec"] = client_wait_sec
        peer_rows = client.peers().get("peers", [])
        server_peer_seen = any(p.get("dm") == "server" and p.get("online") for p in peer_rows)
        client_peer_seen = any(p.get("dm") == "client" and p.get("online") for p in peer_rows)
        player_joined = any(p.get("dm") == "server" and int(p.get("playerCount", 0)) > 0 for p in peer_rows)
        client_route_ready = client_peer_seen or any(
            p.get("dm") == "server" and p.get("online") and (p.get("hasClient") or int(p.get("playerCount", 0)) > 0)
            for p in peer_rows
        )

        # A started session without a live runtime peer is not a passing scenario,
        # even when no user probe was supplied.
        if not server_peer_seen or (args.get("clientCode") and mode == "play" and not (client_route_ready and player_joined)):
            reason = "server_peer_not_seen" if not server_peer_seen else "client_peer_or_player_not_seen"
            stop_result = None
            if not args.get("keepAlive", True):
                try:
                    stop_result = _unwrap_job(client.submit_job("playtest", {"action": "stop", "reason": "scenario_peer_missing"}, wait=15.0, dm="edit", peer_id=peer_id))
                except Exception as exc:
                    stop_result = {"ok": False, "error": str(exc)}
            return {
                "ok": False, "reason": reason, "warnings": [reason], "mode": mode,
                "input": None, "deviceOrientation": None,
                "durationSec": round(time.monotonic() - t_start, 2),
                "serverPeerSeen": server_peer_seen, "clientPeerSeen": client_peer_seen,
                "clientRouteReady": client_route_ready, "playerJoined": player_joined, "autoRestarted": restarted,
                "keepAlive": args.get("keepAlive", True), "sessionSurvived": False,
                "sessionStopped": bool(stop_result and stop_result.get("stopped")),
                "phaseTimings": {**phase_timings, "totalSec": round(time.monotonic() - t_start, 3)},
                "serverResult": None, "clientResult": None, "runtimeErrors": [],
                "consoleExcerpt": [], "console": [],
            }

        warnings_so_far = []
        warmup_complete = time.monotonic()
        phase_timings["startupAndWarmupSec"] = round(warmup_complete - t_start, 3)
        warnings = warnings_so_far

        # v5.4.0: real input can now be part of a scenario, so a playtest proves
        # "pressing E opens the door" rather than only "the door exists".
        input_out = None
        orientation_out = None
        wanted_orientation = args.get("deviceOrientation")
        if wanted_orientation:
            try:
                orientation_out = device_simulator_helper(
                    client, {"action": "set", "orientation": wanted_orientation}, peer_id=peer_id
                )
                if orientation_out.get("unsupported"):
                    warnings.append("deviceOrientation was skipped: StudioDeviceSimulatorService is unavailable here.")
            except Exception as exc:  # noqa: BLE001
                warnings.append("deviceOrientation failed: " + str(exc)[:160])
        input_steps = args.get("inputSteps") or []
        if input_steps:
            steps_norm, step_problems = normalise_input_steps(input_steps)
            if step_problems:
                raise ValueError("run_playtest_scenario inputSteps: " + "; ".join(step_problems))
            phase_start = time.monotonic()
            try:
                input_out = _unwrap_job(client.submit_job(
                    "virtual_input",
                    {
                        "steps": steps_norm,
                        "holdMs": float(args.get("inputHoldMs") or 60.0),
                        "settleMs": float(args.get("inputSettleMs") or 250.0),
                        "playerName": args.get("playerName"),
                        "_playerName": args.get("playerName"),
                    },
                    wait=min(60.0, max(10.0, timeout / 2)),
                    dm="client",
                    peer_id=peer_id,
                ))
                input_summary = _input_receipt_summary(input_out)
                input_out["summary"] = input_summary
                if input_out.get("unsupported"):
                    warnings.append("inputSteps were skipped: VirtualInput is unavailable in this Studio build.")
                elif input_summary["delivered"] < input_summary["total"]:
                    warnings.append(
                        f"inputSteps: {input_summary['total'] - input_summary['delivered']} of "
                        f"{input_summary['total']} step(s) were not delivered."
                    )
            except Exception as exc:  # noqa: BLE001 - a scenario must still stop the session
                input_out = {"ok": False, "error": str(exc), "summary": {"delivered": 0, "refused": 0, "errors": 1, "total": len(input_steps)}}
                warnings.append("inputSteps failed: " + str(exc)[:180])
            phase_timings["inputSec"] = round(time.monotonic() - phase_start, 3)

        server_out = None
        client_out = None
        errors_caught = []
        logs_res = {"lines": [], "nextIndex": args.get("consoleSinceIndex", 0)}

        phase_start = time.monotonic()
        if args.get("serverCode"):
            sj = client.submit_job("run_luau", {"code": args["serverCode"], "_timeout": timeout}, wait=timeout, dm="server", peer_id=peer_id)
            server_out = _unwrap_job(sj)
        phase_timings["serverProbeSec"] = round(time.monotonic() - phase_start, 3)
        phase_start = time.monotonic()
        if args.get("clientCode"):
            cj = client.submit_job("run_luau", {"code": args["clientCode"], "_timeout": timeout, "_playerName": args.get("playerName")}, wait=timeout, dm="client", peer_id=peer_id)
            client_out = _unwrap_job(cj)
        phase_timings["clientProbeSec"] = round(time.monotonic() - phase_start, 3)
        phase_start = time.monotonic()
        logs_res = _unwrap_job(client.submit_job("console", {
            "limit": args.get("consoleLimit", 200),
            "sinceIndex": args.get("consoleSinceIndex", 0),
            "source": "game",
        }, wait=15.0, dm="server", peer_id=peer_id))
        for ln in logs_res.get("lines", []):
            if "error" in ln.get("type", "").lower():
                errors_caught.append(ln.get("message", ""))
        phase_timings["consoleReadSec"] = round(time.monotonic() - phase_start, 3)

        if wanted_orientation and isinstance(orientation_out, dict):
            # Restore exactly what the peer reported before the override — the
            # engine has no "unset orientation", so a guessed default would be
            # its own mutation.
            before = orientation_out.get("before") or {}
            if orientation_out.get("unsupported"):
                orientation_out["restored"] = True  # nothing was applied
            elif before.get("orientation"):
                try:
                    device_simulator_helper(
                        client, {"action": "set", "orientation": before["orientation"]}, peer_id=peer_id
                    )
                    orientation_out["restored"] = True
                except Exception:  # noqa: BLE001 - the operator can always fix it in Studio
                    orientation_out["restored"] = False
                    warnings.append(
                        f"could not restore the device orientation to {before['orientation']}: "
                        "fix it in Studio's Device Simulator panel."
                    )
            else:
                orientation_out["restored"] = False
                warnings.append("orientation override left in place: the peer did not report its previous orientation.")
                restored = False

        keep_alive = args.get("keepAlive", True)
        stop_result = None
        if not keep_alive:
            phase_start = time.monotonic()
            try:
                stop_result = _unwrap_job(client.submit_job("playtest", {"action": "stop", "reason": "scenario_complete"}, wait=15.0, dm="edit", peer_id=peer_id))
            except Exception as exc:
                stop_result = {"ok": False, "error": str(exc)}
            phase_timings["stopSec"] = round(time.monotonic() - phase_start, 3)

        peer_rows_after = client.peers().get("peers", [])
        session_stopped = bool(stop_result and stop_result.get("stopped"))
        session_survived = any(p.get("dm") == "server" and p.get("online") for p in peer_rows_after)
        # Peer heartbeats can remain cached for the lease window after EndTest;
        # an acknowledged stop is stronger evidence than a just-expired heartbeat.
        if not keep_alive and session_stopped:
            session_survived = False
        if keep_alive and not session_survived:
            warnings.append("Play session did not survive the scenario probes.")
        if not keep_alive and not session_stopped:
            warnings.append("Play session stop was not confirmed.")
        probes_ok = (not args.get("serverCode") or server_out is not None) and (not args.get("clientCode") or client_out is not None)
        scenario_ok = server_peer_seen and probes_ok and not errors_caught and (session_survived if keep_alive else session_stopped)
        phase_timings["totalSec"] = round(time.monotonic() - t_start, 3)
        return {
            "ok": scenario_ok, "warnings": warnings, "mode": mode,
            "durationSec": round(time.monotonic() - t_start, 2),
            "serverPeerSeen": server_peer_seen, "clientPeerSeen": client_peer_seen,
            "clientRouteReady": client_route_ready, "playerJoined": player_joined, "autoRestarted": restarted,
            "keepAlive": keep_alive, "sessionSurvived": session_survived,
            "sessionStopped": session_stopped,
            "diedReason": None if server_peer_seen else "server_peer_not_seen",
            "phaseTimings": phase_timings, "serverResult": server_out,
            "clientResult": client_out, "runtimeErrors": errors_caught,
            "consoleExcerpt": logs_res.get("lines", []),
            "console": logs_res.get("lines", []),
            "consoleNextIndex": logs_res.get("nextIndex"),
            "consoleCount": len(logs_res.get("lines", [])),
        }

    if name == "get_console_output":
        return _unwrap_job(client.submit_job("console", {
            "limit": args.get("limit", 120),
            "level": args.get("level", "all"),
            "search": args.get("search"),
            "sinceIndex": args.get("sinceIndex", 0),
            "source": args.get("source", "game"),
        }, wait=25.0, dm=dm, peer_id=peer_id))

    if name == "build_tree":
        return _unwrap_job(client.submit_job("build", {
            "parent": args.get("parent", "Workspace"),
            "tree": args["tree"],
            "select": args.get("select", True),
        }, wait=60.0, dm=dm if dm != "auto" else "edit", peer_id=peer_id))

    if name == "set_properties":
        return _unwrap_job(client.submit_job("set_properties", {
            "path": args["path"],
            "properties": args["properties"],
        }, wait=30.0, dm=dm, peer_id=peer_id))

    if name == "mass_set_properties":
        return _unwrap_job(client.submit_job("mass_set_properties", {
            "paths": args["paths"],
            "properties": args.get("properties"),
            "relative": args.get("relative"),
        }, wait=45.0, dm=dm, peer_id=peer_id))

    if name == "clone_instance":
        return _unwrap_job(client.submit_job("clone_instance", {
            k: args[k] for k in ("path", "parent", "newName", "count", "offset", "properties") if k in args
        }, wait=45.0, dm=dm, peer_id=peer_id))

    if name == "reparent_or_rename":
        return _unwrap_job(client.submit_job("reparent_instance", {
            "paths": args["paths"],
            "newParent": args.get("newParent"),
            "newName": args.get("newName"),
            "preserveWorldPosition": args.get("preserveWorldPosition", False),
        }, wait=30.0, dm=dm, peer_id=peer_id))

    if name == "delete_instance":
        return _unwrap_job(client.submit_job("delete", {
            "paths": args["paths"],
        }, wait=30.0, dm=dm, peer_id=peer_id))

    if name == "manage_attributes":
        return _unwrap_job(client.submit_job("attributes", {
            "action": args["action"],
            "paths": args["paths"],
            "attributes": args.get("attributes"),
            "keys": args.get("keys"),
        }, wait=30.0, dm=dm, peer_id=peer_id))

    if name == "manage_tags":
        return _unwrap_job(client.submit_job("tags", {
            "action": args["action"],
            "tag": args.get("tag"),
            "tags": args.get("tags"),
            "paths": args.get("paths"),
            "limit": args.get("limit", 100),
        }, wait=30.0, dm=dm, peer_id=peer_id))

    if name == "manage_selection":
        return _unwrap_job(client.submit_job("selection", {
            "action": args.get("action", "get"),
            "paths": args.get("paths", []),
        }, wait=20.0, dm="edit", peer_id=peer_id))

    if name == "manage_camera":
        return _unwrap_job(client.submit_job("camera", {
            k: args[k] for k in ("action", "path", "position", "lookAt", "fov", "distanceMultiplier") if k in args
        }, wait=20.0, dm=dm, peer_id=peer_id))

    if name == "undo_redo":
        return _unwrap_job(client.submit_job("history", {
            "action": args["action"],
            "steps": args.get("steps", 1),
            "name": args.get("name"),
        }, wait=20.0, dm="edit", peer_id=peer_id))

    if name == "edit_terrain":
        return _unwrap_job(client.submit_job("terrain", {
            k: args[k] for k in (
                "action", "material", "sourceMaterial", "targetMaterial",
                "position", "rotation", "size", "radius", "height",
                "region", "confirmClearAll"
            ) if k in args
        }, wait=45.0, dm="edit", peer_id=peer_id))

    if name == "insert_asset":
        return _unwrap_job(client.submit_job("insert_asset", {
            k: args[k] for k in ("assetId", "parent", "name", "position", "stripScripts") if k in args
        }, wait=60.0, dm="edit", peer_id=peer_id))

    if name == "drift_and_sync":
        return drift_and_sync_tree(
            client=client,
            mode=args["mode"],
            local_dir=Path(args["localDir"]).expanduser(),
            studio_root=args["studioRoot"].replace("/", "."),
            skip_check=args.get("skipCheck", False),
            peer_id=peer_id,
        )

    if name == "manage_snapshots":
        act = args["action"]
        if act == "create":
            return create_snapshot_helper(
                client,
                root=args.get("root", "game"),
                label=args.get("label"),
                peer_id=peer_id,
                paths_filter=args.get("paths"),
            )
        if act == "list":
            return client.snapshots_action("list")
        if act == "get":
            if not args.get("snapshotId"):
                raise ValueError("snapshotId is required when action='get'")
            return client.snapshots_action("get", {"id": args["snapshotId"]})
        if act == "diff":
            if not args.get("snapshotId"):
                raise ValueError("snapshotId is required when action='diff'")
            snap = client.snapshots_action("get", {"id": args["snapshotId"]})
            live = _unwrap_job(client.submit_job("hash_scripts", {"root": snap.get("root", "game")}, wait=45.0, peer_id=peer_id))
            old_map = {x["path"]: x.get("hash") for x in snap.get("scripts", [])}
            new_map = {x["path"]: x.get("hash") for x in live.get("scripts", [])}
            changes = ([{"path": p, "change": "added"} for p in sorted(new_map.keys() - old_map.keys())]
                + [{"path": p, "change": "deleted"} for p in sorted(old_map.keys() - new_map.keys())]
                + [{"path": p, "change": "modified", "before": old_map[p], "after": new_map[p]} for p in sorted(old_map.keys() & new_map.keys()) if old_map[p] != new_map[p]])
            return {"ok": True, "snapshotId": args["snapshotId"], "changes": changes, "count": len(changes), "warnings": []}
        if act == "restore":
            if not args.get("snapshotId"):
                raise ValueError("snapshotId is required when action='restore'")
            snap = client.snapshots_action("get", {"id": args["snapshotId"]})
            scripts = snap.get("scripts", [])
            restored = []
            for entry in scripts:
                parts = entry["path"].split(".")
                sname = parts[-1]
                parent_path = ".".join(parts[:-1]) or "ServerScriptService"
                _unwrap_job(client.submit_job("write_script", {
                    "parent": parent_path,
                    "name": sname,
                    "className": entry.get("className", "ModuleScript"),
                    "source": entry["source"],
                    "replaceInstance": True,
                }, wait=30.0, dm="edit", peer_id=peer_id))
                restored.append(entry["path"])
            return {"restoredSnapshot": args["snapshotId"], "restoredCount": len(restored), "paths": restored}

    if name == "browse_kit":
        return browse_kit_helper(category=args.get("category", "all"), item=args.get("item"),
            output_format=args.get("format", "summary"), section=args.get("section"),
            cursor=int(args.get("cursor", 0)), limit=int(args.get("limit", 50)))

    if name == "search_assets":
        return search_assets_helper(
            query=args.get("query", ""), asset_type=args.get("assetType", "any"),
            category=args.get("category"), source_id=args.get("sourceId"),
            license_id=args.get("license"), provenance=args.get("provenance", "any"),
            cursor=int(args.get("cursor", 0)), limit=int(args.get("limit", 25)),
        )

    if name == "search_cloud_icons":
        return search_cloud_icons_helper(
            query=args.get("query", ""),
            pack=args.get("pack", "all"),
            category=args.get("category"),
            variant=args.get("variant", "any"),
            limit=int(args.get("limit", 40)),
        )

    if name == "install_template":
        return install_template_helper(
            client=client,
            template_name=args["template"],
            peer_id=peer_id,
        )

    if name == "import_audio":
        return import_audio_helper(client=client, args=args, peer_id=peer_id)

    if name == "import_rbxmx":
        return import_rbxmx_helper(client=client, args=args, peer_id=peer_id)

    if name == "export_rbxmx":
        return export_rbxmx_helper(client=client, args=args, peer_id=peer_id)

    if name == "audit_ui_layout":
        return audit_ui_layout_helper(client=client, args=args, peer_id=peer_id)

    if name == "install_dropin_system":
        return install_dropin_system_helper(client=client, system_arg=args["system"], peer_id=peer_id)

    if name == "capture_viewport":
        return capture_viewport_helper(client=client, args=args, peer_id=peer_id)

    if name == "export_geometry":
        return export_geometry_helper(client=client, args=args, peer_id=peer_id)

    if name == "render_offline":
        return render_offline_helper(client=client, args=args, peer_id=peer_id)

    if name == "screenshot_gui":
        return render_offline_helper(client=client, args=args, peer_id=peer_id, gui_only=True)

    if name == "perf_audit":
        return perf_audit_helper(client=client, args=args, peer_id=peer_id)

    if name == "mesh_capabilities":
        return _unwrap_job(client.submit_job("mesh_capabilities", {}, wait=25.0, dm="edit", peer_id=peer_id))

    if name == "wait_for":
        return wait_for_helper(client=client, args=args, peer_id=peer_id)

    if name == "audit_3d_scene":
        return audit_3d_scene_helper(client=client, args=args, peer_id=peer_id)

    if name == "export_place_rbxlx":
        return export_place_rbxlx_helper(client=client, args=args, peer_id=peer_id)

    if name == "import_place_rbxlx":
        return import_place_rbxlx_helper(client=client, args=args, peer_id=peer_id)

    if name == "open_cloud_action":
        return open_cloud_action_helper(args=args)

    if name == "generate_procedural_image":
        return import_image_helper(
            client=client,
            args={
                "proceduralPreset": args["preset"],
                "name": args.get("name") or args["preset"],
                "width": args.get("width", 128),
                "height": args.get("height", 128),
                "primaryColor": args.get("primaryColor"),
                "secondaryColor": args.get("secondaryColor"),
                "saveSubdir": args.get("saveSubdir", "generated"),
                "saveToRepo": args.get("saveToRepo", True),
                "targetPath": args.get("targetPath"),
                "applyMode": args.get("applyMode", "auto"),
            },
            peer_id=peer_id,
        )

    if name == "import_image":
        return import_image_helper(client=client, args=args, peer_id=peer_id)

    if name == "import_images_batch":
        return import_images_batch_helper(client=client, args=args, peer_id=peer_id)

    if name == "install_kit_module":
        return install_kit_module_helper(
            client=client,
            module_name=args.get("module", "all"),
            parent_path=args.get("parent", "ReplicatedStorage.Shared"),
            peer_id=peer_id,
        )

    if name == "spawn_prefab":
        return spawn_prefab_helper(client=client, args=args, peer_id=peer_id)

    if name == "simulate_input":
        return simulate_input_helper(client=client, args=args, peer_id=peer_id, dm=dm)

    if name == "manage_device_simulator":
        return device_simulator_helper(client=client, args=args, peer_id=peer_id)

    if name == "run_studio_tests":
        return run_studio_tests_helper(client=client, args=args, peer_id=peer_id)

    if name == "playtest_capabilities":
        return playtest_capabilities_helper(client=client, args=args, peer_id=peer_id, dm=dm)

    raise KeyError(f"Unknown tool: {name}")


def drift_and_sync_tree(
    client: BridgeClient,
    mode: str,
    local_dir: Path,
    studio_root: str,
    skip_check: bool = False,
    peer_id: str = None,
) -> dict:
    """Compare or synchronize a local Luau directory and live Studio subtree."""
    if mode == "pull":
        local_dir.mkdir(parents=True, exist_ok=True)
        hashes_res = _unwrap_job(client.submit_job("hash_scripts", {"root": studio_root}, wait=45.0, peer_id=peer_id))
        paths = [s["path"] for s in hashes_res.get("scripts", [])]
        written_files = []
        for i in range(0, len(paths), 25):
            batch = paths[i : i + 25]
            read_res = _unwrap_job(
                client.submit_job("read_scripts", {"paths": batch, "maxBytes": 2_000_000}, wait=60.0, peer_id=peer_id)
            )
            for item in read_res.get("scripts", []):
                if "error" in item:
                    continue
                rel = item["path"]
                if rel.startswith(studio_root + "."):
                    rel = rel[len(studio_root) + 1 :]
                elif rel == studio_root:
                    rel = "init"
                cls = item.get("className", "ModuleScript")
                ext = ".server.lua" if cls == "Script" else (".client.lua" if cls == "LocalScript" else ".lua")
                rel_parts = rel.split(".")
                out_file = local_dir.joinpath(*rel_parts[:-1], rel_parts[-1] + ext)
                out_file.parent.mkdir(parents=True, exist_ok=True)
                out_file.write_text(normalize_newlines(item.get("source", "")), encoding="utf-8")
                written_files.append(str(out_file))
        return {"mode": "pull", "studioRoot": studio_root, "localDir": str(local_dir), "filesWritten": len(written_files), "files": written_files}

    if not local_dir.exists():
        raise FileNotFoundError(f"Local directory does not exist: {local_dir}")

    local_map, build_root = scan_local_lua_tree(local_dir, studio_root)

    live_map = {}
    root_exists = True
    live_job = client.submit_job("hash_scripts", {"root": studio_root}, wait=45.0, dm="edit", peer_id=peer_id)
    if live_job.get("status") == "done":
        for entry in (live_job.get("result") or {}).get("scripts", []):
            live_map[entry["path"]] = entry
    elif "path not found" in str(live_job.get("error", "")):
        root_exists = False
    else:
        _unwrap_job(live_job)

    identical, modified, local_only, studio_only = [], [], [], []
    for sp, linfo in sorted(local_map.items()):
        rinfo = live_map.get(sp)
        if not rinfo:
            local_only.append(sp)
        elif rinfo["hash"] == linfo["hash"]:
            identical.append(sp)
        else:
            modified.append({
                "path": sp,
                "localHash": linfo["hash"],
                "liveHash": rinfo["hash"],
                "localBytes": linfo["bytes"],
                "liveBytes": rinfo["bytes"],
            })

    for sp in sorted(live_map.keys()):
        if sp not in local_map:
            studio_only.append(sp)

    drift_report = {
        "studioRoot": studio_root,
        "studioRootExists": root_exists,
        "localDir": str(local_dir.resolve()),
        "inSync": len(modified) == 0 and len(local_only) == 0 and len(studio_only) == 0,
        "identicalCount": len(identical),
        "modifiedCount": len(modified),
        "localOnlyCount": len(local_only),
        "studioOnlyCount": len(studio_only),
        "modified": modified,
        "localOnly": local_only,
        "studioOnly": studio_only,
    }

    if mode == "check":
        return drift_report

    if mode == "push":
        if not skip_check:
            chk = run_luau_check(path=str(local_dir))
            if not chk["ok"]:
                raise ValueError(
                    f"Aborted push: {chk['filesFailed']}/{chk['filesChecked']} local files failed luau_check: "
                    + json.dumps([r for r in chk["results"] if not r["ok"]])
                )
        snap_id = None
        if root_exists and live_map:
            snap = create_snapshot_helper(
                client, root=studio_root, label=f"auto-before-sync:{studio_root}", peer_id=peer_id
            )
            snap_id = snap.get("id")

        parent_parts = studio_root.split(".")[:-1]
        parent_path = ".".join(parent_parts) if parent_parts else "ServerScriptService"
        build_res = _unwrap_job(client.submit_job("build", {
            "parent": parent_path,
            "tree": [build_root],
            "select": False,
        }, note=f"sync:{studio_root}", wait=90.0, dm="edit", peer_id=peer_id))
        return {
            "mode": "push",
            "pushedScripts": len(local_map),
            "snapshotId": snap_id,
            "previousDrift": drift_report,
            "buildResult": build_res,
        }

    raise ValueError(f"Unsupported mode: {mode}")


# ---------------------------------------------------------------------------
# MCP Resources & Workflow Prompts
# ---------------------------------------------------------------------------

RESOURCES = [
    {
        "uri": "studio://state",
        "name": "Roblox Studio & Bridge State",
        "description": "Live connection health, connected Edit/Server/Client DataModel peers, place metadata, and recent job log.",
        "mimeType": "application/json",
    },
    {
        "uri": "studio://peers",
        "name": "Connected Studio Peers",
        "description": "All active Roblox Studio DataModel peers polling the bridge.",
        "mimeType": "application/json",
    },
    {
        "uri": "studio://console",
        "name": "Studio Output Log",
        "description": "Latest 150 lines from Roblox Studio LogService.",
        "mimeType": "application/json",
    },
    {
        "uri": "studio://selection",
        "name": "Current Studio Selection",
        "description": "Currently selected Instances in the Explorer/Viewport.",
        "mimeType": "application/json",
    },
    {
        "uri": "studio://survey",
        "name": "Place Architecture Survey (Metadata)",
        "description": "Service breakdown, instance census, script inventory + hashes, remotes, tags, and settings.",
        "mimeType": "application/json",
    },
    {
        "uri": "studio://bus",
        "name": "Multi-Agent Bus (Presence, Tasks, Messages)",
        "description": "Registered agents, open task board with leases, and the current activity cursor — shared by every agent on this bridge.",
        "mimeType": "application/json",
    },
    {
        "uri": "studio://ops",
        "name": "Bridge Operations Report",
        "description": "Queue depth, peer health, tunnel state, metrics, recent audit entries, and open tasks in one document.",
        "mimeType": "application/json",
    },
    {
        "uri": "studio://audit",
        "name": "Tamper-Evident Audit Log (head)",
        "description": "Latest hash-chained audit entries plus chain verification status.",
        "mimeType": "application/json",
    },
    {
        "uri": "studio://jobs",
        "name": "Job Queue & Per-Tool Latency",
        "description": "Queue depth, pause state, live peer list, recent job summaries and p50/p95 wall time per tool.",
        "mimeType": "application/json",
    },
    {
        "uri": "studio://studios",
        "name": "Connected Studio Fleet",
        "description": "Every connected Studio window with peerId, place, DataModel kind, online age, label, pinned/default routing and queued job counts.",
        "mimeType": "application/json",
    },
    {
        "uri": "studio://handoff",
        "name": "Markdown Handoff Brief",
        "description": "The bus export as markdown: roster, task board, recent messages and stats — paste-ready for the next agent or human.",
        "mimeType": "text/markdown",
    },
]

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "studio://scripts/{path}",
        "name": "Live Script Source by Path",
        "description": "Read live Luau source code for any dot-separated script path (e.g. studio://scripts/ServerScriptService.Main).",
        "mimeType": "text/x-luau",
    },
    {
        "uriTemplate": "studio://instance/{path}",
        "name": "Instance Subtree & Properties",
        "description": "Inspect properties, attributes, tags, and immediate children of any Instance path.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "skill://{name}",
        "name": "Roblox Studio Agent Skill Markdown",
        "description": "Read full Markdown instructions for any built-in skill in `.claude/skills/{name}/SKILL.md`.",
        "mimeType": "text/markdown",
    },
]

PROMPTS = [
    {
        "name": "survey_and_architecture",
        "description": "Survey an unfamiliar Roblox Studio place, map client/server architecture & remotes, and spot risks before making edits.",
        "arguments": [],
    },
    {
        "name": "safe_module_edit",
        "description": "Edit a Roblox ModuleScript safely using hash checks, luau_check static global verification, cache-busting replaceInstance/require_fresh, and quantitative verification.",
        "arguments": [
            {"name": "scriptPath", "description": "Dot path of target ModuleScript", "required": True},
            {"name": "goal", "description": "Desired code change", "required": True},
        ],
    },
    {
        "name": "automated_playtest_debug",
        "description": "Run a live playtest loop checking both Server and Client DataModel state, console errors, and clean teardown.",
        "arguments": [
            {"name": "scenario", "description": "Gameplay behavior to verify", "required": True},
        ],
    },
    {
        "name": "sync_repo_package",
        "description": "Audit repo <-> Studio drift with exact 32-bit FNV-1a hashes, pre-check all files for undefined globals, and push atomically.",
        "arguments": [
            {"name": "localDir", "description": "Local source folder (e.g. nightloop/src)", "required": True},
            {"name": "studioRoot", "description": "Studio container path (e.g. ServerScriptService.NightLoop)", "required": True},
        ],
    },
    {
        "name": "multi_agent_handoff",
        "description": "Hand work to another agent: read the live board, release what you cannot finish, and post a handoff with the next concrete step.",
        "arguments": [
            {"name": "agentName", "description": "Who is handing off (defaults to the bus roster)", "required": False},
            {"name": "focus", "description": "What the next agent should pick up first", "required": False},
        ],
    },
    {
        "name": "bus_standup",
        "description": "Render a five-line standup from the live Agent Bus: what changed, who owns what, what is blocked.",
        "arguments": [
            {"name": "since", "description": "Bus cursor to report from (defaults to the current cursor)", "required": False},
        ],
    },
    {
        "name": "task_triage",
        "description": "Rank open Agent Bus tasks by priority and dependencies, propose a claim order, and flag tasks without acceptance criteria.",
        "arguments": [],
    },
]


def read_mcp_resource(client: BridgeClient, uri: str) -> dict:
    if uri == "studio://state":
        data = execute_tool(client, "get_studio_state", {})
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, indent=2)}]}
    if uri == "studio://peers":
        data = client.peers()
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, indent=2)}]}
    if uri == "studio://console":
        data = execute_tool(client, "get_console_output", {"limit": 150})
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, indent=2)}]}
    if uri == "studio://selection":
        data = execute_tool(client, "manage_selection", {"action": "get"})
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, indent=2)}]}
    if uri == "studio://survey":
        data = execute_tool(client, "survey_place", {"includeSource": False})
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, indent=2)}]}
    if uri == "studio://bus":
        data = client.bus_action({"action": "stats"})
        board = client.bus_action({"action": "tasks", "includeDone": False})
        roster = client.bus_action({"action": "agents"})
        payload = {"stats": data, "agents": roster, "openTasks": board}
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, indent=2)}]}
    if uri == "studio://ops":
        data = client.ops_action({"action": "status"})
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, indent=2)}]}
    if uri == "studio://studios":
        data = client.fleet_action({"action": "list"})
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, indent=2)}]}
    if uri == "studio://jobs":
        ops = client.ops_action({"action": "status"})
        payload = {
            "queue": ops.get("queue"),
            "sweeper": ops.get("sweeper"),
            "health": {k: ops.get("health", {}).get(k) for k in ("queued", "jobs", "peers", "studio_connected", "queuePaused")},
            "jobLatency": ops.get("jobLatency"),
            "recentJobs": ops.get("recentJobs"),
        }
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, indent=2)}]}
    if uri == "studio://handoff":
        brief = client.bus_action({"action": "export", "summaryOnly": False})
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": brief.get("markdown", "")}]}
    if uri == "studio://audit":
        data = client.ops_action({"action": "audit", "limit": 25})
        verify = client.ops_action({"action": "audit_verify"})
        payload = {"entries": data, "verification": verify}
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(payload, indent=2)}]}
    if uri.startswith("studio://scripts/"):
        path = uri[len("studio://scripts/") :]
        data = execute_tool(client, "script_read", {"path": path})
        return {"contents": [{"uri": uri, "mimeType": "text/x-luau", "text": data.get("source", "")}]}
    if uri.startswith("studio://instance/"):
        path = uri[len("studio://instance/") :]
        data = execute_tool(client, "inspect_instance", {"path": path, "depth": 2})
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(data, indent=2)}]}
    if uri.startswith("skill://"):
        skill_name = uri[len("skill://") :].strip("/")
        data = browse_kit_helper(category="skills", item=skill_name)
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": data.get("content", "")}]}
    raise KeyError(f"Unknown resource URI: {uri}")


# ---------------------------------------------------------------------------
# Direct Image Import, Procedural Asset & Studio Kit Helpers
# ---------------------------------------------------------------------------

def _find_skills_dir() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here.parent / ".claude" / "skills", here.parent.parent / ".claude" / "skills"):
        if cand.is_dir():
            return cand
    return here.parent / ".claude" / "skills"


_CLOUD_ICONS_CACHE = None


def _load_cloud_icons() -> list:
    global _CLOUD_ICONS_CACHE
    if _CLOUD_ICONS_CACHE is not None:
        return _CLOUD_ICONS_CACHE
    from image_pipeline import find_kit_root

    mod_dir = find_kit_root() / "modules"
    entries = []
    icons_file = mod_dir / "CheckUIIcons.lua"
    if icons_file.is_file():
        src = icons_file.read_text(encoding="utf-8")
        for m in re.finditer(r'\[\s*"([^"]+)"\s*\]\s*=\s*"rbxassetid://(\d+)"', src):
            path_key, aid = m.group(1), m.group(2)
            parts = path_key.split("/")
            entries.append({
                "pack": "stud",
                "path": path_key,
                "category": parts[0] if parts else "General",
                "subCategory": parts[1] if len(parts) > 2 else "",
                "name": parts[-1],
                "assetId": int(aid),
                "uri": f"rbxassetid://{aid}",
            })
    lucide_file = mod_dir / "LucideIcons.lua"
    if lucide_file.is_file():
        lsrc = lucide_file.read_text(encoding="utf-8")
        for m in re.finditer(r'\[\s*"([^"]+)"\s*\]\s*=\s*"rbxassetid://(\d+)"', lsrc):
            lname, aid = m.group(1), m.group(2)
            entries.append({
                "pack": "lucide",
                "path": f"Lucide/{lname}",
                "category": "Lucide",
                "subCategory": "Outline",
                "name": lname,
                "assetId": int(aid),
                "uri": f"rbxassetid://{aid}",
            })
    lucide_fill_file = mod_dir / "LucideFilledIcons.lua"
    if lucide_fill_file.is_file():
        fsrc = lucide_fill_file.read_text(encoding="utf-8")
        for m in re.finditer(r'\[\s*"([^"]+)"\s*\]\s*=\s*"rbxassetid://(\d+)"', fsrc):
            fname, aid = m.group(1), m.group(2)
            entries.append({
                "pack": "lucide_fill",
                "path": f"LucideFill/{fname}-fill",
                "category": "LucideFill",
                "subCategory": "Filled",
                "name": f"{fname}-fill",
                "assetId": int(aid),
                "uri": f"rbxassetid://{aid}",
            })
    _CLOUD_ICONS_CACHE = entries
    return entries


def build_biome_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    presets = {
        "forest": ((83, 139, 72), (105, 100, 85), "Grass", "Rock"),
        "desert": ((197, 166, 106), (134, 110, 78), "Sand", "Rock"),
        "tundra": ((190, 213, 222), (113, 130, 139), "Snow", "Ice"),
        "volcanic": ((76, 66, 63), (134, 57, 39), "Slate", "Basalt"),
        "island": ((76, 153, 84), (113, 100, 75), "Grass", "Rock"),
    }
    preset = args.get("preset", "forest")
    ground_rgb, prop_rgb, ground_material, prop_material = presets[preset]
    center = [float(v) for v in args.get("center", [0, 0, 0])]
    size = [float(v) for v in args.get("size", [96, 96])]
    if len(center) != 3 or len(size) != 2 or any(not math.isfinite(v) for v in center + size) or min(size) <= 0:
        raise ValueError("center must be three finite numbers and size must be two positive finite numbers")
    tile = int(args.get("tileSize", 16))
    if not 8 <= tile <= 48:
        raise ValueError("tileSize must be between 8 and 48 studs")
    nx, nz = max(1, math.ceil(size[0] / tile)), max(1, math.ceil(size[1] / tile))
    if nx * nz > 576:
        raise ValueError("Biome exceeds the 576 tile safety cap; increase tileSize or reduce size")
    density = float(args.get("decorationDensity", 0.08))
    if not 0 <= density <= 1:
        raise ValueError("decorationDensity must be between 0 and 1")
    rng = random.Random(int(args.get("seed", 2026)))
    children = []
    decorations = 0
    for iz in range(nz):
        dz = min(tile, size[1] - iz * tile)
        z = center[2] - size[1] / 2 + iz * tile + dz / 2
        for ix in range(nx):
            dx = min(tile, size[0] - ix * tile)
            x = center[0] - size[0] / 2 + ix * tile + dx / 2
            height = rng.uniform(-2.5, 2.5)
            children.append({
                "name": f"Ground_{iz+1:02d}_{ix+1:02d}", "className": "Part",
                "properties": {"Anchored": True, "CanCollide": True, "Size": [dx, 8.0, dz],
                               "Position": [x, center[1] + height - 4.0, z], "Color": list(ground_rgb),
                               "Material": f"Enum.Material.{ground_material}"},
            })
            if density and rng.random() < density and decorations < 200:
                prop_x = x + rng.uniform(-dx * 0.35, dx * 0.35)
                prop_z = z + rng.uniform(-dz * 0.35, dz * 0.35)
                prop_height = rng.uniform(1.0, 3.0)
                children.append({
                    "name": f"Prop_{decorations+1:03d}", "className": "Part",
                    "properties": {"Anchored": True, "CanCollide": True,
                                   "Size": [prop_height, prop_height, prop_height],
                                   "Position": [prop_x, center[1] + height + prop_height / 2, prop_z],
                                   "Color": list(prop_rgb), "Material": f"Enum.Material.{prop_material}"},
                })
                decorations += 1
    result = _unwrap_job(client.submit_job("build", {
        "parent": args.get("parent", "Workspace"),
        "tree": [{"name": args.get("name", "ArenaBiome"), "className": "Folder", "replaceExisting": True, "children": children}],
        "select": False,
    }, note=f"build_biome:{preset}", wait=90.0, dm="edit", peer_id=peer_id))
    result.update({"ok": True, "preset": preset, "tiles": nx * nz, "decorations": decorations,
                   "seed": int(args.get("seed", 2026)), "warnings": ["Generated as anchored Parts; no Roblox Terrain voxels were modified."]})
    return result


def build_skybox_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    faces = args.get("faces") or {}
    face_keys = {"bk": "SkyboxBk", "dn": "SkyboxDn", "ft": "SkyboxFt", "lf": "SkyboxLf", "rt": "SkyboxRt", "up": "SkyboxUp"}
    unknown_faces = sorted(set(faces) - set(face_keys))
    missing = [key for key in face_keys if not isinstance(faces.get(key), str) or not faces[key].strip()]
    if unknown_faces or missing:
        raise ValueError(f"faces must contain exactly the six non-empty strings bk,dn,ft,lf,rt,up; missing={missing}, unknown={unknown_faces}")
    sky_props = {prop: faces[key].strip() for key, prop in face_keys.items()}
    nodes = [{"name": args.get("name", "ArenaSky"), "className": "Sky", "replaceExisting": True, "properties": sky_props}]
    atmosphere = args.get("atmosphere") or {}
    allowed = {"Density", "Offset", "Glare", "Haze", "Color", "Decay"}
    unknown = sorted(set(atmosphere) - allowed)
    if unknown:
        raise ValueError(f"Unknown atmosphere properties: {', '.join(unknown)}")
    if atmosphere:
        props = {}
        for key, value in atmosphere.items():
            if key in ("Color", "Decay"):
                if isinstance(value, str):
                    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                        raise ValueError(f"Atmosphere.{key} must be #RRGGBB or a 3-component array")
                    props[key] = value
                elif isinstance(value, list) and len(value) == 3 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
                    props[key] = value
                else:
                    raise ValueError(f"Atmosphere.{key} must be #RRGGBB or a 3-component array")
            else:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError(f"Atmosphere.{key} must be finite")
                props[key] = number
        nodes.append({"name": "ArenaAtmosphere", "className": "Atmosphere", "replaceExisting": True, "properties": props})
    result = _unwrap_job(client.submit_job("build", {"parent": "Lighting", "tree": nodes, "select": False}, note="build_skybox", wait=45.0, dm="edit", peer_id=peer_id))
    result.update({"ok": True, "sky": f"Lighting.{args.get('name', 'ArenaSky')}", "atmosphereCreated": bool(atmosphere),
                   "warnings": ["Skybox face content IDs must be accessible to this experience; this tool does not publish or bundle marketplace assets."]})
    return result


def scatter_props_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    center = [float(v) for v in args["center"]]
    if len(center) != 3 or any(not math.isfinite(v) for v in center):
        raise ValueError("center must contain three finite coordinates")
    radius = float(args["radius"])
    min_spacing = float(args.get("minSpacing", 6.0))
    count = int(args.get("count", 25))
    if not math.isfinite(radius) or radius <= 0 or not math.isfinite(min_spacing) or min_spacing < 0 or not 1 <= count <= 500:
        raise ValueError("radius, minSpacing, or count is outside supported bounds")
    seed = int(args.get("seed", 2026))
    rng = random.Random(seed)
    points = []
    tries = 0
    max_tries = max(1000, count * 120)
    while len(points) < count and tries < max_tries:
        tries += 1
        theta = rng.random() * math.tau
        r = radius * math.sqrt(rng.random())
        x = center[0] + math.cos(theta) * r
        z = center[2] + math.sin(theta) * r
        if min_spacing and any((x - px) ** 2 + (z - pz) ** 2 < min_spacing ** 2 for px, pz, _, _ in points):
            continue
        points.append([round(x, 3), round(center[1], 3), round(z, 3), round(rng.random() * 360, 2)])
    if len(points) != count:
        raise ValueError(f"Could place only {len(points)} of {count} requested props at minSpacing={min_spacing}; reduce spacing or count")
    result = _unwrap_job(client.submit_job("scatter_props", {
        "sourcePath": args["sourcePath"], "parent": args.get("parent", "Workspace"),
        "points": points, "namePrefix": args.get("namePrefix", "Scatter"),
        "stripScripts": args.get("stripScripts", True),
    }, note="scatter_props", wait=90.0, dm="edit", peer_id=peer_id))
    result.update({"ok": True, "seed": seed, "requestedCount": count, "radius": radius, "minSpacing": min_spacing, "warnings": []})
    return result


def search_cloud_icons_helper(
    query: str = "",
    pack: str = "all",
    category: str = None,
    variant: str = "any",
    limit: int = 40,
) -> dict:
    all_icons = _load_cloud_icons()
    pack_counts, cat_counts = {}, {}
    for ic in all_icons:
        pack_counts[ic["pack"]] = pack_counts.get(ic["pack"], 0) + 1
        if pack in ("all", None, "") or ic["pack"] == pack:
            cat_counts[ic["category"]] = cat_counts.get(ic["category"], 0) + 1

    requested_pack = (pack or "all").strip().lower()
    category_filter = (category or "").strip().lower()
    variant_filter = (variant or "any").lower()
    # Normalize common catalogue suffixes into a concept family, preserving every source URI as a variant.
    suffixes = re.compile(r"(?:[-_ ](?:1st|2nd|3rd|outline|filled|black|white|golden|blue|red|green|silver|bronze|small|large))+$", re.I)
    def family_name(name):
        return re.sub(r"[-_ ]+", " ", suffixes.sub("", name)).strip().lower()
    allowed = [ic for ic in all_icons if requested_pack in ("all", "") or ic["pack"] == requested_pack]
    terms = [t.lower() for t in (query or "").split() if t]

    def build_groups(candidates):
        groups = {}
        for ic in candidates:
            if category_filter and ic["category"].lower() != category_filter:
                continue
            fam = family_name(ic["name"])
            hay = f"{fam} {ic['category']} {ic.get('subCategory','')}".lower()
            if not all(t in hay for t in terms):
                continue
            groups.setdefault((ic["pack"], fam), []).append(ic)
        results = []
        for (pack_used, fam), variants in groups.items():
            variants.sort(key=lambda ic: (0 if ic["name"].lower() == fam.replace(" ", "-") else 1, ic["name"].lower()))
            selected = None
            if variant_filter != "any":
                selected = next((ic for ic in variants if variant_filter in (ic["name"] + " " + ic.get("subCategory", "")).lower()), None)
            if selected is None:
                selected = variants[0]
            record = dict(selected)
            record["family"] = fam
            record["variants"] = [{"name": x["name"], "uri": x["uri"], "assetId": x["assetId"], "subCategory": x.get("subCategory", "")} for x in variants]
            results.append(record)
        return results

    matches = build_groups(allowed)
    fallback_used = False
    if not matches and requested_pack not in ("all", ""):
        matches = build_groups(all_icons)
        fallback_used = bool(matches)
    matches.sort(key=lambda ic: (0 if ic["family"] == (query or "").strip().lower() else 1, ic["family"], ic["pack"]))
    final = matches[:max(1, min(int(limit), 250))]
    return {
        "ok": True, "warnings": [], "totalCatalogIcons": len(all_icons), "packs": pack_counts,
        "pack": requested_pack, "packUsed": "all" if fallback_used else requested_pack,
        "fallbackUsed": fallback_used, "query": query or "", "category": category or "all",
        "returnedCount": len(final), "categories": cat_counts, "icons": final,
    }


def _load_templates_manifest() -> list:
    from image_pipeline import find_kit_root

    manifest_file = find_kit_root() / "templates" / "systems.json"
    if not manifest_file.is_file():
        return []
    try:
        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        return data.get("systems", [])
    except Exception:
        return []


def install_template_helper(client: BridgeClient, template_name: str, peer_id: str = None) -> dict:
    from image_pipeline import find_kit_root

    kit = find_kit_root()
    systems = _load_templates_manifest()
    spec = next((s for s in systems if s.get("name") == template_name), None)
    if not spec:
        raise KeyError(
            f"Unknown template {template_name!r}. Available: {[s.get('name') for s in systems]}"
        )

    installed_modules = []
    mod_specs = []
    for mod_stem in spec.get("modules") or []:
        mfile = kit / "modules" / f"{mod_stem}.lua"
        if not mfile.is_file():
            raise FileNotFoundError(f"Required module missing: {mfile}")
        mod_specs.append({
            "name": mod_stem,
            "className": "ModuleScript",
            "replaceExisting": True,
            "source": mfile.read_text(encoding="utf-8"),
        })
        installed_modules.append(f"ReplicatedStorage.{mod_stem}")

    if mod_specs:
        _unwrap_job(client.submit_job("build", {
            "parent": "ReplicatedStorage",
            "tree": mod_specs,
            "select": False,
        }, note=f"template_modules:{template_name}", wait=45.0, dm="edit", peer_id=peer_id))

    screens_dir = kit / "templates" / "screens"
    builders = spec.get("builderModules") or []
    built_screens = []
    if builders:
        builder_children = []
        for bstem in builders:
            bfile = screens_dir / f"{bstem}.lua"
            if not bfile.is_file():
                raise FileNotFoundError(f"Screen builder missing: {bfile}")
            builder_children.append({
                "name": bstem,
                "className": "ModuleScript",
                "replaceExisting": True,
                "source": bfile.read_text(encoding="utf-8"),
            })
        _unwrap_job(client.submit_job("build", {
            "parent": "ServerStorage",
            "tree": [{
                "name": "StudExamples",
                "className": "Folder",
                "replaceExisting": True,
                "children": builder_children,
            }],
            "select": False,
        }, note=f"template_builders:{template_name}", wait=45.0, dm="edit", peer_id=peer_id))

        controller_src = ""
        ctrl_file = screens_dir / "06_MenuController.client.lua"
        if ctrl_file.is_file():
            controller_src = ctrl_file.read_text(encoding="utf-8")

        attach_list = spec.get("attachMenuControllerTo") or []
        preview_list = spec.get("enableForPreview") or []
        luau_lines = [
            "local RS = game:GetService('ReplicatedStorage')",
            "local SS = game:GetService('ServerStorage')",
            "local SG = game:GetService('StarterGui')",
            "local UI = require(RS:WaitForChild('StudUI'))",
            "local Icons = require(RS:WaitForChild('CheckUIIcons'))",
            "local built = {}",
        ]
        for bstem in builders:
            luau_lines.append(
                f"do local fn = require(SS.StudExamples[{json.dumps(bstem)}]); fn(UI, Icons, SG); table.insert(built, {json.dumps(bstem)}) end"
            )
        for modal_name in attach_list:
            if controller_src:
                luau_lines.append(
                    f"do local g = SG:FindFirstChild({json.dumps(modal_name)}); "
                    f"if g then local old = g:FindFirstChild('MenuController'); if old then old:Destroy() end; "
                    f"local ls = Instance.new('LocalScript'); ls.Name = 'MenuController'; ls.Source = {json.dumps(controller_src)}; ls.Parent = g end end"
                )
        for prev_name in preview_list:
            luau_lines.append(
                f"do local g = SG:FindFirstChild({json.dumps(prev_name)}); if g and g:IsA('ScreenGui') then g.Enabled = true end end"
            )
        luau_lines.append("return built")

        eval_res = _unwrap_job(client.submit_job("run_luau", {
            "code": "\n".join(luau_lines),
        }, note=f"template_generate:{template_name}", wait=45.0, dm="edit", peer_id=peer_id))
        built_screens = eval_res.get("returned") or eval_res.get("result") or builders

    written_scripts = []
    for sc in spec.get("scripts") or []:
        _unwrap_job(client.submit_job("write_script", {
            "parent": sc["parent"],
            "name": sc["name"],
            "className": sc.get("className", "Script"),
            "source": sc["source"],
            "replaceInstance": True,
        }, note=f"template_script:{sc['name']}", wait=30.0, dm="edit", peer_id=peer_id))
        written_scripts.append(f"{sc['parent']}.{sc['name']}")

    return {
        "template": template_name,
        "title": spec.get("title", template_name),
        "installedModules": installed_modules,
        "generatedScreens": built_screens,
        "installedScripts": written_scripts,
    }


def browse_kit_helper(category: str = "all", item: str = None, output_format: str = "summary", section: str = None, cursor: int = 0, limit: int = 50) -> dict:
    from image_pipeline import PROCEDURAL_PRESETS, find_kit_root

    kit = find_kit_root()
    skills_dir = _find_skills_dir()
    modules_dir = kit / "modules"
    prefabs_dir = kit / "prefabs"
    images_dir = kit / "assets" / "images"
    templates = _load_templates_manifest()

    if item:
        target = item.strip()
        # Check skill
        s_file = skills_dir / target / "SKILL.md"
        if category in ("all", "skills") and s_file.is_file():
            return {"type": "skill", "name": target, "path": str(s_file), "content": s_file.read_text(encoding="utf-8")}
        # Check Luau module
        m_stem = target[:-4] if target.endswith(".lua") else target
        m_file = modules_dir / f"{m_stem}.lua"
        if category in ("all", "modules") and m_file.is_file():
            return {"type": "module", "name": m_stem, "path": str(m_file), "content": m_file.read_text(encoding="utf-8")}
        # Check template
        t_spec = next((t for t in templates if t.get("name") == target), None)
        if category in ("all", "templates") and t_spec is not None:
            return {"type": "template", "name": target, "spec": t_spec, "content": json.dumps(t_spec, indent=2)}
        # Check prefab
        p_stem = target[:-5] if target.endswith(".json") else target
        p_file = prefabs_dir / f"{p_stem}.json"
        if category in ("all", "prefabs") and p_file.is_file():
            raw = p_file.read_text(encoding="utf-8")
            return {"type": "prefab", "name": p_stem, "path": str(p_file), "spec": json.loads(raw), "content": raw}
        # Check repo image asset
        img_cand = images_dir / target
        if category in ("all", "images") and img_cand.is_file():
            rel = img_cand.relative_to(images_dir).as_posix()
            return {
                "type": "image",
                "name": img_cand.stem,
                "relPath": rel,
                "localUri": f"rbxasset://arena_assets/{rel}",
                "bytes": img_cand.stat().st_size,
            }
        raise KeyError(f"Kit item not found: {target}")

    out = {"kitRoot": str(kit)}
    if category in ("all", "skills"):
        skills = []
        if skills_dir.is_dir():
            for d in sorted(skills_dir.iterdir()):
                md = d / "SKILL.md"
                if d.is_dir() and md.is_file():
                    skills.append({"name": d.name, "uri": f"skill://{d.name}", "path": str(md)})
        out["skills"] = skills
    if category in ("all", "modules"):
        out["modules"] = [
            {"name": f.stem, "bytes": f.stat().st_size, "path": str(f)}
            for f in sorted(modules_dir.glob("*.lua"))
        ] if modules_dir.is_dir() else []
    if category in ("all", "templates"):
        out["templates"] = [
            {
                "name": t["name"],
                "title": t.get("title", t["name"]),
                "category": t.get("category", "ui-screen"),
                "description": t.get("description", ""),
                "modules": t.get("modules", []),
            }
            for t in templates
        ]
    if category in ("all", "prefabs"):
        prefabs = []
        if prefabs_dir.is_dir():
            for f in sorted(prefabs_dir.glob("*.json")):
                try:
                    spec = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    spec = {}
                prefabs.append({
                    "name": f.stem,
                    "category": spec.get("category", "general"),
                    "description": spec.get("description", ""),
                    "defaultParent": spec.get("defaultParent", "Workspace"),
                })
        out["prefabs"] = prefabs
    registry_by_path = {}
    asset_registry_path = kit / "assets" / "registry.json"
    if asset_registry_path.is_file():
        try:
            registry_rows = json.loads(asset_registry_path.read_text(encoding="utf-8")).get("assets", [])
            registry_by_path = {row.get("path"): row for row in registry_rows if row.get("path")}
        except Exception:
            registry_by_path = {}

    if category in ("all", "images"):
        imgs = []
        cat_counts = {}
        if images_dir.is_dir():
            for f in sorted(images_dir.rglob("*")):
                if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
                    rel = f.relative_to(images_dir).as_posix()
                    rel_parts = rel.split("/")
                    if rel_parts[0] == "kenney_fetched":
                        pack = "kenney_" + (rel_parts[1] if len(rel_parts) > 1 else "other")
                    elif rel_parts[0] == "arena_skies":
                        pack = "arena_skies"
                    else:
                        pack = rel_parts[0] if rel_parts else "misc"
                    cat_counts[pack] = cat_counts.get(pack, 0) + 1
                    row = registry_by_path.get((kit / "assets" / "images" / rel).relative_to(kit.parent).as_posix(), {})
                    imgs.append({
                        "name": f.stem,
                        "pack": pack,
                        "category": row.get("category", pack),
                        "sourceId": row.get("sourceId"),
                        "license": row.get("license"),
                        "provenance": row.get("provenance"),
                        "thumbnailKind": row.get("thumbnailKind", "source-image"),
                        "relPath": rel,
                        "localUri": f"rbxasset://arena_assets/{rel}",
                        "bytes": f.stat().st_size,
                    })
        out["images"] = imgs
        out["imageCategories"] = cat_counts
    if category in ("all", "presets"):
        out["proceduralPresets"] = list(PROCEDURAL_PRESETS.keys())
    if category in ("all", "audio"):
        audio_dir = kit / "assets" / "audio"
        out["audio"] = [
            {
                "name": af.stem,
                "fileName": af.name,
                "localUri": f"rbxasset://arena_assets/audio/{af.name}",
                "bytes": af.stat().st_size,
            }
            for af in sorted(audio_dir.glob("*"))
            if af.is_file() and af.suffix.lower() in (".wav", ".ogg", ".mp3")
        ] if audio_dir.is_dir() else []
    if category in ("all", "models"):
        models_dir = kit / "models"
        out["models"] = [
            {"name": mf.stem, "fileName": mf.name, "bytes": mf.stat().st_size, "path": str(mf)}
            for mf in sorted(models_dir.glob("*.rbxmx"))
        ] if models_dir.is_dir() else []
    if category in ("all", "systems"):
        out["systems"] = _discover_dropin_systems(kit)
    if category in ("all", "cloud_icons"):
        ci = _load_cloud_icons()
        out["cloudIconsCount"] = len(ci)
    collection_keys = ("skills", "modules", "systems", "templates", "prefabs", "models", "images", "audio", "proceduralPresets")
    counts = {key: len(out.get(key, [])) for key in collection_keys}
    counts["cloudIcons"] = out.get("cloudIconsCount", 0)
    if section:
        rows = out.get(section)
        if rows is None:
            raise ValueError(f"Unknown or unavailable kit section: {section}")
        start = max(0, int(cursor))
        page_limit = max(1, min(200, int(limit)))
        page = rows[start:start + page_limit]
        return {"kitRoot": str(kit), "section": section, "counts": counts, "cursor": start,
                "nextCursor": start + len(page) if start + len(page) < len(rows) else None,
                "hasMore": start + len(page) < len(rows), "items": page}
    if output_format == "summary" and not item:
        for key in collection_keys:
            out.pop(key, None)
        out["counts"] = counts
        out["format"] = "summary"
    elif output_format == "full":
        out["format"] = "full"
        out["counts"] = counts
    return out


def search_assets_helper(query: str = "", asset_type: str = "any", category: str = None,
                         source_id: str = None, license_id: str = None,
                         provenance: str = "any", cursor: int = 0, limit: int = 25) -> dict:
    """Search the repository's pinned, licensed asset inventory without Studio RPC."""
    registry_path = Path(__file__).resolve().parent.parent / "roblox-kit" / "assets" / "registry.json"
    if not registry_path.is_file():
        raise FileNotFoundError("Licensed asset registry is missing; run tools/fetch_assets.py --build-registry")
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if registry.get("schemaVersion") != 1 or not isinstance(registry.get("assets"), list):
        raise ValueError("Unsupported local asset registry schema")
    terms = [part.casefold() for part in str(query or "").split() if part]
    filtered = []
    for item in registry["assets"]:
        if asset_type != "any" and item.get("assetType") != asset_type:
            continue
        if category and str(category).casefold() not in str(item.get("category", "")).casefold():
            continue
        if source_id and str(source_id).casefold() not in str(item.get("sourceId", "")).casefold():
            continue
        if license_id and item.get("license", "").casefold() != str(license_id).casefold():
            continue
        if provenance != "any" and item.get("provenance") != provenance:
            continue
        haystack = " ".join(str(item.get(key, "")) for key in ("name", "path", "category", "sourceId", "license", "provenance")).casefold()
        if any(term not in haystack for term in terms):
            continue
        result = {key: item.get(key) for key in (
            "name", "path", "assetType", "category", "sourceId", "sourceUrl",
            "sourceCommit", "license", "provenance", "sha256", "bytes",
            "thumbnail", "thumbnailKind", "upstreamPath", "derivation",
        )}
        path = Path(str(item.get("path", "")))
        parts = path.parts
        if len(parts) >= 4 and parts[:3] == ("roblox-kit", "assets", "images"):
            result["localStudioUri"] = "rbxasset://arena_assets/" + "/".join(parts[3:])
        elif len(parts) >= 4 and parts[:3] == ("roblox-kit", "assets", "audio"):
            result["localStudioUri"] = "rbxasset://arena_assets/audio/" + "/".join(parts[3:])
        else:
            result["localStudioUri"] = None
        filtered.append(result)
    filtered.sort(key=lambda row: (str(row.get("category", "")).casefold(), str(row.get("name", "")).casefold(), str(row.get("path", ""))))
    start = max(0, int(cursor))
    page_size = max(1, min(100, int(limit)))
    page = filtered[start:start + page_size]
    next_cursor = start + len(page) if start + len(page) < len(filtered) else None
    return {
        "ok": True,
        "query": str(query or ""),
        "registryAssetCount": len(registry["assets"]),
        "totalMatches": len(filtered),
        "cursor": start,
        "nextCursor": next_cursor,
        "hasMore": next_cursor is not None,
        "results": page,
        "warnings": [],
    }


def _discover_dropin_systems(kit: Path) -> list:
    sys_root = kit / "systems"
    if not sys_root.is_dir():
        return []
    found = []
    for group_dir in sorted(sys_root.iterdir()):
        if not group_dir.is_dir():
            continue
        for sys_dir in sorted(group_dir.iterdir()):
            if not sys_dir.is_dir():
                continue
            lua_files = sorted(sys_dir.rglob("*.lua"))
            if not lua_files:
                continue
            readme = sys_dir / "README.md"
            first_line = ""
            if readme.is_file():
                for ln in readme.read_text(encoding="utf-8").splitlines():
                    ln = ln.strip().lstrip("#").strip()
                    if ln:
                        first_line = ln
                        break
            found.append({
                "system": f"{group_dir.name}/{sys_dir.name}",
                "group": group_dir.name,
                "name": sys_dir.name,
                "scriptCount": len(lua_files),
                "summary": first_line or f"Drop-in {group_dir.name}/{sys_dir.name} system",
            })
    return found


def install_dropin_system_helper(client: BridgeClient, system_arg: str, peer_id: str = None) -> dict:
    from image_pipeline import find_kit_root

    kit = find_kit_root()
    sys_root = kit / "systems"
    all_sys = _discover_dropin_systems(kit)
    if system_arg == "all":
        targets = [s["system"] for s in all_sys]
    else:
        norm = system_arg.strip().strip("/")
        matches = [s["system"] for s in all_sys if s["system"] == norm or s["name"] == norm]
        if not matches:
            raise KeyError(f"System {system_arg!r} not found. Available: {[s['system'] for s in all_sys]}")
        targets = matches

    shared_children = []
    server_writes = []
    client_writes = []
    for sys_id in targets:
        sdir = sys_root / sys_id
        for subdir_name in ("shared", "server", "client"):
            sub = sdir / subdir_name
            if not sub.is_dir():
                continue
            for lf in sorted(sub.glob("*.lua")):
                src = lf.read_text(encoding="utf-8")
                if lf.name.endswith(".server.lua"):
                    stem = lf.name[:-len(".server.lua")]
                    server_writes.append((stem, "Script", src))
                elif lf.name.endswith(".client.lua"):
                    stem = lf.name[:-len(".client.lua")]
                    client_writes.append((stem, "LocalScript", src))
                else:
                    stem = lf.stem
                    if subdir_name == "server":
                        server_writes.append((stem, "ModuleScript", src))
                    elif subdir_name == "client":
                        client_writes.append((stem, "ModuleScript", src))
                    else:
                        shared_children.append({
                            "name": stem,
                            "className": "ModuleScript",
                            "replaceExisting": True,
                            "source": src,
                        })

    installed_paths = []
    if shared_children:
        _unwrap_job(client.submit_job("build", {
            "parent": "ReplicatedStorage",
            "tree": [{
                "name": "Shared",
                "className": "Folder",
                "replaceExisting": False,
                "children": shared_children,
            }],
            "select": False,
        }, note=f"system_shared:{system_arg}", wait=45.0, dm="edit", peer_id=peer_id))
        installed_paths.extend(f"ReplicatedStorage.Shared.{c['name']}" for c in shared_children)

    for nm, cls, src in server_writes:
        _unwrap_job(client.submit_job("write_script", {
            "parent": "ServerScriptService",
            "name": nm,
            "className": cls,
            "source": src,
            "replaceInstance": True,
        }, note=f"system_server:{nm}", wait=30.0, dm="edit", peer_id=peer_id))
        installed_paths.append(f"ServerScriptService.{nm}")

    for nm, cls, src in client_writes:
        _unwrap_job(client.submit_job("write_script", {
            "parent": "StarterPlayer.StarterPlayerScripts",
            "name": nm,
            "className": cls,
            "source": src,
            "replaceInstance": True,
        }, note=f"system_client:{nm}", wait=30.0, dm="edit", peer_id=peer_id))
        installed_paths.append(f"StarterPlayer.StarterPlayerScripts.{nm}")

    return {
        "systemsInstalled": targets,
        "installedCount": len(installed_paths),
        "installedPaths": installed_paths,
    }


def import_audio_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    import base64 as _b64
    from image_pipeline import find_kit_root, install_into_studio_content, update_repo_asset_manifest

    kit = find_kit_root()
    audio_dir = kit / "assets" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    if args.get("repoAudio"):
        cand = audio_dir / Path(args["repoAudio"]).name
        if not cand.is_file():
            raise FileNotFoundError(f"Repo audio not found: {cand}")
        raw = cand.read_bytes()
        fname = cand.name
        stem = cand.stem
    elif args.get("filePath"):
        p = Path(args["filePath"]).expanduser()
        if not p.is_file():
            raise FileNotFoundError(f"Audio file not found: {p}")
        raw = p.read_bytes()
        ext = p.suffix.lower() or ".wav"
        stem = re.sub(r"[^A-Za-z0-9_\-]", "_", args.get("name") or p.stem) or "sfx"
        fname = f"{stem}{ext}"
        (audio_dir / fname).write_bytes(raw)
    elif args.get("base64Data"):
        b64 = args["base64Data"].strip()
        if b64.startswith("data:"):
            _, _, b64 = b64.partition(",")
        raw = _b64.b64decode(b64)
        stem = re.sub(r"[^A-Za-z0-9_\-]", "_", args.get("name") or "imported_sfx") or "imported_sfx"
        fname = f"{stem}.wav"
        (audio_dir / fname).write_bytes(raw)
    else:
        raise ValueError("Provide 'repoAudio', 'filePath', or 'base64Data'.")

    inst = install_into_studio_content(raw, f"audio/{fname}")
    manifest = update_repo_asset_manifest()

    created = []
    target_path = args.get("targetPath")
    if target_path is None:
        target_path = "SoundService"
    if target_path and client.health().get("studio_connected"):
        res = _unwrap_job(client.submit_job("build", {
            "parent": target_path,
            "tree": [{
                "name": args.get("name") or stem,
                "className": "Sound",
                "replaceExisting": True,
                "properties": {
                    "SoundId": inst["uri"],
                    "Volume": float(args.get("volume", 0.6)),
                    "PlaybackSpeed": float(args.get("playbackSpeed", 1.0)),
                    "Looped": bool(args.get("looped", False)),
                },
            }],
            "select": True,
        }, note=f"import_audio:{stem}", wait=30.0, dm="edit", peer_id=peer_id))
        created = res.get("created", [])

    return {
        "name": stem,
        "fileName": fname,
        "uri": inst["uri"],
        "bytes": len(raw),
        "localStudioContentInstalled": inst["installed"],
        "studioCreated": created,
        "audioManifestCount": manifest.get("audioCount", 0),
    }


def import_rbxmx_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    from image_pipeline import find_kit_root
    from rbxmx_pipeline import parse_rbxmx_to_tree

    kit = find_kit_root()
    if args.get("xmlContent"):
        xml_str = args["xmlContent"]
    elif args.get("repoModel"):
        mname = args["repoModel"]
        if not mname.endswith(".rbxmx"):
            mname = f"{mname}.rbxmx"
        mp = kit / "models" / mname
        if not mp.is_file():
            raise FileNotFoundError(f"Repo .rbxmx model not found: {mp}")
        xml_str = mp.read_text(encoding="utf-8")
    elif args.get("filePath"):
        fp = Path(args["filePath"]).expanduser()
        if not fp.is_file():
            raise FileNotFoundError(f"File not found: {fp}")
        xml_str = fp.read_text(encoding="utf-8")
    else:
        raise ValueError("Provide 'filePath', 'repoModel', or 'xmlContent'.")

    tree, stats = parse_rbxmx_to_tree(xml_str, strip_scripts=bool(args.get("stripScripts", False)))
    pos = args.get("position")
    if isinstance(pos, list) and len(pos) == 3:
        for root_node in tree:
            _offset_tree_positions(root_node, float(pos[0]), float(pos[1]), float(pos[2]))

    parent_path = args.get("parent") or "Workspace"
    res = _unwrap_job(client.submit_job("build", {
        "parent": parent_path,
        "tree": tree,
        "select": True,
    }, note="import_rbxmx", wait=60.0, dm="edit", peer_id=peer_id))

    return {
        "parent": parent_path,
        "created": res.get("created", []),
        "itemsParsed": stats["itemsParsed"],
        "scriptsStripped": stats["scriptsStripped"],
    }


def _live_inspect_to_export_node(node: dict, client: BridgeClient, peer_id: str = None) -> dict:
    cls = node.get("className") or "Folder"
    out = {
        "name": node.get("name") or cls,
        "className": cls,
        "properties": {**(node.get("properties") or {}), **(node.get("structuredProperties") or {})},
        "tags": node.get("tags") or [],
        "children": [],
    }
    if cls in ("Script", "LocalScript", "ModuleScript") and node.get("path"):
        try:
            sr = _unwrap_job(client.submit_job("read_scripts", {"paths": [node["path"]]}, wait=20.0, dm="edit", peer_id=peer_id))
            arr = sr.get("scripts") or []
            if arr and arr[0].get("source") is not None:
                out["source"] = arr[0]["source"]
        except Exception:
            pass
    for ch in node.get("children") or []:
        out["children"].append(_live_inspect_to_export_node(ch, client, peer_id))
    return out


def export_rbxmx_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    from image_pipeline import find_kit_root
    from rbxmx_pipeline import serialize_tree_to_rbxmx

    kit = find_kit_root()
    if args.get("prefab"):
        stem = args["prefab"][:-5] if args["prefab"].endswith(".json") else args["prefab"]
        pf = kit / "prefabs" / f"{stem}.json"
        if not pf.is_file():
            raise FileNotFoundError(f"Prefab not found: {pf}")
        spec = json.loads(pf.read_text(encoding="utf-8"))
        nodes = spec.get("tree") or []
        default_name = stem
    elif args.get("path"):
        insp = _unwrap_job(client.submit_job("inspect", {
            "path": args["path"],
            "depth": int(args.get("depth", 8)),
            "includeProperties": True,
        }, wait=45.0, dm="edit", peer_id=peer_id))
        nodes = [_live_inspect_to_export_node(insp, client, peer_id)]
        default_name = args["path"].split(".")[-1]
    else:
        raise ValueError("Provide either live Studio 'path' or repo 'prefab'.")

    xml_str = serialize_tree_to_rbxmx(nodes)
    out_file = Path(args["outputFile"]).expanduser() if args.get("outputFile") else (kit / "models" / f"{default_name}.rbxmx")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(xml_str, encoding="utf-8")
    return {
        "outputFile": str(out_file),
        "bytes": len(xml_str.encode("utf-8")),
        "rootItems": len(nodes),
    }


def audit_ui_layout_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    from image_pipeline import find_kit_root
    from ui_linter import run_multi_viewport_audit

    kit = find_kit_root()
    if args.get("prefab"):
        stem = args["prefab"][:-5] if args["prefab"].endswith(".json") else args["prefab"]
        pf = kit / "prefabs" / f"{stem}.json"
        if not pf.is_file():
            raise FileNotFoundError(f"Prefab not found: {pf}")
        spec = json.loads(pf.read_text(encoding="utf-8"))
        tree = spec.get("tree") or []
        source_label = f"prefab:{stem}"
    else:
        target_path = args.get("path") or "StarterGui"
        insp = _unwrap_job(client.submit_job("inspect", {
            "path": target_path,
            "depth": 10,
            "includeProperties": True,
            "includeStructuredDescendants": True,
        }, wait=45.0, dm=args.get("dm", "edit"), peer_id=peer_id))
        if insp.get("className") == "StarterGui":
            tree = insp.get("children") or []
        else:
            tree = [insp]
        source_label = f"studio:{target_path}"

    viewports = args.get("viewports")
    sweep = None
    warnings = []
    specs = viewports
    if args.get("deviceSweep"):
        specs, sweep, sweep_warnings = _device_sweep_viewports(client, viewports, peer_id=peer_id)
        warnings.extend(sweep_warnings)
    audit = run_multi_viewport_audit(tree, viewports=specs)
    audit["source"] = source_label
    if sweep is not None:
        audit["deviceSweep"] = sweep
        audit["warnings"] = list(audit.get("warnings") or []) + warnings
    return audit


#: What each logical viewport asks Studio's Device Simulator for during a sweep.
DEVICE_SWEEP_TARGETS = {
    "desktop": (1920, 1080),
    "tablet": (1024, 768),
    "mobile": (390, 844),
}


def _device_sweep_viewports(client: BridgeClient, viewports, peer_id: str = None):
    """Drive Studio's Device Simulator per profile and audit the measured size.

    Returns `(specs, sweep_report, warnings)`. `specs` is what gets handed to
    `run_multi_viewport_audit` — preset names when the sweep is unavailable, or
    `{name, width, height}` dicts from what Studio actually applied.
    """
    names = list(viewports) if viewports else ["desktop", "tablet", "mobile"]
    try:
        before = _unwrap_job(client.submit_job(
            "device_simulator", {"action": "status"}, wait=25.0, dm="edit", peer_id=peer_id,
        ))
    except Exception as exc:  # noqa: BLE001 - a missing peer must degrade, not explode
        return None, {"applied": False, "reason": "no-peer", "error": str(exc)[:200]}, [
            "deviceSweep skipped: the Device Simulator could not be read (" + str(exc)[:120] + ")."
        ]
    if before.get("unsupported"):
        return None, {"applied": False, "reason": "unavailable"}, [
            "deviceSweep skipped: StudioDeviceSimulatorService is unavailable in this build "
            "(probe with playtest_capabilities); audited against the standard presets instead."
        ]
    previous = before.get("status") or {}
    profiles = []
    specs = []
    for name in names:
        width, height = DEVICE_SWEEP_TARGETS.get(name, (1920, 1080))
        entry = {"viewport": name, "requested": [width, height], "applied": False, "measured": None}
        try:
            res = _unwrap_job(client.submit_job(
                "device_simulator",
                {"action": "set_resolution", "width": width, "height": height},
                wait=25.0, dm="edit", peer_id=peer_id,
            ))
            measured = ((res.get("status") or {}).get("resolution")
                        or ((res.get("measured") or {}).get("resolution")))
            if measured and len(measured) == 2:
                entry["measured"] = [float(measured[0]), float(measured[1])]
                entry["applied"] = True
                specs.append({"name": name, "width": entry["measured"][0], "height": entry["measured"][1]})
            else:
                specs.append(name)
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)[:160]
            specs.append(name)
        profiles.append(entry)
    restored = {"attempted": True, "ok": False}
    try:
        prev_resolution = (previous or {}).get("resolution")
        if (previous or {}).get("simulationActive") and prev_resolution and len(prev_resolution) == 2:
            _unwrap_job(client.submit_job(
                "device_simulator",
                {"action": "set_resolution", "width": int(prev_resolution[0]), "height": int(prev_resolution[1])},
                wait=25.0, dm="edit", peer_id=peer_id,
            ))
        else:
            _unwrap_job(client.submit_job("device_simulator", {"action": "stop"}, wait=25.0,
                                          dm="edit", peer_id=peer_id))
        restored = {"attempted": True, "ok": True, "to": prev_resolution or "native"}
    except Exception as exc:  # noqa: BLE001
        restored = {"attempted": True, "ok": False, "error": str(exc)[:160]}
    warnings = [] if restored["ok"] else [
        "Device Simulator could not be restored — run manage_device_simulator(action='stop') in Studio."
    ]
    if len(specs) != len(profiles):  # defensive; keeps profiles and specs in lockstep
        specs = names
    return specs, {
        "applied": True,
        "before": previous,
        "profiles": profiles,
        "restored": restored,
        "engine": "StudioDeviceSimulatorService",
    }, warnings


def capture_viewport_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    import base64 as _b64
    from viewport_renderer import render_scene_png

    preset = args.get("preset") or "perspective"
    size = args.get("size") or {}
    w = int(size.get("w", args.get("width", 800)))
    h = int(size.get("h", args.get("height", 450)))
    include_hud = bool(args.get("includeHud", False))
    overlays = args.get("overlays") or {}
    show_labels = bool(overlays.get("labels", args.get("showLabels", True)))
    mode = args.get("mode") or "software"

    if mode == "native":
        return {"ok": False, "reason": "native_capture_unsafe", "warnings": ["Desktop capture is disabled because it cannot guarantee Studio-only pixels."]}

    ws_tree = _unwrap_job(client.submit_job("inspect", {
        "path": "Workspace",
        "depth": 8,
        "includeProperties": True,
    }, wait=45.0, dm="edit", peer_id=peer_id))

    lighting_props = {}
    try:
        lg = _unwrap_job(client.submit_job("inspect", {
            "path": "Lighting",
            "depth": 1,
            "includeProperties": True,
        }, wait=15.0, dm="edit", peer_id=peer_id))
        lighting_props = lg.get("properties") or {}
    except Exception:
        pass

    gui_children = []
    if include_hud:
        try:
            sg = _unwrap_job(client.submit_job("inspect", {
                "path": "StarterGui",
                "depth": 8,
                "includeProperties": True,
            }, wait=20.0, dm="edit", peer_id=peer_id))
            gui_children = sg.get("children") or []
        except Exception:
            pass

    png_bytes, meta = render_scene_png(
        workspace_tree=ws_tree,
        gui_tree=gui_children,
        lighting_props=lighting_props,
        width=w,
        height=h,
        camera_preset=preset,
        explicit_cam_pos=tuple(args["camera"]["position"]) if args.get("camera", {}).get("position") else None,
        explicit_look_at=tuple(args["camera"]["target"]) if args.get("camera", {}).get("target") else None,
        explicit_ortho=bool(args.get("camera", {}).get("ortho", False)),
        fov_deg=float(args.get("camera", {}).get("fov", 70.0)),
        include_hud=include_hud,
        show_labels=show_labels,
        show_grid=bool(overlays.get("grid", True)),
    )
    if args.get("saveFile"):
        sp = Path(args["saveFile"]).expanduser()
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_bytes(png_bytes)
        meta["savedFile"] = str(sp)
    meta["ok"] = True
    meta["warnings"] = []
    if args.get("returnInline", True):
        meta["_mcp_image_png_b64"] = _b64.b64encode(png_bytes).decode("ascii")
    return meta


def export_geometry_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    from viewport_renderer import collect_parts_from_tree
    root = _unwrap_job(client.submit_job("inspect", {"path": args.get("path", "Workspace"), "depth": args.get("depth", 10), "includeProperties": True}, wait=60.0, dm=args.get("dm", "edit"), peer_id=peer_id))
    requested = {str(x).lower() for x in args.get("include", ["cframe", "size", "color", "material", "transparency", "shape", "tags"])}
    parts = []
    stack = [root]
    while stack:
        node = stack.pop()
        props = {**(node.get("properties") or {}), **(node.get("structuredProperties") or {})}
        if node.get("className") in {"Part", "MeshPart", "WedgePart", "CornerWedgePart", "UnionOperation", "TrussPart", "SpawnLocation", "Seat", "VehicleSeat"}:
            for parsed in collect_parts_from_tree(node):
                pos, axes = parsed["pos"], parsed["axes"]
                record = {"n": parsed["name"], "path": parsed["path"]}
                if "size" in requested: record["s"] = list(parsed["size"])
                if "cframe" in requested:
                    ax, ay, az = axes
                    record["z"] = list(pos)
                    record["r"] = [ax[0], ay[0], az[0], ax[1], ay[1], az[1], ax[2], ay[2], az[2]]
                if "color" in requested: record["c"] = [round(v / 255.0, 6) for v in parsed["color"]]
                if "transparency" in requested: record["t"] = parsed["transparency"]
                if "material" in requested: record["m"] = str(props.get("Material", "SmoothPlastic"))
                if "shape" in requested: record["sh"] = str(props.get("Shape", "Block"))
                if "tags" in requested: record["tags"] = list(node.get("tags") or [])
                parts.append(record)
        stack.extend(reversed(node.get("children") or []))
    return {"ok": True, "path": args.get("path", "Workspace"), "count": len(parts), "parts": parts, "warnings": []}


def render_offline_helper(client: BridgeClient, args: dict, peer_id: str = None, gui_only: bool = False) -> dict:
    import base64 as _b64
    from viewport_renderer import render_scene_png
    from ui_linter import _has_absolute_measurements

    dm = args.get("dm", "edit") if gui_only else "edit"
    width, height = int(args.get("width", 800)), int(args.get("height", 450))
    camera = args.get("camera") or {}
    if gui_only:
        target = args.get("path") or ("PlayerGui" if dm == "client" else "StarterGui")
        tree = _unwrap_job(client.submit_job("inspect", {
            "path": target, "depth": 12, "includeProperties": True,
            "includeStructuredDescendants": True,
        }, wait=60.0, dm=dm, peer_id=peer_id))
        gui_nodes = tree.get("children", []) if tree.get("className") in ("StarterGui", "PlayerGui") else [tree]
        world = {"name": "Workspace", "className": "Workspace", "children": []}
        png, meta = render_scene_png(world, gui_tree=gui_nodes, width=width, height=height,
                                     include_hud=True, show_labels=False, show_grid=False)
        has_live_bounds = _has_absolute_measurements(gui_nodes)
        meta["measuredFrom"] = "client" if dm == "client" and has_live_bounds else "derived"
        meta.setdefault("safeArea", [])
        if meta["measuredFrom"] == "derived":
            meta.setdefault("warnings", []).append("No live client AbsolutePosition/AbsoluteSize measurements were used; layout was derived from UDim2.")
    else:
        offline_tree = args.get("tree")
        gui_nodes = args.get("gui") or []
        if not isinstance(gui_nodes, list):
            raise ValueError("gui must be an array of serialized GuiObject nodes")
        if offline_tree is not None:
            if not isinstance(offline_tree, dict):
                raise ValueError("tree must be one serialized hierarchy root object")
            tree = offline_tree
            lighting = args.get("lighting") or {}
            if not isinstance(lighting, dict):
                raise ValueError("lighting must be an object")
            source = "exported_tree"
        else:
            target = args.get("path", "Workspace")
            tree = _unwrap_job(client.submit_job("inspect", {
                "path": target, "depth": 12, "includeProperties": True,
            }, wait=60.0, dm="edit", peer_id=peer_id))
            lighting = {}
            try:
                lighting = _unwrap_job(client.submit_job("inspect", {
                    "path": "Lighting", "depth": 1, "includeProperties": True,
                }, wait=15.0, dm="edit", peer_id=peer_id)).get("properties", {})
            except Exception:
                pass
            if args.get("includeHud", False) and not gui_nodes:
                gui_nodes = _unwrap_job(client.submit_job("inspect", {
                    "path": "StarterGui", "depth": 10, "includeProperties": True,
                    "includeStructuredDescendants": True,
                }, wait=25.0, dm="edit", peer_id=peer_id)).get("children", [])
            source = "studio"
        png, meta = render_scene_png(
            tree, gui_tree=gui_nodes, lighting_props=lighting, width=width, height=height,
            camera_preset="perspective",
            explicit_cam_pos=tuple(camera["position"]) if camera.get("position") else None,
            explicit_look_at=tuple(camera["target"]) if camera.get("target") else None,
            explicit_ortho=bool(camera.get("ortho", False)), fov_deg=float(camera.get("fov", 70)),
            include_hud=bool(args.get("includeHud", False)), show_labels=False,
        )
        meta["source"] = source
        meta["measuredFrom"] = "client" if source == "exported_tree" and _has_absolute_measurements(gui_nodes) else "derived"
        if source == "exported_tree" and meta["measuredFrom"] == "derived":
            meta.setdefault("warnings", []).append("No client absolute UI measurements were present in the exported tree; layout was derived from UDim2.")
    if args.get("saveFile"):
        out = Path(args["saveFile"]).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(png)
        meta["savedFile"] = str(out)
    meta["ok"] = True
    meta.setdefault("warnings", [])
    meta["_mcp_image_png_b64"] = _b64.b64encode(png).decode("ascii")
    return meta


def perf_audit_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    root = _unwrap_job(client.submit_job("inspect", {"path": args.get("path", "Workspace"), "depth": 20, "includeProperties": True, "descendantSummary": True}, wait=60.0, dm="edit", peer_id=peer_id))
    counts, meshes, models, unanchored, bad_queries, total = {}, 0, 0, 0, 0, 0
    stack = [root]
    while stack:
        node = stack.pop(); total += 1
        cls = node.get("className", "Instance"); counts[cls] = counts.get(cls, 0) + 1
        props = {**(node.get("properties") or {}), **(node.get("structuredProperties") or {})}
        if cls == "MeshPart": meshes += 1
        if cls == "Model": models += 1
        if cls in ("Part", "MeshPart", "WedgePart", "CornerWedgePart", "UnionOperation", "TrussPart", "SpawnLocation", "Seat", "VehicleSeat"):
            if props.get("Anchored") is False: unanchored += 1
            if props.get("CanQuery") is False and props.get("CanCollide") is True: bad_queries += 1
        stack.extend(node.get("children") or [])
    part_count = sum(counts.get(c, 0) for c in ("Part", "MeshPart", "WedgePart", "CornerWedgePart", "UnionOperation", "TrussPart", "SpawnLocation", "Seat", "VehicleSeat"))
    risk = "high" if part_count >= 8000 else "elevated" if part_count >= 3000 or meshes >= 1500 else "moderate" if part_count >= 1000 else "low"
    return {"ok": True, "partCount": part_count, "triangleCount": None, "uniqueMeshCount": meshes, "textureMemoryEstimateMB": None, "modelCount": models, "unanchoredCount": unanchored, "canQueryCanCollideMisuse": bad_queries, "streamingEnabled": (root.get("structuredProperties") or {}).get("StreamingEnabled"), "mobileVerdict": risk, "warnings": ["Triangle counts and texture memory require mesh/texture metadata not exposed by the plugin."]}


def audit_3d_scene_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    from scene_auditor import audit_3d_hierarchy

    target_path = args.get("path") or "Workspace"
    root = _unwrap_job(client.submit_job("inspect", {
        "path": target_path,
        "depth": 10,
        "includeProperties": True,
    }, wait=45.0, dm="edit", peer_id=peer_id))

    report = audit_3d_hierarchy(root)
    report["path"] = target_path
    anchored_fixed = 0
    snapped_fixed = 0

    if args.get("autoFixUnanchored") and report["unanchoredPaths"]:
        _unwrap_job(client.submit_job("mass_set_properties", {
            "paths": report["unanchoredPaths"],
            "properties": {"Anchored": True},
        }, note="auto_anchor_3d_audit", wait=30.0, dm="edit", peer_id=peer_id))
        anchored_fixed = len(report["unanchoredPaths"])

    if args.get("snapFloating") and report["floatingSnapCandidates"]:
        for cand in report["floatingSnapCandidates"]:
            _unwrap_job(client.submit_job("set_properties", {
                "path": cand["path"],
                "properties": {"Position": {"__t": "Vector3", "v": cand["newPosition"]}},
            }, note="snap_floating_part", wait=20.0, dm="edit", peer_id=peer_id))
            snapped_fixed += 1

    if anchored_fixed or snapped_fixed:
        root_after = _unwrap_job(client.submit_job("inspect", {
            "path": target_path,
            "depth": 10,
            "includeProperties": True,
        }, wait=45.0, dm="edit", peer_id=peer_id))
        report = audit_3d_hierarchy(root_after)
        report["path"] = target_path

    report["autoFixedUnanchored"] = anchored_fixed
    report["autoSnappedFloating"] = snapped_fixed
    return report


def export_place_rbxlx_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    from image_pipeline import find_kit_root
    from rbxmx_pipeline import serialize_place_to_rbxlx

    kit = find_kit_root()
    out_file = Path(args.get("outputFile") or (kit / "models" / "StarterPlace.rbxlx")).expanduser()
    services_map = {}

    if args.get("offlineStarterPlace") or not client.health().get("studio_connected"):
        plaza = json.loads((kit / "prefabs" / "spawn_plaza.json").read_text(encoding="utf-8"))["tree"]
        shop = json.loads((kit / "prefabs" / "ui_kenney_shop_window.json").read_text(encoding="utf-8"))["tree"]
        mods = [
            {
                "name": mf.stem,
                "className": "ModuleScript",
                "source": mf.read_text(encoding="utf-8"),
            }
            for mf in sorted((kit / "modules").glob("*.lua"))
        ]
        services_map = {
            "Workspace": [
                {
                    "name": "Baseplate",
                    "className": "Part",
                    "properties": {
                        "Anchored": True,
                        "Size": {"__t": "Vector3", "v": [512, 16, 512]},
                        "Position": {"__t": "Vector3", "v": [0, -8, 0]},
                        "Color": {"__t": "Color3", "v": [0.38, 0.4, 0.44]},
                    },
                },
                *plaza,
            ],
            "ReplicatedStorage": [{"name": "Shared", "className": "Folder", "children": mods}],
            "StarterGui": shop,
        }
        mode_used = "offline_starter_place"
    else:
        for svc in ("Workspace", "Lighting", "ReplicatedStorage", "ServerScriptService", "StarterGui", "StarterPlayer", "SoundService"):
            try:
                insp = _unwrap_job(client.submit_job("inspect", {
                    "path": svc,
                    "depth": 8,
                    "includeProperties": True,
                }, wait=30.0, dm="edit", peer_id=peer_id))
                exported = _live_inspect_to_export_node(insp, client, peer_id)
                services_map[svc] = {
                    "properties": exported.get("properties") or {},
                    "children": exported.get("children") or [],
                }
            except Exception:
                services_map[svc] = []
        mode_used = "live_studio_export"

    xml_str = serialize_place_to_rbxlx(services_map)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(xml_str, encoding="utf-8")
    return {
        "mode": mode_used,
        "outputFile": str(out_file),
        "bytes": len(xml_str.encode("utf-8")),
        "servicesExported": list(services_map.keys()),
    }


def import_place_rbxlx_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    from rbxmx_pipeline import parse_rbxlx_place

    fp = Path(args["filePath"]).expanduser()
    if not fp.is_file():
        raise FileNotFoundError(f".rbxlx file not found: {fp}")
    services, stats = parse_rbxlx_place(fp.read_text(encoding="utf-8"), strip_scripts=bool(args.get("stripScripts", False)))
    synced_services = []
    created_total = 0
    for svc_name, payload in services.items():
        children = payload.get("children") or []
        if not children:
            continue
        res = _unwrap_job(client.submit_job("build", {
            "parent": svc_name,
            "tree": children,
            "select": False,
        }, note=f"import_rbxlx:{svc_name}", wait=60.0, dm="edit", peer_id=peer_id))
        synced_services.append(svc_name)
        created_total += len(res.get("created", []))

    return {
        "file": str(fp),
        "servicesSynced": synced_services,
        "createdRoots": created_total,
        "itemsParsed": stats["itemsParsed"],
        "scriptsStripped": stats["scriptsStripped"],
    }


def open_cloud_action_helper(args: dict) -> dict:
    from urllib.parse import quote

    api_key = (args.get("apiKey") or os.environ.get("ROBLOX_OPEN_CLOUD_API_KEY") or "").strip()
    if not api_key:
        raise ValueError(
            "Missing Open Cloud API key. Set ROBLOX_OPEN_CLOUD_API_KEY or pass 'apiKey' with permissions for Universes / DataStores / Messaging."
        )
    action = args["action"]
    uid = int(args["universeId"])
    headers = {"x-api-key": api_key}

    if action == "publish_place":
        pid = int(args["placeId"])
        pfile = Path(args["placeFile"]).expanduser()
        if not pfile.is_file():
            raise FileNotFoundError(f"Place file not found: {pfile}")
        raw = pfile.read_bytes()
        vtype = args.get("versionType") or "Saved"
        ctype = "application/xml" if pfile.suffix.lower() == ".rbxlx" else "application/octet-stream"
        url = f"https://apis.roblox.com/universes/v1/{uid}/places/{pid}/versions?versionType={quote(vtype)}"
        req = urllib.request.Request(url, data=raw, method="POST", headers={**headers, "Content-Type": ctype})
    elif action == "datastore_list":
        url = f"https://apis.roblox.com/datastores/v1/universes/{uid}/standard-datastores?limit=50"
        req = urllib.request.Request(url, method="GET", headers=headers)
    elif action == "datastore_get":
        ds = quote(args["datastoreName"])
        ek = quote(args["entryKey"])
        url = f"https://apis.roblox.com/datastores/v1/universes/{uid}/standard-datastores/datastore/entries/entry?datastoreName={ds}&entryKey={ek}"
        req = urllib.request.Request(url, method="GET", headers=headers)
    elif action == "datastore_set":
        ds = quote(args["datastoreName"])
        ek = quote(args["entryKey"])
        body = json.dumps(args.get("value")).encode("utf-8")
        url = f"https://apis.roblox.com/datastores/v1/universes/{uid}/standard-datastores/datastore/entries/entry?datastoreName={ds}&entryKey={ek}"
        req = urllib.request.Request(url, data=body, method="POST", headers={**headers, "Content-Type": "application/json"})
    elif action == "messaging_publish":
        topic = quote(args["topic"])
        body = json.dumps({"message": json.dumps(args.get("value"))}).encode("utf-8")
        url = f"https://apis.roblox.com/messaging-service/v1/universes/{uid}/topics/{topic}"
        req = urllib.request.Request(url, data=body, method="POST", headers={**headers, "Content-Type": "application/json"})
    else:
        raise ValueError(f"Unsupported Open Cloud action: {action}")

    with urllib.request.urlopen(req, timeout=30) as resp:
        raw_out = resp.read().decode("utf-8", errors="replace")
        return {
            "status": resp.status,
            "action": action,
            "response": json.loads(raw_out) if raw_out.strip() else {"ok": True},
        }


def import_image_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    import base64 as _b64
    import re as _re
    from image_pipeline import (
        decode_any_to_rgba,
        encode_png_rgba,
        find_kit_root,
        generate_procedural_rgba,
        install_into_studio_content,
        resize_rgba,
        update_repo_asset_manifest,
        upload_open_cloud_image,
    )

    kit = find_kit_root()
    images_dir = kit / "assets" / "images"

    raw_bytes = None
    ext = ".png"
    default_stem = "imported_image"
    rel_override = None

    if args.get("proceduralPreset"):
        preset = args["proceduralPreset"]
        w, h, rgba = generate_procedural_rgba(
            preset,
            width=int(args.get("width") or 0),
            height=int(args.get("height") or 0),
            primary_hex=args.get("primaryColor"),
            secondary_hex=args.get("secondaryColor"),
        )
        raw_bytes = encode_png_rgba(w, h, rgba)
        default_stem = preset
    elif args.get("repoAsset"):
        rel_clean = args["repoAsset"].replace("\\", "/").lstrip("/")
        cand = images_dir / rel_clean
        if not cand.is_file():
            raise FileNotFoundError(f"Repo asset not found: {cand}")
        raw_bytes = cand.read_bytes()
        ext = cand.suffix.lower() or ".png"
        default_stem = cand.stem
        rel_override = rel_clean
    elif args.get("filePath"):
        src_p = Path(args["filePath"]).expanduser()
        if not src_p.is_file():
            raise FileNotFoundError(f"Image file not found: {src_p}")
        raw_bytes = src_p.read_bytes()
        ext = src_p.suffix.lower() or ".png"
        default_stem = src_p.stem
    elif args.get("base64Data"):
        b64_in = args["base64Data"].strip()
        if b64_in.startswith("data:"):
            header, _, b64_in = b64_in.partition(",")
            if "jpeg" in header or "jpg" in header:
                ext = ".jpg"
            elif "bmp" in header:
                ext = ".bmp"
        raw_bytes = _b64.b64decode(b64_in)
    elif args.get("imageUrl"):
        req = urllib.request.Request(args["imageUrl"], headers={"User-Agent": f"ArenaRobloxMCP/{MCP_SERVER_VERSION}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw_bytes = resp.read()
        url_path = args["imageUrl"].split("?", 1)[0]
        if Path(url_path).suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
            ext = Path(url_path).suffix.lower()
        default_stem = Path(url_path).stem or "remote_image"
    else:
        raise ValueError(
            "Provide one image source: 'filePath', 'base64Data', 'imageUrl', 'repoAsset', or 'proceduralPreset'."
        )

    raw_name = args.get("name") or default_stem
    safe_stem = _re.sub(r"[^A-Za-z0-9_\-]", "_", Path(raw_name).stem) or "image"
    subdir = _re.sub(r"[^A-Za-z0-9_/\-]", "", args.get("saveSubdir") or "imported").strip("/") or "imported"
    rel_path = rel_override or f"{subdir}/{safe_stem}{ext}"

    repo_saved_path = None
    if args.get("saveToRepo", True):
        out_path = images_dir / rel_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(raw_bytes)
        repo_saved_path = str(out_path)

    # 1. Install into local Roblox Studio `content/arena_assets/...` when Studio exists locally
    local_install = install_into_studio_content(raw_bytes, rel_path)
    best_uri = local_install["uri"]
    cloud_result = None

    # 2. Optional Open Cloud permanent `rbxassetid://` publish
    publish_via = args.get("publishVia") or "none"
    if publish_via == "open_cloud":
        cloud_result = upload_open_cloud_image(
            raw_bytes=raw_bytes,
            filename=f"{safe_stem}{ext}",
            display_name=safe_stem,
            api_key=args.get("apiKey"),
            creator_id=args.get("creatorId"),
            creator_type=args.get("creatorType") or "user",
        )
        best_uri = cloud_result["uri"]

    # 3. Decode RGBA pixels whenever Studio hydration / target application / studio publish is needed
    need_editable = bool(
        args.get("useEditableImage")
        or publish_via == "studio"
        or (not local_install["installed"] and args.get("targetPath") and not cloud_result)
    )
    img_w = img_h = 0
    rgba_b64 = None
    ed_w = ed_h = 0
    try:
        img_w, img_h, rgba_raw = decode_any_to_rgba(raw_bytes, f"{safe_stem}{ext}")
        if need_editable:
            scale = min(1.0, 512.0 / max(img_w, img_h, 1))
            ed_w = max(1, int(round(img_w * scale)))
            ed_h = max(1, int(round(img_h * scale)))
            ed_bytes = resize_rgba(img_w, img_h, rgba_raw, ed_w, ed_h)
            rgba_b64 = _b64.b64encode(ed_bytes).decode("ascii")
    except Exception:
        if need_editable and publish_via == "studio":
            raise

    # 4. Send to live Studio when applying to a target, hydrating EditableImage, or publishing via Studio
    studio_apply = None
    health = client.health()
    if health.get("studio_connected") and (args.get("targetPath") or need_editable):
        studio_apply = _unwrap_job(client.submit_job("import_image", {
            "uri": best_uri,
            "preferEditable": bool(args.get("useEditableImage") or (not local_install["installed"] and not cloud_result)),
            "rgbaBase64": rgba_b64,
            "width": ed_w,
            "height": ed_h,
            "publishStudioAsset": publish_via == "studio",
            "displayName": safe_stem,
            "targetPath": args.get("targetPath"),
            "applyMode": args.get("applyMode", "auto"),
            "instanceName": args.get("instanceName") or safe_stem,
            "face": args.get("face", "Front"),
        }, note=f"import_image:{safe_stem}", wait=45.0, dm="edit", peer_id=peer_id))
        if studio_apply.get("publishedAssetId"):
            best_uri = f"rbxassetid://{studio_apply['publishedAssetId']}"

    manifest = None
    if args.get("saveToRepo", True):
        manifest = update_repo_asset_manifest({
            "key": _re.sub(r"[^A-Za-z0-9_]", "_", safe_stem),
            "cloudUri": best_uri if best_uri.startswith("rbxassetid://") else None,
        })
        if args.get("syncAssetsModule") and health.get("studio_connected"):
            install_kit_module_helper(client, "Assets", "ReplicatedStorage.Shared", peer_id=peer_id)

    return {
        "name": safe_stem,
        "uri": best_uri,
        "width": img_w,
        "height": img_h,
        "bytes": len(raw_bytes),
        "savedToRepo": repo_saved_path,
        "localStudioContentInstalled": local_install["installed"],
        "localContentPaths": local_install["installedPaths"],
        "cloudUpload": cloud_result,
        "studioApplied": studio_apply,
        "manifestUpdated": manifest["moduleFile"] if manifest else None,
    }


def import_images_batch_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    from image_pipeline import find_kit_root, install_into_studio_content, update_repo_asset_manifest

    kit = find_kit_root()
    images_dir = kit / "assets" / "images"
    explicit_files = args.get("files")
    subdir = (args.get("saveSubdir") or "imported").strip("/")

    items = []
    if explicit_files:
        file_list = [Path(p).expanduser() for p in explicit_files]
    else:
        scan_dir = Path(args["directory"]).expanduser() if args.get("directory") else images_dir
        file_list = [
            f for f in sorted(scan_dir.rglob("*"))
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp")
        ]

    for f in file_list:
        raw = f.read_bytes()
        try:
            rel = f.relative_to(images_dir).as_posix()
        except ValueError:
            rel = f"{subdir}/{f.name}"
            dest = images_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw)
        inst = install_into_studio_content(raw, rel)
        items.append({"file": str(f), "relPath": rel, "uri": inst["uri"], "localInstalled": inst["installed"]})

    manifest = update_repo_asset_manifest()
    synced_module = False
    if args.get("syncAssetsModule", True) and client.health().get("studio_connected"):
        install_kit_module_helper(client, "Assets", "ReplicatedStorage.Shared", peer_id=peer_id)
        synced_module = True

    return {
        "importedCount": len(items),
        "items": items,
        "manifestModule": manifest["moduleFile"],
        "syncedAssetsModuleToStudio": synced_module,
    }


def install_kit_module_helper(
    client: BridgeClient,
    module_name: str = "all",
    parent_path: str = "ReplicatedStorage.Shared",
    peer_id: str = None,
) -> dict:
    from image_pipeline import find_kit_root

    kit = find_kit_root()
    modules_dir = kit / "modules"
    if module_name == "all":
        files = sorted(modules_dir.glob("*.lua"))
    else:
        stem = module_name[:-4] if module_name.endswith(".lua") else module_name
        target = modules_dir / f"{stem}.lua"
        if not target.is_file():
            raise FileNotFoundError(f"Kit module not found: {target}")
        files = [target]

    parts = [p for p in parent_path.replace("/", ".").split(".") if p]
    children_specs = [
        {
            "name": f.stem,
            "className": "ModuleScript",
            "replaceExisting": True,
            "source": f.read_text(encoding="utf-8"),
        }
        for f in files
    ]

    if len(parts) >= 2:
        service_root = ".".join(parts[:-1])
        folder_name = parts[-1]
        tree = [{
            "name": folder_name,
            "className": "Folder",
            "replaceExisting": False,
            "children": children_specs,
        }]
        # Ensure folder exists or write directly if already present
        existing_check = client.submit_job("inspect", {"path": parent_path, "depth": 0}, wait=10.0, dm="edit", peer_id=peer_id)
        if existing_check.get("status") == "done":
            build_res = _unwrap_job(client.submit_job("build", {
                "parent": parent_path,
                "tree": children_specs,
                "select": False,
            }, note=f"install_kit:{module_name}", wait=45.0, dm="edit", peer_id=peer_id))
        else:
            build_res = _unwrap_job(client.submit_job("build", {
                "parent": service_root,
                "tree": tree,
                "select": False,
            }, note=f"install_kit:{module_name}", wait=45.0, dm="edit", peer_id=peer_id))
    else:
        build_res = _unwrap_job(client.submit_job("build", {
            "parent": parent_path,
            "tree": children_specs,
            "select": False,
        }, note=f"install_kit:{module_name}", wait=45.0, dm="edit", peer_id=peer_id))

    return {
        "installed": [f"{parent_path}.{f.stem}" for f in files],
        "count": len(files),
        "buildResult": build_res,
    }


def _offset_tree_positions(node: dict, dx: float, dy: float, dz: float):
    props = node.get("properties")
    if isinstance(props, dict):
        if "Position" in props:
            p = props["Position"]
            if isinstance(p, dict) and p.get("__t") == "Vector3":
                v = p["v"]
                props["Position"] = {"__t": "Vector3", "v": [v[0] + dx, v[1] + dy, v[2] + dz]}
            elif isinstance(p, list) and len(p) == 3:
                props["Position"] = [p[0] + dx, p[1] + dy, p[2] + dz]
        if "CFrame" in props:
            cf = props["CFrame"]
            if isinstance(cf, dict) and cf.get("__t") == "CFrame":
                v = list(cf["v"])
                v[0], v[1], v[2] = v[0] + dx, v[1] + dy, v[2] + dz
                props["CFrame"] = {"__t": "CFrame", "v": v}
    for ch in node.get("children") or []:
        _offset_tree_positions(ch, dx, dy, dz)


def spawn_prefab_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    import copy as _copy
    from image_pipeline import find_kit_root, install_into_studio_content

    kit = find_kit_root()
    prefab_arg = args["prefab"]
    cand = Path(prefab_arg)
    if not cand.is_file():
        stem = prefab_arg[:-5] if prefab_arg.endswith(".json") else prefab_arg
        cand = kit / "prefabs" / f"{stem}.json"
    if not cand.is_file():
        raise FileNotFoundError(f"Prefab not found: {prefab_arg}")

    spec = _copy.deepcopy(json.loads(cand.read_text(encoding="utf-8")))
    synced_imgs = []
    for rel_img in spec.get("requiredImages") or []:
        img_file = kit / "assets" / "images" / rel_img
        if img_file.is_file():
            inst = install_into_studio_content(img_file.read_bytes(), rel_img)
            synced_imgs.append(inst["uri"])

    if spec.get("lightingProperties"):
        _unwrap_job(client.submit_job("set_properties", {
            "path": "Lighting",
            "properties": spec["lightingProperties"],
        }, note=f"prefab_lighting:{spec.get('name')}", wait=30.0, dm="edit", peer_id=peer_id))

    tree = spec.get("tree") or []
    if args.get("name") and tree:
        tree[0]["name"] = args["name"]
    pos = args.get("position")
    if isinstance(pos, list) and len(pos) == 3:
        for root_item in tree:
            _offset_tree_positions(root_item, float(pos[0]), float(pos[1]), float(pos[2]))

    target_parent = args.get("parent") or spec.get("defaultParent") or "Workspace"
    build_res = _unwrap_job(client.submit_job("build", {
        "parent": target_parent,
        "tree": tree,
        "select": True,
    }, note=f"spawn_prefab:{spec.get('name')}", wait=45.0, dm="edit", peer_id=peer_id))

    return {
        "prefab": spec.get("name") or cand.stem,
        "parent": target_parent,
        "created": build_res.get("created", []),
        "syncedImages": synced_imgs,
    }


BUS_FALLBACK = (
    "Live bus state is unavailable (bridge offline, read-only client, or the bus is empty). "
    "Work from the handoff brief you were given and state any assumption you had to make."
)



# ---------------------------------------------------------------------------
# Live playtest control: VirtualInput, Device Simulator, StudioTestService
# ---------------------------------------------------------------------------

#: VirtualInput / StudioTestService / StudioDeviceSimulatorService were added in
#: these Studio engine versions; the number only explains an absent capability,
#: `playtest_capabilities` is what proves it.
DEVICE_FORMS = ("Phone", "Tablet", "Desktop", "Console", "VR")

PLAYTEST_API_BASELINE = {
    "VirtualInput": "0.720",
    "StudioDeviceSimulatorService": "0.717",
    "StudioTestService": "0.700",
    "StudioTestService:ExecuteMultiplayerTestAsync": "0.715",
}


def normalise_input_steps(steps: list) -> tuple:
    """Validate + canonicalise `simulate_input` / scenario input steps.

    Returns `(normalised, problems)`. Agents write friendly shapes
    (`{action:'click', target:'StarterGui.Main.Buy'}`); the plugin only ever
    sees `{action, position|[x,y], key, text, amount, ms}` plus an optional
    `target` path it resolves to viewport pixels itself.
    """
    normalised = []
    problems = []
    if not isinstance(steps, list):
        return [], ["`steps` must be an array of input step objects"]
    if not steps:
        return [], ["`steps` must contain at least one step"]
    if len(steps) > MAX_INPUT_STEPS:
        problems.append(f"`steps` is limited to {MAX_INPUT_STEPS} entries (got {len(steps)})")
    for index, raw in enumerate(steps[:MAX_INPUT_STEPS]):
        label = f"step {index + 1}"
        if not isinstance(raw, dict):
            problems.append(f"{label}: expected an object")
            continue
        action = str(raw.get("action") or "").strip().lower()
        if action not in INPUT_ACTIONS:
            problems.append(f"{label}: unknown action {action!r} (expected one of {', '.join(INPUT_ACTIONS)})")
            continue
        step = {"action": action}
        key = raw.get("key") or raw.get("keyCode")
        if action in INPUT_KEY_ACTIONS:
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,24}", key.strip()):
                problems.append(f"{label}: `{action}` needs an Enum.KeyCode name such as 'E' or 'LeftShift'")
                continue
            step["key"] = key.strip()
        if action == "type":
            text = raw.get("text")
            if not isinstance(text, str) or not text:
                problems.append(f"{label}: `type` needs a non-empty `text`")
                continue
            if len(text) > 512:
                problems.append(f"{label}: `text` is limited to 512 characters")
                continue
            step["text"] = text
        if action in INPUT_POSITION_ACTIONS:
            position = raw.get("position")
            target = raw.get("target")
            anchored = False
            if isinstance(position, (list, tuple)) and len(position) == 2:
                try:
                    step["position"] = [round(float(position[0]), 2), round(float(position[1]), 2)]
                    anchored = True
                except (TypeError, ValueError):
                    problems.append(f"{label}: `position` must be two numbers [x, y]")
                    continue
            elif isinstance(target, str) and target.strip():
                step["target"] = target.strip()
                anchored = True
            if action in ("drag", "swipe", "pinch"):
                pair = {}
                for side in ("from", "to"):
                    value = raw.get(side)
                    if isinstance(value, (list, tuple)) and len(value) == 2:
                        try:
                            pair[side] = [round(float(value[0]), 2), round(float(value[1]), 2)]
                        except (TypeError, ValueError):
                            problems.append(f"{label}: `{side}` must be two numbers [x, y]")
                            pair = None
                            break
                    else:
                        problems.append(f"{label}: `{action}` needs both `from` and `to` as [x, y]")
                        pair = None
                        break
                if pair is None:
                    continue
                step.update(pair)
                # A pinch may name a centre as well; it is optional garnish.
                centre = raw.get("center")
                if isinstance(centre, (list, tuple)) and len(centre) == 2:
                    try:
                        step["center"] = [round(float(centre[0]), 2), round(float(centre[1]), 2)]
                    except (TypeError, ValueError):
                        problems.append(f"{label}: `center` must be two numbers [x, y]")
                        continue
            elif not anchored:
                problems.append(f"{label}: `{action}` needs `position` [x, y] or a `target` instance path")
                continue
        if action == "wheel":
            try:
                step["amount"] = float(raw.get("amount", 1))
            except (TypeError, ValueError):
                problems.append(f"{label}: `amount` must be a number of wheel notches")
                continue
        ms = raw.get("ms", raw.get("durationMs"))
        if ms is not None:
            try:
                step["ms"] = max(0.0, min(float(ms), 10000.0))
            except (TypeError, ValueError):
                problems.append(f"{label}: `ms` must be a number (0-10000)")
                continue
        pointer = str(raw.get("pointerType") or "").strip().lower()
        if pointer:
            if pointer not in ("mouse", "touch", "pen"):
                problems.append(f"{label}: `pointerType` must be mouse, touch or pen")
                continue
            step["pointerType"] = pointer
        if action == "wait" and "ms" not in step:
            step["ms"] = 100.0
        normalised.append(step)
    return normalised, problems


def _input_receipt_summary(receipt: dict) -> dict:
    """Collapse a plugin receipt into the counts agents (and warnings) need."""
    steps = (receipt or {}).get("steps") or []
    delivered = sum(1 for s in steps if s.get("delivered"))
    refused = sum(1 for s in steps if str(s.get("reason") or "") == "coregui_refused")
    errored = sum(1 for s in steps if s.get("error"))
    return {
        "delivered": delivered,
        "refused": refused,
        "errors": errored,
        "total": len(steps),
    }


def simulate_input_helper(client: BridgeClient, args: dict, peer_id: str = None, dm: str = "auto") -> dict:
    """Deliver synthetic input to a live play client, then optionally capture it."""
    steps, problems = normalise_input_steps(args.get("steps") or [])
    if problems:
        raise ValueError("simulate_input: " + "; ".join(problems))
    timeout = max(5.0, min(float(args.get("timeout") or 45.0), 300.0))
    target_dm = "client" if (dm or "auto") in ("auto", "client") else dm
    result = _unwrap_job(client.submit_job(
        "virtual_input",
        {
            "steps": steps,
            "holdMs": max(0.0, min(float(args.get("holdMs") if args.get("holdMs") is not None else 60.0), 5000.0)),
            "settleMs": max(0.0, min(float(args.get("settleMs") if args.get("settleMs") is not None else 150.0), 15000.0)),
            "playerName": args.get("playerName"),
            # `_playerName` is what forwardJobToClient reads to pick the Player.
            "_playerName": args.get("playerName"),
        },
        wait=timeout,
        dm=target_dm,
        peer_id=peer_id,
    ))
    summary = _input_receipt_summary(result)
    warnings = []
    if result.get("unsupported"):
        warnings.append(
            "VirtualInput is unavailable in this Studio build (needs "
            f"{PLAYTEST_API_BASELINE['VirtualInput']}+); steps were not delivered."
        )
    if summary["refused"]:
        warnings.append(f"{summary['refused']} step(s) aimed at Roblox CoreGui and were refused by the engine.")
    if summary["errors"]:
        warnings.append(f"{summary['errors']} step(s) failed inside Studio; see `steps[].error`.")
    if summary["delivered"] == 0 and not result.get("unsupported"):
        warnings.append("No input was delivered — start a playtest first "
                        "(`start_stop_play` with `action=\"start_play\"`).")
    payload = {
        "ok": bool(result.get("unsupported")) is False and summary["delivered"] > 0,
        "delivered": summary["delivered"],
        "refused": summary["refused"],
        "errors": summary["errors"],
        "steps": result.get("steps", []),
        "dm": result.get("dm", target_dm),
        "viewport": result.get("viewport"),
        "focused": result.get("focused"),
        "warnings": warnings,
    }
    for key in ("unsupported", "reason", "cursorOverlay"):
        if key in result:
            payload[key] = result[key]
    if args.get("captureAfter") and not result.get("unsupported"):
        shot = capture_viewport_helper(client, {"perspective": "front", "includeHud": True}, peer_id=peer_id)
        if shot.get("_mcp_image_png_b64"):
            payload["_mcp_image_png_b64"] = shot["_mcp_image_png_b64"]
            payload["capturedAfter"] = True
        else:
            payload["warnings"].append("captureAfter produced no image: " + str(shot.get("reason") or "no peer"))
    return payload


#: `DeviceInfo` / `DeviceConfiguration` fields arrive from the engine in
#: PascalCase; every other field this bridge returns is camelCase, so list rows
#: are translated on the way out and callers never have to know which layer
#: produced them.
_DEVICE_INFO_KEYS = {
    "DeviceId": "deviceId", "Name": "name", "IsCustom": "isCustom",
    "DeviceForm": "deviceForm", "Width": "width", "Height": "height",
    "PixelDensity": "pixelDensity", "ResolutionScale": "resolutionScale",
    "PortraitKeyboardHeight": "portraitKeyboardHeight",
    "LandscapeKeyboardHeight": "landscapeKeyboardHeight",
}


def _device_info_row(row) -> dict:
    if not isinstance(row, dict):
        return {"deviceId": str(row), "name": str(row), "isCustom": False}
    return {_DEVICE_INFO_KEYS.get(key, key): value for key, value in row.items()}


def device_simulator_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    """Drive StudioDeviceSimulatorService through one job."""
    action = (args.get("action") or "status").strip().lower()
    if action == "set":
        # One job that applies whichever overrides were supplied, in the order the
        # engine needs (device first: resolution/DPI overrides are cleared by it).
        action = "set"
    payload = {"action": action}
    for key in ("deviceId", "orientation", "density", "scalingMode"):
        if args.get(key) is not None:
            payload[key] = args[key]
    if args.get("orientation") is not None:
        wanted = str(args["orientation"]).strip()
        alias = {"landscape": "LandscapeRight", "landscapeleft": "LandscapeLeft",
                 "landscaperight": "LandscapeRight", "portrait": "Portrait"}
        mapped = alias.get(wanted.lower(), wanted)
        if mapped not in ("Portrait", "LandscapeLeft", "LandscapeRight"):
            raise ValueError(
                "`orientation` must be Portrait, LandscapeLeft or LandscapeRight "
                "(the engine errors on anything else)"
            )
        payload["orientation"] = mapped
    if args.get("width") is not None or args.get("height") is not None:
        width = args.get("width")
        height = args.get("height")
        if not (isinstance(width, int) and isinstance(height, int) and width and height):
            raise ValueError("set_resolution needs both integer `width` and `height`")
        if not (1 <= width <= 7680 and 1 <= height <= 4320):
            raise ValueError("set_resolution is bounded by the engine to 1-7680 x 1-4320 pixels")
        payload["width"] = width
        payload["height"] = height
    if args.get("density") is not None:
        try:
            dpi = float(args["density"])
        except (TypeError, ValueError):
            raise ValueError("`density` must be a number of DPI")
        if not 72 <= dpi <= 10000:
            raise ValueError("`density` must be between 72 and 10000 DPI")
    if args.get("profile") is not None:
        profile = args["profile"]
        if not isinstance(profile, dict):
            raise ValueError("`profile` must be a DeviceConfiguration object")
        # The service speaks PascalCase; accept camelCase so agents do not have
        # to remember which casing each layer of the stack uses.
        canonical = {"name": "Name", "width": "Width", "height": "Height",
                     "pixelDensity": "PixelDensity", "deviceForm": "DeviceForm",
                     "resolutionScale": "ResolutionScale",
                     "portraitKeyboardHeight": "PortraitKeyboardHeight",
                     "landscapeKeyboardHeight": "LandscapeKeyboardHeight"}
        normalised = {}
        for key, value in profile.items():
            normalised[canonical.get(key, key)] = value
        name = str(normalised.get("Name") or "").strip()
        if action in ("create_profile", "update_profile"):
            if not re.fullmatch(r"[A-Za-z0-9_. -]{1,200}", name or "") or name.lower() == "default":
                raise ValueError("`profile.Name` must be 1-200 characters and cannot be 'default'")
        if action == "create_profile":
            for field, lo, hi in (("Width", 1, 7680), ("Height", 1, 4320), ("PixelDensity", 72, 10000)):
                value = normalised.get(field)
                if not (isinstance(value, (int, float)) and lo <= value <= hi):
                    raise ValueError(f"`profile.{field}` is required and must be within {lo}-{hi}")
        scale = normalised.get("ResolutionScale")
        if scale is not None and not (isinstance(scale, (int, float)) and 0 < scale <= 10):
            raise ValueError("`profile.ResolutionScale` must be greater than 0 and at most 10")
        form = normalised.get("DeviceForm")
        if isinstance(form, str) and form:
            # Kept as a name: the peer turns it into Enum.DeviceForm.<name>, and a
            # plain string survives JSON round-trips through the bridge intact.
            if form not in DEVICE_FORMS:
                raise ValueError("`profile.DeviceForm` must be one of: " + ", ".join(sorted(DEVICE_FORMS)))
            normalised["DeviceForm"] = form
        payload["profile"] = normalised
    if action not in DEVICE_SIMULATOR_ACTIONS:
        raise ValueError(
            "manage_device_simulator action must be one of: " + ", ".join(sorted(DEVICE_SIMULATOR_ACTIONS))
        )
    result = _unwrap_job(client.submit_job("device_simulator", payload, wait=60.0, dm="edit", peer_id=peer_id))
    result.setdefault("ok", True)
    if isinstance(result.get("devices"), list):
        result["devices"] = [_device_info_row(row) for row in result["devices"]]
    warnings = list(result.get("warnings") or [])
    if result.get("unsupported"):
        warnings.append(
            "StudioDeviceSimulatorService is unavailable in this Studio build (needs "
            f"{PLAYTEST_API_BASELINE['StudioDeviceSimulatorService']}+) — nothing was applied."
        )
    if action != "stop" and not result.get("unsupported") and result.get("applied"):
        warnings.append("The simulated device stays applied until you call `stop` (or set it back).")
    result["warnings"] = warnings
    result["baseline"] = PLAYTEST_API_BASELINE["StudioDeviceSimulatorService"]
    return result


def run_studio_tests_helper(client: BridgeClient, args: dict, peer_id: str = None) -> dict:
    """StudioTestService-backed native test session with a bounded watchdog."""
    mode = (args.get("mode") or "run").strip().lower()
    if mode not in ("run", "play", "multiplayer"):
        raise ValueError("run_studio_tests mode must be run, play or multiplayer")
    players = int(args.get("players") or 2)
    if mode == "multiplayer" and not (1 <= players <= 8):
        raise ValueError("multiplayer `players` must be between 1 and 8 (Studio's own limit)")
    timeout_sec = max(5.0, min(float(args.get("timeoutSec") or 120.0), 900.0))
    module = (args.get("module") or "").strip()
    if module and not re.fullmatch(r"(game\.)?[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,12}", module):
        raise ValueError("module must be a dot-separated instance path such as ServerScriptService.Tests.ShopTests")
    payload = {
        "mode": mode,
        "module": module or None,
        "players": players if mode == "multiplayer" else None,
        "args": args.get("args") if isinstance(args.get("args"), dict) else None,
        "timeoutSec": timeout_sec,
        "collectClientLogs": bool(args.get("collectClientLogs", True)),
        "keepOpen": bool(args.get("keepOpen", False)),
        "runnerName": f"__ArenaTestRunner_{uuid.uuid4().hex[:8]}",
    }
    result = _unwrap_job(client.submit_job("studio_tests", payload, wait=timeout_sec + 20.0, dm="edit", peer_id=peer_id))
    result.setdefault("mode", mode)
    result.setdefault("warnings", [])
    if result.get("unsupported"):
        result.setdefault("warnings", []).append(
            f"StudioTestService is unavailable in this Studio build (needs "
            f"{PLAYTEST_API_BASELINE['StudioTestService']}+; multiplayer needs "
            f"{PLAYTEST_API_BASELINE['StudioTestService:ExecuteMultiplayerTestAsync']})."
        )
    if result.get("timedOut"):
        result.setdefault("warnings", []).append(
            "The watchdog fired before EndTest ran; the temporary runner was destroyed and the session stopped."
        )
    result["ok"] = bool(result.get("ok", not result.get("failed"))) and not result.get("timedOut")
    return result


def playtest_capabilities_helper(client: BridgeClient, args: dict, peer_id: str = None, dm: str = "auto") -> dict:
    """Ask the live peer which playtest APIs actually exist right now."""
    result = _unwrap_job(client.submit_job(
        "playtest_caps", {"expect": PLAYTEST_API_BASELINE}, wait=30.0,
        dm=dm if dm and dm != "auto" else "edit", peer_id=peer_id,
    ))
    result.setdefault("ok", True)
    result.setdefault("baselineVersions", PLAYTEST_API_BASELINE)
    result.setdefault("warnings", [])
    studio_version = str(result.get("studioVersion") or "")
    for name, key, baseline_key in (
        ("simulate_input", "virtualInput", "VirtualInput"),
        ("manage_device_simulator", "deviceSimulator", "StudioDeviceSimulatorService"),
        ("run_studio_tests", "studioTestService", "StudioTestService"),
    ):
        cap = result.get(key) or {}
        if not cap.get("available"):
            result["warnings"].append(
                f"{name} is unavailable in this build (expected from Studio "
                f"{PLAYTEST_API_BASELINE[baseline_key]}+); probed, not assumed."
            )
    if result.get("playtest", {}).get("running") is False:
        result["warnings"].append("No playtest is running, so input and client-side capabilities cannot be exercised yet.")
    if studio_version:
        result.setdefault("notes", []).append(
            "The Studio version is reported for context only — availability above is what was actually probed."
        )
    return result


def _bus_lines(client, include_tasks: bool = True, tasks_include_done: bool = False) -> str:
    """Compact bus snapshot for prompt rendering; never raises."""
    if client is None:
        return BUS_FALLBACK
    try:
        stats = client.bus_action({"action": "stats"})
        roster = client.bus_action({"action": "agents", "includeOffline": True}).get("agents", [])
    except Exception:
        return BUS_FALLBACK
    lines = [
        f"Bus stats: {stats.get('agents')} agents ({stats.get('agentsOnline')} online), "
        f"{stats.get('tasks')} tasks, cursor {stats.get('cursor')}."
    ]
    if roster:
        lines.append("Roster:")
        for a in roster:
            state = "online" if a.get("online") else "offline"
            lines.append(
                f"  - {a.get('name')} ({a.get('role')}, {state}, open tasks {a.get('openTasks')}, "
                f"last seen {a.get('ageSeconds')}s ago)"
            )
    else:
        lines.append("Roster: _no agents registered_.")
    if include_tasks:
        try:
            tasks = client.bus_action({"action": "tasks", "includeDone": tasks_include_done}).get("tasks", [])
        except Exception:
            tasks = []
        if tasks:
            lines.append("Task board:")
            for t in tasks[:20]:
                owner = t.get("assignee") or "unclaimed"
                deps = t.get("dependsOn") or []
                dep_note = f", blocked by {', '.join(deps)}" if deps else ""
                lines.append(
                    f"  - {t.get('id')} [{t.get('status')}/{t.get('priority')}] {t.get('title')} "
                    f"(owner: {owner}{dep_note})"
                )
        else:
            lines.append("Task board: _empty_.")
    return "\n".join(lines)


def get_mcp_prompt(name: str, arguments: dict = None, client=None) -> dict:
    arguments = arguments or {}
    if name == "survey_and_architecture":
        text = (
            "1. Call `get_studio_state` and `survey_place` to inspect the place structure, scripts, remotes, and tags.\n"
            "2. Map ServerScriptService entry scripts, ReplicatedStorage shared modules/remotes, and StarterPlayerScripts client controllers.\n"
            "3. Check `get_console_output` for pre-existing warnings or errors.\n"
            "4. Summarize architecture, key tunables, and recommended next steps before mutating any instance."
        )
    elif name == "safe_module_edit":
        sp = arguments.get("scriptPath", "<ModulePath>")
        goal = arguments.get("goal", "<Goal>")
        text = (
            f"Safely modify `{sp}` to achieve: {goal}\n"
            f"1. Read `{sp}` with `script_read` and record its `hash`.\n"
            f"2. Apply targeted edits via `multi_edit` with `expectedHash`, `validateSyntax: true`, and `replaceInstance: true` (so Studio's ModuleScript `require()` cache does not serve stale code).\n"
            f"3. Verify behavior without side effects using `require_fresh` or an isolated Edit-mode probe at `y >= 2000`, asserting concrete numeric/boolean invariants and destroying temporary objects."
        )
    elif name == "automated_playtest_debug":
        sc = arguments.get("scenario", "<Scenario>")
        text = (
            f"Verify live runtime scenario: {sc}\n"
            "1. Use `run_playtest_scenario` (or `start_stop_play` -> `execute_luau` with `dm='server'` and `dm='client'` -> `get_console_output` -> `start_stop_play(action='stop')`).\n"
            "2. Always ensure play mode is stopped when verification completes, and persist permanent code fixes in `dm='edit'`."
        )
    elif name == "sync_repo_package":
        ld = arguments.get("localDir", "./src")
        sr = arguments.get("studioRoot", "ServerScriptService.App")
        text = (
            f"Synchronize `{ld}` -> `{sr}` safely:\n"
            f"1. Run `drift_and_sync(mode='check', localDir='{ld}', studioRoot='{sr}')` to inspect modified/only files.\n"
            f"2. Run `luau_check(path='{ld}')` to ensure zero syntax errors and zero undefined globals.\n"
            f"3. Run `drift_and_sync(mode='push', localDir='{ld}', studioRoot='{sr}')` (automatically snapshots live scripts and replaces instances in one undoable step).\n"
            f"4. Re-run `drift_and_sync(mode='check', ...)` to confirm `inSync: true`."
        )
    elif name == "multi_agent_handoff":
        who = arguments.get("agentName") or "<your agent name>"
        focus = arguments.get("focus") or "<what you are handing over>"
        snapshot = _bus_lines(client)
        text = (
            f"You are {who} and you are handing work to another agent on the same Roblox Studio bridge.\n\n"
            f"{snapshot}\n\n"
            "Do this now:\n"
            "1. Call `agent_bus {action:'export', summaryOnly:true}` and keep the brief short enough to paste into a message.\n"
            f"2. Release (or complete, with artifacts) every task you own that you will not finish. State why. Focus: {focus}\n"
            "3. Post exactly one handoff message: `agent_bus {action:'post', kind:'handoff', to:'<agent name>', "
            "subject:'handoff: <task id>', body:'<state, next concrete step, files touched>'} .\n"
            "4. Do not claim a new task until the handoff is posted; the next agent needs the lease and the note.\n"
            "5. Reply with the message id and the task ids you released."
        )
    elif name == "bus_standup":
        since = arguments.get("since")
        snapshot = _bus_lines(client)
        cursor_line = (
            f"Report only activity after cursor {since}."
            if since not in (None, "", 0)
            else "Use the current cursor as the starting point."
        )
        text = (
            "Produce a five-line standup for the humans watching this bridge.\n\n"
            f"{snapshot}\n\n"
            f"{cursor_line}\n"
            "Do this:\n"
            "1. Call `agent_bus {action:'export', since:<cursor>}` to get only recent activity.\n"
            "2. Summarise as exactly five lines: done / in progress / blocked / who is idle / next claim.\n"
            "3. Name task ids, not vibes. If nothing changed, say so in one line instead of padding.\n"
            "4. Post the standup with `agent_bus {action:'post', kind:'status', subject:'standup'}`."
        )
    elif name == "task_triage":
        snapshot = _bus_lines(client)
        text = (
            "Triage the open Agent Bus tasks so the team claims work in the right order.\n\n"
            f"{snapshot}\n\n"
            "Do this:\n"
            "1. Read the board with `agent_bus {action:'tasks', includeDone:false}`.\n"
            "2. Rank by: dependency depth (unblock others first), then priority (critical > high > normal > low), then age.\n"
            "3. Flag every task with no acceptance criteria, no owner and no detail; propose the one-line criterion to add.\n"
            "4. Output a numbered claim order with the task id, the owner you suggest, and the reason.\n"
            "5. Do not claim anything yourself unless the user asks you to; this prompt only plans."
        )
    else:
        raise KeyError(f"Unknown prompt: {name}")

    return {
        "description": next(p["description"] for p in PROMPTS if p["name"] == name),
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


# ---------------------------------------------------------------------------
# Protocol-Compliant JSON-RPC 2.0 MCP Request Dispatcher
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Request-scoped context (protocol era, principal, trace) for this thread
# ---------------------------------------------------------------------------

_CALL_CTX = threading.local()


def current_call_context() -> dict:
    """The MCP request context the current tool call belongs to (or None)."""
    return getattr(_CALL_CTX, "value", None)


def set_call_context(ctx: dict):
    """Install a request context; returns the previous one for nesting."""
    previous = getattr(_CALL_CTX, "value", None)
    _CALL_CTX.value = ctx
    return previous


def clear_call_context(previous):
    _CALL_CTX.value = previous


def take_subscription_stream(rpc_out) -> dict:
    """Extract (and strip) the private `_arenaStream` marker from a response.

    `subscriptions/listen` answers with an acknowledgement *plus* an instruction
    to keep the response stream open. Transports own that stream — stdio writes
    notifications to stdout, Streamable HTTP switches the POST response to SSE —
    so the marker is handed to them here and never reaches the wire.
    """
    candidates = []
    if isinstance(rpc_out, dict):
        candidates.append(rpc_out)
    elif isinstance(rpc_out, list):
        candidates.extend(item for item in rpc_out if isinstance(item, dict))
    for resp in candidates:
        result = resp.get("result")
        if isinstance(result, dict) and isinstance(result.get("_arenaStream"), dict):
            info = dict(result.pop("_arenaStream"))
            return {
                "subscriptionId": info.get("subscriptionId"),
                "maxSeconds": info.get("maxSeconds"),
                "pollSeconds": info.get("pollSeconds"),
                "ack": {"jsonrpc": "2.0", "id": resp.get("id"), "result": result},
            }
    return None


class MCPDispatcher:
    """Era-aware JSON-RPC 2.0 handler for stdio & Streamable HTTP.

    Modern era (`2026-07-28`) is served the way the stateless spec describes it:
    every request carries its own version/capabilities/principal in `_meta`,
    every result carries `resultType`, `tasks` is an announced extension
    (`io.modelcontextprotocol/tasks`) polled with `tasks/get` and answered with
    `tasks/update`, change notifications arrive through `subscriptions/listen`,
    and a dangerous call can pause for a human with Multi Round-Trip Requests
    (`resultType: "input_required"` + a signed, single-use `requestState`).

    Legacy clients (2025-11-25 and older) keep their original shapes
    byte-for-byte — including `initialize`, `tasks/result` and `tasks/list`,
    which the modern era no longer has.
    """

    def __init__(self, client: BridgeClient, gate=None, auto_task_ms: float = None,
                 legacy_wait_seconds: float = None, notifier=None):
        self.client = client
        # Task-augmented tool calls (the 2026-07-28 tasks extension, and the
        # 2025-11-25 in-core experiment) map onto a background execution of the
        # normal tool path, so any tool can be a task.
        self.tasks = {}
        self._task_threads = {}
        self._task_lock = threading.Lock()
        self.gate = gate if gate is not None else ConfirmGate(mode="off")
        if auto_task_ms is None:
            try:
                auto_task_ms = float(os.environ.get("BRIDGE_MCP_AUTO_TASK_MS") or 0)
            except (TypeError, ValueError):
                auto_task_ms = 0.0
        self.auto_task_ms = max(0.0, min(float(auto_task_ms or 0), 600_000.0))
        if legacy_wait_seconds is None:
            try:
                legacy_wait_seconds = float(os.environ.get("BRIDGE_CONFIRM_WAIT_SECONDS") or 45)
            except (TypeError, ValueError):
                legacy_wait_seconds = 45.0
        self.legacy_wait_seconds = max(0.0, min(float(legacy_wait_seconds or 0), 300.0))
        self.notifier = notifier
        self.subscriptions = {}
        self._sub_lock = threading.Lock()
        self._sub_seq = 0

    # -- visibility ----------------------------------------------------------

    def visible_tools(self) -> list:
        if not self.client.read_only:
            return TOOL_SPECS
        # In read-only Inspector mode, advertise only tools that are read-only
        # or have read-only query sub-actions, marking them clearly.
        allowed = []
        for t in TOOL_SPECS:
            if t["annotations"].get("readOnlyHint") or t["name"] in (
                "manage_attributes", "manage_tags", "manage_selection",
                "manage_camera", "undo_redo", "edit_terrain",
                "start_stop_play", "drift_and_sync", "manage_snapshots",
            ):
                copy_t = dict(t)
                copy_t["annotations"] = dict(t["annotations"], readOnlyHint=True, destructiveHint=False)
                allowed.append(copy_t)
        return allowed

    # -- entry points --------------------------------------------------------

    def handle_payload(self, payload):
        """Process a single JSON-RPC message dict or batch list.
        Returns None for notifications, or a JSON-serializable response dict/list.
        """
        if isinstance(payload, list):
            if not payload:
                return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Empty batch"}}
            responses = []
            for item in payload:
                res = self._handle_one(item)
                if res is not None:
                    responses.append(res)
            return responses if responses else None
        if isinstance(payload, dict):
            return self._handle_one(payload)
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid JSON-RPC request"}}

    def _ok(self, msg_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _err(self, msg_id, code: int, message: str, data=None) -> dict:
        err = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {"jsonrpc": "2.0", "id": msg_id, "error": err}

    @staticmethod
    def _meta_of(msg: dict, params: dict) -> dict:
        """Merge the two legal `_meta` homes; top-level request metadata wins."""
        nested = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        top = msg.get("_meta") if isinstance(msg.get("_meta"), dict) else {}
        merged = dict(nested)
        merged.update(top)
        return merged

    def request_context(self, msg: dict) -> dict:
        """Everything era-dependent about one request, in one dict.

        `x-arena-principal` is injected by server.py from the authenticated
        token so a confirmation can never be replayed by a different caller;
        stdio clients default to their clientInfo name and finally to `owner`.
        """
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        meta = self._meta_of(msg, params)
        requested = meta.get(META_PROTOCOL_VERSION) or params.get("protocolVersion")
        caps = meta.get(META_CLIENT_CAPABILITIES)
        if not isinstance(caps, dict):
            caps = {}
        extensions = caps.get("extensions") if isinstance(caps.get("extensions"), dict) else {}
        info = meta.get(META_CLIENT_INFO) if isinstance(meta.get(META_CLIENT_INFO), dict) else {}
        trace = {}
        for key in TRACE_META_KEYS:
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                trace[key] = value.strip()[:400]
        log_level = meta.get(META_LOG_LEVEL)
        principal = meta.get("x-arena-principal") or info.get("name") or "owner"
        return {
            "method": msg.get("method") or "",
            "meta": meta,
            "params": params,
            "requestedVersion": requested,
            "modern": requested == MODERN_PROTOCOL_VERSION,
            "capabilities": caps,
            "extensions": extensions,
            "clientInfo": info,
            # Presence, not truthiness: an empty `{}` is a full declaration.
            "tasks_capable": TASKS_EXTENSION_KEY in extensions or "tasks" in caps,
            "elicitation_capable": "elicitation" in caps,
            "logLevel": str(log_level).strip().lower() if isinstance(log_level, str) else None,
            "trace": trace,
            "principal": str(principal)[:80],
        }

    def _handle_one(self, msg: dict):
        """Validates the per-request protocol version, serves the modern-era
        methods, then decorates results for 2026-07-28 clients."""
        if not isinstance(msg, dict):
            return self._dispatch_method(msg)
        method = msg.get("method") or ""
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        ctx = self.request_context(msg)
        requested = ctx["requestedVersion"]
        if (requested and requested not in SUPPORTED_PROTOCOL_VERSIONS
                and method not in ("initialize", "server/discover")):
            return self._err(msg.get("id"), -32022, "Unsupported protocol version", {
                "requestedVersion": requested,
                "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            })
        modern = ctx["modern"]

        if method == "server/discover":
            return self._ok(msg.get("id"), self.discover_result())

        if method in ("tasks/get", "tasks/result", "tasks/list", "tasks/cancel", "tasks/update"):
            return self._handle_tasks(msg.get("id"), method, params, ctx)

        if method == "subscriptions/listen":
            try:
                ack = self.subscribe(params, ctx)
            except ValueError as exc:
                return self._err(msg.get("id"), -32602, str(exc))
            if modern and isinstance(ack, dict):
                ack.setdefault("resultType", "complete")
            return self._ok(msg.get("id"), ack)

        previous = set_call_context(ctx)
        try:
            resp = self._dispatch_method(msg, ctx)
        finally:
            clear_call_context(previous)

        if modern and isinstance(resp, dict) and isinstance(resp.get("result"), dict):
            result = resp["result"]
            result.setdefault("resultType", "complete")
            if method in ("tools/list", "resources/list", "resources/templates/list", "prompts/list"):
                result.setdefault("ttlMs", 60000)
                result.setdefault("cacheScope", "private")
            self._decorate_meta(result, ctx)
        return resp

    def _decorate_meta(self, result: dict, ctx: dict):
        """`serverInfo` on every modern result, plus per-request log shipping."""
        meta = dict(result.get("_meta") or {})
        meta.setdefault(META_SERVER_INFO, {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION})
        log_level = ctx.get("logLevel")
        if log_level:
            entries = self._capture_log_tail(ctx, log_level)
            if entries:
                if self.notifier:
                    for note in self._log_notifications(entries, ctx):
                        try:
                            self.notifier(note)
                        except Exception:  # noqa: BLE001 - a dead stream must not fail the call
                            break
                else:
                    # A single-shot Streamable HTTP response has no stream to
                    # carry notifications/message on, so the lines ride along in
                    # _meta instead. Documented in docs/OPS.md §13.
                    meta.setdefault("io.modelcontextprotocol/logEntries", entries[:50])
        result["_meta"] = meta

    @staticmethod
    def _log_level_rank(level: str) -> int:
        order = {"debug": 0, "info": 1, "notice": 2, "warning": 3, "warn": 3,
                 "error": 4, "critical": 5}
        return order.get(str(level).strip().lower(), 1)

    def _capture_log_tail(self, ctx: dict, level: str) -> list:
        """Bridge log lines produced while this request was in flight."""
        cursor = int(ctx.get("_logCursor") or 0)
        try:
            batch = self.client.ops_action({"action": "events", "cursor": cursor, "limit": 100})
        except Exception:  # noqa: BLE001 - logs are best-effort garnish
            return []
        minimum = self._log_level_rank(level)
        rows = []
        for event in (batch or {}).get("events", []):
            if self._log_level_rank(event.get("level") or "info") < minimum:
                continue
            rows.append({
                "level": event.get("level") or "info",
                "logger": event.get("kind"),
                "data": event.get("data") if isinstance(event.get("data"), str)
                        else json.dumps(event.get("data") or {}, default=str)[:400],
                "seq": event.get("seq"),
            })
        ctx["_logCursor"] = int((batch or {}).get("cursor") or cursor)
        return rows

    @staticmethod
    def _log_notifications(entries: list, ctx: dict) -> list:
        out = []
        for row in entries:
            out.append({
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {
                    "level": row["level"],
                    "logger": f"arena-bridge/{row['logger']}",
                    "data": row["data"],
                    "_meta": {"relatedRequest": ctx.get("method")},
                },
            })
        return out

    # -- discovery -----------------------------------------------------------

    def capabilities(self, modern: bool = False) -> dict:
        if not modern:
            return {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
                "tasks": {
                    "list": {},
                    "cancel": {},
                    "requests": {"tools": {"call": {}}},
                },
            }
        return {
            # The visible tool set really does change with the confirmation
            # policy and read-only mode, so the modern era advertises it.
            "tools": {"listChanged": True},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
            "subscriptions": {"types": list(SUBSCRIPTION_TYPES)},
            "extensions": {TASKS_EXTENSION_KEY: {"requests": {"tools": {"call": {}}}, "cancel": {}}},
            # Kept for clients that still read the 2025-11-25 capability name.
            "tasks": {
                "cancel": {},
                "requests": {"tools": {"call": {}}},
            },
        }

    def _instructions(self) -> str:
        mode_tag = " (Read-Only Inspector)" if self.client.read_only else ""
        lines = [
            f"Arena <-> Roblox Studio MCP Server v{MCP_SERVER_VERSION}{mode_tag}.",
            "Use `get_studio_state` or `survey_place` first to understand the open place.",
            "With several Studio windows connected, `studio_fleet {action:'list'}` shows them, "
            "`set_active` pins a default target and `broadcast` fans a read-only job out to all.",
            "Long work can be task-augmented (`params.task`): poll it with `tasks/get`, answer a "
            "`input_required` task with `tasks/update`, stop it with `tasks/cancel`.",
            "Live playtests are drivable: `playtest_capabilities` probes, `simulate_input` sends real "
            "keyboard/mouse/touch input, `manage_device_simulator` switches the device, and "
            "`run_studio_tests` runs Studio's own test session (up to 8 simulated clients).",
            "Use `multi_edit` / `write_script` (with `replaceInstance: true` when updating already-required "
            "ModuleScripts) and `luau_check` to prevent undefined-global runtime bugs.",
        ]
        if self.gate.enabled:
            lines.append(
                f"--require-confirm={self.gate.mode} is active: a gated call answers with "
                "`resultType: \"input_required\"` and a signed `requestState`; re-issue the same call "
                "with `inputResponses` (or have the operator approve it) to run it."
            )
        return " ".join(lines)

    def discover_result(self) -> dict:
        return {
            "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "protocolVersion": MODERN_PROTOCOL_VERSION,
            "capabilities": self.capabilities(modern=True),
            "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
            "instructions": self._instructions(),
            "ttlMs": 300000,
            "cacheScope": "public",
            "extensions": [
                {
                    "name": TASKS_EXTENSION_KEY,
                    "version": "1.0",
                    "methods": ["tasks/get", "tasks/update", "tasks/cancel"],
                }
            ],
            "subscriptions": {
                "types": list(SUBSCRIPTION_TYPES),
                "transport": "long-lived POST /mcp (or stdio) response stream",
                "idleTypes": list(SUBSCRIPTION_TYPES_IDLE),
            },
            "confirmation": {
                "mode": self.gate.mode,
                "ttlSeconds": self.gate.ttl_seconds,
                "gatedToolCount": len(self.gate.destructive_tools),
            },
        }

    # -- tasks (io.modelcontextprotocol/tasks) -------------------------------

    @staticmethod
    def _iso(ts: float) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

    def _task_view(self, task_id: str, modern: bool = False):
        with self._task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return None
            view = {
                "taskId": task["taskId"],
                "status": task["status"],
                "statusMessage": task.get("statusMessage"),
                "createdAt": self._iso(task["createdAt"]),
                "lastUpdatedAt": self._iso(task["updatedAt"]),
                "ttlMs": task["ttlMs"],
                "pollIntervalMs": TASK_POLL_INTERVAL_MS,
                "tool": task["tool"],
            }
            if modern:
                if task["status"] == "input_required":
                    view["inputRequests"] = task.get("inputRequests") or {}
                    if task.get("requestState"):
                        view["requestState"] = task["requestState"]
                elif task["status"] == "completed" and task.get("result") is not None:
                    view["result"] = task["result"]
                elif task["status"] == "failed":
                    view["error"] = task.get("error") or {
                        "code": -32603,
                        "message": task.get("statusMessage") or "task failed",
                    }
            return view

    def _task_list(self) -> list:
        with self._task_lock:
            items = sorted(self.tasks.values(), key=lambda t: t["createdAt"], reverse=True)
        return [
            {
                "taskId": t["taskId"],
                "status": t["status"],
                "statusMessage": t.get("statusMessage"),
                "createdAt": self._iso(t["createdAt"]),
                "lastUpdatedAt": self._iso(t["updatedAt"]),
                "ttlMs": t["ttlMs"],
                "pollIntervalMs": TASK_POLL_INTERVAL_MS,
            }
            for t in items
        ]

    def _task_cancel(self, task_id: str):
        request_id = None
        with self._task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return None
            if task["status"] in ("working", "input_required"):
                task["status"] = "cancelled"
                task["statusMessage"] = ("Cancelled by the client; the in-flight Studio job still "
                                         "finishes, its result is discarded.")
                task["result"] = None
                task["updatedAt"] = time.time()
                request_id = task.get("requestId")
        if request_id:
            try:
                self.gate.cancel(request_id, reason="task_cancelled")
            except Exception:  # noqa: BLE001
                pass
        return self._task_view(task_id)

    def _drop_unstarted_thread(self, task_id: str):
        with self._task_lock:
            self._task_threads.pop(task_id, None)

    def _task_prune(self):
        with self._task_lock:
            if len(self.tasks) <= TASK_HISTORY_LIMIT:
                return
            finished = sorted(
                (t for t in self.tasks.values() if t["status"] != "working"),
                key=lambda t: t["createdAt"],
            )
            for task in finished[: len(self.tasks) - TASK_HISTORY_LIMIT]:
                self.tasks.pop(task["taskId"], None)

    def _handle_tasks(self, msg_id, method: str, params: dict, ctx: dict):
        """`tasks/get` · `tasks/update` · `tasks/cancel` (both eras) plus the
        legacy `tasks/result` (blocking) and `tasks/list` the modern era dropped."""
        modern = ctx["modern"]
        if modern and method in ("tasks/list", "tasks/result"):
            return self._err(msg_id, -32601, f"'{method}' does not exist in the 2026-07-28 tasks extension", {
                "use": ["tasks/get", "tasks/update", "tasks/cancel"],
                "note": ("Poll tasks/get until the status is terminal; the final result is in its "
                         "'result' field."),
            })

        if method == "tasks/get":
            view = self._task_view((params or {}).get("taskId"), modern=modern)
            if view is None:
                return self._err(msg_id, -32602, f"Unknown task: {(params or {}).get('taskId')!r}")
            return self._ok(msg_id, view)

        if method == "tasks/result":
            tid = (params or {}).get("taskId")
            timeout_ms = (params or {}).get("timeoutMs") or 120000
            payload = self._task_result(tid, float(timeout_ms) / 1000.0)
            if payload is None:
                return self._err(msg_id, -32602, f"Unknown task: {tid!r}")
            return self._ok(msg_id, payload)

        if method == "tasks/list":
            return self._ok(msg_id, {"tasks": self._task_list()})

        if method == "tasks/cancel":
            tid = (params or {}).get("taskId")
            view = self._task_cancel(tid)
            if view is None:
                return self._err(msg_id, -32602, f"Unknown task: {tid!r}")
            return self._ok(msg_id, view)

        if method == "tasks/update":
            return self._task_update(msg_id, params or {}, ctx)

        return self._err(msg_id, -32601, f"Unknown task method: {method}")

    def _task_update(self, msg_id, params: dict, ctx: dict):
        """Client-to-server input on a live task (SEP-2663)."""
        task_id = params.get("taskId")
        with self._task_lock:
            task = self.tasks.get(task_id)
            if not task:
                return self._err(msg_id, -32602, f"Unknown task: {task_id!r}")
            status = task["status"]
            status_message = params.get("statusMessage")
            if status_message and isinstance(status_message, str) and status in ("working", "input_required"):
                task["statusMessage"] = status_message[:300]
                task["updatedAt"] = time.time()
            if status != "input_required":
                return self._err(msg_id, -32602, f"Task {task_id!r} is not waiting for input (status {status}).",
                                 {"status": status})
            request_id = task.get("requestId")
        responses = params.get("inputResponses", params.get("responses"))
        if responses is None:
            return self._err(msg_id, -32602, "tasks/update needs `inputResponses` for a task in input_required",
                             {"taskId": task_id, "requestId": request_id})
        if not ctx.get("elicitation_capable"):
            # Answering here is claiming to carry a human's decision. A client
            # that has not declared `elicitation` cannot make that claim; the
            # operator answers out-of-band instead (POST /api/confirmations).
            return self._err(msg_id, -32021,
                             "This client has not declared the elicitation capability, so it may not answer "
                             "a confirmation on a human's behalf",
                             {"requiredCapability": "elicitation", "requestId": request_id,
                              "operatorAction": f"POST /api/confirmations {{\"action\":\"approve\",\"requestId\":\"{request_id}\"}}"})
        try:
            decision = self.gate.resolve(
                request_id, responses, ctx.get("principal"), source="client",
                expected_digest=canonical_digest(task["tool"], task["args"]),
            )
        except GateError as exc:
            with self._task_lock:
                current = self.tasks.get(task_id) or {}
            if current.get("status") != "input_required":
                return self._ok(msg_id, {"taskId": task_id, "status": current.get("status"),
                                         "note": "the request was already answered ("
                                                 f"{exc.reason})"})
            return self._err(msg_id, -32602, exc.message, {"reason": exc.reason, "requestId": request_id})
        approved = decision.get("state") == "approved"
        with self._task_lock:
            task = self.tasks.get(task_id)
            status = task["status"] if task else "unknown"
            if task and status == "input_required":
                # The worker may already have finished (a fast approve, or an
                # out-of-band answer that arrived first) — never resurrect a
                # terminal task, and never overwrite its result.
                task["status"] = "working" if approved else "failed"
                task["statusMessage"] = (
                    f"{task['tool']} running after approval" if approved
                    else f"{task['tool']} declined: {decision.get('note') or 'no reason given'}"
                )
                if not approved:
                    task["error"] = {"code": -32602, "message": task["statusMessage"],
                                    "data": {"reason": "declined", "requestId": request_id}}
                    task["status"] = "failed"
                    task["result"] = {
                        "isError": True,
                        "content": [{"type": "text", "text": json.dumps({
                            "ok": False, "reason": "declined", "requestId": request_id,
                            "note": decision.get("note"),
                        })}],
                        "structuredContent": {"ok": False, "reason": "declined",
                                              "note": decision.get("note")},
                    }
                task["inputRequests"] = None
                task["requestState"] = None
                task["updatedAt"] = time.time()
                status = task["status"]
        return self._ok(msg_id, {
            "taskId": task_id,
            "status": status,
            "decision": decision.get("state"),
            "remembered": bool(decision.get("remembered")),
        })

    def _create_task(self, name: str, args: dict, task_params: dict, ctx: dict = None) -> dict:
        ttl_ms = int((task_params or {}).get("ttl") or TASK_DEFAULT_TTL_MS)
        task = self._start_task(name, args, ttl_ms=ttl_ms, ctx=ctx, start=False)
        handle = self._task_handle(task["taskId"])
        self._begin_task(task["taskId"])
        return handle

    def _begin_task(self, task_id: str):
        """Start a task registered with start=False."""
        with self._task_lock:
            thread = self._task_threads.pop(task_id, None)
        if thread is not None:
            thread.start()

    def _task_handle(self, task_id: str) -> dict:
        with self._task_lock:
            task = self.tasks.get(task_id) or {}
            handle = {
                "resultType": "task",
                "taskId": task_id,
                "status": task.get("status", "working"),
                "statusMessage": task.get("statusMessage"),
                "createdAt": self._iso(task.get("createdAt", time.time())),
                "lastUpdatedAt": self._iso(task.get("updatedAt", time.time())),
                "ttlMs": task.get("ttlMs", TASK_DEFAULT_TTL_MS),
                "pollIntervalMs": TASK_POLL_INTERVAL_MS,
            }
        if handle["status"] == "input_required":
            view = self._task_view(task_id, modern=True) or {}
            handle["inputRequests"] = view.get("inputRequests") or {}
            if view.get("requestState"):
                handle["requestState"] = view["requestState"]
        return handle

    def _start_task(self, name: str, args: dict, ttl_ms: int, ctx: dict = None, inline: bool = False,
                    start: bool = True) -> dict:
        """Register a background execution and start the worker immediately."""
        task_id = "task-" + uuid.uuid4().hex[:20]
        now = time.time()
        ctx = ctx or {}
        initial_message = f"Running {name}…"
        task = {
            "taskId": task_id,
            "tool": name,
            "args": args or {},
            "status": "working",
            "statusMessage": initial_message,
            "createdAt": now,
            "updatedAt": now,
            "ttlMs": ttl_ms,
            "ttlDeadline": now + ttl_ms / 1000.0,
            "result": None,
            "error": None,
            "principal": ctx.get("principal", "owner"),
            "modern": bool(ctx.get("modern")),
            "elicitation": bool(ctx.get("elicitation_capable")),
            "inline": inline,
            "requestId": None,
            "inputRequests": None,
            "requestState": None,
            "trace": dict(ctx.get("trace") or {}),
        }
        with self._task_lock:
            self.tasks[task_id] = task
        self._task_prune()
        self._notify_subscribers("notifications/tasks", {"taskId": task_id, "status": "working"})

        def publish_waiting(request_id, input_requests, state_token):
            with self._task_lock:
                current = self.tasks.get(task_id)
                if not current or current["status"] not in ("working", "input_required"):
                    return
                current["status"] = "input_required"
                current["requestId"] = request_id
                current["inputRequests"] = input_requests
                current["requestState"] = state_token
                current["statusMessage"] = (
                    f"{name} is waiting for a human to approve it "
                    f"(requestId {request_id}); answer with tasks/update."
                )
                current["updatedAt"] = time.time()
            self._notify_subscribers("notifications/tasks", {"taskId": task_id, "status": "input_required"})

        def worker():
            ctx_token = set_call_context({
                "principal": task["principal"], "trace": task["trace"], "modern": task["modern"],
                "logLevel": None, "_logCursor": 0,
            })
            try:
                gate = self._gate_for_task(name, args, task, publish_waiting)
                if gate is not None and gate.get("verdict") != "run":
                    payload = gate.get("result") or {
                        "isError": True,
                        "content": [{"type": "text", "text": json.dumps(gate.get("error") or {})}],
                        "structuredContent": {"ok": False, **(gate.get("error") or {})},
                    }
                else:
                    payload = self._invoke_tool(name, args)
            except Exception as exc:  # noqa: BLE001 - surface into the task
                payload = {
                    "isError": True,
                    "content": [{"type": "text", "text": f"{name} failed: {exc}"}],
                    "structuredContent": {"ok": False, "error": str(exc)},
                }
            finally:
                clear_call_context(ctx_token)
            with self._task_lock:
                current = self.tasks.get(task_id)
                if not current:
                    return
                if current["status"] == "cancelled":
                    current["updatedAt"] = time.time()
                    return
                current["result"] = payload
                failed = bool(payload.get("isError"))
                current["status"] = "failed" if failed else "completed"
                current["statusMessage"] = f"{name} failed" if failed else f"{name} completed"
                if failed:
                    current["error"] = {
                        "code": -32603,
                        "message": f"{name} failed",
                        "data": (payload.get("structuredContent") or {}).get("reason"),
                    }
                current["updatedAt"] = time.time()
            self._notify_subscribers("notifications/tasks", {
                "taskId": task_id,
                "status": "failed" if payload.get("isError") else "completed",
            })

        thread = threading.Thread(target=worker, name=f"mcp-task-{task_id[-6:]}", daemon=True)
        if start:
            thread.start()
        else:
            with self._task_lock:
                self._task_threads[task_id] = thread
        return task

    def _task_result(self, task_id: str, timeout: float):
        """Blocking result collection — 2025-11-25 era only."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._task_lock:
                task = self.tasks.get(task_id)
                if not task:
                    return None
                if task["status"] in ("completed", "failed", "cancelled"):
                    payload = task["result"] or {
                        "isError": True,
                        "content": [{"type": "text", "text": task.get("statusMessage") or task["status"]}],
                    }
                    payload = dict(payload)
                    payload["_meta"] = {META_RELATED_TASK: {"taskId": task_id}}
                    payload["resultType"] = "complete"
                    return payload
            if time.monotonic() >= deadline:
                return {
                    "resultType": "task",
                    "taskId": task_id,
                    "status": "working",
                    "statusMessage": "Still running; call tasks/result again.",
                    "pollIntervalMs": TASK_POLL_INTERVAL_MS,
                }
            time.sleep(0.05)

    # -- confirmation gate (Multi Round-Trip Requests) -----------------------

    def _gate_pre(self, name: str, args: dict, params: dict, ctx: dict) -> dict:
        """Decide whether this `tools/call` may run. Returns None when it may."""
        gate = self.gate
        if not gate.enabled:
            return None
        spec = TOOL_MAP.get(name) or {}
        # Reading the queue AND answering it stay ungated: an approval is itself the
        # authorization step, so gating it with a gate would be circular. The guard
        # that matters is the self-approval refusal in `_local_ops`.
        if name == "bridge_ops" and str((args or {}).get("action") or "") in (
                CONFIRM_CONTROL_ACTIONS + CONFIRM_ANSWER_ACTIONS):
            return None  # the queue stays reachable while the gate is closed
        if self.gate.mode != "all" and is_tool_call_read_only(name, args or {}):
            return None  # a `status` / `dryRun` action of a write tool is not a write
        if not gate.requires(name, spec.get("annotations") or {}):
            return None
        principal = ctx.get("principal", "owner")
        if gate.allowance_active(name, principal):
            return None

        state_token = params.get("requestState")
        digest = canonical_digest(name, args)
        if state_token:
            try:
                payload = gate.parse_state(state_token)
            except GateError as exc:
                return self._reprompt_or_error(name, args, exc, ctx)
            if payload.get("dig") != digest:
                return self._reprompt_or_error(
                    name, args,
                    GateError("digest_mismatch",
                              "The retried call is not the call the confirmation was issued for."),
                    ctx,
                )
            request_id = payload.get("id")
            try:
                decided = gate.take_decision(request_id, principal)
            except GateError as exc:
                return self._reprompt_or_error(name, args, exc, ctx)
            if decided is not None:
                if not decided.get("approved"):
                    return self._confirmation_denied(name, decided, ctx)
                self._publish("mcp.confirm.applied", {"requestId": request_id, "tool": name,
                                                       "source": decided.get("source")})
                return None
            if not ctx.get("elicitation_capable"):
                rec = gate.pending(request_id)
                if rec is None:
                    return self._reprompt_or_error(
                        name, args,
                        GateError("unknown_request", "That confirmation is no longer pending; "
                                                      "re-issue the call."), ctx)
                return self._input_required(name, args, rec, ctx, note=(
                    "This client has not declared the `elicitation` capability, so it cannot answer on a "
                    "human's behalf. The bridge operator must approve it "
                    "(`arena_studio.py approve <requestId>` or the Ops console), then retry this call."
                ))
            try:
                decision = gate.resolve(request_id, params.get("inputResponses"), principal,
                                        source="client", expected_digest=digest)
            except GateError as exc:
                return self._reprompt_or_error(name, args, exc, ctx)
            if decision.get("state") != "approved":
                return self._confirmation_denied(name, decision, ctx)
            return None

        # First sight of the call: open a request and answer per era.
        rec = gate.request(name, args, principal, danger_keys_in(args))
        if ctx.get("modern"):
            return self._input_required(name, args, rec, ctx)
        # Legacy clients have no MRTR round-trip, so hold the call for an
        # out-of-band approval instead (bounded by BRIDGE_CONFIRM_WAIT_SECONDS).
        answered = gate.wait(rec, min(gate.ttl_seconds, self.legacy_wait_seconds) or gate.ttl_seconds)
        if answered and rec.state == "approved":
            return None
        if not answered:
            return self._confirmation_error(name, GateError(
                "confirmation_timeout",
                f"No decision within {int(self.legacy_wait_seconds)}s. Approve it with "
                f"`arena_studio.py approve {rec.id}` (or the Ops console) and retry the same call.",
            ), ctx, extra={"requestId": rec.id, "expiresAt": self._iso(rec.expires)})
        return self._confirmation_denied(name, {"state": rec.state, "note": rec.note,
                                                "requestId": rec.id}, ctx)

    def _gate_for_task(self, name: str, args: dict, task: dict, publish_waiting) -> dict:
        """Task workers cannot answer with `input_required` (the handle is already
        out), so the task itself goes to `input_required` and waits."""
        gate = self.gate
        if not gate.enabled:
            return None
        spec = TOOL_MAP.get(name) or {}
        # Reading the queue AND answering it stay ungated: an approval is itself the
        # authorization step, so gating it with a gate would be circular. The guard
        # that matters is the self-approval refusal in `_local_ops`.
        if name == "bridge_ops" and str((args or {}).get("action") or "") in (
                CONFIRM_CONTROL_ACTIONS + CONFIRM_ANSWER_ACTIONS):
            return None
        if self.gate.mode != "all" and is_tool_call_read_only(name, args or {}):
            return None  # a `status` / `dryRun` action of a write tool is not a write
        if not gate.requires(name, spec.get("annotations") or {}):
            return None
        principal = task.get("principal") or "owner"
        if gate.allowance_active(name, principal):
            return None
        rec = gate.request(name, args, principal, danger_keys_in(args))
        publish_waiting(rec.id, rec.to_input_requests(), gate.state_token(rec))
        answered = gate.wait(rec)
        if not answered:
            return {"verdict": "answer", "result": {
                "isError": True,
                "content": [{"type": "text", "text": json.dumps({
                    "ok": False, "reason": "confirmation_expired", "requestId": rec.id,
                    "tool": name,
                    "hint": "The approval window closed. Re-issue the call (or raise --confirm-ttl).",
                }, indent=2)}],
                "structuredContent": {"ok": False, "reason": "confirmation_expired", "requestId": rec.id},
            }}
        if rec.state != "approved":
            return {"verdict": "denied", "result": {
                "isError": True,
                "content": [{"type": "text", "text": json.dumps({
                    "ok": False, "reason": "declined", "requestId": rec.id, "tool": name,
                    "note": rec.note, "decidedBy": rec.decision_source,
                }, indent=2)}],
                "structuredContent": {"ok": False, "reason": "declined", "note": rec.note,
                                      "requestId": rec.id},
            }}
        return {"verdict": "run"}

    def _input_required(self, name: str, args: dict, rec, ctx: dict, note: str = None,
                        reason: str = None) -> dict:
        """`InputRequiredResult` (SEP-2322): the request is over, the answer
        comes back on a retry that echoes `requestState` verbatim."""
        result = {
            "resultType": "input_required",
            "inputRequests": rec.to_input_requests({"mode": self.gate.mode}),
            "requestState": self.gate.state_token(rec),
            "isError": False,
            "content": [{
                "type": "text",
                "text": (note + "\n\n" if note else "") + (
                    f"`{name}` needs human approval before it runs "
                    f"(bridge confirmation mode: {self.gate.mode}). Answer the elicitation, then "
                    f"re-issue this exact tools/call with the same arguments plus "
                    f"`params.inputResponses` and the unchanged `params.requestState`. "
                    f"requestId {rec.id} · expires {self._iso(rec.expires)}."
                ),
            }],
            "structuredContent": {
                "ok": False,
                "reason": reason or "confirmation_required",
                "tool": name,
                "requestId": rec.id,
                "mode": self.gate.mode,
                "expiresAt": self._iso(rec.expires),
                "arguments": args,
                "howToAnswer": {
                    "modern": "retry tools/call with params.inputResponses + params.requestState",
                    "outOfBand": f"POST /api/confirmations {{\"action\":\"approve\",\"requestId\":\"{rec.id}\"}}"
                                 f" or `arena_studio.py approve {rec.id}`",
                },
            },
        }
        if note:
            result["structuredContent"]["note"] = note
        return result

    def _confirmation_denied(self, name: str, decision: dict, ctx: dict) -> dict:
        payload = {
            "ok": False,
            "reason": "declined",
            "tool": name,
            "requestId": decision.get("requestId"),
            "note": decision.get("note"),
            "decidedBy": decision.get("source"),
        }
        self._publish("mcp.confirm.denied_result", payload)
        return {
            "isError": True,
            "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
            "structuredContent": payload,
        }

    def _reprompt_or_error(self, name: str, args: dict, exc, ctx: dict) -> dict:
        """A wrong or spent answer re-prompts a modern client instead of
        failing it; legacy clients (no MRTR) get the error payload."""
        if ctx.get("modern") and exc.reason in REPROMPT_REASONS:
            principal = ctx.get("principal", "owner")
            rec = self.gate.request(name, args, principal, danger_keys_in(args))
            return self._input_required(name, args, rec, ctx, note=exc.message,
                                        reason=exc.reason)
        return self._confirmation_error(name, exc, ctx)

    def _confirmation_error(self, name: str, exc, ctx: dict, extra: dict = None) -> dict:
        payload = {"ok": False, "reason": exc.reason, "tool": name, "error": exc.message,
                   "confirmationMode": self.gate.mode}
        if extra:
            payload.update(extra)
        return {
            "isError": True,
            "content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
            "structuredContent": payload,
        }

    def confirmations(self, params: dict = None) -> dict:
        """Read (and let operators answer) the pending-confirmation registry."""
        params = dict(params or {})
        action = (params.get("action") or "status").strip().lower()
        if action == "approve":
            decision = self.gate.resolve(
                params.get("requestId") or params.get("id"),
                {"approve": True, "remember": bool(params.get("remember")), "note": params.get("note")},
                source=params.get("source") or "operator",
            )
            self._publish("mcp.confirm.approved", decision)
            return {"ok": True, **decision}
        if action == "deny":
            decision = self.gate.resolve(
                params.get("requestId") or params.get("id"),
                {"approve": False, "note": params.get("note")},
                source=params.get("source") or "operator",
            )
            self._publish("mcp.confirm.denied", decision)
            return {"ok": True, **decision}
        if action == "expire":
            return {"ok": True, "swept": self.gate.expire_stale()}
        status = self.gate.status()
        status["ok"] = True
        return status

    def _publish(self, kind: str, detail: dict):
        try:
            self.client.publish_event(kind, detail)
        except Exception:  # noqa: BLE001 - observability is never allowed to break a call
            pass

    # -- subscriptions/listen ------------------------------------------------

    def subscribe(self, params: dict, ctx: dict = None) -> dict:
        """Open an opt-in change-notification subscription (2026-07-28).

        The returned result carries `_arenaStream`, which the transport turns
        into a long-lived response stream feeding `pump_subscription`.
        """
        ctx = ctx or {}
        requested = (params or {}).get("types")
        if not isinstance(requested, list) or not requested:
            raise ValueError("subscriptions/listen needs a non-empty `types` array "
                             f"(available: {', '.join(SUBSCRIPTION_TYPES)})")
        wanted = [str(t).strip() for t in requested]
        accepted = [t for t in wanted if t in SUBSCRIPTION_TYPES]
        unavailable = [t for t in wanted if t in SUBSCRIPTION_TYPES_IDLE]
        unknown = [t for t in wanted if t not in SUBSCRIPTION_TYPES and t not in SUBSCRIPTION_TYPES_IDLE]
        if not accepted:
            raise ValueError(f"No subscribable notification types in {unknown or wanted}; "
                             f"this bridge offers: {', '.join(SUBSCRIPTION_TYPES)}")
        resources = (params or {}).get("resources")
        if resources is not None and not isinstance(resources, list):
            raise ValueError("`resources` must be an array of studio:// URIs")
        if "resourceSubscriptions" in accepted:
            if resources:
                known = {r["uri"] for r in RESOURCES}
                bad = [u for u in resources if u not in known]
                if bad:
                    raise ValueError(f"Unknown resource URIs: {', '.join(map(str, bad))}")
                targets = list(resources)
            else:
                targets = [uri for uri in RESOURCE_EVENT_TRIGGERS]
            accepted = [t for t in accepted if t != "resourceSubscriptions"] + ["resourceSubscriptions"]
        else:
            targets = []
        sub_id = "sub-" + uuid.uuid4().hex[:12]
        with self._sub_lock:
            self._sub_seq += 1
            self.subscriptions[sub_id] = {
                "id": sub_id,
                "types": accepted,
                "resources": targets,
                "principal": ctx.get("principal") or "owner",
                "created": time.time(),
                "cursor": int((params or {}).get("cursor") or 0),
                "toolsFingerprint": None,
                "emitted": 0,
                "sequence": self._sub_seq,
            }
        ack = {
            "subscriptionId": sub_id,
            "types": accepted,
            "resources": targets,
            "keepAliveSeconds": 15,
            "_arenaStream": {"subscriptionId": sub_id,
                             "maxSeconds": (params or {}).get("maxSeconds"),
                             "pollSeconds": (params or {}).get("pollSeconds")},
        }
        if unavailable:
            ack["idleTypes"] = unavailable
            ack["note"] = (f"{', '.join(unavailable)} are acknowledged but cannot fire: this server's "
                           "prompt and resource lists are static.")
        if unknown:
            ack["unknownTypes"] = unknown
        return ack

    def drop_subscription(self, sub_id: str) -> dict:
        with self._sub_lock:
            return {"dropped": self.subscriptions.pop(sub_id, None) is not None}

    def subscription_status(self) -> dict:
        with self._sub_lock:
            rows = list(self.subscriptions.values())
        return {
            "count": len(rows),
            "subscriptions": [
                {"subscriptionId": r["id"], "types": r["types"], "resources": r["resources"],
                 "emitted": r["emitted"], "ageSeconds": round(time.time() - r["created"], 1)}
                for r in rows
            ],
        }

    def _notify_subscribers(self, method: str, params: dict):
        """Fan a notification out to every subscription that opted in to it."""
        if not self.subscriptions:
            return []
        sent = 0
        with self._sub_lock:
            rows = list(self.subscriptions.values())
        for rec in rows:
            if method == "notifications/tasks":
                continue  # task progress is polled; documented in docs/OPS.md §13
            if method == "notifications/tools/list_changed" and "toolsListChanged" not in rec["types"]:
                continue
            if method == "notifications/resources/updated" and "resourceSubscriptions" not in rec["types"]:
                continue
            meta = dict(rec.get("meta") or {})
            meta[META_SUBSCRIPTION_ID] = rec["id"]
            note = {"jsonrpc": "2.0", "method": method,
                    "params": {**dict(params or {}), "_meta": meta}}
            sink = rec.get("sink")
            if callable(sink):
                try:
                    sink(note)
                    rec["emitted"] += 1
                    sent += 1
                except Exception:  # noqa: BLE001 - a dead stream ends that subscriber
                    with self._sub_lock:
                        self.subscriptions.pop(rec["id"], None)
        return sent

    def _event_to_updates(self, rec: dict, event: dict) -> list:
        """Map one bridge telemetry event onto resource-update notifications."""
        kind = event.get("kind") or ""
        out = []
        for uri in rec.get("resources") or []:
            triggers = RESOURCE_EVENT_TRIGGERS.get(uri)
            if not triggers:
                continue
            if kind in triggers or any(kind.startswith(t) for t in triggers if t.endswith(".")):
                out.append({
                    "jsonrpc": "2.0",
                    "method": "notifications/resources/updated",
                    "params": {
                        "uri": uri,
                        "timestamp": self._iso(time.time()),
                        "_meta": {META_SUBSCRIPTION_ID: rec["id"],
                                  "io.modelcontextprotocol/eventKind": kind},
                    },
                })
        return out

    def tools_fingerprint(self) -> str:
        import hashlib as _hashlib
        names = ",".join(sorted(t["name"] for t in self.visible_tools()))
        return _hashlib.sha256(
            f"{names}|read_only={int(bool(self.client.read_only))}|confirm={self.gate.mode}".encode()
        ).hexdigest()[:16]

    def pump_subscription(self, sub_id: str, sink, stop_event: threading.Event = None,
                          max_seconds: float = None, poll_seconds: float = 0.5) -> dict:
        """Write change notifications for one subscription until it ends.

        `sink(notification_dict)` is provided by the transport (stdout for
        stdio, one SSE frame per notification for Streamable HTTP).
        """
        with self._sub_lock:
            rec = self.subscriptions.get(sub_id)
        if rec is None:
            return {"ok": False, "reason": f"Unknown subscription: {sub_id!r}"}
        rec["sink"] = sink
        started = time.monotonic()
        cursor = rec["cursor"]
        poll_seconds = max(0.05, min(float(poll_seconds or 0.5), 10.0))
        emitted = 0
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                if max_seconds is not None and time.monotonic() - started > float(max_seconds):
                    break
                try:
                    batch = self.client.ops_action({"action": "events", "cursor": cursor, "limit": 60})
                except Exception:  # noqa: BLE001 - the bridge may be mid-restart
                    time.sleep(poll_seconds)
                    continue
                for event in (batch or {}).get("events", []):
                    seq = event.get("seq")
                    if isinstance(seq, int):
                        cursor = max(cursor, seq)
                        rec["cursor"] = cursor
                    for note in self._event_to_updates(rec, event):
                        try:
                            sink(note)
                            emitted += 1
                            rec["emitted"] += 1
                        except Exception:  # noqa: BLE001
                            return {"ok": False, "reason": "stream closed", "emitted": emitted}
                if "toolsListChanged" in rec["types"]:
                    fingerprint = self.tools_fingerprint()
                    if rec["toolsFingerprint"] is None:
                        rec["toolsFingerprint"] = fingerprint
                    elif fingerprint != rec["toolsFingerprint"]:
                        rec["toolsFingerprint"] = fingerprint
                        try:
                            sink({"jsonrpc": "2.0", "method": "notifications/tools/list_changed",
                                  "params": {"_meta": {META_SUBSCRIPTION_ID: sub_id}}})
                            emitted += 1
                            rec["emitted"] += 1
                        except Exception:  # noqa: BLE001
                            return {"ok": False, "reason": "stream closed", "emitted": emitted}
                if max_seconds is not None and time.monotonic() - started > float(max_seconds) - poll_seconds:
                    break
                time.sleep(poll_seconds)
        finally:
            with self._sub_lock:
                active = self.subscriptions.get(sub_id)
                if active is not None:
                    active["sink"] = None
            self._emit_subscription_end(sub_id, emitted, cursor)
        return {"ok": True, "emitted": emitted, "cursor": cursor,
                "seconds": round(time.monotonic() - started, 2)}

    def _emit_subscription_end(self, sub_id: str, emitted: int, cursor: int):
        with self._sub_lock:
            rec = self.subscriptions.pop(sub_id, None)
        if rec is None:
            return
        self._publish("mcp.subscription.closed", {"subscriptionId": sub_id, "emitted": emitted,
                                                   "cursor": cursor,
                                                   "seconds": round(time.time() - rec["created"], 1)})

    # -- tool invocation -----------------------------------------------------

    def _local_ops(self, tname: str, targs: dict) -> dict:
        """`bridge_ops` actions that describe *this* dispatcher, not the bridge.

        Pending confirmations and live subscriptions belong to the process that
        answers the JSON-RPC — a stdio proxy attached to a remote bridge must not
        be pointed at that bridge's registry, or it would be approving a different
        connection's requests. Returns None for everything else.
        """
        if tname != "bridge_ops":
            return None
        action = str((targs or {}).get("action") or "").strip().lower()
        if action in CONFIRM_ANSWER_ACTIONS:
            # Deliberate: the connection whose call is parked on a human is not
            # the one allowed to un-park it. Reading the queue stays available,
            # and a *different* principal (an operator-side agent, a reviewer)
            # may still answer through this tool.
            request_id = str((targs or {}).get("requestId") or (targs or {}).get("id") or "")
            blocked = self.gate.pending(request_id)
            caller = (current_call_context() or {}).get("principal") or "owner"
            if blocked is not None and self.gate.same_principal(blocked.principal, caller):
                return self._self_approval_refusal(action)
            verb = action.replace("_", " ")
            if blocked is None:
                return {
                    "ok": False,
                    "reason": "unknown_request",
                    "error": f"{verb}: no confirmation {request_id!r} is pending on this connection.",
                    "pending": [row["requestId"] for row in self.gate.status()["pending"]],
                }
            return self.confirmations({
                "action": "approve" if action == "confirm_approve" else "deny",
                "requestId": request_id,
                "remember": (targs or {}).get("remember"),
                "note": (targs or {}).get("note"),
                "source": "mcp-operator",
            })
        if action == "confirmations":
            return self.confirmations(targs)
        if action == "subscriptions":
            return {"ok": True, **self.subscription_status()}
        return None

    def _self_approval_refusal(self, action: str) -> dict:
        """Why an agent cannot approve its own parked call, and what to do instead."""
        capable = bool((current_call_context() or {}).get("elicitation_capable"))
        verb = action.replace("_", " ")
        return {
            "ok": False,
            "reason": "operator_only",
            "error": (
                f"{verb} a confirmation is an operator decision: the connection whose call is "
                "waiting is not allowed to answer it."
            ),
            "howToAnswer": [
                "arena_studio.py approve <requestId>   /   arena_studio.py deny <requestId>",
                "the Ops console\'s Confirmations tab",
                ("retry tools/call with requestState + inputResponses (this client declares the "
                 "`elicitation` capability)") if capable else
                ("an MCP client that declares the `elicitation` capability can answer in-band with "
                 "requestState + inputResponses"),
            ],
            "pending": [row["requestId"] for row in self.gate.status()["pending"]],
        }

    def _invoke_tool(self, tname: str, targs: dict) -> dict:
        """Run one tool call and return the MCP CallToolResult payload."""
        local = self._local_ops(tname, targs)
        if local is not None:
            return {
                "isError": False,
                "content": [{"type": "text", "text": json.dumps(local, indent=2)}],
                "structuredContent": local,
            }
        if self.client.read_only and not is_tool_call_read_only(tname, targs):
            return {
                "isError": True,
                "content": [{
                    "type": "text",
                    "text": f"Blocked by Read-Only Inspector mode: tool '{tname}' with arguments {json.dumps(targs)} mutates Studio state.",
                }],
            }
        try:
            data = execute_tool(self.client, tname, targs)
            if isinstance(data, dict):
                data.setdefault("ok", True)
                data.setdefault("warnings", [])
            img_b64 = data.pop("_mcp_image_png_b64", None) if isinstance(data, dict) else None
            content_items = [{"type": "text", "text": json.dumps(data, indent=2)}]
            if img_b64:
                content_items.insert(0, {
                    "type": "image",
                    "data": img_b64,
                    "mimeType": "image/png",
                })
            return {"isError": False, "content": content_items, "structuredContent": data}
        except Exception as tool_exc:
            if isinstance(tool_exc, NoPeerError):
                data = {"ok": False, "reason": tool_exc.reason, "dm": tool_exc.dm, "warnings": []}
                return {"isError": True, "content": [{"type": "text", "text": json.dumps(data)}], "structuredContent": data}
            data = {"ok": False, "reason": "tool_error", "error": str(tool_exc), "warnings": []}
            return {
                "isError": True,
                "content": [{"type": "text", "text": json.dumps(data, indent=2)}],
                "structuredContent": data,
            }

    def _dispatch_method(self, msg: dict, ctx: dict = None):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self._err(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")

        method = msg.get("method", "")
        params = msg.get("params") or {}
        has_id = "id" in msg
        msg_id = msg.get("id")
        ctx = ctx or self.request_context(msg)

        # Notifications (no id) never produce a JSON-RPC response object
        if not has_id:
            return None

        try:
            if method == "initialize":
                requested_ver = params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
                negotiated = (
                    requested_ver if requested_ver in SUPPORTED_PROTOCOL_VERSIONS
                    else DEFAULT_PROTOCOL_VERSION
                )
                payload = {
                    "protocolVersion": negotiated,
                    "capabilities": self.capabilities(modern=negotiated == MODERN_PROTOCOL_VERSION),
                    "serverInfo": {
                        "name": MCP_SERVER_NAME,
                        "version": MCP_SERVER_VERSION,
                    },
                    "instructions": self._instructions(),
                }
                if negotiated == MODERN_PROTOCOL_VERSION:
                    payload["resultType"] = "complete"
                return self._ok(msg_id, payload)

            if method == "ping":
                # Removed in 2026-07-28; still answered, because a legacy client
                # that pings is doing nothing wrong and a modern one will not ask.
                return self._ok(msg_id, {})

            if method == "tools/list":
                tools = []
                for spec in self.visible_tools():
                    entry = dict(spec)
                    execution = dict(entry.get("execution") or {})
                    execution.setdefault("taskSupport", "optional")
                    if self.gate.enabled and self.gate.requires(
                        spec["name"], spec.get("annotations") or {}
                    ):
                        execution["confirmationRequired"] = True
                    entry["execution"] = execution
                    tools.append(entry)
                return self._ok(msg_id, {"tools": tools})

            if method == "tools/call":
                tname = params.get("name")
                targs = params.get("arguments") or {}
                if not tname or tname not in TOOL_MAP:
                    return self._err(msg_id, -32602, f"Unknown tool: {tname!r}")

                validation_err = validate_arguments(TOOL_MAP[tname]["inputSchema"], targs)
                if validation_err:
                    return self._err(msg_id, -32602, validation_err)

                # A task request is gated *inside* the task: the handle is already
                # out, so the task itself goes to `input_required` and `tasks/update`
                # answers it. A retry that carries `requestState` is answered
                # in-band and runs inline instead.
                if isinstance(params.get("task"), dict) and not params.get("requestState"):
                    if ctx.get("modern") and not ctx.get("tasks_capable"):
                        return self._err(msg_id, -32021,
                                         "Task handles need the io.modelcontextprotocol/tasks extension "
                                         "capability in this era",
                                         {"requiredCapability": TASKS_EXTENSION_KEY,
                                          "declared": sorted((ctx.get("extensions") or {}).keys())})
                    return self._ok(msg_id, self._create_task(tname, targs, params["task"], ctx=ctx))

                gated = self._gate_pre(tname, targs, params, ctx)
                if gated is not None:
                    return self._ok(msg_id, gated)

                if self.auto_task_ms > 0 and ctx.get("tasks_capable"):
                    task = self._start_task(tname, targs, TASK_DEFAULT_TTL_MS, ctx=ctx, inline=True)
                    deadline = time.monotonic() + self.auto_task_ms / 1000.0
                    while time.monotonic() < deadline:
                        with self._task_lock:
                            if self.tasks.get(task["taskId"], {}).get("status") not in ("working", "input_required"):
                                break
                        time.sleep(0.02)
                    with self._task_lock:
                        current = self.tasks.get(task["taskId"], {})
                        finished = current.get("status") in ("completed", "failed", "cancelled")
                        result = current.get("result")
                    if finished and result is not None and current.get("status") == "completed":
                        # Fast enough to answer inline; the task record survives
                        # so an idempotent tasks/get still finds the outcome.
                        return self._ok(msg_id, result)
                    return self._ok(msg_id, self._task_handle(task["taskId"]))

                return self._ok(msg_id, self._invoke_tool(tname, targs))

            if method == "resources/list":
                return self._ok(msg_id, {"resources": RESOURCES})

            if method == "resources/templates/list":
                return self._ok(msg_id, {"resourceTemplates": RESOURCE_TEMPLATES})

            if method == "resources/read":
                uri = params.get("uri", "")
                if not uri:
                    return self._err(msg_id, -32602, "Missing 'uri' parameter.")
                return self._ok(msg_id, read_mcp_resource(self.client, uri))

            if method == "prompts/list":
                return self._ok(msg_id, {"prompts": PROMPTS})

            if method == "prompts/get":
                pname = params.get("name", "")
                if not pname:
                    return self._err(msg_id, -32602, "Missing 'name' parameter.")
                return self._ok(msg_id, get_mcp_prompt(pname, params.get("arguments"), self.client))

            if method == "logging/setLevel":
                # Removed in 2026-07-28 (log level is per-request now via
                # _meta['io.modelcontextprotocol/logLevel']); still honoured for
                # clients from the eras that had it.
                return self._ok(msg_id, {})

            if method == "completion/complete":
                return self._err(msg_id, -32601, "This server does not offer argument completion")

            return self._err(msg_id, -32601, f"Method not found: {method}")

        except KeyError as exc:
            return self._err(msg_id, -32602, str(exc))
        except ValueError as exc:
            return self._err(msg_id, -32602, str(exc))
        except Exception as exc:  # noqa: BLE001
            return self._err(msg_id, -32603, f"Internal error: {exc}")
