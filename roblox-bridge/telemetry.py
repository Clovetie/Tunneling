#!/usr/bin/env python3
"""
telemetry.py — Metrics, live event stream, and tamper-evident audit log for the
Arena Roblox Studio bridge.

Zero-dependency. Three cooperating pieces:

  Metrics      counters / gauges / latency histograms with label sets, plus a
               Prometheus text exposition and a JSON snapshot for the dashboard.
  EventStream  bounded, sequence-numbered event ring with condition-variable
               waits, powering `GET /api/events` (Server-Sent Events).
  AuditLog     append-only JSONL log where every record chains the SHA-256 hash
               of its predecessor, so silent edits/tampering are detectable with
               `verify()`.

All three are thread-safe and safe to embed in server.py.
"""

import hashlib
import json
import os
import threading
import time
from collections import deque
from pathlib import Path

TELEMETRY_VERSION = "5.1.0"
DEFAULT_STATE_DIR = Path(__file__).resolve().parent / "bridge_state"

# Bounded sample count per histogram key; percentiles are computed from this
# reservoir and the true count/sum/min/max are kept exactly.
_HIST_SAMPLES = 2048


def _now() -> float:
    return time.time()


def _file_lock(fh):
    """Best-effort advisory exclusive lock for cross-process audit appends."""
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return "fcntl"
    except (ImportError, OSError):
        try:
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            return "msvcrt"
        except (ImportError, OSError):
            return None


def _file_unlock(fh, kind):
    try:
        if kind == "fcntl":
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        elif kind == "msvcrt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except (ImportError, OSError):
        pass


def _label_key(labels: dict) -> str:
    if not labels:
        return ""
    return ",".join(f"{k}={labels[k]}" for k in sorted(labels))


def _fmt_num(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


class Metrics:
    """Counter/gauge/histogram registry with Prometheus + JSON output."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started = _now()
        self.counters = {}   # key -> {"name","labels","value"}
        self.gauges = {}
        self.histograms = {}  # key -> {"name","labels","count","sum","min","max","samples":deque}
        self.histogram_order = {}

    def _inc_unlocked(self, name: str, labels: dict, value: float):
        key = (name, _label_key(labels))
        row = self.counters.get(key)
        if row is None:
            row = {"name": name, "labels": dict(labels or {}), "value": 0.0}
            self.counters[key] = row
        row["value"] += value
        return row

    def inc(self, name: str, value: float = 1, labels: dict = None):
        with self.lock:
            self._inc_unlocked(name, labels or {}, float(value))

    def gauge(self, name: str, value: float, labels: dict = None):
        with self.lock:
            key = (name, _label_key(labels or {}))
            self.gauges[key] = {"name": name, "labels": dict(labels or {}), "value": float(value)}

    def observe(self, name: str, value: float, labels: dict = None, buckets=None):
        value = float(value)
        with self.lock:
            key = (name, _label_key(labels or {}))
            row = self.histograms.get(key)
            if row is None:
                row = {
                    "name": name,
                    "labels": dict(labels or {}),
                    "count": 0,
                    "sum": 0.0,
                    "min": value,
                    "max": value,
                    "samples": deque(maxlen=_HIST_SAMPLES),
                    "buckets": list(buckets or ()),
                    "bucketCounts": {b: 0 for b in (buckets or ())},
                }
                self.histograms[key] = row
            row["count"] += 1
            row["sum"] += value
            row["min"] = min(row["min"], value)
            row["max"] = max(row["max"], value)
            row["samples"].append(value)
            for b in row["buckets"]:
                if value <= b:
                    row["bucketCounts"][b] += 1
            self.histogram_order[key] = self.histogram_order.pop(
                key, len(self.histogram_order)
            )
            return row

    @staticmethod
    def _percentile(sorted_samples, p: float) -> float:
        if not sorted_samples:
            return 0.0
        if len(sorted_samples) == 1:
            return float(sorted_samples[0])
        rank = (len(sorted_samples) - 1) * p
        lo = int(rank)
        hi = min(lo + 1, len(sorted_samples) - 1)
        frac = rank - lo
        return float(sorted_samples[lo] * (1 - frac) + sorted_samples[hi] * frac)

    def summarize_histogram(self, row: dict) -> dict:
        samples = sorted(row["samples"])
        return {
            "count": row["count"],
            "sum": round(row["sum"], 3),
            "min": round(row["min"], 3),
            "max": round(row["max"], 3),
            "mean": round(row["sum"] / row["count"], 3) if row["count"] else 0.0,
            "p50": round(self._percentile(samples, 0.50), 3),
            "p90": round(self._percentile(samples, 0.90), 3),
            "p95": round(self._percentile(samples, 0.95), 3),
            "p99": round(self._percentile(samples, 0.99), 3),
        }

    def snapshot(self, uptime: float = None) -> dict:
        with self.lock:
            counters = [dict(v) for v in self.counters.values()]
            gauges = [dict(v) for v in self.gauges.values()]
            histograms = []
            for row in self.histograms.values():
                entry = {"name": row["name"], "labels": row["labels"]}
                entry.update(self.summarize_histogram(row))
                histograms.append(entry)
        counters.sort(key=lambda r: (r["name"], _label_key(r["labels"])))
        gauges.sort(key=lambda r: (r["name"], _label_key(r["labels"])))
        histograms.sort(key=lambda r: r["name"])
        return {
            "version": TELEMETRY_VERSION,
            "uptimeSeconds": round(uptime if uptime is not None else (_now() - self.started), 2),
            "counters": counters,
            "gauges": gauges,
            "histograms": histograms,
        }

    def prometheus(self, extra_gauges: dict = None) -> str:
        lines = []
        with self.lock:
            counters = list(self.counters.values())
            gauges = list(self.gauges.values())
            histograms = list(self.histograms.values())
        seen_types = set()

        def _decl(name, kind):
            if name not in seen_types:
                seen_types.add(name)
                lines.append(f"# TYPE {name} {kind}")

        for row in counters:
            _decl(row["name"], "counter")
            lines.append(f"{row['name']}{self._fmt_labels(row['labels'])} {_fmt_num(row['value'])}")
        for row in gauges:
            _decl(row["name"], "gauge")
            lines.append(f"{row['name']}{self._fmt_labels(row['labels'])} {_fmt_num(row['value'])}")
        for row in histograms:
            name = row["name"]
            _decl(name, "histogram")
            labels = dict(row["labels"])
            total = row["count"] or 1
            for b, count in row["bucketCounts"].items():
                bucket_labels = dict(labels)
                bucket_labels["le"] = b
                lines.append(f"{name}_bucket{self._fmt_labels(bucket_labels)} {count}")
            bucket_labels = dict(labels)
            bucket_labels["le"] = "+Inf"
            lines.append(f"{name}_bucket{self._fmt_labels(bucket_labels)} {row['count']}")
            summary = self.summarize_histogram(row)
            lines.append(f"{name}_sum{self._fmt_labels(labels)} {_fmt_num(round(row['sum'], 3))}")
            lines.append(f"{name}_count{self._fmt_labels(labels)} {row['count']}")
            for q, key in (("0.5", "p50"), ("0.9", "p90"), ("0.95", "p95"), ("0.99", "p99")):
                q_labels = dict(labels)
                q_labels["quantile"] = q
                lines.append(f"{name}{self._fmt_labels(q_labels)} {_fmt_num(summary[key])}")
            _ = total
        for name, value in (extra_gauges or {}).items():
            try:
                _decl(name, "gauge")
                lines.append(f"{name} {_fmt_num(float(value))}")
            except (TypeError, ValueError):
                continue
        return "\n".join(lines) + "\n"

    @staticmethod
    def _fmt_labels(labels: dict) -> str:
        if not labels:
            return ""
        parts = []
        for key in sorted(labels):
            value = str(labels[key]).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
            parts.append(f'{key}="{value}"')
        return "{" + ",".join(parts) + "}"


class EventStream:
    """Sequence-numbered event ring with blocking tail reads (SSE friendly)."""

    def __init__(self, max_events: int = 1500):
        self.lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.events = deque(maxlen=max_events)
        self.seq = 0

    def emit(self, kind: str, data: dict = None, level: str = "info") -> dict:
        with self.cv:
            self.seq += 1
            event = {
                "seq": self.seq,
                "ts": _now(),
                "kind": kind,
                "level": level,
                "data": data or {},
            }
            self.events.append(event)
            self.cv.notify_all()
            return event

    def since(self, since: int = 0, limit: int = 200, kinds=None) -> dict:
        limit = max(1, min(int(limit or 200), 1000))
        with self.lock:
            rows = [e for e in self.events if e["seq"] > since]
            if kinds:
                wanted = {k.strip() for k in str(kinds).split(",") if k.strip()}
                rows = [e for e in rows if any(e["kind"].startswith(w) for w in wanted)]
            rows = rows[-limit:]
            cursor = self.seq
        return {"events": rows, "count": len(rows), "cursor": cursor}

    def tail(self, cursor: int, timeout: float = 15.0, limit: int = 100) -> dict:
        deadline = time.monotonic() + max(0.0, timeout)
        with self.cv:
            while self.seq <= cursor:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {"events": [], "count": 0, "cursor": self.seq, "timeout": True}
                self.cv.wait(timeout=min(remaining, 1.0))
            rows = [e for e in self.events if e["seq"] > cursor][:limit]
            return {"events": rows, "count": len(rows), "cursor": self.seq, "timeout": False}


class AuditLog:
    """Append-only, hash-chained audit log (JSONL)."""

    GENESIS = "0" * 64

    def __init__(self, path=None, persist: bool = True, max_memory: int = 5000):
        self.path = Path(path) if path else DEFAULT_STATE_DIR / "audit.jsonl"
        self.persist = bool(persist)
        self.lock = threading.Lock()
        self.entries = deque(maxlen=max_memory)
        self.last_hash = self.GENESIS
        self.seq = 0
        self._load()

    def _load(self):
        if not self.persist or not self.path.is_file():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    self.entries.append(row)
                    self.last_hash = row.get("hash") or self.last_hash
                    self.seq = max(self.seq, int(row.get("seq") or 0))
        except OSError:
            pass

    def _read_disk_tail(self, fh) -> dict:
        """Read the last complete JSONL record from an open file handle."""
        try:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            window = min(size, 16384)
            fh.seek(size - window)
            chunk = fh.read()
            lines = [ln for ln in chunk.splitlines() if ln.strip()]
            if not lines:
                return {}
            return json.loads(lines[-1])
        except (OSError, ValueError):
            return {}

    def _sync_head_from_disk(self):
        """Adopt a newer on-disk head so concurrent writers keep one chain."""
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                row = self._read_disk_tail(fh)
        except OSError:
            return
        head = row.get("hash")
        if head and head != self.last_hash:
            self.last_hash = head
            self.seq = max(self.seq, int(row.get("seq") or 0))

    def append(self, action: str, actor: str = "system", target: str = "", detail=None, ip: str = "", outcome: str = "ok") -> dict:
        with self.lock:
            if not self.persist:
                self._append_unlocked(action, actor, target, detail, ip, outcome)
                return dict(self.entries[-1])
            self.path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self.path.open("a+", encoding="utf-8") as fh:
                    lock = _file_lock(fh)
                    try:
                        self._sync_head_from_disk()
                        entry = self._append_unlocked(action, actor, target, detail, ip, outcome)
                        fh.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                        fh.flush()
                    finally:
                        _file_unlock(fh, lock)
                return dict(entry)
            except OSError:
                entry = self._append_unlocked(action, actor, target, detail, ip, outcome)
                return dict(entry)

    def _append_unlocked(self, action, actor, target, detail, ip, outcome) -> dict:
        self.seq += 1
        entry = {
            "seq": self.seq,
            "ts": _now(),
            "action": action,
            "actor": actor or "system",
            "target": target or "",
            "outcome": outcome,
            "ip": ip or "",
            "detail": detail if isinstance(detail, (dict, list, str, int, float, type(None))) else str(detail),
            "prev": self.last_hash,
        }
        canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        entry["hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.last_hash = entry["hash"]
        self.entries.append(entry)
        return entry

    def tail(self, limit: int = 100, action: str = None) -> dict:
        with self.lock:
            rows = list(self.entries)
        if action:
            rows = [r for r in rows if action in str(r.get("action", ""))]
        rows = rows[-max(1, min(int(limit or 100), 2000)):]
        rows.reverse()
        return {"entries": rows, "count": len(rows), "cursor": self.seq}

    def verify(self) -> dict:
        """Recompute the whole chain. Detects edit, reorder, deletion or insertion."""
        with self.lock:
            rows = list(self.entries)
        prev = self.GENESIS
        for index, row in enumerate(rows):
            stored = row.get("hash")
            body = {k: v for k, v in row.items() if k != "hash"}
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if row.get("prev") != prev:
                return {
                    "ok": False,
                    "checked": index + 1,
                    "brokenAt": row.get("seq"),
                    "reason": "prev-hash mismatch (entry inserted, removed or reordered)",
                }
            if expected != stored:
                return {
                    "ok": False,
                    "checked": index + 1,
                    "brokenAt": row.get("seq"),
                    "reason": "content hash mismatch (entry was edited)",
                }
            prev = stored
        return {
            "ok": True,
            "checked": len(rows),
            "head": prev,
            "entries": len(rows),
            "file": str(self.path) if self.persist else None,
        }


class Telemetry:
    """Convenience container wired into BridgeState."""

    def __init__(self, state_dir=None, persist: bool = True):
        state_dir = Path(state_dir) if state_dir else DEFAULT_STATE_DIR
        self.metrics = Metrics()
        self.events = EventStream()
        self.audit = AuditLog(state_dir / "audit.jsonl", persist=persist)
        self.started = _now()

    @property
    def uptime(self) -> float:
        return _now() - self.started

    def record_job(self, job: dict):
        """Called when a job finishes: counters + latency histogram + event."""
        ok = bool(job.get("ok"))
        labels = {"type": job.get("type") or "unknown", "dm": job.get("dm") or job.get("target_dm") or "auto"}
        self.metrics.inc("bridge_jobs_total", 1, dict(labels, outcome="ok" if ok else "error"))
        duration = job.get("duration_ms")
        if isinstance(duration, (int, float)):
            self.metrics.observe("bridge_job_duration_ms", float(duration), labels)
        self.events.emit(
            "job.finish",
            {
                "id": job.get("id"),
                "type": job.get("type"),
                "status": job.get("status"),
                "durationMs": duration,
                "error": job.get("error"),
            },
            level="info" if ok else "error",
        )
