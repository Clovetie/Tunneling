#!/usr/bin/env node
'use strict';

/**
 * call-graph.js — Build a module dependency + export-reference graph for the
 * BRINE Luau codebase and report:
 *   1. The require() dependency edges (module A depends on module B).
 *   2. Every exported function/method and which module(s) reference it.
 *   3. "Orphaned" exports: defined but never referenced from outside their own
 *      module. These are the audit's dead-code/library-only surface.
 *
 * Heuristic and honest: references are detected lexically by matching
 * `Namespace.name` / `Namespace:name` tokens where `Namespace` is a known
 * module table name or a `local alias = require(...)` binding. It is NOT a
 * full type checker, so treat the output as a high-signal pointer, not a proof.
 *
 * Usage:
 *   node tools/call-graph.js                     # print to stdout (table)
 *   node tools/call-graph.js --json out.json     # write machine-readable JSON
 *   node tools/call-graph.js --src src           # custom source root
 *   node tools/call-graph.js --no-internal-refs  # don't count same-file refs
 */

const fs = require('fs');
const path = require('path');
const luau = require('./lib/luau');
const { defaultSourceRoot } = require('./lib/paths');

function die(msg) { console.error(`call-graph: ${msg}`); process.exit(2); }

function parseArgs(argv) {
	const out = { src: defaultSourceRoot(), json: null, noInternal: false };
	for (let i = 2; i < argv.length; i++) {
		const a = argv[i];
		if (a === '--src') out.src = path.resolve(process.cwd(), argv[++i]);
		else if (a === '--json') out.json = argv[++i];
		else if (a === '--no-internal-refs') out.noInternal = true;
		else if (a === '--help') { console.log('Usage: node tools/call-graph.js [--src src] [--json out.json] [--no-internal-refs]'); process.exit(0); }
		else die(`unknown argument ${a}`);
	}
	return out;
}

function build(cli) {
	const files = luau.collectLuauFiles(cli.src).map((f) => luau.analyseFile(f));

	// Map instanceName -> module info (fall back to file basename for uniqueness).
	const byName = {};
	for (const mod of files) {
		(byName[mod.instanceName] ||= []).push(mod);
	}

	// Map each module to a set of "namespace" tokens we accept when detecting
	// references: its own instanceName + the aliases it binds to other modules.
	const namespaceByModule = {};
	for (const mod of files) {
		const set = new Set([mod.instanceName]);
		for (const r of mod.requires) set.add(r.alias);
		namespaceByModule[mod.file] = set;
	}

	// Resolve an alias to a target module file.
	function resolveAlias(mod, alias) {
		// Find the require binding for this alias.
		const req = mod.requires.find((r) => r.alias === alias);
		if (!req) return null;
		// Match target segment to a known instanceName, preferring the shortest
		// unambiguous module. If ambiguous, pick the first (heuristic).
		const matches = byName[req.target];
		if (matches && matches.length === 1) return matches[0];
		if (matches && matches.length > 1) return matches[0];
		// `script.Parent` style: the alias refers to the *parent instance* of the
		// file. Rojo maps an `init.luau` file to the Folder/ModuleScript named after
		// its parent directory, so:
		//   - a non-init file (`Helper.luau`) has parent = its folder (dirname);
		//   - an init file (`Settings/init.luau`) has parent = the folder one level
		//     up from its dirname (i.e. `Gerstner`).
		if (req.target === 'Parent') {
			const base = path.basename(mod.file);
			let parentDir;
			if (base === 'init.luau' || base === 'init.server.luau' || base === 'init.client.luau') {
				parentDir = path.basename(path.dirname(path.dirname(mod.file)));
			} else {
				parentDir = path.basename(path.dirname(mod.file));
			}
			const parentMatches = byName[parentDir];
			if (parentMatches && parentMatches.length === 1) return parentMatches[0];
			// Ambiguous or parent is a Folder (not a module): fall back to another
			// module whose instanceName matches parentDir.
			const anyMatch = files.find((f) => f.instanceName === parentDir || path.basename(path.dirname(f.file)) === parentDir);
			if (anyMatch) return anyMatch;
		}
		return null;
	}

	// Build dependency edges {from, to, viaAlias, line}.
	const dependencies = [];
	for (const mod of files) {
		for (const r of mod.requires) {
			const target = resolveAlias(mod, r.alias);
			if (target && target.file !== mod.file) {
				dependencies.push({
					from: mod.instanceName,
					to: target.instanceName,
					fromFile: mod.file,
					toFile: target.file,
					via: r.alias,
					line: r.line,
				});
			}
		}
	}

	// Build the set of exports per module plus their reference count.
	const exportsList = [];
	for (const mod of files) {
		for (const d of mod.definitions) {
			if (!d.namespace) continue; // plain local function, not a module export
			exportsList.push({
				module: mod.instanceName,
				file: mod.file,
				namespace: d.namespace,
				name: d.name,
				kind: d.kind,
				line: d.line,
			});
		}
	}

	// Decide which namespaces a reference may legitimately belong to.
	// For each module, the accepted namespace tokens are union of all module
	// instanceNames and all aliases across the whole repo (so `GerstnerModule`,
	// `OctreeModule`, `Setup`, `ZoneManager`, etc. are all recognized).
	const allNamespaces = new Set();
	for (const mod of files) {
		allNamespaces.add(mod.instanceName);
		for (const r of mod.requires) allNamespaces.add(r.alias);
	}

	// For each file, count references to each (namespace, name) where namespace
	// is a known module token. We do NOT include the definition line itself: the
	// definition of `Helper.GetFrustumPlanes` would otherwise be counted as a
	// reference to `Helper.GetFrustumPlanes`. We exclude any occurrence whose
	// line matches a definition of the same (namespace, name) in that file.
	// We also track which FILE originated each reference so self-references can
	// be separated from external ones.
	// Blank the definition lines in each file's masked source before scanning for
	// references, so `function Helper.GetFrustumPlanes(` is NOT later picked up as
	// a *reference* to Helper.GetFrustumPlanes. We scan a per-file masked copy
	// whose definition lines are replaced by whitespace (preserving newlines so
	// line numbers stay valid for any downstream use).
	const blankedByFile = {};
	for (const mod of files) {
		const masked = luau.stripCommentsAndStrings(fs.readFileSync(mod.file, 'utf8'));
		const lines = masked.split('\n');
		for (const d of mod.definitions) {
			if (d.namespace && lines[d.line - 1]) {
				lines[d.line - 1] = lines[d.line - 1].replace(/[^\n]/g, ' ');
			}
		}
		blankedByFile[mod.file] = lines.join('\n');
	}
	// Build an alias -> target-instanceName map so that references written through
	// an alias (e.g. `GerstnerModule.ComputeTransform`, `GerstnerWave.new`,
	// `OctreeModule.new`, `Config.Placement`, `Helper.GetZoneMultipliers`) are
	// normalised to the *module instance name* (e.g. `Gerstner::ComputeTransform`).
	// Without this, the same export could be counted under two different keys and
	// real cross-module uses would look "orphaned".
	const aliasToInstance = {};
	for (const mod of files) {
		aliasToInstance[mod.instanceName] = mod.instanceName;
		for (const r of mod.requires) {
			const target = resolveAlias(mod, r.alias);
			if (target) aliasToInstance[r.alias] = target.instanceName;
		}
	}

	const refCount = {}; // key `Module::export` -> { count, byFile: { file: count } }
	// resolveNamespace maps a namespace token (module instanceName or an alias)
	// to a module instanceName; used both for alias normalisation and for the
	// `local inst = Mod.new(); inst:Method()` instance-method pattern.
	const resolveNamespace = (ns) => aliasToInstance[ns] || (byName[ns] && byName[ns][0] && byName[ns][0].instanceName) || ns;
	const addRef = (key, fromModule) => {
		(refCount[key] ||= { count: 0, byFile: {} });
		refCount[key].count += 1;
		refCount[key].byFile[fromModule] = (refCount[key].byFile[fromModule] || 0) + 1;
	};

	for (const mod of files) {
		const masked = blankedByFile[mod.file];

		// (a) Direct namespace references: Helper.GetFrustumPlanes, Module.X, Alias.Y.
		const refs = luau.extractReferences(masked, [...allNamespaces]);
		for (const ref of refs) {
			const ns = ref.namespace;
			if (!allNamespaces.has(ns)) continue;
			const resolved = resolveNamespace(ns);
			if (!resolved) continue;
			addRef(`${resolved}::${ref.name}`, mod.instanceName);
		}

		// (b) Instance-method references: `local X = Mod.new(); X:Method()`.
		//     Builds map of `X -> Mod` for THIS file and adds `Mod::Method`.
		const instMap = luau.buildInstanceVarMap(masked, resolveNamespace);
		const instRefs = luau.extractInstanceRefs(masked, instMap);
		for (const ref of instRefs) {
			addRef(`${resolveNamespace(ref.namespace)}::${ref.name}`, mod.instanceName);
		}
	}

	// Attach reference info to each export. An export is "orphaned" if it is not
	// referenced by ANY OTHER module. Self-references (e.g. the definition line
	// itself, or Helper calling its own Helper.Lerp) are excluded so the report
	// reflects the real "only another module can call this" contract.
	const richExports = exportsList.map((e) => {
		const key = `${e.module}::${e.name}`;
		const data = refCount[key] || { count: 0, byFile: {} };
		// Only references from files OTHER than the export's own module count as
		// "external". The definition line was already blanked, so `count` is the
		// true count of real call sites (including intra-module ones).
		const externalFiles = Object.keys(data.byFile).filter((f) => f !== e.module);
		const externalRefs = externalFiles.reduce((a, f) => a + data.byFile[f], 0);
		const internalOnly = data.count > 0 && externalFiles.length === 0;
		const used = externalFiles.length > 0;
		return {
			...e,
			references: data.count,
			externalReferences: externalRefs,
			referencingModules: Object.keys(data.byFile),
			externalReferencingModules: externalFiles,
			internalOnly,
			orphaned: !used,
		};
	});

	const orphaned = richExports.filter((e) => e.orphaned);
	const used = richExports.filter((e) => !e.orphaned);

	return {
		generated_at: new Date().toISOString(),
		srcRoot: cli.src,
		moduleCount: files.length,
		moduleNames: files.map((f) => f.instanceName),
		dependencies,
		exports: richExports,
		orphaned,
		summary: {
			modules: files.length,
			exports: richExports.length,
			usedExports: used.length,
			orphanedExports: orphaned.length,
			dependencyEdges: dependencies.length,
		},
	};
}

function printTable(graph) {
	console.log(`\n=== BRINE call graph ===`);
	console.log(`Modules: ${graph.summary.modules}`);
	console.log(`Exports: ${graph.summary.exports} (used ${graph.summary.usedExports}, orphaned ${graph.summary.orphanedExports})`);
	console.log(`Require edges: ${graph.summary.dependencyEdges}\n`);

	console.log('--- Dependencies (module A -> module B) ---');
	for (const d of graph.dependencies) {
		console.log(`  ${d.from} -> ${d.to}  (via '${d.via}', ${path.relative(path.resolve(__dirname,'..'), d.fromFile)}:${d.line})`);
	}
	if (!graph.dependencies.length) console.log('  (none)');

	console.log('\n--- Orphaned exports (defined but never referenced) ---');
	if (!graph.orphaned.length) console.log('  (none)');
	graph.orphaned.forEach((e, i) => {
		console.log(`  ${String(i + 1).padStart(2)}. ${e.module}.${e.name}  (${e.kind}, ${path.relative(path.resolve(__dirname,'..'), e.file)}:${e.line})`);
	});

	console.log('\n--- Used exports (referenced outside their module) ---');
	if (!graph.exports.filter((e) => !e.orphaned).length) console.log('  (none)');
	for (const e of graph.exports.filter((x) => !x.orphaned)) {
		console.log(`  ${e.module}.${e.name}  <- ${e.externalReferencingModules.join(', ') || '(self)'}`);
	}
}

function main() {
	const cli = parseArgs(process.argv);
	const graph = build(cli);
	if (cli.json) {
		fs.mkdirSync(path.dirname(path.resolve(cli.json)), { recursive: true });
		fs.writeFileSync(cli.json, JSON.stringify(graph, null, 2) + '\n');
		console.log(`Written ${cli.json}`);
	} else {
		printTable(graph);
	}
}
main();
