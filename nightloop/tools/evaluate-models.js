#!/usr/bin/env node
/**
 * Fail-closed, preflight evaluator for candidate text models.
 *
 * A model command receives one JSON request on stdin and must write its answer
 * to stdout. The production prompt is deliberately not accepted by this tool.
 * The scoring / adapter / report engine is shared with the interactive
 * workbench via tools/lib/evaluate.js.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { die, shuffle, evaluateModel } = require('./lib/evaluate');

function readJson(file) { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { die(`${file}: ${e.message}`); } }
function argsOf(argv) {
  const out = { models: null, suite: null, output: 'evaluation-report.json', includeOutputs: false, seed: 17 };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--include-outputs') out.includeOutputs = true;
    else if (a === '--models') out.models = argv[++i];
    else if (a === '--suite') out.suite = argv[++i];
    else if (a === '--output') out.output = argv[++i];
    else if (a === '--seed') out.seed = Number(argv[++i]);
    else if (a === '--help') { console.log('Usage: node tools/evaluate-models.js --models models.json --suite suite.json [--output report.json] [--include-outputs] [--seed N]'); process.exit(0); }
    else die(`unknown argument ${a}`);
  }
  if (!out.models || !out.suite) die('--models and --suite are required');
  return out;
}

async function main() {
  const cli = argsOf(process.argv);
  const models = readJson(cli.models);
  const suite = readJson(cli.suite);
  if (!Array.isArray(models) || !models.length) die('models must be a non-empty array');
  if (!suite.tests || !Array.isArray(suite.tests) || !suite.tests.length) die('suite.tests must be a non-empty array');

  const cfg = {
    timeout_ms: 15000, pass_threshold: 0.8,
    hard_fail_categories: [], category_thresholds: {}, repeats: 1,
    ...suite,
  };
  const tests = shuffle(cfg.tests, cli.seed);
  const report = {
    schema: 'brine.preflight.v1',
    generated_at: new Date().toISOString(),
    suite: suite.name || 'unnamed',
    seed: cli.seed,
    models: [],
  };

  for (const model of models) {
    const result = await evaluateModel(model, tests, cfg, { includeOutputs: cli.includeOutputs });
    report.models.push(result);
    console.log(`${result.id}: ${result.admitted ? 'ADMIT' : 'REJECT'} ${(result.overall_score * 100).toFixed(1)}%`);
  }

  fs.mkdirSync(path.dirname(path.resolve(cli.output)), { recursive: true });
  fs.writeFileSync(cli.output, JSON.stringify(report, null, 2) + '\n');
  console.log(`Report: ${cli.output}`);
}

main().catch(e => die(e.stack || e.message));
