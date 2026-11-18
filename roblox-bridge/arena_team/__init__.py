"""Arena Team — multi-agent orchestration layer for the Roblox Studio bridge.

The package deliberately contains no Studio-specific tool implementations: agents
execute the bridge's existing, annotated MCP tools through ``mcp_core.execute_tool``.
It adds coordination only: a configurable roster, persistent project context,
a planner, a policy-enforcing tool runner, human approvals, and a scheduler that
uses the existing Agent Bus leases and task board.

Public entry point: :class:`arena_team.orchestrator.Orchestrator`.
"""

from .orchestrator import Orchestrator  # noqa: F401

__all__ = ["Orchestrator"]
__version__ = "1.0.0"
