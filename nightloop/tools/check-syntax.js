#!/usr/bin/env node
// Lightweight Luau syntax check for the BRINE project.
//
// `luaparse` understands standard Lua but not Luau-only syntax, so we strip
// common Luau type syntax (`:: Type`, `type` aliases, etc.) before parsing.
// This catches typos, missing `end`, and other structural mistakes without
// requiring the full Luau toolchain.
//
// By default it checks the Explorer-first `code/` tree when present. In the
// older BRINE checkout it falls back to the historical `src/` roots. Pass
// explicit paths to check selected files instead:
//   node tools/check-syntax.js code/Path/To/Script.luau
//
// Run with:
//   npm --prefix tools install        (once)
//   node tools/check-syntax.js

const fs = require("fs");
const path = require("path");
const luaparse = require("luaparse");
const { defaultSourceRoot } = require("./lib/paths");

const root = defaultSourceRoot();
// Scope note: luaparse understands Lua 5.1, not every Luau-only construct.
// The checker strips common annotations, but bitwise operators and some newer
// syntax remain honest exceptions. The Vitriol quality gate allowlists only the
// two known bitwise files; any new failure must be fixed or explicitly reviewed.
// For whole-repo analysis use the quality gate plus the Luau-aware custom tools.
const LEGACY_ROOTS = [
	path.join(root, "ReplicatedStorage", "HandHolding"),
	path.join(root, "ReplicatedStorage", "IslandGeneration"),
	path.join(root, "ReplicatedStorage", "FloatingObjectGeneration"),
	path.join(root, "ReplicatedStorage", "Gerstner", "Helper.luau"),
	path.join(root, "ReplicatedStorage", "Gerstner", "Serial", "ZoneManager"),
	path.join(root, "ServerScriptService"),
	path.join(root, "StarterPlayerScripts"),
];
const DEFAULT_ROOTS = path.basename(root) === "code" ? [root] : LEGACY_ROOTS;

function collectLuauFiles(dir) {
	const out = [];
	for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
		const full = path.join(dir, entry.name);
		if (entry.isDirectory()) {
			out.push(...collectLuauFiles(full));
		} else if (entry.name.endsWith(".luau")) {
			out.push(full);
		}
	}
	return out;
}

function stripLuauTypeDeclarations(source) {
	const lines = source.split("\n");
	const out = [];
	let skipping = false;
	let braceDepth = 0;

	for (const line of lines) {
		const startsType = /^\s*(?:export\s+)?type\s+/.test(line);
		if (startsType || skipping) {
			braceDepth += (line.match(/\{/g) || []).length;
			braceDepth -= (line.match(/\}/g) || []).length;
			out.push("-- <luau type declaration removed>");
			skipping = braceDepth > 0;
			continue;
		}
		out.push(line);
	}

	return out.join("\n");
}

function stripLuauCasts(source) {
	let out = stripLuauTypeDeclarations(source)
		// Type-assertion casts: `expr :: Type` and `expr :: (Type)`.
		.replace(/::\s*[\w?]+(?:[\w?]|\.)*/g, "")
		.replace(/::\s*\([^)]*\)/g, "");

	// Luau `continue` statement: substitute a benign Lua 5.1 no-op statement.
	out = out.replace(/(^|[^\w])continue(?=\s|$)/g, "$1do end");

	// Turn Luau interpolated strings (`text {expr}`) and ordinary quoted
	// strings into placeholder quoted strings so luaparse (Lua 5.1) doesn't
	// choke on backticks, and so the loose type-annotation regexes below never
	// mistake a colon inside a string (for example a Roblox asset URI) for a
	// Luau annotation.
	out = out.replace(/`(?:[^`\\]|\\.)*`/g, '"<interpolated>"');
	out = out.replace(/(["'])(?:\\.|(?!\1)[^\\\r\n])*\1/g, '"<string>"');

	// Expand Luau compound assignment operators (+=, -=, *=, /=, //=, ^=, ..=)
	// into plain `x = x op expr` form. Supports an optional statement prefix
	// (e.g. `then x += 1`). Comment lines are left untouched.
	out = out
		.split("\n")
		.map((line) => {
			// Never transform comment lines (including divider lines like
			// `--===`); comments never appear mid-line in this codebase.
			const stripped = line.trimStart();
			if (stripped.startsWith("--")) {
				return line;
			}
			const m = line.match(
				/^(\s*(?:.*?\b(?:then|else|do)\s+)?)([A-Za-z_][\w.\[\]]*)\s*(?:\.\.|\/\/)?([+\-*/%^])=\s*(.*?)\s*;?\s*(end)?\s*$/
			);
			if (!m) {
				return line;
			}
			const prefix = m[1];
			const lvalue = m[2];
			const op = m[3];
			const tail = m[5] ? " end" : "";
			return `${prefix}${lvalue} = ${lvalue} ${op} (${m[4]})${tail}`;
		})
		.join("\n");

	// Remove Luau declaration type annotations line-by-line so a pattern can
	// never cross a line boundary into a statement, and so a method-call colon
	// (`obj:Foo(...)` - the line then continues with the call args) is left
	// intact: call colons always precede an identifier immediately followed by
	// `(`, whereas annotation colons precede a Type that ends the declaration.
	out = out
		.split("\n")
		.map((line) => {
			let s = line;
			// A type-annotation colon is never immediately followed (after
			// optional space) by `(`` - that is always a method-call colon,
			// which must be preserved. `(?!\\s*\\()` enforces that.
			// 1) Return type after a function declaration's closing `)`
			//    (including tuple returns like `): (number, boolean)`). Do not
			//    run this on method-call chains such as `Signal:Connect(...)`.
			if (/^\s*(?:local\s+)?function\b/.test(s)) {
				s = s.replace(/\)\s*:\s*.*$/, ")");
			}
			// 1b) Multi-line function head: a line that is only `): Type`
			//     (the closing paren of the parameter list plus the return
			//     annotation). A line starting with `)` is never anything
			//     else in valid code.
			if (/^\s*\)/.test(s)) {
				s = s.replace(/\)\s*:\s*.*$/, ")");
			}
			// 2) `: <type>` immediately followed by `,` `=` or `)` on the same
			//    line -> drop the type, keep the delimiter (params / bindings).
			s = s.replace(/:(?!\s*\()\s*[^,:;=(){}]*?(\{[^{}]*\})?\s*(?=\s*[,=)])/g, "");
			// 3) `: <type>` at end of line after a binding identifier (covers
			//    multi-line parameter lists and `local x: Type`).
			s = s.replace(/([A-Za-z_]\w*)\s*:(?!\s*\()\s*[^,;=()]*$/, "$1");
			return s;
		})
		.join("\n");
	return out;
}

function resolveTargets(argv) {
	if (argv.length > 0) {
		return argv.map((item) => path.resolve(process.cwd(), item));
	}
	return DEFAULT_ROOTS;
}

let failures = 0;
const targets = resolveTargets(process.argv.slice(2));
const files = [];

for (const target of targets) {
	if (fs.statSync(target).isDirectory()) {
		files.push(...collectLuauFiles(target));
	} else {
		files.push(target);
	}
}

if (files.length === 0) {
	console.error("No .luau files found in the requested locations.");
	process.exit(1);
}

for (const file of files) {
	const source = fs.readFileSync(file, "utf8");
	try {
		luaparse.parse(stripLuauCasts(source), { luaVersion: "5.1" });
		console.log("OK   ", path.relative(process.cwd(), file));
	} catch (err) {
		failures += 1;
		console.error("FAIL ", path.relative(process.cwd(), file));
		console.error("     ", err.message);
	}
}

if (failures > 0) {
	console.error(`\n${failures} file(s) failed the syntax check.`);
	process.exit(1);
}

console.log(`\nAll ${files.length} Luau file(s) parsed successfully.`);
