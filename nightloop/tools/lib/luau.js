'use strict';

const fs = require('fs');
const path = require('path');

/**
 * Recursively collect all `.luau` and `.lua` source files under `dir`.
 * Accepts a single file path as well.
 */
function collectLuauFiles(dir) {
	const resolved = path.resolve(dir);
	if (!fs.existsSync(resolved)) return [];
	const stat = fs.statSync(resolved);
	if (stat.isFile()) {
		return /\.(luau|lua)$/.test(resolved) ? [resolved] : [];
	}
	const out = [];
	const entries = fs.readdirSync(resolved, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name));
	for (const entry of entries) {
		if (entry.name.startsWith('.') || entry.name === 'node_modules') continue;
		const full = path.join(resolved, entry.name);
		if (entry.isDirectory()) {
			out.push(...collectLuauFiles(full));
		} else if (entry.isFile() && /\.(luau|lua)$/.test(entry.name)) {
			out.push(full);
		}
	}
	return out;
}

/**
 * Derive the Roblox Instance name corresponding to a Rojo-style script path.
 */
function moduleInstanceName(file) {
	const base = path.basename(file);
	if (/^init(?:\.(?:server|client))?\.(?:luau|lua)$/.test(base)) {
		return path.basename(path.dirname(file));
	}
	return base
		.replace(/\.(?:server|client)\.(?:luau|lua)$/, '')
		.replace(/\.(?:luau|lua)$/, '');
}

/**
 * Replace Luau comments and string literals with spaces while preserving `\n`
 * so line numbers remain identical to the original source.
 */
function stripCommentsAndStrings(source) {
	const chars = Array.from(source);
	const out = new Array(chars.length);
	let i = 0;

	const matchLongBracket = (idx) => {
		if (chars[idx] !== '[') return -1;
		let eq = 0;
		let p = idx + 1;
		while (p < chars.length && chars[p] === '=') {
			eq++;
			p++;
		}
		return chars[p] === '[' ? eq : -1;
	};

	const skipLongBracket = (startIdx, eq) => {
		let p = startIdx;
		while (p < chars.length) {
			if (chars[p] === ']') {
				let k = p + 1;
				let count = 0;
				while (k < chars.length && chars[k] === '=' && count < eq) {
					count++;
					k++;
				}
				if (count === eq && chars[k] === ']') {
					const end = k + 1;
					for (let m = i; m < end; m++) {
						out[m] = chars[m] === '\n' ? '\n' : ' ';
					}
					return end;
				}
			}
			p++;
		}
		for (let m = i; m < chars.length; m++) {
			out[m] = chars[m] === '\n' ? '\n' : ' ';
		}
		return chars.length;
	};

	while (i < chars.length) {
		const c = chars[i];
		const next = chars[i + 1];

		// Comments: `--[[...]]`, `--[=[...]=]`, or `-- ...`
		if (c === '-' && next === '-') {
			const eq = matchLongBracket(i + 2);
			if (eq >= 0) {
				i = skipLongBracket(i + 4 + eq, eq);
				continue;
			}
			while (i < chars.length && chars[i] !== '\n') {
				out[i] = ' ';
				i++;
			}
			continue;
		}

		// Long strings: `[[...]]` or `[=[...]=]`
		if (c === '[') {
			const eq = matchLongBracket(i);
			if (eq >= 0) {
				i = skipLongBracket(i + 2 + eq, eq);
				continue;
			}
		}

		// Quoted / interpolated strings: `"..."`, `'...'`, `` `...` ``
		if (c === '"' || c === "'" || c === '`') {
			const quote = c;
			out[i++] = ' ';
			while (i < chars.length) {
				if (chars[i] === '\\') {
					out[i] = chars[i] === '\n' ? '\n' : ' ';
					if (i + 1 < chars.length) {
						out[i + 1] = chars[i + 1] === '\n' ? '\n' : ' ';
					}
					i += 2;
					continue;
				}
				if (chars[i] === quote) {
					out[i++] = ' ';
					break;
				}
				if (chars[i] === '\n' && quote !== '`') {
					out[i++] = '\n';
					break;
				}
				out[i] = chars[i] === '\n' ? '\n' : ' ';
				i++;
			}
			continue;
		}

		out[i] = c;
		i++;
	}

	return out.join('');
}

/**
 * Extract `local Alias = require(...)` bindings from original or masked source.
 */
function extractRequires(source) {
	const lines = source.split('\n');
	const out = [];
	for (let idx = 0; idx < lines.length; idx++) {
		const line = lines[idx];
		if (/^\s*--/.test(line)) continue;
		const m = line.match(/\blocal\s+([A-Za-z_]\w*)\s*(?::[^=]+)?=\s*require\s*\(([^)]+)\)/);
		if (!m) continue;
		const alias = m[1];
		const expr = m[2].trim();
		let target = null;
		const waitMatch = expr.match(/(?:WaitForChild|FindFirstChild)\s*\(\s*["']([^"']+)["']\s*\)\s*$/);
		if (waitMatch) {
			target = waitMatch[1];
		} else {
			const parts = expr.split('.').map((s) => s.trim()).filter(Boolean);
			const last = parts[parts.length - 1] || '';
			const ident = last.match(/([A-Za-z_]\w*)$/);
			target = ident ? ident[1] : alias;
		}
		out.push({ alias, target, line: idx + 1 });
	}
	return out;
}

/**
 * Extract module function/method definitions and local function definitions.
 */
function extractDefinitions(maskedSource) {
	const lines = maskedSource.split('\n');
	const defs = [];
	const returnMatch = maskedSource.match(/\breturn\s+([A-Za-z_]\w*)\s*;?\s*$/);
	const returnedTable = returnMatch ? returnMatch[1] : null;

	for (let idx = 0; idx < lines.length; idx++) {
		const line = lines[idx];
		let m = line.match(/^\s*local\s+function\s+([A-Za-z_]\w*)\s*\(/);
		if (m) {
			defs.push({ namespace: null, name: m[1], kind: 'local', line: idx + 1 });
			continue;
		}
		m = line.match(/^\s*function\s+([A-Za-z_]\w*)\s*([.:])\s*([A-Za-z_]\w*)\s*\(/);
		if (m) {
			defs.push({
				namespace: m[1],
				name: m[3],
				kind: m[2] === ':' ? 'method' : 'function',
				line: idx + 1,
			});
			continue;
		}
		m = line.match(/^\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*=\s*function\s*\(/);
		if (m && (!returnedTable || m[1] === returnedTable)) {
			defs.push({
				namespace: m[1],
				name: m[2],
				kind: 'function',
				line: idx + 1,
			});
		}
	}
	return defs;
}

/**
 * Extract `Namespace.member` and `Namespace:member` references from masked source.
 */
function extractReferences(maskedSource, namespaces) {
	const nsSet = new Set(namespaces || []);
	if (!nsSet.size) return [];
	const lines = maskedSource.split('\n');
	const refs = [];
	const re = /\b([A-Za-z_]\w*)\s*([.:])\s*([A-Za-z_]\w*)\b/g;
	for (let idx = 0; idx < lines.length; idx++) {
		const line = lines[idx];
		let m;
		re.lastIndex = 0;
		while ((m = re.exec(line)) !== null) {
			if (nsSet.has(m[1])) {
				refs.push({ namespace: m[1], name: m[3], kind: m[2], line: idx + 1 });
			}
		}
	}
	return refs;
}

/**
 * Build a map of `varName -> resolvedModuleName` for `local x = Mod.new(...)`.
 */
function buildInstanceVarMap(maskedSource, resolveNamespace) {
	const map = {};
	const re = /\blocal\s+([A-Za-z_]\w*)\s*(?::[^=]+)?=\s*([A-Za-z_]\w*)\s*[.:]\s*new\s*\(/g;
	let m;
	while ((m = re.exec(maskedSource)) !== null) {
		const varName = m[1];
		const modToken = m[2];
		const resolved = typeof resolveNamespace === 'function' ? resolveNamespace(modToken) : modToken;
		if (resolved) map[varName] = resolved;
	}
	return map;
}

/**
 * Extract `inst:Method(...)` references for variables tracked in `instMap`.
 */
function extractInstanceRefs(maskedSource, instMap) {
	const keys = Object.keys(instMap || {});
	if (!keys.length) return [];
	const varSet = new Set(keys);
	const lines = maskedSource.split('\n');
	const refs = [];
	const re = /\b([A-Za-z_]\w*)\s*:\s*([A-Za-z_]\w*)\s*\(/g;
	for (let idx = 0; idx < lines.length; idx++) {
		const line = lines[idx];
		let m;
		re.lastIndex = 0;
		while ((m = re.exec(line)) !== null) {
			if (varSet.has(m[1])) {
				refs.push({ namespace: instMap[m[1]], name: m[2], line: idx + 1 });
			}
		}
	}
	return refs;
}

/**
 * Full single-file static summary consumed by `call-graph.js`.
 */
function analyseFile(file) {
	const raw = fs.readFileSync(file, 'utf8');
	const masked = stripCommentsAndStrings(raw);
	return {
		file,
		instanceName: moduleInstanceName(file),
		requires: extractRequires(raw),
		definitions: extractDefinitions(masked),
	};
}

module.exports = {
	collectLuauFiles,
	moduleInstanceName,
	stripCommentsAndStrings,
	extractRequires,
	extractDefinitions,
	extractReferences,
	buildInstanceVarMap,
	extractInstanceRefs,
	analyseFile,
};
