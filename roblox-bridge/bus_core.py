#!/usr/bin/env python3
"""
bus_core.py — Multi-agent collaboration bus for the Arena Roblox Studio bridge.

Zero-dependency, thread-safe, JSON-persisted coordination layer that lets several
AI agents (and humans) share one Roblox Studio session without stepping on each
other:

  * Presence registry   — named agents with roles, capabilities and heartbeats.
  * Mailbox             — direct + broadcast messages, cursor-based inbox, acks.
  * Task board          — claim/lease/complete work items with priorities, tags
                          and dependencies. A lease expires automatically, so a
                          crashed agent cannot permanently hold a task.
  * Event feed          — bounded, sequence-numbered activity log.
  * Handoff export      — markdown brief for the next agent.

Everything is plain stdlib so it can be embedded in server.py, used from the CLI,
or driven through the `agent_bus` MCP tool.
"""

import json
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path

BUS_VERSION = "5.1.0"
DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "bridge_state"

AGENT_STATUSES = ("online", "busy", "idle", "offline")
TASK_STATUSES = ("todo", "in_progress", "blocked", "done", "cancelled")
MESSAGE_KINDS = ("note", "question", "answer", "handoff", "alert", "result")
PRIORITIES = ("low", "normal", "high", "critical")
_PRIORITY_RANK = {"low": 3, "normal": 2, "high": 1, "critical": 0}


def _now() -> float:
    return time.time()


def _clean(text, limit: int = 8000) -> str:
    if text is None:
        return ""
    text = str(text)
    return text[:limit]


class AgentBus:
    """Presence + mailbox + task board shared by every agent on the bridge."""

    def __init__(
        self,
        path=None,
        max_messages: int = 2000,
        max_events: int = 1000,
        default_lease_seconds: int = 900,
        presence_ttl: float = 90.0,
        persist: bool = True,
    ):
        self.path = Path(path) if path else DEFAULT_STATE_DIR / "bus.json"
        self.persist = bool(persist)
        self.lock = threading.RLock()
        self.agents = {}
        self.messages = deque(maxlen=max_messages)
        self.events = deque(maxlen=max_events)
        self.tasks = {}
        self.default_lease_seconds = int(default_lease_seconds)
        self.presence_ttl = float(presence_ttl)
        self.seq = 0
        self.created = _now()
        self.agents_reaped = 0
        self.tasks_reaped = 0
        self.last_sweep_at = None
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self):
        if not self.persist or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        with self.lock:
            self.agents = {a["id"]: a for a in raw.get("agents", []) if a.get("id")}
            self.messages = deque(raw.get("messages", [])[-self.messages.maxlen:], maxlen=self.messages.maxlen)
            self.events = deque(raw.get("events", [])[-self.events.maxlen:], maxlen=self.events.maxlen)
            self.tasks = {t["id"]: t for t in raw.get("tasks", []) if t.get("id")}
            self.seq = int(raw.get("seq") or 0)
            counters = raw.get("counters") or {}
            self.agents_reaped = int(counters.get("agentsReaped") or 0)
            self.tasks_reaped = int(counters.get("tasksReaped") or 0)
            self.last_sweep_at = counters.get("lastSweepAt")

    def _save(self):
        if not self.persist:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": BUS_VERSION,
                "saved": _now(),
                "seq": self.seq,
                "agents": list(self.agents.values()),
                "messages": list(self.messages),
                "events": list(self.events),
                "tasks": list(self.tasks.values()),
                "counters": {
                    "agentsReaped": self.agents_reaped,
                    "tasksReaped": self.tasks_reaped,
                    "lastSweepAt": self.last_sweep_at,
                },
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    # -- internals -----------------------------------------------------------

    def _next_seq_unlocked(self) -> int:
        self.seq += 1
        return self.seq

    def _emit_unlocked(self, kind: str, data: dict) -> dict:
        event = {"seq": self._next_seq_unlocked(), "kind": kind, "ts": _now(), "data": data}
        self.events.append(event)
        return event

    def _agent_unlocked(self, agent_id: str) -> dict:
        agent = self.agents.get(agent_id)
        if not agent:
            raise KeyError(f"Unknown agent id: {agent_id!r}")
        return agent

    def _touch_unlocked(self, agent: dict):
        agent["lastSeen"] = _now()
        agent["online"] = True

    def _expire_leases_unlocked(self):
        now = _now()
        for task in self.tasks.values():
            lease = task.get("lease") or {}
            if task.get("status") == "in_progress" and lease.get("expiresAt") and lease["expiresAt"] < now:
                prev_owner = task.get("assignee")
                task["status"] = "todo"
                task["assignee"] = None
                task["lease"] = None
                task["updated"] = now
                task["leaseExpiries"] = int(task.get("leaseExpiries", 0)) + 1
                self._emit_unlocked("task.lease_expired", {"taskId": task["id"], "previousAssignee": prev_owner})

    # -- agents / presence ---------------------------------------------------

    def register_agent(
        self,
        name: str,
        role: str = "agent",
        capabilities=None,
        agent_id: str = None,
        ttl: float = None,
        meta: dict = None,
    ) -> dict:
        name = (name or "").strip()
        if not name:
            raise ValueError("name is required")
        with self.lock:
            # Reuse an existing registration with the same name (case-insensitive).
            existing = next((a for a in self.agents.values() if a["name"].lower() == name.lower()), None)
            now = _now()
            caps = [str(c) for c in (capabilities or [])][:32]
            if existing:
                existing.update({
                    "name": name,
                    "role": _clean(role, 40) or "agent",
                    "capabilities": caps or existing.get("capabilities", []),
                    "status": "online",
                    "online": True,
                    "lastSeen": now,
                    "ttl": float(ttl or self.presence_ttl),
                    "meta": meta if isinstance(meta, dict) else existing.get("meta", {}),
                })
                agent = existing
                created_new = False
            else:
                aid = agent_id or ("agent-" + uuid.uuid4().hex[:8])
                if aid in self.agents:
                    aid = "agent-" + uuid.uuid4().hex[:8]
                agent = {
                    "id": aid,
                    "name": name,
                    "role": _clean(role, 40) or "agent",
                    "capabilities": caps,
                    "status": "online",
                    "online": True,
                    "firstSeen": now,
                    "lastSeen": now,
                    "ttl": float(ttl or self.presence_ttl),
                    "meta": meta if isinstance(meta, dict) else {},
                }
                self.agents[agent["id"]] = agent
                created_new = True
            self._emit_unlocked("agent.register" if created_new else "agent.heartbeat", {
                "agentId": agent["id"], "name": agent["name"], "role": agent["role"],
            })
            self._save()
            return self._agent_view_unlocked(agent)

    def heartbeat(self, agent_id: str, status: str = None) -> dict:
        with self.lock:
            agent = self._agent_unlocked(agent_id)
            self._touch_unlocked(agent)
            if status in AGENT_STATUSES:
                agent["status"] = status
            self._save()
            return self._agent_view_unlocked(agent)

    def set_status(self, agent_id: str, status: str) -> dict:
        if status not in AGENT_STATUSES:
            raise ValueError(f"status must be one of {', '.join(AGENT_STATUSES)}")
        with self.lock:
            agent = self._agent_unlocked(agent_id)
            self._touch_unlocked(agent)
            agent["status"] = status
            self._save()
            return self._agent_view_unlocked(agent)

    def _agent_view_unlocked(self, agent: dict) -> dict:
        row = dict(agent)
        age = _now() - float(agent.get("lastSeen") or 0)
        row["ageSeconds"] = round(age, 1)
        row["online"] = age <= float(agent.get("ttl") or self.presence_ttl)
        if not row["online"]:
            row["status"] = "offline"
        row["openTasks"] = sum(
            1 for t in self.tasks.values()
            if t.get("assignee") == agent["id"] and t.get("status") in ("todo", "in_progress", "blocked")
        )
        row["inboxUnread"] = sum(
            1 for m in self.messages
            if (m.get("to") in (agent["id"], "all", None)) and agent["id"] not in (m.get("readBy") or {})
        )
        return row

    def list_agents(self, include_offline: bool = True) -> dict:
        with self.lock:
            rows = [self._agent_view_unlocked(a) for a in self.agents.values()]
        if not include_offline:
            rows = [r for r in rows if r["online"]]
        rows.sort(key=lambda r: (not r["online"], r["name"].lower()))
        return {"agents": rows, "count": len(rows), "onlineCount": sum(1 for r in rows if r["online"])}

    # -- mailbox -------------------------------------------------------------

    def post_message(
        self,
        sender: str,
        body: str,
        to: str = None,
        subject: str = "",
        kind: str = "note",
        priority: str = "normal",
        thread_id: str = None,
        meta: dict = None,
    ) -> dict:
        sender = (sender or "").strip()
        if not sender:
            raise ValueError("sender is required")
        if not (body or subject):
            raise ValueError("body or subject is required")
        if kind not in MESSAGE_KINDS:
            kind = "note"
        if priority not in PRIORITIES:
            priority = "normal"
        with self.lock:
            # Accept an agent name as well as an id; "all" / None means broadcast.
            to_id = None
            if to and to not in ("all", "*", "broadcast"):
                to_id = to
                if to_id not in self.agents:
                    match = next((a["id"] for a in self.agents.values() if a["name"].lower() == str(to).lower()), None)
                    to_id = match or to
            msg = {
                "id": "msg-" + uuid.uuid4().hex[:10],
                "seq": self._next_seq_unlocked(),
                "ts": _now(),
                "from": sender,
                "fromId": next((a["id"] for a in self.agents.values() if a["name"].lower() == sender.lower()), None),
                "to": to_id,
                "subject": _clean(subject, 300),
                "body": _clean(body),
                "kind": kind,
                "priority": priority,
                "threadId": thread_id,
                "meta": meta if isinstance(meta, dict) else {},
                "readBy": {},
            }
            self.messages.append(msg)
            self._emit_unlocked("message.post", {
                "id": msg["id"], "from": sender, "to": to_id or "broadcast",
                "subject": msg["subject"], "kind": kind, "priority": priority,
            })
            self._save()
            return msg

    def inbox(
        self,
        agent_id: str,
        since: int = 0,
        limit: int = 50,
        unread_only: bool = False,
        include_broadcast: bool = True,
    ) -> dict:
        limit = max(1, min(int(limit or 50), 500))
        with self.lock:
            agent = self.agents.get(agent_id)
            names = {agent_id}
            if agent:
                names.add(agent["name"].lower())
            rows = []
            for msg in reversed(self.messages):
                if since and msg["seq"] <= since:
                    break
                target = msg.get("to")
                if target is None:
                    if not include_broadcast:
                        continue
                elif target not in names and str(target).lower() not in names:
                    continue
                if msg.get("from") == agent_id:
                    continue
                if unread_only and agent_id in (msg.get("readBy") or {}):
                    continue
                rows.append(msg)
                if len(rows) >= limit:
                    break
            rows.reverse()
            unread = sum(
                1 for m in self.messages
                if (m.get("to") is None or m.get("to") in names or str(m.get("to", "")).lower() in names)
                and agent_id not in (m.get("readBy") or {}) and m.get("from") != agent_id
            )
            return {
                "agentId": agent_id,
                "messages": rows,
                "count": len(rows),
                "unread": unread,
                "cursor": rows[-1]["seq"] if rows else since,
            }

    def read_thread(self, thread_id: str) -> dict:
        with self.lock:
            rows = [m for m in self.messages if m.get("threadId") == thread_id or m["id"] == thread_id]
        return {"threadId": thread_id, "messages": rows, "count": len(rows)}

    def mark_read(self, agent_id: str, message_ids=None, all_messages: bool = False) -> dict:
        now = _now()
        marked = 0
        with self.lock:
            for msg in self.messages:
                if not all_messages and message_ids and msg["id"] not in message_ids:
                    continue
                if all_messages or message_ids:
                    if agent_id not in (msg.get("readBy") or {}):
                        msg.setdefault("readBy", {})[agent_id] = now
                        marked += 1
            self._save()
        return {"agentId": agent_id, "marked": marked}

    # -- task board ----------------------------------------------------------

    def create_task(
        self,
        title: str,
        detail: str = "",
        created_by: str = "",
        assignee: str = None,
        priority: str = "normal",
        tags=None,
        depends_on=None,
        estimate_minutes: int = None,
    ) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("title is required")
        if priority not in PRIORITIES:
            priority = "normal"
        with self.lock:
            now = _now()
            task = {
                "id": "task-" + uuid.uuid4().hex[:8],
                "title": _clean(title, 300),
                "detail": _clean(detail),
                "createdBy": created_by or "unknown",
                "assignee": assignee,
                "status": "todo",
                "priority": priority,
                "tags": [str(t)[:40] for t in (tags or [])][:20],
                "dependsOn": [str(d) for d in (depends_on or [])][:20],
                "estimateMinutes": int(estimate_minutes) if estimate_minutes else None,
                "created": now,
                "updated": now,
                "lease": None,
                "result": None,
                "artifacts": [],
                "history": [{"ts": now, "actor": created_by or "unknown", "action": "created"}],
            }
            self.tasks[task["id"]] = task
            self._emit_unlocked("task.created", {"taskId": task["id"], "title": task["title"], "priority": priority})
            self._save()
            return dict(task)

    def list_tasks(self, status: str = None, assignee: str = None, tag: str = None, include_done: bool = True) -> dict:
        with self.lock:
            self._expire_leases_unlocked()
            rows = list(self.tasks.values())
        if status:
            wanted = {s.strip() for s in status.split(",") if s.strip()}
            rows = [t for t in rows if t["status"] in wanted]
        if not include_done:
            rows = [t for t in rows if t["status"] not in ("done", "cancelled")]
        if assignee:
            rows = [t for t in rows if t.get("assignee") == assignee or t.get("createdBy") == assignee]
        if tag:
            rows = [t for t in rows if tag in (t.get("tags") or [])]
        rows.sort(key=lambda t: (
            {"in_progress": 0, "blocked": 1, "todo": 2, "done": 3, "cancelled": 4}.get(t["status"], 5),
            _PRIORITY_RANK.get(t.get("priority"), 2),
            t.get("created", 0),
        ))
        now = _now()
        out = []
        for t in rows:
            row = dict(t)
            lease = t.get("lease")
            row["leaseRemainingSeconds"] = round(lease["expiresAt"] - now, 1) if lease and lease.get("expiresAt") else None
            row["blockedBy"] = [d for d in (t.get("dependsOn") or []) if self.tasks.get(d, {}).get("status") != "done"]
            out.append(row)
        return {
            "tasks": out,
            "count": len(out),
            "counts": {
                s: sum(1 for t in self.tasks.values() if t["status"] == s) for s in TASK_STATUSES
            },
            "serverTime": now,
        }

    def _resolve_agent_unlocked(self, agent_id: str) -> str:
        if agent_id in self.agents:
            return agent_id
        match = next((a["id"] for a in self.agents.values() if a["name"].lower() == str(agent_id).lower()), None)
        if not match:
            raise KeyError(f"Unknown agent: {agent_id!r} — register first via action=register")
        return match

    def claim_task(self, task_id: str, agent_id: str, lease_seconds: int = None) -> dict:
        with self.lock:
            self._expire_leases_unlocked()
            task = self.tasks.get(task_id)
            if not task:
                raise KeyError(f"Unknown task id: {task_id!r}")
            agent_id = self._resolve_agent_unlocked(agent_id)
            if task["status"] in ("done", "cancelled"):
                raise ValueError(f"Task {task_id} is already {task['status']}")
            blocked = [d for d in (task.get("dependsOn") or []) if self.tasks.get(d, {}).get("status") != "done"]
            if blocked:
                raise ValueError(f"Task {task_id} is blocked by incomplete dependencies: {', '.join(blocked)}")
            current = task.get("assignee")
            if current and current != agent_id and task.get("lease"):
                raise ValueError(f"Task {task_id} is already claimed by {current} until the lease expires")
            now = _now()
            lease_s = int(lease_seconds or self.default_lease_seconds)
            task.update({
                "assignee": agent_id,
                "status": "in_progress",
                "updated": now,
                "lease": {"owner": agent_id, "claimedAt": now, "expiresAt": now + lease_s, "seconds": lease_s},
            })
            task.setdefault("history", []).append({"ts": now, "actor": agent_id, "action": "claimed"})
            self._emit_unlocked("task.claimed", {"taskId": task_id, "agentId": agent_id, "leaseSeconds": lease_s})
            self._save()
            return dict(task)

    def renew_task(self, task_id: str, agent_id: str, lease_seconds: int = None) -> dict:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise KeyError(f"Unknown task id: {task_id!r}")
            lease = task.get("lease") or {}
            if lease.get("owner") != agent_id:
                raise ValueError("Only the current lease owner can renew this task")
            lease_s = int(lease_seconds or lease.get("seconds") or self.default_lease_seconds)
            lease["expiresAt"] = _now() + lease_s
            task["lease"] = lease
            task["updated"] = _now()
            self._save()
            return dict(task)

    def release_task(self, task_id: str, agent_id: str, note: str = "") -> dict:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise KeyError(f"Unknown task id: {task_id!r}")
            task.update({"assignee": None, "status": "todo", "lease": None, "updated": _now()})
            task.setdefault("history", []).append({"ts": _now(), "actor": agent_id, "action": "released", "note": note})
            self._emit_unlocked("task.released", {"taskId": task_id, "agentId": agent_id})
            self._save()
            return dict(task)

    def update_task(self, task_id: str, actor: str = "unknown", **changes) -> dict:
        allowed = {"status", "priority", "assignee", "detail", "tags", "dependsOn", "title"}
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise KeyError(f"Unknown task id: {task_id!r}")
            if changes.get("status") and changes["status"] not in TASK_STATUSES:
                raise ValueError(f"status must be one of {', '.join(TASK_STATUSES)}")
            if changes.get("priority") and changes["priority"] not in PRIORITIES:
                raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")
            for key, value in changes.items():
                if key in allowed and value is not None:
                    if key in ("detail", "title"):
                        value = _clean(value, 300 if key == "title" else 8000)
                    task[key] = value
            task["updated"] = _now()
            task.setdefault("history", []).append({"ts": _now(), "actor": actor, "action": "updated", "changes": sorted(changes)})
            self._emit_unlocked("task.updated", {"taskId": task_id, "actor": actor, "changes": sorted(changes)})
            self._save()
            return dict(task)

    def complete_task(self, task_id: str, agent_id: str, result: str = "", artifacts=None, status: str = "done") -> dict:
        if status not in ("done", "cancelled"):
            raise ValueError("complete status must be 'done' or 'cancelled'")
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise KeyError(f"Unknown task id: {task_id!r}")
            lease_owner = (task.get("lease") or {}).get("owner")
            if lease_owner and lease_owner != agent_id and task.get("assignee") not in (None, agent_id):
                raise ValueError(f"Task {task_id} is leased to {lease_owner}")
            now = _now()
            task.update({
                "status": status,
                "assignee": task.get("assignee") or agent_id,
                "result": _clean(result),
                "artifacts": [str(a)[:500] for a in (artifacts or [])][:50],
                "lease": None,
                "updated": now,
                "completedAt": now,
            })
            task.setdefault("history", []).append({"ts": now, "actor": agent_id, "action": status})
            self._emit_unlocked(f"task.{status}", {"taskId": task_id, "agentId": agent_id, "title": task["title"]})
            self._save()
            return dict(task)

    def delete_task(self, task_id: str, actor: str = "unknown") -> dict:
        with self.lock:
            task = self.tasks.pop(task_id, None)
            if not task:
                raise KeyError(f"Unknown task id: {task_id!r}")
            self._emit_unlocked("task.deleted", {"taskId": task_id, "actor": actor})
            self._save()
            return {"deleted": task_id}

    # -- feed / export -------------------------------------------------------

    def feed(self, since: int = 0, limit: int = 100) -> dict:
        limit = max(1, min(int(limit or 100), 500))
        with self.lock:
            rows = [e for e in self.events if e["seq"] > since][-limit:]
            seq = self.seq
        return {"events": rows, "count": len(rows), "cursor": seq}

    def sweep_stale(self, offline_seconds: float = 900, now: float = None) -> dict:
        """Release tasks held by agents that stopped reporting.

        A crashed agent keeps its lease until it expires (15 minutes by default), which
        makes the board look busy while nothing moves. This sweep is the escape hatch:
        it only touches tasks whose owner has been silent for `offline_seconds`, never
        tasks held by an agent that is still heart-beating.
        """
        offline_seconds = float(offline_seconds or 900)
        now = float(now if now is not None else _now())
        released = []
        stale_agents = []
        with self.lock:
            self._expire_leases_unlocked()
            for agent in self.agents.values():
                last_seen = float(agent.get("lastSeen") or 0)
                if (now - last_seen) >= offline_seconds:
                    stale_agents.append(agent)
            stale_ids = {a["id"] for a in stale_agents}
            if not stale_agents:
                self.last_sweep_at = now
                self._save()
                return {"released": [], "agents": [], "agentsReaped": 0, "tasksReaped": 0,
                        "alerted": False, "sweptAt": now}

            for task in self.tasks.values():
                if task.get("status") != "in_progress":
                    continue
                owner = (task.get("lease") or {}).get("owner") or task.get("assignee")
                if owner not in stale_ids:
                    continue
                task.update({"status": "todo", "assignee": None, "lease": None, "updated": now})
                task["leaseExpiries"] = int(task.get("leaseExpiries", 0)) + 1
                task.setdefault("history", []).append(
                    {"ts": now, "actor": "sweeper", "action": "reaped", "previousAssignee": owner}
                )
                released.append({"taskId": task["id"], "title": task.get("title"), "previousAssignee": owner})

            self.agents_reaped += len({r["previousAssignee"] for r in released})
            self.tasks_reaped += len(released)
            self.last_sweep_at = now
            if released:
                names = sorted({a["name"] for a in stale_agents})
                listing = ", ".join(f"{r['taskId']} ({r.get('title') or 'untitled'})" for r in released)
                self.post_message(
                    sender="sweeper",
                    subject="Stale agent tasks released",
                    body=f"{len(released)} task(s) released from offline agents "
                         f"({', '.join(names)}): {listing}",
                    to=None,
                    kind="alert",
                    priority="high",
                )
            self._emit_unlocked("bus.sweep", {
                "released": len(released), "agents": len(stale_ids), "offlineSeconds": offline_seconds,
            })
            self._save()

        return {"released": released, "agents": sorted(a["name"] for a in stale_agents),
                "agentsReaped": len(stale_ids), "tasksReaped": len(released),
                "alerted": bool(released), "sweptAt": now}

    def export_handoff(self, path: str = None, include_done: bool = True,
                       since: int = 0, summary_only: bool = False) -> dict:
        """Render a markdown brief that any next agent (or human) can pick up."""
        with self.lock:
            agents = [self._agent_view_unlocked(a) for a in self.agents.values()]
            board = self.list_tasks(include_done=include_done)
            since = int(since or 0)
            messages = [m for m in self.messages if int(m.get("seq") or 0) > since][-25:]
            if since > 0:
                # Cursor -> wall clock: the newest timestamp we handed out at that seq point.
                cutoff = 0.0
                for message in self.messages:
                    if int(message.get("seq") or 0) <= since:
                        cutoff = max(cutoff, float(message.get("ts") or 0))
                for event in self.events:
                    if int(event.get("seq") or 0) <= since:
                        cutoff = max(cutoff, float(event.get("ts") or 0))
                open_tasks = [t for t in board["tasks"] if t.get("status") in ("todo", "in_progress", "blocked")]
                recent = [t for t in board["tasks"] if float(t.get("updated") or 0) > cutoff]
                merged = {t["id"]: t for t in open_tasks}
                merged.update({t["id"]: t for t in recent})
                board = {"tasks": list(merged.values()), "count": len(merged)}
            stats = self.stats()

        lines = [
            "# Agent handoff brief",
            "",
            f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}  ·  bus v{BUS_VERSION}"
            + (f"  ·  incremental since seq {since}" if since else ""),
            "",
            "## Roster",
            "",
        ]
        if agents:
            lines += ["| Agent | Role | Status | Open tasks | Capabilities |", "|---|---|---|---|---|"]
            for a in agents:
                lines.append(
                    f"| {a['name']} (`{a['id']}`) | {a['role']} | {a['status']} | {a['openTasks']} | "
                    f"{', '.join(a.get('capabilities') or []) or '—'} |"
                )
        else:
            lines.append("_No agents registered yet._")

        lines += ["", "## Task board", ""]
        if board["tasks"]:
            for t in board["tasks"]:
                mark = {"done": "x"}.get(t["status"], " ")
                owner = f" — @{t['assignee']}" if t.get("assignee") else ""
                lines.append(f"- [{mark}] **{t['title']}** ({t['status']}, {t['priority']}){owner}  ")
                if summary_only:
                    continue
                if t.get("detail"):
                    lines.append(f"  {t['detail']}")
                if t.get("result"):
                    lines.append(f"  _Result:_ {t['result']}")
                for artifact in (t.get("artifacts") or [])[:5]:
                    lines.append(f"  _Artifact:_ {artifact}")
        else:
            lines.append("_Task board is empty._")

        if summary_only:
            messages = []
        lines += ["", "## Recent messages", ""]
        if messages:
            for m in messages:
                target = m.get("to") or "broadcast"
                lines.append(
                    f"- **{m['from']} → {target}** [{m['kind']}/{m['priority']}] "
                    f"{m.get('subject') or '(no subject)'}: {m.get('body', '')[:400]}"
                )
        else:
            lines.append("_No messages._")

        lines += [
            "",
            "## Live bus stats",
            "",
            f"- agents: {stats['agents']} ({stats['agentsOnline']} online)",
            f"- tasks: {stats['tasks']} total · " + ", ".join(f"{k}={v}" for k, v in stats["taskCounts"].items()),
            f"- messages: {stats['messages']}",
            "",
            "Pick a task with `agent_bus {action:'task_claim', taskId:'…', agentId:'…'}`, "
            "or post status with `agent_bus {action:'post', …}`.",
            "",
        ]
        text = "\n".join(lines)
        target = None
        if path:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return {"markdown": text, "path": str(target) if target else None, "bytes": len(text.encode("utf-8"))}

    def stats(self) -> dict:
        with self.lock:
            self._expire_leases_unlocked()
            agents = [self._agent_view_unlocked(a) for a in self.agents.values()]
            return {
                "version": BUS_VERSION,
                "agents": len(agents),
                "agentsOnline": sum(1 for a in agents if a["online"]),
                "tasks": len(self.tasks),
                "taskCounts": {s: sum(1 for t in self.tasks.values() if t["status"] == s) for s in TASK_STATUSES},
                "messages": len(self.messages),
                "events": len(self.events),
                "cursor": self.seq,
                "unreadTotal": sum(a["inboxUnread"] for a in agents),
                "agentsReaped": self.agents_reaped,
                "tasksReaped": self.tasks_reaped,
                "lastSweepAt": self.last_sweep_at,
                "serverTime": _now(),
            }

    # -- single entry point used by the HTTP + MCP layers ---------------------

    def handle(self, payload: dict) -> dict:
        action = (payload or {}).get("action", "stats")
        p = payload or {}
        if action == "register":
            return self.register_agent(
                name=p.get("name") or p.get("agent") or "agent",
                role=p.get("role") or "agent",
                capabilities=p.get("capabilities"),
                agent_id=p.get("agentId"),
                ttl=p.get("ttl"),
                meta=p.get("meta"),
            )
        if action == "heartbeat":
            return self.heartbeat(p.get("agentId") or p.get("agent"), p.get("status"))
        if action == "status":
            if p.get("agentId") and p.get("status"):
                return self.set_status(p["agentId"], p["status"])
            return self.stats()
        if action in ("agents", "list_agents"):
            return self.list_agents(bool(p.get("includeOffline", True)))
        if action in ("post", "send"):
            return self.post_message(
                sender=p.get("agentId") or p.get("from") or p.get("agent"),
                body=p.get("body") or p.get("message"),
                to=p.get("to"),
                subject=p.get("subject"),
                kind=p.get("kind") or "note",
                priority=p.get("priority") or "normal",
                thread_id=p.get("threadId"),
                meta=p.get("meta"),
            )
        if action == "inbox":
            return self.inbox(
                p.get("agentId") or p.get("agent"),
                since=int(p.get("since") or 0),
                limit=int(p.get("limit") or 50),
                unread_only=bool(p.get("unreadOnly")),
                include_broadcast=p.get("includeBroadcast", True),
            )
        if action == "thread":
            return self.read_thread(p.get("threadId") or p.get("id"))
        if action == "mark_read":
            return self.mark_read(
                p.get("agentId") or p.get("agent"),
                p.get("messageIds"),
                bool(p.get("all")),
            )
        if action in ("tasks", "list_tasks"):
            return self.list_tasks(
                p.get("status"), p.get("assignee"), p.get("tag"),
                include_done=p.get("includeDone", True),
            )
        if action == "task_create":
            return self.create_task(
                title=p.get("title"),
                detail=p.get("detail"),
                created_by=p.get("agentId") or p.get("createdBy") or "unknown",
                assignee=p.get("assignee"),
                priority=p.get("priority") or "normal",
                tags=p.get("tags"),
                depends_on=p.get("dependsOn"),
                estimate_minutes=p.get("estimateMinutes"),
            )
        if action == "task_claim":
            return self.claim_task(p.get("taskId") or p.get("id"), p.get("agentId") or p.get("agent"), p.get("leaseSeconds"))
        if action == "task_renew":
            return self.renew_task(p.get("taskId") or p.get("id"), p.get("agentId") or p.get("agent"), p.get("leaseSeconds"))
        if action == "task_release":
            return self.release_task(p.get("taskId") or p.get("id"), p.get("agentId") or p.get("agent"), p.get("note") or "")
        if action == "task_update":
            return self.update_task(
                p.get("taskId") or p.get("id"),
                actor=p.get("agentId") or p.get("actor") or "unknown",
                **{k: p.get(k) for k in ("status", "priority", "assignee", "detail", "tags", "dependsOn", "title") if k in p},
            )
        if action == "task_complete":
            return self.complete_task(
                p.get("taskId") or p.get("id"),
                p.get("agentId") or p.get("agent"),
                result=p.get("result") or "",
                artifacts=p.get("artifacts"),
                status=p.get("status") or "done",
            )
        if action == "task_delete":
            return self.delete_task(p.get("taskId") or p.get("id"), p.get("actor") or "unknown")
        if action in ("feed", "events"):
            return self.feed(int(p.get("since") or 0), int(p.get("limit") or 100))
        if action in ("export", "handoff"):
            return self.export_handoff(
                p.get("path"),
                bool(p.get("includeDone", True)),
                since=int(p.get("since") or 0),
                summary_only=bool(p.get("summaryOnly")),
            )
        if action in ("sweep", "sweep_stale"):
            return self.sweep_stale(p.get("offlineSeconds") or 900)
        if action == "stats":
            return self.stats()
        raise ValueError(
            "Unknown agent_bus action. Use one of: register, heartbeat, status, agents, post, inbox, "
            "thread, mark_read, tasks, task_create, task_claim, task_renew, task_release, task_update, "
            "task_complete, task_delete, feed, export, sweep, stats."
        )
