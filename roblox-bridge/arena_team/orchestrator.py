"""Orchestrator — coordinates a configurable team of specialized agents over the
existing Arena bridge services (Agent Bus, ConfirmGate, Telemetry, Studio fleet,
and the annotated MCP tool pipeline).

Design rules (see docs/MULTI_AGENT_ARCHITECTURE.md):
  * Bus = coordination; ProjectStore = project context; ConfirmGate = approvals.
  * Agents never write Studio directly: ToolRunner calls mcp_core.execute_tool.
  * A task is done only after real verification; no_peer is a failure, not a skip.
  * No bridge token or external credential is ever returned in a status payload.
"""

import difflib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mcp_core import (  # noqa: E402
    AGENT_TEAM_ADMIN_ACTIONS,
    AGENT_TEAM_READ_ACTIONS,
    AGENT_TEAM_WRITE_ACTIONS,
    TOOL_MAP,
)

from .config import APPROVAL_MODES, TeamConfig, TeamConfigError, load_config, redact  # noqa: E402
from .context import ProjectStore  # noqa: E402
from .drivers import (  # noqa: E402
    PolicyError, RunContext, ToolRunner, call_tool_with_context, make_driver,
)
from .planner import build_design, build_plan, external_plan  # noqa: E402
from .registry import AgentRegistry  # noqa: E402

ORCHESTRATOR_VERSION = "1.0.0"

READ_ACTIONS = AGENT_TEAM_READ_ACTIONS
WRITE_ACTIONS = AGENT_TEAM_WRITE_ACTIONS
ADMIN_ACTIONS = AGENT_TEAM_ADMIN_ACTIONS


def _now() -> float:
    return time.time()


def _exc_reason(exc: Exception) -> str:
    return getattr(exc, "reason", None) or type(exc).__name__


class Orchestrator:
    def __init__(self, *, bus, client, telemetry, confirm_gate, state_dir,
                 read_only: bool = False, fleet_snapshot=None, token: str = ""):
        self.bus = bus
        self.client = client
        self.telemetry = telemetry
        self.gate = confirm_gate
        self.read_only = bool(read_only)
        self._fleet_snapshot = fleet_snapshot
        self._token = str(token or "")
        self._secrets = [self._token] if len(self._token) >= 4 else []
        self.lock = threading.RLock()
        self.write_lock = threading.RLock()
        self.config = load_config()
        self.settings = dict(self.config.settings)
        self.store = ProjectStore(Path(state_dir), self.settings["evidenceDir"])
        self.registry = AgentRegistry(bus, self.config, TOOL_MAP, emit=self.emit)
        # A stable human seat lets the operator claim/complete Bus tasks from the
        # UI. Like the agent seats, it is created on first team use so an idle
        # bridge never pollutes the operator's existing Agent Bus.
        self.human_agent_id = "human-operator"
        self._presence_ready = False
        self.workers = {}                 # projectId -> {thread, stop, pause}
        self.agent_stats = {}             # agentKey -> counters
        self._studio_cache = {"ts": 0.0, "data": {}}
        self._stop = threading.Event()
        # Restore presence for a persisted project, but leave a fresh bridge clean.
        if self.store.current():
            try:
                self.ensure_agent_presence()
            except Exception:  # noqa: BLE001 — presence is recoverable on first action
                pass
        self._heartbeat = threading.Thread(target=self._heartbeat_loop, name="arena-team-heartbeat", daemon=True)
        self._heartbeat.start()

    # -- security helpers -----------------------------------------------------

    def redact(self, value):
        return redact(value, extra_secrets=self._secrets)

    # -- observability --------------------------------------------------------

    def emit(self, kind: str, detail: dict = None, level: str = "info"):
        try:
            self.telemetry.events.emit(kind, self.redact(detail or {}), level=level)
        except Exception:  # noqa: BLE001 — telemetry must never break orchestration
            pass

    def audit(self, action: str, actor: str = "system", detail: dict = None,
              outcome: str = "ok", target: str = ""):
        try:
            self.telemetry.audit.append(action, actor=actor, target=target,
                                        detail=self.redact(detail or {}), outcome=outcome)
        except Exception:  # noqa: BLE001
            pass

    def append_timeline(self, project, actor, kind, message, task_key=None,
                        level="info", detail=None):
        row = self.store.append_timeline(project, actor, kind, message,
                                         task_key=task_key, level=level, detail=self.redact(detail or {}))
        self.emit("agent." + kind, {"projectId": project["id"], "actor": actor,
                                    "taskKey": task_key, "message": str(message)[:300]}, level=level)
        if kind in ("project.created", "project.status", "approval.decided", "task.failed",
                    "task.done", "project.completed", "context.revision"):
            self.audit("agent." + kind, actor=actor, target=project["id"],
                       detail={"taskKey": task_key, "message": str(message)[:300]},
                       outcome="error" if level in ("error", "warn") else "ok")
        return row

    def post_bus(self, sender_key: str, subject: str, body: str, kind: str = "note",
                 project: dict = None, to: str = None, priority: str = "normal"):
        spec = self.registry.spec(sender_key)
        sender = spec.name if spec else str(sender_key)
        meta = {"projectId": project["id"]} if project else {}
        try:
            msg = self.bus.post_message(sender=sender, subject=subject, body=body, to=to,
                                        kind=kind, priority=priority,
                                        thread_id=project["id"] if project else None, meta=meta)
            self.emit("agent.message", {"projectId": project["id"] if project else None,
                                        "from": sender, "subject": subject, "kind": kind})
            return msg
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    # -- background presence --------------------------------------------------

    def _heartbeat_loop(self):
        while not self._stop.wait(max(5.0, float(self.settings.get("heartbeatSeconds", 20)))):
            try:
                self.registry.heartbeat_active()
            except Exception:  # noqa: BLE001
                pass

    def ensure_agent_presence(self):
        """Register configured seats and the human operator exactly once."""
        with self.lock:
            if self._presence_ready:
                return
            self.registry.ensure_registered()
            try:
                human = self.bus.register_agent(
                    name="Human Operator", role="human", capabilities=["operator"],
                    agent_id="human-operator",
                    meta={"agentKey": "human", "layer": "arena_team"},
                )
                self.human_agent_id = human["id"]
            except Exception:  # noqa: BLE001 — keep the stable ID if Bus rejects the seat
                self.human_agent_id = "human-operator"
            self._presence_ready = True

    def shutdown(self):
        self._stop.set()
        for worker in list(self.workers.values()):
            worker["stop"].set()

    # -- design / plan / report ----------------------------------------------

    def plan_design(self, project: dict) -> dict:
        design = build_design(project["objective"], project.get("analysis") or {},
                              self.registry.roster(), project.get("peerId"))
        project["design"] = design
        self.store.save(project)
        self.append_timeline(project, "project-manager", "design.generated",
                             f"Technical design: {design['title']}")
        return design

    def build_report(self, project: dict) -> dict:
        tasks = project.get("plan", {}).get("tasks", [])
        done = [t for t in tasks if t.get("status") == "done"]
        failed = [{"key": t["key"], "agentKey": t["agentKey"], "error": t.get("error")}
                  for t in tasks if t.get("status") in ("failed", "blocked", "cancelled", "todo")]
        write_tasks = [t for t in tasks if t.get("kind") == "implementation"]
        verified_writes = sum(1 for t in write_tasks
                              if any(isinstance(r, dict) and r.get("readBack") for r in (t.get("verification") or {}).values()))
        qa = next((t for t in tasks if t.get("key", "").startswith("qa")), None)
        qa_ok = bool(qa and qa.get("status") == "done" and not (
            ((qa.get("resultData") or {}).get("qa") or {}).get("errorCount")))
        all_done = len(done) == len(tasks) and not failed
        studio_ok = bool((project.get("analysis") or {}).get("studioConnected")
                         or (project.get("studio") or {}).get("connected"))
        evidence = project.get("evidence") or []
        verification = {
            "tasksDone": len(done), "tasksTotal": len(tasks),
            "writeTasks": len(write_tasks), "verifiedWriteReadBacks": verified_writes,
            "toolCalls": (project.get("stats") or {}).get("toolCalls", 0),
            "writes": (project.get("stats") or {}).get("writes", 0),
            "verifications": (project.get("stats") or {}).get("verifications", 0),
            "failures": (project.get("stats") or {}).get("failures", 0),
            "repairs": (project.get("stats") or {}).get("repairs", 0),
            "qaPassed": qa_ok, "studioConnected": studio_ok,
            "evidenceCount": len(evidence),
            "consoleErrors": ((qa.get("resultData") or {}).get("qa") or {}).get("errorCount") if qa else None,
        }
        try:
            audit_check = self.telemetry.audit.verify()
        except Exception:  # noqa: BLE001
            audit_check = {"ok": False, "reason": "audit_unavailable"}
        verified = bool(all_done and qa_ok and studio_ok and verified_writes >= len(write_tasks)
                        and verification["evidenceCount"] > 0 and audit_check.get("ok", False))
        status = "verified" if verified else ("failed" if failed else "unverified")
        report = {
            "title": f"Arena development report — {project['objective'][:80]}",
            "projectId": project["id"], "generatedAt": _now(),
            "status": status, "verified": verified,
            "objective": project["objective"],
            "studio": self.redact(project.get("studio") or {}),
            "design": {"title": (project.get("design") or {}).get("title"),
                       "components": [c.get("name") for c in (project.get("design") or {}).get("components", [])]},
            "verification": verification,
            "tasks": [{"key": t["key"], "title": t["title"], "agentKey": t["agentKey"],
                       "status": t["status"], "result": t.get("result"), "error": t.get("error"),
                       "attempts": t.get("attempts"), "verification": t.get("verification") or {}}
                      for t in tasks],
            "failedTasks": failed,
            "evidence": list(evidence),
            "artifacts": list(project.get("artifacts") or [])[-30:],
            "auditChain": {"ok": audit_check.get("ok"), "entries": audit_check.get("entries")},
            "warnings": [w for w in self._collect_warnings(project)],
            "note": ("Verified against the live Edit DataModel, playtest, console output and "
                     "viewport evidence." if verified else
                     "NOT VERIFIED: one or more tasks failed, Studio was unavailable, or verification "
                     "evidence is missing. Do not treat this as a successful change."),
        }
        report["markdown"] = self._report_markdown(report)
        return report

    @staticmethod
    def _collect_warnings(project: dict) -> list:
        out = []
        for task in project.get("plan", {}).get("tasks", []):
            for w in ((task.get("resultData") or {}).get("warnings") or []):
                out.append(f"{task['key']}: {w}")
        if not (project.get("analysis") or {}).get("studioConnected"):
            out.append("Studio peer was not connected during analysis")
        return out[:20]

    @staticmethod
    def _report_markdown(report: dict) -> str:
        v = report["verification"]
        lines = [
            f"# {report['title']}", "",
            f"**Status:** `{report['status']}` · **verified:** `{report['verified']}`", "",
            report["note"], "",
            "## Verification", "",
            f"- tasks: {v['tasksDone']}/{v['tasksTotal']} done",
            f"- write read-backs: {v['verifiedWriteReadBacks']}/{v['writeTasks']}",
            f"- tool calls: {v['toolCalls']}, writes: {v['writes']}, verifications: {v['verifications']}",
            f"- playtest/console QA: {'pass' if v['qaPassed'] else 'fail/not run'} · console errors: {v['consoleErrors']}",
            f"- viewport evidence: {v['evidenceCount']} file(s) · Studio connected: {v['studioConnected']}",
            f"- audit chain: {'intact' if report['auditChain']['ok'] else 'unavailable/broken'}", "",
            "## Tasks", "",
        ]
        for t in report["tasks"]:
            lines.append(f"- **{t['title']}** (`{t['agentKey']}`): `{t['status']}`"
                         + (f" — {t['result']}" if t.get("result") else "")
                         + (f" — ERROR {t['error']}" if t.get("error") else ""))
        if report["failedTasks"]:
            lines += ["", "## Failures", ""]
            for t in report["failedTasks"]:
                lines.append(f"- `{t['key']}` ({t['agentKey']}): {t['error']}")
        if report["warnings"]:
            lines += ["", "## Warnings", ""] + [f"- {w}" for w in report["warnings"]]
        evidence_lines = [f"- {e['kind']}: `{e['filename']}` — {e['summary']}"
                          for e in report["evidence"]] or ["- none"]
        lines += ["", "## Evidence", ""] + evidence_lines
        return "\n".join(lines)

    # -- approvals (existing ConfirmGate, never a parallel approval store) ----

    def request_approval(self, project, spec, task: dict, tool: str, args: dict,
                         kind: str, target: str, before=None, summary: str = ""):
        labelled_tool = f"agent_team:{tool}"
        pid = (project or {}).get("id") or "ad-hoc"
        has_project = bool(project and str(pid).startswith("proj-") and self.store.get(pid))
        preview_args = self.redact({"projectId": pid, "agentKey": spec.key,
                                    "taskKey": task.get("key"), "tool": tool,
                                    "target": target, "arguments": args})
        rec = self.gate.request(labelled_tool, preview_args, principal=f"orchestrator:{spec.key}",
                                danger_keys=sorted(list((args or {}).keys()))[:12])
        before_text = str(before or "")[:4000]
        row = {
            "requestId": rec.id, "ts": _now(), "projectId": pid,
            "agentKey": spec.key, "taskKey": task.get("key"), "tool": tool,
            "kind": kind, "target": target, "summary": str(summary)[:400],
            "state": "pending", "preview": self.redact(preview_args),
            "before": before_text, "after": "", "changePreview": "",
            "undo": {"method": "undo_redo/undo or manage_snapshots/restore",
                     "snapshotId": ((project or {}).get("recovery") or {}).get("snapshotId")},
            "decidedAt": None, "note": None,
        }
        if has_project:
            self.store.add_approval(project, row)
            project.setdefault("stats", {})["approvalRequests"] = int(project.get("stats", {}).get("approvalRequests", 0)) + 1
            self.append_timeline(project, spec.key, "approval.requested",
                                 f"Human approval required for {tool} ({kind}) — request {rec.id}",
                                 task_key=task.get("key"), level="warn",
                                 detail={"requestId": rec.id, "kind": kind, "target": target})
        timeout = min(float(self.settings["approvalWaitSeconds"]), max(1.0, rec.expires - _now()))
        answered = self.gate.wait(rec, timeout)
        if not answered or rec.state != "approved":
            state = rec.state if answered else "expired"
            if has_project:
                self.store.update_approval(project, rec.id, {"state": state, "decidedAt": _now(),
                                                             "note": rec.note})
                self.append_timeline(project, "human", "approval.decided",
                                     f"Approval {rec.id} {state}", task_key=task.get("key"), level="warn")
            raise PolicyError("approval_denied" if state == "denied" else "approval_timeout",
                              f"Approval {rec.id} for {tool} was {state}")
        if has_project:
            self.store.update_approval(project, rec.id, {"state": "approved", "decidedAt": _now(),
                                                         "note": rec.note})
            self.append_timeline(project, "human", "approval.decided",
                                 f"Approval {rec.id} approved", task_key=task.get("key"))
        return {"ok": True, "requestId": rec.id, "approved": True}

    def finish_approval(self, project, agent_key: str, after: str = ""):
        if not after:
            return
        rows = [r for r in (project or {}).get("approvals", []) if r.get("agentKey") == agent_key
                and r.get("state") == "approved"]
        if not rows:
            return
        row = rows[-1]
        after_text = str(after)[:4000]
        diff = list(difflib.unified_diff(
            str(row.get("before") or "").splitlines(), after_text.splitlines(),
            fromfile="before", tofile="after", lineterm=""))[:40]
        self.store.update_approval(project, row["requestId"],
                                   {"after": after_text, "changePreview": "\n".join(diff)[:4000]})

    def _resolve_approval(self, request_id: str, approve: bool, note: str = "", remember: bool = False):
        from confirm_gate import GateError  # noqa: E402
        if not request_id:
            raise ValueError("requestId is required")
        try:
            decision = self.gate.resolve(
                request_id,
                {"approve": bool(approve), "note": note, "remember": bool(remember)},
                principal=None, source="operator",
            )
        except GateError as exc:
            raise ValueError(f"{exc.reason}: {exc.message}") from exc
        self.store.update_approval_by_request(request_id, {
            "state": "approved" if decision.get("state") == "approved" else "denied",
            "decidedAt": _now(), "note": note,
        })
        self.emit("mcp.confirm." + ("approved" if approve else "denied"),
                  {"requestId": request_id, "source": "operator", "note": note})
        return {"ok": True, **decision}

    def approvals_view(self, project: dict = None) -> dict:
        gate_status = self.gate.status()
        rows = list((project or {}).get("approvals") or [])
        return {
            "gate": {"mode": gate_status["mode"], "ttlSeconds": gate_status["ttlSeconds"],
                     "counters": gate_status["counters"]},
            "pending": gate_status["pending"],
            "pendingCount": gate_status["pendingCount"],
            "recentDecisions": gate_status["recentDecisions"][:10],
            "projectRequests": rows[-30:][::-1],
        }

    # -- bus task board -------------------------------------------------------

    def _create_bus_tasks(self, project: dict, tasks: list, key_to_bus: dict):
        pm_id = self.registry.bus_id("project-manager") or "operator"
        for task in tasks:
            deps = [key_to_bus[d] for d in task.get("dependsOn", []) if d in key_to_bus]
            row = self.bus.create_task(
                title=f"[{project['slug']}] {task['title']}",
                detail=task["detail"], created_by=pm_id,
                assignee=self.registry.bus_id(task["agentKey"]) or None,
                priority=task.get("priority", "normal"),
                tags=["arena_team", project["id"], task["key"], task["agentKey"]],
                depends_on=deps,
            )
            task["busTaskId"] = row["id"]
            key_to_bus[task["key"]] = row["id"]
        self.store.save(project)
        return key_to_bus

    def _bootstrap_plan(self, project: dict) -> dict:
        analysis = {
            "key": "analysis", "title": "Analyze existing Roblox place",
            "detail": "Read live Studio state, survey the place, inspect selection and playtest capabilities before any edit.",
            "agentKey": "project-manager", "busTaskId": None, "paths": [], "dependsOn": [],
            "priority": "critical", "kind": "analysis", "status": "todo", "attempts": 0,
            "result": None, "resultData": {}, "artifacts": [], "verification": {}, "work": {"phase": "analysis"},
            "reviewRequired": False, "baseRevision": None, "started": None, "completed": None, "error": None,
        }
        design = dict(analysis)
        design.update({"key": "design", "title": "Produce technical design",
                       "detail": "Derive architecture, component ownership, dependencies and verification plan from survey facts.",
                       "dependsOn": ["analysis"], "kind": "design", "work": {"phase": "design"}})
        return {"tasks": [analysis, design], "conflicts": [], "generatedAt": _now(),
                "generator": "arena_team.bootstrap-v1",
                "settings": {k: self.settings[k] for k in ("maxConcurrentTasks", "leaseSeconds", "maxRepairAttempts")}}

    def _expand_plan(self, project: dict):
        design = self.plan_design(project)
        plan = build_plan(project["objective"], design, self.registry.roster(), self.settings)
        external = external_plan(project["objective"], project.get("analysis") or {},
                                 design, self.registry.roster())
        if external is not None:
            # Validate and adopt externally supplied tasks, retaining our phase tasks.
            enabled = {a["key"] for a in self.registry.roster() if a["enabled"] and a["driverReady"]}
            for raw in external.get("tasks") or []:
                if not isinstance(raw, dict):
                    continue
                key = str(raw.get("key") or "").strip()
                agent_key = str(raw.get("agentKey") or "")
                if not key or agent_key not in enabled:
                    continue
                plan["tasks"].append({
                    "key": key, "title": str(raw.get("title") or key),
                    "detail": str(raw.get("detail") or ""), "agentKey": agent_key, "busTaskId": None,
                    "paths": [p for p in (raw.get("paths") or []) if isinstance(p, str)],
                    "dependsOn": [str(d) for d in (raw.get("dependsOn") or [])],
                    "priority": "normal", "kind": "implementation", "status": "todo",
                    "attempts": 0, "result": None, "resultData": {}, "artifacts": [],
                    "verification": {}, "work": {"external": True}, "reviewRequired": False,
                    "baseRevision": None, "started": None, "completed": None, "error": None,
                })
            plan["generator"] = "arena_team.external-planner+deterministic"
            design["generatedBy"] = plan["generator"]
            design["externalPlanner"] = True
            project["design"] = design
        if plan["conflicts"]:
            raise PolicyError("plan_conflict", f"Plan has ownership conflicts: {plan['conflicts']}")
        existing = {t["key"] for t in project["plan"]["tasks"]}
        additions = [t for t in plan["tasks"] if t["key"] not in existing]
        key_to_bus = {t["key"]: t["busTaskId"] for t in project["plan"]["tasks"] if t.get("busTaskId")}
        self._create_bus_tasks(project, additions, key_to_bus)
        project["plan"]["tasks"].extend(additions)
        project["plan"]["conflicts"] = plan["conflicts"]
        project["plan"]["generatedAt"] = plan["generatedAt"]
        project["plan"]["generator"] = plan["generator"]
        project["plan"]["settings"] = plan["settings"]
        project["phase"] = "implementation"
        self.store.save(project)
        self.post_bus("project-manager", "Implementation plan ready",
                      f"{len(additions)} tasks created with dependencies and exclusive ownership "
                      f"(generator: {plan['generator']})", kind="handoff", project=project)
        self.append_timeline(project, "project-manager", "plan.generated",
                             f"Plan expanded to {len(project['plan']['tasks'])} tasks "
                             f"({len(plan['conflicts'])} conflicts)", level="info")
        return project["plan"]

    # -- task execution -------------------------------------------------------

    def _run_task(self, project: dict, task: dict) -> bool:
        key = task["key"]
        agent_key = task["agentKey"]
        spec = self.registry.spec(agent_key)
        if not spec or not spec.enabled:
            return self._fail_task(project, task, PolicyError("agent_unavailable", f"Agent {agent_key} is not enabled"))
        agent_id = self.registry.bus_id(agent_key)
        task["status"] = "in_progress"
        task["started"] = _now()
        task["attempts"] = int(task.get("attempts") or 0) + 1
        task["baseRevision"] = int(project.get("revision") or 0)
        self.store.save(project)
        self.registry.set_activity(agent_key, "working", key, task["title"])
        stop_lease = threading.Event()

        def _lease_keeper():
            while not stop_lease.wait(20.0):
                try:
                    self.bus.renew_task(task["busTaskId"], agent_id, int(self.settings["leaseSeconds"]))
                    self.registry.heartbeat_active()
                except Exception:  # noqa: BLE001
                    return

        try:
            if task.get("busTaskId"):
                self.bus.claim_task(task["busTaskId"], agent_id, int(self.settings["leaseSeconds"]))
            threading.Thread(target=_lease_keeper, name=f"lease-{key}", daemon=True).start()
            self.post_bus(agent_key, f"Started: {task['title']}",
                          task["detail"], kind="note", project=project)
            self.append_timeline(project, agent_key, "task.started", f"Claimed '{task['title']}'", task_key=key)
            runner = ToolRunner(self, spec, task, project)
            ctx = RunContext(self, spec, task, project, runner)
            driver = make_driver(spec, TOOL_MAP)
            result = driver.run(ctx)
            if not isinstance(result, dict) or not result.get("ok"):
                raise PolicyError("driver_failed", f"Driver returned no success for {key}")
            task["result"] = str(result.get("summary") or "completed")
            task["resultData"] = self.redact({
                k: v for k, v in result.items() if k not in ("artifacts", "toolCalls")
            }) | {"toolCalls": len(result.get("toolCalls") or []),
                  "driver": result.get("driver") or spec.driver.get("type")}
            task["artifacts"] = [{"id": a.get("id"), "kind": a.get("kind"), "path": a.get("path"),
                                  "summary": a.get("summary")} for a in (result.get("artifacts") or [])]
            task["verification"] = dict(task.get("verification") or {})
            task["status"] = "done"
            task["completed"] = _now()
            task["error"] = None
            if task.get("busTaskId"):
                self.bus.complete_task(task["busTaskId"], agent_id, result=task["result"],
                                       artifacts=[a["path"] or a["summary"] for a in task["artifacts"]][:10])
            if key == "analysis" and isinstance(result.get("analysis"), dict):
                project["analysis"] = self.redact(result["analysis"])
                if project["analysis"].get("peerId") and not project.get("peerId"):
                    project["peerId"] = project["analysis"]["peerId"]
                project["studio"]["connected"] = bool(project["analysis"].get("studioConnected"))
            if key == "design":
                project["phase"] = "design"
            self.store.save(project)
            self.post_bus(agent_key, f"Done: {task['title']}", task["result"],
                          kind="result", project=project)
            self.append_timeline(project, agent_key, "task.done", task["result"], task_key=key)
            self._bump_agent_stat(agent_key, "tasksDone")
            self._bump_agent_stat(agent_key, "toolCalls", int(len(runner.calls)))
            return True
        except Exception as exc:  # noqa: BLE001 — every failure is recorded
            return self._fail_task(project, task, exc)
        finally:
            stop_lease.set()
            self.registry.set_activity(agent_key, "idle", None, "waiting for work")

    def _fail_task(self, project: dict, task: dict, exc: Exception) -> bool:
        reason = _exc_reason(exc)
        task["status"] = "failed"
        task["error"] = f"{reason}: {str(exc)[:400]}"
        task["completed"] = _now()
        self.store.save(project)
        self._bump_agent_stat(task["agentKey"], "failures")
        agent_id = self.registry.bus_id(task["agentKey"])
        if task.get("busTaskId") and agent_id:
            try:
                self.bus.release_task(task["busTaskId"], agent_id, note=task["error"])
                self.bus.update_task(task["busTaskId"], actor="orchestrator", status="blocked")
            except Exception:  # noqa: BLE001
                pass
        self.post_bus(task["agentKey"], f"Blocked: {task['title']}", task["error"],
                      kind="alert", project=project, priority="high")
        self.append_timeline(project, task["agentKey"], "task.failed", task["error"],
                             task_key=task["key"], level="error",
                             detail={"reason": reason})
        return False

    def _bump_agent_stat(self, agent_key: str, name: str, amount: int = 1):
        with self.lock:
            row = self.agent_stats.setdefault(agent_key, {"tasksDone": 0, "failures": 0,
                                                          "toolCalls": 0, "seconds": 0.0})
            row[name] = float(row.get(name, 0)) + amount

    def _deps_done(self, project: dict, task: dict) -> bool:
        by_key = {t["key"]: t for t in project["plan"]["tasks"]}
        return all(by_key.get(d, {}).get("status") == "done" for d in task.get("dependsOn", []))

    def _execute_waves(self, project: dict, worker: dict):
        tasks = project["plan"]["tasks"]
        max_workers = int(self.settings["maxConcurrentTasks"])
        while not worker["stop"].is_set():
            if worker["pause"].is_set() or project.get("status") == "paused":
                worker["stop"].wait(0.5)
                continue
            ready = [t for t in tasks if t.get("status") == "todo" and self._deps_done(project, t)
                     and self._agent_ready(t["agentKey"])]
            pending = [t for t in tasks if t.get("status") in ("todo", "in_progress")]
            failed = [t for t in tasks if t.get("status") == "failed"]
            if not ready:
                if not pending:
                    break
                if failed:
                    before = len(project["plan"]["tasks"])
                    self._maybe_repair(project, failed[-1])
                    repairs = int(project.get("stats", {}).get("repairs", 0))
                    if len(project["plan"]["tasks"]) == before or repairs >= int(self.settings["maxRepairAttempts"]):
                        break
                    continue
                worker["stop"].wait(0.35)
                continue
            # Mark before submitting so the next loop cannot dispatch twice.
            for t in ready[:max_workers]:
                t["status"] = "in_progress"
            self.store.save(project)
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="arena-team") as pool:
                futures = [pool.submit(self._run_task, project, t) for t in ready[:max_workers]]
                for future in futures:
                    try:
                        future.result()
                    except Exception as exc:  # noqa: BLE001
                        self.append_timeline(project, "orchestrator", "task.worker_error",
                                             str(exc)[:300], level="error")

    def _agent_ready(self, key: str) -> bool:
        spec = self.registry.spec(key)
        return bool(spec and spec.enabled and self.registry.driver_ready(spec))

    def _maybe_repair(self, project: dict, failed: dict):
        """Create a real repair task, then re-run QA — bounded by maxRepairAttempts."""
        reason = str(failed.get("error") or "")
        if failed.get("kind") not in ("qa", "implementation", "review"):
            return
        if not any(word in reason for word in ("runtime_errors", "playtest_failed", "verification_failed",
                                               "review_failed", "tool_reported_failure", "no_peer")):
            return
        if not (project.get("analysis") or {}).get("studioConnected"):
            return  # a repair cannot succeed against a disconnected Studio
        repairs = int(project.get("stats", {}).get("repairs", 0))
        if repairs >= int(self.settings["maxRepairAttempts"]):
            return
        repairs += 1
        project.setdefault("stats", {})["repairs"] = repairs
        owner_key = failed.get("agentKey") if failed.get("kind") == "implementation" else "luau-engineer"
        spec = self.registry.spec(owner_key) or self.registry.spec("luau-engineer")
        key = f"repair_{repairs}"
        repair = {
            "key": key, "title": f"Repair pass {repairs} after {failed['key']} failure",
            "detail": f"Re-run {spec.name}'s verified playbook to repair: {reason[:200]}",
            "agentKey": spec.key, "busTaskId": None, "paths": list(spec.owned_paths),
            "dependsOn": [], "priority": "critical", "kind": "repair", "status": "todo",
            "attempts": 0, "result": None, "resultData": {}, "artifacts": [], "verification": {},
            "work": {"repair": True, "failedTask": failed["key"]}, "reviewRequired": False,
            "baseRevision": None, "started": None, "completed": None, "error": None,
        }
        key_to_bus = {t["key"]: t["busTaskId"] for t in project["plan"]["tasks"] if t.get("busTaskId")}
        self._create_bus_tasks(project, [repair], key_to_bus)
        project["plan"]["tasks"].append(repair)
        # Reset the failed QA/implementation task so it runs again after the repair.
        failed["status"] = "todo"
        failed["dependsOn"] = sorted(set(list(failed.get("dependsOn") or []) + [key]))
        if failed.get("busTaskId"):
            deps = [key_to_bus[d] for d in failed["dependsOn"] if d in key_to_bus]
            try:
                self.bus.update_task(failed["busTaskId"], actor="orchestrator",
                                     status="todo", dependsOn=deps)
            except Exception:  # noqa: BLE001
                pass
        self.store.save(project)
        self.post_bus("project-manager", f"Repair pass {repairs} scheduled",
                      f"{spec.name} will re-run its playbook after {failed['key']} failed.",
                      kind="alert", project=project, priority="high")
        self.append_timeline(project, "project-manager", "task.repair_scheduled",
                             f"Repair {key} scheduled for {failed['key']}", level="warn")

    # -- project worker -------------------------------------------------------

    def _project_worker(self, project_id: str, worker: dict):
        project = self.store.get(project_id)
        if not project:
            return
        try:
            project["status"] = "running"
            project["stats"]["startTime"] = _now()
            self.store.save(project)
            self.append_timeline(project, "orchestrator", "project.status", "Pipeline running")
            for key in ("analysis", "design"):
                task = next(t for t in project["plan"]["tasks"] if t["key"] == key)
                task["status"] = "todo"
                self._run_task(project, task)
                if worker["stop"].is_set():
                    return
                if task["status"] != "done":
                    raise PolicyError("pipeline_failed", f"{key} task failed: {task.get('error')}")
            if project.get("approvalMode") != "off":
                project["status"] = "awaiting_approval"
                self.store.save(project)
                self._await_plan_approval(project)
                if worker["stop"].is_set():
                    return
            project["status"] = "running"
            self.store.save(project)
            self._expand_plan(project)
            self._execute_waves(project, worker)
            project["phase"] = "report"
            project["status"] = "verifying"
            self.store.save(project)
            # The report task is part of the board and depends on QA + security.
            report_task = next((t for t in project["plan"]["tasks"] if t["key"] == "report"), None)
            if report_task and report_task.get("status") == "todo" and self._deps_done(project, report_task):
                self._run_task(project, report_task)
            report = self.build_report(project)
            project["report"] = report
            verified = bool(report["verified"])
            project["status"] = "completed" if verified else "failed"
            project["stats"]["endTime"] = _now()
            self.store.save(project)
            self.emit("agent.project.completed" if verified else "agent.project.failed",
                      {"projectId": project_id, "verified": verified},
                      level="info" if verified else "warn")
            self.audit("agent.project.completed" if verified else "agent.project.failed",
                       actor="orchestrator", target=project_id,
                       detail={"verified": verified, "tasks": len(project["plan"]["tasks"])},
                       outcome="ok" if verified else "error")
            self.post_bus("project-manager", "Final development report",
                          f"{report['title']} · verified={verified}", kind="result",
                          project=project, priority="high")
            self.append_timeline(project, "orchestrator", "project.completed",
                                 f"Report: {report['status']} (verified={verified})",
                                 level="info" if verified else "warn")
        except Exception as exc:  # noqa: BLE001
            project["status"] = "failed"
            project["error"] = f"{_exc_reason(exc)}: {str(exc)[:400]}"
            project["stats"]["endTime"] = _now()
            project["report"] = self.build_report(project)
            self.store.save(project)
            self.append_timeline(project, "orchestrator", "project.status",
                                 f"Pipeline failed: {project['error']}", level="error")
        finally:
            with self.lock:
                self.workers.pop(project_id, None)

    def _await_plan_approval(self, project: dict):
        spec = self.registry.spec("project-manager") or next(iter(self.registry.config.agents.values()))
        preview = {
            "plan": "project-wide implementation plan",
            "tasks": [{"key": t["key"], "title": t["title"], "agentKey": t["agentKey"]}
                      for t in project["plan"]["tasks"]],
            "design": (project.get("design") or {}).get("title"),
            "studio": self.redact(project.get("studio") or {}),
        }
        self.request_approval(
            project, spec, {"key": "plan", "title": "Implementation plan", "paths": []},
            "project_plan", preview, "project_wide", "project-wide", before=None,
            summary=f"Approve the project-wide change set for: {project['objective'][:140]}",
        )

    # -- public actions -------------------------------------------------------

    def handle(self, payload: dict, role: str = "admin", principal: str = "operator") -> dict:
        payload = dict(payload or {})
        action = str(payload.pop("action", "status") or "status").strip().lower()
        if action not in READ_ACTIONS | WRITE_ACTIONS | ADMIN_ACTIONS:
            raise ValueError(f"Unknown agent_team action: {action!r}")
        if action in ADMIN_ACTIONS and role != "admin":
            raise PermissionError(f"agent_team {action} requires admin scope")
        if action in WRITE_ACTIONS and role == "read":
            raise PermissionError(f"agent_team {action} requires write scope")
        if action in WRITE_ACTIONS and self.read_only:
            raise PermissionError("Read-Only Inspector mode blocks agent_team writes")
        handler = getattr(self, "_action_" + action, None)
        if handler is None:
            raise ValueError(f"agent_team action {action!r} is not implemented")
        # Reads of an idle team stay side-effect free; writes, admin actions and
        # views of a started project require live Bus leases/presence.
        if action in WRITE_ACTIONS | ADMIN_ACTIONS or self.store.current():
            self.ensure_agent_presence()
        return handler(payload, role=role, principal=principal)

    def _project_for(self, payload: dict) -> dict:
        pid = payload.get("projectId")
        return self.store.get(pid) if pid else self.store.current()

    def _action_status(self, payload, role, principal):
        project = self._project_for(payload)
        return self.status_view(project)

    def _action_roster(self, payload, role, principal):
        return {"ok": True, "roster": self.registry.roster(), "agentStats": dict(self.agent_stats),
                "config": self.config.public()}

    def _action_config(self, payload, role, principal):
        return {"ok": True, "config": self.config.public(),
                "unknownTools": self.registry.unknown_tools(),
                "ownershipConflicts": self.registry.ownership_conflicts()}

    def _action_tool_manifest(self, payload, role, principal):
        query = str(payload.get("query") or "").lower()
        rows = [{"name": name, "description": spec.get("description", "")[:300],
                 "readOnlyHint": (spec.get("annotations") or {}).get("readOnlyHint")}
                for name, spec in TOOL_MAP.items() if not query or query in name]
        rows.sort(key=lambda r: r["name"])
        return {"ok": True, "tools": rows[:200], "total": len(rows)}

    def _action_plan(self, payload, role, principal):
        project = self._project_for(payload)
        if not project:
            return {"ok": True, "active": False}
        return {"ok": True, "projectId": project["id"], "objective": project["objective"],
                "design": project.get("design") or {}, "plan": project.get("plan") or {},
                "analysis": self.redact(project.get("analysis") or {})}

    def _action_board(self, payload, role, principal):
        project = self._project_for(payload)
        board = self.bus.list_tasks(include_done=payload.get("includeDone", True))
        rows = [t for t in board["tasks"] if project and project["id"] in (t.get("tags") or [])]
        return {"ok": True, "bus": {"counts": board["counts"], "cursor": self.bus.stats()["cursor"]},
                "tasks": rows or board["tasks"], "projectTasks": (project or {}).get("plan", {}).get("tasks", [])}

    def _action_feed(self, payload, role, principal):
        project = self._project_for(payload)
        limit = max(1, min(int(payload.get("limit") or 80), 300))
        thread = self.bus.read_thread(project["id"]) if project else {"messages": []}
        messages = (thread.get("messages") or [])[-limit:][::-1]
        feed = self.bus.feed(int(payload.get("since") or 0), limit)
        return {"ok": True, "messages": messages, "events": feed["events"][-limit:],
                "cursor": feed["cursor"], "threadId": project["id"] if project else None}

    def _action_logs(self, payload, role, principal):
        project = self._project_for(payload)
        if not project:
            return {"ok": True, "logs": []}
        limit = max(1, min(int(payload.get("limit") or 150), 800))
        level = str(payload.get("level") or "all").lower()
        rows = project.get("timeline") or []
        if level != "all":
            rows = [r for r in rows if r.get("level") == level]
        return {"ok": True, "logs": rows[-limit:][::-1], "total": len(project.get("timeline") or [])}

    def _action_approvals(self, payload, role, principal):
        return {"ok": True, **self.approvals_view(self._project_for(payload))}

    def _action_confirmations(self, payload, role, principal):
        return {"ok": True, **self.approvals_view(self._project_for(payload))}

    def _action_approve(self, payload, role, principal):
        return self._resolve_approval(str(payload.get("requestId") or ""), True,
                                      str(payload.get("note") or ""), bool(payload.get("remember")))

    def _action_deny(self, payload, role, principal):
        return self._resolve_approval(str(payload.get("requestId") or ""), False,
                                      str(payload.get("note") or ""))

    def _action_report(self, payload, role, principal):
        project = self._project_for(payload)
        if not project:
            return {"ok": True, "active": False}
        report = project.get("report") or self.build_report(project)
        return {"ok": True, "report": report}

    def _action_evidence(self, payload, role, principal):
        project = self._project_for(payload)
        return {"ok": True, "evidence": list((project or {}).get("evidence") or [])[::-1],
                "root": str(self.store.evidence_dir)}

    def _action_studio(self, payload, role, principal):
        return {"ok": True, "studio": self._studio_snapshot(force=bool(payload.get("force")))}

    def _action_stats(self, payload, role, principal):
        project = self._project_for(payload)
        return {"ok": True, "project": (project or {}).get("stats") or {},
                "agents": dict(self.agent_stats), "bus": self.bus.stats(),
                "workers": {pid: {"paused": w["pause"].is_set(), "alive": w["thread"].is_alive()}
                            for pid, w in self.workers.items()}}

    def _action_projects(self, payload, role, principal):
        return {"ok": True, "projects": self.store.list(int(payload.get("limit") or 20)),
                "current": self.store.status_view(self.store.current())}

    def _action_studio_summary(self, payload, role, principal):
        return self.studio_summary()

    def _action_message(self, payload, role, principal):
        project = self._project_for(payload)
        body = str(payload.get("body") or payload.get("message") or "").strip()
        if not body:
            raise ValueError("message body is required")
        msg = self.post_bus(str(payload.get("from") or "human"), str(payload.get("subject") or "Operator message"),
                            body, kind=str(payload.get("kind") or "note"), project=project,
                            to=payload.get("to"), priority=str(payload.get("priority") or "normal"))
        if project:
            self.append_timeline(project, "human", "operator.message", body[:200])
        return {"ok": True, "message": msg}

    def _action_start_project(self, payload, role, principal):
        objective = str(payload.get("objective") or "").strip()
        if len(objective) < 5 or len(objective) > 2000:
            raise ValueError("objective must be 5..2000 characters")
        mode = str(payload.get("approvalMode") or self.settings["defaultApprovalMode"])
        if mode not in APPROVAL_MODES:
            raise ValueError(f"approvalMode must be one of {APPROVAL_MODES}")
        if mode == "off" and (role != "admin" or not payload.get("allowUnattended")):
            raise PermissionError("approvalMode='off' requires admin scope and allowUnattended=true")
        require_studio = bool(payload.get("requireStudio", self.settings["requireStudioForWriteTasks"]))
        studio = self._studio_snapshot(force=True)
        if require_studio and not studio.get("connected"):
            raise ValueError("no_peer: no live Studio Edit peer is connected; set requireStudio=false for analysis-only runs")
        peer_id = payload.get("peerId") or studio.get("peerId")
        project = self.store.create(
            objective, mode,
            studio={"connected": studio.get("connected"), "place": studio.get("place"),
                    "placeId": studio.get("placeId"), "peerId": peer_id,
                    "dataModel": studio.get("activeDataModel") or "edit"},
            peer_id=peer_id, requested_by=principal,
        )
        project["plan"] = self._bootstrap_plan(project)
        key_to_bus = {}
        self._create_bus_tasks(project, project["plan"]["tasks"], key_to_bus)
        if mode == "off":
            self.audit("agent.project.unattended", actor=principal, target=project["id"],
                       detail={"objective": objective[:200]}, outcome="ok")
            self.append_timeline(project, "human", "approval.bypassed",
                                 "Unattended mode explicitly enabled by admin (audited)", level="warn")
        self.post_bus("project-manager", "Project objective received", objective,
                      kind="note", project=project, priority="high")
        self.emit("agent.project.started", {"projectId": project["id"], "approvalMode": mode,
                                            "studioConnected": studio.get("connected")})
        stop = threading.Event()
        pause = threading.Event()
        thread = threading.Thread(target=self._project_worker, args=(project["id"],
                                  {"thread": None, "stop": stop, "pause": pause}),
                                  name=f"arena-team-{project['id']}", daemon=True)
        worker = {"thread": thread, "stop": stop, "pause": pause}
        thread.start()
        with self.lock:
            self.workers[project["id"]] = worker
        return {"ok": True, "projectId": project["id"], "status": "planning",
                "approvalMode": mode, "studioConnected": studio.get("connected"),
                "peerId": peer_id, "agentCount": len(self.registry.roster()),
                "note": "Worker started. Approve the plan when the confirmation appears."}

    def _worker_for(self, payload: dict):
        project = self._project_for(payload)
        if not project:
            raise ValueError("No project found")
        with self.lock:
            worker = self.workers.get(project["id"])
        if not worker:
            raise ValueError(f"Project {project['id']} has no active worker")
        return project, worker

    def _action_pause(self, payload, role, principal):
        project, worker = self._worker_for(payload)
        worker["pause"].set()
        project["status"] = "paused"
        self.store.save(project)
        self.append_timeline(project, "human", "project.status", "Pipeline paused by operator", level="warn")
        return {"ok": True, "projectId": project["id"], "status": "paused"}

    def _action_resume(self, payload, role, principal):
        project, worker = self._worker_for(payload)
        worker["pause"].clear()
        project["status"] = "running"
        self.store.save(project)
        self.append_timeline(project, "human", "project.status", "Pipeline resumed by operator")
        return {"ok": True, "projectId": project["id"], "status": "running"}

    def _action_cancel(self, payload, role, principal):
        project = self._project_for(payload)
        if not project:
            raise ValueError("No project found")
        with self.lock:
            worker = self.workers.get(project["id"])
        if worker:
            worker["stop"].set()
        project["status"] = "cancelled"
        project["stats"]["endTime"] = _now()
        self.store.save(project)
        for task in project["plan"]["tasks"]:
            if task.get("status") == "in_progress" and task.get("busTaskId"):
                try:
                    self.bus.release_task(task["busTaskId"], self.registry.bus_id(task["agentKey"]),
                                          note="project cancelled")
                except Exception:  # noqa: BLE001
                    pass
            if task.get("status") in ("todo", "in_progress"):
                task["status"] = "cancelled"
        self.store.save(project)
        self.append_timeline(project, "human", "project.status", "Project cancelled by operator", level="warn")
        return {"ok": True, "projectId": project["id"], "status": "cancelled"}

    def _action_configure(self, payload, role, principal):
        config_path = payload.get("configPath")
        if payload.get("agents") and isinstance(payload["agents"], list):
            raw = {"version": int(payload.get("version") or 1),
                   "description": "Runtime-configured team", "agents": payload["agents"],
                   "settings": {**self.config.settings, **(payload.get("settings") or {})}}
            cfg = TeamConfig(raw, source="runtime")
        else:
            cfg = load_config(config_path)
        unknown = [f"{s.key}:{t}" for s in cfg.agents.values() for t in s.allowed_tools if t not in TOOL_MAP]
        if unknown:
            raise ValueError(f"Config references unknown MCP tools: {unknown[:8]}")
        self.config = cfg
        self.settings = dict(cfg.settings)
        sync = self.registry.reload(cfg)
        self.audit("agent.team.configured", actor=principal, detail={"source": cfg.source,
                   "agentCount": len(cfg.agents)}, outcome="ok")
        return {"ok": True, "config": cfg.public(), "sync": sync}

    def _action_reset(self, payload, role, principal):
        """Admin-only: clear persisted projects/evidence/agents (never the bridge)."""
        raise PermissionError("reset is disabled; delete bridge_state/arena_team manually")

    def _action_run_task(self, payload, role, principal):
        """Operator-triggered retry of a blocked/failed task (explicitly supervised)."""
        project = self._project_for(payload)
        key = str(payload.get("taskKey") or "")
        task = next((t for t in (project or {}).get("plan", {}).get("tasks", []) if t["key"] == key), None)
        if not project or not task:
            raise ValueError(f"Unknown taskKey {key!r}")
        task["status"] = "todo"
        if task.get("busTaskId"):
            try:
                self.bus.update_task(task["busTaskId"], actor="operator", status="todo")
            except Exception:  # noqa: BLE001
                pass
        self.store.save(project)
        ok = self._run_task(project, task)
        return {"ok": ok, "task": {k: task.get(k) for k in ("key", "status", "result", "error")}}

    def _action_claim(self, payload, role, principal):
        project = self._project_for(payload)
        key = str(payload.get("taskKey") or "")
        task = next((t for t in (project or {}).get("plan", {}).get("tasks", []) if t["key"] == key), None)
        if not task or not task.get("busTaskId"):
            raise ValueError(f"Unknown taskKey {key!r}")
        agent_id = payload.get("agentId") or self.human_agent_id
        # A human operator claims through the existing Bus; the task stays leased.
        row = self.bus.claim_task(task["busTaskId"], str(agent_id), int(self.settings["leaseSeconds"]))
        return {"ok": True, "task": row}

    def _action_complete(self, payload, role, principal):
        project = self._project_for(payload)
        key = str(payload.get("taskKey") or "")
        task = next((t for t in (project or {}).get("plan", {}).get("tasks", []) if t["key"] == key), None)
        if not task or not task.get("busTaskId"):
            raise ValueError(f"Unknown taskKey {key!r}")
        agent_id = payload.get("agentId") or self.human_agent_id
        row = self.bus.complete_task(task["busTaskId"], str(agent_id),
                                     result=str(payload.get("result") or "completed by operator"))
        task["status"] = "done"
        task["result"] = str(payload.get("result") or "completed by operator")
        self.store.save(project)
        return {"ok": True, "task": row}

    def _action_undo(self, payload, role, principal):
        project = self._project_for(payload)
        spec = self.registry.spec("lead-developer") or next(iter(self.registry.config.agents.values()))
        steps = max(1, min(int(payload.get("steps") or 1), 5))
        self.request_approval(project or {"id": "ad-hoc", "approvals": [], "stats": {}}, spec,
                              {"key": "undo", "paths": []}, "undo_redo",
                              {"action": "undo", "steps": steps}, "destructive", "ChangeHistoryService",
                              summary=f"Undo the last {steps} Studio job(s)")
        result = call_tool_with_context(
            self.client, "undo_redo", {"action": "undo", "steps": steps},
            project=project, principal=f"agent_team:operator:{principal}",
        )
        self.audit("agent.undo", actor=principal, target=(project or {}).get("id", ""),
                   detail={"steps": steps}, outcome="ok")
        return {"ok": True, "result": result}

    def _action_create_snapshot(self, payload, role, principal):
        result = call_tool_with_context(
            self.client, "manage_snapshots",
            {"action": "create", "root": str(payload.get("root") or "game"),
             "label": str(payload.get("label") or "Arena Team recovery point")},
            project=project, principal=f"agent_team:operator:{principal}",
        )
        self.audit("agent.snapshot.create", actor=principal, detail=result, outcome="ok")
        return {"ok": True, "result": result}

    def _action_restore_snapshot(self, payload, role, principal):
        project = self._project_for(payload)
        spec = self.registry.spec("lead-developer") or next(iter(self.registry.config.agents.values()))
        snapshot_id = str(payload.get("snapshotId") or "")
        if not snapshot_id:
            raise ValueError("snapshotId is required")
        self.request_approval(project or {"id": "ad-hoc", "approvals": [], "stats": {}}, spec,
                              {"key": "restore", "paths": []}, "manage_snapshots",
                              {"action": "restore", "snapshotId": snapshot_id}, "destructive", "game",
                              summary=f"Restore snapshot {snapshot_id}")
        result = call_tool_with_context(
            self.client, "manage_snapshots", {"action": "restore", "snapshotId": snapshot_id},
            project=project, principal=f"agent_team:operator:{principal}",
        )
        self.audit("agent.snapshot.restore", actor=principal, target=snapshot_id, outcome="ok")
        return {"ok": True, "result": result}

    # -- views ----------------------------------------------------------------

    def _studio_snapshot(self, force: bool = False) -> dict:
        with self.lock:
            if not force and _now() - self._studio_cache["ts"] < 4.0:
                return dict(self._studio_cache["data"])
        try:
            health = self.client.health() or {}
        except Exception as exc:  # noqa: BLE001
            health = {"error": str(exc)[:200]}
        try:
            fleet = self._fleet_snapshot() if self._fleet_snapshot else self.client.peers()
        except Exception:
            fleet = {"peers": []}
        peers = fleet.get("studios") or fleet.get("peers") or []
        online = [p for p in peers if p.get("online")]
        edit = next((p for p in online if p.get("dm") == "edit"), online[0] if online else {})
        studio = health.get("studio") or {}
        selection = {"count": 0, "reason": "not_queried"}
        if health.get("studio_connected"):
            try:
                selection = call_tool_with_context(
                    self.client, "manage_selection", {"action": "get"},
                    principal="agent_team:status",
                ) or {}
            except Exception as exc:  # noqa: BLE001
                selection = {"count": 0, "reason": _exc_reason(exc)}
        selection_rows = selection.get("selection") if isinstance(selection.get("selection"), list) else []
        selection_paths = [str(r.get("path")) for r in selection_rows
                           if isinstance(r, dict) and r.get("path")][:10]
        data = {
            "connected": bool(health.get("studio_connected")),
            "place": studio.get("place") or edit.get("place"),
            "placeId": studio.get("place_id") or edit.get("placeId") or 0,
            "pluginVersion": studio.get("plugin_version") or edit.get("pluginVersion"),
            "activeDataModel": edit.get("dm") or "edit",
            "peerId": edit.get("peerId") or edit.get("peer_id") or studio.get("peer_id"),
            "pinned": bool(fleet.get("pinned") or fleet.get("active")),
            "peers": [{"peerId": p.get("peerId"), "dm": p.get("dm"), "place": p.get("place"),
                       "online": p.get("online"), "label": p.get("label")} for p in peers],
            "peerCount": len(online),
            "selection": {"count": selection.get("count"), "paths": selection_paths,
                          "reason": selection.get("reason")},
            "confirmGate": health.get("confirmGate"),
            "readOnly": self.read_only,
        }
        with self.lock:
            self._studio_cache = {"ts": _now(), "data": data}
        return data

    def _all_projects(self) -> list:
        rows = []
        for info in self.store.list(50):
            doc = self.store.get(info["id"])
            if doc:
                rows.append(doc)
        return rows

    def status_view(self, project: dict = None) -> dict:
        """One redacted snapshot for the Control Center."""
        if project is None:
            project = self.store.current()
        studio = self._studio_snapshot()
        feed = self._action_feed({"limit": 60}, "read", "status")
        approvals = self.approvals_view(project)
        tasks = (project or {}).get("plan", {}).get("tasks", [])
        counts = {}
        for t in tasks:
            counts[t.get("status", "todo")] = counts.get(t.get("status", "todo"), 0) + 1
        return {
            "ok": True,
            "version": ORCHESTRATOR_VERSION,
            "serverTime": _now(),
            "active": bool(project),
            "project": self.store.status_view(project),
            "studio": studio,
            "roster": self.registry.roster(),
            "agentStats": dict(self.agent_stats),
            "plan": {"design": (project or {}).get("design") or {},
                     "tasks": tasks, "conflicts": (project or {}).get("plan", {}).get("conflicts", []),
                     "generator": (project or {}).get("plan", {}).get("generator")},
            "taskCounts": counts,
            "feed": feed["messages"],
            "events": feed["events"],
            "logs": ((project or {}).get("timeline") or [])[-120:][::-1],
            "approvals": approvals,
            "evidence": ((project or {}).get("evidence") or [])[::-1],
            "artifacts": ((project or {}).get("artifacts") or [])[-30:][::-1],
            "stats": (project or {}).get("stats") or {},
            "report": (project or {}).get("report") or {},
            "config": {"settings": self.settings, "source": self.config.source,
                       "unknownTools": self.registry.unknown_tools(),
                       "ownershipConflicts": self.registry.ownership_conflicts()},
            "workers": {pid: {"paused": w["pause"].is_set(), "alive": w["thread"].is_alive()}
                        for pid, w in self.workers.items()},
        }

    def studio_summary(self) -> dict:
        """Tiny, secret-free summary embedded into /api/poll for the Studio DockWidget."""
        project = self.store.current()
        roster = self.registry.roster()
        tasks = (project or {}).get("plan", {}).get("tasks", [])
        active = next((f"{t['agentKey']}: {t['title'][:32]}" for t in tasks
                       if t.get("status") == "in_progress"), "")
        try:
            pending = self.gate.status()["pendingCount"]
        except Exception:
            pending = 0
        return {
            "active": bool(project),
            "status": (project or {}).get("status") or "idle",
            "phase": (project or {}).get("phase") or "",
            "objective": (project or {}).get("objective", "")[:80],
            "agentsOnline": sum(1 for a in roster if a["online"]),
            "agentsTotal": len(roster),
            "tasksDone": sum(1 for t in tasks if t.get("status") == "done"),
            "tasksTotal": len(tasks),
            "pendingApprovals": pending,
            "activeTask": active,
            "verified": ((project or {}).get("report") or {}).get("verified"),
        }
