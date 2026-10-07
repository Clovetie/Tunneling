#!/usr/bin/env node
'use strict';

/**
 * hot-loop-scan.js — Flag expensive operations that live on a per-frame or
 * per-iteration hot path. This is not a profiler; it is a static "smell" scan
 * that answers "which heavy calls happen inside RunService callbacks or inside
 * loops?" so you know where to look first when frame time spikes.
 *
 * It detects:
 *   1. Callbacks wired to RunService.* (Heartbeat / Stepped / PostSimulation /
 *      RenderStepped / PreSimulation) — the per-frame root.
 *   2. Heavy/priced calls (raycasts, `:GetAllNodes`/`GetAllNodes`, `:Clone`,
 *      `CreateMeshPart`, `:RaycastLocal`, `GetPartBoundsInRadius`, table sort,
 *      SharedTable writes, `GetServerTimeNow`, `TweenService:Create`,
 *      `workspace:Raycast`) that appear ANYWHERE, and whether they fall inside
 *      a `for`/`while` loop body.
 *   3. Loops over large/nondecreasing sets (e.g. `for _, node in NodeList`).
 *
 * It prints a ranked list of "hot" spots with file:line, the offending call,
 * and a cone (wrapped loop) indicator. JSON is available for CI.
 *
 * Usage:
 *   node tools/hot-loop-scan.js [--src src] [--top 20] [--json out.json]
 */

const fs = require('fs');
const path = require('path');
const luau = require('./lib/luau');
const { defaultSourceRoot } = require('./lib/paths');

function die(msg) { console.error(`hot-loop-scan: ${msg}`); process.exit(2); }

function parseArgs(argv) {
	const out = { src: defaultSourceRoot(), top: 20, json: null };
	for (let i = 2; i < argv.length; i++) {
		const a = argv[i];
		if (a === '--src') out.src = path.resolve(process.cwd(), argv[++i]);
		else if (a === '--top') out.top = Number(argv[++i]);
		else if (a === '--json') out.json = argv[++i];
		else if (a === '--help') { console.log('Usage: node tools/hot-loop-scan.js [--src src] [--top 20] [--json out.json]'); process.exit(0); }
		else die(`unknown argument ${a}`);
	}
	return out;
}

const PRICED_CALLS = [
	{ re: /GetServerTimeNow/gi, label: 'GetServerTimeNow (janky under load; hoist)' },
	{ re: /:Raycast\(/gi, label: 'workspace:Raycast (depth/shore)' },
	{ re: /RaycastLocal/gi, label: 'EditableMesh:RaycastLocal (builds KD tree)' },
	{ re: /GetAllNodes/gi, label: 'Octree:GetAllNodes (full scan)' },
	{ re: /SearchRadius/gi, label: 'Octree:SearchRadius' },
	{ re: /GetPartBoundsInRadius/gi, label: 'Workspace:GetPartBoundsInRadius (broadphase)' },
	{ re: /:Clone\(/gi, label: ':Clone (instance clone)' },
	{ re: /CreateMeshPartAsync|CreateMesh|CreateEditableMesh/gi, label: 'mesh creation' },
	{ re: /CreateNode/gi, label: 'Octree:CreateNode' },
	{ re: /SharedTable/gi, label: 'SharedTable (slower than plain table)' },
	{ re: /table\.sort/gi, label: 'table.sort' },
	{ re: /table\.move|table\.insert/gi, label: 'table.move/insert (gc pressure)' },
	{ re: /GetServerTimeNow|os\.clock/gi, label: 'time query' },
	{ re: /TweenService:Create/gi, label: 'TweenService:Create' },
	{ re: /AddVertex|AddTriangle|AddPart/gi, label: 'mesh add' },
	{ re: /task\.wait|task\.delay/gi, label: 'task.yield' },
	{ re: /:GetDescendants\(/gi, label: ':GetDescendants (traverse)' },
	{ re: /:FindFirstChild\(/gi, label: ':FindFirstChild (early warn on client)' },
];

// Track Luau block structure with a stack so "inside a loop" is accurate.
// A line is "in a loop" if it sits inside the body of a `for`/`while`/`repeat`
// block (lexically, before the matching `end`/`until`). `if`/`function`/`do`
// blocks are pushed too so `end` counting stays balanced, but only `for`/`while`
///`repeat` mark a loop. This is a lightweight lexer over the masked (code-only)
// source and relies on BRINE's consistent indentation to keep outputs sensible.
// It is NOT a full Luau parser; treat results as a strong pointer, not a proof.
function buildBlockStack(maskedLines) {
	const stack = []; // each entry: { type, startLine }
	const lineIsInLoop = [];
	let loopDepth = 0;
	for (let i = 0; i < maskedLines.length; i++) {
		const code = maskedLines[i];
		lineIsInLoop.push(loopDepth > 0);
		if (!code.trim()) continue;
		const last = code.trimEnd();
		// Count openers. Order matters: `elseif`/`else` are NOT new blocks.
		if (/\bfor\b/.test(code)) { stack.push({ type: 'loop', line: i }); loopDepth++; }
		else if (/\bwhile\b/.test(code)) { stack.push({ type: 'loop', line: i }); loopDepth++; }
		else if (/\brepeat\b/.test(code)) { stack.push({ type: 'loop', line: i }); loopDepth++; }
		else if (/\bfunction\b/.test(code)) { stack.push({ type: 'fn', line: i }); }
		else if (/\bif\b/.test(code)) { stack.push({ type: 'if', line: i }); }
		else if (/\bdo\b\s*$/.test(code) && !/\bfor\b/.test(code) && !/\bwhile\b/.test(code)) { stack.push({ type: 'do', line: i }); }
		// A single-line `if x then y() end` closes on the same line; skip.
		// Count closers: `end`, `until`. A line may contain multiple `end`s.
		let m;
		const closerRE = /\bend\b|\buntil\b/g;
		let closed = 0;
		while ((m = closerRE.exec(code))) {
			if (code.trim() === 'end' || code.trim() === 'until' || true) {
				// Pop for each closer, but do not pop below 0.
				const top = stack[stack.length - 1];
				if (top) {
					stack.pop();
					if (top.type === 'loop') loopDepth = Math.max(0, loopDepth - 1);
				}
			}
		}
	}
	return lineIsInLoop;
}

function scanFile(file) {
	const raw = fs.readFileSync(file, 'utf8');
	const lines = raw.split('\n');
	const maskedArr = luau.stripCommentsAndStrings(raw).split('\n');
	// Precompute the per-line "inside a loop" array.
	const lineIsInLoop = buildBlockStack(maskedArr);
	maskedArr._inLoop = lineIsInLoop;
	const issues = [];
	let selfWired = false;

	for (let i = 0; i < lines.length; i++) {
		const code = maskedArr[i];
		if (!code.trim()) continue;

		if (/RunService\s*[:.]\s*(Heartbeat|Stepped|PostSimulation|RenderStepped|PreSimulation)\s*:/g.test(code)) {
			selfWired = true;
		}

		for (const pc of PRICED_CALLS) {
			const m = code.match(pc.re);
			if (!m) continue;
			const inLoop = (lineIsInLoop[i] === true);
			issues.push({
				file: path.relative(process.cwd(), file).replace(/\\/g, '/'),
				line: i + 1,
				call: pc.label,
				insideLoop: inLoop,
				frameCallback: selfWired,
				snippet: code.trim().slice(0, 90),
			});
		}
	}
	// Sticky per-file: if ANY line wired a RunService callback, report it.
	const hasFrame = issues.some((x) => x.frameCallback);
	return { file, issues: issues.map((x) => ({ ...x, frameCallback: hasFrame })) };
}

function build(cli) {
	const files = luau.collectLuauFiles(cli.src).map(scanFile);
	const all = [];
	for (const f of files) for (const i of f.issues) all.push(i);
	const ranked = all.sort((a, b) => (b.insideLoop - a.insideLoop) || (b.frameCallback - a.frameCallback));
	const byCall = {};
	for (const i of all) (byCall[i.call] ||= []).push(i);
	return {
		generated_at: new Date().toISOString(),
		srcRoot: cli.src,
		total: all.length,
		hot: ranked.slice(0, cli.top),
		inLoop: all.filter((x) => x.insideLoop).length,
		inFrameFile: all.filter((x) => x.frameCallback).length,
		byCall,
	};
}

function main() {
	const cli = parseArgs(process.argv);
	const r = build(cli);
	if (cli.json) {
		fs.mkdirSync(path.dirname(path.resolve(cli.json)), { recursive: true });
		fs.writeFileSync(cli.json, JSON.stringify(r, null, 2) + '\n');
		console.log(`Written ${cli.json}`);
	} else {
		console.log(`\n=== BRINE hot-loop scan ===`);
		console.log(`Priced calls: ${r.total} | inside-a-loop: ${r.inLoop} | in a RunService file: ${r.inFrameFile}\n`);
		console.log('--- Top hotspots (loop & frame-file markers first) ---');
		for (const h of r.hot) {
			const flags = [
				h.insideLoop ? 'LOOP' : '',
				h.frameCallback ? 'FRAME' : '',
			].filter(Boolean).join('+') || ' ';
			console.log(`  [${flags}] ${h.file}:${h.line}  ${h.call}`);
			console.log(`         ${h.snippet}`);
		}
		console.log('\n--- By call type (occurrences) ---');
		for (const [k, v] of Object.entries(r.byCall).sort((a, b) => b[1].length - a[1].length)) {
			console.log(`  ${String(v.length).padStart(2)}  ${k}`);
		}
	}
}
main();
