#!/usr/bin/env node
/*
 * dashboard_harness.js — DOM-free test harness for the Control Center.
 *
 * The dashboard used to be ~3,000 lines of HTML/CSS/JS inside a Python raw
 * string, so nothing about its *behaviour* was testable: the only check the
 * suite could make was `node --check` on the extracted script. That is exactly
 * why the `esc()` attribute-injection hole (P0-S3) shipped. This harness loads
 * `static/dashboard.js` into a stub DOM, then asserts the security invariants:
 *
 *   1. `esc()` escapes every character that can terminate an HTML text node, an
 *      attribute value (either quote) or a JS template literal.
 *   2. `studioCard()` renders a hostile peer id / place name as inert text —
 *      no `onclick`, no unescaped `'`/`"`, no live script.
 *   3. `static/index.html` contains no inline event handler and no inline
 *      `<script>` (both are refused by the `script-src 'self'` CSP anyway).
 *   4. Every `data-act` in the markup resolves to a real function, and every
 *      `data-args` is valid JSON — a typo'd action is a dead button, which is
 *      the failure mode delegated handlers introduce.
 *
 * Usage: node tools/dashboard_harness.js [--json]
 * Exit:  0 when every check passes, 1 otherwise.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HERE = path.resolve(__dirname, '..');
const STATIC = path.join(HERE, 'static');
const wantJson = process.argv.includes('--json');

// ---------------------------------------------------------------------------
// Stub DOM — just enough for the module to evaluate and for pure render
// functions (esc, actData, studioCard, …) to run.
// ---------------------------------------------------------------------------
function makeElement(tag) {
  const attrs = {};
  const el = {
    tagName: String(tag || 'div').toUpperCase(),
    style: {},
    classList: {
      _s: new Set(),
      add(...c) { c.forEach(x => this._s.add(x)); },
      remove(...c) { c.forEach(x => this._s.delete(x)); },
      contains(c) { return this._s.has(c); },
      toggle(c) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); },
    },
    children: [],
    dataset: {},
    textContent: '',
    innerHTML: '',
    value: '',
    checked: false,
    disabled: false,
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(attrs, k); },
    removeAttribute(k) { delete attrs[k]; },
    appendChild(child) { this.children.push(child); return child; },
    removeChild(child) { this.children = this.children.filter(c => c !== child); return child; },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    closest() { return null; },
    addEventListener() {},
    removeEventListener() {},
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; },
    focus() {},
    blur() {},
    click() {},
  };
  return el;
}

const listeners = {};
const document = {
  _els: new Map(),
  getElementById(id) {
    if (!this._els.has(id)) this._els.set(id, makeElement('div'));
    return this._els.get(id);
  },
  createElement: makeElement,
  querySelectorAll: () => [],
  querySelector: () => null,
  addEventListener(type) { (listeners[type] = listeners[type] || []).push(true); },
  removeEventListener() {},
  body: makeElement('body'),
  documentElement: makeElement('html'),
  execCommand: () => true,
};

const sandbox = {
  document,
  console,
  setTimeout: () => 0,
  setInterval: () => 0,
  clearTimeout: () => {},
  clearInterval: () => {},
  // Nothing in the harness may touch the network; a stub that throws makes an
  // accidental fetch a loud failure instead of a hang.
  fetch: () => { throw new Error('dashboard_harness: fetch is not available'); },
  navigator: { clipboard: { writeText: async () => {} } },
  location: { href: 'http://127.0.0.1:8077/', origin: 'http://127.0.0.1:8077',
              pathname: '/', search: '', hash: '', reload() {} },
  localStorage: (() => { const m = new Map(); return {
    getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)),
    removeItem: k => m.delete(k), clear: () => m.clear() }; })(),
  history: { replaceState() {} },
  URL,
  URLSearchParams,
  EventSource: function () { this.close = () => {}; },
  Image: function () { return makeElement('img'); },
  performance: { now: () => 0 },
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

// ---------------------------------------------------------------------------
// Load the dashboard module without its bootstrap.
// ---------------------------------------------------------------------------
function loadDashboard() {
  const file = path.join(STATIC, 'dashboard.js');
  let src = fs.readFileSync(file, 'utf8');
  // `initBoot()` needs fetch + a live page; cut the bootstrap and keep the
  // listener registration, which is what the delegation checks exercise.
  const cut = src.indexOf('initBoot().then(');
  const bootstrap = cut >= 0 ? src.slice(cut) : '';
  if (cut >= 0) src = src.slice(0, cut);
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox, { filename: 'dashboard.js' });
  // Top-level `const`/`let` live in the realm's global *lexical* scope, not on
  // the sandbox object, so pull the ones the checks need out explicitly.
  const expose = vm.runInContext(`({
    esc: typeof esc === 'function' ? esc : null,
    ESC_MAP: typeof ESC_MAP === 'object' ? ESC_MAP : null,
    ACT_ADAPTER: typeof ACT_ADAPTER === 'object' ? ACT_ADAPTER : null,
    actData: typeof actData === 'function' ? actData : null,
    actDataOn: typeof actDataOn === 'function' ? actDataOn : null,
    runAction: typeof runAction === 'function' ? runAction : null,
    resolveAction: typeof resolveAction === 'function' ? resolveAction : null,
  })`, sandbox, { filename: 'dashboard-expose.js' });
  Object.assign(sandbox, expose);
  return { sandbox, bootstrap, file };
}

/* Strip comments so a check never matches a sentence *about* the thing it forbids. */
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

/* Find real HTML tags in generated markup and assert none carries an inline
   handler. Entity-encoded payloads are inert, so they are ignored by design —
   what matters is whether a *tag* the browser would parse has `on…=`. */
function inlineHandlersIn(html) {
  const bad = [];
  const tagRe = /<[a-zA-Z][^>]*>/g;
  let m;
  while ((m = tagRe.exec(html)) !== null) {
    const tag = m[0];
    // Attribute values are quoted and `esc()` encodes both quote characters, so
    // a value can never terminate its own attribute. Collapse the values away
    // and inspect only what a parser would treat as attribute *names* — that is
    // what decides whether `on…=` is a handler or just escaped text.
    const skeleton = tag.replace(/=\s*"[^"]*"/g, '=""').replace(/=\s*'[^']*'/g, "=''");
    if (/\son[a-z]+\s*=/i.test(skeleton)) bad.push(tag.slice(0, 140));
    if (/javascript:/i.test(skeleton)) bad.push(`javascript: URL in ${tag.slice(0, 140)}`);
  }
  return bad;
}

// ---------------------------------------------------------------------------
// Checks
// ---------------------------------------------------------------------------
const failures = [];
const checks = [];

function check(name, fn) {
  try {
    const detail = fn();
    checks.push({ name, status: 'pass', detail: detail === undefined ? '' : String(detail) });
  } catch (err) {
    checks.push({ name, status: 'fail', detail: String(err && err.message || err) });
    failures.push(`${name}: ${err && err.message || err}`);
  }
}

function assert(cond, msg) { if (!cond) throw new Error(msg || 'assertion failed'); }

const { sandbox: ctx, bootstrap } = loadDashboard();
const esc = ctx.esc;
const actData = ctx.actData;
const studioCard = ctx.studioCard;
const indexHtml = fs.readFileSync(path.join(STATIC, 'index.html'), 'utf8');

check('esc escapes the full attribute-breaking set', () => {
  const table = [
    ['&', '&amp;'], ['<', '&lt;'], ['>', '&gt;'], ['"', '&quot;'],
    ["'", '&#39;'], ['`', '&#96;'],
  ];
  for (const [raw, want] of table) {
    assert(esc(raw) === want, `esc(${JSON.stringify(raw)}) === ${JSON.stringify(esc(raw))}, want ${want}`);
    assert(esc(`a${raw}b`) === `a${want}b`, `esc did not escape ${JSON.stringify(raw)} in context`);
  }
  assert(esc(null) === '' && esc(undefined) === '', 'esc(null|undefined) must be empty');
  assert(esc(0) === '0' && esc(false) === 'false', 'esc must not drop falsy scalars');
  assert(esc('plain text 123') === 'plain text 123', 'esc must not touch safe text');
  return `${table.length} characters + null/undefined/0/false/safe-text`;
});

check('esc neutralises the attribute-injection payload', () => {
  const payload = "x'); alert(1)//";
  const out = esc(payload);
  assert(!out.includes("'"), `escaped output still contains a raw single quote: ${out}`);
  assert(!out.includes('"'), `escaped output still contains a raw double quote: ${out}`);
  assert(out.includes('&#39;'), `the quote must be entity-encoded, got ${out}`);
  // Inside a single-quoted attribute the payload must not be able to close it.
  const attr = `<button data-x='${out}'>`;
  assert((attr.match(/'/g) || []).length === 2, `attribute delimiters were broken: ${attr}`);
  return out;
});

check('studioCard renders hostile peer/place data as inert markup', () => {
  const hostile = [
    "x'); alert(1)//",
    '"><img src=x onerror=alert(1)>',
    '`${alert(1)}`',
    "</div><script>alert(1)</script>",
  ];
  for (const evil of hostile) {
    const html = studioCard({ peerId: evil, place: evil, dm: 'edit', label: evil,
                              online: true, ageSeconds: 1, pinned: false }, true);
    const handlers = inlineHandlersIn(html);
    assert(handlers.length === 0,
           `studioCard emitted an inline handler for ${JSON.stringify(evil)}: ${handlers[0]}`);
    assert(!/<script/i.test(html), `studioCard emitted a script tag for ${JSON.stringify(evil)}`);
    // No raw angle bracket from the payload may survive: that is what would let
    // it open a tag of its own.
    const rawLt = (html.match(/</g) || []).length;
    const ownTags = (html.match(/<[a-zA-Z/]/g) || []).length;
    assert(rawLt === ownTags, `unescaped '<' survived for ${JSON.stringify(evil)}`);
    assert(!html.includes(evil), `studioCard echoed the raw payload ${JSON.stringify(evil)}`);
    // The payload must survive as text, escaped, not vanish.
    assert(html.includes('&#39;') || html.includes('&quot;') || html.includes('&lt;'),
           `studioCard did not entity-encode ${JSON.stringify(evil)}:\n${html}`);
  }
  // The pin control is delegated data, never an evaluated call expression.
  const pinHtml = studioCard({ peerId: 'edit-abc123', place: 'Demo', dm: 'edit',
                               online: true, ageSeconds: 1, pinned: false }, false);
  assert(pinHtml.includes('data-act="pinStudio"'), `pin button is not delegated:\n${pinHtml}`);
  // actData entity-encodes its JSON, so a peer id can never close the attribute.
  // Decode the way an HTML parser would and check the value round-trips.
  const attr = /data-args='([^']*)'/.exec(pinHtml);
  assert(attr, `pin button carries no data-args:\n${pinHtml}`);
  const decoded = attr[1].replace(/&quot;/g, '"').replace(/&#39;/g, "'")
                         .replace(/&#96;/g, '`').replace(/&lt;/g, '<')
                         .replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  assert(JSON.stringify(JSON.parse(decoded)) === '["edit-abc123"]',
         `peer id did not round-trip through data-args: ${decoded}`);
  return `${hostile.length} payloads escaped; pin button uses data-act/data-args`;
});

check('actData emits JSON arguments that cannot become code', () => {
  const evil = "a',alert(1),'b";
  const out = actData('copyText', evil);
  assert(out.startsWith('data-act="copyText"'), out);
  const json = out.slice(out.indexOf("data-args='") + 11, out.lastIndexOf("'"));
  const parsed = JSON.parse(json.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, '&'));
  assert(Array.isArray(parsed) && parsed[0] === evil, `round-trip lost the value: ${JSON.stringify(parsed)}`);
  assert(!/on\w+=/.test(out), `actData emitted a handler attribute: ${out}`);
  assert(actData('plain') === 'data-act="plain"', 'actData with no args must omit data-args');
  return 'round-trip preserved';
});

check('static/index.html has no inline handlers and no inline script', () => {
  const handlers = indexHtml.match(/\son(?:click|change|input|submit|load|error|focus|blur|key\w+|mouse\w+|drag\w+|touch\w+|wheel|scroll|contextmenu|dblclick)\s*=/gi) || [];
  assert(handlers.length === 0, `${handlers.length} inline handler attribute(s) remain: ${handlers.slice(0, 5).join(', ')}`);
  const scripts = indexHtml.match(/<script[^>]*>/gi) || [];
  for (const tag of scripts) {
    assert(/\bsrc\s*=/.test(tag), `<script> without src is refused by the CSP: ${tag}`);
    assert(!/\son\w+\s*=/i.test(tag), `<script> carries a handler attribute: ${tag}`);
  }
  const jsSrc = fs.readFileSync(path.join(STATIC, 'dashboard.js'), 'utf8');
  const jsHandlers = jsSrc.match(/\son(?:click|change|input|mouseenter|error)\s*=/g) || [];
  assert(jsHandlers.length === 0, `dashboard.js still generates ${jsHandlers.length} inline handler(s)`);
  return `${scripts.length} script tag(s), all src-referenced`;
});

check('every data-act resolves and every data-args parses', () => {
  const names = new Set();
  const re = /data-act="([^"]+)"(?:[^>]*?data-args='([^']*)')?/g;
  let m, count = 0;
  while ((m = re.exec(indexHtml)) !== null) {
    count += 1;
    names.add(m[1]);
    if (m[2]) {
      let parsed;
      try { parsed = JSON.parse(m[2]); } catch (err) {
        throw new Error(`data-args for ${m[1]} is not valid JSON: ${m[2].slice(0, 80)} (${err.message})`);
      }
      assert(Array.isArray(parsed), `data-args for ${m[1]} must be a JSON array: ${m[2].slice(0, 80)}`);
    }
  }
  assert(count > 100, `expected the full delegated surface, saw only ${count} data-act sites`);
  const adapters = ctx.ACT_ADAPTER || {};
  const missing = [...names].filter(n => typeof ctx[n] !== 'function' && typeof adapters[n] !== 'function');
  assert(missing.length === 0, `${missing.length} data-act name(s) resolve to nothing: ${missing.join(', ')}`);
  // The generated markup path must resolve too.
  const generated = [...bootstrap.matchAll(/actData\('([A-Za-z_$][\w$]*)'/g)].map(x => x[1])
    .concat([...fs.readFileSync(path.join(STATIC, 'dashboard.js'), 'utf8')
      .matchAll(/actData(?:On)?\('([A-Za-z_$][\w$]*)'/g)].map(x => x[1]));
  const missingGen = [...new Set(generated)].filter(n => typeof ctx[n] !== 'function' && typeof adapters[n] !== 'function');
  assert(missingGen.length === 0, `generated markup references undefined action(s): ${missingGen.join(', ')}`);
  return `${count} sites, ${names.size} distinct actions, ${new Set(generated).size} generated`;
});

check('delegated listeners are registered for every action event', () => {
  const registered = Object.keys(listeners);
  for (const type of ['click', 'change', 'input', 'mouseover', 'keydown', 'error']) {
    assert(registered.includes(type), `no document-level '${type}' listener was registered`);
  }
  // A keydown-bound control must declare its key, or Enter is assumed.
  const keyed = indexHtml.match(/data-act-on="keydown(?::[^"]*)?"/g) || [];
  for (const attr of keyed) {
    assert(/keydown(?::[\w,+ ]+)?"$/.test(attr), `malformed keydown binding: ${attr}`);
  }
  return `${registered.length} listener types, ${keyed.length} keydown binding(s)`;
});

check('the page keeps its token out of URLs', () => {
  const src = stripComments(fs.readFileSync(path.join(STATIC, 'dashboard.js'), 'utf8'));
  const leaks = src.match(/[?&]token=/g) || [];
  assert(leaks.length === 0, `dashboard.js still puts a token in a query string (${leaks.length} site(s))`);
  assert(!/window\.open\([^)]*\/api\//.test(src), 'a credentialed route must not be opened as a bare URL');
  assert(src.includes('/api/sse-ticket'), 'the SSE stream must redeem a ticket, not a token');
  assert(src.includes('?ticket='), 'EventSource must use the one-shot ticket');
  assert(!/new EventSource\([^)]*getToken\(\)/.test(src), 'EventSource is still given the raw token');
  return 'EventSource uses /api/sse-ticket + ?ticket=';
});

// ---------------------------------------------------------------------------
if (wantJson) {
  process.stdout.write(JSON.stringify({
    ok: failures.length === 0, checks, failures,
    harness: 'tools/dashboard_harness.js', node: process.version,
  }, null, 2) + '\n');
} else {
  for (const row of checks) {
    process.stdout.write(`${row.status === 'pass' ? 'PASS' : 'FAIL'}  ${row.name}` +
                         (row.detail ? `  — ${row.detail}` : '') + '\n');
  }
  if (failures.length) {
    process.stdout.write(`\n${failures.length} dashboard check(s) failed.\n`);
  } else {
    process.stdout.write(`\nAll ${checks.length} dashboard checks passed.\n`);
  }
}
process.exit(failures.length === 0 ? 0 : 1);
