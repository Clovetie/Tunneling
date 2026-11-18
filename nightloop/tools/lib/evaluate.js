'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, spawnSync } = require('child_process');

const REPO_ROOT = path.resolve(__dirname, '..', '..');

function die(message) {
	console.error(`evaluate: ${message}`);
	process.exit(2);
}

/**
 * Deterministic Fisher-Yates shuffle using Mulberry32 PRNG.
 */
function shuffle(items, seed = 17) {
	const arr = Array.isArray(items) ? items.slice() : [];
	let a = (Number(seed) || 17) >>> 0;
	const rand = () => {
		a |= 0;
		a = (a + 0x6D2B79F5) | 0;
		let t = Math.imul(a ^ (a >>> 15), 1 | a);
		t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
	for (let i = arr.length - 1; i > 0; i--) {
		const j = Math.floor(rand() * (i + 1));
		const tmp = arr[i];
		arr[i] = arr[j];
		arr[j] = tmp;
	}
	return arr;
}

function deepEqual(a, b) {
	if (a === b) return true;
	if (a === null || b === null || typeof a !== typeof b) return false;
	if (Array.isArray(a) || Array.isArray(b)) {
		if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
		for (let i = 0; i < a.length; i++) {
			if (!deepEqual(a[i], b[i])) return false;
		}
		return true;
	}
	if (typeof a === 'object') {
		const ka = Object.keys(a).sort();
		const kb = Object.keys(b).sort();
		if (!deepEqual(ka, kb)) return false;
		for (const k of ka) {
			if (!deepEqual(a[k], b[k])) return false;
		}
		return true;
	}
	return false;
}

function validateJsonSchema(value, schema) {
	if (!schema || typeof schema !== 'object') return { ok: true };
	if (schema.type) {
		if (schema.type === 'object') {
			if (value === null || typeof value !== 'object' || Array.isArray(value)) {
				return { ok: false, reason: 'expected JSON object' };
			}
		} else if (schema.type === 'array') {
			if (!Array.isArray(value)) return { ok: false, reason: 'expected JSON array' };
		} else if (schema.type === 'integer') {
			if (typeof value !== 'number' || !Number.isInteger(value)) {
				return { ok: false, reason: 'expected integer' };
			}
		} else if (typeof value !== schema.type) {
			return { ok: false, reason: `expected ${schema.type}, got ${typeof value}` };
		}
	}
	if (Array.isArray(schema.required) && value && typeof value === 'object') {
		for (const reqKey of schema.required) {
			if (!(reqKey in value)) {
				return { ok: false, reason: `missing required property "${reqKey}"` };
			}
		}
	}
	if (schema.properties && value && typeof value === 'object') {
		for (const [prop, subSchema] of Object.entries(schema.properties)) {
			if (prop in value) {
				const sub = validateJsonSchema(value[prop], subSchema);
				if (!sub.ok) return { ok: false, reason: `${prop}: ${sub.reason}` };
			}
		}
	}
	return { ok: true };
}

function stripCodeFences(text) {
	const trimmed = text.trim();
	const m = trimmed.match(/^```[A-Za-z0-9_-]*\r?\n([\s\S]*?)\r?\n```$/);
	return m ? m[1] : trimmed;
}

function scoreGeneratedCode(output, spec, timeoutMs) {
	const code = stripCodeFences(output);
	if (!code) return { score: 0, reason: 'empty code output' };
	const lang = String((spec && spec.lang) || 'node').toLowerCase();
	const expected = String((spec && spec.expected) ?? '').trim();
	const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'brine-eval-'));
	try {
		const ext = lang.startsWith('py') ? '.py' : '.js';
		const scriptFile = path.join(tmpDir, `snippet${ext}`);
		fs.writeFileSync(scriptFile, code, 'utf8');
		const cmd = lang.startsWith('py') ? 'python3' : process.execPath;
		const proc = spawnSync(cmd, [scriptFile], {
			cwd: tmpDir,
			encoding: 'utf8',
			timeout: Math.min(timeoutMs || 5000, 10000),
		});
		if (proc.error) return { score: 0, reason: `exec error: ${proc.error.message}` };
		if (proc.status !== 0) {
			return { score: 0, reason: `exit ${proc.status}: ${(proc.stderr || '').trim().slice(0, 120)}` };
		}
		const actual = String(proc.stdout || '').trim();
		if (actual === expected) return { score: 1, reason: 'ok' };
		return { score: 0, reason: `stdout "${actual}" !== expected "${expected}"` };
	} finally {
		try {
			fs.rmSync(tmpDir, { recursive: true, force: true });
		} catch (_) {}
	}
}

function scoreOutput(test, rawOutput, timeoutMs) {
	const output = String(rawOutput ?? '');
	const trimmed = output.trim();
	if (!trimmed) {
		return { score: 0, reason: 'empty output (fail-closed)' };
	}

	if (Array.isArray(test.must_not_match)) {
		const lower = output.toLowerCase();
		for (const forbidden of test.must_not_match) {
			if (lower.includes(String(forbidden).toLowerCase())) {
				return { score: 0, reason: `matched forbidden phrase "${forbidden}"` };
			}
		}
	}

	const type = test.type || 'exact';
	if (type === 'exact') {
		const exp = String(test.expected ?? '').trim();
		return trimmed === exp
			? { score: 1, reason: 'ok' }
			: { score: 0, reason: `got "${trimmed.slice(0, 80)}", expected "${exp}"` };
	}

	if (type === 'includes') {
		const needles = Array.isArray(test.expected) ? test.expected : [test.expected];
		const lower = output.toLowerCase();
		for (const needle of needles) {
			if (!lower.includes(String(needle).toLowerCase())) {
				return { score: 0, reason: `missing expected substring "${needle}"` };
			}
		}
		return { score: 1, reason: 'ok' };
	}

	if (type === 'regex') {
		const re = new RegExp(String(test.expected));
		return re.test(trimmed)
			? { score: 1, reason: 'ok' }
			: { score: 0, reason: `did not match /${test.expected}/` };
	}

	if (type === 'json') {
		let parsed;
		try {
			parsed = JSON.parse(trimmed);
		} catch (err) {
			return { score: 0, reason: `invalid JSON: ${err.message}` };
		}
		return deepEqual(parsed, test.expected)
			? { score: 1, reason: 'ok' }
			: { score: 0, reason: 'JSON value mismatch' };
	}

	if (type === 'json_schema') {
		let parsed;
		try {
			parsed = JSON.parse(stripCodeFences(trimmed));
		} catch (err) {
			return { score: 0, reason: `invalid JSON: ${err.message}` };
		}
		const check = validateJsonSchema(parsed, test.expected);
		return check.ok ? { score: 1, reason: 'ok' } : { score: 0, reason: check.reason };
	}

	if (type === 'code') {
		return scoreGeneratedCode(trimmed, test.expected, timeoutMs);
	}

	return { score: 0, reason: `unsupported test type "${type}"` };
}

function invokeAdapter(model, payload, timeoutMs) {
	return new Promise((resolve) => {
		const cwd = model.cwd || REPO_ROOT;
		const cmd = model.command;
		const args = Array.isArray(model.args) ? model.args : [];
		if (!cmd) {
			resolve({ ok: false, output: '', error: 'missing model command' });
			return;
		}
		let settled = false;
		let stdout = '';
		let stderr = '';
		let child;
		try {
			child = spawn(cmd, args, { cwd, stdio: ['pipe', 'pipe', 'pipe'] });
		} catch (err) {
			resolve({ ok: false, output: '', error: err.message });
			return;
		}

		const timer = setTimeout(() => {
			if (settled) return;
			settled = true;
			try { child.kill('SIGKILL'); } catch (_) {}
			resolve({ ok: false, output: stdout, error: `timeout after ${timeoutMs}ms` });
		}, timeoutMs);

		child.stdout.setEncoding('utf8');
		child.stderr.setEncoding('utf8');
		child.stdout.on('data', (chunk) => { stdout += chunk; });
		child.stderr.on('data', (chunk) => { stderr += chunk; });
		child.on('error', (err) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			resolve({ ok: false, output: '', error: err.message });
		});
		child.on('close', (code) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			if (code !== 0) {
				resolve({
					ok: false,
					output: stdout,
					error: `adapter exited ${code}${stderr.trim() ? `: ${stderr.trim().slice(0, 140)}` : ''}`,
				});
				return;
			}
			resolve({ ok: true, output: stdout, error: null });
		});

		try {
			child.stdin.write(JSON.stringify(payload));
			child.stdin.end();
		} catch (_) {}
	});
}

async function evaluateModel(model, tests, cfg = {}, options = {}) {
	const id = model.id || model.name || 'unnamed-model';
	const timeoutMs = Number(cfg.timeout_ms) || 15000;
	const repeats = Math.max(1, Number(cfg.repeats) || 1);
	const passThreshold = cfg.pass_threshold !== undefined ? Number(cfg.pass_threshold) : 0.8;
	const hardFailCategories = new Set(cfg.hard_fail_categories || []);
	const categoryThresholds = cfg.category_thresholds || {};

	const cases = [];
	const byCat = {};

	for (const test of tests) {
		let bestScore = 1;
		let lastReason = 'ok';
		let lastOutput = '';

		for (let r = 0; r < repeats; r++) {
			const res = await invokeAdapter(
				model,
				{ id: test.id, category: test.category, prompt: test.prompt },
				timeoutMs
			);
			lastOutput = res.output;
			if (!res.ok) {
				bestScore = 0;
				lastReason = res.error || 'adapter error';
				break;
			}
			const scored = scoreOutput(test, res.output, timeoutMs);
			if (scored.score < bestScore) {
				bestScore = scored.score;
				lastReason = scored.reason;
			}
		}

		const cat = test.category || 'uncategorized';
		(byCat[cat] ||= []).push(bestScore);

		const entry = {
			id: test.id,
			category: cat,
			type: test.type || 'exact',
			score: bestScore,
			reason: lastReason,
		};
		if (options.includeOutputs) {
			entry.output = lastOutput;
		}
		cases.push(entry);
	}

	const categoryScores = {};
	for (const [cat, scores] of Object.entries(byCat)) {
		categoryScores[cat] = scores.reduce((a, b) => a + b, 0) / scores.length;
	}

	const overallScore = cases.length
		? cases.reduce((a, c) => a + c.score, 0) / cases.length
		: 0;

	const rejectionReasons = [];
	if (overallScore < passThreshold) {
		rejectionReasons.push(`overall_score ${(overallScore * 100).toFixed(1)}% < ${(passThreshold * 100).toFixed(1)}%`);
	}
	for (const cat of hardFailCategories) {
		if (cat in categoryScores && categoryScores[cat] < 1) {
			rejectionReasons.push(`hard-fail category "${cat}" scored ${(categoryScores[cat] * 100).toFixed(1)}%`);
		}
	}
	for (const [cat, minScore] of Object.entries(categoryThresholds)) {
		if (cat in categoryScores && categoryScores[cat] < Number(minScore)) {
			rejectionReasons.push(`category "${cat}" ${(categoryScores[cat] * 100).toFixed(1)}% < ${(Number(minScore) * 100).toFixed(1)}%`);
		}
	}

	return {
		id,
		admitted: rejectionReasons.length === 0,
		overall_score: overallScore,
		category_scores: categoryScores,
		rejection_reasons: rejectionReasons,
		cases,
	};
}

module.exports = {
	die,
	shuffle,
	evaluateModel,
};
