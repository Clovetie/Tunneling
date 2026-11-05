#!/usr/bin/env python3
"""
seed_agent_bus.py — Populate the live bridge Agent Bus with the partner work order.

Reads `handoff/bus_seed.json` (agents, tasks, messages) and posts it to a running
bridge over HTTP, or writes it into an offline state directory with `--offline`.

Usage:
    python3 tools/seed_agent_bus.py                       # uses BRIDGE_URL/BRIDGE_TOKEN
    python3 tools/seed_agent_bus.py --url http://127.0.0.1:8077 --token <token>
    python3 tools/seed_agent_bus.py --offline --state-dir ./bridge_state
    python3 tools/seed_agent_bus.py --dry-run

Idempotent: re-running matches agents by name and skips tasks whose title
already exists on the board.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DEFAULT_SEED = ROOT / "handoff" / "bus_seed.json"


def http(url: str, token: str, payload: dict, timeout: float = 20.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Arena-Token"] = token
    req = urllib.request.Request(url.rstrip("/") + "/api/bus", data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot reach bridge at {url}: {exc.reason}") from exc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("BRIDGE_URL", "http://127.0.0.1:8077"))
    ap.add_argument("--token", default=os.environ.get("BRIDGE_TOKEN") or None)
    ap.add_argument("--seed", default=str(DEFAULT_SEED))
    ap.add_argument("--offline", action="store_true", help="Write directly to a local state dir (no server)")
    ap.add_argument("--state-dir", default=None, help="State dir for --offline (default roblox-bridge/bridge_state)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    seed_path = Path(args.seed)
    seed = json.loads(seed_path.read_text(encoding="utf-8"))

    if not args.token:
        token_file = HERE.parent / "bridge.token"
        if token_file.is_file():
            args.token = token_file.read_text(encoding="utf-8").strip() or None

    if args.offline:
        sys.path.insert(0, str(HERE.parent))
        from bus_core import AgentBus

        bus = AgentBus(Path(args.state_dir) / "bus.json" if args.state_dir else None)
        call = lambda payload: bus.handle(payload)  # noqa: E731
        existing_agents = bus.list_agents()["agents"]
        existing_tasks = bus.list_tasks()["tasks"]
    else:
        call = lambda payload: http(args.url, args.token, payload)  # noqa: E731
        existing_agents = call({"action": "agents"}).get("agents", [])
        existing_tasks = call({"action": "tasks"}).get("tasks", [])

    if args.dry_run:
        print(json.dumps({"agents": seed.get("agents", []), "tasks": seed.get("tasks", []),
                          "messages": seed.get("messages", [])}, indent=2, ensure_ascii=False))
        return 0

    registered = {}
    for agent in seed.get("agents", []):
        row = call({
            "action": "register",
            "name": agent["name"],
            "role": agent.get("role", "agent"),
            "capabilities": agent.get("capabilities", []),
        })
        registered[agent["name"]] = row
        print(f"[agent] {row['name']:<18} {row['id']}  role={row['role']}")

    known_titles = {t.get("title") for t in existing_tasks}
    created = 0
    for task in seed.get("tasks", []):
        if task.get("title") in known_titles:
            print(f"[task ] skip (exists) {task['title']}")
            continue
        row = call({
            "action": "task_create",
            "title": task["title"],
            "detail": task.get("detail", ""),
            "agentId": "seed",
            "priority": task.get("priority", "normal"),
            "tags": task.get("tags", []),
        })
        created += 1
        print(f"[task ] {row['id']}  {row['priority']:<8} {row['title']}")
        if task.get("result") or task.get("status") in ("done", "cancelled"):
            try:
                call({
                    "action": "task_complete",
                    "taskId": row["id"],
                    "agentId": "seed",
                    "result": task.get("result") or "completed",
                    "artifacts": task.get("artifacts") or [],
                    "status": task.get("status") or "done",
                })
                print(f"[done ] {row['title']}  {task.get('result', '')[:80]}")
            except RuntimeError as exc:
                print(f"[warn ] could not complete {row['id']}: {exc}")

    posted = 0
    for message in (seed.get("messages", []) if (created or not existing_tasks) else []):
        sender = registered.get(message.get("from", ""), {"id": message.get("from", "seed")})
        row = call({
            "action": "post",
            "agentId": sender["id"],
            "subject": message.get("subject", ""),
            "body": message.get("body", ""),
            "kind": message.get("kind", "note"),
            "priority": message.get("priority", "normal"),
        })
        posted += 1
        print(f"[msg  ] {row['id']}  {row['subject']}")

    print(f"\nseeded {len(registered)} agents, {created} new tasks, {posted} messages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
