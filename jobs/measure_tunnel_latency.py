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
TOOL_PAYLOADS = {
    "ping_studio": {},
    "get_studio_state": {},
    "script_read": {"path": "ServerScriptService"},
}


def parse_target(value: str):
    """Accept 'label=url' or a bare url."""
    if "=" in value and value.split("=", 1)[0] and not value.startswith("http"):
        label, url = value.split("=", 1)
        return label.strip(), url.strip().rstrip("/")
    return "bridge", value.strip().rstrip("/")


def post_job(base_url: str, tool: str, token: str, timeout: float):
    """POST one Studio job and return (elapsed_ms, job_dict)."""
    payload = {
        "type": tool,
        "payload": TOOL_PAYLOADS.get(tool, {}),
        "wait": timeout,
        "dm": "edit",
        "note": "measure_tunnel_latency",
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


def percentile(values, fraction: float) -> float:
    """Nearest-rank percentile; deterministic for small samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), int(round(fraction * len(ordered) + 0.5))))
    return ordered[rank - 1]


def measure(targets, tools, token, runs, timeout, warmup, quiet=False):
    """Return {label: {tool: {"samples": [...], "errors": [...]}}} plus a fatal reason if any."""
    results = {}
    fatal = None
    for label, base in targets:
        results[label] = {}
        for tool in tools:
            samples, errors = [], []
            attempts = runs + max(0, warmup)
            for index in range(attempts):
                try:
                    elapsed, job = post_job(base, tool, token, timeout)
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
                if job.get("status") == "error":
                    message = str(job.get("error") or "")
                    errors.append(message or "job failed")
                    if "no peer" in message.lower() or "no studio" in message.lower():
                        fatal = fatal or (
                            f"{label}: no Studio peer connected to the bridge, so there is nothing to time. "
                            "Open the place with the ArenaBridge plugin and retry."
                        )
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
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-job wait in seconds")
    parser.add_argument("--json-out", default=None, help="Write raw samples and summary JSON here")
    parser.add_argument("--markdown", action="store_true", help="Print only the markdown table")
    args = parser.parse_args(argv)

    targets = [parse_target(u) for u in (args.url or ["http://127.0.0.1:8077"])]
    tools = [t.strip() for t in args.tools.split(",") if t.strip()]
    results, fatal = measure(targets, tools, args.token, max(1, args.runs), args.timeout,
                             max(0, args.warmup))
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

    if args.markdown:
        labels = list(summary)
        print(render_markdown(summary, tools, labels))
    else:
        print("\n".join(lines))
        if len(summary) > 1:
            print("Markdown excerpt for docs/OPS.md:\n")
            print(render_markdown(summary, tools, list(summary)))

    measured = sum(1 for label in summary for tool in tools if summary[label][tool]["count"])
    if fatal and "token rejected" in fatal:
        print(f"error: {fatal}", file=sys.stderr)
        return 2
    if measured == 0:
        if fatal:
            print(f"error: {fatal}", file=sys.stderr)
            return 3
        print("error: every request failed; check the URL and the bridge log", file=sys.stderr)
        return 4
    if fatal:
        print(f"note: {fatal}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
