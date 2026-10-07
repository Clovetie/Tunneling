#!/usr/bin/env node
"use strict";

/**
 * Vitriol quality gate — one deterministic pre-commit/CI command for future
 * iterations.
 *
 * This deliberately orchestrates the repository's existing tests and analysis
 * tools rather than replacing them. It understands the one documented parser
 * limitation in check-syntax.js (Lua 5.1 luaparse cannot parse Luau bitwise
 * `&` in Lib/RNG and Lib/Noise), but fails closed on every new syntax failure.
 *
 * Usage:
 *   node tools/vitriol-quality-gate.js
 *   node tools/vitriol-quality-gate.js --src code --report tools/reports/quality-gate.json
 *
 * The report contains bounded stdout/stderr for every command so a later agent
 * can inspect the exact evidence without rerunning a long suite. Generated
 * reports belong under tools/reports/ and are ignored by Git.
 */

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const { defaultSourceRoot } = require("./lib/paths");

const ROOT = path.resolve(__dirname, "..");
const NODE = process.execPath;
const MAX_CAPTURE = 400000;
const KNOWN_SYNTAX_FAILURES = new Set([
	"code/ReplicatedStorage/Vitriol/Lib/Noise.luau",
	"code/ReplicatedStorage/Vitriol/Lib/RNG.luau",
]);

function die(message) {
	console.error(`vitriol-quality-gate: ${message}`);
	process.exit(2);
}

function parseArgs(argv) {
	const out = {
		src: path.relative(ROOT, defaultSourceRoot()).replace(/\\/g, "/") || ".",
		report: null,
		quiet: false,
	};
	for (let i = 2; i < argv.length; i += 1) {
		const arg = argv[i];
		if (arg === "--src") {
			const absolute = path.resolve(process.cwd(), argv[++i]);
			out.src = path.relative(ROOT, absolute).replace(/\\/g, "/") || ".";
		}
		else if (arg === "--report") out.report = argv[++i];
		else if (arg === "--quiet") out.quiet = true;
		else if (arg === "--help") {
			console.log("Usage: node tools/vitriol-quality-gate.js [--src code] [--report path] [--quiet]");
			process.exit(0);
		} else {
			die(`unknown argument ${arg}`);
		}
	}
	return out;
}

function relativePath(value) {
	return path.relative(ROOT, value).replace(/\\/g, "/");
}

function commandString(args) {
	return ["node", ...args].join(" ");
}

function runNode(args) {
	const result = spawnSync(NODE, args, {
		cwd: ROOT,
		encoding: "utf8",
		maxBuffer: 32 * 1024 * 1024,
	});
	const stdout = result.stdout || "";
	const stderr = result.stderr || "";
	const combined = `${stdout}${stderr}`;
	return {
		command: commandString(args),
		args,
		exitCode: typeof result.status === "number" ? result.status : 1,
		signal: result.signal || null,
		stdout,
		stderr,
		combined,
		spawnError: result.error ? String(result.error.message || result.error) : null,
	};
}

function runShell(args) {
	const result = spawnSync(args[0], args.slice(1), {
		cwd: ROOT,
		encoding: "utf8",
		maxBuffer: 32 * 1024 * 1024,
	});
	const stdout = result.stdout || "";
	const stderr = result.stderr || "";
	return {
		command: args.join(" "),
		args,
		exitCode: typeof result.status === "number" ? result.status : 1,
		signal: result.signal || null,
		stdout,
		stderr,
		combined: `${stdout}${stderr}`,
		spawnError: result.error ? String(result.error.message || result.error) : null,
	};
}

function bounded(text) {
	if (text.length <= MAX_CAPTURE) return { text, truncated: false };
	return { text: `${text.slice(0, MAX_CAPTURE)}\n[output truncated]`, truncated: true };
}

function summaryLine(output, regex) {
	const match = output.combined.match(regex);
	return match ? match[0].trim() : null;
}

function syntaxAssessment(output) {
	const failures = [...output.combined.matchAll(/^FAIL\s+(.+)$/gm)].map((match) => match[1].trim());
	const normalised = failures.map((file) => file.replace(/^.*?(?=code\/)/, ""));
	const unexpected = normalised.filter((file) => !KNOWN_SYNTAX_FAILURES.has(file));
	const okCount = (output.combined.match(/^OK\s+/gm) || []).length;
	const allowed = unexpected.length === 0 && (output.exitCode === 0 || normalised.length === KNOWN_SYNTAX_FAILURES.size);
	return {
		passed: allowed,
		okCount,
		failures: normalised,
		unexpected,
		known: normalised.filter((file) => KNOWN_SYNTAX_FAILURES.has(file)),
		note: unexpected.length === 0 && normalised.length > 0
			? "known Lua 5.1 parser exceptions only"
			: "all Luau files parsed",
	};
}

function evaluateCheck(id, output) {
	const combined = output.combined;
	if (id === "smoke") {
		const match = combined.match(/VITRIOL smoke test:\s+(\d+) passed,\s+(\d+) failed/);
		return { passed: output.exitCode === 0 && !!match && Number(match[2]) === 0, summary: match ? match[0] : null };
	}
	if (id === "session") {
		const match = combined.match(/VITRIOL session test:\s+(\d+) passed,\s+(\d+) failed/);
		return { passed: output.exitCode === 0 && !!match && Number(match[2]) === 0, summary: match ? match[0] : null };
	}
	if (id === "syntax") return syntaxAssessment(output);
	if (id === "metrics") {
		const match = combined.match(/=== BRINE metrics \((\d+) files\) ===[\s\S]*?LOC (\d+) \| code (\d+) \| comments (\d+) \| exports (\d+) \| requires (\d+)/);
		return { passed: output.exitCode === 0 && !!match && Number(match[1]) > 0, summary: match ? match[0].replace(/\s+/g, " ") : null };
	}
	if (id === "hot-loop") {
		const match = combined.match(/Priced calls:\s+(\d+) \| inside-a-loop:\s+(\d+) \| in a RunService file:\s+(\d+)/);
		return { passed: output.exitCode === 0 && !!match && Number(match[3]) === 0, summary: match ? match[0] : null, framePathCalls: match ? Number(match[3]) : null };
	}
	if (id === "call-graph") {
		const match = combined.match(/Modules:\s+(\d+)[\s\S]*?Require edges:\s+(\d+)/);
		return { passed: output.exitCode === 0 && !!match && Number(match[1]) > 0, summary: match ? match[0].replace(/\s+/g, " ") : null };
	}
	if (id === "dead-config") {
		return { passed: output.exitCode === 0, summary: summaryLine(output, /(?:^! .*: not found$|^=== .*config keys ===$)/m) };
	}
	if (id === "evaluation") {
		const admit = /fixture-known-good:\s+ADMIT\s+100\.0%/.test(combined);
		const reject = /candidate-local:\s+REJECT\s+0\.0%/.test(combined);
		return { passed: output.exitCode === 0 && admit && reject, summary: `${admit ? "ADMIT 100.0%" : "missing ADMIT"} / ${reject ? "REJECT 0.0%" : "missing REJECT"}` };
	}
	if (id === "diff-check") {
		return { passed: output.exitCode === 0, summary: output.exitCode === 0 ? "no whitespace errors" : null };
	}
	return { passed: output.exitCode === 0, summary: null };
}

function main() {
	const cli = parseArgs(process.argv);
	const src = cli.src;
	const checks = [
		["smoke", ["tools/vitriol-smoke-test.js"]],
		["session", ["tools/vitriol-session-test.js"]],
		["syntax", ["tools/check-syntax.js", src]],
		["metrics", ["tools/metrics.js", "--src", src]],
		["hot-loop", ["tools/hot-loop-scan.js", "--src", src]],
		["call-graph", ["tools/call-graph.js", "--src", src]],
		["dead-config", ["tools/find-dead-config.js", "--src", src]],
		["evaluation", ["tools/evaluate-models.js", "--models", "tools/models.example.json", "--suite", "tools/evaluation-suite.example.json"]],
		["diff-check", null],
	];
	const results = [];
	for (const [id, args] of checks) {
		const raw = args ? runNode(args) : runShell(["git", "diff", "--check", "HEAD"]);
		const assessment = evaluateCheck(id, raw);
		const record = {
			id,
			command: raw.command,
			exitCode: raw.exitCode,
			signal: raw.signal,
			spawnError: raw.spawnError,
			passed: assessment.passed,
			summary: assessment.summary || null,
			notes: assessment.note || null,
			stdout: bounded(raw.stdout),
			stderr: bounded(raw.stderr),
		};
		if (id === "syntax") {
			record.okCount = assessment.okCount;
			record.failures = assessment.failures;
			record.knownFailures = assessment.known;
			record.unexpectedFailures = assessment.unexpected;
		}
		results.push(record);
		if (!cli.quiet) {
			console.log(`[${record.passed ? "PASS" : "FAIL"}] ${id}${record.summary ? ` — ${record.summary}` : ""}`);
			if (id === "syntax" && assessment.known && assessment.known.length) {
				console.log(`       allowlisted syntax exceptions: ${assessment.known.join(", ")}`);
			}
			if (!record.passed && raw.stderr) console.error(raw.stderr.trim().split("\n").slice(-4).join("\n"));
		}
	}

	const failed = results.filter((result) => !result.passed);
	const report = {
		schema: 1,
		generated_at: new Date().toISOString(),
		sourceRoot: src,
		checks: results,
		passed: failed.length === 0,
		summary: { total: results.length, passed: results.length - failed.length, failed: failed.length },
	};
	if (cli.report) {
		const reportPath = path.resolve(ROOT, cli.report);
		fs.mkdirSync(path.dirname(reportPath), { recursive: true });
		fs.writeFileSync(reportPath, JSON.stringify(report, null, 2) + "\n");
		if (!cli.quiet) console.log(`Report: ${relativePath(reportPath)}`);
	}
	console.log(`Vitriol quality gate: ${report.summary.passed}/${report.summary.total} checks passed`);
	if (failed.length) {
		console.error(`Failed checks: ${failed.map((result) => result.id).join(", ")}`);
		process.exitCode = 1;
	}
}

main();
