#!/usr/bin/env python3
"""
live_acceptance.py — the G7 acceptance run, as a script instead of a checklist.

It talks to a *running* bridge (local or through the public tunnel) and records the
evidence the unit tests cannot produce: a real Studio peer, a real public URL, and
the whole path from an MCP client through that URL.

    python3 jobs/live_acceptance.py --url http://127.0.0.1:8077 --token "$BRIDGE_TOKEN"
    python3 jobs/live_acceptance.py --url https://your-name.trycloudflare.com \
        --token "$BRIDGE_TOKEN" --self-test --json-out handoff/live-acceptance.json

Every check reports pass / fail / skipped with the reason, and the JSON block is
ready to paste under "Live acceptance" in docs/OPS.md.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

STEPS = []


def record(name, status, detail, data=None):
    STEPS.append({"step": name, "status": status, "detail": detail, "data": data})
    mark = {"pass": "PASS", "fail": "FAIL", "skipped": "SKIP"}[status]
    print(f"[{mark}] {name}: {detail}")
    return status == "pass"


def http_json(base, path, token=None, method="GET", body=None, timeout=20):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw) if raw.strip().startswith(("{", "[")) else raw


def mcp_call(base, token, name, arguments, timeout=90):
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": arguments}}
    result = http_json(base, "/mcp", token=token, method="POST", body=payload, timeout=timeout)
    if "error" in result:
        raise RuntimeError(result["error"].get("message") or "MCP error")
    content = result.get("result", {})
    if content.get("isError"):
        raise RuntimeError(json.dumps(content)[:300])
    text = content.get("content", [{}])[0].get("text", "{}")
    try:
        return json.loads(text)
    except ValueError:
        return {"text": text}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8077", help="Bridge base URL (local or tunnel)")
    parser.add_argument("--token", default=os.environ.get("BRIDGE_TOKEN", ""), help="Bridge token")
    parser.add_argument("--self-test", action="store_true",
                        help="Also run the bridge self-test through /mcp (slow; touches every tool)")
    parser.add_argument("--public-url", default=None,
                        help="Also verify the public URL reported by /api/tunnel (default: the tunnel's own URL)")
    parser.add_argument("--json-out", default=None, help="Write the evidence JSON here")
    args = parser.parse_args(argv)

    base = args.url.rstrip("/")
    token = args.token
    started = time.time()
    print(f"Arena bridge live acceptance · {base} · {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1. The bridge answers and reports its version.
    try:
        health = http_json(base, "/api/health")
    except Exception as exc:  # noqa: BLE001
        record("bridge-reachable", "fail", f"{base} did not answer /api/health: {exc}")
        return finish(args, base, started, fatal=True)
    record("bridge-reachable", "pass",
           f"v{health.get('version')} · auth {'on' if health.get('authEnabled') else 'off'} · "
           f"queued {health.get('queued')} · peers {health.get('peers')}", health)

    # 2. Studio is connected through the plugin.
    try:
        peers = http_json(base, "/api/peers", token=token)
    except urllib.error.HTTPError as exc:
        record("studio-peer", "fail", f"/api/peers -> HTTP {exc.code} (token rejected?)")
        peers = {"peers": []}
    peer_rows = peers.get("peers") or []
    if peer_rows:
        record("studio-peer", "pass", f"{len(peer_rows)} peer(s): " +
               ", ".join(str(p.get('peer') or p.get('id')) for p in peer_rows[:3]), peers)
    else:
        record("studio-peer", "fail",
               "no Studio peer: open the place with the ArenaBridge plugin, then re-run")

    # 3. Tunnel state and public URL.
    try:
        tunnel = http_json(base, "/api/tunnel", token=token)
    except Exception as exc:  # noqa: BLE001
        tunnel = {"state": "error", "lastError": str(exc)}
    public_url = (tunnel.get("publicUrl") or "").rstrip("/")
    if tunnel.get("state") == "online" and public_url:
        record("tunnel-online", "pass", f"{tunnel.get('flavor')} · {public_url} · health.ok={tunnel.get('health', {}).get('ok')}", tunnel)
    else:
        record("tunnel-online", "fail",
               f"state={tunnel.get('state')} url={public_url or 'none'} lastError={tunnel.get('lastError')}", tunnel)

    # 4. The public URL serves the same bridge, with auth enforced.
    if public_url:
        try:
            with urllib.request.urlopen(public_url + "/api/health", timeout=15) as response:
                public_health = json.loads(response.read().decode("utf-8"))
            same = str(public_health.get("version")) == str(health.get("version"))
            record("public-health", "pass" if same else "fail",
                   f"public /api/health v{public_health.get('version')} "
                   f"({'matches' if same else 'differs from local ' + str(health.get('version'))})",
                   public_health)
        except Exception as exc:  # noqa: BLE001
            record("public-health", "fail", f"public URL unreachable: {exc}")

    # 5. An MCP client reaches the bridge through the tunnel and talks to Studio.
    mcp_base = args.public_url.rstrip("/") if args.public_url else (public_url or base)
    try:
        status = mcp_call(mcp_base, token, "bridge_ops", {"action": "status"})
        connected = bool(status.get("studio_connected") or status.get("studioConnected"))
        record("mcp-bridge-ops", "pass" if connected else "fail",
               f"studio_connected={connected} via {mcp_base}", status)
    except Exception as exc:  # noqa: BLE001
        record("mcp-bridge-ops", "fail", f"{mcp_base}/mcp: {exc}")

    # 6. A real Studio read through the tunnel (proves end-to-end, not just /api/health).
    if peer_rows:
        try:
            state = mcp_call(mcp_base, token, "get_studio_state", {})
            place = state.get("place") or state.get("placeName") or state.get("placeId")
            record("mcp-studio-read", "pass", f"get_studio_state returned place={place}", {"place": place})
        except Exception as exc:  # noqa: BLE001
            record("mcp-studio-read", "fail", str(exc))

    # 7. Optional: the full bridge self-test, including the Studio smoke test.
    if args.self_test:
        try:
            result = mcp_call(mcp_base, token, "self_test", {"includeStudioSmoke": True}, timeout=180)
            ok = bool(result.get("ok"))
            record("self-test", "pass" if ok else "fail",
                   f"ok={ok} tools={len(result.get('toolMatrix') or [])}", {"ok": ok})
        except Exception as exc:  # noqa: BLE001
            record("self-test", "fail", str(exc))
    else:
        record("self-test", "skipped", "pass --self-test to run every tool through the tunnel")

    # 8. Audit chain stays intact after all of that traffic.
    try:
        audit = http_json(base, "/api/audit?verify=1", token=token)
        record("audit-verify", "pass" if audit.get("ok") else "fail",
               f"chain {'intact' if audit.get('ok') else 'BROKEN'} · {audit.get('checked')} entries", audit)
    except Exception as exc:  # noqa: BLE001
        record("audit-verify", "fail", str(exc))

    return finish(args, base, started)


def finish(args, base, started, fatal=False):
    passed = sum(1 for s in STEPS if s["status"] == "pass")
    failed = sum(1 for s in STEPS if s["status"] == "fail")
    skipped = sum(1 for s in STEPS if s["status"] == "skipped")
    verdict = "pass" if failed == 0 and passed else ("blocked" if fatal else "fail")
    evidence = {
        "kind": "live-acceptance",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "url": base,
        "verdict": verdict,
        "counts": {"pass": passed, "fail": failed, "skipped": skipped},
        "durationSec": round(time.time() - started, 2),
        "steps": STEPS,
    }
    print(f"\nverdict: {verdict} ({passed} pass / {failed} fail / {skipped} skip)")
    if args.json_out:
        path = os.path.abspath(args.json_out)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, indent=2)
        print(f"[written] {path}", file=sys.stderr)
    else:
        print("\nPaste this under 'Live acceptance' in docs/OPS.md:\n")
        print(json.dumps(evidence, indent=2))
    return 0 if verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
