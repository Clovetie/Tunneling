'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');

function stripLuauTypeDeclarations(source) {
	const lines = source.split('\n');
	const out = [];
	let skipping = false;
	let braceDepth = 0;

	for (const line of lines) {
		const startsType = /^\s*(?:export\s+)?type\s+[A-Za-z_]\w*/.test(line);
		if (startsType || skipping) {
			braceDepth += (line.match(/\{/g) || []).length;
			braceDepth -= (line.match(/\}/g) || []).length;
			out.push('-- <type stripped>');
			skipping = braceDepth > 0;
			continue;
		}
		out.push(line);
	}
	return out.join('\n');
}

function transformLuau(src, label = 'luau') {
	const cleaned = stripLuauTypeDeclarations(src);
	const lines = cleaned.split('\n');
	const out = [];
	const stack = [];
	let contId = 0;
	let inBlockComment = false;
	let inFnHead = false;

	for (const raw of lines) {
		let line = raw;
		let trimmed = line.trim();

		if (inBlockComment) {
			if (/\]=*\]/.test(trimmed)) {
				inBlockComment = false;
			}
			out.push(line);
			continue;
		}
		if (/^--\[=*\[/.test(trimmed)) {
			if (!/\]=*\]/.test(trimmed.slice(4))) {
				inBlockComment = true;
			}
			out.push(line);
			continue;
		}

		const isComment = trimmed.startsWith('--');

		if (!isComment) {
			const m = line.match(/^(\s*)([A-Za-z_][\w.[\]"']*)\s*(\+=|-=|\*=|\/=|\/\/=|%=|\.\.=)(.+)$/);
			if (m) {
				const op = m[3].slice(0, -1);
				line = `${m[1]}${m[2]} = ${m[2]} ${op} (${m[4].trim()})`;
			}
			line = line.replace(/\s*::\s*\([^)]*\)/g, '');
			line = line.replace(/\s*::\s*[A-Za-z_][\w?.|&<>]*/g, '');
			line = line.replace(/^(\s*local\s+[A-Za-z_]\w*)\s*:\s*[^=]+?=\s*/, '$1 = ');
			line = line.replace(/^(\s*local\s+[A-Za-z_]\w*)\s*:\s*[^=]+$/, '$1');

			if (inFnHead) {
				line = line.replace(/^(\s*[A-Za-z_]\w*\s*):\s*[^,]+\s*,\s*$/, '$1,');
				line = line.replace(/^(\s*[A-Za-z_]\w*\s*):\s*[^,]+\s*$/, '$1');
				line = line.replace(/^(\s*\))\s*:\s*.*$/, '$1');
				if (/^\s*\)/.test(line)) {
					inFnHead = false;
				}
			} else if (/^\s*(local\s+)?function\b/.test(line)) {
				if (/\($/.test(line.trim())) {
					inFnHead = true;
				} else {
					line = line.replace(/\)\s*:\s*\([^()]*\)\s*$/, ')');
					line = line.replace(/\)\s*:\s*[^(),]*$/, '');
					line = line.replace(/:\s*[A-Za-z_{][^,()]*?(?=[,)])/g, '');
				}
			}

			if (/^\s*for\b/.test(line)) {
				const fm = line.match(/^(\s*for\s+[A-Za-z_][\w,\s]*?\s+)in\s+(.+?)\s+do\b(.*)$/);
				if (fm && !/^(pairs|ipairs|next)\s*\(/.test(fm[2].trim())) {
					line = `${fm[1]}in pairs(${fm[2]}) do${fm[3] || ''}`;
				}
			}
		}

		trimmed = line.trim();

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
			out.push(line.replace(/\bcontinue\b/, 'goto ' + frame.labels[0]));
			continue;
		}

		if (!isComment) {
			if (/^(for|while)\b/.test(trimmed) && /\bdo\s*(--.*)?$/.test(trimmed)) {
				stack.push({ loop: true, labels: [], at: `line ${out.length + 1}: ${trimmed}` });
			} else if (trimmed === 'repeat') {
				stack.push({ loop: true, labels: [], at: `line ${out.length + 1}: ${trimmed}` });
			} else if (
				(/^(local\s+)?function\b/.test(trimmed) && !/\bend\s*(--.*)?$/.test(trimmed)) ||
				(!/^(local\s+)?function\b/.test(trimmed) && /\bfunction\s*\([^)]*\)\s*$/.test(trimmed))
			) {
				stack.push({ loop: false, labels: [], at: `line ${out.length + 1}: ${trimmed}` });
			} else if (/\bthen\s*(--.*)?$/.test(trimmed) && !/^elseif\b/.test(trimmed) && !/\bend\s*(--.*)?$/.test(trimmed)) {
				stack.push({ loop: false, labels: [], at: `line ${out.length + 1}: ${trimmed}` });
			} else if (/^do\s*(--.*)?$/.test(trimmed)) {
				stack.push({ loop: false, labels: [], at: `line ${out.length + 1}: ${trimmed}` });
			} else if (/^end\b/.test(trimmed) || /^until\b/.test(trimmed)) {
				const frame = stack.pop();
				if (frame && frame.loop && frame.labels.length > 0) {
					const indent = line.match(/^\s*/)[0];
					for (const lab of frame.labels) {
						out.push(`${indent}::${lab}::`);
					}
				}
			}
		}

		out.push(line);
	}

	return out.join('\n');
}

function luaLongString(level, s) {
	const eq = '='.repeat(level);
	return `[${eq}[${s}]${eq}]`;
}

function read(relPath) {
	return fs.readFileSync(path.join(ROOT, relPath), 'utf8');
}

module.exports = {
	transformLuau,
	luaLongString,
	read,
};
