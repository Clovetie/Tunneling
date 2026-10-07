#!/usr/bin/env node
'use strict';

/**
 * validate-project.js — Validate BRINE's Rojo project file against the on-disk
 * source tree, and check the Rojo file-layout rules this repo depends on.
 *
 * It catches the easy-to-break mistakes documented in README.md:
 *   1. A `$path` that points at a non-existent file/folder.
 *   2. The forbidden `Foo.luau` + `Foo/` collision (Rojo would emit two
 *      instances called `Foo` instead of attaching a sibling folder).
 *   3. A "module folder" (a folder whose instance is meant to be a ModuleScript,
 *      i.e. it has a `init.luau`) that is missing its `init.luau`.
 *   4. A folder-with-children that is actually a script but has no `init.luau`
 *      (so it stays a Folder, not a ModuleScript).
 *   5. `.model.json` className sanity where we know the contract (e.g.
 *      EditZone must be a RemoteEvent for ZoneManager's FireAllClients).
 *
 * It does NOT run Rojo (that needs rojo). This is a static sanity gate you can
 * run in CI or before committing.
 *
 * Usage:
 *   node tools/validate-project.js [--project default.project.json] [--src src]
 *   node tools/validate-project.js --json out.json
 */

const fs = require('fs');
const path = require('path');

function die(msg) { console.error(`validate-project: ${msg}`); process.exit(2); }

function parseArgs(argv) {
	const out = {
		project: path.resolve(__dirname, '..', 'default.project.json'),
		src: path.resolve(__dirname, '..', 'src'),
		json: null,
		noFail: false,
	};
	for (let i = 2; i < argv.length; i++) {
		const a = argv[i];
		if (a === '--project') out.project = path.resolve(process.cwd(), argv[++i]);
		else if (a === '--src') out.src = path.resolve(process.cwd(), argv[++i]);
		else if (a === '--json') out.json = argv[++i];
		else if (a === '--no-fail') out.noFail = true;
		else if (a === '--help') { console.log('Usage: node tools/validate-project.js [--project p] [--src s] [--json out.json] [--no-fail]'); process.exit(0); }
		else die(`unknown argument ${a}`);
	}
	return out;
}

// Known contract checks for the .model.json class names.
const KNOWN_CONTRACT = {
	'EditZone.model.json': {
		expect: ['RemoteEvent'],
		reason: 'ZoneManager calls FireAllClients + .OnClientEvent (RemoteEvent API, not BindableEvent)',
	},
	'ZoneUpdated.model.json': {
		expect: ['BindableEvent'],
		reason: 'ZoneManager uses :Fire() and .Event only (BindableEvent API)',
	},
	'Seed.model.json': {
		expect: ['IntValue'],
		reason: 'IslandGenerator reads Seed as an IntValue',
	},
};

function walkProject(node, prefix, out, errors, warnings) {
	const className = node.$className || '(default)';
	const isRoot = prefix === '';
	const keys = Object.keys(node).filter((k) => !k.startsWith('$'));

	// Any children that aren't directives are class/instance children.
	for (const key of keys) {
		const child = node[key];
		const childPath = prefix ? `${prefix}.${key}` : key;
		const pathKey = child.$path;
		if (pathKey) {
			const abs = path.resolve(path.dirname(out.project), pathKey);
			if (!fs.existsSync(abs)) {
				errors.push({ type: 'missing-path', path: childPath, value: pathKey });
			} else if (fs.statSync(abs).isDirectory()) {
				// Directory must be a Folder: fine unless it intends to be a module.
				const hasInit = fs.existsSync(path.join(abs, 'init.luau')) ||
					fs.existsSync(path.join(abs, 'init.server.luau')) ||
					fs.existsSync(path.join(abs, 'init.client.luau'));
				if (!hasInit) {
					// Could legitimately be an empty Folder, but if the instance is a
					// script-ish className this is a mistake. Warn.
					if (className && !['Folder', 'DataModel'].includes(className)) {
						warnings.push({ type: 'folder-not-module', path: childPath, className });
					}
				}
			} else {
				// File path.
				const base = path.basename(abs);
				const dir = path.dirname(abs);
				if (base === 'init.luau' || base === 'init.server.luau' || base === 'init.client.luau') {
					// This dir maps to a ModuleScript/Folder named after the dir.
					const dirName = path.basename(dir);
					// Check for forbidden boundary `Foo.luau` and `Foo/` sibling.
					const siblingModule = path.join(dir, dirName + '.luau');
					const siblingClient = path.join(dir, dirName + '.client.luau');
					const siblingServer = path.join(dir, dirName + '.server.luau');
					for (const s of [siblingModule, siblingClient, siblingServer]) {
						if (fs.existsSync(s)) {
							errors.push({ type: 'sibling-module-collision', path: childPath, detail: `Both ${path.basename(s)} and init.luau exist in ${path.relative(path.dirname(out.project), dir)}` });
						}
					}
				} else if (base.endsWith('.luau')) {
					const stem = base.replace(/\.(server|client)\.luau$/, '').replace(/\.luau$/, '');
					const dirName = path.basename(dir);
					// Forbidden `Foo.luau` + `Foo/` collision: if the sibling dir
					// `<name>/` exists as a directory with an init, that's the bug.
					if (fs.existsSync(path.join(dir, stem)) && fs.statSync(path.join(dir, stem)).isDirectory()) {
						errors.push({ type: 'dir-script-collision', path: childPath, detail: `${base} has a sibling ${stem}/ folder; Rojo will emit two instances called ${stem}` });
					}
				}
			}
		}
		// Recurse into folders with a $path (they are the filesystem nodes).
		if (child.$path) {
			const abs = path.resolve(path.dirname(out.project), child.$path);
			if (fs.existsSync(abs) && fs.statSync(abs).isDirectory()) {
				// Also check this directory for sibling-collision within it.
				walkProject(child, childPath, out, errors, warnings);
			}
		} else if (child && typeof child === 'object' && !child.$path && !child.$className) {
			walkProject(child, childPath, out, errors, warnings);
		}
	}
}

// Scan the src tree for the forbidden `Foo.luau` + `Foo/` sibling rule, since the
// project file may not enumerate every folder (e.g. serial folders).
function scanSrcForCollisions(src, errors, warnings) {
	function scan(dir) {
		let entries = fs.readdirSync(dir, { withFileTypes: true });
		const dirs = new Set(entries.filter((e) => e.isDirectory()).map((e) => e.name));
		for (const e of entries) {
			if (e.isDirectory()) {
				// Skip hidden + build dirs.
				if (e.name.startsWith('.') || e.name === 'node_modules') continue;
				scan(path.join(dir, e.name));
			} else if (e.name.endsWith('.luau')) {
				const stem = e.name.replace(/\.(server|client)\.luau$/, '').replace(/\.init\.luau$/, '').replace(/\.luau$/, '');
				// A non-init script with a sibling folder of the same name.
				if (stem !== path.basename(dir) && dirs.has(stem)) {
					errors.push({ type: 'src-dir-script-collision', path: path.relative(path.dirname(out.project), path.join(dir, e.name)), detail: `Sibling ${stem}/ folder conflicts with ${e.name}` });
				}
			}
		}
	}
	scan(src);
}

// Check the known .model.json contract names in the src tree.
function scanModelContracts(src, errors, projectPath) {
	const seen = new Map();
	function scan(dir) {
		let entries = fs.readdirSync(dir, { withFileTypes: true });
		for (const e of entries) {
			const full = path.join(dir, e.name);
			if (e.isDirectory()) {
				if (e.name.startsWith('.') || e.name === 'node_modules') continue;
				scan(full);
			} else if (e.name.endsWith('.model.json')) {
				const rel = path.relative(path.dirname(projectPath), full);
				seen.set(rel, full);
				const contract = KNOWN_CONTRACT[e.name];
				if (contract) {
					let className;
					try { className = JSON.parse(fs.readFileSync(full, 'utf8')).className; } catch (_) {}
					if (className && !contract.expect.includes(className)) {
						errors.push({ type: 'model-contract', path: rel, detail: `className ${className} but expected ${contract.expect.join('/')}: ${contract.reason}` });
					}
				}
			}
		}
	}
	scan(src);
	return seen;
}

function main() {
	const cli = parseArgs(process.argv);
	const errors = [];
	const warnings = [];
	const out = { project: cli.project };

	if (!fs.existsSync(cli.project)) die(`project file not found: ${cli.project}`);
	let tree;
	try { tree = JSON.parse(fs.readFileSync(cli.project, 'utf8')); out.tree = tree; }
	catch (e) { die(`invalid project JSON: ${e.message}`); }

	walkProject(tree, '', out, errors, warnings);
	scanSrcForCollisions(cli.src, errors, warnings);
	const seenModels = scanModelContracts(cli.src, errors, cli.project);

	const result = {
		generated_at: new Date().toISOString(),
		project: cli.project,
		srcRoot: cli.src,
		errorCount: errors.length,
		warningCount: warnings.length,
		errors,
		warnings,
		modelFiles: [...seenModels.keys()],
		ok: errors.length === 0,
	};

	if (cli.json) {
		fs.mkdirSync(path.dirname(path.resolve(cli.json)), { recursive: true });
		fs.writeFileSync(cli.json, JSON.stringify(result, null, 2) + '\n');
		console.log(`Written ${cli.json}`);
	}
	// Print to stdout regardless, then exit non-zero if there are hard errors so
	// CI can treat this as a real gate. Pass --no-fail to never hard-fail.
	if (!cli.noFail && errors.length > 0) process.exitCode = 1;
	if (!cli.json) {
		console.log(`\n=== Rojo project validation ===`);
		console.log(`Project: ${path.relative(path.dirname(cli.src), cli.project) || cli.project}`);
		console.log(`Model files: ${seenModels.size}`);
		console.log(`Errors: ${errors.length} | Warnings: ${warnings.length}\n`);
		if (errors.length) {
			console.log('--- ERRORS ---');
			for (const e of errors) console.log(`  ✗ ${e.type}: ${e.path} ${e.detail || ''}`);
		}
		if (warnings.length) {
			console.log('--- WARNINGS ---');
			for (const w of warnings) console.log(`  ! ${w.type}: ${w.path} ${w.detail || ''}`);
		}
		if (!errors.length && !warnings.length) console.log('  ✓ Project tree is internally consistent.');
		else if (!errors.length) console.log('  ✓ No hard errors (see warnings above).');
	}
}
main();
