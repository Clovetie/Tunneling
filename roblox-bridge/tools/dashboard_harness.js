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
      /* Real DOM: `toggle(token, force)` adds when force is truthy and removes
         when it is falsy, and only flips when force is omitted. The stub used to
         ignore the second argument, so any dashboard code that passes an explicit
         force — `renderPolicy`'s banner does — was being tested against a DOM that
         does not exist. A stub that mis-models the platform gives false confidence
         in both directions. */
      toggle(c, force) {
        if (force === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); }
        else if (force) { this._s.add(c); }
        else { this._s.delete(c); }
        return this._s.has(c);
      },
    },
    children: [],
    dataset: {},
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
  /* Real DOM `textContent` on a container reads as the concatenation of its
     descendants' text, and assigning it removes every child. The stub used to keep
     a flat string, so `box.textContent = ''; box.appendChild(line)` read back as
     '' — dashboard code that builds a message out of child nodes looked broken in
     the harness while working in a browser. Second instance of the stub
     under-modelling the platform (the first was `classList.toggle`), and the same
     reason to fix the stub rather than the code. */
  let ownText = '';
  Object.defineProperty(el, 'textContent', {
    enumerable: true,
    get() { return ownText + el.children.map(c => (c && c.textContent) || '').join(''); },
    set(v) { ownText = String(v == null ? '' : v); el.children = []; },
  });
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
    renderPolicy: typeof renderPolicy === 'function' ? renderPolicy : null,
    policyUnavailable: typeof policyUnavailable === 'function' ? policyUnavailable : null,
    stdioMcpConfig: typeof stdioMcpConfig === 'function' ? stdioMcpConfig : null,
    localHttpMcpConfig: typeof localHttpMcpConfig === 'function' ? localHttpMcpConfig : null,
    tunnelHttpMcpConfig: typeof tunnelHttpMcpConfig === 'function' ? tunnelHttpMcpConfig : null,
    renderTunnelConfig: typeof renderTunnelConfig === 'function' ? renderTunnelConfig : null,
    isTypingTarget: typeof isTypingTarget === 'function' ? isTypingTarget : null,
    onDashboardKeydown: typeof onDashboardKeydown === 'function' ? onDashboardKeydown : null,
    copyMcpConfig: typeof copyMcpConfig === 'function' ? copyMcpConfig : null,
    POLICY_PILL_CLASS: typeof POLICY_PILL_CLASS === 'object' ? POLICY_PILL_CLASS : null,
    CHORD_TARGETS: typeof CHORD_TARGETS === 'object' ? CHORD_TARGETS : null,
    lastTunnel: () => LAST_TUNNEL,
    policyReport: () => POLICY_REPORT,
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
/* The U1 risk-row check needs the same inline-handler scan the markup check uses,
   applied to generated markup instead of a file. */
function handlersIn(html) { return inlineHandlersIn(html); }
/* `LAST_TUNNEL` is a module-level `let`, so the checks drive it through these two
   accessors rather than reaching into the realm's lexical scope. */
vm.runInContext(`
  globalThis.LAST_TUNNEL_RESET_FOR_TEST = () => { LAST_TUNNEL = { state: 'unknown', publicUrl: '' };
    const box = document.getElementById('tunnel-url'); if (box) box.value = ''; };
  globalThis.LAST_TUNNEL_SET_FOR_TEST = (state, url) => { LAST_TUNNEL = { state, publicUrl: url }; };
`, sandbox, { filename: 'dashboard-test-hooks.js' });
ctx.LAST_TUNNEL_RESET_FOR_TEST = sandbox.LAST_TUNNEL_RESET_FOR_TEST;
ctx.LAST_TUNNEL_SET_FOR_TEST = sandbox.LAST_TUNNEL_SET_FOR_TEST;
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

/* ---------------------------------------------------------------------------
   P5-U1..U3: the posture panel, per-transport configs, and the shortcuts.

   The report literals below are hand-written, and a hand-written literal can drift
   from the function that produces it — so `TestV61DashboardU1U3` in
   test_bridge_mcp.py builds a real `BridgeState.policy_report()` and asserts its key
   set matches `POLICY_REPORT_KEYS` here. Renaming a field therefore fails a Python
   test that says which side to update, instead of silently testing a shape the
   bridge no longer emits.
   --------------------------------------------------------------------------- */
const POLICY_REPORT_KEYS = ['version', 'generatedAt', 'riskLevel', 'actionableRisks',
  'exposed', 'risks', 'auth', 'confirmGate', 'network', 'tunnel', 'surface'];

function policyFixture(over) {
  const base = {
    version: '6.1.0',
    generatedAt: 1795540000.5,
    riskLevel: 'ok',
    actionableRisks: 0,
    exposed: false,
    risks: [],
    auth: { mode: 'scoped', readOnly: false, credentialConfigured: true,
            tokens: { count: 2, active: 2, revoked: 0, expired: 0,
                      byRole: { admin: 1, read: 1 }, roles: ['admin', 'read'] } },
    confirmGate: { version: '6.1.0', mode: 'write', pendingCount: 0, ttlSeconds: 300,
                   allowanceSeconds: 120, counters: {}, enabled: true, gatedToolCount: 34,
                   gatedTools: [], gatedJobKinds: ['run_luau'], destructiveToolCount: 6,
                   destructiveTools: [], destructiveNotGated: [] },
    network: { bind: { host: '127.0.0.1', port: 8077, public: false, loopback: true },
               maxBodyBytes: 33554432,
               cors: { allowlist: [], wildcard: false, policy: 'same-origin',
                       reflectsAllowedRequestOrigin: false, wildcardWhenNoOriginHeader: true } },
    tunnel: { state: 'stopped', publicUrl: '', localUrl: '', flavor: '' },
    surface: { mcpTools: 103, mcpResources: 14, mcpResourceTemplates: 3, mcpPrompts: 10 },
  };
  return Object.assign(base, over || {});
}
const el = id => ctx.document.getElementById(id);

check('U1: the posture banner fires on open auth and on an off gate, and only on those', () => {
  const cases = [
    ['scoped + write gate', policyFixture(), false],
    ['open auth', policyFixture({ riskLevel: 'critical', exposed: true, actionableRisks: 1,
      auth: { mode: 'open', readOnly: false, credentialConfigured: false,
              tokens: { count: 0, active: 0, revoked: 0, expired: 0, byRole: {}, roles: [] } } }), true],
    ['gate off', policyFixture({ riskLevel: 'medium', actionableRisks: 1,
      confirmGate: { mode: 'off', enabled: false, gatedToolCount: 34, gatedJobKinds: ['run_luau'],
                     destructiveNotGated: [], destructiveToolCount: 6 } }), true],
    // A public bind with a credential and a gate on is a *risk row*, not a banner:
    // if the banner fired for every finding, operators would stop reading it.
    ['public bind, scoped, gated', policyFixture({ riskLevel: 'high', exposed: true, actionableRisks: 1,
      risks: [{ id: 'public_bind', severity: 'high', message: 'bound to 0.0.0.0', fix: 'bind loopback' }],
      network: { bind: { host: '0.0.0.0', port: 8077, public: true, loopback: false },
                 maxBodyBytes: 33554432, cors: { allowlist: [], wildcard: false, policy: 'same-origin' } } }), false],
  ];
  for (const [label, report, wantBanner] of cases) {
    ctx.renderPolicy(report);
    const shown = el('policy-banner').classList.contains('show');
    assert(shown === wantBanner, `${label}: banner shown=${shown}, expected ${wantBanner}`);
    const pill = el('policy-risk-pill');
    assert(pill.textContent.indexOf(report.riskLevel) === 0,
      `${label}: pill reads "${pill.textContent}", expected it to start with "${report.riskLevel}"`);
    const wantClass = 'pill ' + (ctx.POLICY_PILL_CLASS[report.riskLevel] || 'info-pill');
    assert(pill.className === wantClass, `${label}: pill class "${pill.className}" != "${wantClass}"`);
  }
  // The banner says what is wrong in words an operator can act on, not just "risk".
  ctx.renderPolicy(policyFixture({ riskLevel: 'critical', exposed: true,
    auth: { mode: 'open', tokens: { count: 0, active: 0, revoked: 0, expired: 0, byRole: {}, roles: [] } },
    confirmGate: { mode: 'off', gatedToolCount: 34, gatedJobKinds: ['run_luau'], destructiveNotGated: [] } }));
  const msg = el('policy-banner-msg').textContent;
  assert(/no credential is configured/.test(msg), `banner does not explain open auth: ${msg}`);
  assert(/no human approving/.test(msg), `banner does not explain the off gate: ${msg}`);
  assert(/34/.test(msg), `banner drops the gated tool count: ${msg}`);
  // Cards read the report rather than inventing values.
  assert(el('policy-auth-mode').textContent === 'OPEN', el('policy-auth-mode').textContent);
  assert(/0\.0\.0\.0|127\.0\.0\.1/.test(el('policy-bind').textContent), el('policy-bind').textContent);
  assert(/loopback|PUBLIC/.test(el('policy-bind').textContent), el('policy-bind').textContent);
  assert(/2 total/.test(el('policy-tokens').textContent) || /0 total/.test(el('policy-tokens').textContent),
    el('policy-tokens').textContent);
  return `${cases.length} posture combinations, banner + pill + 4 cards asserted`;
});

check('U1: risk rows escape server-supplied text, and no risks says what that means', () => {
  ctx.renderPolicy(policyFixture({ riskLevel: 'high', actionableRisks: 1, risks: [
    { id: 'cors_wildcard', severity: 'medium',
      message: `allowlist is * <img src=x onerror=alert(1)>`,
      fix: `bind loopback '); alert('xss` },
  ] }));
  const html = el('policy-risks').innerHTML;
  assert(html.indexOf('<img') < 0, `a risk message became markup: ${html.slice(0, 160)}`);
  assert(/onerror=/.test(html) === false || /&#39;|&quot;/.test(html),
    `quotes in a risk fix were not escaped: ${html.slice(0, 160)}`);
  assert(/sev-medium/.test(html), `severity class missing: ${html.slice(0, 120)}`);
  assert(/cors_wildcard/.test(html), 'risk id missing');
  assert(handlersIn(html).length === 0, `inline handler survived into a risk row: ${handlersIn(html).join(', ')}`);
  ctx.renderPolicy(policyFixture());
  const empty = el('policy-risks').innerHTML;
  assert(/No risks reported/.test(empty), `empty state changed: ${empty.slice(0, 120)}`);
  assert(/not a guarantee/.test(empty), 'the empty state must not read as a clean bill of health');
  return 'hostile message + fix inert, empty state states its own limit';
});

check('U1: an unreadable report clears the panel instead of leaving stale numbers', () => {
  ctx.renderPolicy(policyFixture({ riskLevel: 'critical', exposed: true,
    auth: { mode: 'open', tokens: { count: 0, active: 0, revoked: 0, expired: 0, byRole: {}, roles: [] } } }));
  assert(el('policy-banner').classList.contains('show'), 'precondition: banner was showing');
  ctx.policyUnavailable('HTTP 401');
  assert(!el('policy-banner').classList.contains('show'), 'banner stayed up after the report went away');
  assert(el('policy-risk-pill').textContent === 'not measured', el('policy-risk-pill').textContent);
  for (const id of ['policy-auth-mode', 'policy-gate-mode', 'policy-bind',
                    'policy-tunnel', 'policy-cors', 'policy-tokens']) {
    assert(el(id).textContent === 'unknown', `${id} kept a stale value: "${el(id).textContent}"`);
  }
  const note = el('policy-risks').textContent;
  assert(/UNAVAILABLE/.test(note) && /nothing below is a measurement/.test(note),
    `the unavailable state does not say nothing was measured: ${note.slice(0, 140)}`);
  assert(/HTTP 401/.test(note), 'the reason is dropped');
  assert(ctx.policyReport() === null, 'a failed read kept the previous report for copying');
  // A malformed payload must degrade the same way rather than half-render.
  ctx.renderPolicy({ nope: true });
  assert(el('policy-risk-pill').textContent === 'not measured', 'a malformed report half-rendered');
  return 'banner down, 6 cards unknown, reason shown, stale report dropped';
});

check('U2: each transport builds only the URL it means', () => {
  const stdio = ctx.stdioMcpConfig().mcpServers['roblox-studio'];
  assert(stdio.command === 'python' && Array.isArray(stdio.args), 'stdio config is not a command');
  assert(stdio.url === undefined, 'a stdio config must not carry a url');
  assert(stdio.args.some(a => /mcp_server\.py$/.test(a)), `stdio args lose the server path: ${stdio.args}`);

  const local = ctx.localHttpMcpConfig().mcpServers['roblox-studio-http'];
  assert(local.type === 'http' && local.url === 'http://127.0.0.1:8077/mcp', local.url);
  assert(/^Bearer /.test(local.headers.Authorization), 'local config has no bearer header');

  const tunnel = ctx.tunnelHttpMcpConfig('https://abc.trycloudflare.com/').mcpServers['roblox-studio-tunnel'];
  assert(tunnel.url === 'https://abc.trycloudflare.com/mcp', tunnel.url);
  assert(tunnel.url !== local.url, 'the tunnel config points at the local origin');
  return 'stdio (command+args), local (origin), tunnel (given url, trailing slash stripped)';
});

check('U2: the tunnel card refuses to fall back to this page origin', () => {
  // No live tunnel and nothing typed: the old code emitted `window.location.origin`
  // under a heading that said tunnel, which fails remotely with an error that never
  // mentions the tunnel.
  ctx.LAST_TUNNEL_RESET_FOR_TEST();
  ctx.renderTunnelConfig();
  const refused = el('cfg-http-tunnel').textContent;
  assert(refused.indexOf('mcpServers') < 0, `a config was emitted with no tunnel: ${refused.slice(0, 120)}`);
  assert(/No public tunnel URL is known/.test(refused), refused.slice(0, 140));
  assert(/deliberately does not fall back/.test(refused), 'the refusal does not explain itself');
  assert(el('cfg-tunnel-state').className.indexOf('off') > 0, el('cfg-tunnel-state').className);

  ctx.LAST_TUNNEL_SET_FOR_TEST('online', 'https://abc.trycloudflare.com');
  ctx.renderTunnelConfig();
  const cfg = JSON.parse(el('cfg-http-tunnel').textContent);
  assert(cfg.mcpServers['roblox-studio-tunnel'].url === 'https://abc.trycloudflare.com/mcp',
    cfg.mcpServers['roblox-studio-tunnel'].url);
  assert(el('cfg-tunnel-state').className.indexOf('on') > 0, el('cfg-tunnel-state').className);
  return 'refuses with no tunnel, emits the live URL when there is one';
});

check('U3: shortcuts never fire while typing or with a modifier held', () => {
  const typing = ctx.isTypingTarget;
  for (const tag of ['INPUT', 'TEXTAREA', 'SELECT']) {
    assert(typing({ tagName: tag }) === true, `${tag} must count as typing`);
  }
  assert(typing({ tagName: 'DIV', isContentEditable: true }) === true, 'contentEditable must count');
  assert(typing({ tagName: 'DIV' }) === false, 'a plain div is not a typing target');
  assert(typing(null) === false, 'a missing target is not a typing target');

  let prevented = 0;
  const ev = over => Object.assign({ key: 'r', ctrlKey: false, metaKey: false, altKey: false,
    defaultPrevented: false, target: { tagName: 'DIV' },
    preventDefault() { this.defaultPrevented = true; prevented += 1; } }, over || {});

  ctx.onDashboardKeydown(ev({ target: { tagName: 'INPUT' } }));
  ctx.onDashboardKeydown(ev({ ctrlKey: true }));
  ctx.onDashboardKeydown(ev({ metaKey: true, key: '/' }));
  ctx.onDashboardKeydown(ev({ altKey: true, key: 'g' }));
  ctx.onDashboardKeydown(ev({ defaultPrevented: true }));
  assert(prevented === 0, `${prevented} shortcut(s) fired while typing or with a modifier held`);

  // The positive path that needs no DOM: `/` with nothing to focus must say so
  // rather than throw or silently focus an unrelated field.
  ctx.onDashboardKeydown(ev({ key: '/' }));
  assert(prevented === 1, `slash did not preventDefault the browser's quick-find (prevented=${prevented})`);
  assert(/no search field/i.test(el('toast').textContent), el('toast').textContent);
  assert(ctx.CHORD_TARGETS.o === 'ops', 'g+o must open the Ops tab');
  assert(Object.keys(ctx.CHORD_TARGETS).length === 1,
    'an unbound chord was added without documenting it in the tips tab');
  return '5 guard cases inert, / prevents quick-find and toasts, g+o bound to ops';
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
