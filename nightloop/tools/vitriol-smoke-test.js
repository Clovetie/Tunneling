#!/usr/bin/env node
// vitriol-smoke-test.js
// Headless runtime smoke test for the VITRIOL pure-Luau modules.
//
// Roblox is not available here, so this harness:
//   1. Transforms the Vitriol Luau sources (and the test file) into plain
//      Lua 5.3: strips `type` aliases and type annotations, rewrites compound
//      assignment, and generalises `for x in t`.
//   2. Runs them inside fengari (a Lua 5.3 VM in JS) with a tiny shim for the
//      Luau-only stdlib the modules use (table.create/clone/find/clear, math.clamp,
//      and `buffer` - see PREAMBLE - which is what lets Creature/Replicator's
//      byte-array <-> Buffer adapters be executed, not just read).
//   3. Executes tools/vitriol/tests.luau and prints PASS/FAIL per assertion.
//
// The modules under test are deliberately PURE (no game:GetService, no Instances,
// no task.*), which is what makes this possible and is a hard rule in
// docs/01-ARCHITECTURE.md §7.
//
// Run with:  npm --prefix tools install   (once)
//            node tools/vitriol-smoke-test.js
//            node tools/vitriol-smoke-test.js --debug-transform

const fs = require("fs");
const path = require("path");
const fengari = require("fengari");
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = fengari;

const ROOT = path.resolve(__dirname, "..");

// Modules under test: logical name -> repo-relative path.
const MODULES = {
	MathExt: "code/ReplicatedStorage/Vitriol/Lib/MathExt.luau",
	RNG: "code/ReplicatedStorage/Vitriol/Lib/RNG.luau",
	Fabrik: "code/ReplicatedStorage/Vitriol/Lib/Fabrik.luau",
	Genetics: "code/ReplicatedStorage/Vitriol/Lib/Genetics.luau",
	Tables: "code/ReplicatedStorage/Vitriol/Lib/Tables.luau",
	Signal: "code/ReplicatedStorage/Vitriol/Lib/Signal.luau",
	Noise: "code/ReplicatedStorage/Vitriol/Lib/Noise.luau",
	SpatialHash: "code/ReplicatedStorage/Vitriol/Lib/SpatialHash.luau",
	UtilityAI: "code/ReplicatedStorage/Vitriol/Lib/UtilityAI.luau",
	QLearning: "code/ReplicatedStorage/Vitriol/Lib/QLearning.luau",
	MarkovPredictor: "code/ReplicatedStorage/Vitriol/Lib/MarkovPredictor.luau",
	// Config is pure data with no requires, so the modules that consume it
	// (NoiseField, NetContract, TimeModel, Remotes) can be loaded for real.
	GameConfig: "code/ReplicatedStorage/Vitriol/Config/GameConfig.luau",
	DayNightConfig: "code/ReplicatedStorage/Vitriol/Config/DayNightConfig.luau",
	MonsterConfig: "code/ReplicatedStorage/Vitriol/Config/MonsterConfig.luau",
	SurvivalConfig: "code/ReplicatedStorage/Vitriol/Config/SurvivalConfig.luau",
	RegionIntegrity: "code/ReplicatedStorage/Vitriol/Shared/RegionIntegrity.luau",
	Skeleton: "code/ReplicatedStorage/Vitriol/Shared/Skeleton.luau",
	SkeletonCodec: "code/ReplicatedStorage/Vitriol/Shared/SkeletonCodec.luau",
	NoiseField: "code/ReplicatedStorage/Vitriol/Shared/NoiseField.luau",
	Remotes: "code/ReplicatedStorage/Vitriol/Shared/Remotes.luau",
	NetContract: "code/ReplicatedStorage/Vitriol/Shared/NetContract.luau",
	TimeModel: "code/ReplicatedStorage/Vitriol/Shared/TimeModel.luau",
	// ServerStorage, but its codec/adapter half is pure Lua + a `buffer` shim, which
	// is what the Replicator parity test needs to prove array/buffer agreement.
	Replicator: "code/ServerStorage/Vitriol/Creature/Replicator.luau",
	KillChoreography: "code/ServerStorage/Vitriol/Creature/Body/KillChoreography.luau",
};

// ---------------------------------------------------------------------------
// Luau -> Lua 5.3 transform (shared with vitriol-session-test.js; see tools/lib)
// ---------------------------------------------------------------------------
const { transformLuau, luaLongString, read } = require("./lib/luau-transform");

// ---------------------------------------------------------------------------
// Bundle
// ---------------------------------------------------------------------------
const transformed = {};
for (const [name, rel] of Object.entries(MODULES)) {
	transformed[name] = transformLuau(read(rel), name);
}
// --tests <path> lets you point the harness at a scratch file while debugging.
const testsArgIdx = process.argv.indexOf("--tests");
const testsPath = testsArgIdx >= 0 ? process.argv[testsArgIdx + 1] : "tools/vitriol/tests.luau";
const tTests = transformLuau(read(testsPath), "tests");

if (process.argv.includes("--debug-transform")) {
	for (const [name, src] of Object.entries(transformed)) {
		fs.writeFileSync(`/tmp/vitriol-${name}.lua`, src);
	}
	fs.writeFileSync("/tmp/vitriol-tests.lua", tTests);
	console.log("transformed sources written to /tmp/vitriol-*.lua");
	process.exit(0);
}

const PREAMBLE = `
-- Luau stdlib shims (fengari is Lua 5.3).
table.create = function(n, v)
	local t = {}
	for i = 1, n do t[i] = v end
	return t
end
table.clone = function(t)
	local u = {}
	for k, v in pairs(t) do u[k] = v end
	return u
end
table.clear = function(t)
	for k in pairs(t) do t[k] = nil end
end
table.find = function(t, value)
	for i = 1, #t do
		if t[i] == value then return i end
	end
	return nil
end
math.clamp = function(x, lo, hi)
	if x < lo then return lo end
	if x > hi then return hi end
	return x
end

-- The Roblox buffer library, minimal but faithful to the documented semantics
-- (create/len/readu8/writeu8; offsets are 0-based and fixed-size; an access
-- outside the buffer errors; writeu8 requires an integer in [0,255]).
-- It exists so Creature/Replicator's adapters can be EXECUTED here rather than
-- eyeballed: what this proves is the adapter's indexing arithmetic and byte
-- fidelity, not Roblox's allocator.
buffer = {
	create = function(size)
		if type(size) ~= "number" or size < 0 or size ~= math.floor(size) then
			error("buffer.create: size must be a non-negative integer", 2)
		end
		return { __size = size, __bytes = table.create(size, 0) }
	end,
	len = function(b)
		return b.__size
	end,
	writeu8 = function(b, offset, value)
		if type(offset) ~= "number" or offset < 0 or offset >= b.__size then
			error("buffer.writeu8: offset out of range", 2)
		end
		if type(value) ~= "number" or value < 0 or value > 255 or value ~= math.floor(value) then
			error("buffer.writeu8: value must be an integer in [0,255]", 2)
		end
		b.__bytes[offset + 1] = value
	end,
	readu8 = function(b, offset)
		if type(offset) ~= "number" or offset < 0 or offset >= b.__size then
			error("buffer.readu8: offset out of range", 2)
		end
		return b.__bytes[offset + 1]
	end,
}

LOGSTRING = ""
local origPrint = print
print = function(...)
	local parts = {}
	for i = 1, select("#", ...) do parts[i] = tostring(select(i, ...)) end
	LOGSTRING = LOGSTRING .. "print|" .. table.concat(parts, "\\t") .. "\\n"
end
warn = function(...)
	local parts = {}
	for i = 1, select("#", ...) do parts[i] = tostring(select(i, ...)) end
	LOGSTRING = LOGSTRING .. "warn|" .. table.concat(parts, "\\t") .. "\\n"
end

local loaded = {}
function VitriolRequire(name)
	if loaded[name] ~= nil then return loaded[name] end
	local src = SOURCES[name]
	if not src then error("VitriolRequire: unknown module " .. tostring(name)) end
	local fn, err = load(src, "@" .. name)
	if not fn then error("VitriolRequire: failed to load " .. name .. ": " .. tostring(err)) end
	local ok, result = pcall(fn)
	if not ok then error("VitriolRequire: module " .. name .. " errored: " .. tostring(result)) end
	loaded[name] = result
	return result
end
`;

const parts = [PREAMBLE, "SOURCES = {"];
for (const [name, src] of Object.entries(transformed)) {
	parts.push(`  ${name} = ${luaLongString(4, src)},`);
}
parts.push("}", tTests);
const bundle = parts.join("\n");

// ---------------------------------------------------------------------------
// Run
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
		if (!entry) continue;
		if (entry.startsWith("warn|")) console.log("[warn] " + entry.slice(5));
		else if (entry.startsWith("print|")) console.log(entry.slice(6));
	}
	return log;
}

if (status !== lua.LUA_OK) {
	const err = to_jsstring(lua.lua_tostring(L, -1) || to_luastring("<no error object>"));
	dumpLog();
	console.error("\nHARNESS CRASHED:\n" + err);
	process.exit(1);
}

dumpLog();

lua.lua_getglobal(L, to_luastring("TESTS"));
if (lua.lua_type(L, -1) !== lua.LUA_TTABLE) {
	console.error("tests.luau did not define a TESTS table");
	process.exit(1);
}
lua.lua_getfield(L, -1, to_luastring("passed"));
const passed = lua.lua_tonumber(L, -1);
lua.lua_pop(L, 1);
lua.lua_getfield(L, -1, to_luastring("failed"));
const failed = lua.lua_tonumber(L, -1);
lua.lua_pop(L, 2);

console.log("");
console.log(`VITRIOL smoke test: ${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
