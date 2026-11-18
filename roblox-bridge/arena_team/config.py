"""Team configuration loading, validation and secret redaction.

Agents are *independently configurable, replaceable and extensible*: this module
owns nothing but a JSON description of the roster. ``ARENA_TEAM_CONFIG`` may point
at a project-specific file; POST /api/orchestrator {action:'configure'} reloads it
at runtime. Driver credentials are always environment-variable *names*, never
values, so no secret is ever persisted or returned to a browser/agent.
"""

import copy
import json
import os
import re
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "agents.json"
CONFIG_ENV = "ARENA_TEAM_CONFIG"

PERMISSIONS = ("read", "write", "admin")
APPROVAL_MODES = ("plan", "each_write", "off")
DRIVER_TYPES = ("builtin", "external", "human")

#: Keys whose values must never appear in status payloads, approval previews,
#: audit details, or anything handed to an external model.
SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|password|passwd|api[-_ ]?key|authorization|bearer|credential|cookie|private[-_ ]?key)",
    re.IGNORECASE,
)


def redact(value, extra_secrets=()):
    """Deep-copy ``value`` with credential-shaped keys and live secret values removed."""
    secrets = [str(s) for s in extra_secrets if s and len(str(s)) >= 4]
    def walk(node):
        if isinstance(node, dict):
            out = {}
            for key, val in node.items():
                if SENSITIVE_KEY_RE.search(str(key)):
                    out[key] = "[redacted]"
                else:
                    out[key] = walk(val)
            return out
        if isinstance(node, (list, tuple)):
            return [walk(v) for v in node]
        if isinstance(node, str):
            text = node
            for secret in secrets:
                text = text.replace(secret, "[redacted]")
            # Generic bearer / long random credential shapes.
            text = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}", "Bearer [redacted]", text)
            return text
        return node
    return walk(copy.deepcopy(value))


class TeamConfigError(ValueError):
    pass


class AgentSpec:
    """One validated, immutable-by-convention agent description."""

    def __init__(self, raw: dict):
        if not isinstance(raw, dict):
            raise TeamConfigError("Each agent must be a JSON object")
        self.key = str(raw.get("key") or "").strip()
        self.name = str(raw.get("name") or self.key).strip()
        self.role = str(raw.get("role") or "Agent").strip()
        self.description = str(raw.get("description") or "").strip()
        self.capabilities = [str(c) for c in (raw.get("capabilities") or [])][:32]
        self.permission = str(raw.get("permission") or "read").strip().lower()
        self.owned_paths = [str(p).strip().strip(".") for p in (raw.get("ownedPaths") or []) if str(p).strip()]
        self.allowed_tools = sorted({str(t) for t in (raw.get("allowedTools") or [])})
        self.driver = dict(raw.get("driver") or {"type": "builtin"})
        self.enabled = bool(raw.get("enabled", True))
        self.review_required = bool(raw.get("reviewRequired", False))
        self.max_concurrent = max(1, min(int(raw.get("maxConcurrent") or 1), 16))
        self.color = str(raw.get("color") or "#8fa0b4")
        self._validate()

    def _validate(self):
        if not re.fullmatch(r"[a-z0-9][a-z0-9._\-]{0,48}", self.key):
            raise TeamConfigError(f"Invalid agent key: {self.key!r}")
        if not self.name:
            raise TeamConfigError(f"Agent {self.key!r} needs a name")
        if self.permission not in PERMISSIONS:
            raise TeamConfigError(f"Agent {self.key!r}: permission must be one of {PERMISSIONS}")
        if self.driver.get("type") not in DRIVER_TYPES:
            raise TeamConfigError(f"Agent {self.key!r}: unknown driver type {self.driver.get('type')!r}")
        if self.driver.get("type") == "builtin" and not self.driver.get("playbook"):
            raise TeamConfigError(f"Agent {self.key!r}: builtin driver needs a playbook")
        if self.driver.get("type") == "external" and not self.driver.get("endpointEnv"):
            raise TeamConfigError(f"Agent {self.key!r}: external driver needs endpointEnv")
        if not self.allowed_tools:
            raise TeamConfigError(f"Agent {self.key!r}: allowedTools must not be empty")
        for path in self.owned_paths:
            if not re.fullmatch(r"[A-Za-z0-9_.\-/ ]{1,160}", path):
                raise TeamConfigError(f"Agent {self.key!r}: invalid ownedPath {path!r}")

    def public(self, extras: dict = None) -> dict:
        """The browser/agent-safe view of this spec (never contains credentials)."""
        row = {
            "key": self.key,
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "permission": self.permission,
            "ownedPaths": list(self.owned_paths),
            "allowedTools": list(self.allowed_tools),
            "driver": {
                "type": self.driver.get("type"),
                "playbook": self.driver.get("playbook"),
                "model": self.driver.get("model"),
                "endpointEnv": self.driver.get("endpointEnv"),
            },
            "enabled": self.enabled,
            "reviewRequired": self.review_required,
            "maxConcurrent": self.max_concurrent,
            "color": self.color,
        }
        if isinstance(extras, dict):
            row.update(extras)
        return redact(row)


class TeamConfig:
    def __init__(self, raw: dict, source: str = ""):
        if not isinstance(raw, dict):
            raise TeamConfigError("Team config must be a JSON object")
        self.source = source
        self.version = int(raw.get("version") or 1)
        self.description = str(raw.get("description") or "")
        agents = raw.get("agents")
        if not isinstance(agents, list) or not agents:
            raise TeamConfigError("Team config needs a non-empty 'agents' list")
        self.agents = {}
        for item in agents:
            spec = AgentSpec(item)
            if spec.key in self.agents:
                raise TeamConfigError(f"Duplicate agent key: {spec.key}")
            self.agents[spec.key] = spec
        settings = dict(raw.get("settings") or {})
        self.settings = {
            "heartbeatSeconds": max(5, float(settings.get("heartbeatSeconds") or 20)),
            "leaseSeconds": max(30, int(settings.get("leaseSeconds") or 420)),
            "maxConcurrentTasks": max(1, min(int(settings.get("maxConcurrentTasks") or 3), 16)),
            "defaultApprovalMode": str(settings.get("defaultApprovalMode") or "plan"),
            "approvalWaitSeconds": max(5, float(settings.get("approvalWaitSeconds") or 180)),
            "maxRepairAttempts": max(0, min(int(settings.get("maxRepairAttempts") or 1), 5)),
            "evidenceDir": str(settings.get("evidenceDir") or "arena_evidence"),
            "requireStudioForWriteTasks": bool(settings.get("requireStudioForWriteTasks", True)),
        }
        if self.settings["defaultApprovalMode"] not in APPROVAL_MODES:
            raise TeamConfigError(f"defaultApprovalMode must be one of {APPROVAL_MODES}")

    def public(self) -> dict:
        return {
            "version": self.version,
            "description": self.description,
            "source": self.source,
            "settings": dict(self.settings),
            "agents": [spec.public() for spec in self.agents.values()],
        }


def load_config(path=None) -> TeamConfig:
    chosen = Path(path or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG_PATH).expanduser()
    try:
        raw = json.loads(chosen.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TeamConfigError(f"Team config not found: {chosen}") from exc
    except (ValueError, OSError) as exc:
        raise TeamConfigError(f"Cannot read team config {chosen}: {exc}") from exc
    return TeamConfig(raw, source=str(chosen))
