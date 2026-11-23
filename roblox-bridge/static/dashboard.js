/* v6.1.0 (P0-S3): `esc` used to replace only `& < >`, and it was interpolated
   straight into inline `onclick` attributes — so a peer id containing `'` closed
   the attribute and executed attacker JS, stealing the admin token from
   localStorage. It now escapes every character that can terminate an HTML text
   node, an attribute value (single- or double-quoted), or a JS template
   literal. The dashboard no longer emits inline handlers at all (see the
   delegated action binder below), so this is defence in depth, not the fix. */
const ESC_MAP = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','`':'&#96;'};
const esc = v => String(v==null?'':v).replace(/[&<>"'`]/g, c => ESC_MAP[c]);

/* -- delegated actions ------------------------------------------------------
   The page is served with `Content-Security-Policy: script-src 'self'`, which
   forbids inline event handlers outright. Every control therefore carries
   `data-act="fnName"` plus an optional `data-args='[...]'` JSON array and
   `data-act-on="click|change|input|mouseover|keydown:Enter"`; one set of
   document-level listeners resolves and calls them. Arguments become inert data
   instead of parsed JS, which removes the attribute-injection class of XSS.
   `actData(name, ...args)` builds the attribute pair for generated markup. */
const ACT_ADAPTER = {
  reload: () => location.reload(),
  hideQuickOut: () => { const w = document.getElementById('quick-out-wrap'); if (w) w.style.display = 'none'; },
  copyQuickOut: () => copyText((document.getElementById('quick-out') || {}).textContent || ''),
  copyTunnelUrl: () => copyText((document.getElementById('ops-tunnel-url') || {}).textContent || ''),
  applyActiveAtmo: () => applyAtmospherePreset(ACTIVE_ATMO_PRESET, true),
};
function copyPrompt(idx){
  const list = window.__PROMPTS || [];
  return copyText(list[Number(idx)] || '');
}

function showUiStageMeta(text){
  const meta = document.getElementById('ui-stage-meta');
  if (meta) meta.textContent = text;
}

const ACT_EVENTS = ['click', 'change', 'input', 'mouseover', 'keydown'];

function resolveAction(name){
  const direct = typeof name === 'string' ? (window[name] || ACT_ADAPTER[name]) : null;
  return typeof direct === 'function' ? direct : null;
}

function actSpec(el){
  const fn = el.getAttribute('data-act');
  if (!fn) return null;
  const spec = String(el.getAttribute('data-act-on') || 'click');
  const colon = spec.indexOf(':');
  return {
    fn,
    ev: (colon < 0 ? spec : spec.slice(0, colon)).trim(),
    keys: colon < 0 ? null : spec.slice(colon + 1).split(',').map(k => k.trim()),
  };
}

function actArgs(el){
  const raw = el.getAttribute('data-args');
  let args = [];
  if (raw) {
    try {
      const parsed = JSON.parse(raw);
      args = Array.isArray(parsed) ? parsed.slice() : [parsed];
    } catch(_){
      console.warn('dashboard: unparseable data-args', raw.slice(0, 120));
    }
  }
  if (el.hasAttribute('data-act-el')) args.push(el);   // the old `this` argument
  return args;
}

function runAction(el){
  const fn = resolveAction(el.getAttribute('data-act'));
  if (!fn) return false;
  try {
    const out = fn.apply(null, actArgs(el));
    if (out && typeof out.catch === 'function') out.catch(err => console.error('dashboard action failed', err));
  } catch(err){
    console.error('dashboard action failed', err);
  }
  return true;
}

/* Emit `data-act` / `data-args` for generated markup. Arguments are JSON, so a
   hostile value can never become code. */
function actData(name, ...args){
  let out = `data-act="${esc(name)}"`;
  if (args.length) out += ` data-args='${esc(JSON.stringify(args))}'`;
  return out;
}

/* Same, but bound to a non-click event (`mouseover`, `change`, `input`,
   `keydown:Enter`). */
function actDataOn(name, ev, ...args){
  return `${actData(name, ...args)} data-act-on="${esc(ev)}"`;
}

let ACT_BOUND = false;
function bindDelegatedActions(){
  if (ACT_BOUND) return;
  ACT_BOUND = true;
  ACT_EVENTS.forEach(type => {
    document.addEventListener(type, ev => {
      const el = ev.target && ev.target.closest ? ev.target.closest('[data-act]') : null;
      if (!el) return;
      const spec = actSpec(el);
      if (!spec || spec.ev !== type) return;
      if (type === 'keydown' && (spec.keys || ['Enter']).indexOf(ev.key) < 0) return;
      if (el.hasAttribute('disabled')) return;
      if (type === 'click' && el.tagName === 'A') return;   // real links keep working
      ev.preventDefault();
      runAction(el);
    }, type === 'input');
  });
  // Replacement for the old per-image error attribute: thumbnails that 404
  // simply vanish instead of showing the browser's broken-image glyph.
  document.addEventListener('error', ev => {
    const t = ev.target;
    if (t && t.tagName === 'IMG' && t.hasAttribute('data-hide-on-error')) t.style.display = 'none';
  }, true);
}
let BOOT = { local:false, token:'', bridgeDir:'roblox-bridge', mcpServerPath:'roblox-bridge/mcp_server.py', tools:[] };
let LAST_STATE = {};

function toast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg || 'Copied to clipboard';
  t.classList.add('show');
  clearTimeout(window.__toastTimer);
  window.__toastTimer = setTimeout(() => t.classList.remove('show'), 1800);
}

async function copyText(txt){
  try { await navigator.clipboard.writeText(txt); }
  catch(_){
    const ta = document.createElement('textarea');
    ta.value = txt; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
  }
  toast('Copied to clipboard');
}

function copyById(id){ copyText(document.getElementById(id).textContent); }

function toggleToken(){
  const inp = document.getElementById('tok-input');
  inp.type = inp.type === 'password' ? 'text' : 'password';
}

const PAGE_VERSION = "__BRIDGE_VERSION__";
let TOKEN = '';
function readStoredToken(){ try { return localStorage.getItem('arena_bridge_token') || ''; } catch(_) { return ''; } }
function getToken(){ return TOKEN || (document.getElementById('tok-input').value || '').trim(); }
function setToken(t){
  TOKEN = (t || '').trim();
  const box = document.getElementById('tok-input');
  if(box) box.value = TOKEN;
}
function storeToken(t){ try { localStorage.setItem('arena_bridge_token', t); } catch(_) {} }
function authHeaders(){
  const h = {'Content-Type':'application/json'};
  const t = getToken();
  if(t) h['Authorization'] = 'Bearer ' + t;
  return h;
}
function showAuthBanner(title, message){
  const b = document.getElementById('auth-banner');
  if(!b) return;
  if(title) document.getElementById('auth-title').textContent = title;
  if(message) document.getElementById('auth-msg').textContent = message;
  b.classList.add('show');
  const st = document.getElementById('status');
  st.textContent = 'token required';
  st.className = 'pill ro';
}
function hideAuthBanner(){
  const b = document.getElementById('auth-banner');
  if(b) b.classList.remove('show');
}
function saveToken(){
  const box = document.getElementById('auth-token');
  const value = (box && box.value || '').trim();
  if(!value) return toast('Paste the bridge token first.');
  storeToken(value); setToken(value); box.value = '';
  hideAuthBanner();
  toast('Token saved for this browser — reloading data…');
  tick(); refreshOps(); refreshFleet(); refreshBus(); loadKitGallery(); refresh3DViewport(); run3DSceneAudit(false, false);
}
function applyHeaderToken(){
  const box = document.getElementById('tok-input');
  const value = (box && box.value || '').trim();
  if(!value) return;
  storeToken(value); setToken(value);
  hideAuthBanner();
  toast('Header token applied — reloading data…');
  tick(); refreshOps(); refreshFleet(); refreshBus(); loadKitGallery(); refresh3DViewport(); run3DSceneAudit(false, false);
}
function showStaleBanner(runningVersion){
  const b = document.getElementById('stale-banner');
  if(!b) return;
  document.getElementById('stale-msg').textContent =
    `This tab is v${PAGE_VERSION}; the bridge is running v${runningVersion}. Reload for the current console.`;
  b.classList.add('show');
}
async function apiFetch(path, opts){
  let res;
  try {
    res = await fetch(path, Object.assign({cache:'no-store', headers:authHeaders()}, opts || {}));
  } catch(err){
    const st = document.getElementById('status');
    st.textContent = 'bridge unreachable'; st.className = 'pill off';
    throw err;
  }
  if(res.status === 401 || res.status === 403){
    showAuthBanner('This dashboard needs the bridge token',
      `The bridge rejected the request (HTTP ${res.status}). Paste bridge.token — opening the console from a tunnel or LAN address cannot auto-load it.`);
    throw new Error('unauthorized');
  }
  return res;
}

async function runSelfTest(){
  toast('Running isolated bridge suite and Studio scratch test…');
  const response = await fetch('/api/self-test', {headers: authHeaders()});
  const result = await response.json();
  if(!response.ok) throw new Error(result.error || ('HTTP ' + response.status));
  const title = document.getElementById('quick-out-title');
  const wrap = document.getElementById('quick-out-wrap');
  const pre = document.getElementById('quick-out');
  title.textContent = 'v__BRIDGE_VERSION__ Self-Test — ' + (result.ok ? 'PASS' : 'FAIL');
  pre.textContent = result.humanTable + '\n\n' + JSON.stringify({suite:result.suite,studioSmoke:result.studioSmoke,toolCount:(result.toolMatrix||[]).length}, null, 2);
  wrap.style.display = 'block';
  toast(result.ok ? 'Self-test passed.' : 'Self-test reported failures or skipped Studio checks.');
}

function showTab(name, btn){
  document.querySelectorAll('.tab-pane').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
  if(name === 'plugin') loadPluginDashboard();
}

async function callMcp(toolName, argsObj){
  const hdrs = Object.assign({}, authHeaders(), {
    'MCP-Protocol-Version': '2026-07-28',
    'Mcp-Method': 'tools/call',
    'Mcp-Name': String(toolName || '')
  });
  const r = await fetch('/mcp', {
    method: 'POST',
    headers: hdrs,
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 'web-' + Date.now(),
      method: 'tools/call',
      params: { name: toolName, arguments: argsObj || {} }
    })
  });
  const data = await r.json();
  if(!r.ok) throw new Error((data.error && data.error.message) || data.error || ('HTTP ' + r.status));
  return data.result;
}

async function quickTool(toolName, argsObj){
  const wrap = document.getElementById('quick-out-wrap');
  const pre = document.getElementById('quick-out');
  document.getElementById('quick-out-title').textContent = 'MCP Tool: ' + toolName + ' (running...)';
  wrap.style.display = 'block';
  pre.textContent = 'Waiting for Studio peer response...';
  try {
    const res = await callMcp(toolName, argsObj);
    document.getElementById('quick-out-title').textContent = 'MCP Tool: ' + toolName + (res.isError ? ' [ERROR]' : ' [OK]');
    pre.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    tick();
  } catch (err) {
    document.getElementById('quick-out-title').textContent = 'MCP Tool: ' + toolName + ' [FAILED]';
    pre.textContent = String(err);
  }
}

async function clearQueue(){
  await fetch('/api/queue/clear', { method:'POST', headers:authHeaders(), body:'{}' });
  toast('Queue cleared');
  tick();
}

async function inspectJob(jobId){
  const r = await fetch('/api/jobs/' + encodeURIComponent(jobId), { headers: authHeaders() });
  const j = await r.json();
  const wrap = document.getElementById('quick-out-wrap');
  document.getElementById('quick-out-title').textContent = 'Job #' + jobId + ' (' + (j.type || '') + ')';
  document.getElementById('quick-out').textContent = JSON.stringify(j, null, 2);
  wrap.style.display = 'block';
}

async function runLiveLuau(){
  const dm = document.getElementById('luau-dm').value;
  const code = document.getElementById('luau-code').value;
  const out = document.getElementById('luau-out');
  out.textContent = 'Executing in ' + dm + ' DataModel...';
  try {
    const res = await callMcp('execute_luau', { code, dm });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    tick();
  } catch (e) { out.textContent = String(e); }
}

const SAMPLE_ARGS = {
  survey_place: { includeSource: false },
  inspect_instance: { path: 'Workspace', depth: 2, descendantSummary: true },
  search_game_tree: { query: 'Spawn', root: 'game', limit: 30 },
  get_properties: { paths: ['Workspace'] },
  compare_instances: { pathA: 'Workspace.PartA', pathB: 'Workspace.PartB' },
  script_read: { path: 'ServerScriptService.Main', startLine: 1, endLine: 80, withLineNumbers: true },
  script_search: { query: 'Config' },
  script_grep: { pattern: 'RemoteEvent', root: 'game' },
  get_console_output: { limit: 50, level: 'all' },
  luau_check: { code: 'local x = Vector3.new(1, 2, 3)\nreturn tostring(x)\n' },
  drift_and_sync: { mode: 'check', localDir: './src', studioRoot: 'ServerScriptService.App' },
  manage_snapshots: { action: 'list' },
  generate_dungeon_layout: { seed: 2026, roomCount: 10, theme: 'horror_mansion', buildInStudio: false },
  build_gameplay_mechanic: { mechanic: 'horror-director', installInStudio: false },
  configure_atmosphere_fx: { preset: 'BloodMoon', intensity: 1.0, applyToStudio: false },
  synthesize_sound_effect: { preset: 'horror_heartbeat', saveToRepo: false },
  manage_plugin_and_playtest: { action: 'status' },
  audit_network_security: { includeStudio: true },
  build_cutscene_sequence: { preset: 'boss_awakening', letterbox: true, buildInStudio: false },
  generate_ui_theme_pack: { theme: 'HorrorBloodMoon', installInStudio: false },
  import_ui_from_web: { preset: 'rpg_hud', guiName: 'WebImportedGui', deSlop: true, installInStudio: false },
  export_ui_to_web: { path: 'StarterGui', deSlop: true },
  build_vfx_emitter: { preset: 'portal_swirl', burstCount: 32, continuous: false, buildInStudio: false },
  de_slop_audit: { texts: ['Furthermore — delve into this seamless quest!'], includeStudio: true, autoFix: false },
  spawn_kit_mesh: { query: 'crystal', scale: 1.2, spawnInStudio: false },
  kitbash_mesh_prop: { preset: 'runic_obelisk', scale: 1.2, interactive: true, spawnInStudio: false },
  build_combat_hitbox: { preset: 'melee_combo', baseDamage: 28, maxReach: 14.0, installInStudio: false },
  configure_camera_rig: { preset: 'over_shoulder_action', fov: 72, installInStudio: false },
  generate_sfx_soundbank: { pack: 'combat_pack', installInStudio: false },
  operator_chat: { action: 'history', limit: 25 }
};

function onSelectTool(){
  const sel = document.getElementById('tool-select').value;
  const meta = BOOT.tools.find(t => t.name === sel);
  if(meta){
    document.getElementById('tool-desc').textContent =
      (meta.readOnly ? '[Read-Only] ' : '[Mutating] ') + meta.description +
      (meta.properties.length ? ' | Fields: ' + meta.properties.join(', ') : '');
  }
  document.getElementById('tool-args').value = JSON.stringify(SAMPLE_ARGS[sel] || {}, null, 2);
}

async function callSelectedTool(){
  const sel = document.getElementById('tool-select').value;
  const out = document.getElementById('tool-out');
  out.textContent = 'Calling ' + sel + '...';
  try {
    const parsed = JSON.parse(document.getElementById('tool-args').value || '{}');
    const res = await callMcp(sel, parsed);
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    tick();
  } catch (e) { out.textContent = String(e); }
}

function renderConfigsAndCommands(){
  const mcpPath = (BOOT.mcpServerPath || 'roblox-bridge/mcp_server.py').replace(/\\/g, '/');
  const tok = getToken() || '<YOUR_BRIDGE_TOKEN>';
  const pubUrl = (document.getElementById('tunnel-url').value.trim() || window.location.origin).replace(/\/+$/, '');

  document.getElementById('cfg-cli').textContent =
`# Claude Code
claude mcp add roblox-studio -- python "${mcpPath}" --auto-install-plugin

# OpenAI Codex CLI
codex mcp add roblox-studio -- python "${mcpPath}" --auto-install-plugin`;

  document.getElementById('cfg-http').textContent = JSON.stringify({
    mcpServers: {
      "roblox-studio-http": {
        type: "http",
        url: pubUrl + "/mcp",
        headers: { Authorization: "Bearer " + tok }
      }
    }
  }, null, 2);

  document.getElementById('cfg-json').textContent = JSON.stringify({
    mcpServers: {
      "roblox-studio": {
        command: "python",
        args: [mcpPath, "--auto-install-plugin"],
        env: { BRIDGE_URL: window.location.origin }
      }
    }
  }, null, 2);

  document.getElementById('cfg-ro').textContent = JSON.stringify({
    mcpServers: {
      "roblox-studio-inspector": {
        command: "python",
        args: [mcpPath, "--read-only"],
        env: { BRIDGE_URL: window.location.origin }
      }
    }
  }, null, 2);

  const cmds = [
    [".\\ab.ps1 health", "Check bridge health, connected peers & MCP status"],
    [".\\ab.ps1 ping", "Round-trip ping to active Roblox Studio peer"],
    [".\\ab.ps1 state", "Full Studio state (PlaceId, memory, FPS, undo state)"],
    [".\\ab.ps1 survey", "Map entire open place to survey.json"],
    [".\\ab.ps1 tree Workspace --depth 3", "Inspect DataModel hierarchy to depth 3"],
    [".\\ab.ps1 search Spawn --root Workspace", "Search instances by name/class/tag/attribute"],
    [".\\ab.ps1 grep RemoteEvent --root ReplicatedStorage", "Search code across all scripts in Studio"],
    [".\\ab.ps1 read ServerScriptService.Main --start 1 --end 90 --numbers", "Read script slice with line numbers"],
    [".\\ab.ps1 edit ServerScriptService.Main --old \"DEBUG = false\" --new \"DEBUG = true\" --replace", "Surgical edit + bust ModuleScript cache"],
    [".\\ab.ps1 check ./src", "Offline Luau bytecode + undefined-global check"],
    [".\\ab.ps1 drift ./src ServerScriptService.App", "Compare local folder vs Studio via exact 32-bit FNV-1a"],
    [".\\ab.ps1 sync ./src ServerScriptService.App", "Validate + snapshot + atomic single-undo sync to Studio"],
    [".\\ab.ps1 import-image ./icon.png --target StarterGui --apply-mode screen_gui", "Import PNG/JPG into repo + Studio directly"],
    [".\\ab.ps1 gen-image icon_coin --size 128 --target StarterGui --apply-mode screen_gui", "Generate crisp procedural icon/texture PNG"],
    [".\\ab.ps1 viewport --preset perspective --out viewport.png", "Render shaded 3D Workspace + HUD PNG frame"],
    [".\\ab.ps1 audit-3d --fix-unanchored --snap-floating", "Audit & auto-fix unanchored/floating 3D geometry & spawns"],
    [".\\ab.ps1 export-place --out StarterPlace.rbxlx", "Export full live Studio place (or offline starter place) to .rbxlx"],
    [".\\ab.ps1 icons \"shield\" --pack lucide_fill", "Search 4,811 cloud icons across Stud, Lucide Outline & Lucide Filled"],
    [".\\ab.ps1 system liveops/codes", "Install any of 14 drop-in LiveOps / Monetization / Data systems"],
    [".\\ab.ps1 import-audio click_001.wav --target SoundService", "Import any of 45 CC0 WAVs or custom audio straight into Studio"],
    [".\\ab.ps1 audit-ui --path StarterGui", "Lint responsive UI bounds across Desktop, Tablet & Mobile"],
    [".\\ab.ps1 export-rbxmx StarterGui --out ./ui.rbxmx", "Export live Studio hierarchy to .rbxmx XML model file"],
    [".\\ab.ps1 template full_stud_ui_suite", "Install full HUD + Shop + Daily + Rebirth + Index + Economy"],
    [".\\ab.ps1 install-module all --parent ReplicatedStorage.Shared", "Install all 19 reusable Luau modules (StudUI, ModernUI, WidgetKit, etc.)"],
    [".\\ab.ps1 prefab ui_minimap_radar", "Spawn Minimap Radar / Ability Hotbar / Quest NPC / Kenney Shop prefab"],
    [".\\ab.ps1 scenario --mode play --server-code \"return {ok=true}\"", "Run automated Play Solo test & stop cleanly"]
  ];
  document.getElementById('cmd-list').innerHTML = cmds.map(([c, d]) => `
    <div class="cmd-row">
      <code>${esc(c)}</code>
      <span class="cmd-desc">${esc(d)}</span>
      <button class="sm" ${actData('copyText', c)}>Copy</button>
    </div>`).join('');
}

function renderPrompts(){
  const placeName = (LAST_STATE.studio && LAST_STATE.studio.place) ? LAST_STATE.studio.place : 'my open Roblox place';
  const placeId = (LAST_STATE.studio && LAST_STATE.studio.place_id) ? LAST_STATE.studio.place_id : 0;
  const dir = BOOT.bridgeDir || 'roblox-bridge';
  const tok = getToken() || '<PASTE_BRIDGE_TOKEN>';
  const pubUrl = (document.getElementById('tunnel-url').value.trim() || window.location.origin).replace(/\/+$/, '');

  const prompts = [
    {
      title: "1. Next-Agent Handoff — HANDS MODE (Local Terminal Relay, No Tunnel Needed)",
      sub: "Best when the next AI agent is in a cloud sandbox without tunnel access and you paste command outputs.",
      body:
`We are working on a Roblox Studio project (${placeName}${placeId ? ', PlaceId ' + placeId : ''}).
Roblox Studio MCP Bridge v__BRIDGE_VERSION__ is already running locally on my machine at ${window.location.origin} (directory: \`${dir}\`).

Use **HANDS MODE**: give me exact commands to run in PowerShell from \`${dir}\` using \`.\\ab.ps1 <subcommand>\` (or \`python arena_studio.py <subcommand>\`), and I will paste the output back.

Available high-signal commands:
- \`.\\ab.ps1 state\` / \`.\\ab.ps1 survey\` / \`.\\ab.ps1 tree Workspace --depth 3\`
- \`.\\ab.ps1 grep "<pattern>" --root game\` / \`.\\ab.ps1 read <Studio.Script.Path> --start 1 --end 120 --numbers\`
- \`.\\ab.ps1 check <local_folder_or_lua_file>\` (runs offline luau-compile + luau-analyze undefined-global check — ALWAYS run before pushing edits!)
- \`.\\ab.ps1 drift <local_dir> <Studio.Root>\` & \`.\\ab.ps1 sync <local_dir> <Studio.Root>\` (validates + snapshots + syncs in 1 Ctrl+Z step)
- \`.\\ab.ps1 edit <Studio.Script.Path> --old "..." --new "..." --replace\`
- \`.\\ab.ps1 scenario --mode play --server-code "..." --client-code "..."\`

Start by running a quick recon (\`.\\ab.ps1 state\` and \`.\\ab.ps1 survey\`) so you have ground truth of the open place before making changes.`
    },
    {
      title: "2. Next-Agent Handoff — Direct Tunnel / Streamable HTTP MCP + REST",
      sub: "Best when sharing a Cloudflare Quick Tunnel (`python setup.py --tunnel`) or local HTTP connection with an agent.",
      body:
`Connect directly to my live Roblox Studio Bridge & MCP Server:
- Base URL: ${pubUrl}
- Auth Header: \`Authorization: Bearer ${tok}\` (or \`X-Arena-Token: ${tok}\`)
- Native MCP Streamable HTTP Endpoint: \`POST ${pubUrl}/mcp\` (MCP tools, bundled skills, multimodal 3D viewport vision, 3D physics auditor, runtime mesh capability probe, .rbxlx place export, 14 LiveOps/Monetization systems, 520 3D models, 3,705 raster images + 50 LDR skies (300 cube faces), 45 CC0 WAVs, 4,811 cloud icons)
- Agent Onboarding & Live State Endpoint: \`GET ${pubUrl}/api/agent-guide\`
- Built-in Repo Kit: call \`browse_kit\` for bundled skills, 19 modules, 6 templates, 20 prefabs, 3,755 image/sky-face files, 50 LDR sky panoramas, 520 models, and 45 CC0 audio files. Use \`search_assets\` for paginated type/category/source/license/provenance filters; exact Git blob matches and pack-level attestations are distinguished. Send supported images/audio/models into Studio with \`import_image\`, \`import_audio\`, and \`import_rbxmx\`.

Rules of engagement:
1. Inspect live ground truth first (\`get_studio_state\`, \`survey_place\` with \`includeSource=false\`, and \`browse_kit\`).
2. **Import UI from Web & Dashboard**: You can import UI directly from the Web (HTML/CSS/Tailwind/SVG/JSON) and from the Control Center Dashboard using \`import_ui_from_web\` (or \`POST ${pubUrl}/api/ui/import-web\`).
3. **Prioritize 3D Meshes over Blocky Primitives**: Always prefer 3D meshes (\`spawn_kit_mesh\`, \`import_rbxmx\`, \`MeshPart\`, \`SpecialMesh\`, and the 520-model CC0 catalog) over basic blocky \`Part\` primitives.
4. **De-Slop & Visual Clarity**: Never use AI-slop artifacts such as em-dashes (\`—\`), robotic filler connectors (\`Furthermore\`, \`Moreover\`, \`Delve\`, \`Tapestry\`), or wall-of-text dumps. Pair HUD labels/buttons with iconography and run \`de_slop_audit\`.
5. **Direct Operator Live Chat**: Watch for \`operatorLiveChat\` injected into tool responses and reply immediately using \`operator_chat(action="reply", message="...")\`.
6. Validate Luau edits with \`luau_check\` before writing, and create a rollback point via \`manage_snapshots(action="create")\` before multi-script changes.
7. Pass \`replaceInstance=true\` when modifying \`ModuleScript\`s that may already be cached by \`require()\`.
8. Verify behaviour via \`run_playtest_scenario\` across \`server\` and \`client\` DataModels and check \`get_console_output\` for zero errors.
9. Run \`self_test\` before completion; Studio-only scratch checks are \`no_peer\` skips when no peer is connected, never passes.
10. Use \`mesh_capabilities\` to probe runtime APIs. A Studio version string is not proof of EditableMesh support; report the actual probe result and limitations.
11. Run \`python3 -B tools/verify_licences.py\` before asset changes are accepted. Do not present pack-level attestations as exact file matches, or LDR skies/placeholders as HDRIs/rendered mesh previews.`
    },
    {
      title: "3. Generic Prompt — Full Place Recon & Architecture Map (Read-Only Safe)",
      sub: "Drop into any existing or unfamiliar Roblox place to get a complete architecture breakdown first.",
      body:
`Survey my currently open Roblox Studio place without modifying anything yet:
1. Call \`get_studio_state\` and \`survey_place(includeSource=false)\`.
2. Inspect the hierarchy of \`Workspace\`, \`ReplicatedStorage\`, \`ServerScriptService\`, \`ServerStorage\`, \`StarterPlayer\`, and \`StarterGui\`.
3. Read key entry-point scripts and shared \`ModuleScript\`s with \`script_read\`, and map every \`RemoteEvent\` / \`RemoteFunction\` / \`CollectionService\` tag.
4. Give me a clear architecture map, entry points, data flow between Server and Client, and top improvement opportunities before we change anything.`
    },
    {
      title: "4. Generic Prompt — Build a Complete Feature + Playtest Verification Loop",
      sub: "End-to-end engineering workflow using 3D instance building, verified Luau scripts, and automated Play Solo checks.",
      body:
`I want to build the following feature in my open Roblox Studio place:
[DESCRIBE FEATURE HERE — e.g. an interactive shop / round system / obby checkpoint / ability tool]

Follow this exact loop:
1. Survey existing services/remotes/tags first so naming and structure match the place.
2. Create a safety snapshot with \`manage_snapshots(action="create")\`.
3. Build any needed 3D models/UI/remotes with \`build_tree\` (using clean hierarchy, anchored parts where appropriate, attributes/tags).
4. Check all Luau code with \`luau_check\`, then write/edit scripts using \`write_script\` or \`multi_edit\` (\`replaceInstance=true\` on ModuleScripts).
5. Run \`run_playtest_scenario(mode="play", serverCode=..., clientCode=...)\` and check \`get_console_output\` to verify everything works cleanly with zero warnings or errors.`
    },
    {
      title: "5. Generic Prompt — Diagnose & Fix a Runtime Bug",
      sub: "Uses console cursors, cross-script grep, fresh module isolation, and surgical edits.",
      body:
`Help me track down and fix a bug in my Roblox Studio place:
Symptom: [DESCRIBE WHAT HAPPENS OR PASTE ERROR MESSAGE]

Workflow:
1. Check recent logs with \`get_console_output\` and search relevant code across the whole place with \`script_grep\` and \`script_search\`.
2. Inspect any referenced instances/properties/attributes/tags in the tree with \`inspect_instance\` and \`get_properties\`.
3. Isolate module logic with \`require_fresh\` or reproduce in live Play mode with \`run_playtest_scenario\`.
4. Explain the exact root cause, apply a minimal surgical fix with \`multi_edit\` (after \`luau_check\`), and re-run the playtest scenario to prove the fix.`
    },
    {
      title: "6. Generic Prompt — Audit Place Security, Performance & Code Quality",
      sub: "Finds unvalidated Remotes, embedded free-model scripts, unanchored physics leaks, and undefined globals.",
      body:
`Run a full technical audit of my open Roblox place (read-only first — do not modify anything until I approve):
1. Security: find every \`RemoteEvent\` / \`RemoteFunction\` and audit all \`OnServerEvent\` / \`OnServerInvoke\` handlers for missing server-side type, range, cooldown, and ownership validation; scan \`Workspace\` for unexpected embedded \`Script\`s.
2. Static quality: run \`luau_check\` on scripts to flag syntax issues, undefined globals, or unused variables.
3. Physics & scene health: check total instance/part counts, unanchored parts without constraints, duplicate scripts via \`compare_instances\`, and live memory/FPS from \`get_studio_state\`.
4. Present a prioritized report grouped into Critical, Medium, and Polish fixes.`
    }
  ];

  const grid = document.getElementById('prompt-grid');
  window.__PROMPTS = prompts.map(p => p.body);
  grid.innerHTML = prompts.map((p, idx) => `
    <div class="prompt-card">
      <div class="prompt-head">
        <div><h3>${esc(p.title)}</h3><div class="sub">${esc(p.sub)}</div></div>
        <button class="primary sm" ${actData('copyPrompt', idx)}>Copy Prompt</button>
      </div>
      <pre>${esc(p.body)}</pre>
    </div>`).join('');
  renderConfigsAndCommands();
}

function renderTips(){
  const tips = [
    ["Edit vs Server vs Client DataModels",
     "During Play Solo (F5) or Run (F8), BOTH the Edit DataModel and Play Server DataModel stay alive simultaneously. Always target `dm='edit'` for permanent edits to the place, `dm='server'` to inspect/test live server state, and `dm='client'` to inspect local UI, camera, or player state via the silent RemoteFunction relay."],
    ["Busting Cached ModuleScripts (`require()`)",
     "Luau caches `require(moduleScript)` per DataModel lifetime. Editing `.Source` alone won't reload already-required tables. Pass `replaceInstance: true` on `write_script` / `multi_edit` (or `--replace` in CLI), or test edits in a clean sandbox clone with `require_fresh`."],
    ["Always Pre-Flight Luau with `luau_check`",
     "In non-strict Luau, deleting a helper function leaves callers compiling cleanly while crashing at runtime with `attempt to call a nil value`. `luau_check` runs both `luau-compile` and `luau-analyze` to catch undefined globals before code ever reaches Studio."],
    ["Open Script Editor Tabs & >200 KB Scripts",
     "ArenaBridge v4.1.0 uses `ScriptEditorService:GetEditorSource` and `UpdateSourceAsync` with CRLF -> LF normalization instead of raw `.Source` assignment — safely supporting scripts over 200 KB and keeping open Studio tabs synchronized without cursor corruption."],
    ["Single-Step Undo (`Ctrl+Z`) + Multi-Script Snapshots",
     "Every mutating tool wraps inside a `ChangeHistoryService` recording so one `Ctrl+Z` in Studio undoes an entire build or batch sync. Before sweeping refactors, call `manage_snapshots(action='create')` for instant 1-command rollback."],
    ["Safe Toolbox / Asset Insertion",
     "`insert_asset` scans all inserted descendants for embedded `Script` / `LocalScript` / `ModuleScript` instances and strips them by default (`stripScripts: true`), protecting places from malicious free-model scripts."],
    ["Zero-Drift Local Folder <-> Studio Workflow",
     "`drift_and_sync` computes identical 53-bit-safe 32-bit FNV-1a hashes in Python and Luau. Always run `mode='check'` first to inspect modified/local-only/studio-only scripts before running `mode='push'` or `mode='pull'`."],
    ["Why the Bridge Runs on Your Machine (Not Inside Cloud Preview URLs)",
     "Cloud sandbox preview hosts require browser session headers that Roblox Studio `HttpService` cannot attach. Running `server.py` locally on `:8077` works out-of-the-box for all local MCP clients, and adding `--tunnel` exposes `/mcp` + `/api/*` cleanly when a remote cloud agent needs direct access."]
  ];
  document.getElementById('tips-grid').innerHTML = tips.map(([t, b]) => `
    <div class="card"><h3>${esc(t)}</h3><p style="margin:6px 0 0;color:#b6c0cb">${esc(b)}</p></div>`).join('');
}

async function uploadBrowserImage(){
  const fileInput = document.getElementById('img-file');
  const out = document.getElementById('img-import-out');
  if(!fileInput.files || !fileInput.files[0]){
    out.textContent = 'Select a .png, .jpg, or .bmp file first.';
    return;
  }
  const f = fileInput.files[0];
  out.textContent = 'Encoding & importing ' + f.name + '...';
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(f);
  });
  try {
    const res = await callMcp('import_image', {
      base64Data: dataUrl,
      name: document.getElementById('img-name').value.trim() || f.name.replace(/\.[^.]+$/, ''),
      targetPath: document.getElementById('img-target').value.trim() || undefined,
      applyMode: document.getElementById('img-mode').value,
      useEditableImage: true,
      syncAssetsModule: true
    });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    loadKitGallery();
    tick();
  } catch (e) { out.textContent = String(e); }
}

let ALL_KIT_IMAGES = [];
let ALL_KIT_MODELS = [];
let ALL_KIT_AUDIO = [];
let ACTIVE_PACK = 'all';
let ACTIVE_ASSET_TYPE = 'any';
let ASSET_PAGE = 0;
const ASSET_PAGE_SIZE = 72;
let ACTIVE_CLOUD_CAT = '';
let ACTIVE_ATMO_PRESET = 'BloodMoon';

async function installAllModules(){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing all 25 Luau modules -> ReplicatedStorage.Shared...';
  try {
    const res = await callMcp('install_kit_module', { module: 'all', parent: 'ReplicatedStorage.Shared' });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function installSingleModule(mname){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing Luau module ' + mname + ' -> ReplicatedStorage.Shared...';
  try {
    const res = await callMcp('install_kit_module', { module: mname, parent: 'ReplicatedStorage.Shared' });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Installed module ' + mname);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function inspectSingleModule(mname){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Loading module ' + mname + '.lua...';
  try {
    const r = await apiFetch('/api/kit/catalog?category=modules&item=' + encodeURIComponent(mname));
    const d = await r.json();
    out.textContent = d.content || JSON.stringify(d, null, 2);
  } catch (e) { out.textContent = String(e); }
}

async function runMechanicBuilder(installInStudio){
  const mech = (document.getElementById('mechanic-select') && document.getElementById('mechanic-select').value) || 'horror-director';
  const meta = document.getElementById('mechanic-meta');
  const out = document.getElementById('kit-action-out');
  try {
    if(installInStudio){
      out.textContent = 'Installing gameplay mechanic ' + mech + ' into Studio...';
      const res = await callMcp('build_gameplay_mechanic', { mechanic: mech, installInStudio: true });
      const d = res.structuredContent || res;
      out.textContent = JSON.stringify(d, null, 2);
      toast('Installed gameplay mechanic: ' + mech);
      tick();
      return;
    }
    const r = await apiFetch('/api/kit/catalog?format=full');
    const cat = await r.json();
    const b = (cat.mechanicBundles || []).find(x => x.name === mech);
    if(b && meta){
      meta.innerHTML = `<strong>${esc(b.name)}</strong>: ${esc(b.description)} &bull; System: <code>${esc(b.system||'none')}</code> &bull; Prefab: <code>${esc(b.prefab||'none')}</code> &bull; Modules: <code>${esc((b.modules||[]).join(', '))}</code>`;
    }
  } catch (e) {
    if(meta) meta.textContent = String(e);
  }
}

async function applyAtmospherePreset(presetName, sendToStudio){
  ACTIVE_ATMO_PRESET = presetName || 'BloodMoon';
  document.querySelectorAll('#atmo-preset-btns button').forEach(b => {
    b.classList.toggle('primary', b.textContent.trim() === ACTIVE_ATMO_PRESET);
  });
  const meta = document.getElementById('atmo-meta');
  if(meta) meta.textContent = `Preset: ${ACTIVE_ATMO_PRESET} (Atmosphere + ColorCorrectionEffect + BloomEffect)`;
  if(sendToStudio){
    const out = document.getElementById('kit-action-out');
    out.textContent = 'Applying atmosphere preset ' + ACTIVE_ATMO_PRESET + ' to Studio Lighting...';
    try {
      const res = await callMcp('configure_atmosphere_fx', { preset: ACTIVE_ATMO_PRESET, intensity: 1.0, applyToStudio: true });
      out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
      toast('Applied ' + ACTIVE_ATMO_PRESET + ' to Lighting');
      tick();
    } catch (e) { out.textContent = String(e); }
  }
}

function drawDungeonCanvas(rooms, corridors){
  const cv = document.getElementById('dungeon-canvas');
  if(!cv || !cv.getContext) return;
  const ctx = cv.getContext('2d');
  ctx.clearRect(0, 0, cv.width, cv.height);
  if(!rooms || !rooms.length) return;
  let minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
  rooms.forEach(r => {
    const [cx, , cz] = r.center;
    const [w, , d] = r.size;
    minX = Math.min(minX, cx - w); maxX = Math.max(maxX, cx + w);
    minZ = Math.min(minZ, cz - d); maxZ = Math.max(maxZ, cz + d);
  });
  const spanX = Math.max(40, maxX - minX);
  const spanZ = Math.max(40, maxZ - minZ);
  const pad = 22;
  const scale = Math.min((cv.width - pad * 2) / spanX, (cv.height - pad * 2) / spanZ);
  const toScreen = (x, z) => [
    pad + (x - minX) * scale,
    pad + (z - minZ) * scale
  ];
  const roleColors = {
    spawn: '#45e0a0', normal: '#5e6e82', treasure: '#ffc857',
    puzzle: '#5cc8ff', saferoom: '#8dc6af', boss: '#eb4b4b'
  };
  (corridors || []).forEach(c => {
    const ra = rooms[c.from - 1];
    const rb = rooms[c.to - 1];
    if(!ra || !rb) return;
    const [x1, y1] = toScreen(ra.center[0], ra.center[2]);
    const [x2, y2] = toScreen(rb.center[0], rb.center[2]);
    ctx.save();
    ctx.strokeStyle = c.isLoop ? 'rgba(255,200,87,0.75)' : 'rgba(92,200,255,0.65)';
    ctx.lineWidth = c.isLoop ? 1.8 : 2.6;
    if(c.isLoop) ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    ctx.restore();
  });
  rooms.forEach(r => {
    const [sx, sy] = toScreen(r.center[0], r.center[2]);
    const rw = Math.max(14, r.size[0] * scale);
    const rd = Math.max(14, r.size[2] * scale);
    ctx.fillStyle = 'rgba(15, 22, 34, 0.92)';
    ctx.strokeStyle = roleColors[r.role] || '#5e6e82';
    ctx.lineWidth = 2;
    ctx.fillRect(sx - rw / 2, sy - rd / 2, rw, rd);
    ctx.strokeRect(sx - rw / 2, sy - rd / 2, rw, rd);
    ctx.fillStyle = roleColors[r.role] || '#e2e8f0';
    ctx.font = 'bold 10px ui-monospace, monospace';
    ctx.textAlign = 'center';
    ctx.fillText(String(r.id) + ':' + r.role.slice(0, 4), sx, sy + 3);
  });
}

async function previewDungeonLayout(buildInStudio){
  const seed = parseInt((document.getElementById('dungeon-seed') && document.getElementById('dungeon-seed').value) || '2026', 10) || 2026;
  const roomCount = Math.max(3, Math.min(28, parseInt((document.getElementById('dungeon-rooms') && document.getElementById('dungeon-rooms').value) || '10', 10) || 10));
  const theme = (document.getElementById('dungeon-theme') && document.getElementById('dungeon-theme').value) || 'horror_mansion';
  try {
    const res = await callMcp('generate_dungeon_layout', { seed, roomCount, theme, buildInStudio: Boolean(buildInStudio) });
    const d = res.structuredContent || res;
    drawDungeonCanvas(d.rooms || [], d.corridors || []);
    const meta = document.getElementById('dungeon-meta');
    if(meta){
      meta.innerHTML = `Seed <code>${d.seed}</code> · <strong>${d.roomCount}</strong> rooms · <strong>${d.corridorCount}</strong> corridors (MST + escape loops) · Reachable: <strong>${d.allRoomsReachable ? '100%' : 'partial'}</strong>`;
    }
    if(buildInStudio){
      document.getElementById('kit-action-out').textContent = JSON.stringify(d, null, 2);
      toast('Generated 3D dungeon in Studio!');
      tick();
    }
  } catch (e) {
    // Deterministic client-side fallback preview if offline/unauthenticated
    const rooms = [];
    const corridors = [];
    for(let i = 0; i < roomCount; i++){
      const angle = (i / roomCount) * Math.PI * 2;
      const rad = 45 + (i % 2) * 24;
      const roles = ['spawn', 'normal', 'treasure', 'puzzle', 'saferoom', 'boss'];
      const role = i === 0 ? 'spawn' : (i === roomCount - 1 ? 'boss' : roles[(i % 4) + 1]);
      rooms.push({ id: i + 1, role, center: [Math.cos(angle) * rad, 0, Math.sin(angle) * rad], size: [24, 14, 24] });
      if(i > 0) corridors.push({ from: i, to: i + 1, isLoop: false });
    }
    if(roomCount > 3) corridors.push({ from: 1, to: roomCount, isLoop: true });
    drawDungeonCanvas(rooms, corridors);
  }
}

function playSynthAudio(preset){
  const player = document.getElementById('sfx-player');
  player.src = '/api/kit/synth-audio/' + encodeURIComponent(preset) + '.wav';
  player.currentTime = 0;
  player.play().catch(_ => {});
}

async function sendSynthAudioToStudio(preset){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Synthesizing procedural WAV ' + preset + ' -> SoundService...';
  try {
    const res = await callMcp('synthesize_sound_effect', { preset, targetPath: 'SoundService', saveToRepo: false });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Synthesized & installed ' + preset);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function sendProceduralPresetToStudio(preset){
  toast('Generating procedural shader ' + preset + ' -> StarterGui...');
  await quickTool('generate_procedural_image', {
    preset,
    name: preset,
    saveToRepo: false,
    targetPath: 'StarterGui',
    applyMode: 'screen_gui'
  });
}

async function importRbxmxModelToStudio(relModel){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Importing 3D .rbxmx model ' + relModel + ' -> Workspace...';
  try {
    const res = await callMcp('import_rbxmx', { repoModel: relModel, parent: 'Workspace' });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Imported ' + relModel + ' into Workspace!');
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function installTemplateUI(tname){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing template ' + tname + ' (modules + ScreenGuis in StarterGui + runtime controllers)...';
  try {
    const res = await callMcp('install_template', { template: tname });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Installed ' + tname + ' into Studio!');
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function spawnKitPrefab(name){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Spawning prefab ' + name + '...';
  try {
    const res = await callMcp('spawn_prefab', { prefab: name });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Spawned ' + name);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function importRepoAssetToStudio(relPath){
  toast('Sending ' + relPath + ' to Studio...');
  await quickTool('import_image', {
    repoAsset: relPath,
    targetPath: 'StarterGui',
    applyMode: 'screen_gui',
    useEditableImage: true
  });
}

async function spawnCloudIconPreview(uri, label){
  toast('Spawning cloud icon ' + label + ' in StarterGui...');
  await quickTool('build_tree', {
    parent: 'StarterGui',
    tree: [{
      name: 'CloudIconPreview',
      className: 'ScreenGui',
      replaceExisting: true,
      properties: { ResetOnSpawn: false },
      children: [{
        name: label.replace(/[^A-Za-z0-9_]/g, '_') || 'Icon',
        className: 'ImageLabel',
        properties: {
          AnchorPoint: { __t: 'Vector2', v: [0.5, 0.5] },
          Position: { __t: 'UDim2', v: [0.5, 0, 0.5, 0] },
          Size: { __t: 'UDim2', v: [0, 128, 0, 128] },
          BackgroundTransparency: 0.25,
          Image: uri
        }
      }]
    }]
  });
}

function setRegistryAssetType(atype){
  ACTIVE_ASSET_TYPE = atype || 'any';
  ASSET_PAGE = 0;
  ['any', 'image', 'sky', 'model', 'audio'].forEach(k => {
    const b = document.getElementById('atype-' + k);
    if(b) b.classList.toggle('primary', k === ACTIVE_ASSET_TYPE);
  });
  renderFilteredAssets();
}

function stepAssetPage(delta){
  ASSET_PAGE = Math.max(0, ASSET_PAGE + delta);
  renderFilteredAssets();
}

function selectPack(pack){
  ACTIVE_PACK = pack;
  ASSET_PAGE = 0;
  document.querySelectorAll('#asset-pack-pills button').forEach(b => {
    b.classList.toggle('primary', b.dataset.pack === pack);
  });
  renderFilteredAssets();
}

function renderFilteredAssets(){
  const q = (document.getElementById('asset-search').value || '').trim().toLowerCase();
  const combined = [];

  if(ACTIVE_ASSET_TYPE === 'any' || ACTIVE_ASSET_TYPE === 'image' || ACTIVE_ASSET_TYPE === 'sky'){
    ALL_KIT_IMAGES.forEach(im => {
      const isSky = im.pack === 'arena_skies' || im.thumbnailKind === 'equirectangular-panorama';
      if(ACTIVE_ASSET_TYPE === 'sky' && !isSky) return;
      if(ACTIVE_ASSET_TYPE === 'image' && isSky) return;
      if(ACTIVE_PACK !== 'all' && im.pack !== ACTIVE_PACK) return;
      if(q && !(im.relPath.toLowerCase().includes(q) || im.name.toLowerCase().includes(q) || String(im.category||'').toLowerCase().includes(q))) return;
      combined.push({ kind: isSky ? 'sky' : 'image', data: im });
    });
  }
  if(ACTIVE_ASSET_TYPE === 'any' || ACTIVE_ASSET_TYPE === 'model'){
    ALL_KIT_MODELS.forEach(m => {
      const packName = 'model_' + (m.pack || 'featured');
      if(ACTIVE_PACK !== 'all' && ACTIVE_PACK !== packName && ACTIVE_PACK !== m.pack) return;
      if(q && !(m.fileName.toLowerCase().includes(q) || m.name.toLowerCase().includes(q))) return;
      combined.push({ kind: 'model', data: m });
    });
  }
  if(ACTIVE_ASSET_TYPE === 'any' || ACTIVE_ASSET_TYPE === 'audio'){
    ALL_KIT_AUDIO.forEach(a => {
      if(ACTIVE_PACK !== 'all' && ACTIVE_PACK !== 'audio_cc0') return;
      if(q && !(a.fileName.toLowerCase().includes(q) || a.name.toLowerCase().includes(q))) return;
      combined.push({ kind: 'audio', data: a });
    });
  }

  const maxPage = Math.max(0, Math.ceil(combined.length / ASSET_PAGE_SIZE) - 1);
  if(ASSET_PAGE > maxPage) ASSET_PAGE = maxPage;
  const start = ASSET_PAGE * ASSET_PAGE_SIZE;
  const pageItems = combined.slice(start, start + ASSET_PAGE_SIZE);

  const pageMeta = document.getElementById('asset-page-meta');
  if(pageMeta){
    pageMeta.textContent = combined.length
      ? `Showing ${start + 1}–${Math.min(combined.length, start + pageItems.length)} of ${combined.length.toLocaleString()} matching licensed assets (Page ${ASSET_PAGE + 1} of ${maxPage + 1})`
      : 'No assets match the current filter.';
  }
  const prevBtn = document.getElementById('asset-prev-btn');
  const nextBtn = document.getElementById('asset-next-btn');
  if(prevBtn) prevBtn.disabled = ASSET_PAGE <= 0;
  if(nextBtn) nextBtn.disabled = ASSET_PAGE >= maxPage;

  document.getElementById('asset-gallery').innerHTML = pageItems.map(entry => {
    if(entry.kind === 'model'){
      const m = entry.data;
      const thumbUrl = m.thumbnailPath ? `/api/kit/thumbnail/${esc(m.thumbnailPath)}` : '';
      return `
        <div class="card" style="padding:10px;display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center">
          <div style="width:76px;height:76px;display:flex;align-items:center;justify-content:center;background:#0d131d;border:1px solid var(--border);border-radius:8px;padding:4px;overflow:hidden">
            ${thumbUrl ? `<img src="${thumbUrl}" alt="${esc(m.name)}" loading="lazy" style="max-width:66px;max-height:66px;object-fit:contain" data-hide-on-error>` : `<span class="sub">3D .rbxmx</span>`}
          </div>
          <div style="font-size:10px;color:var(--accent)">3D Model &bull; ${esc(m.pack||'featured')}</div>
          <div style="font-size:9px;color:var(--muted)">CC0-1.0 &bull; ${(m.bytes/1024).toFixed(1)} KB</div>
          <code style="font-size:11px;word-break:break-all">${esc(m.name)}</code>
          <div style="display:flex;gap:4px">
            <button class="sm" ${actData("copyText", m.fileName)}>Path</button>
            <button class="sm primary" ${actData("importRbxmxModelToStudio", m.fileName)}>Spawn</button>
          </div>
        </div>`;
    }
    if(entry.kind === 'audio'){
      const a = entry.data;
      const thumbUrl = a.thumbnailPath ? `/api/kit/thumbnail/${esc(a.thumbnailPath)}` : '';
      return `
        <div class="card" style="padding:10px;display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center">
          <div style="width:110px;height:52px;display:flex;align-items:center;justify-content:center;background:#0d131d;border:1px solid var(--border);border-radius:8px;padding:4px;overflow:hidden">
            ${thumbUrl ? `<img src="${thumbUrl}" alt="${esc(a.name)}" loading="lazy" style="width:100%;height:100%;object-fit:contain">` : `<span class="sub">WAV</span>`}
          </div>
          <div style="font-size:10px;color:var(--green)">CC0 Audio &bull; ${(a.bytes/1024).toFixed(1)} KB</div>
          <code style="font-size:11px;word-break:break-all">${esc(a.name)}</code>
          <div style="display:flex;gap:4px">
            <button class="sm" ${actData("playBrowserAudio", a.fileName)}>&#9654;</button>
            <button class="sm" ${actData("copyText", a.localUri)}>URI</button>
            <button class="sm primary" ${actData("sendAudioToStudio", a.fileName)}>Send</button>
          </div>
        </div>`;
    }
    const im = entry.data;
    return `
      <div class="card" style="padding:10px;display:flex;flex-direction:column;align-items:center;gap:6px;text-align:center">
        <div style="width:76px;height:76px;display:flex;align-items:center;justify-content:center;background:repeating-conic-gradient(#161d29 0% 25%, #0d121a 0% 50%) 50% / 14px 14px;border-radius:8px;padding:6px">
          <img src="/api/kit/image/${esc(im.relPath)}" alt="${esc(im.name)}" loading="lazy" style="max-width:62px;max-height:62px;image-rendering:auto">
        </div>
        <div style="font-size:10px;color:var(--muted)">${esc(im.pack)} &middot; ${esc(im.category||im.pack)}</div>
        <div style="font-size:9px;color:var(--muted);max-width:100%;overflow-wrap:anywhere">${esc(im.license||'CC0-1.0')} &middot; ${esc(im.provenance||'verified')}</div>
        <code style="font-size:11px;word-break:break-all">${esc(im.name)}</code>
        <div style="display:flex;gap:4px">
          <button class="sm" ${actData("copyText", im.localUri)}>URI</button>
          <button class="sm primary" ${actData("importRepoAssetToStudio", im.relPath)}>Send</button>
        </div>
      </div>`;
  }).join('');
}

let ACTIVE_ICON_PACK = 'all';
let ACTIVE_PREVIEW_VP = 'desktop';
let LAST_UI_AUDIT = null;

function playBrowserAudio(fname){
  const player = document.getElementById('sfx-player');
  player.src = '/api/kit/audio/' + encodeURIComponent(fname);
  player.currentTime = 0;
  player.play().catch(_ => {});
}

async function sendAudioToStudio(fname){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing CC0 audio ' + fname + ' -> SoundService...';
  try {
    const res = await callMcp('import_audio', { repoAudio: fname, targetPath: 'SoundService' });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Installed Sound ' + fname + ' in SoundService');
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function installDropinSystemUI(sysName){
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Installing drop-in system ' + sysName + ' -> ReplicatedStorage.Shared + ServerScriptService + StarterPlayerScripts...';
  try {
    const res = await callMcp('install_dropin_system', { system: sysName });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Installed system ' + sysName);
    tick();
  } catch (e) { out.textContent = String(e); }
}

async function exportLiveRbxmx(){
  const p = (document.getElementById('export-rbxmx-path').value || 'StarterGui').trim();
  const out = document.getElementById('kit-action-out');
  out.textContent = 'Exporting ' + p + ' -> .rbxmx XML model file...';
  try {
    const res = await callMcp('export_rbxmx', { path: p });
    out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast('Exported ' + p + ' to .rbxmx');
  } catch (e) { out.textContent = String(e); }
}

function switchPreviewViewport(vp){
  ACTIVE_PREVIEW_VP = vp;
  ['desktop', 'tablet', 'mobile'].forEach(k => {
    const b = document.getElementById('vp-btn-' + k);
    if(b) b.classList.toggle('primary', k === vp);
  });
  renderUiPreviewStage();
}

async function runUiPreviewAndAudit(){
  const raw = document.getElementById('ui-audit-src').value || 'prefab:ui_kenney_shop_window';
  const args = raw.startsWith('prefab:')
    ? { prefab: raw.slice('prefab:'.length) }
    : { path: raw.slice('studio:'.length) };
  try {
    const res = await callMcp('audit_ui_layout', args);
    LAST_UI_AUDIT = res.structuredContent || null;
    renderUiPreviewStage();
  } catch (e) {
    document.getElementById('ui-issues-list').innerHTML = `<div class="sub">${esc(String(e))}</div>`;
  }
}

function renderUiPreviewStage(){
  if(!LAST_UI_AUDIT || !LAST_UI_AUDIT.viewports) return;
  const rep = LAST_UI_AUDIT.viewports[ACTIVE_PREVIEW_VP] || LAST_UI_AUDIT.viewports.desktop;
  if(!rep) return;
  const maxStageW = 540;
  const scale = Math.min(1, maxStageW / rep.width);
  const stage = document.getElementById('ui-preview-stage');
  stage.style.width = Math.round(rep.width * scale) + 'px';
  stage.style.height = Math.round(rep.height * scale) + 'px';

  stage.innerHTML = (rep.elements || []).map(el => {
    const [x, y, w, h] = el.rect;
    let imgHtml = '';
    if(el.image && el.image.startsWith('rbxasset://arena_assets/')){
      const rel = el.image.slice('rbxasset://arena_assets/'.length);
      imgHtml = `<img src="/api/kit/image/${esc(rel)}" style="width:100%;height:100%;object-fit:fill;display:block;pointer-events:none">`;
    }
    const txtHtml = el.text != null && el.text !== ''
      ? `<span style="position:relative;z-index:2;color:${esc(el.textColor||'#fff')};font-size:${Math.max(8, Math.round((el.textSize||14)*scale))}px;font-weight:700;padding:0 4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(el.text)}</span>`
      : '';
    return `<div ${actDataOn('showUiStageMeta', 'mouseover', `${el.path} (${el.className}) \u2014 [${x}, ${y}, ${w}x${h}px]`)}
      style="position:absolute;left:${(x*scale).toFixed(1)}px;top:${(y*scale).toFixed(1)}px;width:${Math.max(2, w*scale).toFixed(1)}px;height:${Math.max(2, h*scale).toFixed(1)}px;background:${esc(el.bg||'transparent')};border:${esc(el.border||'1px solid rgba(88,166,255,0.22)')};border-radius:${Math.round((el.radius||0)*scale)}px;display:flex;align-items:center;justify-content:center;overflow:hidden;box-sizing:border-box">
      ${imgHtml ? `<div style="position:absolute;inset:0">${imgHtml}</div>` : ''}${txtHtml}
    </div>`;
  }).join('');

  const allIssues = Object.values(LAST_UI_AUDIT.viewports).flatMap(v => v.issues || []);
  document.getElementById('ui-issues-title').textContent =
    `Layout Lint Report — ${allIssues.length === 0 ? '0 Issues Across All 3 Devices!' : allIssues.length + ' Advisory / Overflow Notes'}`;
  document.getElementById('ui-issues-list').innerHTML = allIssues.length === 0
    ? '<div class="pill on" style="display:inline-block">Clean responsive bounds across Desktop, Tablet &amp; Mobile</div>'
    : allIssues.map(iss => `
      <div class="card" style="padding:8px 10px">
        <div style="display:flex;gap:6px;align-items:center">
          <span class="pill ${iss.severity==='error'?'off':'ro'}" style="font-size:10px;padding:1px 7px">${esc(iss.viewport)} &bull; ${esc(iss.code)}</span>
          <code style="font-size:11px">${esc(iss.path)}</code>
        </div>
        <div class="sub" style="margin-top:4px">${esc(iss.message)}</div>
      </div>`).join('');
}

let ACTIVE_3D_PRESET = 'perspective';
let ACTIVE_3D_HUD = false;
let ACTIVE_3D_LABELS = true;
let ACTIVE_3D_GRID = true;
let ACTIVE_3D_YAW = 0;
let ACTIVE_3D_PITCH = 0;
let ACTIVE_3D_ZOOM = 1.0;

function renderAuditReportUI(d, labelPrefix){
  if(!d) return;
  const prefix = labelPrefix ? (labelPrefix + ' • ') : '';
  document.getElementById('scene-audit-summary').textContent =
    `${prefix}Audited ${d.totalPartsAudited||0} parts — Errors: ${d.errorCount||0}, Warnings: ${d.warningCount||0}` +
    (d.autoFixedUnanchored ? ` (Auto-anchored ${d.autoFixedUnanchored}!)` : '') +
    (d.autoSnappedFloating ? ` (Snapped ${d.autoSnappedFloating}!)` : '');
  const issues = d.issues || [];
  document.getElementById('scene-audit-list').innerHTML = issues.length === 0
    ? '<div class="pill on" style="display:inline-block">All 3D geometry grounded, anchored &amp; spawn-clear!</div>'
    : issues.map(iss => `
      <div class="cmd-row" style="flex-direction:column;align-items:flex-start;gap:3px">
        <div><span class="pill ${iss.severity==='error'?'off':'ro'}" style="font-size:10px;padding:1px 7px">${esc(iss.code)}</span> <code style="font-size:11px">${esc(iss.path)}</code></div>
        <div class="sub">${esc(iss.message)}</div>
      </div>`).join('');
}

async function refresh3DViewport(preset){
  if(preset) ACTIVE_3D_PRESET = preset;
  ['perspective', 'iso_ne', 'iso_nw', 'top_blueprint', 'front'].forEach(k => {
    const b = document.getElementById('cam-' + k);
    if(b) b.classList.toggle('primary', k === ACTIVE_3D_PRESET);
  });
  const srcSel = document.getElementById('vp3d-source');
  const source = (srcSel && srcSel.value) || 'studio:Workspace';
  const seed = parseInt((document.getElementById('dungeon-seed') && document.getElementById('dungeon-seed').value) || '2026', 10) || 2026;
  const theme = (document.getElementById('dungeon-theme') && document.getElementById('dungeon-theme').value) || 'horror_mansion';

  // 1. If targeting live Studio and a Studio peer is online, try MCP capture_viewport first
  if(source.startswith ? source.startswith('studio:') : source.indexOf('studio:') === 0){
    const studioPath = source.slice('studio:'.length) || 'Workspace';
    try {
      const res = await callMcp('capture_viewport', {
        path: studioPath,
        preset: ACTIVE_3D_PRESET,
        includeHud: ACTIVE_3D_HUD,
        showLabels: ACTIVE_3D_LABELS,
        overlays: { grid: ACTIVE_3D_GRID, labels: ACTIVE_3D_LABELS },
        yaw: ACTIVE_3D_YAW,
        pitch: ACTIVE_3D_PITCH,
        zoom: ACTIVE_3D_ZOOM,
        width: 680,
        height: 360
      });
      const imgItem = (res && res.content || []).find(c => c.type === 'image');
      const sc = (res && res.structuredContent) || {};
      if(!res.isError && imgItem && imgItem.data){
        document.getElementById('live-viewport-img').src = 'data:image/png;base64,' + imgItem.data;
        document.getElementById('live-viewport-meta').textContent =
          `Live Studio (${studioPath}) • Camera: ${sc.cameraPreset} [${(sc.cameraPosition||[]).join(', ')}] • Parts rendered: ${sc.partsRendered||0} • Faces: ${sc.facesRasterized||0} • HUD widgets: ${sc.hudElementsComposited||0}`;
        return;
      }
    } catch(_){}
  }

  // 2. Render via /api/kit/viewport-preview (live Studio or deterministic 3D showcase/prefab/dungeon fallback)
  try {
    const qs = new URLSearchParams({
      source,
      preset: ACTIVE_3D_PRESET,
      includeHud: ACTIVE_3D_HUD ? '1' : '0',
      showLabels: ACTIVE_3D_LABELS ? '1' : '0',
      showGrid: ACTIVE_3D_GRID ? '1' : '0',
      yaw: String(ACTIVE_3D_YAW),
      pitch: String(ACTIVE_3D_PITCH),
      zoom: String(ACTIVE_3D_ZOOM),
      seed: String(seed),
      theme,
      width: '680',
      height: '360'
    });
    const r = await apiFetch('/api/kit/viewport-preview?' + qs.toString());
    const d = await r.json();
    if(d && d.imageBase64){
      document.getElementById('live-viewport-img').src = 'data:image/png;base64,' + d.imageBase64;
      const sc = d.meta || {};
      document.getElementById('live-viewport-meta').textContent =
        `${d.sourceLabel || source} • Camera: ${sc.cameraPreset} [${(sc.cameraPosition||[]).join(', ')}] • Parts rendered: ${sc.partsRendered||0} • Faces: ${sc.facesRasterized||0} • HUD widgets: ${sc.hudElementsComposited||0}`;
      if(d.audit){
        renderAuditReportUI(d.audit, d.isFallback ? 'Offline Showcase' : '');
      }
    }
  } catch(e){
    document.getElementById('live-viewport-meta').textContent = String(e);
  }
}

function onChange3DSource(){
  ACTIVE_3D_YAW = 0;
  ACTIVE_3D_PITCH = 0;
  ACTIVE_3D_ZOOM = 1.0;
  refresh3DViewport();
}

function orbit3DCamera(dYaw, dPitch, zoomMult){
  ACTIVE_3D_YAW = (ACTIVE_3D_YAW + (dYaw || 0)) % 360;
  ACTIVE_3D_PITCH = Math.max(-65, Math.min(65, ACTIVE_3D_PITCH + (dPitch || 0)));
  if(zoomMult && zoomMult !== 1){
    ACTIVE_3D_ZOOM = Math.max(0.3, Math.min(3.0, +(ACTIVE_3D_ZOOM * zoomMult).toFixed(2)));
  }
  refresh3DViewport();
}

function reset3DCamera(){
  ACTIVE_3D_YAW = 0;
  ACTIVE_3D_PITCH = 0;
  ACTIVE_3D_ZOOM = 1.0;
  refresh3DViewport('perspective');
}

function toggle3DHud(){
  ACTIVE_3D_HUD = !ACTIVE_3D_HUD;
  const b = document.getElementById('btn-hud-toggle');
  if(b){
    b.textContent = 'HUD: ' + (ACTIVE_3D_HUD ? 'ON' : 'OFF');
    b.classList.toggle('primary', ACTIVE_3D_HUD);
  }
  refresh3DViewport();
}

function toggle3DLabels(){
  ACTIVE_3D_LABELS = !ACTIVE_3D_LABELS;
  const b = document.getElementById('btn-labels-toggle');
  if(b){
    b.textContent = 'Labels: ' + (ACTIVE_3D_LABELS ? 'ON' : 'OFF');
    b.classList.toggle('primary', ACTIVE_3D_LABELS);
  }
  refresh3DViewport();
}

function toggle3DGrid(){
  ACTIVE_3D_GRID = !ACTIVE_3D_GRID;
  const b = document.getElementById('btn-grid-toggle');
  if(b){
    b.textContent = 'Grid: ' + (ACTIVE_3D_GRID ? 'ON' : 'OFF');
    b.classList.toggle('primary', ACTIVE_3D_GRID);
  }
  refresh3DViewport();
}

async function run3DSceneAudit(fixUnanchored, snapFloating){
  const srcSel = document.getElementById('vp3d-source');
  const source = (srcSel && srcSel.value) || 'studio:Workspace';
  try {
    if(source.indexOf('studio:') === 0){
      const studioPath = source.slice('studio:'.length) || 'Workspace';
      const res = await callMcp('audit_3d_scene', {
        path: studioPath,
        autoFixUnanchored: Boolean(fixUnanchored),
        snapFloating: Boolean(snapFloating)
      });
      if(!res.isError && res.structuredContent){
        renderAuditReportUI(res.structuredContent, `Live Studio (${studioPath})`);
        if(fixUnanchored || snapFloating) refresh3DViewport();
        return;
      }
    }
    const r = await apiFetch('/api/kit/viewport-preview?source=' + encodeURIComponent(source));
    const d = await r.json();
    if(d && d.audit){
      renderAuditReportUI(d.audit, d.sourceLabel || source);
    }
  } catch(e){
    document.getElementById('scene-audit-summary').textContent = String(e);
  }
}

function setIconPack(pk){
  ACTIVE_ICON_PACK = pk;
  ['all', 'stud', 'lucide', 'lucide_fill'].forEach(k => {
    const b = document.getElementById('pk-' + k);
    if(b) b.classList.toggle('primary', k === pk);
  });
  ACTIVE_CLOUD_CAT = '';
  searchCloudIconsUI('');
}

async function searchCloudIconsUI(catOverride){
  if(catOverride !== undefined) ACTIVE_CLOUD_CAT = catOverride;
  const q = (document.getElementById('cloud-icon-q').value || '').trim();
  try {
    const qs = new URLSearchParams({ query: q, pack: ACTIVE_ICON_PACK, limit: '36' });
    if(ACTIVE_CLOUD_CAT) qs.set('category', ACTIVE_CLOUD_CAT);
    const r = await apiFetch('/api/kit/cloud-icons?' + qs.toString());
    const d = await r.json();
    const cats = d.categories || {};
    document.getElementById('cloud-icon-cats').innerHTML =
      `<button class="sm ${ACTIVE_CLOUD_CAT===''?'primary':''}" ${actData("searchCloudIconsUI", "")}>All (${d.totalCatalogIcons||4811})</button>` +
      Object.entries(cats).map(([c, n]) =>
        `<button class="sm ${ACTIVE_CLOUD_CAT===c?'primary':''}" ${actData("searchCloudIconsUI", c)}>${esc(c)} (${n})</button>`
      ).join('');
    document.getElementById('cloud-icon-results').innerHTML = (d.icons || []).map(ic => `
      <div class="cmd-row" style="align-items:center;justify-content:space-between">
        <div style="min-width:0">
          <strong style="font-size:12px">${esc(ic.name)}</strong> <span class="sub">[${esc(ic.pack)}]</span>
          <div class="sub" style="font-size:11px;overflow:hidden;text-overflow:ellipsis">${esc(ic.path)} &bull; <code>${esc(ic.uri)}</code></div>
        </div>
        <div style="display:flex;gap:4px;flex-shrink:0">
          <button class="sm" ${actData("copyText", ic.uri)}>Copy</button>
          <button class="sm primary" ${actData("spawnCloudIconPreview", ic.uri, ic.name)}>Spawn</button>
        </div>
      </div>`).join('');
  } catch(_){}
}

async function loadKitGallery(){
  try {
    let data = {};
    try {
      const r = await apiFetch('/api/kit/catalog?category=all&format=full');
      if(r.ok) data = await r.json();
    } catch(_){}
    if(!data.images){
      const res = await callMcp('browse_kit', { category: 'all', format: 'full' });
      data = res.structuredContent || {};
    }

    const templates = data.templates || [];
    document.getElementById('template-list').innerHTML = templates.map(t => `
      <div class="cmd-row">
        <div><strong>${esc(t.title)}</strong> <code style="font-size:11px">${esc(t.name)}</code><div class="sub">${esc(t.description)}</div></div>
        <div class="spacer"></div>
        <button class="sm primary" ${actData("installTemplateUI", t.name)}>Install</button>
      </div>`).join('');

    const systems = data.systems || [];
    document.getElementById('dropin-system-list').innerHTML = systems.map(s => `
      <div class="cmd-row">
        <div><strong>${esc(s.system)}</strong> <span class="sub">(${s.scriptCount} scripts)</span><div class="sub">${esc(s.summary)}</div></div>
        <div class="spacer"></div>
        <button class="sm primary" ${actData("installDropinSystemUI", s.system)}>Install</button>
      </div>`).join('');

    const synthSpecs = data.audioSynthSpecs || [];
    const synthBox = document.getElementById('synth-audio-list');
    if(synthBox){
      synthBox.innerHTML = synthSpecs.map(s => `
        <div class="cmd-row" style="justify-content:space-between;padding:6px 8px;border-color:rgba(92,200,255,.3)">
          <div style="min-width:0">
            <code style="font-size:11px;color:var(--accent)">[Synth] ${esc(s.name)}</code>
            <div class="sub" style="font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(s.description||'')}</div>
          </div>
          <div style="display:flex;gap:4px;flex-shrink:0">
            <button class="sm" ${actData("playSynthAudio", s.name)}>&#9654;</button>
            <button class="sm primary" ${actData("sendSynthAudioToStudio", s.name)}>Send</button>
          </div>
        </div>`).join('');
    }

    const audios = data.audio || [];
    ALL_KIT_AUDIO = audios;
    document.getElementById('audio-list').innerHTML = audios.map(a => `
      <div class="cmd-row" style="justify-content:space-between;padding:6px 8px">
        <code style="font-size:11px">${esc(a.name)}</code>
        <div style="display:flex;gap:4px">
          <button class="sm" ${actData("playBrowserAudio", a.fileName)}>&#9654;</button>
          <button class="sm primary" ${actData("sendAudioToStudio", a.fileName)}>Send</button>
        </div>
      </div>`).join('');

    const presets = data.proceduralPresets || [];
    const presetBox = document.getElementById('procedural-preset-list');
    if(presetBox){
      presetBox.innerHTML = presets.map(p => `
        <div class="card" style="padding:8px;display:flex;flex-direction:column;align-items:center;gap:5px;text-align:center">
          <img src="/api/kit/procedural/${esc(p)}.png" alt="${esc(p)}" loading="lazy" style="width:48px;height:48px;border-radius:6px;background:#0d121a">
          <code style="font-size:10px;word-break:break-all">${esc(p)}</code>
          <button class="sm primary" ${actData("sendProceduralPresetToStudio", p)}>Send</button>
        </div>`).join('');
    }

    const modules = data.modules || [];
    const modBox = document.getElementById('module-list');
    if(modBox){
      modBox.innerHTML = modules.map(m => `
        <div class="cmd-row" style="justify-content:space-between;padding:6px 8px">
          <div><strong style="font-size:12px">${esc(m.name)}</strong> <span class="sub">${(m.bytes/1024).toFixed(1)}K</span></div>
          <div style="display:flex;gap:4px">
            <button class="sm" ${actData("inspectSingleModule", m.name)}>Code</button>
            <button class="sm primary" ${actData("installSingleModule", m.name)}>Install</button>
          </div>
        </div>`).join('');
    }

    const prefabs = data.prefabs || [];
    document.getElementById('prefab-list').innerHTML = prefabs.map(p => `
      <div class="cmd-row">
        <div><strong>${esc(p.name)}</strong> <span class="pill" style="font-size:10px;padding:1px 6px">${esc(p.category)}</span><div class="sub">${esc(p.description)}</div></div>
        <div class="spacer"></div>
        <button class="sm primary" ${actData("spawnKitPrefab", p.name)}>Spawn</button>
      </div>`).join('');

    ALL_KIT_IMAGES = data.images || [];
    ALL_KIT_MODELS = data.models || [];
    const totalLicensed = ALL_KIT_IMAGES.length + ALL_KIT_MODELS.length + ALL_KIT_AUDIO.length;
    const setKpi = (id, v) => { const el = document.getElementById(id); if(el) el.textContent = v.toLocaleString(); };
    setKpi('kit-cnt-assets', totalLicensed || 4320);
    setKpi('kit-cnt-icons', data.cloudIconsCount || 4811);
    setKpi('kit-cnt-modules', modules.length || 25);
    setKpi('kit-cnt-systems', systems.length || 20);
    setKpi('kit-cnt-prefabs', prefabs.length || 26);
    setKpi('kit-cnt-models', ALL_KIT_MODELS.length || 520);

    const catCounts = data.imageCategories || {};
    document.getElementById('asset-pack-pills').innerHTML =
      `<button class="sm ${ACTIVE_PACK==='all'?'primary':''}" data-pack="all" ${actData("selectPack", "all")}>All Sources (${totalLicensed})</button>` +
      Object.entries(catCounts).sort().map(([pack, cnt]) =>
        `<button class="sm ${ACTIVE_PACK===pack?'primary':''}" data-pack="${esc(pack)}" ${actData("selectPack", pack)}>${esc(pack)} (${cnt})</button>`
      ).join('') +
      `<button class="sm ${ACTIVE_PACK==='audio_cc0'?'primary':''}" data-pack="audio_cc0" ${actData("selectPack", "audio_cc0")}>audio_cc0 (${ALL_KIT_AUDIO.length})</button>`;

    renderFilteredAssets();
    searchCloudIconsUI('');
    runMechanicBuilder(false);
    previewDungeonLayout(false);
    runUiPreviewAndAudit();
    refresh3DViewport('perspective');
    run3DSceneAudit(false, false);
  } catch(_){}
}

function bindViewportInteractiveControls(){
  const stage = document.getElementById('live-viewport-stage');
  if(!stage || stage.__boundOrbit) return;
  stage.__boundOrbit = true;
  let dragging = false, startX = 0, startY = 0, baseYaw = 0, basePitch = 0;
  stage.addEventListener('pointerdown', e => {
    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    baseYaw = ACTIVE_3D_YAW;
    basePitch = ACTIVE_3D_PITCH;
    stage.style.cursor = 'grabbing';
    try { stage.setPointerCapture(e.pointerId); } catch(_){}
  });
  stage.addEventListener('pointermove', e => {
    if(!dragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    ACTIVE_3D_YAW = Math.round(baseYaw - dx * 0.55) % 360;
    ACTIVE_3D_PITCH = Math.max(-65, Math.min(65, Math.round(basePitch + dy * 0.45)));
  });
  const finishDrag = e => {
    if(!dragging) return;
    dragging = false;
    stage.style.cursor = 'grab';
    if(Math.abs(e.clientX - startX) > 3 || Math.abs(e.clientY - startY) > 3){
      refresh3DViewport();
    }
  };
  stage.addEventListener('pointerup', finishDrag);
  stage.addEventListener('pointercancel', finishDrag);
  stage.addEventListener('wheel', e => {
    e.preventDefault();
    const mult = e.deltaY > 0 ? 1.12 : 0.88;
    orbit3DCamera(0, 0, mult);
  }, { passive: false });
}

async function initBoot(){
  // Token precedence: ?token= / #token= in the URL (a one-time paste convenience
  // — it is stripped from the address bar with history.replaceState immediately
  // and never re-sent), then this browser's saved token, then whatever
  // /api/local-boot is willing to disclose (nothing, unless you are admin).
  try {
    const url = new URL(location.href);
    const fromQuery = url.searchParams.get('token') || (url.hash.indexOf('#token=') === 0 ? url.hash.slice(7) : '');
    if(fromQuery){
      setToken(fromQuery); storeToken(getToken());
      url.searchParams.delete('token');
      history.replaceState({}, '', url.pathname + (url.search || ''));
    }
  } catch(_){}
  if(!getToken()) setToken(readStoredToken());
  try {
    // v6.1.0 (P0-S2): the boot endpoint no longer hands the owner token to an
    // unauthenticated caller, so send whatever this browser already has. An
    // admin credential still self-fills; anything less gets the banner.
    const r = await fetch('/api/local-boot', { cache:'no-store', headers: authHeaders() });
    BOOT = await r.json();
    if(BOOT.token && !getToken()){
      setToken(BOOT.token);
      storeToken(BOOT.token);
    }
    if(BOOT.tokenRequired && !getToken()){
      // The banner renders through textContent, so this stays plain text: no
      // markup can be injected here, and none is needed.
      showAuthBanner(BOOT.local ? 'This bridge is protected by a token'
                                : 'Opened from a tunnel or LAN address',
        'The bridge no longer hands out its owner token over HTTP. Paste the contents of '
        + 'roblox-bridge/bridge.token (printed by setup.py, or `python setup.py --token`) '
        + '\u2014 it is remembered in this browser afterwards.');
    } else if(BOOT.tokenWithheld && getToken() && BOOT.role !== 'admin'){
      showAuthBanner('Signed in with a limited token',
        'This credential is ' + (BOOT.role || 'read') + '-scoped, so admin surfaces stay locked. '
        + 'Paste the owner token from bridge.token for full access.');
    }
    const sel = document.getElementById('tool-select');
    sel.innerHTML = BOOT.tools.map(t =>
      `<option value="${esc(t.name)}">${esc(t.name)}${t.readOnly ? ' (read-only)' : ''}</option>`
    ).join('');
    onSelectTool();
  } catch(_){}
  bindViewportInteractiveControls();
  renderTips();
  renderPrompts();
  refresh3DViewport('perspective');
  run3DSceneAudit(false, false);
  loadKitGallery();
  loadPluginDashboard();
}

async function loadPluginDashboard(){
  try {
    const r = await apiFetch('/api/plugin-status');
    const d = await r.json();
    const setEl = (id, v) => { const el = document.getElementById(id); if(el) el.textContent = v; };
    setEl('plg-bundled-ver', 'v' + (d.bundledPluginVersion || PAGE_VERSION));
    setEl('plg-peer-cnt', (d.onlinePeerCount || 0) + ' online');
    setEl('plg-compat-state', d.outdatedPeerCount > 0 ? (d.outdatedPeerCount + ' outdated') : 'All v6 Up-to-Date');
    const ptActive = d.playtestState && d.playtestState.playtestActive;
    setEl('plg-playtest-state', ptActive ? 'RUNNING (' + (d.playtestState.serverPeersOnline || 0) + ' srv)' : 'Idle / Clean');
    const tel = d.livePluginTelemetry || {};
    setEl('plg-dockwidget', tel.dashboardAvailable ? 'DockWidget v6 Ready' : (d.onlinePeerCount ? 'Toolbar Active' : 'Connect Studio'));
    setEl('plg-jobs-run', String(tel.jobsProcessed != null ? tel.jobsProcessed : 0));

    const ptb = document.getElementById('plg-peer-table');
    if(ptb){
      const peers = d.peers || [];
      if(!peers.length){
        ptb.innerHTML = '<tr><td colspan="5" class="empty">No Studio peers currently online. Open Roblox Studio with <code>ArenaBridge.lua</code> installed.</td></tr>';
      } else {
        ptb.innerHTML = peers.map(p => `<tr>
          <td><code>${esc(p.peerId)}</code></td>
          <td><code>${esc(p.dm)}</code></td>
          <td>${esc(p.place || '')}</td>
          <td><code>v${esc(p.pluginVersion || '?')}</code></td>
          <td class="${p.upToDate ? 's-done' : 's-error'}">${p.upToDate ? '&#10003; v6 Up-to-Date' : '&#9888; Update to v' + esc(d.bundledPluginVersion || PAGE_VERSION)}</td>
        </tr>`).join('');
      }
    }
    const tbox = document.getElementById('plg-telemetry-box');
    if(tbox){
      if(d.livePluginTelemetry){
        tbox.innerHTML = `Live In-Studio Telemetry: <strong>${d.livePluginTelemetry.jobsProcessed || 0}</strong> jobs processed (${d.livePluginTelemetry.jobsFailed || 0} errors) &bull; Last job: <code>${esc(d.livePluginTelemetry.lastJobType || 'none')}</code> (${d.livePluginTelemetry.lastJobMs || 0}ms) &bull; DockWidget UI: <strong>${d.livePluginTelemetry.dashboardAvailable ? 'Available' : 'N/A'}</strong>`;
      } else if(d.outdatedPeerCount > 0){
        tbox.innerHTML = `<span style="color:var(--warn)">Connected Studio is running an older plugin version. Click <strong>Download ArenaBridge.lua v${PAGE_VERSION}</strong> or <strong>Install to Local Studio</strong> and reload plugins in Roblox Studio.</span>`;
      } else {
        tbox.textContent = 'Connect a Roblox Studio Edit session to view live in-Studio plugin telemetry.';
      }
    }
  } catch(_){}
}

async function downloadPluginFile(){
  // v6.1.0 (P0-S2): this used to `window.open('/api/plugin/download?token=…')`,
  // which put a credential in a URL (history, proxy logs, Referer) *and* handed
  // back the owner token baked into the plugin. The route is admin-only now and
  // bakes a short-TTL write token, so we fetch it with headers and hand the body
  // to the browser as a blob.
  const out = document.getElementById('plg-action-out');
  try {
    toast(`Requesting pre-configured ArenaBridge.lua v${PAGE_VERSION}…`);
    const r = await apiFetch('/api/plugin/download');
    if(!r.ok){
      let detail = '';
      try { detail = (await r.json()).error || ''; } catch(_){}
      const msg = r.status === 403
        ? `Admin token required to mint a plugin credential (HTTP 403). ${detail}`.trim()
        : `Plugin download failed: HTTP ${r.status} ${detail}`.trim();
      if(out) out.textContent = msg;
      toast(msg);
      return;
    }
    const text = await r.text();
    const blob = new Blob([text], { type: 'text/x-lua;charset=utf-8' });
    const href = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = href;
    a.download = 'ArenaBridge.lua';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 4000);
    if(out) out.textContent = 'ArenaBridge.lua downloaded. It carries a dedicated write token '
      + '(not your owner credential) that expires in 24h — revoke it any time from the Ops console.';
    toast('ArenaBridge.lua downloaded');
  } catch(err){
    const msg = `Plugin download failed: ${err.message}`;
    if(out) out.textContent = msg;
    toast(msg);
  }
}

async function installLocalPluginUI(){
  const out = document.getElementById('plg-action-out');
  if(out) out.textContent = `Installing ArenaBridge.lua v${PAGE_VERSION} into local Studio Plugins folder…`;
  try {
    const r = await apiFetch('/api/plugin-status', {
      method: 'POST',
      body: JSON.stringify({ action: 'install_local' })
    });
    const d = await r.json();
    if(out) out.textContent = JSON.stringify(d, null, 2);
    toast(d.installedToOsPlugins ? `Installed ArenaBridge.lua v${PAGE_VERSION} to local Studio Plugins!` : 'Local Studio Plugins folder not found on this host — use Download button instead.');
    loadPluginDashboard();
  } catch(e){
    if(out) out.textContent = String(e);
  }
}

async function forceStopPlaytestUI(){
  toast('Force-stopping active playtest & evicting ghost runtime peers…');
  const out = document.getElementById('plg-action-out');
  if(out) out.textContent = 'Dispatching dual edit + server force_stop + runner cleanup + ghost peer eviction…';
  try {
    const r = await apiFetch('/api/playtest/force-stop', {
      method: 'POST',
      body: JSON.stringify({ reason: 'dashboard_force_stop' })
    });
    const d = await r.json();
    if(out) out.textContent = JSON.stringify(d, null, 2);
    const wrap = document.getElementById('quick-out-wrap');
    const pre = document.getElementById('quick-out');
    if(wrap && pre){
      document.getElementById('quick-out-title').textContent = 'Playtest Force-Stop & Cleanup [OK]';
      pre.textContent = JSON.stringify(d, null, 2);
      wrap.style.display = 'block';
    }
    toast(`Playtest stopped cleanly (${d.evictedPlaytestPeers || 0} runtime peers evicted).`);
    tick();
    refreshFleet();
    loadPluginDashboard();
  } catch(e){
    if(out) out.textContent = String(e);
    toast('Force-stop error: ' + e.message);
  }
}

async function evictGhostPeersUI(){
  const out = document.getElementById('plg-action-out');
  try {
    const r = await apiFetch('/api/playtest/force-stop', {
      method: 'POST',
      body: JSON.stringify({ action: 'evict_only', reason: 'manual_ghost_peer_evict' })
    });
    const d = await r.json();
    if(out) out.textContent = JSON.stringify(d, null, 2);
    toast(`Evicted ${d.evictedPeers || 0} runtime peer(s).`);
    tick();
    refreshFleet();
    loadPluginDashboard();
  } catch(e){
    if(out) out.textContent = String(e);
  }
}

async function runV6ToolPreview(toolName, argsObj){
  const out = document.getElementById('v6-tool-out');
  if(out) out.textContent = `Running ${toolName}…`;
  try {
    const res = await callMcp(toolName, argsObj);
    if(out) out.textContent = JSON.stringify(res.structuredContent || res, null, 2);
    toast(`Completed ${toolName}`);
    tick();
  } catch(e){
    if(out) out.textContent = String(e);
  }
}

async function tick(){
  try {
    const r = await apiFetch('/api/state');
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const s = await r.json();
    if(s && s.version && PAGE_VERSION && s.version !== PAGE_VERSION){
      showStaleBanner(s.version);
    }
    const prevPlace = LAST_STATE.studio && LAST_STATE.studio.place;
    const wasConnected = Boolean(LAST_STATE.studio_connected);
    LAST_STATE = s;
    if((!wasConnected && s.studio_connected) || (s.studio && s.studio.place && s.studio.place !== prevPlace)){
      refresh3DViewport();
      run3DSceneAudit(false, false);
    }

    const st = document.getElementById('status');
    st.textContent = s.studio_connected ? 'Studio connected' : 'Waiting for Studio';
    st.className = 'pill ' + (s.studio_connected ? 'on' : 'off');
    document.getElementById('ro-badge').style.display = s.read_only ? 'inline-block' : 'none';

    const pl = document.getElementById('place');
    if(s.studio && s.studio.place){
      pl.style.display = 'inline-block';
      pl.textContent = s.studio.place + ' (PlaceId ' + (s.studio.place_id || 0) + ')';
      if(s.studio.place !== prevPlace) renderPrompts();
    } else {
      pl.style.display = 'none';
    }

    const peers = s.peers || [];
    const onlinePeers = peers.filter(p => p.online);
    document.getElementById('c-peers').textContent = onlinePeers.length + ' online';
    document.getElementById('c-tools').textContent = s.mcp_tools ?? '—';
    document.getElementById('c-queued').textContent = s.queued || 0;
    document.getElementById('c-snaps').textContent = s.snapshots || 0;
    const rows = s.recent || [];
    document.getElementById('c-done').textContent = rows.filter(j => j.status === 'done').length;
    document.getElementById('c-fail').textContent = rows.filter(j => j.status === 'error').length;

    const ptb = document.getElementById('peer-rows');
    if(!peers.length){
      ptb.innerHTML = '<tr><td colspan="6" class="empty">Waiting for Roblox Studio plugin to poll <code>/api/poll</code>&hellip;</td></tr>';
    } else {
      ptb.innerHTML = peers.map(p => {
        const pv = String(p.pluginVersion || '?');
        const isV6 = pv.startsWith('6.');
        const pvBadge = isV6
          ? `<span class="s-done">v${esc(pv)} &#10003;</span>`
          : `<span class="s-error" title="Update ArenaBridge.lua to v${PAGE_VERSION} in Tab 8">v${esc(pv)} &#9888;</span>`;
        return `<tr>
          <td><code>${esc(p.peerId)}</code></td>
          <td><code>${esc(p.dm)}</code></td>
          <td>${esc(p.place || '')}</td>
          <td>${pvBadge}</td>
          <td>${p.hasClient ? 'ready (' + p.playerCount + 'p)' : '&mdash;'}</td>
          <td class="${p.online ? 's-done' : 's-queued'}">${p.online ? 'online' : 'stale'}</td>
        </tr>`;
      }).join('');
    }

    const tb = document.getElementById('rows');
    if(!rows.length){
      tb.innerHTML = '<tr><td colspan="7" class="empty">No jobs executed yet.</td></tr>';
      return;
    }
    tb.innerHTML = rows.map(j => {
      const out = j.error ? j.error : (j.result == null ? '' : (typeof j.result === 'string' ? j.result : JSON.stringify(j.result)));
      const lat = j.duration_ms != null ? j.duration_ms + 'ms' : '&mdash;';
      return `<tr class="clickable" ${actData("inspectJob", j.id)}>
        <td><code>${esc(j.id)}</code></td>
        <td>${esc(j.type)}</td>
        <td><code>${esc(j.dm || 'auto')}</code></td>
        <td>${lat}</td>
        <td>${esc(j.note || '')}</td>
        <td class="s-${esc(j.status)}">${esc(j.status)}</td>
        <td>${esc(out).slice(0, 190)}</td>
      </tr>`;
    }).join('');
  } catch(_){}
}

// ---------------------------- v5 Ops Console ----------------------------
let EV_SOURCE = null, EV_RETRY = null, EV_DELAY = 1000, EV_WANT = false;
let QUEUE_PAUSED = false;
async function opsJson(path, opts){
  const r = await apiFetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if(!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
  return data;
}
const REDUCED_MOTION = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function animateValue(el, to){
  if(!el) return;
  const target = Number(to);
  if(!isFinite(target)){ el.textContent = to; return; }
  if(REDUCED_MOTION || document.hidden){ el.textContent = target; return; }
  const from = Number(String(el.textContent).replace(/[^0-9.-]/g, '')) || 0;
  if(from === target){ el.textContent = target; return; }
  const t0 = performance.now(), dur = 420;
  const step = (t) => {
    const p = Math.min(1, (t - t0) / dur);
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = Math.round(from + (target - from) * eased);
    if(p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
let FLEET = {studios: [], count: 0, online: 0, pinned: null, routing: 'auto'};
function studioCard(s, detailed){
  const dot = s.online ? 'dot-live' : 'dot-live dot-dead';
  const cls = 'studio-card' + (s.pinned ? ' pinned' : '') + (s.defaultTarget ? ' active-target' : '');
  const age = s.online ? (s.ageSeconds < 2 ? 'live' : Math.round(s.ageSeconds) + 's ago') : 'offline';
  const pin = s.pinned
    ? `<button class="pin" ${actData("unpinStudio")} title="Unpin">pinned</button>`
    : `<button class="pin" ${actData('pinStudio', s.peerId)} title="Make this the default Studio">pin</button>`;
  return `<div class="${cls}">
    <span class="${dot}"></span>
    <div>
      <div class="place" title="${esc(s.place)} · ${esc(s.peerId)}">${esc(s.label || s.place)}</div>
      <div class="meta"><span class="badge-dm">${esc(s.dm)}</span>
        <span>${esc(age)}</span>
        ${s.queuedJobs ? `<span>· ${s.queuedJobs} queued</span>` : ''}
        ${detailed && s.placeId ? `<span>· id ${esc(String(s.placeId))}</span>` : ''}
        ${s.defaultTarget ? '<span>· default</span>' : ''}
      </div>
    </div>
    ${pin}
  </div>`;
}
function renderFleet(d){
  FLEET = d || FLEET;
  const studios = FLEET.studios || [];
  const chips = document.getElementById('fleet-chips');
  const detail = document.getElementById('fleet-detail');
  const badge = document.getElementById('fleet-badge');
  const dot = document.getElementById('fleet-dot');
  if(chips){
    chips.innerHTML = studios.length
      ? studios.map(s => studioCard(s, false)).join('')
      : '<span class="sub">No Studio peers yet — open Roblox Studio with the ArenaBridge plugin.</span>';
  }
  if(detail){
    detail.innerHTML = studios.length
      ? studios.map(s => studioCard(s, true)).join('')
      : '<span class="sub">No Studio windows connected yet.</span>';
  }
  if(badge){
    badge.style.display = studios.length ? 'inline-flex' : 'none';
    badge.textContent = `${FLEET.online || 0}/${studios.length} studios`;
  }
  if(dot) dot.classList.toggle('dot-dead', !(FLEET.online > 0));
  const note = document.getElementById('fleet-note');
  if(note){
    note.textContent = !studios.length ? '' :
      (FLEET.pinned
        ? `Pinned: ${FLEET.pinOnline ? 'routing untargeted jobs to the pinned Studio' : 'pinned Studio is offline — falling back to auto edit-first routing'}`
        : 'Routing: auto (edit DataModels first). Pin a Studio to make it the default target.');
  }
}
async function refreshFleet(){
  try { renderFleet(await opsJson('/api/studios')); } catch(_){}
}
async function pinStudio(peerId){
  try {
    const res = await opsJson('/api/studios', {method:'POST', body:JSON.stringify({action:'set_active', peerId})});
    toast(`Pinned ${res.place || peerId} as the default Studio.`);
    refreshFleet(); refreshOps();
  } catch(err){ toast(err.message); }
}
async function unpinStudio(){
  try {
    await opsJson('/api/studios', {method:'POST', body:JSON.stringify({action:'clear_active'})});
    toast('Pin cleared — back to auto routing.');
    refreshFleet(); refreshOps();
  } catch(err){ toast(err.message); }
}
async function broadcastPing(){
  const box = document.getElementById('fleet-broadcast');
  if(box){ box.style.display = 'block'; box.textContent = 'Broadcasting to every online Studio…'; }
  try {
    const res = await opsJson('/api/studios', {method:'POST', body:JSON.stringify({action:'broadcast', type:'ping', timeout:12})});
    const rows = (res.results || []).map(r =>
      `${r.ok ? '✓' : '✗'} ${r.label || r.peerId} · ${r.status}${r.durationMs != null ? ' · ' + Math.round(r.durationMs) + 'ms' : ''}${r.error ? ' · ' + r.error : ''}`);
    if(box) box.textContent = `${res.succeeded}/${res.peers} studios responded in ${res.elapsedMs}ms\n` + (rows.join('\n') || res.error || 'no peers');
    toast(res.peers ? `${res.succeeded}/${res.peers} studios answered the broadcast.` : (res.error || 'No Studio peers.'));
    refreshFleet();
  } catch(err){
    if(box) box.textContent = err.message;
    toast(err.message);
  }
}
async function refreshOps(){
  try {
    const ops = await opsJson('/api/ops');
    const t = ops.tunnel || {};
    document.getElementById('ops-tunnel-state').textContent = t.state || 'unknown';
    document.getElementById('ops-tunnel-url').textContent = t.publicUrl || '—';
    const pill = document.getElementById('ops-tunnel-pill');
    pill.textContent = t.state || 'unknown';
    pill.className = 'pill ' + (t.state === 'online' ? 'on' : (t.state === 'degraded' ? 'ro' : 'off'));
    document.getElementById('ops-agents').textContent = (ops.bus && ops.bus.agentsOnline) || 0;
    document.getElementById('ops-tasks').textContent = (ops.openTasks || []).length;
    animateValue(document.getElementById('ops-queue'), (ops.health && ops.health.queued) || 0);
    if(ops.fleet) renderFleet(ops.fleet);
    document.getElementById('ops-audit-count').textContent = (ops.audit || []).length;
    const health = t.health || {};
    document.getElementById('ops-tunnel-meta').textContent =
      `flavor=${t.flavor || '—'} · restarts=${t.restarts || 0} · health=${health.ok === null || health.ok === undefined ? 'n/a' : (health.ok ? 'ok' : 'failing')}` +
      (health.latencyMs != null ? ` · ${health.latencyMs}ms` : '') + (t.lastError ? ' · ' + t.lastError : '');
    document.getElementById('ops-tunnel-lan').textContent = (t.lanUrls || []).length ? 'LAN: ' + t.lanUrls.join('  ·  ') : '';
    document.getElementById('ops-tunnel-log').textContent = (t.logTail || []).join('\n') || 'No tunnel output yet.';
    if((ops.audit || []).length){
      document.getElementById('ops-audit').textContent = ops.audit.slice(0,8).map(e => `${e.seq} ${e.action} · ${e.actor} · ${e.outcome}`).join('\n');
    }
    renderBus(ops.openTasks || [], null);
    // Queue pause switch + sweeper status.
    const queue = ops.queue || {};
    QUEUE_PAUSED = !!queue.paused;
    const qBtn = document.getElementById('queue-toggle');
    if(qBtn) qBtn.textContent = QUEUE_PAUSED ? 'Resume queue' : 'Pause queue';
    const qState = document.getElementById('queue-state');
    if(qState) qState.textContent = QUEUE_PAUSED
      ? `Queue paused${queue.reason ? ' — ' + queue.reason : ''} · ${queue.depth == null ? '?' : queue.depth} held`
      : `Queue live · ${queue.depth == null ? 0 : queue.depth} queued`;
    const sweeper = ops.sweeper || {};
    const sState = document.getElementById('sweeper-state');
    if(sState) sState.textContent = sweeper.intervalSeconds
      ? `Sweeper every ${Math.round(sweeper.intervalSeconds)}s (silent > ${Math.round(sweeper.offlineSeconds || 0)}s) · reaped ${sweeper.tasksReaped || 0}`
      : 'Sweeper disabled';
    const latency = ops.jobLatency || {};
    const row = document.getElementById('ops-latency');
    if(row){
      const top = Object.entries(latency).slice(0, 4)
        .map(([tool, d]) => `${tool} p50 ${d.p50}ms / p95 ${d.p95}ms (n=${d.count})`);
      row.textContent = top.length ? 'Latency: ' + top.join(' · ') : '';
    }
  } catch(_){}
}
async function tunnelAction(action){
  try {
    const r = await fetch('/api/tunnel', {method:'POST', headers:authHeaders(), body:JSON.stringify({action})});
    const data = await r.json();
    if(!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    toast('Tunnel ' + action + ' → ' + (data.state || 'ok'));
    refreshOps();
  } catch(err){ toast('Tunnel ' + action + ' failed: ' + err.message); }
}
async function busRegister(){
  const name = document.getElementById('bus-name').value.trim();
  const role = document.getElementById('bus-role').value.trim() || 'agent';
  if(!name) return toast('Enter an agent name.');
  try {
    const reg = await opsJson('/api/bus', {method:'POST', body:JSON.stringify({action:'register', name, role})});
    localStorage.setItem('arena_agent_id', reg.id);
    localStorage.setItem('arena_agent_name', reg.name);
    document.getElementById('bus-out').textContent = `Registered ${reg.name} as ${reg.id} (${reg.role})`;
    refreshBus();
  } catch(err){ toast(err.message); }
}
function currentAgent(){ return localStorage.getItem('arena_agent_id') || 'web-operator'; }
async function busPost(){
  const to = document.getElementById('bus-to').value.trim() || null;
  const subject = document.getElementById('bus-subject').value.trim();
  const body = document.getElementById('bus-body').value.trim();
  if(!subject && !body) return toast('Write a subject or body first.');
  try {
    const msg = await opsJson('/api/bus', {method:'POST', body:JSON.stringify({
      action:'post', agentId: currentAgent(), to, subject, body: body || subject, kind:'note', priority:'normal'
    })});
    document.getElementById('bus-out').textContent = 'Posted ' + msg.id;
    document.getElementById('bus-body').value = '';
    refreshBus();
  } catch(err){ toast(err.message); }
}
async function busExport(){
  try {
    const r = await opsJson('/api/bus', {method:'POST', body:JSON.stringify({action:'export', agentId:currentAgent()})});
    const note = document.getElementById('bus-out');
    note.textContent = r.markdown;
    copyText(r.markdown);
    toast('Handoff brief copied to clipboard.');
  } catch(err){ toast(err.message); }
}
function renderBus(tasks, agents){
  const taskBox = document.getElementById('bus-tasks');
  if(!tasks.length){
    taskBox.innerHTML = '<div class="sub">No open tasks. Create one from any agent with <code>agent_bus {action:"task_create"}</code>.</div>';
  } else {
    taskBox.innerHTML = tasks.map(t => {
      const lease = t.leaseRemainingSeconds != null ? Math.max(0, Math.round(t.leaseRemainingSeconds)) + 's lease' : 'unclaimed';
      return `<div class="cmd-row" style="align-items:flex-start">
        <div style="flex:1">
          <div><strong>${esc(t.title)}</strong> <span class="sub">· ${esc(t.priority)} · ${esc(t.status)} · ${lease}</span></div>
          <div class="sub">${esc(t.assignee || 'unassigned')} ${t.blockedBy && t.blockedBy.length ? '· blocked by ' + esc(t.blockedBy.join(', ')) : ''}</div>
        </div>
        <button class="sm" ${actData("claimTask", t.id)}>Claim</button>
        <button class="sm" ${actData("completeTask", t.id)}>Done</button>
      </div>`;
    }).join('');
  }
  if(agents){
    const box = document.getElementById('bus-agents');
    box.innerHTML = agents.length ? agents.map(a =>
      `<div style="display:flex;justify-content:space-between;gap:8px;padding:3px 0">
         <span><strong>${esc(a.name)}</strong> <span class="sub">${esc(a.role)}</span></span>
         <span class="sub">${esc(a.status)} · ${a.openTasks} tasks · ${a.inboxUnread} unread</span>
       </div>`).join('') : 'No agents registered yet.';
  }
}
async function refreshBus(){
  try {
    const agents = await opsJson('/api/bus?action=agents');
    const tasks = await opsJson('/api/bus?action=tasks&includeDone=0');
    renderBus(tasks.tasks || [], agents.agents || []);
    document.getElementById('ops-agents').textContent = agents.onlineCount || 0;
    document.getElementById('ops-tasks').textContent = (tasks.tasks || []).length;
  } catch(_){}
}
async function createTask(){
  const title = document.getElementById('task-title').value.trim();
  if(!title) return toast('Enter a task title first.');
  try {
    await opsJson('/api/bus', {method:'POST', body:JSON.stringify({
      action:'task_create', title, priority:document.getElementById('task-priority').value,
      agentId: currentAgent()
    })});
    document.getElementById('task-title').value = '';
    toast('Task added to the board.');
    refreshBus();
  } catch(err){ toast(err.message); }
}
async function toggleQueue(){
  const wantPause = !QUEUE_PAUSED;
  try {
    const res = await opsJson(wantPause ? '/api/queue/pause' : '/api/queue/resume', {
      method:'POST',
      body: JSON.stringify(wantPause ? {reason: 'paused from the Ops Console'} : {})
    });
    QUEUE_PAUSED = !!res.paused;
    toast(wantPause ? `Queue paused (${res.queued} job(s) held).` : 'Queue resumed — Studio will pick work up.');
    refreshOps();
  } catch(err){ toast(err.message); }
}
async function sweepAgents(){
  try {
    const res = await opsJson('/api/bus', {method:'POST', body:JSON.stringify({
      action:'sweep', offlineSeconds:900, agentId: currentAgent()
    })});
    toast(res.tasksReaped
      ? `Released ${res.tasksReaped} task(s) from ${(res.agents || []).join(', ') || 'offline agents'}.`
      : 'No stale agents — nothing to release.');
    refreshBus();
  } catch(err){ toast(err.message); }
}
async function claimTask(id){
  try {
    await opsJson('/api/bus', {method:'POST', body:JSON.stringify({action:'task_claim', taskId:id, agentId:currentAgent()})});
    toast('Task claimed.');
    refreshBus();
  } catch(err){ toast(err.message); }
}
async function completeTask(id){
  try {
    await opsJson('/api/bus', {method:'POST', body:JSON.stringify({action:'task_complete', taskId:id, agentId:currentAgent(), result:'Completed from the Ops Console'})});
    toast('Task completed.');
    refreshBus();
  } catch(err){ toast(err.message); }
}
async function refreshMetrics(){
  try {
    const m = await opsJson('/api/metrics');
    const counters = (m.counters || []).map(c => `${c.name} = ${c.value}${Object.keys(c.labels||{}).length ? ' ' + JSON.stringify(c.labels) : ''}`);
    const hists = (m.histograms || []).map(h => `${h.name} p50=${h.p50} p95=${h.p95} n=${h.count}`);
    document.getElementById('ops-metrics').textContent = 'uptime ' + m.uptimeSeconds + 's\n' + counters.concat(hists).join('\n');
  } catch(err){ document.getElementById('ops-metrics').textContent = err.message; }
}
async function verifyAudit(){
  try {
    const v = await opsJson('/api/audit?verify=1');
    document.getElementById('ops-audit-note').textContent = v.ok
      ? `Chain intact: ${v.checked} entries, head ${String(v.head).slice(0,12)}…`
      : `CHAIN BROKEN at #${v.brokenAt}: ${v.reason}`;
    const a = await opsJson('/api/audit?limit=8');
    document.getElementById('ops-audit').textContent = (a.entries || []).map(e => `${e.seq} ${e.action} · ${e.actor} · ${e.outcome}`).join('\n');
  } catch(err){ document.getElementById('ops-audit-note').textContent = err.message; }
}
function evFeedLine(text){
  const feed = document.getElementById('ev-feed');
  feed.textContent += text + '\n';
  feed.scrollTop = feed.scrollHeight;
}
function connectEvents(){
  const btn = document.getElementById('ev-btn');
  clearTimeout(EV_RETRY);
  if(EV_SOURCE){ try { EV_SOURCE.close(); } catch(_){} EV_SOURCE = null; }
  if(!EV_WANT){ btn.textContent = 'Connect'; return; }
  // EventSource cannot send an Authorization header, so we redeem a one-shot
  // ticket instead of putting the token in the URL (v6.1.0, P0-S2).
  apiFetch('/api/sse-ticket').then(r => r.json()).then(boot => {
    openEventSource(boot && boot.ticket ? boot.ticket : '');
  }).catch(() => openEventSource(''));
}
function openEventSource(ticket){
  const btn = document.getElementById('ev-btn');
  if(EV_SOURCE){ try { EV_SOURCE.close(); } catch(_){} EV_SOURCE = null; }
  if(!EV_WANT){ btn.textContent = 'Connect'; return; }
  const src = new EventSource('/api/events' + (ticket ? '?ticket=' + encodeURIComponent(ticket) : ''));
  EV_SOURCE = src;
  btn.textContent = 'Disconnect';
  src.onopen = () => {
    EV_DELAY = 1000;
    btn.textContent = 'Disconnect';
    evFeedLine('[stream live]');
  };
  src.onmessage = ev => {
    try {
      const e = JSON.parse(ev.data);
      evFeedLine(`${new Date((e.ts||0)*1000).toLocaleTimeString()}  ${e.kind}  ${JSON.stringify(e.data)}`);
    } catch(_){}
  };
  src.onerror = () => {
    try { src.close(); } catch(_){}
    if(EV_SOURCE === src) EV_SOURCE = null;
    if(!EV_WANT){ btn.textContent = 'Connect'; return; }
    const wait = Math.max(1, Math.min(EV_DELAY, 30000));
    btn.textContent = `Reconnecting (${Math.round(wait/1000)}s)`;
    evFeedLine(`[stream error — retrying in ${Math.round(wait/1000)}s]`);
    EV_DELAY = Math.min(EV_DELAY * 2, 30000);
    EV_RETRY = setTimeout(connectEvents, wait);
  };
}
function toggleEventStream(){
  const btn = document.getElementById('ev-btn');
  if(EV_WANT){
    EV_WANT = false;
    clearTimeout(EV_RETRY);
    if(EV_SOURCE){ try { EV_SOURCE.close(); } catch(_){} EV_SOURCE = null; }
    btn.textContent = 'Connect';
    evFeedLine('[disconnected]');
    return;
  }
  EV_WANT = true;
  EV_DELAY = 1000;
  connectEvents();
}
async function copyMcpConfig(){
  try {
    const ops = await opsJson('/api/ops');
    const url = (ops.tunnel || {}).publicUrl;
    if(!url) throw new Error('Tunnel is not online yet — start it, then copy the config.');
    const token = getToken() || '<bridge-token>';
    const cfg = { mcpServers: { 'roblox-studio': {
      url: String(url).replace(/\/$/, '') + '/mcp',
      headers: { Authorization: 'Bearer ' + token }
    } } };
    await copyText(JSON.stringify(cfg, null, 2));
  } catch(err){ toast(err.message || 'Cannot build MCP config'); }
}

async function refreshDirectChat(){
  const box = document.getElementById('direct-chat-feed');
  if(!box) return;
  try {
    const r = await fetch('/api/chat?limit=40', { headers: authHeaders(), cache: 'no-store' });
    if(!r.ok) return;
    const d = await r.json();
    const msgs = d.messages || [];
    const badge = document.getElementById('live-chat-badge');
    if(badge){
      badge.textContent = d.unreadOperatorCount > 0
        ? `${d.unreadOperatorCount} Queued for Next Tool Call`
        : `Direct Live (${msgs.length})`;
    }
    if(!msgs.length){
      box.innerHTML = '<div class="sub">No direct chat messages yet. Send a message below to inject it directly into the agent&rsquo;s active tool loop.</div>';
      return;
    }
    box.innerHTML = msgs.map(m => {
      const isOp = m.role === 'operator';
      const status = isOp
        ? (m.repliedByAgent ? '✓ Replied by Agent' : (m.deliveredToAgent ? `✓ Delivered (${m.deliveredVia || 'tool'})` : '⏳ Queued for next tool call'))
        : '✓ Agent Reply';
      const pinBadge = m.pinned ? ' <span class="pill ro" style="font-size:10px;padding:1px 5px">PINNED</span>' : '';
      return `<div style="padding:6px 8px;margin-bottom:6px;border-radius:6px;background:${isOp ? 'rgba(56,189,248,0.10)' : 'rgba(52,211,153,0.10)'};border-left:3px solid ${isOp ? '#38bdf8' : '#34d399'}">
        <div style="display:flex;justify-content:space-between;font-size:11px">
          <strong>${esc(m.sender || (isOp ? 'Operator' : 'Agent'))}${pinBadge}</strong>
          <span class="sub">${esc(status)}</span>
        </div>
        <div style="margin-top:3px;color:#e2e8f0">${esc(m.text)}</div>
      </div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;
  } catch(_){}
}

async function sendOperatorLiveChat(){
  const inp = document.getElementById('direct-chat-input');
  const pin = document.getElementById('direct-chat-pin');
  const text = (inp && inp.value || '').trim();
  if(!text) return toast('Type a message for the agent first.');
  try {
    const r = await fetch('/api/chat', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({
        action: 'send',
        role: 'operator',
        sender: 'Operator',
        text,
        priority: 'high',
        pinned: Boolean(pin && pin.checked)
      })
    });
    const d = await r.json();
    if(!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    inp.value = '';
    if(pin) pin.checked = false;
    toast('Direct message queued — will inject into the agent’s next tool call!');
    refreshDirectChat();
  } catch(err){ toast(err.message); }
}

async function quickOperatorDirective(text){
  const inp = document.getElementById('direct-chat-input');
  const pin = document.getElementById('direct-chat-pin');
  if(inp) inp.value = text;
  if(pin) pin.checked = true;
  await sendOperatorLiveChat();
}

async function exportStudioUiToWeb(){
  const out = document.getElementById('web-ui-out');
  const htmlBox = document.getElementById('web-ui-html');
  if(out) out.textContent = 'Exporting StarterGui to Web HTML/Tailwind...';
  try {
    const r = await fetch('/api/ui/export-web', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ path: 'StarterGui', deSlop: true })
    });
    const d = await r.json();
    if(!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    if(htmlBox && d.html) htmlBox.value = d.html;
    if(out) out.textContent = JSON.stringify(d, null, 2);
    toast('Exported StarterGui into Web HTML/Tailwind!');
  } catch(err){
    if(out) out.textContent = err.message;
    toast(err.message);
  }
}

async function importWebUiFromDashboard(installInStudio){
  const preset = (document.getElementById('web-ui-preset') && document.getElementById('web-ui-preset').value) || 'rpg_hud';
  const guiName = (document.getElementById('web-ui-name') && document.getElementById('web-ui-name').value) || 'WebImportedGui';
  const html = (document.getElementById('web-ui-html') && document.getElementById('web-ui-html').value) || '';
  const out = document.getElementById('web-ui-out');
  if(out) out.textContent = installInStudio ? 'Converting & building in StarterGui...' : 'Converting Web UI to Roblox ScreenGui...';
  try {
    const r = await fetch('/api/ui/import-web', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ preset, guiName, html, deSlop: true, installInStudio: Boolean(installInStudio) })
    });
    const d = await r.json();
    if(!r.ok) throw new Error(d.error || ('HTTP ' + r.status));
    if(out) out.textContent = JSON.stringify(d, null, 2);
    toast(installInStudio ? 'Imported Web UI into StarterGui!' : 'Converted Web UI to Roblox ScreenGui tree');
  } catch(err){
    if(out) out.textContent = err.message;
    toast(err.message);
  }
}

bindDelegatedActions();
initBoot().then(() => { tick(); refreshOps(); refreshBus(); refreshFleet(); refreshDirectChat();
  setInterval(tick, 1500);
  setInterval(refreshFleet, 3000);
  setInterval(refreshDirectChat, 2500);
  setInterval(() => {
    const active = document.getElementById('tab-ops').classList.contains('active');
    if(active){ refreshOps(); refreshBus(); }
  }, 4000);
});
