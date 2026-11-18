"""Technical design + task DAG generation.

The default planner is a deterministic, inspectable generator: it reads the
*actual* place survey produced by the Project Manager and emits a design with
explicit component ownership, dependencies and verification steps. It does not
pretend to be a language model. A project can opt into an external planning
endpoint (``ARENA_PLANNER_ENDPOINT``) whose JSON plan is validated against the
same contract; an unconfigured endpoint never produces a fake "AI" plan.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


def _now() -> float:
    return time.time()


KEYWORDS = {
    "world": ("world", "map", "terrain", "environment", "obby", "level", "scene", "landscape", "build"),
    "ui": ("ui", "hud", "interface", "menu", "screen", "button", "shop", "inventory", "layout"),
    "luau": ("luau", "script", "module", "code", "system", "refactor", "framework", "api"),
    "gameplay": ("gameplay", "mechanic", "npc", "combat", "collect", "coin", "progression", "quest", "interaction", "player"),
}


def _hits(objective: str) -> list:
    text = str(objective or "").lower()
    return [name for name, words in KEYWORDS.items() if any(word in text for word in words)]


def _survey_numbers(analysis: dict) -> dict:
    survey = (analysis or {}).get("survey") or {}
    totals = survey.get("totals") or {}
    state = (analysis or {}).get("studioState") or {}
    peers = (analysis or {}).get("peers") or {}
    studio = state.get("studio") or {}
    place_info = studio.get("place") if isinstance(studio.get("place"), dict) else {}
    place_name = (studio.get("name") or place_info.get("name")
                  or (studio.get("place") if isinstance(studio.get("place"), str) else "")
                  or (analysis or {}).get("place") or "unknown")
    return {
        "place": place_name,
        "placeId": studio.get("placeId") or place_info.get("placeId") or (analysis or {}).get("placeId") or 0,
        "studioConnected": bool(state.get("studioConnected")),
        "scripts": int(totals.get("scripts") or 0),
        "instances": int(totals.get("instances") or totals.get("total") or 0),
        "remotes": int(totals.get("remotes") or 0),
        "peerCount": int(peers.get("onlineCount") or 0),
        "activeDataModel": (analysis or {}).get("activeDataModel") or "edit",
        "selectionCount": ((analysis or {}).get("selection") or {}).get("count") or 0,
    }


def build_design(objective: str, analysis: dict, roster: list, peer_id: str = None) -> dict:
    facts = _survey_numbers(analysis)
    enabled = {a["key"]: a for a in roster if a["enabled"] and a["driverReady"]}
    hits = _hits(objective) or list(KEYWORDS)
    namespaces = {key: spec["ownedPaths"] for key, spec in enabled.items() if spec["ownedPaths"]}
    components = [
        {
            "name": "World / environment",
            "owner": "world-builder",
            "paths": (enabled.get("world-builder", {}).get("ownedPaths") or []),
            "description": "Mesh-first licensed kit assets, procedural layout, anchored geometry and a rendered viewport inspection.",
            "tools": ["search_assets", "mesh_capabilities", "spawn_kit_mesh", "audit_3d_scene", "capture_viewport"],
        },
        {
            "name": "Luau systems",
            "owner": "luau-engineer",
            "paths": (enabled.get("luau-engineer", {}).get("ownedPaths") or []),
            "description": "Validated Luau ModuleScripts and a server bootstrap, pre-flighted with luau_check and written to the Edit DataModel.",
            "tools": ["luau_check", "write_script", "script_read", "build_tree"],
        },
        {
            "name": "Interface",
            "owner": "ui-ux-designer",
            "paths": (enabled.get("ui-ux-designer", {}).get("ownedPaths") or []),
            "description": "Responsive ScreenGui built from the existing UI pipeline, with desktop/tablet/mobile audits and icons.",
            "tools": ["search_cloud_icons", "import_ui_from_web", "audit_ui_layout", "screenshot_gui"],
        },
        {
            "name": "Gameplay",
            "owner": "gameplay-engineer",
            "paths": (enabled.get("gameplay-engineer", {}).get("ownedPaths") or []),
            "description": "Server-authoritative gameplay scripts from the reusable kit; client never grants its own rewards.",
            "tools": ["build_gameplay_mechanic", "luau_check", "write_script", "run_playtest_scenario"],
        },
    ]
    components = [c for c in components if c["owner"] in enabled]
    return {
        "title": f"Technical design — {str(objective)[:90]}",
        "objective": objective,
        "generatedAt": _now(),
        "generatedBy": "arena_team.deterministic-v1",
        "objectiveFocus": hits,
        "currentPlace": facts,
        "architecture": {
            "persistentDataModel": "edit",
            "playtestDataModels": ["server", "client"],
            "targetPeerId": peer_id,
            "routing": "pinned peerId when available, otherwise automatic edit-first",
            "exclusiveNamespaces": namespaces,
            "clientServer": "server-authoritative state; clients send intent through validated RemoteEvents only",
            "cachePolicy": "replaceInstance=true on ModuleScript updates; require_fresh for isolated tests",
        },
        "components": components,
        "verificationPlan": [
            "luau_check before every script write (existing bridge rule)",
            "script_read read-back after every write; expectedHash guards concurrent edits",
            "run_playtest_scenario + get_console_output(level=Error) after integration",
            "capture_viewport / screenshot_gui evidence captured from the Edit DataModel",
            "audit_network_security + perf_audit + audit_ui_layout review",
            "final report states verified=true only with successful read-backs, playtest and console inspection",
        ],
        "risks": [
            "No Studio peer: every Studio-dependent task must fail with no_peer, never report success",
            "Concurrent edits: exclusive path ownership, leases, revision and hash checks",
            "ModuleScript require cache: replaceInstance=true on updates",
        ],
    }


def _task(key, title, detail, agent_key, depends_on, paths, work, priority="normal",
          kind="implementation", review_required=False):
    return {
        "key": key, "title": title, "detail": detail, "agentKey": agent_key,
        "busTaskId": None, "paths": list(paths or []), "dependsOn": list(depends_on or []),
        "priority": priority, "kind": kind, "status": "todo", "attempts": 0,
        "result": None, "artifacts": [], "verification": {}, "work": dict(work or {}),
        "reviewRequired": bool(review_required), "baseRevision": None,
        "started": None, "completed": None, "error": None,
    }


def build_plan(objective: str, design: dict, roster: list, settings: dict) -> dict:
    """Return a DAG with real bus-compatible dependency keys and no unknown tools."""
    enabled = {a["key"]: a for a in roster if a["enabled"] and a["driverReady"]}
    hits = set(design.get("objectiveFocus") or [])
    # Always keep the full pipeline visible. Objectives with a narrower focus
    # still get the other components, but their detail says why they are baseline.
    def paths(key):
        return enabled.get(key, {}).get("ownedPaths") or []

    tasks = [
        _task("analysis", "Analyze existing Roblox place", "Read live Studio state, survey the place, inspect selection and playtest capabilities before any edit.",
              "project-manager", [], [], {"phase": "analysis"}, priority="critical", kind="analysis"),
        _task("design", "Produce technical design", "Derive the architecture, component ownership, dependencies and verification plan from the survey facts.",
              "project-manager", ["analysis"], [], {"phase": "design"}, priority="critical", kind="design"),
    ]
    spec_map = [
        ("world", "world-builder", "Build mesh-first environment",
         "Create an anchored, mesh-first environment from licensed kit assets, audit the 3D scene and capture viewport evidence."),
        ("luau", "luau-engineer", "Implement validated Luau modules",
         "Write reusable Luau modules and a server bootstrap, pre-flight with luau_check and verify by reading the source back."),
        ("ui", "ui-ux-designer", "Build responsive interface",
         "Create a responsive ScreenGui using existing icon packs and UI pipeline, then audit desktop, tablet and mobile layouts."),
        ("gameplay", "gameplay-engineer", "Implement server-authoritative gameplay",
         "Install a reusable gameplay system, validate every generated Luau source and write it into the owned server/shared namespaces."),
    ]
    for key, agent_key, title, detail in spec_map:
        if agent_key not in enabled:
            continue
        focus = "" if key in hits or not hits else " (baseline integration work — not the objective's primary focus)"
        tasks.append(_task(
            key, title, detail + focus, agent_key, ["design"], paths(agent_key),
            {"objective": objective, "focus": key in hits, "component": key},
            priority="high" if key in hits else "normal",
            review_required=bool(enabled[agent_key].get("reviewRequired")),
        ))
    impl_keys = [t["key"] for t in tasks if t["kind"] == "implementation"]
    tasks.append(_task("review", "Lead Developer integration review",
                       "Review every contribution: read back scripts, check consistent structure, capture a recovery snapshot and report integration issues.",
                       "lead-developer", impl_keys or ["design"], [], {"phase": "review"},
                       priority="high", kind="review"))
    tasks.append(_task("qa", "Playtest, console inspection and scene evidence",
                       "Run a playtest scenario, inspect Error-level console output, verify the written scripts exist and capture viewport evidence.",
                       "qa-engineer", ["review"], [], {"phase": "qa"}, priority="critical", kind="qa"))
    tasks.append(_task("security", "Security and performance audit",
                       "Audit RemoteEvents, server-side validation, expensive loops and UI layout regressions; report reproducible findings.",
                       "security-performance-engineer", ["review"], [], {"phase": "security"},
                       priority="high", kind="audit"))
    tasks.append(_task("report", "Produce verified development report",
                       "Compile task results, verification receipts, evidence and regressions into a report that never claims unverified success.",
                       "project-manager", ["qa", "security"], [], {"phase": "report"},
                       priority="critical", kind="report"))

    # Conflict detection: two tasks may not write the same namespace prefix.
    conflicts = []
    owned = []
    for task in tasks:
        for p in task["paths"]:
            owned.append((task["key"], task["agentKey"], p))
    for i, (key_a, agent_a, path_a) in enumerate(owned):
        for key_b, agent_b, path_b in owned[i + 1:]:
            if key_a == key_b:
                continue
            if path_a == path_b or path_a.startswith(path_b + ".") or path_b.startswith(path_a + "."):
                conflicts.append({"tasks": [key_a, key_b], "agents": [agent_a, agent_b], "paths": [path_a, path_b]})
    # Every declared path must be owned by the task's agent.
    for task in tasks:
        spec = enabled.get(task["agentKey"], {})
        allowed = tuple(spec.get("ownedPaths") or [])
        for path in task["paths"]:
            if allowed and not any(path == p or path.startswith(p + ".") for p in allowed):
                conflicts.append({"tasks": [task["key"]], "agents": [task["agentKey"]],
                                  "paths": [path], "reason": "path_not_owned"})

    return {
        "tasks": tasks,
        "conflicts": conflicts,
        "generatedAt": _now(),
        "generator": "arena_team.deterministic-v1",
        "settings": {"maxConcurrentTasks": settings.get("maxConcurrentTasks", 3),
                     "leaseSeconds": settings.get("leaseSeconds", 420),
                     "maxRepairAttempts": settings.get("maxRepairAttempts", 1)},
    }


def external_plan(objective: str, analysis: dict, design: dict, roster: list) -> dict:
    """Optionally ask a configured planning model for a plan JSON. Real or error."""
    endpoint = (os.environ.get("ARENA_PLANNER_ENDPOINT") or "").strip()
    if not endpoint:
        return None
    key_env = os.environ.get("ARENA_PLANNER_KEY_ENV") or ""
    api_key = os.environ.get(key_env, "") if key_env else ""
    payload = {
        "objective": objective,
        "analysis": analysis,
        "baselineDesign": design,
        "roster": [{"key": a["key"], "role": a["role"], "capabilities": a["capabilities"],
                    "ownedPaths": a["ownedPaths"], "allowedTools": a["allowedTools"]} for a in roster],
        "contract": "Return JSON: {\"title\":string,\"components\":[...],\"tasks\":[{\"key\",\"title\",\"detail\",\"agentKey\",\"dependsOn\",\"paths\"}]}. "
                    "Only agent keys from roster, only ownedPaths from that agent, tools are existing MCP tools.",
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, OSError) as exc:
        raise RuntimeError(f"External planner failed at {endpoint}: {exc}") from exc
    if not isinstance(body, dict) or not isinstance(body.get("tasks"), list):
        raise RuntimeError("External planner returned no valid tasks array")
    return body
