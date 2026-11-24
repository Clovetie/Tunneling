#!/usr/bin/env python3
"""
measure_tunnel_latency.py — how much does the public tunnel cost per Studio call?

Runs the same Studio job through two bridge URLs (typically localhost and the
public tunnel) and reports p50 / p95 / max round-trip time per tool. Stdlib only,
so it runs anywhere Python 3 does.

    # one target (writes the raw samples to JSON)
    python3 jobs/measure_tunnel_latency.py --url http://127.0.0.1:8077 --runs 20

    # compare local against the tunnel and emit a markdown table
    python3 jobs/measure_tunnel_latency.py \
        --url local=http://127.0.0.1:8077 \
        --url tunnel=https://your-name.trycloudflare.com \
        --token "$BRIDGE_TOKEN" --runs 20 --json-out jobs/tunnel_latency.json

Exit codes: 0 measured, 2 token rejected, 3 no Studio peer (nothing to measure),
4 every request failed.

Each target's peer list is checked before any job is enqueued, and only a job that comes
back "done" counts as a sample: a job that outlives --timeout returns "queued" with no
error, and timing that measures the wait window, not the tunnel.
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

DEFAULT_TOOLS = "ping_studio,get_studio_state,script_read"
JOB_TYPE_MAP = {
    "ping_studio": "ping",
    "get_studio_state": "survey",
    "survey_place": "survey",
    "inspect_instance": "inspect",
    "script_read": "inspect",
    "execute_luau": "run_luau",
}
TOOL_PAYLOADS = {
    "ping_studio": {},
    "get_studio_state": {},
    "survey_place": {},
    "inspect_instance": {"path": "ServerScriptService", "depth": 1},
    "script_read": {"path": "ServerScriptService", "depth": 1},
    "execute_luau": {"code": "return true"},
}
MCP_TOOL_ARGS = {
    "ping_studio": {},
    "get_studio_state": {},
    "survey_place": {},
    "inspect_instance": {"path": "ServerScriptService", "depth": 1},
    "script_read": {"path": "ServerScriptService", "startLine": 1, "endLine": 5},
    "execute_luau": {"code": "return true"},
}


NO_PEER_FATAL = (
    "{label}: no Studio peer connected to the bridge, so there is nothing to time. "
    "Open the place with the ArenaBridge plugin and retry."
)


def http_json(base_url: str, path: str, token: str, timeout: float = 10.0):
    """GET one JSON endpoint from the bridge (used for the peer pre-flight)."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base_url + path, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_target(value: str):
    """Accept 'label=url' or a bare url."""
    if "=" in value and value.split("=", 1)[0] and not value.startswith("http"):
        label, url = value.split("=", 1)
        return label.strip(), url.strip().rstrip("/")
    return "bridge", value.strip().rstrip("/")


def post_job(base_url: str, tool: str, token: str, timeout: float):
    """POST one Studio job and return (elapsed_ms, job_dict)."""
    job_type = JOB_TYPE_MAP.get(tool, tool)
    payload = {
        "type": job_type,
        "payload": TOOL_PAYLOADS.get(tool, {}),
        "wait": timeout,
        "dm": "edit",
        "note": f"measure_tunnel_latency:{tool}",
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base_url + "/api/jobs", data=data, method="POST", headers=headers)
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout + 15) as response:
        body = response.read().decode("utf-8")
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    try:
        job = json.loads(body)
    except ValueError:
        job = {"status": "error", "error": body[:200]}
    return elapsed_ms, job


def post_mcp_call(base_url: str, tool: str, token: str, timeout: float):
    """POST one Streamable HTTP /mcp tools/call and return (elapsed_ms, normalized_dict)."""
    rpc = {
        "jsonrpc": "2.0",
        "id": f"lat-{tool}-{int(time.time() * 1000)}",
        "method": "tools/call",
        "params": {"name": tool, "arguments": MCP_TOOL_ARGS.get(tool, {})},
    }
    data = json.dumps(rpc).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/call",
        "Mcp-Name": tool,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(base_url + "/mcp", data=data, method="POST", headers=headers)
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=timeout + 15) as response:
        body = response.read().decode("utf-8")
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    try:
        parsed = json.loads(body)
    except ValueError:
        return elapsed_ms, {"status": "error", "error": body[:200]}
    if isinstance(parsed, dict) and parsed.get("error"):
        err = parsed["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return elapsed_ms, {"status": "error", "error": msg}
    result = (parsed or {}).get("result") if isinstance(parsed, dict) else {}
    if isinstance(result, dict) and result.get("isError"):
        content = result.get("content") or []
        text = content[0].get("text", "MCP tool error") if content and isinstance(content[0], dict) else "MCP tool error"
        return elapsed_ms, {"status": "error", "error": text}
    return elapsed_ms, {"status": "done", "result": result}


def percentile(values, fraction: float) -> float:
    """Nearest-rank percentile; deterministic for small samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), int(round(fraction * len(ordered) + 0.5))))
    return ordered[rank - 1]


def measure(targets, tools, token, runs, timeout, warmup, quiet=False, transport="jobs"):
    """Return {label: {tool: {"samples": [...], "errors": [...]}}} plus a fatal reason if any."""
    results = {}
    fatal = None
    caller = post_mcp_call if transport == "mcp" else post_job
    for label, base in targets:
        results[label] = {}
        # A target with no peer cannot be measured at all: every job waits out --timeout and
        # comes back still "queued". Pre-flight the peer list so the operator is told that
        # in a second instead of after runs x tools x --timeout seconds.
        peers = None
        try:
            peers = http_json(base, "/api/peers", token).get("peers") or []
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            # Cannot tell - no /api/peers on this target, a token without read scope, or an
            # unreachable host. That is not the same as "no peer", so fall through and let
            # the jobs themselves speak; only an authoritative empty list short-circuits.
            peers = None
        if peers is not None and not peers:
            fatal = fatal or NO_PEER_FATAL.format(label=label)
            for tool in tools:
                results[label][tool] = {"samples": [], "errors": ["no Studio peer connected"]}
            continue
        for tool in tools:
            samples, errors = [], []
            attempts = runs + max(0, warmup)
            for index in range(attempts):
                try:
                    elapsed, job = caller(base, tool, token, timeout)
                except urllib.error.HTTPError as exc:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                    if exc.code == 401:
                        fatal = (
                            f"{label}: token rejected (HTTP 401). Pass --token or set BRIDGE_TOKEN; "
                            f"server said: {detail}"
                        )
                        break
                    errors.append(f"HTTP {exc.code}: {detail}")
                    continue
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    errors.append(f"network: {exc}")
                    continue
                if job.get("status") != "done":
                    # mcp_core._unwrap_job agrees: "done" is the only success, "error" is a
                    # failure, and anything else means Studio did not finish in time. That
                    # case returns no `error` string at all, so the old `== "error"` test
                    # fell through and recorded the wait window as a latency sample.
                    reason = str(job.get("reason") or "")
                    message = str(job.get("error") or "").strip() or (
                        f"job still {job.get('status')} after {timeout:.0f}s wait")
                    errors.append(message)
                    low = f"{reason} {message}".lower()
                    if (reason in ("no_peer", "no_client_peer")
                            or "no peer" in low or "no studio" in low or "no_peer" in low):
                        fatal = fatal or NO_PEER_FATAL.format(label=label)
                    continue
                if index >= warmup:
                    samples.append(round(elapsed, 2))
            results[label][tool] = {"samples": samples, "errors": errors}
    return results, fatal


def summarize(entry):
    samples = entry["samples"]
    return {
        "count": len(samples),
        "p50": round(percentile(samples, 0.50), 2) if samples else None,
        "p95": round(percentile(samples, 0.95), 2) if samples else None,
        "max": round(max(samples), 2) if samples else None,
        "mean": round(statistics.fmean(samples), 2) if samples else None,
        "errors": len(entry["errors"]),
    }


def render(results, tools, runs):
    summary = {label: {tool: summarize(results[label][tool]) for tool in tools} for label in results}
    lines = []
    for label, rows in summary.items():
        lines.append(f"== {label} ==")
        lines.append(f"{'tool':<22}{'n':>4}{'p50 ms':>10}{'p95 ms':>10}{'max ms':>10}{'errors':>8}")
        for tool in tools:
            row = rows[tool]
            cells = [
                f"{tool:<22}",
                f"{row['count']:>4}",
                f"{row['p50'] if row['p50'] is not None else '-':>10}",
                f"{row['p95'] if row['p95'] is not None else '-':>10}",
                f"{row['max'] if row['max'] is not None else '-':>10}",
                f"{row['errors']:>8}",
            ]
            lines.append("".join(cells))
        lines.append("")
    return summary, lines


def render_markdown(summary, tools, labels):
    header = "| Tool | " + " | ".join(f"{label} p50 / p95 (ms)" for label in labels)
    if len(labels) == 2:
        header += " | delta p50 |"
    header += " |"
    sep = "|---" * (header.count("|") - 1) + "|"
    rows = [header, sep]
    for tool in tools:
        cells = [tool]
        for label in labels:
            row = summary[label][tool]
            p50 = row["p50"] if row["p50"] is not None else "n/a"
            p95 = row["p95"] if row["p95"] is not None else "n/a"
            cells.append(f"{p50} / {p95}")
        if len(labels) == 2:
            a = summary[labels[0]][tool]["p50"]
            b = summary[labels[1]][tool]["p50"]
            cells.append(f"{round(b - a, 2)}" if a is not None and b is not None else "n/a")
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", action="append", default=None,
                        help="Bridge URL, optionally labelled: --url local=http://127.0.0.1:8077")
    parser.add_argument("--token", default=os.environ.get("BRIDGE_TOKEN", ""),
                        help="Bridge token (default: $BRIDGE_TOKEN)")
    parser.add_argument("--runs", type=int, default=20, help="Samples per tool (default: 20)")
    parser.add_argument("--warmup", type=int, default=1, help="Discarded warm-up calls per tool")
    parser.add_argument("--tools", default=DEFAULT_TOOLS, help=f"Comma list (default: {DEFAULT_TOOLS})")
    parser.add_argument("--transport", default="jobs", choices=["jobs", "mcp"],
                        help="Benchmark REST /api/jobs (default) or Streamable HTTP /mcp tools/call")
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-job wait in seconds")
    parser.add_argument("--json-out", default=None, help="Write raw samples and summary JSON here")
    parser.add_argument("--markdown", action="store_true", help="Print only the markdown table")
    args = parser.parse_args(argv)

    targets = [parse_target(u) for u in (args.url or ["http://127.0.0.1:8077"])]
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]
    results, fatal = measure(targets, tools, args.token, max(1, args.runs), args.timeout,
                             max(0, args.warmup), transport=args.transport)
    summary, lines = render(results, tools, args.runs)

    if args.json_out:
        payload = {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "runs": args.runs,
            "warmup": args.warmup,
            "targets": {label: url for label, url in targets},
            "tools": tools,
            "summary": summary,
            "samples": {label: {tool: results[label][tool]["samples"] for tool in tools} for label in results},
            "errors": {label: {tool: results[label][tool]["errors"] for tool in tools} for label in results},
            "fatal": fatal,
        }
        out_path = os.path.abspath(args.json_out)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        print(f"[written] {out_path}", file=sys.stderr)

    measured = sum(1 for label in summary for tool in tools if summary[label][tool]["count"])
    if fatal and "token rejected" in fatal:
        print(f"error: {fatal}", file=sys.stderr)
        return 2
    if measured == 0:
        # Print no table. A table of `n/a` rows is the thing that gets pasted into
        # docs/OPS.md as a latency budget, and --markdown makes it look like a result.
        if fatal:
            print(f"error: {fatal}", file=sys.stderr)
            verdict = 3
        else:
            print("error: every request failed; check the URL and the bridge log", file=sys.stderr)
            verdict = 4
        # The script already knows why; "go read the bridge log" made the operator do the
        # work the tool had done. Up to five distinct reasons, in target order.
        reasons = []
        for label in results:
            for tool in tools:
                for err in results[label][tool]["errors"]:
                    if err not in reasons:
                        reasons.append(err)
        for err in reasons[:5]:
            print(f"  {err}", file=sys.stderr)
        if len(reasons) > 5:
            print(f"  ... and {len(reasons) - 5} more (see --json-out for all of them)", file=sys.stderr)
        return verdict

    if args.markdown:
        labels = list(summary)
        print(render_markdown(summary, tools, labels))
    else:
        print("\n".join(lines))
        if len(summary) > 1:
            print("Markdown excerpt for docs/OPS.md:\n")
            print(render_markdown(summary, tools, list(summary)))
    if fatal:
        print(f"note: {fatal}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
