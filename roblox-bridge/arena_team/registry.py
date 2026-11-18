"""Agent registry: bus presence + capability/permission/ownership enforcement.

Presence is *not* duplicated: ``AgentBus`` remains the single roster of record.
This module registers each configured agent there, keeps its heartbeat alive,
and exposes a browser-safe view that merges config with live bus state.

Registration is lazy: merely constructing the bridge or reading an idle team
status must not add seats to an operator's existing Agent Bus. The orchestrator
calls :meth:`ensure_registered` before the first project/write action.
"""

import os
import threading
import time

from .config import TeamConfig, load_config


def _now() -> float:
    return time.time()


class AgentRegistry:
    def __init__(self, bus, config: TeamConfig = None, tool_map: dict = None, emit=None):
        self.bus = bus
        self.config = config or load_config()
        self.tool_map = tool_map or {}
        self.emit = emit or (lambda kind, detail, level="info": None)
        self.lock = threading.RLock()
        self.agent_ids = {}          # agentKey -> bus agentId
        self.last_heartbeat = {}     # agentKey -> epoch
        self.activity = {}           # agentKey -> {status, taskKey, detail, since}
        self.driver_errors = {}      # agentKey -> reason
        self.registered = False      # Bus seats are created on first team use
        for spec in self.config.agents.values():
            if not spec.enabled:
                self.driver_errors[spec.key] = "disabled"
            elif not self.driver_ready(spec):
                self.driver_errors[spec.key] = (
                    "awaiting_human" if spec.driver.get("type") == "human" else "driver_not_configured"
                )

    # -- configuration -------------------------------------------------------

    def reload(self, config: TeamConfig = None):
        with self.lock:
            self.config = config or load_config()
            self.agent_ids = {}
            self.activity = {}
            self.driver_errors = {}
            self.registered = False
            return self.sync()

    def ensure_registered(self) -> dict:
        """Create/refresh Bus seats when the team is actually used."""
        with self.lock:
            if self.registered:
                return {"registered": [{"key": k, "agentId": v}
                                       for k, v in self.agent_ids.items()],
                        "unknownTools": self.unknown_tools()}
            return self.sync()

    def unknown_tools(self) -> list:
        """Report config entries that are not real MCP tools (none may be invented)."""
        return sorted({
            f"{spec.key}:{tool}"
            for spec in self.config.agents.values()
            for tool in spec.allowed_tools
            if tool not in self.tool_map
        })

    def ownership_conflicts(self) -> list:
        """Detect two agents whose exclusive Studio namespaces overlap."""
        paths = [(spec.key, p) for spec in self.config.agents.values() for p in spec.owned_paths]
        conflicts = []
        for i, (key_a, path_a) in enumerate(paths):
            for key_b, path_b in paths[i + 1:]:
                if key_a == key_b:
                    continue
                if path_a == path_b or path_a.startswith(path_b + ".") or path_b.startswith(path_a + "."):
                    conflicts.append({"agents": sorted([key_a, key_b]), "paths": [path_a, path_b]})
        return conflicts

    # -- presence -------------------------------------------------------------

    def driver_ready(self, spec) -> bool:
        kind = spec.driver.get("type")
        if kind == "builtin":
            return True
        if kind == "human":
            return False  # a human claims work through the Bus/UI; never auto-online
        env_name = str(spec.driver.get("endpointEnv") or "")
        return bool(env_name and os.environ.get(env_name, "").strip())

    def sync(self) -> dict:
        """(Re-)register every enabled agent into the existing Agent Bus."""
        with self.lock:
            result = []
            for spec in self.config.agents.values():
                if not spec.enabled:
                    self.driver_errors[spec.key] = "disabled"
                    continue
                row = self.bus.register_agent(
                    name=spec.name,
                    role=spec.role,
                    capabilities=spec.capabilities,
                    meta={
                        "agentKey": spec.key,
                        "permission": spec.permission,
                        "driverType": spec.driver.get("type"),
                        "ownedPaths": list(spec.owned_paths),
                        "layer": "arena_team",
                    },
                )
                self.agent_ids[spec.key] = row["id"]
                self.last_heartbeat[spec.key] = _now()
                if self.driver_ready(spec):
                    self.bus.set_status(row["id"], "idle")
                    self.activity[spec.key] = {"status": "idle", "taskKey": None, "detail": "ready", "since": _now()}
                else:
                    # Explicitly mark offline-capable agents: no heartbeat means the
                    # Bus will age them out after its presence TTL.
                    self.driver_errors[spec.key] = (
                        "awaiting_human" if spec.driver.get("type") == "human" else "driver_not_configured"
                    )
                    self.activity[spec.key] = {
                        "status": "offline", "taskKey": None,
                        "detail": self.driver_errors[spec.key], "since": _now(),
                    }
                result.append({"key": spec.key, "agentId": row["id"]})
            self.registered = True
            return {"registered": result, "unknownTools": self.unknown_tools()}

    def heartbeat_active(self):
        """Keep ready agents online; this is what lease recovery keys off."""
        with self.lock:
            for key, agent_id in list(self.agent_ids.items()):
                spec = self.config.agents.get(key)
                if not spec or not self.driver_ready(spec):
                    continue
                try:
                    activity = self.activity.get(key) or {}
                    status = "busy" if activity.get("status") == "working" else "idle"
                    self.bus.heartbeat(agent_id, status)
                    self.last_heartbeat[key] = _now()
                except KeyError:
                    # Bus state was reset underneath us; re-register on the next sync.
                    self.sync()
                    return

    def set_activity(self, key: str, status: str, task_key: str = None, detail: str = ""):
        with self.lock:
            self.activity[key] = {"status": status, "taskKey": task_key, "detail": detail[:300], "since": _now()}
            agent_id = self.agent_ids.get(key)
            if agent_id and status in ("working", "idle", "busy", "offline"):
                try:
                    self.bus.set_status(agent_id, "busy" if status == "working" else "idle")
                except KeyError:
                    pass

    def bus_id(self, key: str) -> str:
        with self.lock:
            return self.agent_ids.get(key, "")

    def spec(self, key: str):
        return self.config.agents.get(key)

    def owner_for_path(self, path: str) -> str:
        """Which agent exclusively owns a Studio dot-path (longest prefix wins)."""
        best_key, best_len = "", -1
        for spec in self.config.agents.values():
            for owned in spec.owned_paths:
                if (path == owned or path.startswith(owned + ".")) and len(owned) > best_len:
                    best_key, best_len = spec.key, len(owned)
        return best_key

    def roster(self) -> list:
        """Merged config + live Bus presence, safe for the browser."""
        with self.lock:
            try:
                bus_rows = {a["id"]: a for a in self.bus.list_agents()["agents"]}
            except Exception:
                bus_rows = {}
            rows = []
            for spec in self.config.agents.values():
                agent_id = self.agent_ids.get(spec.key)
                live = bus_rows.get(agent_id, {}) if agent_id else {}
                ready = spec.enabled and self.driver_ready(spec)
                online = bool(ready and live.get("online"))
                activity = dict(self.activity.get(spec.key) or {})
                row = spec.public({
                    "agentId": agent_id or None,
                    "online": online,
                    "presence": live.get("status") if online else "offline",
                    "presenceStatus": live.get("status"),
                    "ageSeconds": live.get("ageSeconds"),
                    "openTasks": live.get("openTasks", 0),
                    "inboxUnread": live.get("inboxUnread", 0),
                    "activity": activity,
                    "driverReady": ready,
                    "driverReason": None if ready else self.driver_errors.get(spec.key),
                })
                rows.append(row)
            rows.sort(key=lambda r: (not r["online"], r["role"]))
            return rows
