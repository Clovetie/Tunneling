# BRINE Lua tooling

Local, repeatable analysis and quality tooling for the BRINE Luau scripts.
Everything in this folder is the reusable setup for future work. Generated
output goes to `tools/reports/` (git-ignored); build caches live under
`.tools/` and `tools/node_modules/` (both git-ignored).

## Quick start

```bash
bash tools/setup-tools.sh     # optional: installs selene/stylua/luau into .tools/
npm --prefix tools install    # installs luaparse for check-syntax.js

# One-shot audit (no external deps needed beyond node + luaparse):
bash tools/audit.sh
bash tools/audit.sh --json    # also writes JSON reports into tools/reports/
```

`tools/audit.sh` runs **all** the analysis tools below and prints a combined report.
It automatically selects the Explorer-first `code/` tree when present and skips the
legacy Rojo project validator when this repository has no `default.project.json`.

### `vitriol-quality-gate.js` — strict evidence gate

This is the command future agents should run before committing. It runs the smoke
and session harnesses, the syntax checker, metrics, hot-loop scan, call graph,
dead-config audit, model evaluation, and `git diff --check`. It treats only the two
known Lua 5.1 parser failures (`Lib/Noise.luau` and `Lib/RNG.luau`) as allowlisted;
a new syntax failure or any priced call in a RunService file fails the gate.

```bash
node tools/vitriol-quality-gate.js --src code
node tools/vitriol-quality-gate.js --src code --report tools/reports/quality-gate.json
```

The report is ignored under `tools/reports/` and keeps bounded stdout/stderr for
post-handoff inspection. A green run ends with `Vitriol quality gate: 9/9 checks
passed`. It is an evidence aggregator, not a substitute for Studio verification.

---

## Analysis tools (these are the "future-iteration toolkit")

All are **dependency-light Node** scripts (only `tools/run-node` needs
`luaparse`); they are heuristic, not compilers, and each prints its own caveat.

### `call-graph.js` — module dependency + orphan/public-API surface

Builds the `require()` dependency graph and, for every exported function, tells
you which module(s) call it. It then reports **orphaned exports** (defined but
never referenced by another module) — this is the machine-checked version of the
"dead/library-only code" finding in [`../AUDIT.md`](../AUDIT.md).

```bash
node tools/call-graph.js                     # human-readable table
node tools/call-graph.js --json reports/call-graph.json
```

Recognises `Helper.GetX`, `Module:Method`, `local Alias = require(...)`,
`local X = Mod.new(); X:Method()`, and Luau generics (`function Octree:CreateNode<T>()`).

### `metrics.js` — code-health / complexity trend

Per-file LOC, comment ratio, exported count, `require` count, heuristic
**cyclomatic complexity**, and a count of "heavy" constructs (raycasts,
`task.`, tween/mesh creation, physics movers, table churn). Useful to spot files
growing too large or too branchy across iterations.

```bash
node tools/metrics.js [--top 8] [--json reports/metrics.json]
```

### `validate-project.js` — Rojo project + layout gate

Checks `default.project.json` against the `src/` tree and enforces the Rojo
file-layout rules this repo depends on:

- a `$path` that points at a missing file/folder;
- the forbidden `Foo.luau` + `Foo/` sibling collision;
- a module folder missing its `init.luau`;
- known `.model.json` contract names (e.g. it flags `EditZone` staying a
  `BindableEvent` when the code needs a `RemoteEvent`).

```bash
node tools/validate-project.js [--json reports/project-validation.json]
```

### `find-dead-config.js` — unused settings knobs

Finds config keys that are declared but never read anywhere else in the tree.
It supports both config styles (`return { Key = ... }` and
`local C = {}; C.Key = ...; return C`). Verified on this repo: it flags
`WORKER_COUNT`, `FrustumRenderDistance`, `MaxChunkRenderDistance`, and
`FRUSTUM_CULLING_FIX` in `Settings` as unread.

```bash
node tools/find-dead-config.js [--file path] [--json reports/dead-config.json]
```

### `hot-loop-scan.js` — per-frame / loop hot-path risk

Flags expensive calls (`workspace:Raycast`, `RaycastLocal`, `GetAllNodes`,
`:Clone`, `GetPartBoundsInRadius`, `GetServerTimeNow`, `table.sort`, etc.) and
whether each sits inside a `for`/`while`/`repeat` body or a `RunService.*`
callback file. It uses a lightweight block-stack lexer, so the "LOOP"/"FRAME"
markers are a strong pointer, not proof.

```bash
node tools/hot-loop-scan.js [--top 20] [--json reports/hot-loop-scan.json]
```

### `check-syntax.js` — Luau-subset syntax gate

The project's original syntax checker. Uses `luaparse` to catch missing `end`s
and structural mistakes after stripping Luau-only syntax.

```bash
node tools/check-syntax.js
```

### `evaluate-models.js` — LLM preflight gate

Provider-neutral fail-closed admission gate for candidate text models. The
production prompt is never accepted here. See [`EVALUATION.md`](EVALUATION.md).

```bash
node tools/evaluate-models.js --models tools/models.example.json --suite tools/evaluation-suite.example.json
```

The committed example is **demonstrably green** now: it ships a deterministic,
known-good fixture adapter (`tools/fixture-model.js`) so you can see an `ADMIT`
end-to-end without network or credentials, alongside a deliberately-broken
`candidate-local` model to show the fail-closed `REJECT` path:

```text
fixture-known-good: ADMIT 100.0%
candidate-local:    REJECT   0.0%
```

### `workbench.js` — interactive evaluation workbench

Interactive CLI on top of the same engine. Lets you list/add/remove candidate
models and suite tests, run the suite against one or all models, inspect
per-test pass/fail (+ raw output with `--include-outputs`), and write a full
JSON report. It operates on *working* files (`tools/models.json` and
`tools/evaluation-suite.json`), seeding them from the `.example.json` templates
on first use.

```bash
node tools/workbench.js            # interactive REPL
node tools/workbench.js run        # run the whole suite (all models)
node tools/workbench.js run fixture-known-good --include-outputs
node tools/workbench.js report /tmp/report.json
node tools/workbench.js add-test '{"id":"r-9","category":"reasoning","type":"exact","prompt":"2+2?","expected":"4"}'
node tools/workbench.js seed       # re-copy working files from the example templates
```

---

## Shared library

`lib/paths.js` resolves the Explorer-first `code/` root and falls back to the
legacy `src/` layout without creating a Rojo project. This keeps bare tool commands
and npm scripts useful for future generations.

`lib/luau.js` is the shared, zero-dependency analysis engine used by the tools
above. It provides:

- `stripCommentsAndStrings` — masks code so heuristics act on real code only
  (correctly handles `--[[ ]]`, `--[=[ ]=]`, `[[ ]]`, quotes, backticks).
- `collectLuauFiles`, `moduleInstanceName` (Rojo init/layout aware).
- `extractDefinitions`, `extractRequires`, `extractReferences`,
  `extractInstanceRefs`, `buildInstanceVarMap`, `analyseFile`.

---

## External ecosystem tools (set via `setup-tools.sh`)

The repo also supports the standard Roblox/Luau toolchain. `setup-tools.sh`
installs into `.tools/` (git-ignored): `stylua` (formatter), `selene` (linter),
and `luau`/`luau-analyze` (compiler/analyzer). The linter/analyzer configs live
here:

```bash
.tools/bin/stylua --config-path tools/stylua.toml --check src
.tools/bin/selene --config tools/selene.toml src
.tools/bin/luau-analyze src
```

The toolchain manager **Rokit** is the recommended way to pin versions in CI:

```bash
curl -sSf https://raw.githubusercontent.com/rojo-rbx/rokit/main/scripts/install.sh | sh
rokit add rojo-rbx/rojo
rokit add Kampfkarren/selene
rokit add JohnnyMorganz/StyLua
rokit install
```

**Recommendations from ecosystem research** (checked 2026-09-09): Roblox's
[Luau type-checking documentation](https://create.roblox.com/docs/luau/type-checking)
confirms that `--!strict`/`--!nonstrict` feed Studio Script Analysis; use the real
Luau analyzer when Studio or an official toolchain is available. [StyLua
2.5.2](https://github.com/JohnnyMorganz/StyLua) provides deterministic Luau
formatting, [Selene](https://github.com/Kampfkarren/selene) provides a fast Lua/Luau
linter, and [Rokit](https://github.com/rojo-rbx/rokit) pins external tool versions.
`luau-lsp` is useful for editor diagnostics, `TestEZ` for Roblox-native tests,
`Wally` for packages, and `Lune` for Luau CI scripts. These remain optional: the
Explorer-first repository must keep its dependency-free Node gate green even when
network access or Roblox Studio is unavailable.

---

## CI

`.github/workflows/ci.yml` runs the deterministic Node analysis (`audit.sh`)
and optionally the ecosystem linters when `selene`/`stylua`/`luau-analyze` are
available. The Node tools are the stable baseline that works without network.

---

## Setup

```bash
bash tools/setup-tools.sh
```

Installs into `.tools/`:

- `stylua` — Luau formatter (config: `tools/stylua.toml`)
- `selene` — Lua linter (config: `tools/selene.toml`)
- `luau` / `luau-analyze` — Luau compiler + analyzer
- `luaparse` (npm, under `tools/node_modules`) — lightweight syntax parse
