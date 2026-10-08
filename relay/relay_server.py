#!/usr/bin/env python3
"""
relay_server.py — sandbox side of the Arena <-> Roblox Studio relay.

Why this exists
---------------
The bridge (`server.py` + the ArenaBridge Studio plugin) lives on the *user's*
machine and speaks `http://127.0.0.1:8077`. The Arena sandbox cannot reach it:
this sandbox's egress is a fixed allowlist (github/pypi/npm only) and a
cloudflared tunnel out of the user's machine needs a host that is not on it.
See CONNECT.md sections 5-6 for the receipts.

So the connection is inverted: the sandbox serves this relay on a public
preview URL, and a *client on the user's machine* polls it. Any client works:

  * the browser page this server serves at `/`  (zero install — the user just
    opens the Arena preview; the page holds an SSE stream and fetches
    127.0.0.1:8077 directly, which is allowed because server.py sends
    `Access-Control-Allow-Origin: *`)
  * `relay/relay.ps1` (PowerShell poller, no browser needed)

Both do the same three things: take a job off the relay, POST it into the local
bridge's `/api/jobs`, POST the local bridge's answer back to the relay.

    agent (sandbox)                relay (sandbox, public URL)        client
    ───────────────                ──────────────────────────         ──────
    POST /api/jobs  ──────▶  queue ──SSE──▶ browser page ──▶ 127.0.0.1:8077
                                       ◀──POST /api/result──  (or relay.ps1)

Job envelope is exactly the local bridge's: {type, payload, note, wait}.
The stored result is exactly what the local server answered, so callers read
`result.result.returned` — same shape as talking to the bridge directly.

Endpoints (all token-gated except `/`, `/relay.ps1`, `/favicon.ico`):

  GET  /                     browser control panel (the user opens this)
  GET  /relay.ps1            the PowerShell client, for download
  GET  /events?token=…       SSE: pending jobs on connect, then live pushes
  GET  /api/jobs?token=…&client=…   poll a batch of jobs (PowerShell path)
  POST /api/jobs?token=…     AGENT: enqueue {type,payload,note,wait}
  POST /api/result?token=…   CLIENT: {id, response|error}
  POST /api/report?token=…   CLIENT: local-bridge health for the agent to read
  GET  /api/state?token=…    agent/status view
  GET  /api/result/<id>?token=…
  GET  /healthz              unauthenticated liveness only

Run:  python3 relay/relay_server.py --port 8787 --token <shared secret> [--url …]
"""

import argparse
import json
import os
import queue
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
DEFAULT_STATE_DIR = HERE.parent / ".relay-state"

LOCK = threading.Lock()
JOBS = {}                    # id -> job dict
ORDER = deque()              # ids, oldest first
CLIENTS = {}                 # client_id -> {"kind", "last_seen", "jobs": set()}
EVENT_LOG = None             # append-only JSONL, for post-mortems
STATE = {
    "started": None,
    "report": None,          # last client-reported local-bridge state
    "url": None,
    "bridge_url": "http://127.0.0.1:8077",
}
TOKEN = ""
SSE_SUBSCRIBERS = []         # list of queues

STALE_CLIENT = 20.0          # a client silent this long loses its in-flight jobs
JOB_KEEP = 400               # remember this many jobs


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def now():
    return time.time()


def new_id(prefix="j"):
    return prefix + uuid.uuid4().hex[:8]


def inline_file(path):
    """A file's text as a JS string literal, so the page can hand it to the
    user directly.

    Downloading from the preview URL needs the traffic token, and the token is
    not always visible in the framed page - which left the user with a 157-byte
    gate JSON saved as `poll_local` and nothing to run. Generating the file in
    the browser removes the download, the token and the filename from the
    equation: it is a Blob with the exact name and the exact bytes.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return '"(missing: ' + path.name + ')"'
    return json.dumps(text).replace("</", "<\\/")


def log_event(kind, payload):
    if EVENT_LOG is None:
        return
    try:
        with EVENT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": now(), "kind": kind, "payload": payload},
                                ensure_ascii=False) + "\n")
    except OSError:
        pass


def touch_client(client_id, kind):
    with LOCK:
        entry = CLIENTS.get(client_id)
        if entry is None:
            entry = {"kind": kind, "last_seen": now(), "jobs": set(), "first_seen": now()}
            CLIENTS[client_id] = entry
        entry["last_seen"] = now()
        entry["kind"] = kind
    return client_id


def enqueue(kind, payload, note=None, wait=0, source="agent"):
    job = {
        "id": new_id(),
        "type": kind,
        "payload": payload or {},
        "note": note,
        "wait": max(0.0, min(float(wait or 0), 120.0)),
        "status": "queued",
        "created": now(),
        "delivered_at": None,
        "delivered_to": None,
        "finished_at": None,
        "client_kind": None,
        "response": None,
        "error": None,
        "source": source,
    }
    with LOCK:
        JOBS[job["id"]] = job
        ORDER.append(job["id"])
        while len(ORDER) > JOB_KEEP:
            old = ORDER.popleft()
            JOBS.pop(old, None)
    log_event("enqueue", {k: job[k] for k in ("id", "type", "note", "wait", "source")})
    push_sse({"type": "job", "job": public_job(job)})
    print(f"[relay] enqueued {job['id']} {kind} (wait={job['wait']:g})", flush=True)
    return job


def public_job(job):
    """What a client needs to execute the job (no answers, no bookkeeping)."""
    return {
        "id": job["id"],
        "type": job["type"],
        "payload": job["payload"],
        "note": job["note"],
        "wait": job["wait"],
        "status": job["status"],
    }


def summary_job(job):
    out = {k: job[k] for k in ("id", "type", "note", "status", "created",
                               "delivered_at", "finished_at", "client_kind",
                               "delivered_to", "source")}
    if job.get("response") is not None:
        resp = job["response"]
        out["ok"] = resp.get("ok")
        out["error"] = resp.get("error") or (resp.get("result") or {}).get("error")
    return out


def local_body(job):
    """The exact body the client POSTs into the local bridge's /api/jobs.

    Pre-rendered so the PowerShell client never has to re-serialize JSON
    (byte-exact, no PS depth/encoding surprises).
    """
    return json.dumps({"type": job["type"], "payload": job["payload"],
                       "note": job["note"], "wait": job["wait"]}, ensure_ascii=False)


def deliver(client_id, kind, limit=8):
    """Hand undelivered (or orphaned) jobs to one client."""
    out = []
    with LOCK:
        entry = CLIENTS.get(client_id) or {"jobs": set()}
        for job_id in list(ORDER):
            if len(out) >= limit:
                break
            job = JOBS.get(job_id)
            if not job or job["status"] != "queued":
                continue
            job["status"] = "delivered"
            job["delivered_at"] = now()
            job["delivered_to"] = client_id
            job["client_kind"] = kind
            entry.setdefault("jobs", set()).add(job_id)
            item = public_job(job)
            if kind == "powershell":
                item["body"] = local_body(job)
            out.append(item)
    return out


def push_sse(message):
    payload = json.dumps(message, ensure_ascii=False)
    with LOCK:
        subs = list(SSE_SUBSCRIBERS)
    for q in subs:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass


def requeue_orphans():
    """A client that went away mid-job must not strand it."""
    with LOCK:
        for job_id, job in JOBS.items():
            if job["status"] != "delivered":
                continue
            entry = CLIENTS.get(job["delivered_to"])
            if entry is None or now() - entry["last_seen"] > STALE_CLIENT:
                job["status"] = "queued"
                job["delivered_at"] = None
                job["delivered_to"] = None
                job["client_kind"] = None
                log_event("requeue", {"id": job_id})
                print(f"[relay] requeued {job_id} (client gone)", flush=True)


def janitor():
    while True:
        time.sleep(5)
        try:
            requeue_orphans()
        except Exception as exc:                      # never die on bookkeeping
            print(f"[relay] janitor error: {exc}", flush=True)


# ---------------------------------------------------------------------------
# the browser page
# ---------------------------------------------------------------------------

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arena ↔ Studio relay</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
      background:#0d1117;color:#c9d1d9}
 .wrap{max-width:820px;margin:0 auto;padding:24px}
 h1{font-size:17px;margin:0 0 4px;font-weight:600}
 .sub{color:#8b949e;margin-bottom:20px}
 .card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:14px 16px;margin-bottom:14px}
 .row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
 .dot{width:10px;height:10px;border-radius:50%;background:#6e7681;flex:0 0 auto}
 .dot.on{background:#3fb950;box-shadow:0 0 0 4px #3fb95022}
 .dot.bad{background:#f85149;box-shadow:0 0 0 4px #f8514922}
 .dot.wait{background:#d29922}
 .k{color:#8b949e}
 .big{font-size:15px}
 code{background:#0d1117;border:1px solid #30363d;border-radius:5px;padding:1px 5px}
 input{background:#0d1117;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;
       padding:7px 9px;font:inherit;width:330px;max-width:100%}
 button{background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:6px;
        padding:7px 12px;font:inherit;cursor:pointer}
 button:hover{background:#30363d}
 button.primary{background:#238636;border-color:#2ea043;color:#fff}
 button.primary:hover{background:#2ea043}
 #log{height:240px;overflow:auto;background:#0d1117;border:1px solid #30363d;border-radius:6px;
      padding:8px 10px;white-space:pre-wrap;word-break:break-word}
 .ok{color:#3fb950}.err{color:#f85149}.dim{color:#8b949e}.warn{color:#d29922}
 .hidden{display:none}
 pre{background:#0d1117;border:1px solid #30363d;border-radius:6px;padding:9px 11px;
     margin:8px 0;white-space:pre-wrap;word-break:break-all;font-size:12.5px}
 a.dl{color:#58a6ff;text-decoration:none;border:1px solid #30363d;border-radius:6px;padding:7px 12px}
 a.dl:hover{background:#30363d}
</style></head><body><div class="wrap">
<h1>Arena ↔ Roblox Studio relay</h1>
<div class="sub">Keeps this tab as the wire between Arena and your local bridge
(<code id="bridgeurl"></code>). Leave it open while we work.</div>

<div class="card" id="gate">
  <div class="row"><span class="k">Bridge token</span>
    <input id="token" type="text" spellcheck="false" autocapitalize="off" autocomplete="off"
           placeholder="paste the 24-hex token here">
    <button class="primary" id="start">Connect</button>
    <button id="stop" class="hidden">Disconnect</button>
  </div>
  <div class="dim" style="margin-top:8px">The token Arena gave you — the relay expects one starting
    with <code id="tokenhint">__TOKEN_HINT__</code>. The local hop uses <code>bridge.token</code>
    (usually the same value).</div>
  <div class="row" style="margin-top:10px">
    <span class="k">this page:</span> <span id="ctxline" class="dim">checking&#8230;</span>
    <span class="k">preview token:</span> <span id="tkline" class="dim">checking&#8230;</span>
    <button id="opennew">Open in a new tab</button>
  </div>
  <details style="margin-top:10px">
    <summary class="k" style="cursor:pointer">Troubleshooting — and using the bridge without Arena</summary>

    <div class="dim" style="margin-top:10px"><b>1.</b> If the <b>local bridge</b> line is red it is
      usually not running. In your <code>roblox-bridge</code> folder (leave it running):</div>
    <pre id="srccmds">$env:BRIDGE_TOKEN = (Get-Content .\bridge.token -Raw).Trim()
python server.py</pre>

    <div class="dim" style="margin-top:10px"><b>2.</b> If the browser cannot reach it (Chrome blocks public
      pages from calling loopback) or PowerShell gave you parse errors, run the Python client
      instead. Download it next to <code>bridge.token</code> and run —
      from the same folder:</div>
    <pre id="pscmds">python poll_local.py --url __RELAY_URL__</pre>
    <div class="row"><button id="copyps">Copy commands</button>
      <button class="primary" id="savepy">Save poll_local.py</button>
      <button id="savetr">Save .arena-traffic-token</button>
      <button id="saveps">Save relay.ps1</button>
      <button id="saveab">Save ab.ps1</button></div>
    <div class="dim" style="margin-top:6px">These save the file straight from this page (no
      download, no preview token, no filename guessing). Put <code>poll_local.py</code> next to
      <code>bridge.token</code>, then run the command above.</div>
    <div class="dim" style="margin-top:6px">The Python client is plain ASCII and needs no
      PowerShell quoting, so it cannot hit the encoding traps <code>relay.ps1</code> can.</div>

    <div class="dim" style="margin-top:10px"><b>3.</b> Downloads above must happen
      <em>in the browser</em>: this preview URL is token-gated, so <code>curl.exe</code> from
      PowerShell gets the gate JSON instead of the file. The PowerShell client needs the preview
      token: <span id="trafficline">checking&#8230;</span></div>

    <div class="dim" style="margin-top:14px"><b>4. Publishing the game code</b> (if you are doing
      it by hand instead of letting the relay run it). Download both jobs in the browser, then:</div>
    <pre id="pubcmds">python arena_studio.py runfile push_all_dry.lua   REM look first (read-only)
python arena_studio.py runfile push_all.lua       REM publish</pre>
    <div class="row">
      <a class="dl" href="/jobs/push_all_dry.lua" download>push_all_dry.lua</a>
      <a class="dl" href="/jobs/push_all.lua" download>push_all.lua</a>
      <a class="dl" href="/jobs" target="_blank" rel="noopener">all job files</a>
    </div>
    <div class="dim" style="margin-top:6px">Both are ASCII-only; run them in Edit mode, not during
      a playtest.</div>
  </details>
</div>

<div class="card">
  <div class="row big"><span class="dot" id="dot-relay"></span><span id="relay-state">relay: idle</span></div>
  <div class="row big" style="margin-top:6px"><span class="dot" id="dot-bridge"></span><span id="bridge-state">local bridge: not checked</span></div>
  <div class="row big" style="margin-top:6px"><span class="dot" id="dot-studio"></span><span id="studio-state">Studio: unknown</span></div>
  <div class="row" style="margin-top:12px">
    <button id="check">Check bridge</button>
    <button id="test">Run a ping through Arena</button>
    <span class="k" id="counts"></span>
  </div>
</div>

<div class="card"><div id="log"></div></div>
<div class="dim">Jobs: Arena → this tab → <code>127.0.0.1:8077</code> → Studio plugin.
 Nothing here is stored after the session; the token lives in this tab only.</div>
</div>
<script>
const $ = (s) => document.querySelector(s);
const KEY = 'arena-relay-token';
const BRIDGE_CANDIDATES = ['http://127.0.0.1:8077', 'http://localhost:8077'];
let BRIDGE = BRIDGE_CANDIDATES[0];   // whichever answers first (set by checkBridge)
const params = new URLSearchParams(location.search);

// The preview gate's token is not always in this frame's query string, and the
// user has no address bar to read it from. Look everywhere a browser can.
function discoverTraffic() {
  const hits = [];
  const add = (value, where) => { if (value) hits.push({value: String(value), where}); };

  add(params.get('e2b-traffic-access-token'), 'this URL');
  try {
    add(new URLSearchParams(location.hash.replace(/^#/, '')).get('e2b-traffic-access-token'), 'URL hash');
  } catch (e) {}
  try {
    document.cookie.split(';').forEach((c) => {
      const i = c.indexOf('=');
      if (i < 0) return;
      const k = c.slice(0, i).trim(), v = c.slice(i + 1).trim();
      if (v && /traffic|access.token|e2b/i.test(k)) add(decodeURIComponent(v), 'cookie ' + k);
    });
  } catch (e) {}
  try {
    for (const store of [sessionStorage, localStorage]) {
      for (let i = 0; i < store.length; i++) {
        const k = store.key(i), v = store.getItem(k);
        if (v && /traffic/i.test(k)) add(v, 'storage ' + k);
      }
    }
  } catch (e) {}
  try {
    const ref = document.referrer || '';
    const fromRef = new URL(ref).searchParams.get('e2b-traffic-access-token');
    add(fromRef, 'the Arena page containing this frame');
  } catch (e) {}
  return hits;
}
const FOUND = discoverTraffic();
const TRAFFIC = FOUND.length ? FOUND[0].value : '';
const TRAFFIC_WHERE = FOUND.length ? FOUND[0].where : '';
const IS_TOP = (() => { try { return window.top === window; } catch (e) { return false; } })();
let token = params.get('token') || sessionStorage.getItem(KEY) || '';
let es = null, poll = null, healthTimer = null, running = false, seen = new Set();
let stats = {ok: 0, bad: 0, n: 0};
const shown = [];

function log(text, cls) {
  const stamp = new Date().toLocaleTimeString();
  shown.push(`<span class="dim">${stamp}</span>  ${cls ? `<span class="${cls}">${text}</span>` : text}`);
  if (shown.length > 200) shown.shift();
  $('#log').innerHTML = shown.join('\n');
  $('#log').scrollTop = $('#log').scrollHeight;
}
function setLine(id, state, text) {
  $('#dot-' + id).className = 'dot ' + state;
  $('#' + id + '-state').textContent = text;
}
function withTraffic(url) {
  if (!TRAFFIC) return url;
  return url + (url.includes('?') ? '&' : '?') + 'e2b-traffic-access-token=' + encodeURIComponent(TRAFFIC);
}
function esc(s) { return String(s).replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function mask(t) { return !t ? '(none)' : (t.length <= 8 ? t : t.slice(0, 6) + '\u2026' + t.slice(-2)) + ` (${t.length} chars)`; }

async function readJSON(res) {
  const text = await res.text();
  try { return {status: res.status, data: JSON.parse(text)}; }
  catch (e) { return {status: res.status, data: {raw: text.slice(0, 400)}}; }
}

// --- local bridge (the user's own machine) ---------------------------------
async function bridgeCall(path, opts) {
  const url = BRIDGE + path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(token);
  return readJSON(await fetch(url, opts));
}

async function bridgeProbe(url) {
  const saved = BRIDGE;
  BRIDGE = url;
  try {
    const r = await bridgeCall('/api/health');
    return {url, r};
  } finally {
    BRIDGE = saved;
  }
}

async function checkBridge(quiet) {
  if (!token) return;
  try {
    // browsers are fussy: one spelling of loopback can be blocked while the
    // other is not, so ask both and keep the one that answers.
    let r = null, lastError = null;
    for (const candidate of BRIDGE_CANDIDATES) {
      try {
        const probe = await bridgeProbe(candidate);
        if (probe.r.status === 200) { r = probe.r; BRIDGE = candidate; break; }
        r = probe.r;
      } catch (err) { lastError = err; }
    }
    if (!r) throw (lastError || new Error('no loopback address answered'));
    if (r.status !== 200) {
      setLine('bridge', 'bad', `local bridge: HTTP ${r.status} ${JSON.stringify(r.data).slice(0, 120)}`);
      if (!quiet) log(`local bridge said HTTP ${r.status}: ${esc(JSON.stringify(r.data))}`, 'err');
      return null;
    }
    const d = r.data;
    setLine('bridge', 'on', 'local bridge: reachable on 127.0.0.1:8077');
    setLine('studio', d.studio_connected ? 'on' : 'wait',
      d.studio_connected
        ? `Studio: connected — "${d.studio && d.studio.place ? d.studio.place : '?'}" (plugin ${d.studio && d.studio.client ? d.studio.client : 'v?'})`
        : 'Studio: bridge up, but the plugin has not polled (Studio closed or plugin off)');
    report({ok: true, health: d});
    return d;
  } catch (err) {
    const detail = (err && (err.name ? err.name + ': ' : '') + (err.message || err)) || String(err);
    setLine('bridge', 'bad', `local bridge: unreachable from this page (${detail})`);
    if (!quiet) {
      log(`cannot reach ${BRIDGE_CANDIDATES.join(' or ')} from the browser -- ${esc(detail)}`, 'err');
      log('a failed fetch cannot say which of these is wrong, so check both:', 'warn');
      log('  1. is server.py running on that machine?  (python server.py, leave it running)', 'warn');
      log('     it prints "[bridge] ... (token disabled)" when started without a token - that is fine', 'warn');
      log('  2. is this page top-level? ' + (IS_TOP
        ? 'yes - and if server.py is up, the browser is blocking local requests: allow it, or use '
          + 'the command-line client instead.'
        : 'no - this page is inside the Arena frame, which browsers often forbid from calling '
          + 'localhost. Click Open in a new tab above, then press Connect there.'), 'warn');
      log('  3. Chrome may be asking permission to reach devices on your network - if a prompt', 'warn');
      log('     appeared, click Allow. Otherwise: chrome://settings/content/localNetworkAccess', 'warn');
      log('     and add this site. In Edge: edge://settings (search "local network").', 'warn');
    }
    report({ok: false, error: detail, page_top_level: IS_TOP});
    return null;
  }
}

async function report(payload) {
  try {
    await fetch(withTraffic('/api/report?token=' + encodeURIComponent(token)), {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify(Object.assign(
        {where: 'browser', ua: navigator.userAgent.slice(0, 60),
         traffic: TRAFFIC || undefined, traffic_where: TRAFFIC_WHERE || undefined,
         page_top_level: IS_TOP},
        payload)),
    });
  } catch (e) { /* reporting is best-effort */ }
}

// --- jobs ------------------------------------------------------------------
async function runJob(job) {
  if (!job || seen.has(job.id)) return;
  seen.add(job.id);
  stats.n++;
  log(`job ${esc(job.id)} ${esc(job.type)} — running in Studio…`);
  let response = null, error = null;
  const t0 = performance.now();
  try {
    const r = await bridgeCall('/api/jobs', {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({type: job.type, payload: job.payload, note: job.note,
                            wait: job.wait || 60}),
    });
    response = r.data;
    if (r.status !== 200) error = `local bridge HTTP ${r.status}`;
  } catch (err) {
    error = 'local bridge unreachable: ' + (err && err.message || err);
  }
  const ms = Math.round(performance.now() - t0);
  const ok = !error && response && response.status === 'done' &&
             !(response.result && response.result.error);
  if (ok) { stats.ok++; } else { stats.bad++; }
  counters();
  log(`job ${esc(job.id)} ${esc(job.type)} — ${ok ? 'ok' : 'FAILED'} in ${ms} ms` +
      (error ? ` (${esc(error)})` : ''), ok ? 'ok' : 'err');
  try {
    await fetch(withTraffic('/api/result?token=' + encodeURIComponent(token)), {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({id: job.id, response: response, error: error, ms: ms}),
    });
  } catch (err) {
    log(`could not return the result to Arena: ${esc(err && err.message || err)}`, 'err');
  }
}

function counters() {
  $('#counts').textContent = `${stats.n} job(s) · ${stats.ok} ok · ${stats.bad} failed`;
}

// --- relay link ------------------------------------------------------------
async function connect() {
  if (!token || running) return;
  running = true;
  sessionStorage.setItem(KEY, token);
  $('#gate').classList.add('hidden');
  $('#stop').classList.remove('hidden');
  setLine('relay', 'wait', 'relay: connecting…');
  log(`using token ${mask(token)}`);
  try {
    const probe = await readJSON(await fetch(withTraffic('/api/state?token=' + encodeURIComponent(token))));
    if (probe.status === 401) {
      setLine('relay', 'bad', 'relay: token rejected (401)');
      log(`the relay rejected this token (HTTP 401). It wants the 24-hex token Arena showed you; this tab has ${mask(token)}.`, 'err');
      $('#gate').classList.remove('hidden');
      $('#stop').classList.add('hidden');
      running = false;
      return;
    }
    log('relay accepted the token.');
  } catch (err) {
    log('token check failed (relay unreachable?): ' + esc(err && err.message || err), 'err');
  }
  checkBridge(false);
  openStream();
  if (poll) clearInterval(poll);
  poll = setInterval(() => { if (!es || es.readyState !== 1) pollJobs(); }, 2000);
  if (healthTimer) clearInterval(healthTimer);
  healthTimer = setInterval(() => checkBridge(true), 30000);
}

let backoff = 1000;
function openStream() {
  try { if (es) es.close(); } catch (e) {}
  const url = withTraffic('/events?token=' + encodeURIComponent(token) +
                          '&client=browser-' + Math.random().toString(36).slice(2, 10));
  es = new EventSource(url);
  es.onopen = () => { backoff = 1000; setLine('relay', 'on', 'relay: connected to Arena (live)'); };
  es.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === 'job') runJob(msg.job);
  };
  es.onerror = () => {
    setLine('relay', 'wait', 'relay: reconnecting…');
    try { es.close(); } catch (e) {}
    es = null;
    backoff = Math.min(backoff * 2, 15000);
    setTimeout(() => { if (running) openStream(); }, backoff);
  };
}

async function pollJobs() {
  try {
    const r = await readJSON(await fetch(withTraffic('/api/jobs?token=' +
      encodeURIComponent(token) + '&client=browser-poll')));
    if (r.status === 401) {
      setLine('relay', 'bad', 'relay: token rejected (401)');
      return;
    }
    setLine('relay', 'on', 'relay: connected to Arena (polling)');
    (r.data.jobs || []).forEach(runJob);
  } catch (err) {
    setLine('relay', 'bad', 'relay: cannot reach Arena (' + (err && err.message || err) + ')');
  }
}

function disconnect() {
  running = false;
  if (es) { try { es.close(); } catch (e) {} es = null; }
  if (poll) { clearInterval(poll); poll = null; }
  if (healthTimer) { clearInterval(healthTimer); healthTimer = null; }
  setLine('relay', 'bad', 'relay: disconnected');
  $('#gate').classList.remove('hidden');
  $('#stop').classList.add('hidden');
  log('disconnected.', 'warn');
}

// --- wiring ----------------------------------------------------------------
$('#bridgeurl').textContent = BRIDGE;
$('#start').onclick = () => {
  token = ($('#token').value || '').trim();
  if (!token) { log('paste the token first.', 'err'); return; }
  connect();
};
$('#stop').onclick = disconnect;
// --- hand the user the client files, generated in the browser -------------
const FILES = {
  'poll_local.py': __POLL_LOCAL_JS__,
  'relay.ps1': __RELAY_PS1_JS__,
  'ab.ps1': __AB_PS1_JS__,
  '.arena-traffic-token': TRAFFIC || '',
};
if (!TRAFFIC) {
  $('#savetr').disabled = true;
  $('#savetr').title = 'the preview token is not visible in this tab';
}
function saveFile(name) {
  const text = FILES[name];
  if (!text || text.startsWith('(missing')) {
    log('this relay build does not have ' + name + ' embedded.', 'err');
    return;
  }
  const blob = new Blob([text], {type: 'text/plain;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  log(`saved ${name} (${text.length} bytes) - check your Downloads folder.`, 'ok');
}
$('#opennew').onclick = () => {
  const w = window.open(location.href, '_blank', 'noopener');
  if (!w) { log('the browser blocked the popup - allow popups for this site.', 'err'); }
  else { log('opened a new tab: use that one (it has an address bar and is top-level).', 'ok'); }
};
$('#savepy').onclick = () => saveFile('poll_local.py');
$('#savetr').onclick = () => saveFile('.arena-traffic-token');
$('#saveps').onclick = () => saveFile('relay.ps1');
$('#saveab').onclick = () => saveFile('ab.ps1');
$('#copyps').onclick = () => {
  const txt = $('#pscmds').textContent;
  navigator.clipboard.writeText(txt).then(
    () => log('PowerShell commands copied.', 'ok'),
    () => log('copy failed — select the text manually.', 'err'));
};
$('#check').onclick = () => checkBridge(false);
$('#test').onclick = async () => {
  if (!token) { log('connect first.', 'err'); return; }
  log('asking Arena for a ping job…');
  try {
    const r = await readJSON(await fetch(withTraffic('/api/jobs?token=' +
      encodeURIComponent(token)), {
      method: 'POST', headers: {'content-type': 'application/json'},
      body: JSON.stringify({type: 'ping', note: 'relay self-test', wait: 30}),
    }));
    if (r.status === 401) {
      log(`the relay rejected this token (401) — this tab has ${mask(token)}.`, 'err');
      return;
    }
    const t = r.data.result && r.data.result.returned;
    log(r.data.status === 'done'
      ? `ping ok: ${esc(String(t || JSON.stringify(r.data.result || {})).slice(0, 200))}` 
      : `ping failed: ${esc(JSON.stringify(r.data).slice(0, 200))}`, r.data.status === 'done' ? 'ok' : 'err');
  } catch (err) { log('self-test failed: ' + esc(err && err.message || err), 'err'); }
};
// the PowerShell fallback commands, filled in for this environment
(function fillPS() {
  const sep = TRAFFIC ? '?e2b-traffic-access-token=' + encodeURIComponent(TRAFFIC) : '';
  const trafficArg = TRAFFIC ? ` -TrafficToken ${TRAFFIC}` : '';
  const known = /^[0-9a-f]{24}$/.test(params.get('token') || '') ? params.get('token') : '';
  const tokArg = known ? ` --token ${known}` : ' --token <the 24-hex token>';
  $('#pscmds').textContent =
    `REM from your repo checkout (the ZIP never ships bridge.token):` +
    `\nREM give it one first if you have another working copy:` +
    `\nREM   Copy-Item "<other>\\roblox-bridge\\bridge.token" .\\roblox-bridge\\` +
    `\npython .\\relay\\poll_local.py --url ${location.origin}${tokArg}${trafficArg}` +
    `\n\nREM PowerShell alternative (same job, ASCII-only):` +
    `\n.\\relay.ps1 -Url ${location.origin} -Token ${known || '<token>'}${trafficArg}`;
  $('#dllink').href = '/relay.ps1' + sep;
  $('#dlpoll').href = '/poll_local.py' + sep;
  $('#dlab').href = '/ab.ps1' + sep;
  const tok = /^[0-9a-f]{24}$/.test(params.get('token') || '') ? params.get('token') : '<token>';
  $('#pubcmds').textContent =
    `$env:BRIDGE_URL = "${BRIDGE}"\n` +
    `$env:BRIDGE_TOKEN = ${known ? '"' + known + '"' : '(Get-Content .\\bridge.token -Raw).Trim()'}\n` +
    'python arena_studio.py health\n' +
    'python arena_studio.py runfile push_all_dry.lua\n' +
    'python arena_studio.py runfile push_all.lua';
  const line = $('#trafficline');
  if (TRAFFIC) {
    line.textContent = '';
    const code = document.createElement('code');
    code.textContent = (TRAFFIC.length > 14 ? TRAFFIC.slice(0, 8) + '\u2026' + TRAFFIC.slice(-4) : TRAFFIC);
    const btn = document.createElement('button');
    btn.textContent = 'Copy preview token';
    btn.onclick = () => navigator.clipboard.writeText(TRAFFIC).then(
      () => log('preview token copied.', 'ok'),
      () => log('copy failed - select it manually.', 'err'));
    line.append('found in ' + TRAFFIC_WHERE + ': ', code, ' ', btn);
  } else {
    line.innerHTML = '<b>not visible from this page</b>. Click ' +
      '<b>Open in a new tab</b> above: the new tab has an address bar, so you can copy the ' +
      '<code>e2b-traffic-access-token</code> value from it (or press F12 there and run ' +
      '<code>copy(location.href)</code>). You do not need it for the browser client - ' +
      'only for the command-line ones.';
  }
  $('#ctxline').innerHTML = IS_TOP
    ? '<span class="ok">top-level tab</span> (localhost requests allowed)'
    : '<span class="warn">inside the Arena frame</span> (localhost requests can be blocked; ' +
      'use Open in a new tab if the bridge line below is red)';
  $('#tkline').innerHTML = TRAFFIC
    ? '<span class="ok">found</span> in ' + TRAFFIC_WHERE
    : '<span class="warn">not visible here</span>';
})();
$('#token').value = params.get('token') || '';
if (token) { connect(); } else { log('waiting for the bridge token…', 'dim'); }
window.addEventListener('beforeunload', () => { running = false; if (es) es.close(); });
</script></body></html>
"""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ArenaStudioRelay/" + VERSION

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt, *args):
        return

    def _qs(self):
        return parse_qs(urlparse(self.path).query)

    def _authorized(self, qs):
        if not TOKEN:
            return True
        supplied = (qs.get("token", [None])[0]
                    or self.headers.get("x-relay-token")
                    or self.headers.get("e2b-traffic-access-token"))
        if supplied == TOKEN:
            return True
        auth = self.headers.get("authorization") or ""
        return auth == f"Bearer {TOKEN}"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _body(self):
        try:
            length = int(self.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"_raw": raw[:500].decode("utf-8", "replace")}

    def _note_page_request(self):
        """Record how the browser reached us.

        Two jobs: keep the preview gate's token when the proxy hands it over
        (it arrives as the E2b-Traffic-Access-Token request header on
        gate-satisfied requests, which is how the user gets a command-line
        client without ever seeing an address bar), and record the *shape* of
        the request - header names, cookie names, a redacted referer - so the
        agent can tell how the gate authenticates without logging secrets.
        """
        names = sorted(self.headers.keys())
        traffic_value = (self.headers.get("e2b-traffic-access-token") or "").strip()
        traffic_header = bool(traffic_value) or any("traffic" in n.lower() for n in names)
        cookie_names = []
        for raw in self.headers.get_all("cookie") or []:
            for part in raw.split(";"):
                name = part.split("=")[0].strip()
                if name:
                    cookie_names.append(name)
        referer = self.headers.get("referer") or ""
        if "?" in referer:
            referer = referer.split("?", 1)[0] + "?<redacted>"
        if traffic_value:
            # The E2B proxy hands the preview token to us as a request header on
            # every gate-satisfied request. Storing it means the user never has
            # to hunt for it: the agent can hand over a command line that
            # already carries --traffic-token.
            with LOCK:
                STATE["traffic"] = traffic_value
                STATE["traffic_from"] = "request header on page load"
        with LOCK:
            STATE["last_page_request"] = {
                "at": now(),
                "host": self.headers.get("host"),
                "header_names": names,
                "traffic_header_present": traffic_header,
                "cookie_names": sorted(set(cookie_names)),
                "referer": referer or None,
                "forwarded_for": self.headers.get("x-forwarded-for"),
            }
        print(f"[relay] page request: {len(names)} header(s), "
              f"traffic_token={'CAPTURED' if traffic_value else 'absent'}"
              + (f" (...{traffic_value[-4:]})" if traffic_value else "")
              + f", cookies={sorted(set(cookie_names)) or 'none'}", flush=True)

    # -- verbs -------------------------------------------------------------
    def do_OPTIONS(self):
        self._send(200, b"")

    def do_GET(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        path = url.path.rstrip("/") or "/"

        if path == "/healthz":
            return self._send(200, {"ok": True, "version": VERSION})

        if path == "/":
            self._note_page_request()
            html = (PAGE
                    .replace("__RELAY_URL__", STATE.get("url") or self.headers.get("host", ""))
                    .replace("__TOKEN_HINT__", (TOKEN[:6] + "\u2026") if TOKEN else "(none)")
                    .replace("__POLL_LOCAL_JS__", inline_file(HERE / "poll_local.py"))
                    .replace("__RELAY_PS1_JS__", inline_file(HERE / "relay.ps1"))
                    .replace("__AB_PS1_JS__", inline_file(HERE.parent / "roblox-bridge" / "ab.ps1")))
            return self._send(200, html, "text/html; charset=utf-8")

        # serve the repo's job files (browser download, no GitHub auth needed)
        if path.startswith("/jobs"):
            rel = path.lstrip("/")
            jobs_root = (HERE.parent / "jobs").resolve()
            target = (jobs_root / rel[len("jobs/"):]).resolve() if rel != "jobs" else jobs_root
            if rel == "jobs":
                listing = sorted(f.name for f in jobs_root.glob("*.lua"))
                body = "\n".join(f'<li><a href="/jobs/{name}">{name}</a></li>' for name in listing)
                return self._send(200, f"<html><body><h3>jobs/</h3><ul>{body}</ul></body></html>",
                                  "text/html; charset=utf-8")
            if jobs_root in target.parents and target.is_file():
                ctype = "text/plain; charset=utf-8" if target.suffix == ".lua" else "application/octet-stream"
                return self._send(200, target.read_bytes(), ctype)
            return self._send(404, {"error": "no such job file"})

        if path in ("/relay.ps1", "/poll_local.py", "/ab.ps1", "/arena_studio.py"):
            if path in ("/relay.ps1", "/poll_local.py"):
                script = HERE / path.lstrip("/")
            else:
                script = HERE.parent / "roblox-bridge" / path.lstrip("/")
            if script.exists():
                data = script.read_bytes()
                if script.suffix == ".ps1":
                    # BOM on purpose: Windows PowerShell 5.1 decodes .ps1 as
                    # ANSI unless it starts with one, which turns any
                    # non-ASCII byte into mojibake and hard parse errors
                    # (bitten 2026-10-08). Python files do not need it.
                    data = b"\xef\xbb\xbf" + data
                return self._send(200, data, "text/plain; charset=utf-8")
            return self._send(404, {"error": path + " not found"})

        if path == "/favicon.ico":
            return self._send(200, b"", "image/x-icon")

        if path == "/events":
            if not self._authorized(qs):
                return self._send(401, {"error": "unauthorized"})
            return self.serve_sse(qs)

        if not self._authorized(qs):
            return self._send(401, {"error": "unauthorized"})

        if path == "/api/jobs":
            client = qs.get("client", [new_id("c")])[0]
            touch_client(client, qs.get("kind", ["poll"])[0])
            jobs = deliver(client, qs.get("kind", ["poll"])[0])
            if jobs:
                print(f"[relay] handed {len(jobs)} job(s) to {client}", flush=True)
            return self._send(200, {"jobs": jobs, "now": now()})

        if path == "/api/state":
            return self._send(200, self.state())

        if path.startswith("/api/result/"):
            job_id = path.rsplit("/", 1)[-1]
            job = JOBS.get(job_id)
            if not job:
                return self._send(404, {"error": "no such job"})
            return self._send(200, job)

        return self._send(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        qs = parse_qs(url.query)
        path = url.path.rstrip("/") or "/"

        if not self._authorized(qs):
            return self._send(401, {"error": "unauthorized"})

        body = self._body()

        # agent enqueues work
        if path == "/api/jobs":
            kind = body.get("type")
            if not kind:
                return self._send(400, {"error": "type is required"})
            job = enqueue(kind, body.get("payload"), body.get("note"),
                          body.get("wait") or 0, source=body.get("source", "agent"))
            wait = float(job["wait"] or 0)
            if wait > 0:
                deadline = now() + wait
                while now() < deadline:
                    with LOCK:
                        cur = JOBS[job["id"]]
                        if cur["status"] in ("done", "error"):
                            return self._send(200, cur)
                    time.sleep(0.2)
                with LOCK:
                    cur = dict(JOBS[job["id"]])
                cur["timeout"] = True
                return self._send(200, cur)
            return self._send(201, summary_job(job))

        # client reports a finished job
        if path == "/api/result":
            job_id = body.get("id")
            with LOCK:
                job = JOBS.get(job_id)
            if not job:
                return self._send(404, {"error": "no such job"})
            response = body.get("response")
            error = body.get("error")
            local_status = (response or {}).get("status")
            if error:
                status = "error"
            elif local_status == "done":
                status = "done"
            elif local_status in ("queued", "running"):
                # fire-and-forget job (wait: 0): accepted, answer never comes back
                status = "accepted"
            else:
                status = "error"
            ok = status in ("done", "accepted")
            with LOCK:
                job["response"] = response
                job["error"] = error
                job["ok"] = ok
                job["status"] = status
                job["finished_at"] = now()
                job["ms"] = body.get("ms")
            log_event("result", {"id": job_id, "ok": ok, "status": status, "error": error})
            print(f"[relay] result {job_id} -> {status}", flush=True)
            return self._send(200, {"ack": True})

        # client reports the local bridge's health
        if path == "/api/report":
            traffic = (body.get("traffic") or "").strip()
            with LOCK:
                STATE["report"] = {
                    "at": now(),
                    "where": body.get("where"),
                    "ok": body.get("ok"),
                    "error": body.get("error"),
                    "health": body.get("health"),
                    "ua": body.get("ua"),
                }
                if traffic:
                    # the page can see the preview gate's token in its own URL;
                    # recording it lets the agent hand the user a command that
                    # already carries --traffic-token (no copy-paste hunt).
                    STATE["traffic"] = traffic
            log_event("report", STATE["report"])
            print(f"[relay] report from {body.get('where')}: "
                  f"{'ok' if body.get('ok') else body.get('error')}"
                  + (f" (preview token seen, ...{traffic[-6:]})" if traffic else ""),
                  flush=True)
            return self._send(200, {"ack": True})

        return self._send(404, {"error": "not found"})

    # -- SSE ---------------------------------------------------------------
    def serve_sse(self, qs):
        client = qs.get("client", [new_id("s")])[0]
        touch_client(client, "browser")
        q = queue.Queue(maxsize=256)
        with LOCK:
            SSE_SUBSCRIBERS.append(q)
        print(f"[relay] SSE client {client} connected", flush=True)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b": arena studio relay\n\n")
            self.wfile.flush()

            for job in deliver(client, "browser"):
                self.wfile.write(("data: " + json.dumps({"type": "job", "job": job}) + "\n\n").encode())
            self.wfile.flush()

            while True:
                try:
                    payload = q.get(timeout=12)
                except queue.Empty:
                    payload = json.dumps({"type": "heartbeat", "t": now()})
                    touch_client(client, "browser")
                self.wfile.write(("data: " + payload + "\n\n").encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            pass
        finally:
            with LOCK:
                if q in SSE_SUBSCRIBERS:
                    SSE_SUBSCRIBERS.remove(q)
            print(f"[relay] SSE client {client} gone", flush=True)

    # -- status view -------------------------------------------------------
    def state(self):
        with LOCK:
            jobs = [summary_job(JOBS[i]) for i in list(ORDER)][::-1]
            clients = [{"id": cid, "kind": c["kind"],
                        "last_seen": round(now() - c["last_seen"], 1)}
                       for cid, c in CLIENTS.items()]
            report = STATE.get("report")
            counts = {}
            for job in JOBS.values():
                counts[job["status"]] = counts.get(job["status"], 0) + 1
        return {
            "ok": True,
            "version": VERSION,
            "relay_url": STATE.get("url"),
            "bridge_url": STATE.get("bridge_url"),
            "started": STATE.get("started"),
            "sse_clients": len(SSE_SUBSCRIBERS),
            "clients": sorted(clients, key=lambda c: c["last_seen"]),
            "counts": counts,
            "last_report": report,
            "preview_token": STATE.get("traffic") or None,
            "preview_token_source": STATE.get("traffic_from"),
            "last_page_request": STATE.get("last_page_request"),
            "jobs": jobs[:40],
        }


def main():
    global TOKEN, EVENT_LOG
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--token", default=os.environ.get("RELAY_TOKEN", ""))
    ap.add_argument("--url", default=os.environ.get("RELAY_URL", ""),
                    help="public preview URL (for display only)")
    ap.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    ap.add_argument("--bridge-url", default="http://127.0.0.1:8077")
    args = ap.parse_args()

    state_dir = Path(args.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    config_path = state_dir / "relay.json"

    token = args.token
    if not token and config_path.exists():
        try:
            token = json.loads(config_path.read_text()).get("token", "")
        except (OSError, json.JSONDecodeError):
            token = ""
    if not token:
        raise SystemExit("no token: pass --token or set RELAY_TOKEN")
    TOKEN = token

    EVENT_LOG = state_dir / "events.jsonl"
    STATE["started"] = now()
    STATE["url"] = args.url
    STATE["bridge_url"] = args.bridge_url
    config_path.write_text(json.dumps({
        "token": token, "port": args.port, "url": args.url,
        "bridge_url": args.bridge_url, "started": STATE["started"],
    }, indent=2) + "\n")
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass

    threading.Thread(target=janitor, daemon=True).start()

    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    httpd.daemon_threads = True
    print(f"[relay] v{VERSION} listening on 0.0.0.0:{args.port}", flush=True)
    if args.url:
        print(f"[relay] public URL: {args.url}", flush=True)
    print(f"[relay] state dir: {state_dir}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[relay] bye", flush=True)


if __name__ == "__main__":
    main()
