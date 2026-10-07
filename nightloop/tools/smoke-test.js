#!/usr/bin/env node
// smoke-test.js
// Headless runtime smoke test for the FloatingObjectGeneration modules.
//
// Roblox is not available here, so this harness:
//   1. Transforms the Luau sources (and the Lua mocks/tests) into plain
//      Lua 5.3: compound assignments, type annotations/casts, generalized
//      iteration (`for x in t` -> pairs), and `continue` -> goto labels.
//   2. Runs them inside fengari (a Lua 5.3 VM written in JS) against a
//      minimal Roblox API mock (tools/smoke/roblox-mocks.lua) with a virtual
//      clock, task scheduler, and physics-free Instance tree.
//   3. Drives the generator through tools/smoke/tests.lua (streaming, LOD,
//      camera caching, render cap, PlaceOne, ClearGenerated, ...) and prints
//      PASS/FAIL plus any warns the Lua code produced.
//
// Run with:  npm --prefix tools install   (once)
//            node tools/smoke-test.js

const fs = require("fs");
const path = require("path");
const fengari = require("fengari");
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = fengari;

const ROOT = path.resolve(__dirname, "..");

// ---------------------------------------------------------------------------
// Luau -> Lua 5.3 transform (line based; repo style is stylua-formatted)
// ---------------------------------------------------------------------------
function transformLuau(src, label) {
	const lines = src.split("\n");
	const out = [];
	const stack = []; // { loop: bool, labels: string[] }
	let contId = 0;
	let inBlockComment = false;
	let inFnHead = false;

	for (const raw of lines) {
		let line = raw;
		let trimmed = line.trim();

		// Inside a --[[ ... ]] block comment: ignore until the closing ]].
		if (inBlockComment) {
			if (/^\]\]/.test(trimmed)) {
				inBlockComment = false;
			}
			out.push(line);
			continue;
		}
		if (/^--\[\[/.test(trimmed)) {
			inBlockComment = true;
			out.push(line);
			continue;
		}

		const isComment = trimmed.startsWith("--");

		if (!isComment) {
			// Compound assignment: `x += y` -> `x = x + (y)`
			const m = line.match(/^(\s*)([A-Za-z_][\w.[\]"']*)\s*(\+=|-=|\*=|\/=)(.+)$/);
			if (m) {
				line = `${m[1]}${m[2]} = ${m[2]} ${m[3][0]} (${m[4].trim()})`;
			}
			// Type assertion casts: `expr :: T` -> `expr`
			line = line.replace(/\s*::\s*[A-Za-z_][\w?]*/g, "");
			// `local x: T = v` / `local x: T`
			line = line.replace(/^(\s*local\s+[A-Za-z_]\w*)\s*:\s*[A-Za-z_][\w?]*\s*=\s*/, "$1 = ");
			line = line.replace(/^(\s*local\s+[A-Za-z_]\w*)\s*:\s*[A-Za-z_][\w?]*\s*$/, "$1");

			// Multi-line function declaration heads: params on their own lines.
			if (inFnHead) {
				// `name: Type,` -> `name,` ; `name: Type` -> `name` ; `): Ret` -> `)`
				line = line.replace(/^(\s*[A-Za-z_]\w*\s*):\s*[^,]+\s*,\s*$/, "$1,");
				line = line.replace(/^(\s*[A-Za-z_]\w*\s*):\s*[^,]+\s*$/, "$1");
				line = line.replace(/^(\s*\))\s*:\s*.*$/, "$1");
				if (/^\s*\)/.test(line)) {
					inFnHead = false;
				}
			} else if (/^\s*(local\s+)?function\b/.test(line)) {
				if (/\($/.test(line.trim())) {
					inFnHead = true; // head continues on following lines
				} else {
					// Single-line head: strip return type, then param annotations.
					line = line.replace(/\)\s*:\s*\([^()]*\)\s*$/, ")"); // tuple returns
					line = line.replace(/\)\s*:\s*[^(),]*$/, ")");
					line = line.replace(/:\s*[A-Za-z_{][^,()]*?(?=[,)])/g, "");
				}
			}
			// Generalized iteration: `for k in t do` -> `for k in pairs(t) do`
			if (/^\s*for\b/.test(line)) {
				const fm = line.match(/^(\s*for\s+[A-Za-z_][\w,\s]*?\s+)in\s+(.+?)\s+do$/);
				if (fm && !/^(pairs|ipairs|next)\s*\(/.test(fm[2].trim())) {
					line = `${fm[1]}in pairs(${fm[2]}) do`;
				} else if (!fm && !/^\s*for\b.*=/.test(line)) {
					throw new Error(`${label}: unhandled for-line: ${line.trim()}`);
				}
			}
		}

		// Re-trim after the transforms above may have rewritten the line.
		trimmed = line.trim();

		// `continue` -> shared per-loop goto label placed before the loop `end`.
		if (/^\s*continue\s*(--.*)?$/.test(line)) {
			let frame = null;
			for (let i = stack.length - 1; i >= 0; i--) {
				if (stack[i].loop) {
					frame = stack[i];
					break;
				}
			}
			if (!frame) {
				throw new Error(`${label}: continue outside a loop: ${line.trim()}`);
			}
			if (frame.labels.length === 0) {
				frame.labels.push(`__cont_${++contId}`);
			}
			out.push(line.replace(/\bcontinue\b/, "goto " + frame.labels[0]));
			continue;
		}

		// Block structure tracking so labels land before the right `end`.
		if (!isComment) {
			if (/^(for|while)\b/.test(trimmed) && /do$/.test(trimmed)) {
				stack.push({ loop: true, labels: [], at: `line ${out.length + 1}: ${line.trim()}` });
			} else if (trimmed === "repeat") {
				stack.push({ loop: true, labels: [], at: `line ${out.length + 1}: ${line.trim()}` });
			} else if (
				(/^(local\s+)?function\b/.test(trimmed) && (/\)$/.test(trimmed) || /\($/.test(trimmed)))
				|| (!/^(local\s+)?function\b/.test(trimmed) && /\bfunction\s*\([^)]*\)\s*$/.test(trimmed))
			) {
				stack.push({ loop: false, labels: [], at: `line ${out.length + 1}: ${line.trim()}` });
			} else if (/then$/.test(trimmed) && !/^elseif\b/.test(trimmed)) {
				stack.push({ loop: false, labels: [], at: `line ${out.length + 1}: ${line.trim()}` });
			} else if (/^end\b/.test(trimmed) || /^until\b/.test(trimmed)) {
				const frame = stack.pop();
				if (!frame) {
					const ctx = out.slice(-6).map((l) => "  | " + l.trim()).join("\n");
					throw new Error(`${label}: unbalanced end: ${line.trim()}\n${ctx}`);
				}
				if (frame.loop && frame.labels.length > 0) {
					const indent = line.match(/^\s*/)[0];
					for (const lab of frame.labels) {
						out.push(`${indent}::${lab}::`);
					}
				}
			}
		}

		out.push(line);
	}

	if (stack.length > 0) {
		throw new Error(`${label}: unclosed block(s):\n  ${stack.map((f) => f.at).join("\n  ")}`);
	}
	return out.join("\n");
}

function luaLongString(level, s) {
	const eq = "=".repeat(level);
	return `[${eq}[${s}]${eq}]`;
}

// ---------------------------------------------------------------------------
// Load + bundle
// ---------------------------------------------------------------------------
function read(p) {
	return fs.readFileSync(path.join(ROOT, p), "utf8");
}

const tMocks = transformLuau(read("tools/smoke/roblox-mocks.lua"), "mocks");
const tTests = transformLuau(read("tools/smoke/tests.lua"), "tests");
const tConfig = transformLuau(read("src/ReplicatedStorage/FloatingObjectGeneration/FloatingObjectConfig.luau"), "config");
const tWaveFloat = transformLuau(
	read("src/ReplicatedStorage/FloatingObjectGeneration/WaveFloat.luau"),
	"wavefloat"
);
const tGenerator = transformLuau(
	read("src/ReplicatedStorage/FloatingObjectGeneration/FloatingObjectGenerator.luau"),
	"generator"
);

if (process.argv.includes("--debug-transform")) {
	fs.writeFileSync("/tmp/gen-transformed.lua", tGenerator);
	fs.writeFileSync("/tmp/wavefloat-transformed.lua", tWaveFloat);
	fs.writeFileSync("/tmp/config-transformed.lua", tConfig);
	fs.writeFileSync("/tmp/mocks-transformed.lua", tMocks);
	fs.writeFileSync("/tmp/tests-transformed.lua", tTests);
	console.log("transformed sources written to /tmp/*-transformed.lua");
	process.exit(0);
}

const bundle = [
	tMocks,
	"SOURCES = {",
	`  FloatingObjectConfig = ${luaLongString(4, tConfig)},`,
	`  WaveFloat = ${luaLongString(4, tWaveFloat)},`,
	`  FloatingObjectGenerator = ${luaLongString(4, tGenerator)},`,
	"}",
	tTests,
].join("\n");

// ---------------------------------------------------------------------------
// Run inside fengari
// ---------------------------------------------------------------------------
const L = lauxlib.luaL_newstate();
lualib.luaL_openlibs(L);

const status = lauxlib.luaL_dostring(L, to_luastring(bundle));

function globalString(name) {
	lua.lua_getglobal(L, to_luastring(name));
	const v = lua.lua_tostring(L, -1);
	lua.lua_pop(L, 1);
	return v === null ? "" : to_jsstring(v);
}

function dumpLog() {
	const log = globalString("LOGSTRING");
	for (const entry of log.split("\n")) {
		if (!entry) {
			continue;
		}
		if (entry.startsWith("warn|")) {
			console.log("[warn] " + entry.slice(5));
		} else if (entry.startsWith("print|")) {
			console.log("[print] " + entry.slice(6));
		}
	}
	return log;
}

if (status !== lua.LUA_OK) {
	const err = to_jsstring(lua.lua_tostring(L, -1) || to_luastring("<no error object>"));
	dumpLog();
	console.error("HARNESS CRASHED:\n" + err);
	process.exit(1);
}

const log = dumpLog();
const result = globalString("RESULT");
const passedCount = result === "" ? 0 : parseInt(result.split("/")[0], 10);
const totalCount = result === "" ? 0 : parseInt(result.split("/")[1], 10);
const failedCount = Math.max(0, totalCount - passedCount);
const warns = log.split("\n").filter((l) => l.startsWith("warn|")).length;

console.log(`\nSmoke test: ${passedCount} passed, ${failedCount} failed (${warns} warn lines).`);
if (failedCount > 0 || result === "") {
	process.exit(1);
}
