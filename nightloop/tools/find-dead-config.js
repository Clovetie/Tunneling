#!/usr/bin/env node
'use strict';

/**
 * find-dead-config.js — Detect configuration keys and settings flags that are
 * declared but never read anywhere in the repo. A "dead" knob is a real cost:
 * it invites confusion ("is this applied?") and drift over time.
 *
 * It scans a config module (or any file) for exported/returned keys and checks
 * whether each key name appears anywhere else in the source tree. It is
 * intentionally conservative: it reports a key as DEAD only if the exact
 * identifier never appears outside the config file itself. Identifiers that
 * share a prefix (e.g. `FrustumRenderDistance` on a settings value that is only
 * read via `_settings.KEY`) are still matched by exact token, so pay attention
 * to the "how to read the result" note.
 *
 * Known limitation: since most reads go through an alias (e.g. `_settings.KEY`),
 * the exact identifier appears in the read line, so it IS matched. If a config
 * value is read by a *derived* computation (e.g. `local X = _settings.A.B` then
 * later uses `X`), the original key name still appears in the config's own
 * struct literal so it won't be flagged — good. If a key is only ever used to
 * build a table that is itself consumed under a different name, it may be
 * under-reported; treat DEAD as "high confidence, verify before removing."
 *
 * Usage:
 *   node tools/find-dead-config.js --file code/ReplicatedStorage/Vitriol/Config/GameConfig.luau
 *   node tools/find-dead-config.js --src code    # audit every Vitriol config
 *   node tools/find-dead-config.js               # use the current checkout's defaults
 *   node tools/find-dead-config.js --json out.json
 */

const fs = require('fs');
const path = require('path');
const luau = require('./lib/luau');
const { defaultSourceRoot } = require('./lib/paths');

function die(msg) { console.error(`find-dead-config: ${msg}`); process.exit(2); }

function parseArgs(argv) {
	const repoRoot = path.resolve(__dirname, '..');
	const legacyDefaults = [
		path.join(repoRoot, 'src/ReplicatedStorage/Gerstner/Settings/init.luau'),
		path.join(repoRoot, 'src/ReplicatedStorage/IslandGeneration/IslandConfig.luau'),
	];
	const out = { files: [], json: null, src: defaultSourceRoot() };
	for (let i = 2; i < argv.length; i++) {
		const a = argv[i];
		if (a === '--file') out.files.push(path.resolve(process.cwd(), argv[++i]));
		else if (a === '--src') out.src = path.resolve(process.cwd(), argv[++i]);
		else if (a === '--json') out.json = argv[++i];
		else if (a === '--help') { console.log('Usage: node tools/find-dead-config.js [--file f] [--src src] [--json out.json]'); process.exit(0); }
		else die(`unknown argument ${a}`);
	}
	if (!out.files.length) {
		const vitriolConfigRoot = path.join(out.src, 'ReplicatedStorage', 'Vitriol', 'Config');
		if (fs.existsSync(vitriolConfigRoot) && fs.statSync(vitriolConfigRoot).isDirectory()) {
			out.files = fs.readdirSync(vitriolConfigRoot)
				.filter((name) => name.endsWith('.luau'))
				.sort()
				.map((name) => path.join(vitriolConfigRoot, name));
		} else {
			out.files = legacyDefaults;
		}
	}
	return out;
}

// Collect the KEYS of the config's returned table literal only, so local
// variables / function params are never mistaken for config knobs.
//
// We find the top-level `return {`, then walk it with brace matching. The keys
// we care about are `Identifier =` / `Identifier = {` entries that are direct
// children (depth 1) of the returned table — i.e. the config's exported fields.
// We also capture nested eager keys at depth 2 (e.g. `GRID_SETTINGS.Padding`)
// since those are real properties too, but we only report deadness at the level
// where a reader would reference them (`_settings.KEY` or `Config.KEY`).
function collectConfigKeys(source) {
	const masked = luau.stripCommentsAndStrings(source);
	// Support two common config shapes:
	//   (A) `return { Key = ..., Nested = { ... } }`  (brace-walk the literal)
	//   (B) `local Config = {}` then `Config.Key = ...` then `return Config`
	const keys = new Set();

	// --- Shape B: `local <Name> = {}` followed by `<Name>.Key = ...` ---
	// Capture the module table name (e.g. `IslandConfig`) from the `return <Name>`
	// at the end, or from a `local <Name> = {}` at the top.
	let configVar = null;
	const retVar = masked.match(/\breturn\s+([_A-Za-z][_A-Za-z0-9]*)\s*;?\s*$/);
	if (retVar) configVar = retVar[1];
	if (!configVar) {
		const localVar = masked.match(/\blocal\s+([_A-Za-z][_A-Za-z0-9]*)\s*=\s*\{\s*\}/);
		if (localVar) configVar = localVar[1];
	}
	if (configVar) {
		const re = new RegExp(`${configVar}\\.([_A-Za-z][_A-Za-z0-9]*)\\s*=`, 'g');
		let m;
		while ((m = re.exec(masked))) keys.add(m[1]);
	}

	// --- Shape A: `return { ... }` literal ---
	const returnIdx = masked.search(/\breturn\s*\{/);
	if (returnIdx !== -1) {
		let i = masked.indexOf('{', returnIdx);
		let depth = 0;
		let j = i;
		while (j < masked.length) {
			const c = masked[j];
			if (c === '{') { depth++; j++; continue; }
			if (c === '}') { depth--; j++; if (depth === 0) break; continue; }
			if (c === '"' || c === "'" || c === '`') {
				const q = c;
				j++;
				while (j < masked.length) {
					if (masked[j] === '\\') { j += 2; continue; }
					if (masked[j] === q) { j++; break; }
					j++;
				}
				continue;
			}
			if (c === '-' && masked[j + 1] === '-') { j += 2; continue; }
			if (/[A-Za-z_]/.test(c)) {
				let start = j;
				while (j < masked.length && /[A-Za-z0-9_]/.test(masked[j])) j++;
				const tok = masked.slice(start, j);
				let k = j;
				while (k < masked.length && /[ \t]/.test(masked[k])) k++;
				if ((masked[k] === '=' || masked[k] === ':') && depth <= 2) {
					keys.add(tok);
				}
				continue;
			}
			j++;
		}
	}

	return [...keys];
}

function collectAllTokens(files) {
	const tokens = new Set();
	for (const f of files) {
		const s = fs.readFileSync(f, 'utf8');
		const masked = luau.stripCommentsAndStrings(s);
		const re = /([_A-Za-z][_A-Za-z0-9]*)/g;
		let m;
		while ((m = re.exec(masked))) tokens.add(m[1]);
	}
	return tokens;
}

function build(cli) {
	// Gather all .luau files in the repo for the "is it read anywhere" check.
	const allFiles = luau.collectLuauFiles(cli.src).map((f) => path.resolve(f));
	const allTokens = collectAllTokens(allFiles);

	const reports = [];
	for (const cfg of cli.files) {
		if (!fs.existsSync(cfg)) { reports.push({ file: cfg, error: 'not found' }); continue; }
		const source = fs.readFileSync(cfg, 'utf8');
		const cfgName = luau.moduleInstanceName(cfg);
		const keys = collectConfigKeys(source);
		const dead = [];
		const alive = [];
		// Pre-mask every file once so both `count` and `inThisFile` measure the
		// same "code-only" token space (comments/strings are excluded). Counting
		// raw source for inThisFile would otherwise pick up e.g. `// DEBUG:` in a
		// doc comment and falsely mark a real read as dead.
		const maskedByFile = allFiles.map((f) => ({ f, masked: luau.stripCommentsAndStrings(fs.readFileSync(f, 'utf8')) }));
		const maskedThis = luau.stripCommentsAndStrings(source);
		for (const key of keys) {
			let count = 0;
			for (const { masked } of maskedByFile) {
				const re = new RegExp(`\\b${key}\\b`, 'g');
				let m;
				while ((m = re.exec(masked))) count++;
			}
			const inThisFile = (maskedThis.match(new RegExp(`\\b${key}\\b`, 'g')) || []).length;
			const elsewhere = count - inThisFile;
			if (elsewhere <= 0) dead.push({ key, occurrences: count, note: 'never referenced outside this file' });
			else alive.push({ key, outsideRefs: elsewhere });
		}
		reports.push({ file: cfg, module: cfgName, keyCount: keys.length, dead, alive });
	}
	return { generated_at: new Date().toISOString(), srcRoot: cli.src, reports };
}

function main() {
	const cli = parseArgs(process.argv);
	const graph = build(cli);
	if (cli.json) {
		fs.mkdirSync(path.dirname(path.resolve(cli.json)), { recursive: true });
		fs.writeFileSync(cli.json, JSON.stringify(graph, null, 2) + '\n');
		console.log(`Written ${cli.json}`);
	} else {
		for (const r of graph.reports) {
			if (r.error) { console.log(`\n! ${r.file}: ${r.error}`); continue; }
			console.log(`\n=== ${r.module} (${r.file}) — ${r.keyCount} config keys ===`);
			if (!r.dead.length) { console.log('  ✓ no dead keys'); continue; }
			console.log('  DEAD (verify before removing):');
			for (const d of r.dead) console.log(`    ✗ ${d.key}`);
			if (r.alive.length) {
				console.log(`  already-referenced (${r.alive.length}): ${r.alive.map((a) => a.key).join(', ')}`);
			}
		}
	}
}
main();
