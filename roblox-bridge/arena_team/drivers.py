"""Tool runner (policy gate) and pluggable agent drivers.

Every Studio action goes through :class:`ToolRunner`, which enforces the rules in
``AGENTS.md`` and the project's safety policy *before* calling the one real MCP
tool dispatcher (``mcp_core.execute_tool``). No tool is invented: names are looked
up in ``mcp_core.TOOL_MAP`` at call time.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from mcp_core import (  # noqa: E402  (bridge modules are importable from roblox-bridge/)
    NoPeerError,
    TOOL_MAP,
    _check_with_fallback,
    clear_call_context,
    execute_tool,
    fnv1a_hex,
    is_tool_call_read_only,
    normalize_newlines,
    set_call_context,
)

WRITE_TOOLS = frozenset({
    "write_script", "multi_edit", "batch_write", "find_and_replace_scripts",
    "build_tree", "set_properties", "mass_set_properties", "delete_instance",
    "reparent_or_rename", "clone_instance", "manage_attributes", "manage_tags",
    "manage_camera", "edit_terrain", "spawn_kit_mesh", "spawn_prefab",
    "import_ui_from_web", "install_template", "install_kit_module",
    "install_dropin_system", "import_rbxmx", "import_image", "import_audio",
    "scatter_props", "build_biome", "build_skybox", "generate_dungeon_layout",
    "build_gameplay_mechanic", "configure_atmosphere_fx", "build_vfx_emitter",
    "kitbash_mesh_prop", "build_combat_hitbox", "configure_camera_rig",
    "generate_sfx_soundbank", "import_place_rbxlx", "open_cloud_action",
    "undo_redo", "manage_snapshots", "drift_and_sync",
})

#: Step kinds that always require an explicit human decision, independent of the
#: bridge's --require-confirm mode and the project's approvalMode.
ALWAYS_APPROVE_KINDS = frozenset({"destructive", "publish", "external", "project_wide"})

#: A read-only audit may legitimately return `ok:false` to mean "issues found".
#: Only explicit transport/policy reasons are treated as a failed tool call.
FATAL_RESULT_REASONS = frozenset({
    "no_peer", "no_client_peer", "permission_denied", "invalid_arguments",
    "missing_required", "not_found", "path_not_found", "error",
})


def _agent_call_context(project=None, spec=None, task=None, tool: str = "", principal: str = "") -> dict:
    """MCP request provenance for jobs queued from orchestrator worker threads.

    ``BridgeClient.submit_job`` reads ``current_call_context()`` to attach a
    trace/actor to each Studio job. Worker threads do not inherit the HTTP/MCP
    request context, so install one for every agent tool call. Never include
    source text, credentials or arbitrary tool arguments in the trace.
    """
    agent_key = getattr(spec, "key", None) or "orchestrator"
    task_key = (task or {}).get("key")
    project_id = (project or {}).get("id")
    who = str(principal or "arena_team:" + agent_key + (":" + str(task_key) if task_key else ""))[:120]
    return {
        "principal": who,
        "requestedVersion": None,
        "modern": False,
        "logLevel": None,
        "_logCursor": 0,
        "trace": {
            "layer": "arena_team",
            "projectId": project_id,
            "taskKey": task_key,
            "agentKey": agent_key if spec else None,
            "tool": tool,
        },
    }


def call_tool_with_context(client, tool: str, args: dict, *, project=None,
                           spec=None, task=None, principal: str = "") -> dict:
    """Call the real MCP dispatcher with thread-local caller provenance."""
    previous = set_call_context(_agent_call_context(project, spec, task, tool, principal))
    try:
        return execute_tool(client, tool, args)
    finally:
        clear_call_context(previous)


class PolicyError(RuntimeError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


class ToolRunner:
    """One agent/task's guarded channel to the existing MCP tool pipeline."""

    def __init__(self, orchestrator, spec, task: dict, project: dict):
        self.orch = orchestrator
        self.spec = spec
        self.task = task
        self.project = project
        self.calls = []
        self.lock = threading.Lock()

    # -- target inference -----------------------------------------------------

    @staticmethod
    def infer_target(tool: str, args: dict) -> str:
        """Best-effort Studio dot-path for ownership + before/after checks."""
        args = args or {}
        if tool == "write_script":
            return f"{args.get('parent') or 'ServerScriptService'}.{args.get('name') or 'Script'}"
        if tool in ("multi_edit", "find_and_replace_scripts", "script_read"):
            return str(args.get("path") or "")
        if tool == "build_tree":
            tree = args.get("tree")
            first = tree[0] if isinstance(tree, list) and tree else tree if isinstance(tree, dict) else {}
            name = (first or {}).get("name") if isinstance(first, dict) else ""
            parent = args.get("parent") or "Workspace"
            return f"{parent}.{name}" if name else str(parent)
        if tool == "import_ui_from_web":
            return f"{args.get('parent') or 'StarterGui'}.{args.get('guiName') or 'WebImportedGui'}"
        if tool in ("spawn_kit_mesh", "spawn_prefab", "import_rbxmx", "install_kit_module"):
            return str(args.get("parent") or "Workspace")
        if tool == "delete_instance":
            paths = args.get("paths") or []
            return str(paths[0]) if paths else ""
        if tool == "mass_set_properties":
            paths = args.get("paths") or []
            return str(paths[0]) if paths else ""
        if tool == "set_properties":
            return str(args.get("path") or "")
        if tool == "manage_snapshots":
            return str(args.get("root") or "game")
        return str(args.get("path") or args.get("parent") or args.get("root") or "")

    def _check_ownership(self, tool: str, args: dict, target: str, kind: str):
        if not target or kind == "verify":
            return
        owner = self.orch.registry.owner_for_path(target)
        if owner and owner != self.spec.key:
            raise PolicyError("ownership_conflict",
                              f"{self.spec.key} may not touch {target}: exclusively owned by {owner}")
        in_task = any(target == p or target.startswith(p + ".") or p.startswith(target + ".")
                      for p in (self.task.get("paths") or []))
        in_agent = any(target == p or target.startswith(p + ".") for p in self.spec.owned_paths)
        if not in_task and not in_agent:
            raise PolicyError("path_not_reserved",
                              f"{self.spec.key} has no declared ownership for {target} (task {self.task.get('key')})")

    # -- public API -----------------------------------------------------------

    def call(self, tool: str, args: dict = None, *, kind: str = None,
             target: str = None, allow_no_peer: bool = False, allow_missing: bool = False,
             summary: str = "", approval_kind: str = None, global_scope: bool = False):
        """Run one *existing* MCP tool under the full agent policy."""
        args = dict(args or {})
        if tool not in TOOL_MAP:
            raise PolicyError("unknown_tool", f"{tool!r} is not a registered MCP tool")
        if tool not in self.spec.allowed_tools:
            raise PolicyError("tool_not_allowed", f"{self.spec.key} is not allowed to call {tool}")
        read_only_call = is_tool_call_read_only(tool, args)
        if self.spec.permission == "read" and not read_only_call:
            raise PolicyError("insufficient_permission", f"{self.spec.key} has read permission only")
        if self.orch.read_only and not read_only_call:
            raise PolicyError("read_only_inspector", "Bridge is in Read-Only Inspector mode")

        inferred = self.infer_target(tool, args)
        effective_target = target or inferred
        needs_write = bool(kind in ("write", "destructive", "publish", "project_wide")
                           or (kind is None and (tool in WRITE_TOOLS and not read_only_call)))
        effective_kind = kind or ("write" if needs_write else "read")
        if needs_write and not global_scope:
            self._check_ownership(tool, args, effective_target, effective_kind)
        if needs_write and global_scope and effective_kind not in ALWAYS_APPROVE_KINDS:
            # A project-wide mutation is always a human decision.
            effective_kind = "project_wide"

        # Gate consequential operations through the existing human-in-the-loop queue.
        gate_kind = approval_kind or effective_kind
        if gate_kind in ALWAYS_APPROVE_KINDS or (
            self.project.get("approvalMode") == "each_write" and effective_kind == "write"
        ):
            before = self._read_before(tool, args) if effective_kind in ("write", "destructive") else None
            self.orch.request_approval(
                self.project, self.spec, self.task, tool, args, gate_kind,
                effective_target, before=before, summary=summary,
            )

        # Mandatory Luau pre-flight for every script write (AGENTS.md rule #2).
        if tool in ("write_script", "multi_edit", "batch_write"):
            sources = self._sources_for_check(tool, args)
            for label, source in sources:
                self._preflight_luau(label, source)

        # Optimistic concurrency: expectedHash from the live source; cache-busting
        # replaceInstance for ModuleScript updates (AGENTS.md rules #3/#4).
        if tool == "write_script":
            self._augment_write_script(args)

        started = time.monotonic()
        outcome = "ok"
        error_text = ""
        result = None
        detail_extra = {}
        try:
            lock = self.orch.write_lock if not read_only_call else threading.Lock()
            with lock:
                result = call_tool_with_context(
                    self.orch.client, tool, args,
                    project=self.project, spec=self.spec, task=self.task,
                )
        except NoPeerError as exc:
            outcome = "no_peer"
            error_text = str(exc)
            if not allow_no_peer:
                self._log(tool, args, effective_target, effective_kind, outcome, started, error_text)
                raise
            result = {"ok": False, "reason": "no_peer", "dm": exc.dm}
        except Exception as exc:  # noqa: BLE001 — every failure must be auditable
            if allow_missing and "path not found" in str(exc).lower():
                # An existence probe for a not-yet-created owned namespace is a
                # normal finding, not a failed tool call.
                outcome = "finding"
                error_text = "path_not_found (expected existence probe)"
                result = {"ok": False, "reason": "path_not_found"}
            else:
                outcome = "error"
                error_text = f"{type(exc).__name__}: {exc}"
                self._log(tool, args, effective_target, effective_kind, outcome, started, error_text)
                raise
        else:
            if isinstance(result, dict) and result.get("ok") is False and not allow_no_peer:
                reason = str(result.get("reason") or result.get("error") or "")
                fatal = (effective_kind not in ("read", "audit")) or (reason.lower() in FATAL_RESULT_REASONS)
                if fatal:
                    outcome = "tool_reported_failure"
                    error_text = reason[:300]
                    self._log(tool, args, effective_target, effective_kind, outcome, started, error_text)
                    raise PolicyError("tool_reported_failure", f"{tool}: {error_text}")
                detail_extra["resultOk"] = False

        self._log(tool, args, effective_target, effective_kind, outcome, started, error_text,
                  detail_extra or None)

        if effective_kind in ("write", "destructive") and outcome == "ok":
            self.orch.store.bump_revision(self.project, self.spec.key, effective_target or tool)
            self.project.setdefault("stats", {})["writes"] = int(self.project.get("stats", {}).get("writes", 0)) + 1
            after = self._read_before(tool, args)
            self.orch.finish_approval(self.project, self.spec.key, after=after)
            self._verify_write(tool, args, effective_target, after, result)
        return result

    def _log(self, tool, args, target, kind, outcome, started, error_text, extra=None):
        duration = round((time.monotonic() - started) * 1000, 1)
        with self.lock:
            self.calls.append({"tool": tool, "target": target, "kind": kind,
                               "outcome": outcome, "durationMs": duration})
        self.project.setdefault("stats", {})["toolCalls"] = int(self.project.get("stats", {}).get("toolCalls", 0)) + 1
        if outcome not in ("ok", "no_peer", "finding"):
            self.project.setdefault("stats", {})["failures"] = int(self.project.get("stats", {}).get("failures", 0)) + 1
        preview = self.orch.redact({k: v for k, v in args.items()
                                    if k not in ("source", "html", "code", "serverCode", "clientCode")})
        detail = {
            "projectId": self.project["id"], "taskKey": self.task.get("key"),
            "agentKey": self.spec.key, "tool": tool, "target": target, "kind": kind,
            "outcome": outcome, "durationMs": duration, "arguments": preview,
        }
        if isinstance(extra, dict):
            detail.update(self.orch.redact(extra))
        if error_text:
            detail["error"] = error_text[:300]
        self.orch.append_timeline(
            self.project, self.spec.key, "agent.tool_call",
            f"{tool} → {outcome} ({duration:.0f}ms)" + (f" · {target}" if target else ""),
            task_key=self.task.get("key"),
            level="warn" if outcome in ("error", "tool_reported_failure", "no_peer") else "debug",
            detail=detail,
        )
        self.orch.audit("agent.tool_call", actor=self.spec.key, detail=detail,
                        outcome="error" if outcome not in ("ok", "no_peer", "finding") else "ok")

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _sources_for_check(tool: str, args: dict) -> list:
        if tool == "write_script":
            return [(f"{args.get('parent')}.{args.get('name')}", args.get("source") or "")]
        if tool == "multi_edit":
            return [(args.get("path") or "edit", "\n".join(str(e.get("newText") or "") for e in (args.get("edits") or [])))]
        if tool == "batch_write":
            return [(str(s.get("name") or "batch"), str(s.get("source") or "")) for s in (args.get("scripts") or [])]
        return []

    def _preflight_luau(self, label: str, source: str):
        if not source or not source.strip():
            raise PolicyError("empty_source", f"Refusing to write empty Luau source for {label}")
        # Same pre-flight the bridge's own write_script uses: local compiler,
        # then Studio's loadstring parser as a fallback. Inspect checksPerformed.
        chk = _check_with_fallback(self.orch.client, source, str(label), dm="edit")
        performed = sorted((chk.get("checksPerformed") or {}).keys()) or [chk.get("backend") or "unavailable"]
        if chk.get("syntaxOk") is False and chk.get("reason") != "all_backends_failed":
            raise PolicyError("luau_syntax_error",
                              f"luau_check rejected {label}: {json.dumps(chk.get('compileErrors') or [])[:300]}")
        self.orch.append_timeline(
            self.project, self.spec.key, "agent.luau_check",
            f"luau_check passed for {label} (checks: {', '.join(map(str, performed)) or 'unavailable'})",
            task_key=self.task.get("key"), level="debug",
            detail={"checksPerformed": performed, "warnings": chk.get("warnings") or []},
        )

    def _augment_write_script(self, args: dict):
        target = self.infer_target("write_script", args)
        live = self._read_script(target)
        if live:
            args["expectedHash"] = fnv1a_hex(normalize_newlines(live))
        else:
            args.pop("expectedHash", None)
        if live and args.get("className", "ModuleScript") == "ModuleScript":
            args["replaceInstance"] = True
        args.setdefault("validateSyntax", True)
        # Persistent changes belong in the Edit DataModel.
        args["dm"] = "edit"
        if self.project.get("peerId"):
            args["peerId"] = self.project["peerId"]

    def _read_script(self, path: str) -> str:
        if not path or "." not in path:
            return ""
        try:
            row = call_tool_with_context(
                self.orch.client, "script_read", {"path": path, "dm": "edit"},
                project=self.project, spec=self.spec, task=self.task,
            )
            return str(row.get("source") or "")
        except Exception:  # noqa: BLE001 — a missing script is a legitimate new write
            return ""

    def _read_before(self, tool: str, args: dict) -> str:
        if tool in ("write_script", "multi_edit"):
            return self._read_script(self.infer_target(tool, args))
        return ""

    def _verify_write(self, tool, args, target, after, result):
        """A write is only done when a read-back proves the intended source landed."""
        if tool == "write_script":
            intended = normalize_newlines(str(args.get("source") or ""))
            observed = normalize_newlines(after or "")
            ok = bool(observed) and observed == intended
            self.task.setdefault("verification", {})[target] = {
                "tool": "script_read", "readBack": ok, "bytes": len(observed),
                "expectedHash": args.get("expectedHash"),
                "replaceInstance": bool(args.get("replaceInstance")),
            }
            self.project.setdefault("stats", {})["verifications"] = int(self.project.get("stats", {}).get("verifications", 0)) + 1
            if not ok:
                raise PolicyError("verification_failed",
                                  f"Read-back mismatch after writing {target}: expected {len(intended)} bytes, got {len(observed)}")
            self.orch.append_timeline(self.project, self.spec.key, "agent.verified",
                                      f"Read-back verified for {target}", task_key=self.task.get("key"), level="info")
        elif tool == "build_tree":
            try:
                row = call_tool_with_context(
                    self.orch.client, "inspect_instance", {"path": target, "depth": 0, "dm": "edit"},
                    project=self.project, spec=self.spec, task=self.task,
                )
                self.task.setdefault("verification", {})[target] = {"tool": "inspect_instance", "readBack": bool(row)}
            except Exception as exc:  # noqa: BLE001
                raise PolicyError("verification_failed", f"Could not inspect created {target}: {exc}") from exc


class RunContext:
    """What a playbook/driver is allowed to see and do."""

    def __init__(self, orchestrator, spec, task, project, runner: ToolRunner):
        self.orch = orchestrator
        self.spec = spec
        self.task = task
        self.project = project
        self.runner = runner
        self.results = {}
        self.artifacts = []
        self.verification = {}
        self.warnings = []

    @property
    def objective(self) -> str:
        return self.project.get("objective", "")

    @property
    def slug(self) -> str:
        return self.project.get("slug", "Project")

    @property
    def settings(self) -> dict:
        return self.orch.settings

    def call(self, tool, args=None, **kwargs):
        return self.runner.call(tool, args, **kwargs)

    def try_call(self, tool, args=None, **kwargs):
        """A non-fatal read: warnings are recorded, never silently ignored."""
        try:
            return self.call(tool, args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"{tool}: {type(exc).__name__}: {str(exc)[:180]}")
            return {"ok": False, "reason": getattr(exc, "reason", "tool_error"), "error": str(exc)[:300]}

    def artifact(self, kind, summary, path=None, data=None):
        row = self.orch.store.add_artifact(self.project, self.spec.key, self.task.get("key"),
                                           kind, path=path, summary=summary, data=self.orch.redact(data or {}))
        self.artifacts.append(row)
        return row

    def evidence(self, kind, filename, summary, meta=None):
        row = self.orch.store.add_evidence(self.project, self.spec.key, self.task.get("key"),
                                           kind, filename, summary=summary, meta=meta or {})
        self.artifacts.append(row)
        return row

    def save_evidence_image(self, label: str, png_bytes: bytes) -> str:
        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", f"{self.project['id']}_{label}")[:100] + ".png"
        target = self.orch.store.evidence_path(safe)
        target.write_bytes(png_bytes)
        return safe

    def done(self, summary: str, **extra) -> dict:
        return {
            "ok": True, "summary": summary, "data": self.results,
            "artifacts": self.artifacts, "verification": dict(self.runner.task.get("verification") or {}),
            "warnings": list(self.warnings), "toolCalls": list(self.runner.calls), **extra,
        }


class BaseDriver:
    type_name = "base"

    def __init__(self, spec):
        self.spec = spec

    def run(self, ctx: RunContext) -> dict:
        raise NotImplementedError


class BuiltinDriver(BaseDriver):
    """Deterministic, offline-capable playbooks built only from real MCP tools."""

    type_name = "builtin"

    def run(self, ctx: RunContext) -> dict:
        from . import workflows
        name = str(self.spec.driver.get("playbook") or "")
        fn = workflows.PLAYBOOKS.get(name)
        if fn is None:
            raise PolicyError("unknown_playbook", f"No builtin playbook named {name!r}")
        return fn(ctx)


class HumanDriver(BaseDriver):
    """A human-owned seat: the Bus/UI claims and completes the task, not a worker."""

    type_name = "human"

    def run(self, ctx: RunContext) -> dict:
        raise PolicyError("awaiting_human", "This task is assigned to a human operator; claim and complete it through the task board.")


class ExternalDriver(BaseDriver):
    """Optional model endpoint with an explicit JSON tool-call contract.

    Configure ``{"type":"external","endpointEnv":"MY_AGENT_URL","keyEnv":"MY_KEY"}``.
    The endpoint receives the project/task context and a manifest of *discovered*
    MCP tool schemas, and must answer
    ``{"summary": "...", "toolCalls":[{"tool": "...", "arguments": {...},
    "kind": "read|write", "target": "..."}]}``. Every call still passes through
    ToolRunner's policy gate. A missing endpoint makes the agent offline; a failing
    endpoint fails the task with the real error instead of faking success.
    """

    type_name = "external"

    def run(self, ctx: RunContext) -> dict:
        env_name = str(self.spec.driver.get("endpointEnv") or "")
        endpoint = (os.environ.get(env_name) or "").strip()
        if not endpoint:
            raise PolicyError("driver_not_configured", f"External endpoint env {env_name!r} is not configured")
        key_env = str(self.spec.driver.get("keyEnv") or "")
        api_key = os.environ.get(key_env, "") if key_env else ""
        # External communication is a consequential operation by policy.
        ctx.orch.request_approval(
            ctx.project, ctx.spec, ctx.task, "external_model_call",
            {"endpointHost": urlparse(endpoint).netloc}, "external",
            "external_communication", before=None,
            summary=f"Send this task's context to the configured model endpoint for {self.spec.name}",
        )
        tools = []
        for name in self.spec.allowed_tools:
            spec = TOOL_MAP.get(name)
            if spec:
                tools.append({"name": name, "description": spec.get("description", "")[:400],
                              "inputSchema": spec.get("inputSchema", {})})
        payload = ctx.orch.redact({
            "agent": ctx.spec.public(), "objective": ctx.objective,
            "task": {"key": ctx.task.get("key"), "title": ctx.task.get("title"),
                     "detail": ctx.task.get("detail"), "paths": ctx.task.get("paths")},
            "project": {"id": ctx.project["id"], "phase": ctx.project.get("phase"),
                        "revision": ctx.project.get("revision"), "design": ctx.project.get("design")},
            "availableTools": tools,
            "instruction": "Return JSON with a summary and toolCalls. Only use tool names from availableTools. "
                           "Writes must target an owned path; use luau_check before script writes.",
        })
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=float(self.spec.driver.get("timeoutSeconds") or 60)) as resp:
                answer = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, OSError) as exc:
            raise PolicyError("external_driver_failed", f"Model endpoint {env_name} failed: {exc}") from exc
        if not isinstance(answer, dict):
            raise PolicyError("external_driver_invalid", "Model endpoint did not return a JSON object")
        calls = answer.get("toolCalls") or []
        if not isinstance(calls, list):
            raise PolicyError("external_driver_invalid", "Model endpoint toolCalls must be an array")
        for i, item in enumerate(calls[:25]):
            if not isinstance(item, dict) or not item.get("tool"):
                raise PolicyError("external_driver_invalid", f"toolCalls[{i}] is malformed")
            ctx.results[f"external_{i}"] = ctx.call(
                str(item["tool"]), item.get("arguments") or {},
                kind=item.get("kind"), target=item.get("target"),
                summary=f"Model-directed call #{i + 1}",
            )
        ctx.results["modelAnswer"] = ctx.orch.redact(answer.get("summary") or "")
        return ctx.done(str(answer.get("summary") or f"{len(calls)} model-directed tool calls executed"),
                        driver="external", endpointEnv=env_name)


def make_driver(spec, tool_map=None) -> BaseDriver:
    kind = spec.driver.get("type")
    if kind == "builtin":
        return BuiltinDriver(spec)
    if kind == "external":
        return ExternalDriver(spec)
    if kind == "human":
        return HumanDriver(spec)
    raise PolicyError("unknown_driver", f"Unknown driver type {kind!r}")
