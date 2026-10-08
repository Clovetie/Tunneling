#!/usr/bin/env python3
"""
Arena <-> Roblox Studio bridge server.

A tiny dependency-free job queue. The Arena agent (or you, via curl) pushes jobs;
the ArenaBridge Studio plugin polls for them, executes them inside Studio, and
posts results back.

    agent  --POST /api/jobs-->  [queue]  <--GET /api/poll--  Studio plugin
    agent  <--GET /api/jobs/id--[results]<--POST /api/result--Studio plugin

Run:  python3 server.py [--port 8077] [--token SECRET]
"""

import argparse
import json
import os
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.0.0"

# ----------------------------------------------------------------------------
# State
# ----------------------------------------------------------------------------

LOCK = threading.Lock()
PENDING = deque()            # jobs waiting to be picked up by Studio
JOBS = {}                    # job_id -> job record
HISTORY = deque(maxlen=100)  # recent job ids, newest last
STUDIO = {"last_seen": 0.0, "place": None, "studio_version": None, "client": None}
NEW_JOB = threading.Event()


def now():
    return time.time()


def make_job(kind, payload, note=None):
    job = {
        "id": uuid.uuid4().hex[:12],
        "type": kind,
        "payload": payload or {},
        "note": note,
        "status": "queued",
        "created": now(),
        "started": None,
        "finished": None,
        "ok": None,
        "result": None,
        "error": None,
        "logs": [],
    }
    with LOCK:
        JOBS[job["id"]] = job
        PENDING.append(job["id"])
        HISTORY.append(job["id"])
    NEW_JOB.set()
    NEW_JOB.clear()
    return job


def studio_online():
    return (now() - STUDIO["last_seen"]) < 10.0


# ----------------------------------------------------------------------------
# HTTP handler
# ----------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = f"ArenaRobloxBridge/{VERSION}"
    protocol_version = "HTTP/1.1"

    # quieter logs
    def log_message(self, fmt, *args):
        if os.environ.get("BRIDGE_VERBOSE"):
            super().log_message(fmt, *args)

    # -- helpers ------------------------------------------------------------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        # a page served from Arena's preview needs this when the browser
        # gates public -> private (loopback) requests (Chrome PNA/LNA).
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {"_raw": raw.decode("utf-8", "replace")}

    def _authorized(self, qs):
        if not TOKEN:
            return True
        supplied = (qs.get("token", [None])[0]
                    or self.headers.get("X-Arena-Token"))
        return supplied == TOKEN

    # -- routes -------------------------------------------------------------
    def do_OPTIONS(self):
        self._send(204, "")

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        path = url.path.rstrip("/") or "/"

        if path == "/":
            return self._send(200, dashboard_html(), "text/html; charset=utf-8")

        if path == "/api/health":
            return self._send(200, {
                "ok": True,
                "version": VERSION,
                "studio_connected": studio_online(),
                "studio": {k: v for k, v in STUDIO.items()},
                "queued": len(PENDING),
                "jobs": len(JOBS),
            })

        if path == "/api/state":
            with LOCK:
                recent = [summary(JOBS[i]) for i in list(HISTORY)[-25:]][::-1]
            return self._send(200, {
                "studio_connected": studio_online(),
                "studio": STUDIO,
                "queued": len(PENDING),
                "recent": recent,
                "server_time": now(),
            })

        # Studio plugin long-polls here
        if path == "/api/poll":
            if not self._authorized(qs):
                return self._send(401, {"error": "bad token"})
            STUDIO["last_seen"] = now()
            STUDIO["place"] = qs.get("place", [STUDIO["place"]])[0]
            STUDIO["studio_version"] = qs.get("sv", [STUDIO["studio_version"]])[0]
            STUDIO["client"] = qs.get("client", [STUDIO["client"]])[0]

            deadline = now() + 8.0
            while now() < deadline:
                with LOCK:
                    if PENDING:
                        job_id = PENDING.popleft()
                        job = JOBS[job_id]
                        job["status"] = "running"
                        job["started"] = now()
                        return self._send(200, {
                            "job": {"id": job["id"], "type": job["type"],
                                    "payload": job["payload"]}
                        })
                NEW_JOB.wait(0.4)
            return self._send(200, {"job": None})

        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            with LOCK:
                job = JOBS.get(job_id)
            if not job:
                return self._send(404, {"error": "no such job"})
            return self._send(200, job)

        if path == "/api/jobs":
            with LOCK:
                return self._send(200, {"jobs": [summary(JOBS[i]) for i in HISTORY]})

        return self._send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        path = url.path.rstrip("/") or "/"
        body = self._body()

        if not self._authorized(qs):
            return self._send(401, {"error": "bad token"})

        # agent enqueues work
        if path == "/api/jobs":
            kind = body.get("type")
            if not kind:
                return self._send(400, {"error": "type is required"})
            job = make_job(kind, body.get("payload"), body.get("note"))
            wait = float(body.get("wait") or 0)
            if wait > 0:
                deadline = now() + min(wait, 120)
                while now() < deadline:
                    with LOCK:
                        cur = JOBS[job["id"]]
                        if cur["status"] in ("done", "error"):
                            return self._send(200, cur)
                    time.sleep(0.25)
                with LOCK:
                    return self._send(200, JOBS[job["id"]])
            return self._send(201, {"id": job["id"], "status": job["status"]})

        # Studio plugin reports a finished job
        if path == "/api/result":
            job_id = body.get("id")
            with LOCK:
                job = JOBS.get(job_id)
                if not job:
                    return self._send(404, {"error": "no such job"})
                job["ok"] = bool(body.get("ok"))
                job["result"] = body.get("result")
                job["error"] = body.get("error")
                job["logs"] = body.get("logs") or []
                job["status"] = "done" if job["ok"] else "error"
                job["finished"] = now()
            return self._send(200, {"ack": True})

        return self._send(404, {"error": "not found"})


def summary(job):
    out = job.get("result")
    if isinstance(out, str) and len(out) > 400:
        out = out[:400] + " ..."
    return {
        "id": job["id"], "type": job["type"], "status": job["status"],
        "note": job["note"], "ok": job["ok"], "error": job["error"],
        "result": out, "created": job["created"], "finished": job["finished"],
    }


# ----------------------------------------------------------------------------
# Dashboard (self-contained, no external assets)
# ----------------------------------------------------------------------------

def dashboard_html():
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>Arena &#8596; Roblox Studio bridge</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root{color-scheme:dark}
 *{box-sizing:border-box}
 body{margin:0;background:#0d1117;color:#e6edf3;
      font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
 header{padding:20px 24px;border-bottom:1px solid #21262d;display:flex;
        align-items:center;gap:14px;flex-wrap:wrap}
 h1{font:600 16px/1.2 system-ui,sans-serif;margin:0;letter-spacing:.3px}
 .pill{padding:4px 11px;border-radius:999px;font-size:12px;font-weight:600;
       border:1px solid transparent}
 .on{background:#0f2f1b;color:#3fb950;border-color:#23562f}
 .off{background:#2d1618;color:#f85149;border-color:#5c2326}
 .muted{color:#8b949e;font-size:12px}
 main{padding:20px 24px;max-width:1100px}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
       gap:12px;margin-bottom:22px}
 .card{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:14px}
 .card .k{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.6px}
 .card .v{font-size:20px;font-weight:600;margin-top:4px;word-break:break-all}
 table{width:100%;border-collapse:collapse;background:#161b22;
       border:1px solid #21262d;border-radius:10px;overflow:hidden}
 th{font:600 11px/1 system-ui,sans-serif;text-transform:uppercase;letter-spacing:.6px;
    color:#8b949e;text-align:left;padding:10px 12px;background:#11161d;
    border-bottom:1px solid #21262d}
 td{padding:10px 12px;border-bottom:1px solid #1b2129;vertical-align:top;font-size:12.5px}
 tr:last-child td{border-bottom:none}
 .s-done{color:#3fb950}.s-error{color:#f85149}.s-running{color:#d29922}
 .s-queued{color:#8b949e}
 code{background:#0d1117;border:1px solid #21262d;border-radius:5px;padding:1px 5px}
 .empty{padding:26px;text-align:center;color:#8b949e}
</style></head><body>
<header>
  <h1>Arena &#8596; Roblox Studio bridge</h1>
  <span id="status" class="pill off">checking&hellip;</span>
  <span id="place" class="muted"></span>
</header>
<main>
  <div class="grid">
    <div class="card"><div class="k">Studio link</div><div class="v" id="c-link">&mdash;</div></div>
    <div class="card"><div class="k">Queued</div><div class="v" id="c-queued">0</div></div>
    <div class="card"><div class="k">Completed</div><div class="v" id="c-done">0</div></div>
    <div class="card"><div class="k">Failed</div><div class="v" id="c-fail">0</div></div>
  </div>
  <table>
    <thead><tr><th>Job</th><th>Type</th><th>Note</th><th>Status</th><th>Result</th></tr></thead>
    <tbody id="rows"><tr><td colspan="5" class="empty">No jobs yet.</td></tr></tbody>
  </table>
  <p class="muted" style="margin-top:18px">
    Plugin polls <code>/api/poll</code> &middot; agent posts to <code>/api/jobs</code>
    &middot; refreshes every 2s
  </p>
</main>
<script>
async function tick(){
  try{
    const r = await fetch('/api/state',{cache:'no-store'});
    const s = await r.json();
    const st = document.getElementById('status');
    st.textContent = s.studio_connected ? 'Studio connected' : 'Waiting for Studio';
    st.className = 'pill ' + (s.studio_connected ? 'on' : 'off');
    document.getElementById('c-link').textContent = s.studio_connected ? 'live' : 'idle';
    document.getElementById('place').textContent = s.studio && s.studio.place ? s.studio.place : '';
    document.getElementById('c-queued').textContent = s.queued;
    const rows = s.recent || [];
    document.getElementById('c-done').textContent = rows.filter(j=>j.status==='done').length;
    document.getElementById('c-fail').textContent = rows.filter(j=>j.status==='error').length;
    const tb = document.getElementById('rows');
    if(!rows.length){ tb.innerHTML = '<tr><td colspan="5" class="empty">No jobs yet.</td></tr>'; return; }
    tb.innerHTML = rows.map(j=>{
      const out = j.error ? j.error : (j.result==null ? '' : (typeof j.result==='string'? j.result : JSON.stringify(j.result)));
      const esc = v => String(v==null?'':v).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
      return `<tr><td><code>${esc(j.id)}</code></td><td>${esc(j.type)}</td>
        <td>${esc(j.note||'')}</td>
        <td class="s-${esc(j.status)}">${esc(j.status)}</td>
        <td>${esc(out).slice(0,240)}</td></tr>`;
    }).join('');
  }catch(e){}
}
tick(); setInterval(tick, 2000);
</script></body></html>"""


# ----------------------------------------------------------------------------

TOKEN = ""

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("BRIDGE_PORT", 8077)))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--token", default=os.environ.get("BRIDGE_TOKEN", ""))
    args = ap.parse_args()
    TOKEN = args.token

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    print(f"[bridge] v{VERSION} listening on {args.host}:{args.port} "
          f"(token {'set' if TOKEN else 'disabled'})", flush=True)
    srv.serve_forever()
