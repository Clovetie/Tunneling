"""The Luau `run_testez_suite` installs into Studio.

`mcp_core.run_testez_suite_helper` reads a local `tests/` directory (the `*.spec.lua`
layout `roblox-kit/systems/CONVENTIONS.md` defines), builds it into a scratch container as
one ModuleScript per spec plus the runner below, runs it through `run_studio_tests` — which
is the existing StudioTestService path, so the session is reported and torn down the same
way — and deletes the container afterwards unless asked to keep it.

The runner discovers TestEZ *in Studio*, in this order: an explicit `testezPath`, then a
`TestEZ` ModuleScript under ReplicatedStorage / ServerStorage / ReplicatedFirst. With none
of those it falls back to the bundled shim below, which implements the subset of TestEZ the
kit's own specs use (`describe` / `it` / `beforeEach` / `afterEach` and the `expect` chains
`.to.equal`, `.to.be.ok()`, `.never.to.be.ok()`, `.to.be.truthy()`, `.to.be.near()`). The
report always names the runner it used, so a green run against the shim is never mistaken
for a green run against TestEZ.

Nothing here is executed by CI — there is no Luau runtime in this repository, only
`luau-compile` and `luau-analyze`. It is compile-checked and undefined-global-checked, and
`run_testez_suite` is marked operator-verify for that reason.
"""

import re

RUNNER_NAME = "ArenaSpecRunner"
DEFAULT_CONTAINER = "ServerStorage.ArenaTestSuites"

# Kept as one string so `luau-compile` can check exactly what Studio will receive.
RUNNER_SOURCE = r'''--!nonstrict
-- Installed by the bridge's run_testez_suite tool. Requires every spec beside it, runs
-- them under TestEZ when the place has it and under a bundled shim when it does not, and
-- returns one report. Deleted again after the run unless keepContainer was set.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerStorage = game:GetService("ServerStorage")

local M = {}

local function findTestEZ(requested)
	if type(requested) == "string" and requested ~= "" then
		local parts = requested:split(".")
		local holder = game
		for _, part in ipairs(parts) do
			holder = holder and holder:FindFirstChild(part)
		end
		if holder then
			local ok, module = pcall(require, holder)
			if ok then
				return module, "explicit:" .. requested
			end
		end
	end
	for _, service in ipairs({ ReplicatedStorage, ServerStorage }) do
		for _, name in ipairs({ "TestEZ", "Packages" }) do
			local candidate = service:FindFirstChild(name)
			if candidate then
				local target = candidate
				if name == "Packages" then
					target = candidate:FindFirstChild("TestEZ")
				end
				if target then
					local ok, module = pcall(require, target)
					if ok and type(module) == "table" then
						return module, target:GetFullName()
					end
				end
			end
		end
	end
	return nil, nil
end

-- ── bundled shim: the subset of TestEZ the kit's specs use ──────────────────────────────

local function deepEqual(a, b)
	if a == b then
		return true
	end
	if type(a) ~= "table" or type(b) ~= "table" then
		return false
	end
	for key, value in pairs(a) do
		if not deepEqual(value, b[key]) then
			return false
		end
	end
	for key in pairs(b) do
		if a[key] == nil then
			return false
		end
	end
	return true
end

local function describeValue(value)
	local kind = typeof(value)
	if kind == "string" then
		return ("[%s]"):format(value)
	elseif kind == "table" then
		return "table"
	end
	return tostring(value)
end

local function makeExpect(state, path)
	local api = {}
	local function fail(message)
		error({ message = message, path = path }, 0)
	end

	local matchers = {}
	function matchers.equal(expected)
		if not deepEqual(state.value, expected) then
			fail(("%s: expected %s, got %s"):format(path, describeValue(expected), describeValue(state.value)))
		end
	end
	function matchers.never_equal(expected)
		if deepEqual(state.value, expected) then
			fail(("%s: expected anything but %s"):format(path, describeValue(expected)))
		end
	end
	function matchers.ok()
		if state.negated then
			if state.value ~= nil then
				fail(("%s: expected nil, got %s"):format(path, describeValue(state.value)))
			end
		elseif state.value == nil then
			fail(("%s: expected a value, got nil"):format(path))
		end
	end
	function matchers.truthy()
		local isTruthy = state.value and true or false
		if state.negated then
			if isTruthy then
				fail(("%s: expected falsy, got %s"):format(path, describeValue(state.value)))
			end
		elseif not isTruthy then
			fail(("%s: expected truthy, got %s"):format(path, describeValue(state.value)))
		end
	end
	function matchers.near(expected, tolerance)
		local delta = math.abs((tonumber(state.value) or math.huge) - (tonumber(expected) or 0))
		local allowed = tonumber(tolerance) or 1e-6
		if state.negated then
			if delta <= allowed then
				fail(("%s: expected a value further than %s from %s"):format(path, tostring(allowed), describeValue(expected)))
			end
		elseif delta > allowed then
			fail(("%s: expected %s to be within %s of %s"):format(path, describeValue(state.value), tostring(allowed), describeValue(expected)))
		end
	end
	function matchers.greater(threshold)
		local passes = (tonumber(state.value) or -math.huge) > (tonumber(threshold) or 0)
		if state.negated then
			if passes then
				fail(("%s: expected %s not to be greater than %s"):format(path, describeValue(state.value), describeValue(threshold)))
			end
		elseif not passes then
			fail(("%s: expected %s to be greater than %s"):format(path, describeValue(state.value), describeValue(threshold)))
		end
	end
	function matchers.less(threshold)
		local passes = (tonumber(state.value) or math.huge) < (tonumber(threshold) or 0)
		if state.negated then
			if passes then
				fail(("%s: expected %s not to be less than %s"):format(path, describeValue(state.value), describeValue(threshold)))
			end
		elseif not passes then
			fail(("%s: expected %s to be less than %s"):format(path, describeValue(state.value), describeValue(threshold)))
		end
	end
	-- expect(function() ... end).to.throw([messageSubstring]). `roblox-kit/modules/tests/
	-- FeatureFlags.spec.lua` asserts that `Flags.set` refuses to run on a client this way,
	-- so the shim has to answer it: an unimplemented matcher would fail that spec for a
	-- reason that has nothing to do with the code under test.
	function matchers.throw(expectedMessage)
		if type(state.value) ~= "function" then
			fail(("%s: .to.throw() needs a function to call, got %s"):format(path, describeValue(state.value)))
			return
		end
		local ok, err = pcall(state.value)
		local wanted = expectedMessage ~= nil and tostring(expectedMessage) or nil
		local threw = not ok
		-- `.never.to.throw("x")` means "does not throw *that* error", so the message is part
		-- of the assertion in both directions rather than a filter applied afterwards.
		local matched = threw and (wanted == nil or string.find(tostring(err), wanted, 1, true) ~= nil)
		local qualifier = ""
		if wanted ~= nil then
			qualifier = " a message containing " .. wanted
		end
		if state.negated then
			if matched then
				fail(("%s: expected the call not to throw%s, but it threw %s")
					:format(path, qualifier, tostring(err)))
			end
		elseif not matched then
			if threw then
				fail(("%s: expected the call to throw%s, but it threw %s")
					:format(path, qualifier, tostring(err)))
			else
				fail(("%s: expected the call to throw%s, but it returned normally")
					:format(path, qualifier))
			end
		end
	end

	local chain = {}
	chain.to = chain
	chain.be = chain
	chain.been = chain
	chain.have = chain
	chain.was = chain
	chain.at = chain
	chain.never = setmetatable({}, {
		__index = function(_, key)
			if key == "equal" then
				return matchers.never_equal
			end
			state.negated = true
			return function(...)
				local result = matchers[key](...)
				state.negated = false
				return result
			end
		end,
	})
	setmetatable(chain, {
		__index = function(_, key)
			local matcher = matchers[key]
			if type(matcher) ~= "function" then
				fail(("%s: the bundled runner has no matcher %q"):format(path, tostring(key)))
			end
			return matcher
		end,
	})
	api.chain = chain
	return api
end

local function shimRun(specModules)
	local stack = { { name = "", befores = {}, afters = {} } }
	local results = {}
	local current = nil

	local function pathOf(name)
		local parts = {}
		for _, frame in ipairs(stack) do
			if frame.name ~= "" then
				table.insert(parts, frame.name)
			end
		end
		table.insert(parts, name)
		return table.concat(parts, " ")
	end

	local function describe(name, body)
		local parent = stack[#stack]
		table.insert(stack, { name = name, befores = {}, afters = {}, parent = parent })
		local ok, err = pcall(body)
		table.remove(stack)
		if not ok then
			table.insert(results, { name = pathOf(name), status = "failure", message = tostring(err) })
		end
	end

	local function it(name, body)
		local path = pathOf(name)
		local started = os.clock()
		current = { name = path, failures = {} }
		for _, frame in ipairs(stack) do
			for _, hook in ipairs(frame.befores) do
				local ok, err = pcall(hook)
				if not ok then
					table.insert(current.failures, ("beforeEach: %s"):format(tostring(err)))
				end
			end
		end
		local ok, err = pcall(body)
		if not ok then
			local message = type(err) == "table" and err.message or tostring(err)
			table.insert(current.failures, message)
		end
		for index = #stack, 1, -1 do
			for _, hook in ipairs(stack[index].afters) do
				local hookOk, hookErr = pcall(hook)
				if not hookOk then
					table.insert(current.failures, ("afterEach: %s"):format(tostring(hookErr)))
				end
			end
		end
		current.durationMs = math.floor((os.clock() - started) * 1000 + 0.5)
		current.status = #current.failures == 0 and "success" or "failure"
		current.message = current.failures[1]
		table.insert(results, current)
		current = nil
	end

	local function expect(value)
		local state = { value = value, negated = false }
		local path = current and current.name or "outside a test"
		return makeExpect(state, path).chain
	end

	local globals = {
		describe = describe,
		it = it,
		itFOCUS = it,
		itSKIP = function() end,
		fit = it,
		expect = expect,
		beforeEach = function(hook)
			table.insert(stack[#stack].befores, hook)
		end,
		afterEach = function(hook)
			table.insert(stack[#stack].afters, hook)
		end,
		FOCUS = function() end,
		SKIP = function() end,
	}

	-- TestEZ exposes its DSL as globals while a spec runs; the kit's specs are written
	-- against that, and tools/check_globals.sh already allowlists the same names.
	local saved = {}
	for name, value in pairs(globals) do
		saved[name] = _G[name]
		_G[name] = value
	end
	for _, spec in ipairs(specModules) do
		local ok, err = pcall(spec.source)
		if not ok then
			table.insert(results, { name = spec.name, status = "failure", message = tostring(err), durationMs = 0 })
		end
	end
	for name in pairs(globals) do
		_G[name] = saved[name]
	end
	return results
end

function M.run(ctx)
	local args = (ctx and ctx.args) or {}
	local started = os.clock()
	local specs = {}
	for _, child in ipairs(script.Parent:GetChildren()) do
		if child:IsA("ModuleScript") and child.Name ~= script.Name and child.Name:match("%.spec$") then
			local ok, loaded = pcall(require, child)
			if ok and type(loaded) == "function" then
				table.insert(specs, { name = child.Name:gsub("%.spec$", ""), source = loaded })
			elseif ok then
				table.insert(specs, { name = child.Name, source = function()
					error("the spec module returned " .. typeof(loaded) .. ", not a function", 0)
				end })
			else
				table.insert(specs, { name = child.Name, source = function()
					error(tostring(loaded), 0)
				end })
			end
		end
	end
	table.sort(specs, function(a, b)
		return a.name < b.name
	end)

	local testez, runnerName = findTestEZ(args.testezPath)
	local results
	if testez and type(testez.TestBootstrap) == "table" then
		-- The place's own TestEZ: hand it the spec modules and let it build the plan.
		local modules = {}
		for _, child in ipairs(script.Parent:GetChildren()) do
			if child:IsA("ModuleScript") and child.Name ~= script.Name then
				table.insert(modules, child)
			end
		end
		local ok, report = pcall(function()
			return testez.TestBootstrap:run(modules, testez.Reporters.TextReporterQuiet)
		end)
		-- One row, not one row per test: the verdict here is TestEZ's own. Its text reporter
		-- raises on a failing suite, so a pcall error means failures and the error text is the
		-- report. Stated outright so nobody reads per-spec detail into this branch.
		local row = {
			name = "TestEZ suite (" .. tostring(#modules) .. " spec modules)",
			status = ok and "success" or "failure",
			durationMs = math.floor((os.clock() - started) * 1000 + 0.5),
			detail = "verdict is TestEZ's own; per-test breakdown needs its Reporters API",
		}
		if ok then
			row.output = tostring(report)
		else
			row.message = tostring(report)
		end
		results = { row }
		runnerName = runnerName or "testez"
	else
		results = shimRun(specs)
		runnerName = "bundled-shim"
	end

	local passed, failed = 0, 0
	for _, result in ipairs(results) do
		if result.status == "success" then
			passed += 1
		else
			failed += 1
		end
	end
	return {
		ok = failed == 0,
		runner = runnerName,
		specs = #specs,
		passed = passed,
		failed = failed,
		durationMs = math.floor((os.clock() - started) * 1000 + 0.5),
		results = results,
	}
end

return M
'''


# What the bundled shim can actually run. Declared here, next to the source that implements
# it, so a spec that reaches past this set can be *reported* rather than silently passing:
# under real TestEZ `beforeEach` works, under the shim it would be an undefined global and
# the assertions inside it would never run — a green report that means nothing.
SUPPORTED_DSL = frozenset({
    "describe", "it", "itFOCUS", "itSKIP", "fit", "expect",
    "beforeEach", "afterEach", "FOCUS", "SKIP",
})

# Matcher names reachable as `expect(x).to.<name>` / `.to.be.<name>`, `never` included.
SUPPORTED_MATCHERS = frozenset({
    "equal", "ok", "truthy", "near", "greater", "less", "throw", "never",
})

# TestEZ's chain has no-op link words (`to.be.ok`, `to.be.a`) and modifiers (`never`).
# A matcher is the single name that terminates the chain, so the scan stops there: matching
# greedily past it reports the *arguments*' field names as matchers, which is how
# `expect(x).to.equal(CFG.MusicVolume.default)` came to look like three unsupported matchers.
_LINK_WORDS = frozenset({"to", "be", "been", "being", "at", "not"})
_MODIFIERS = frozenset({"never"})

_DSL_NAMES = SUPPORTED_DSL | {
    # Names TestEZ has that the shim deliberately does not implement. Scanned for so that
    # using one is *reported* instead of failing inside Studio as an opaque nil call.
    "beforeAll", "afterAll", "fdescribe", "describeFOCUS", "describeSKIP", "HACK_NO_XPCALL",
}
_DSL_RE = re.compile(r"\b(" + "|".join(sorted(_DSL_NAMES)) + r")\b\s*\(")
_CHAIN_RE = re.compile(
    r"\.to\b((?:\s*\.\s*(?:be|been|being|at|not|never))*\s*\.\s*[A-Za-z_]\w*)"
)
_NEVER_RE = re.compile(r"\.never\b")


def scan_spec_dsl(source: str) -> dict:
    """Cross-check one spec's source against what the bundled shim implements.

    Returns the DSL and matcher names the spec uses plus the ones the shim cannot run. Pure
    text analysis, so CI can gate on it without a Luau runtime — the only honest way to test
    the shim, since nothing here can execute it.
    """
    text = source if isinstance(source, str) else ""
    dsl = sorted(set(_DSL_RE.findall(text)))
    used = set()
    if _NEVER_RE.search(text):
        used |= _MODIFIERS
    for chain in _CHAIN_RE.findall(text):
        names = [n for n in re.findall(r"\.\s*([A-Za-z_]\w*)", chain) if n not in _LINK_WORDS]
        used.update(names)
    return {
        "dsl": dsl,
        "matchers": sorted(used),
        "unsupportedDsl": sorted(name for name in dsl if name not in SUPPORTED_DSL),
        "unsupportedMatchers": sorted(name for name in used if name not in SUPPORTED_MATCHERS),
    }


def render_runner_source() -> str:
    return RUNNER_SOURCE
