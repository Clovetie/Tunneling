#!/usr/bin/env python3
"""
mcp_core.py — Zero-dependency Model Context Protocol (MCP) engine & Roblox Studio
tool suite for Arena <-> Roblox Studio (v3.0).

Supports:
  - MCP specification versions 2025-06-18, 2025-03-26, and 2024-11-05
  - Transports: stdio (via mcp_server.py) and Streamable HTTP /mcp (via server.py)
  - 37 curated, annotated Roblox Studio & Luau engineering tools
  - Live MCP Resources + Resource Templates + Workflow Prompts
  - Exact 53-bit-safe 32-bit FNV-1a hashing matching ArenaBridge.lua bit-for-bit
  - Local pre-flight Luau syntax + undefined-global static analysis (luau_check)
  - Repo <-> Studio drift detection, atomic cache-busting sync, & snapshots
  - Read-only Inspector mode enforced on every tool & sub-action
  - Transparent fallback to run_luau if an older v2.0 Studio plugin is still loaded
"""

import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

MCP_SERVER_NAME = "arena-roblox-studio-mcp"
MCP_SERVER_VERSION = "3.0.0"
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_PROTOCOL_VERSION = "2025-06-18"

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
            proc = subprocess.run(
                [compiler, "--binary", tmp_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            if proc.returncode != 0:
                err_txt = proc.stderr.decode("utf-8", "replace").strip()
                err_txt = err_txt.replace(tmp_path, filename)
                result["syntaxOk"] = False
                result["ok"] = False
                result["compileErrors"] = [
                    ln for ln in err_txt.splitlines() if ln.strip()
                ]
                return result

        if analyzer:
            proc = subprocess.run(
                [analyzer, tmp_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=15,
            )
            output = proc.stdout.decode("utf-8", "replace").replace(tmp_path, filename)
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

    result["ok"] = result["syntaxOk"] and len(result["undefinedGlobals"]) == 0
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
        body = {
            "type": job_type,
            "payload": payload,
            "note": note or f"mcp:{job_type}",
            "wait": wait,
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
                "timeout": {"type": "number", "default": 45, "minimum": 5, "maximum": 120},
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
                "path": {"type": "string", "description": "Local file or directory path of .lua/.luau scripts to validate."},
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
                "action": {"type": "string", "enum": ["create", "list", "get", "restore"], "default": "list"},
                "root": {"type": "string", "default": "game", "description": "Subtree of scripts to snapshot when action='create'."},
                "label": {"type": "string", "description": "Human-readable note for snapshot."},
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
        "description": "List or read built-in Roblox Studio Skills (.claude/skills), reusable Luau Modules (roblox-kit/modules), 3D/UI/Lighting Prefabs (roblox-kit/prefabs), and Repo Image Assets (roblox-kit/assets).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["all", "skills", "modules", "prefabs", "images", "presets"],
                    "default": "all",
                },
                "item": {"type": "string", "description": "Optional specific skill/module/prefab name to return full Markdown/Luau/JSON source."},
            },
        },
        "annotations": _ann("Browse Skills, Modules, Prefabs & Assets", read_only=True, destructive=False, idempotent=True),
    },
    {
        "name": "install_kit_module",
        "description": "Install one or all production-verified Luau ModuleScripts (Maid, Signal, NetGuard, TagBinder, SpringUI, StateStore, Assets) from `roblox-kit/modules/` into `ReplicatedStorage.Shared` (or custom Studio container) in a single undoable step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "default": "all",
                    "description": "Module stem ('Maid', 'Signal', 'NetGuard', 'TagBinder', 'SpringUI', 'StateStore', 'Assets') or 'all'.",
                },
                "parent": {"type": "string", "default": "ReplicatedStorage.Shared", "description": "Target Studio folder/service path (auto-creates child Folder if under a Service)."},
                "peerId": PEER_PROP,
            },
        },
        "annotations": _ann("Install Reusable Luau Kit Modules", read_only=False, destructive=False, idempotent=True),
    },
    {
        "name": "spawn_prefab",
        "description": "Spawn a ready-made lighting preset, modern ScreenGui HUD, world sign board, collectible orb VFX, starter plaza, or interaction pad from `roblox-kit/prefabs/` into Roblox Studio (auto-syncing required repo images first).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prefab": {
                    "type": "string",
                    "description": "Prefab name (e.g. 'lighting_cinematic_sunset', 'lighting_cyber_night', 'lighting_stylized_day', 'hud_modern_shell', 'interact_pad', 'spawn_plaza', 'world_sign_board', 'vfx_collectible_orb') or path to a JSON file.",
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
    if name == "manage_snapshots" and args.get("action", "list") in ("list", "get"):
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
        if val is None or key not in props:
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
        raise RuntimeError(job.get("error") or "Studio job failed")
    raise RuntimeError(
        f"Studio did not complete job {job.get('id')} in time (status={status}). "
        "Verify Roblox Studio is open and connected."
    )


def create_snapshot_helper(client: BridgeClient, root: str = "game", label: str = None, peer_id: str = None) -> dict:
    hashes_job = _unwrap_job(client.submit_job("hash_scripts", {"root": root}, wait=45.0, peer_id=peer_id))
    paths = [s["path"] for s in hashes_job.get("scripts", [])]
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


def execute_tool(client: BridgeClient, name: str, args: dict) -> dict:
    dm = args.get("dm", "auto")
    peer_id = args.get("peerId")

    if name == "luau_check":
        return run_luau_check(code=args.get("code"), path=args.get("path"))

    if name == "list_studios":
        return client.peers()

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
        if args.get("validateSyntax", True):
            chk = check_luau_source(args["source"], f"{args['parent']}.{args['name']}.lua")
            if not chk["syntaxOk"] or chk["undefinedGlobals"]:
                raise ValueError(
                    f"Pre-push Luau validation failed for {args['parent']}.{args['name']}: "
                    + json.dumps({
                        "compileErrors": chk["compileErrors"],
                        "undefinedGlobals": chk["undefinedGlobals"],
                    })
                )
        return _unwrap_job(client.submit_job("write_script", {
            "parent": args["parent"],
            "name": args["name"],
            "className": args.get("className", "ModuleScript"),
            "source": args["source"],
            "expectedHash": args.get("expectedHash"),
            "replaceInstance": args.get("replaceInstance", False),
        }, wait=45.0, dm=dm if dm != "auto" else "edit", peer_id=peer_id))

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

    if name == "execute_luau":
        if args.get("validateSyntax", True):
            chk = check_luau_source(args["code"], "execute_luau.lua")
            if not chk["syntaxOk"]:
                raise ValueError("Luau syntax error: " + "; ".join(chk["compileErrors"]))
        payload = {"code": args["code"]}
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
        return res

    if name == "start_stop_play":
        act = args["action"]
        target_dm = "server" if act == "stop" else ("edit" if act in ("start_play", "start_run") else dm)
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
        _unwrap_job(client.submit_job("playtest", {"action": start_act}, wait=20.0, dm="edit"))

        # Wait for live server peer (or fall back to default peer in single-peer mocks)
        deadline = time.monotonic() + min(12.0, timeout / 2)
        while time.monotonic() < deadline:
            peers = client.peers().get("peers", [])
            if any(p.get("dm") in ("server", "any") and p.get("online") for p in peers):
                break
            time.sleep(0.2)

        warmup = float(args.get("warmupSeconds", 1.5))
        if warmup > 0:
            time.sleep(min(warmup, 15.0))

        server_out = None
        client_out = None
        errors_caught = []

        try:
            if args.get("serverCode"):
                sj = client.submit_job("run_luau", {"code": args["serverCode"]}, wait=timeout, dm="server")
                server_out = _unwrap_job(sj)
            if args.get("clientCode"):
                cj = client.submit_job("run_luau", {"code": args["clientCode"]}, wait=timeout, dm="client")
                client_out = _unwrap_job(cj)
            logs_res = _unwrap_job(client.submit_job("console", {"limit": 200}, wait=15.0, dm="server"))
            for ln in logs_res.get("lines", []):
                if "error" in ln.get("type", "").lower():
                    errors_caught.append(ln.get("message", ""))
        finally:
            try:
                client.submit_job("playtest", {"action": "stop", "reason": "scenario_complete"}, wait=15.0, dm="server")
            except Exception:
                pass

        return {
            "ok": len(errors_caught) == 0,
            "mode": mode,
            "durationSec": round(time.monotonic() - t_start, 2),
            "serverResult": server_out,
            "clientResult": client_out,
            "runtimeErrors": errors_caught,
            "console": logs_res.get("lines", []),
        }

    if name == "get_console_output":
        return _unwrap_job(client.submit_job("console", {
            "limit": args.get("limit", 120),
            "level": args.get("level", "all"),
            "search": args.get("search"),
            "sinceIndex": args.get("sinceIndex", 0),
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
            )
        if act == "list":
            return client.snapshots_action("list")
        if act == "get":
            if not args.get("snapshotId"):
                raise ValueError("snapshotId is required when action='get'")
            return client.snapshots_action("get", {"id": args["snapshotId"]})
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
        return browse_kit_helper(category=args.get("category", "all"), item=args.get("item"))

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
                "saveToRepo": True,
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


def browse_kit_helper(category: str = "all", item: str = None) -> dict:
    from image_pipeline import PROCEDURAL_PRESETS, find_kit_root

    kit = find_kit_root()
    skills_dir = _find_skills_dir()
    modules_dir = kit / "modules"
    prefabs_dir = kit / "prefabs"
    images_dir = kit / "assets" / "images"

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
    if category in ("all", "images"):
        imgs = []
        if images_dir.is_dir():
            for f in sorted(images_dir.rglob("*")):
                if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
                    rel = f.relative_to(images_dir).as_posix()
                    imgs.append({
                        "name": f.stem,
                        "relPath": rel,
                        "localUri": f"rbxasset://arena_assets/{rel}",
                        "bytes": f.stat().st_size,
                    })
        out["images"] = imgs
    if category in ("all", "presets"):
        out["proceduralPresets"] = list(PROCEDURAL_PRESETS.keys())
    return out


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
        req = urllib.request.Request(args["imageUrl"], headers={"User-Agent": "ArenaRobloxMCP/3.0"})
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


def get_mcp_prompt(name: str, arguments: dict = None) -> dict:
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
    else:
        raise KeyError(f"Unknown prompt: {name}")

    return {
        "description": next(p["description"] for p in PROMPTS if p["name"] == name),
        "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
    }


# ---------------------------------------------------------------------------
# Protocol-Compliant JSON-RPC 2.0 MCP Request Dispatcher
# ---------------------------------------------------------------------------

class MCPDispatcher:
    """Stateless/session-friendly JSON-RPC 2.0 handler for stdio & Streamable HTTP."""

    def __init__(self, client: BridgeClient):
        self.client = client

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

    def _handle_one(self, msg: dict):
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            return self._err(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")

        method = msg.get("method", "")
        params = msg.get("params") or {}
        has_id = "id" in msg
        msg_id = msg.get("id")

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
                mode_tag = " (Read-Only Inspector)" if self.client.read_only else ""
                return self._ok(msg_id, {
                    "protocolVersion": negotiated,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": MCP_SERVER_NAME,
                        "version": MCP_SERVER_VERSION,
                    },
                    "instructions": (
                        f"Arena <-> Roblox Studio MCP Server v{MCP_SERVER_VERSION}{mode_tag}. "
                        "Use `get_studio_state` or `survey_place` first to understand the open place. "
                        "Use `multi_edit` / `write_script` (with `replaceInstance: true` when updating already-required ModuleScripts) "
                        "and `luau_check` to prevent undefined-global runtime bugs."
                    ),
                })

            if method == "ping":
                return self._ok(msg_id, {})

            if method == "tools/list":
                return self._ok(msg_id, {"tools": self.visible_tools()})

            if method == "tools/call":
                tname = params.get("name")
                targs = params.get("arguments") or {}
                if not tname or tname not in TOOL_MAP:
                    return self._err(msg_id, -32602, f"Unknown tool: {tname!r}")

                validation_err = validate_arguments(TOOL_MAP[tname]["inputSchema"], targs)
                if validation_err:
                    return self._err(msg_id, -32602, validation_err)

                if self.client.read_only and not is_tool_call_read_only(tname, targs):
                    return self._ok(msg_id, {
                        "isError": True,
                        "content": [{
                            "type": "text",
                            "text": f"Blocked by Read-Only Inspector mode: tool '{tname}' with arguments {json.dumps(targs)} mutates Studio state.",
                        }],
                    })

                try:
                    data = execute_tool(self.client, tname, targs)
                    return self._ok(msg_id, {
                        "isError": False,
                        "content": [{"type": "text", "text": json.dumps(data, indent=2)}],
                        "structuredContent": data,
                    })
                except Exception as tool_exc:
                    return self._ok(msg_id, {
                        "isError": True,
                        "content": [{"type": "text", "text": f"Error executing {tname}: {tool_exc}"}],
                    })

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
                return self._ok(msg_id, get_mcp_prompt(pname, params.get("arguments")))

            if method == "logging/setLevel":
                return self._ok(msg_id, {})

            return self._err(msg_id, -32601, f"Method not found: {method}")

        except KeyError as exc:
            return self._err(msg_id, -32602, str(exc))
        except Exception as exc:
            return self._err(msg_id, -32603, f"Internal error: {exc}")
