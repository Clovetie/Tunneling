"""Persistent, project-specific shared context.

The Bus holds coordination state (leases, tasks, messages); this store holds the
*project*: objective, live Studio analysis, technical design, the task DAG with
verification receipts, an append-only execution timeline, evidence references,
approval receipts, agent statistics and the final development report. Files live
under ``bridge_state/arena_team/`` and are gitignored with the rest of bridge state.
"""

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

PROJECT_STATUSES = (
    "planning", "awaiting_approval", "running", "paused", "verifying",
    "completed", "failed", "cancelled",
)


def _now() -> float:
    return time.time()


def _slug(text: str, limit: int = 28) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", str(text or "")).strip("_")
    return (value[:limit] or "Project").rstrip("_")


class ProjectStore:
    def __init__(self, state_dir: Path, evidence_dir_name: str = "arena_evidence"):
        self.root = Path(state_dir) / "arena_team"
        self.projects_dir = self.root / "projects"
        self.evidence_dir = Path(state_dir) / evidence_dir_name
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._current_id = None
        self._load_current()

    # -- persistence ----------------------------------------------------------

    def _path(self, project_id: str) -> Path:
        # Ids are generated here, but never trust a caller-supplied path segment.
        safe = re.sub(r"[^A-Za-z0-9_\-]", "", str(project_id or ""))
        return self.projects_dir / f"{safe}.json"

    def _load_current(self):
        marker = self.root / "current.json"
        if marker.is_file():
            try:
                self._current_id = (json.loads(marker.read_text(encoding="utf-8")) or {}).get("id")
            except (OSError, ValueError):
                self._current_id = None

    def save(self, project: dict):
        with self.lock:
            project["updated"] = _now()
            target = self._path(project["id"])
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(project, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
            os.replace(tmp, target)

    def set_current(self, project_id: str):
        with self.lock:
            self._current_id = project_id
            marker = self.root / "current.json"
            marker.write_text(json.dumps({"id": project_id, "set": _now()}), encoding="utf-8")

    def get(self, project_id: str = None) -> dict:
        pid = project_id or self._current_id
        if not pid:
            return None
        path = self._path(pid)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def current(self) -> dict:
        return self.get()

    def list(self, limit: int = 20) -> list:
        with self.lock:
            rows = []
            for path in sorted(self.projects_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
                try:
                    doc = json.loads(path.read_text(encoding="utf-8"))
                    rows.append({
                        "id": doc.get("id"), "objective": doc.get("objective"),
                        "status": doc.get("status"), "phase": doc.get("phase"),
                        "created": doc.get("created"), "updated": doc.get("updated"),
                        "verified": (doc.get("report") or {}).get("verified"),
                        "taskCount": len(doc.get("plan", {}).get("tasks", [])),
                    })
                except (OSError, ValueError):
                    continue
            return rows

    # -- lifecycle ------------------------------------------------------------

    def create(self, objective: str, approval_mode: str, studio: dict = None,
               peer_id: str = None, requested_by: str = "operator") -> dict:
        pid = "proj-" + uuid.uuid4().hex[:10]
        project = {
            "id": pid,
            "slug": _slug(objective),
            "objective": str(objective).strip(),
            "requestedBy": requested_by,
            "created": _now(),
            "updated": _now(),
            "status": "planning",
            "phase": "analysis",
            "approvalMode": approval_mode,
            "revision": 0,
            "studio": studio or {},
            "peerId": peer_id,
            "analysis": {},
            "design": {},
            "plan": {"tasks": [], "conflicts": [], "generatedAt": None, "generator": None},
            "timeline": [],
            "artifacts": [],
            "evidence": [],
            "approvals": [],
            "messages": {"threadId": pid},
            "stats": {"toolCalls": 0, "writes": 0, "verifications": 0, "failures": 0, "repairs": 0,
                      "approvalRequests": 0, "startTime": None, "endTime": None},
            "report": None,
        }
        self.save(project)
        self.set_current(pid)
        self.append_timeline(project, "human", "project.created",
                             f"Objective received: {project['objective'][:180]}", level="info")
        return project

    def append_timeline(self, project: dict, actor: str, kind: str, message: str,
                        task_key: str = None, level: str = "info", detail: dict = None):
        row = {
            "ts": _now(), "actor": actor, "kind": kind, "message": str(message)[:600],
            "taskKey": task_key, "level": level, "detail": detail or {},
        }
        with self.lock:
            project.setdefault("timeline", []).append(row)
            del project["timeline"][:-800]
            self.save(project)
        return row

    def add_artifact(self, project: dict, agent_key: str, task_key: str, kind: str,
                     path: str = None, summary: str = "", data: dict = None):
        row = {
            "id": "art-" + uuid.uuid4().hex[:8], "ts": _now(), "agentKey": agent_key,
            "taskKey": task_key, "kind": kind, "path": path, "summary": str(summary)[:400],
            "data": data or {},
        }
        with self.lock:
            project.setdefault("artifacts", []).append(row)
            del project["artifacts"][:-300]
            self.save(project)
        return row

    def add_evidence(self, project: dict, agent_key: str, task_key: str, kind: str,
                     filename: str, summary: str = "", meta: dict = None):
        row = {
            "id": "ev-" + uuid.uuid4().hex[:8], "ts": _now(), "agentKey": agent_key,
            "taskKey": task_key, "kind": kind, "filename": str(filename),
            "url": f"/api/orchestrator/evidence/{Path(str(filename)).name}",
            "summary": str(summary)[:400], "meta": meta or {},
        }
        with self.lock:
            project.setdefault("evidence", []).append(row)
            del project["evidence"][:-100]
            self.save(project)
        return row

    def add_approval(self, project: dict, row: dict):
        with self.lock:
            project.setdefault("approvals", []).append(row)
            del project["approvals"][:-100]
            self.save(project)
        return row

    def update_approval(self, project: dict, request_id: str, changes: dict):
        with self.lock:
            for row in project.get("approvals", []):
                if row.get("requestId") == request_id:
                    row.update(changes)
                    self.save(project)
                    return row
        return None

    def update_approval_by_request(self, request_id: str, changes: dict) -> list:
        """Atomically stamp a decision into every persisted project.

        ConfirmGate sets its wake-up event while the REST decision handler is
        still running, so the project worker can save its terminal status before
        this method finishes. Loading/modifying/saving under the store lock
        prevents a stale whole-document write from resurrecting
        ``awaiting_approval`` after a denial.
        """
        with self.lock:
            updated = []
            for path in sorted(self.projects_dir.glob("*.json")):
                try:
                    doc = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                changed = False
                for row in doc.get("approvals") or []:
                    if row.get("requestId") == request_id:
                        row.update(changes)
                        changed = True
                if changed:
                    self.save(doc)
                    updated.append(doc.get("id"))
            return updated

    def bump_revision(self, project: dict, actor: str, target: str):
        with self.lock:
            project["revision"] = int(project.get("revision", 0)) + 1
            self.append_timeline(project, actor, "context.revision",
                                 f"Project revision {project['revision']} after write to {target}",
                                 level="debug")
            return project["revision"]

    def evidence_path(self, filename: str) -> Path:
        # Basename only: evidence files are always created here by the orchestrator.
        safe = Path(str(filename)).name
        return (self.evidence_dir / safe).resolve()

    def status_view(self, project: dict) -> dict:
        """Compact, redaction-friendly view for the Control Center / Studio strip."""
        if not project:
            return {"active": False}
        tasks = project.get("plan", {}).get("tasks", [])
        counts = {}
        for task in tasks:
            counts[task.get("status", "todo")] = counts.get(task.get("status", "todo"), 0) + 1
        return {
            "active": True,
            "id": project["id"],
            "objective": project["objective"],
            "status": project["status"],
            "phase": project["phase"],
            "approvalMode": project["approvalMode"],
            "revision": project["revision"],
            "taskCount": len(tasks),
            "taskCounts": counts,
            "verified": (project.get("report") or {}).get("verified"),
            "reportTitle": (project.get("report") or {}).get("title"),
            "updated": project.get("updated"),
        }
