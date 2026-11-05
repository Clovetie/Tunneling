#!/usr/bin/env python3
"""
tunnel_connect.py — connect to a bridge through its public tunnel and prove it works.

This is the "did the tunnel actually give me a working MCP endpoint?" check: one
short run (< 10 s), no Studio writes, every result printed as PASS / FAIL / SKIP.

    python3 jobs/tunnel_connect.py --url https://name.trycloudflare.com --token "$TOKEN"

It exercises exactly what an MCP client does over the tunnel:

  1. GET  /api/health          (public, no token — proves the URL reaches the bridge)
  2. POST /mcp server/discover (modern 2026-07-28 era, no handshake required)
  3. POST /mcp initialize      (legacy era negotiation, so old clients keep working)
  4. POST /mcp tools/list      (tool count + execution.taskSupport)
  5. POST /mcp resources/list  and  prompts/list
  6. POST /mcp tools/call studio_fleet {action:list}   (the fleet behind the tunnel)
  7. POST /mcp tools/call get_studio_state / ping_studio (SKIP when no Studio is open)

`--seed-tasks FILE` additionally registers an agent on the bus and files the tasks
from a JSON wave (see handoff/bus_seed_v6.json) so other agents can pick them up.
Seeding is idempotent: a task whose exact title is already on the board is skipped.

Exit codes: 0 ok · 2 token rejected · 3 unreachable · 4 MCP endpoint broken.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ROWS = []
MODERN = "2026-07-28"
LEGACY = "2025-06-18"


def record(step, status, detail, data=None):
    ROWS.append({"step": step, "status": status, "detail": detail, "data": data})
    mark = {"pass": "PASS", "fail": "FAIL", "skipped": "SKIP"}[status]
    print(f"[{mark}] {step}: {detail}")
    return status


def http_json(base, path, token=None, method="GET", body=None, timeout=20, headers=None):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    if headers:
        hdrs.update(headers)
    request = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
        return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def rpc(base, token, method, params=None, version=None, timeout=30):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    headers = {"MCP-Protocol-Version": version} if version else None
    return http_json(base, "/mcp", token=token, method="POST", body=payload,
                     timeout=timeout, headers=headers)


def tool_call(base, token, name, arguments, timeout=90):
    result = rpc(base, token, "tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
    if "error" in result:
        raise RuntimeError(result["error"].get("message") or "MCP error")
    content = result.get("result", {})
    if content.get("isError"):
        raise RuntimeError(json.dumps(content)[:300])
    text = (content.get("content") or [{}])[0].get("text", "{}")
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def seed_tasks(base, token, path, agent_name="arena-agent", role="operator"):
    """Register an agent and file each task from a wave file. Idempotent by title."""
    wave = json.loads(open(path, encoding="utf-8").read())
    tasks = wave.get("tasks") or []
    agent = http_json(base, "/api/bus", token=token, method="POST",
                      body={"action": "register", "name": agent_name, "role": role})
    board = http_json(base, "/api/bus?action=tasks&includeDone=1", token=token)
    existing = {t.get("title") for t in (board.get("tasks") or [])}
    created, skipped = [], []
    for spec in tasks:
        title = spec.get("title", "").strip()
        if not title:
            continue
        if title in existing:
            skipped.append(title)
            continue
        body = {
            "action": "task_create",
            "title": title,
            "detail": spec.get("detail", ""),
            "priority": spec.get("priority", "normal"),
            "tags": spec.get("tags") or [],
            "estimateMinutes": spec.get("estimateMinutes"),
            "agentId": agent.get("id"),
        }
        if spec.get("dependsOn"):
            body["dependsOn"] = spec["dependsOn"]
        task = http_json(base, "/api/bus", token=token, method="POST", body=body)
        created.append(task.get("id"))
        existing.add(title)
    return {"agent": agent.get("id"), "created": created, "skipped": skipped,
            "wave": wave.get("name") or os.path.basename(path)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=os.environ.get("BRIDGE_URL", "http://127.0.0.1:8077"),
                        help="Bridge base URL (local or https tunnel)")
    parser.add_argument("--token", default=os.environ.get("BRIDGE_TOKEN", ""), help="Bridge token")
    parser.add_argument("--seed-tasks", default=None,
                        help="File the tasks from this JSON wave on the agent bus")
    parser.add_argument("--agent-name", default="arena-agent", help="Agent name used when seeding")
    parser.add_argument("--json-out", default=None, help="Write the result JSON here")
    parser.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout (seconds)")
    parser.add_argument("--self-test", action="store_true",
                        help="Only check argument wiring, then exit 0")
    args = parser.parse_args(argv)

    if args.self_test:
        print("self-test ok: url/token/seed arguments parsed, no network used")
        return 0

    base, token = args.url.rstrip("/"), args.token
    started = time.time()
    print(f"Arena tunnel connect · {base} · {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  token: {'set (' + str(len(token)) + ' chars)' if token else 'MISSING'}\n")

    # 1. Public health — proves DNS + TLS + Cloudflare routing all reach the bridge.
    t0 = time.monotonic()
    try:
        health = http_json(base, "/api/health", timeout=args.timeout)
    except urllib.error.HTTPError as exc:
        record("health", "fail", f"/api/health -> HTTP {exc.code} (the tunnel reaches something, "
                                f"but not a healthy bridge)")
        return finish(args, base, started, 3)
    except Exception as exc:  # noqa: BLE001
        record("health", "fail",
               f"cannot reach {base} — trycloudflare DNS/egress blocked, tunnel closed, "
               f"or the address is stale ({exc})")
        return finish(args, base, started, 3)
    rtt = round((time.monotonic() - t0) * 1000.0, 1)
    studios = health.get("studios") or {}
    record("health", "pass",
           f"v{health.get('version')} · {rtt}ms · studios {studios.get('online', 0)}/"
           f"{studios.get('count', 0)} online · queued {health.get('queued')}", health)

    # 2. Modern era: server/discover without any handshake.
    discover = None
    try:
        discover = rpc(base, token, "server/discover", version=MODERN, timeout=args.timeout)
        versions = (discover.get("result") or {}).get("supportedVersions") or []
        if MODERN in versions:
            record("mcp-discovery", "pass", f"server/discover ok · supports {', '.join(versions[:3])} …",
                   discover.get("result"))
        else:
            record("mcp-discovery", "fail", f"server/discover did not list {MODERN} (got {versions})")
    except urllib.error.HTTPError as exc:
        code = "token rejected" if exc.code in (401, 403) else f"HTTP {exc.code}"
        record("mcp-discovery", "fail", f"/mcp -> {code}")
        if exc.code in (401, 403):
            return finish(args, base, started, 2)
    except Exception as exc:  # noqa: BLE001
        record("mcp-discovery", "fail", f"server/discover failed: {exc}")

    # 3. Legacy era still negotiates, so older clients keep working through the tunnel.
    try:
        init = rpc(base, token, "initialize",
                   {"protocolVersion": LEGACY, "capabilities": {}, "clientInfo": {"name": "tunnel-connect"}},
                   timeout=args.timeout)
        negotiated = (init.get("result") or {}).get("protocolVersion")
        caps = (init.get("result") or {}).get("capabilities") or {}
        record("mcp-initialize", "pass" if negotiated else "fail",
               f"legacy handshake -> {negotiated} · tasks capability "
               f"{'yes' if 'tasks' in caps else 'no'}")
    except Exception as exc:  # noqa: BLE001
        record("mcp-initialize", "fail", f"initialize failed: {exc}")

    # 4-5. Catalogues.
    try:
        tools = rpc(base, token, "tools/list", version=MODERN, timeout=args.timeout)
        rows = (tools.get("result") or {}).get("tools") or []
        task_support = sum(1 for t in rows if (t.get("execution") or {}).get("taskSupport"))
        record("tools/list", "pass" if rows else "fail",
               f"{len(rows)} tools · {task_support} task-capable · resultType "
               f"{(tools.get('result') or {}).get('resultType')}")
    except Exception as exc:  # noqa: BLE001
        record("tools/list", "fail", str(exc))
    for method, key in (("resources/list", "resources"), ("prompts/list", "prompts")):
        try:
            result = rpc(base, token, method, version=MODERN, timeout=args.timeout)
            rows = (result.get("result") or {}).get(key) or []
            record(method, "pass" if rows else "fail", f"{len(rows)} entries")
        except Exception as exc:  # noqa: BLE001
            record(method, "fail", str(exc))

    # 6. The fleet as seen through the tunnel.
    try:
        fleet = tool_call(base, token, "studio_fleet", {"action": "list"}, timeout=args.timeout + 10)
        names = [s.get("label") or s.get("peerId") for s in (fleet.get("studios") or [])][:4]
        record("studio_fleet", "pass",
               f"{fleet.get('count', 0)} studio(s) · routing {fleet.get('routing')}"
               + (f" · {', '.join(names)}" if names else " · no windows open"))
    except Exception as exc:  # noqa: BLE001
        record("studio_fleet", "fail", str(exc))

    # 7. Studio-dependent calls: SKIP with a reason instead of failing when nothing is open.
    if not (health.get("studio_connected") or studios.get("online")):
        record("get_studio_state", "skipped", "no Studio peer is polling the bridge right now")
        record("ping_studio", "skipped", "no Studio peer is polling the bridge right now")
    else:
        try:
            t0 = time.monotonic()
            state = tool_call(base, token, "get_studio_state", {}, timeout=args.timeout + 20)
            record("get_studio_state", "pass",
                   f"{state.get('studio', {}).get('place') or 'place'} · "
                   f"{round((time.monotonic() - t0) * 1000.0, 1)}ms round trip")
        except Exception as exc:  # noqa: BLE001
            record("get_studio_state", "fail", str(exc))
        try:
            t0 = time.monotonic()
            ping = tool_call(base, token, "ping_studio", {}, timeout=args.timeout + 20)
            record("ping_studio", "pass", f"{round((time.monotonic() - t0) * 1000.0, 1)}ms "
                                          f"· plugin {ping.get('pluginVersion')}")
        except Exception as exc:  # noqa: BLE001
            record("ping_studio", "fail", str(exc))

    # 8. Optional: give other agents work.
    if args.seed_tasks:
        try:
            seeded = seed_tasks(base, token, args.seed_tasks, agent_name=args.agent_name)
            record("seed-tasks", "pass",
                   f"{len(seeded['created'])} created · {len(seeded['skipped'])} already present "
                   f"· agent {seeded['agent']} ({seeded['wave']})", seeded)
        except Exception as exc:  # noqa: BLE001
            record("seed-tasks", "fail", str(exc))

    failed = [r for r in ROWS if r["status"] == "fail"]
    return finish(args, base, started, 0 if not failed else 4)


def finish(args, base, started, code):
    elapsed = round(time.time() - started, 2)
    passed = sum(1 for r in ROWS if r["status"] == "pass")
    skipped = sum(1 for r in ROWS if r["status"] == "skipped")
    failed = sum(1 for r in ROWS if r["status"] == "fail")
    print(f"\n{passed} passed · {failed} failed · {skipped} skipped · {elapsed}s")
    if code == 3:
        print("Verdict: the bridge was never reached — the tunnel is down, the address is stale, "
              "or this machine cannot resolve it (corporate DNS / egress allowlist).")
    elif code == 2:
        print("Verdict: the tunnel works, the token does not. Re-print it with `python setup.py` "
              "and paste the value from bridge.token.")
    elif failed:
        print("Verdict: the tunnel is reachable but the MCP path is not healthy — read the FAIL rows.")
    elif passed:
        print("Verdict: this tunnel carries a working MCP endpoint. Paste the /mcp URL + token into your client.")
    payload = {"url": base, "verdict": "pass" if not failed and passed else "fail",
               "passed": passed, "failed": failed, "skipped": skipped,
               "elapsedSeconds": elapsed, "rows": ROWS}
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"evidence -> {args.json_out}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
