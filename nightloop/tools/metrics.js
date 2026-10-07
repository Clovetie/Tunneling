#!/usr/bin/env node
'use strict';

/**
 * metrics.js — Lightweight code-health metrics for BRINE's Luau, runnable with
 * zero external deps. Reports, per .luau file:
 *   - lines of code (LOC) and non-comment/string "code" count
 *   - exported module functions, and how many require() dependencies
 *   - heuristic cyclomatic complexity (branches + decision points)
 *   - comment ratio and density of dangerous/heavy constructs
 * Then aggregates a whole-project summary + top offenders.
 *
 * This is a *trend* tool, not a grade. Use it to spot files that grow too large
 * or too branchy, and to compare iterations over time.
 *
 * Usage:
 *   node tools/metrics.js [--src src] [--top 8] [--json out.json]
 */

const fs = require('fs');
const path = require('path');
const luau = require('./lib/luau');
const { defaultSourceRoot } = require('./lib/paths');

function die(msg) { console.error(`metrics: ${msg}`); process.exit(2); }

function parseArgs(argv) {
	const out = { src: defaultSourceRoot(), top: 8, json: null };
	for (let i = 2; i < argv.length; i++) {
		const a = argv[i];
		if (a === '--src') out.src = path.resolve(process.cwd(), argv[++i]);
		else if (a === '--top') out.top = Number(argv[++i]);
		else if (a === '--json') out.json = argv[++i];
		else if (a === '--help') { console.log('Usage: node tools/metrics.js [--src src] [--top 8] [--json out.json]'); process.exit(0); }
		else die(`unknown argument ${a}`);
	}
	return out;
}

// Branches that count toward cyclomatic complexity.
const DECISION = /(\bif\b|\belseif\b|\bwhile\b|(?<![=!.])for\b|\breturn\b|\bnot\b|\band\b|\bor\b|\.\.\s*|=|\b)./g;

function countOccurrences(text, re) {
	let n = 0;
	let m;
	const r = new RegExp(re.source, re.flags);
	while ((m = r.exec(text))) n++;
	return n;
}

function analyseFile(file) {
	const full = fs.readFileSync(file, 'utf8');
	const codeOnly = luau.stripCommentsAndStrings(full);
	const lines = full.split('\n');
	const codeLines = codeOnly.split('\n').filter((l) => l.trim().length > 0);
	const commentLines = lines.filter((l) => /^\s*--/.test(l)).length;
	const blankLines = lines.filter((l) => l.trim() === '').length;

	// Cyclomatic = 1 + count of decision keywords (if, elseif, while, for, and,
	// or, not-in-line, and each `true`/logical branch). We approximate using the
	// most meaningful Luau decision tokens on code-only text.
	const complexity = 1 +
		countOccurrences(codeOnly, /\bif\b/g) +
		countOccurrences(codeOnly, /\belseif\b/g) +
		countOccurrences(codeOnly, /\bwhile\b/g) +
		countOccurrences(codeOnly, /\bfor\b/g) +
		countOccurrences(codeOnly, /\band\b/g) +
		countOccurrences(codeOnly, /\bor\b/g) +
		countOccurrences(codeOnly, /\bnot\b/g);

	// Heavy / risky constructs we want to track (raycasts, tasks, tween, new
	// nodes, allocation-heavy calls).
	const heavy = {
		raycast: countOccurrences(codeOnly, /RaycastLocal|:Raycast\(/g),
		async: countOccurrences(codeOnly, /task\./g),
		tween: countOccurrences(codeOnly, /TweenService|tween|PivotTo|CFrameValue/g),
		createIds: countOccurrences(codeOnly, /:Create(Triangle|Vertex|Node|EditableMesh)|AddVertex|AddTriangle|AddPart|CreateMeshPart|new\s*\(/g),
		physics: countOccurrences(codeOnly, /AlignPosition|AlignOrientation|LinearVelocity|VectorForce|BodyGoal|Attachment|AssemblyMass/g),
	};

	const defs = luau.extractDefinitions(codeOnly);
	const exported = defs.filter((d) => d.namespace);
	const requires = luau.extractRequires(codeOnly);

	return {
		file,
		instance: luau.moduleInstanceName(file),
		loc: lines.length,
		codeLines: codeLines.length,
		commentLines,
		blankLines,
		commentRatio: lines.length ? commentLines / lines.length : 0,
		complexity,
		exports: exported.length,
		localFunctions: defs.filter((d) => !d.namespace).length,
		requires: requires.length,
		heavy,
		heavyTotal: Object.values(heavy).reduce((a, b) => a + b, 0),
	};
}

function build(cli) {
	const files = luau.collectLuauFiles(cli.src).map(analyseFile);
	const summary = {
		files: files.length,
		totalLoc: files.reduce((a, f) => a + f.loc, 0),
		totalCodeLines: files.reduce((a, f) => a + f.codeLines, 0),
		totalComments: files.reduce((a, f) => a + f.commentLines, 0),
		totalExports: files.reduce((a, f) => a + f.exports, 0),
		totalRequires: files.reduce((a, f) => a + f.requires, 0),
		avgComplexity: files.length ? files.reduce((a, f) => a + f.complexity, 0) / files.length : 0,
		commentRatio: files.reduce((a, f) => a + f.loc, 0) ? files.reduce((a, f) => a + f.commentLines, 0) / files.reduce((a, f) => a + f.loc, 0) : 0,
		topHeavy: files.slice().sort((a, b) => b.heavyTotal - a.heavyTotal).slice(0, cli.top),
		topComplex: files.slice().sort((a, b) => b.complexity - a.complexity).slice(0, cli.top),
	};
	return { generated_at: new Date().toISOString(), srcRoot: cli.src, files, summary };
}

function bar(n, width) {
	const w = Math.min(width, Math.max(1, Math.round(n)));
	return '#'.repeat(w);
}

function print(graph) {
	const f = graph.files;
	console.log(`\n=== BRINE metrics (${f.length} files) ===`);
	console.log(`LOC ${graph.summary.totalLoc} | code ${graph.summary.totalCodeLines} | comments ${graph.summary.totalComments} | exports ${graph.summary.totalExports} | requires ${graph.summary.totalRequires}`);
	console.log(`avg complexity ${graph.summary.avgComplexity.toFixed(1)} | comment ratio ${(graph.summary.commentRatio * 100).toFixed(1)}%\n`);

	console.log('--- Per file (complexity | heavy | loc) ---');
	for (const m of f.slice().sort((a, b) => b.complexity - a.complexity)) {
		console.log(`  ${String(m.complexity).padStart(3)} ${m.heavyTotal >= 0 ? '✦'.repeat(Math.min(3, m.heavyTotal)) : ''} ${String(m.loc).padStart(4)}  ${path.relative(path.resolve(__dirname, '..'), m.file)}`);
	}

	console.log(`\n--- Top ${graph.summary.topHeavy.length} by heavy-construct count ---`);
	for (const m of graph.summary.topHeavy) {
		console.log(`  ${m.heavyTotal}  ${path.relative(path.resolve(__dirname, '..'), m.file)}  (raycast=${m.heavy.raycast} task=${m.heavy.async} tween=${m.heavy.tween} create=${m.heavy.createIds} physics=${m.heavy.physics})`);
	}

	console.log(`\n--- Top ${graph.summary.topComplex.length} by complexity ---`);
	for (const m of graph.summary.topComplex) {
		console.log(`  ${m.complexity}  ${path.relative(path.resolve(__dirname, '..'), m.file)}`);
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
		print(graph);
	}
}
main();
