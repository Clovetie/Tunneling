#!/usr/bin/env node
/**
 * workbench.js — interactive evaluation workbench for candidate text models.
 *
 * Sits on top of the shared engine (tools/lib/evaluate.js) used by the batch
 * gate (tools/evaluate-models.js). It lets you:
 *
 *   - list / add / remove candidate models;
 *   - list / add / remove suite tests;
 *   - run the suite against one or all models and inspect per-test pass/fail +
 *     reason (and raw output with --include-outputs);
 *   - write a full JSON evaluation report.
 *
 * It operates on *working* files (default tools/models.json and
 * tools/evaluation-suite.json). On first use it seeds those from the committed
 * `.example.json` templates so the tool is green out of the box; the example
 * files stay as frozen reference. `seed` re-copies them.
 *
 * Usage (subcommand mode):
 *   node tools/workbench.js [--models f.json] [--suite s.json]
 *                          [--seed N] [--include-outputs] <command> [args]
 *
 *   Commands: models | tests | add-model | remove-model | add-test | remove-test
 *             run [model-id] | report [file] | summary | seed | help
 *
 * With no command it drops into an interactive REPL.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const { die, shuffle, evaluateModel } = require('./lib/evaluate');

const TOOLS = __dirname;
const ROOT = path.resolve(TOOLS, '..');

const DEFAULT_MODELS = path.join(TOOLS, 'models.json');
const DEFAULT_SUITE = path.join(TOOLS, 'evaluation-suite.json');
const EXAMPLE_MODELS = path.join(TOOLS, 'models.example.json');
const EXAMPLE_SUITE = path.join(TOOLS, 'evaluation-suite.example.json');

// ---------------------------------------------------------------------------
// Arg parsing
// ---------------------------------------------------------------------------
function parseArgs(argv) {
  const opts = { models: null, suite: null, seed: 17, includeOutputs: false, output: null, help: false };
  const positional = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--models') opts.models = argv[++i];
    else if (a === '--suite') opts.suite = argv[++i];
    else if (a === '--seed') opts.seed = Number(argv[++i]);
    else if (a === '--include-outputs') opts.includeOutputs = true;
    else if (a === '--output') opts.output = argv[++i];
    else if (a === '--help' || a === '-h') opts.help = true;
    else positional.push(a);
  }
  return { opts, positional };
}

// ---------------------------------------------------------------------------
// State (working files)
// ---------------------------------------------------------------------------
function workingPaths(opts) {
  return {
    models: opts.models ? path.resolve(opts.models) : DEFAULT_MODELS,
    suite: opts.suite ? path.resolve(opts.suite) : DEFAULT_SUITE,
  };
}

function readJsonIfExists(file) {
  if (!fs.existsSync(file)) return null;
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); }
  catch (e) { die(`${file}: ${e.message}`); }
}

function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + '\n');
}

/** Seed the working file from its example template if the working file is absent. */
function ensureSeed(paths) {
  if (!fs.existsSync(paths.models) && fs.existsSync(EXAMPLE_MODELS)) {
    writeJson(paths.models, JSON.parse(fs.readFileSync(EXAMPLE_MODELS, 'utf8')));
    console.log(`seeded ${path.relative(ROOT, paths.models)} from examples`);
  }
  if (!fs.existsSync(paths.suite) && fs.existsSync(EXAMPLE_SUITE)) {
    writeJson(paths.suite, JSON.parse(fs.readFileSync(EXAMPLE_SUITE, 'utf8')));
    console.log(`seeded ${path.relative(ROOT, paths.suite)} from examples`);
  }
}

function loadState(opts) {
  const paths = workingPaths(opts);
  ensureSeed(paths);
  const models = readJsonIfExists(paths.models) || [];
  const suite = readJsonIfExists(paths.suite) || { name: 'brine-preflight-v1', tests: [] };
  if (!Array.isArray(models)) die(`${paths.models}: models must be an array`);
  if (!Array.isArray(suite.tests)) die(`${paths.suite}: suite.tests must be an array`);
  return { paths, models, suite };
}

function saveState(state) {
  writeJson(state.paths.models, state.models);
  writeJson(state.paths.suite, state.suite);
  console.log(`saved ${path.relative(ROOT, state.paths.models)} + ${path.relative(ROOT, state.paths.suite)}`);
}

function suiteConfig(suite) {
  return {
    timeout_ms: 15000, pass_threshold: 0.8,
    hard_fail_categories: [], category_thresholds: {}, repeats: 1,
    ...suite,
  };
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------
function showModels(state) {
  console.log(`\n${state.models.length} model(s) in ${path.relative(ROOT, state.paths.models)}:`);
  if (!state.models.length) { console.log('  (none)'); return; }
  for (const m of state.models) {
    const cmd = [m.command, ...(m.args || [])].join(' ');
    console.log(`  • ${m.id || m.name || '(unnamed)'}  →  ${cmd}${m.description ? `  (${m.description})` : ''}`);
  }
}

function showTests(state) {
  console.log(`\n${state.suite.tests.length} test(s) in ${path.relative(ROOT, state.paths.suite)} (suite: ${state.suite.name || 'unnamed'}):`);
  if (!state.suite.tests.length) { console.log('  (none)'); return; }
  const byCat = {};
  for (const t of state.suite.tests) (byCat[t.category] ||= []).push(t);
  for (const [cat, tests] of Object.entries(byCat)) {
    console.log(`  [${cat}]`);
    for (const t of tests) console.log(`    ${t.id}  (${t.type || 'includes'})`);
  }
}

function showSummary(state) {
  const cfg = suiteConfig(state.suite);
  console.log(`\nmodels:    ${state.paths.models}`);
  console.log(`suite:     ${state.paths.suite}  (${state.suite.tests.length} tests)`);
  console.log(`pass_threshold: ${cfg.pass_threshold}   repeats: ${cfg.repeats}   timeout_ms: ${cfg.timeout_ms}`);
  console.log(`hard_fail_categories: ${cfg.hard_fail_categories.join(', ') || '(none)'}`);
  console.log(`category_thresholds:  ${JSON.stringify(cfg.category_thresholds)}`);
}

// ---------------------------------------------------------------------------
// Editing helpers
// ---------------------------------------------------------------------------
function addModel(state, spec) {
  const usage = 'add-model: expected "id command arg1 arg2" or a JSON object';
  if (Array.isArray(spec)) {
    if (spec.length < 2) die(usage);
    spec = { id: spec[0], command: spec[1], args: spec.slice(2) };
  } else if (typeof spec === 'string') {
    try { spec = JSON.parse(spec); } catch (_) { die(usage); }
  } else if (!spec || typeof spec !== 'object') {
    die(usage);
  }
  if (!spec.id) die('add-model: id is required');
  if (!spec.command) die(`add-model(${spec.id}): command is required`);
  if (!Array.isArray(spec.args)) spec.args = spec.args ? [spec.args] : [];
  state.models.push(spec);
  saveState(state);
  console.log(`added model ${spec.id}`);
}

function addTest(state, spec) {
  if (typeof spec === 'string') {
    try { spec = JSON.parse(spec); } catch (_) { die('add-test: expected a JSON object (single argument)'); }
  }
  if (!spec || typeof spec !== 'object') die('add-test: expected a JSON object (single argument)');
  if (!spec.id) die('add-test: id is required');
  if (!spec.category) die(`add-test(${spec.id}): category is required`);
  if (!spec.prompt) die(`add-test(${spec.id}): prompt is required`);
  if (!spec.type) spec.type = 'includes';
  if (!('expected' in spec) && spec.type !== 'includes') die(`add-test(${spec.id}): expected is required for type ${spec.type}`);
  if (spec.type === 'includes' && !Array.isArray(spec.expected)) spec.expected = [spec.expected];
  state.suite.tests.push(spec);
  saveState(state);
  console.log(`added test ${spec.id} [${spec.category}]`);
}

function removeModel(state, id) {
  const before = state.models.length;
  state.models = state.models.filter(m => (m.id || m.name) !== id);
  if (state.models.length === before) die(`remove-model: no model with id ${id}`);
  saveState(state);
  console.log(`removed model ${id}`);
}

function removeTest(state, id) {
  const before = state.suite.tests.length;
  state.suite.tests = state.suite.tests.filter(t => t.id !== id);
  if (state.suite.tests.length === before) die(`remove-test: no test with id ${id}`);
  saveState(state);
  console.log(`removed test ${id}`);
}

// ---------------------------------------------------------------------------
// Run / report
// ---------------------------------------------------------------------------
async function runSuite(state, opts, filterId) {
  const cfg = suiteConfig(state.suite);
  if (!state.suite.tests.length) die('suite has no tests to run');
  let models = state.models;
  if (filterId) models = state.models.filter(m => m.id === filterId);
  if (!models.length) die(`no model matched${filterId ? `: ${filterId}` : ''}`);
  const tests = shuffle(cfg.tests, opts.seed);
  const results = [];
  for (const m of models) {
    // Rooted cwd so relative "tools/..." command paths resolve regardless of
    // where the workbench is launched from.
    const result = await evaluateModel({ ...m, cwd: m.cwd || ROOT }, tests, cfg, { includeOutputs: opts.includeOutputs });
    results.push(result);
    console.log(`\n${result.id}: ${result.admitted ? 'ADMIT' : 'REJECT'} ${(result.overall_score * 100).toFixed(1)}%`);
    console.log(`  categories: ${Object.entries(result.category_scores).map(([k, v]) => `${k}=${(v * 100).toFixed(0)}%`).join('  ')}`);
    for (const c of result.cases) {
      const mark = c.score === 1 ? '✓' : '✗';
      console.log(`    ${mark} ${c.id}  [${c.category}]  ${c.reason}`);
      if (opts.includeOutputs && c.output !== undefined) {
        const out = (c.output || '').trim();
        if (out) console.log(`        output: ${out.slice(0, 200).replace(/\n/g, '\\n')}`);
      }
    }
  }
  return results;
}

async function buildReport(state, opts, filterId, outputFile) {
  const cfg = suiteConfig(state.suite);
  const tests = shuffle(cfg.tests, opts.seed);
  let models = state.models;
  if (filterId) models = state.models.filter(m => m.id === filterId);
  if (!models.length) die('no model matched');
  const report = {
    schema: 'brine.preflight.v1',
    generated_at: new Date().toISOString(),
    suite: state.suite.name || 'unnamed',
    seed: opts.seed,
    models: [],
  };
  for (const m of models) {
    const result = await evaluateModel({ ...m, cwd: m.cwd || ROOT }, tests, cfg, { includeOutputs: opts.includeOutputs });
    report.models.push(result);
  }
  const out = outputFile || opts.output || path.join(ROOT, 'evaluation-report.json');
  writeJson(out, report);
  console.log(`wrote ${out} (${report.models.length} model(s))`);
  return report;
}

// ---------------------------------------------------------------------------
// CLI / REPL dispatch
// ---------------------------------------------------------------------------
function parsePositional(specArgs, label) {
  if (!specArgs.length) return null;
  const joined = specArgs.join(' ');
  if (joined.trim().startsWith('{')) { try { return JSON.parse(joined); } catch (_) { die(`${label}: invalid JSON argument`); } }
  return specArgs;
}

const HELP = `
BRINE model-eval workbench

Commands (subcommand mode; or run with no command for an interactive REPL):

  models                      list candidate models
  tests                       list suite tests
  summary                     show current config
  add-model <id> <cmd> [args...]    add a model (or pass a JSON object)
  remove-model <id>           remove a model
  add-test <JSON>             add a test (single JSON object argument)
  remove-test <id>            remove a test
  run [model-id]              run the suite (all models, or one)
  report [file]               write a full JSON report
  seed                        re-copy working files from the .example templates
  help                        show this message
  quit                        exit the REPL

Global flags (before or after the command):
  --models <file>   working models file          --suite <file>   working suite file
  --seed <n>        shuffle seed (default 17)    --include-outputs  show raw outputs
  --output <file>   report file destination (used by the report command)

Examples:
  node tools/workbench.js run fixture-known-good --include-outputs
  node tools/workbench.js add-model '{"id":"demo","command":"node","args":["tools/fixture-model.js"]}'
  node tools/workbench.js add-test '{"id":"r-9","category":"reasoning","type":"exact","prompt":"2+2?","expected":"4"}'
`;

function makeRepl(state, opts) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const queue = [];
  let inflight = 0;
  let streamClosed = false;
  let stopping = false;

  const maybeExit = () => {
    if (inflight === 0 && queue.length === 0) setImmediate(() => process.exit(0));
  };

  const pump = async () => {
    if (inflight > 0) return;               // a dispatch is running; it re-pumps
    if (stopping || (streamClosed && queue.length === 0)) { maybeExit(); return; }
    const line = queue.shift();
    if (line === undefined) {
      if (!streamClosed) rl.prompt();
      return;
    }
    const trimmed = line.trim();
    if (!trimmed) { pump(); return; }
    inflight++;
    try {
      const argc = splitWords(trimmed);
      const cmd = argc.shift().toLowerCase();
      if (cmd === 'quit' || cmd === 'exit') { stopping = true; return; }
      await dispatch(state, opts, cmd, argc);
    } catch (e) {
      console.error(`error: ${e.message}`);
    } finally {
      inflight--;
      pump();
    }
  };

  console.log(`\nBRINE model-eval workbench  (${path.relative(ROOT, state.paths.models)} / ${path.relative(ROOT, state.paths.suite)})`);
  console.log('type "help" for commands, "quit" to exit\n');
  rl.setPrompt('> ');
  rl.prompt();

  rl.on('line', line => { if (stopping) return; queue.push(line); pump(); });
  rl.on('close', () => { streamClosed = true; pump(); });

  return rl;
}

/** Split a line into words, respecting double quotes and simple escapes. */
function splitWords(line) {
  const words = []; let cur = ''; let q = false; let esc = false;
  for (const ch of line) {
    if (esc) { cur += ch; esc = false; continue; }
    if (ch === '\\') { esc = true; continue; }
    if (ch === '"') { q = !q; continue; }
    if (!q && /\s/.test(ch)) { if (cur) { words.push(cur); cur = ''; } continue; }
    cur += ch;
  }
  if (cur) words.push(cur);
  return words;
}

async function dispatch(state, opts, cmd, argc) {
  switch (cmd) {
    case 'help': console.log(HELP); break;
    case 'models': showModels(state); break;
    case 'tests': showTests(state); break;
    case 'summary': showSummary(state); break;
    case 'add-model': addModel(state, parsePositional(argc, 'add-model')); break;
    case 'remove-model': removeModel(state, argc[0]); break;
    case 'add-test': addTest(state, parsePositional(argc, 'add-test')); break;
    case 'remove-test': removeTest(state, argc[0]); break;
    case 'run': await runSuite(state, opts, argc[0]); break;
    case 'report': await buildReport(state, opts, null, argc[0] || opts.output); break;
    case 'seed': {
      if (fs.existsSync(EXAMPLE_MODELS)) writeJson(state.paths.models, JSON.parse(fs.readFileSync(EXAMPLE_MODELS, 'utf8')));
      if (fs.existsSync(EXAMPLE_SUITE)) writeJson(state.paths.suite, JSON.parse(fs.readFileSync(EXAMPLE_SUITE, 'utf8')));
      console.log(`re-seeded ${path.relative(ROOT, state.paths.models)} + ${path.relative(ROOT, state.paths.suite)} from examples`);
      break;
    }
    default: console.log(`unknown command "${cmd}". type "help".`);
  }
}

async function main() {
  const { opts, positional } = parseArgs(process.argv.slice(2));
  if (opts.help) { console.log(HELP); return; }
  const state = loadState(opts);
  const cmd = (positional[0] || '').toLowerCase();
  const argc = positional.slice(1);

  if (!cmd) {
    makeRepl(state, opts);
    return;
  }
  await dispatch(state, opts, cmd, argc);
}

main().catch(e => die(e.stack || e.message));
