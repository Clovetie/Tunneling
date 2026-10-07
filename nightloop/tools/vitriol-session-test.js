#!/usr/bin/env node
// vitriol-session-test.js
// Location: tools/vitriol-session-test.js
// Purpose:  The WP4 acceptance gate, executed. A server with ZERO players must run a
//           full Dawn -> Day -> Dusk -> Night cycle, log exactly one line per phase
//           change with the elapsed time in it, and never touch Lighting. That is not
//           checkable by reading the code, so this harness builds a fake DataModel with
//           a virtual clock and runs the REAL service modules in it.
//
//           What is shimmed, and what that does and does not prove:
//             - Instances, attributes, Parent/children, RemoteEvent/RemoteFunction,
//               Players, RunService, Workspace: enough of the API that the services use,
//               with the documented behaviour for SetAttribute (no change, no signal).
//               It proves the LOGIC, not replication. Nothing here tests packet size,
//               attribute delivery timing or a client's view.
//             - Virtual time. `task.wait`/`task.spawn` are POISONED: they error. The
//               server's only loop is supposed to be the Heartbeat binding Bootstrap
//               makes (docs/01 §4), and a harness where yielding works would let a
//               service quietly invent a scheduler and still pass.
//             - `os.clock` returns the virtual clock, so a cadence slot's measured cost
//               is 0 unless the test deliberately burns time (SIM.burn), which is how
//               the Profiler's auto-degrade path gets exercised deterministically.
//             - Lighting is a poisoned object: every property write errors, and merely
//               fetching the service is counted. The docs/06 §9.5 grep asks "does the
//               text mention Lighting"; this asks the stronger question "did the running
//               server touch it", which is the property the rule is about.
//
//           Run:  node tools/vitriol-session-test.js [--debug-transform]
//                 node tools/vitriol-session-test.js --tests tools/vitriol/session.luau
//           Needs the same fengari install as the smoke test (npm --prefix tools install).

const fs = require("fs");
const path = require("path");
const fengari = require("fengari");
const { transformLuau, luaLongString } = require("./lib/luau-transform");
const { lua, lauxlib, lualib, to_luastring, to_jsstring } = fengari;

const ROOT = path.resolve(__dirname, "..");
const CODE = path.join(ROOT, "code");

// Which Roblox class each script becomes, by where it lives. A Script only runs under
// Workspace or ServerScriptService (docs/08 §10), which is exactly why Bootstrap is a
// ModuleScript in ServerStorage and ServerScriptService.Vitriol.Main is the entry point.
function classFor(rel) {
	if (rel.startsWith("ServerScriptService/") || rel.startsWith("StarterPlayer/")) {
		return rel.startsWith("ServerScriptService/") ? "Script" : "LocalScript";
	}
	return "ModuleScript";
}

// Discover every .luau under code/, keyed by the last path segment (which is what the
// transform's VitriolRequire resolves) and by its full instance path.
const sources = {};
const nodes = [];
(function walk(dir, prefix) {
	for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
		const abs = path.join(dir, entry.name);
		const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
		if (entry.isDirectory()) {
			walk(abs, rel);
		} else if (entry.name.endsWith(".luau")) {
			const key = entry.name.replace(/\.luau$/, "");
			if (sources[key] !== undefined) {
				console.error(`\nHARNESS: two modules share the name "${key}". The` +
					` headless loader resolves require() by last path segment, so it` +
					` cannot tell them apart. Rename one, or give the loader a path.`);
				process.exit(1);
			}
			const label = rel.replace(/\.luau$/, "");
			sources[key] = transformLuau(fs.readFileSync(abs, "utf8"), label);
			nodes.push({ path: label, class: classFor(label) });
		}
	}
})(CODE, "");

const testsArgIdx = process.argv.indexOf("--tests");
const testsPath = testsArgIdx >= 0 ? process.argv[testsArgIdx + 1] : "tools/vitriol/session.luau";
const scenario = transformLuau(fs.readFileSync(path.join(ROOT, testsPath), "utf8"), "session");

if (process.argv.includes("--debug-transform")) {
	for (const [name, src] of Object.entries(sources)) {
		fs.writeFileSync(`/tmp/vitriol-session-${name}.lua`, src);
	}
	fs.writeFileSync("/tmp/vitriol-session-scenario.lua", scenario);
	console.log(`transformed ${Object.keys(sources).length} modules to /tmp/vitriol-session-*.lua`);
	process.exit(0);
}

// ---------------------------------------------------------------------------
// The fake DataModel, in Lua (it is much shorter here than it would be in JS,
// and it has to be Lua anyway: the code under test indexes instances).
// ---------------------------------------------------------------------------
const SHIM = `
-- ======================= Luau stdlib the modules use =======================
table.create = function(n, v)
	local t = {}
	for i = 1, n do t[i] = v end
	return t
end
table.clone = function(t)
	local c = {}
	for i, v in ipairs(t) do c[i] = v end
	for k, v in pairs(t) do c[k] = v end
	return c
end
table.find = function(t, value)
	for i = 1, #t do
		if t[i] == value then return i end
	end
	return nil
end
table.clear = function(t)
	for k in pairs(t) do t[k] = nil end
end
math.clamp = function(v, lo, hi)
	if v < lo then return lo end
	if v > hi then return hi end
	return v
end

-- ======================= virtual clock =======================
SIM = {}
local VTIME = 0
local BURNED = 0
COUNTERS = {
	taskYield = 0,
	lightingAccess = 0,
	lightingWrite = 0,
	heartbeats = 0,
	heartbeatConnections = 0,
	remoteFires = 0,
}
SIM.COUNTERS = COUNTERS
SIM.now = function() return VTIME end
SIM.VTIME = 0

-- One tick of virtual time. A cadence slot that wants to look expensive calls
-- SIM.burn(ms); it moves os.clock without moving the world, so the watchdog can be
-- tested without perturbing the very cycle the test is measuring.
function SIM.burn(ms)
	BURNED = BURNED + ms / 1000
end
os.clock = function()
	return VTIME + BURNED
end
os.time = function() return math.floor(VTIME) end
os.date = function() return "" end

LOGSTRING = ""
print = function(...)
	local buf = {}
	local n = select("#", ...)
	for i = 1, n do buf[i] = tostring(select(i, ...)) end
	LOGSTRING = LOGSTRING .. "print|" .. table.concat(buf, " ") .. "\\n"
end
warn = function(...)
	local buf = {}
	local n = select("#", ...)
	for i = 1, n do buf[i] = tostring(select(i, ...)) end
	LOGSTRING = LOGSTRING .. "warn|" .. table.concat(buf, " ") .. "\\n"
end

-- task: yielding is FORBIDDEN, not simulated. See the header.
task = {}
local function poisoned(what)
	return function()
		COUNTERS.taskYield = COUNTERS.taskYield + 1
		error("session harness: " .. what .. " is not allowed in the Vitriol server - the only driver is Bootstrap's Heartbeat binding (docs/01 §4)", 2)
	end
end
task.wait = poisoned("task.wait")
task.delay = poisoned("task.delay")
task.spawn = poisoned("task.spawn")
task.defer = poisoned("task.defer")
task.scheduling = function() return 0 end

-- ======================= signals =======================
local Signal = {}
Signal.__index = Signal
function Signal.new()
	return setmetatable({ list = {} }, Signal)
end
function Signal:Connect(fn)
	local entry = { fn = fn, dead = false }
	self.list[#self.list + 1] = entry
	return {
		Disconnect = function(conn)
			conn.Connected = false
			entry.dead = true
		end,
		Connected = true,
	}
end
Signal.connect = Signal.Connect
function Signal:Once(fn)
	local conn
	conn = self:Connect(function(...)
		conn:Disconnect()
		fn(...)
	end)
	return conn
end
function Signal:Fire(...)
	local list = self.list
	local n = #list
	local live = {}
	local m = 0
	for i = 1, n do
		local entry = list[i]
		if not entry.dead then
			m = m + 1
			live[m] = entry
		end
	end
	for i = 1, m do
		live[i].fn(...)
	end
end
Signal.fire = Signal.Fire
SIM.Signal = Signal

-- ======================= instances =======================
local Instance = {}
Instance.__index = Instance
local function newNode(className, name)
	return setmetatable({
		ClassName = className,
		Name = name or className,
		children = {},
		byName = {},
		props = {},
		attrs = {},
		attrSignals = {},
	}, Instance)
end

function Instance:FindFirstChild(name)
	return self.byName[name]
end
function Instance:FindFirstChildOfClass(className)
	local kids = self.children
	for i = 1, #kids do
		if kids[i].ClassName == className then
			return kids[i]
		end
	end
	return nil
end
function Instance:FindFirstChildWhichIsA(className)
	return self:FindFirstChildOfClass(className)
end
function Instance:GetChildren()
	return self.children
end
function Instance:GetDescendants()
	local out = {}
	local function walk(node)
		local kids = node.children
		for i = 1, #kids do
			out[#out + 1] = kids[i]
			walk(kids[i])
		end
	end
	walk(self)
	return out
end
function Instance:IsDescendantOf(ancestor)
	local p = self.props.Parent
	while p do
		if p == ancestor then return true end
		p = p.props.Parent
	end
	return false
end
function Instance:AddChild(child)
	child.props.Parent = self
	self.byName[child.Name] = child
	self.children[#self.children + 1] = child
	return child
end
function Instance:Destroy()
	local parent = self.props.Parent
	if parent then
		local kids = parent.children
		for i = 1, #kids do
			if kids[i] == self then
				table.remove(kids, i)
				break
			end
		end
		parent.byName[self.Name] = nil
	end
	self.props.Parent = nil
end
function Instance:ClearAllChildren()
	self.children = {}
	self.byName = {}
end
function Instance:SetAttribute(name, value)
	if self.attrs[name] == value then
		return
	end
	self.attrs[name] = value
	local signal = self.attrSignals[name]
	if signal then signal:Fire(value) end
end
function Instance:GetAttribute(name)
	return self.attrs[name]
end
function Instance:GetAttributes()
	return self.attrs
end
function Instance:GetAttributeChangedSignal(name)
	local signal = self.attrSignals[name]
	if signal == nil then
		signal = Signal.new()
		self.attrSignals[name] = signal
	end
	return signal
end
function Instance:CollectGarbage() end

-- The property layer: anything not a method or a child is a plain stored value.
-- Setting Parent goes through AddChild so the tree stays consistent in both
-- directions, exactly like the engine.
local function instanceIndex(inst, key)
	local method = Instance[key]
	if method ~= nil then return method end
	local child = inst.byName[key]
	if child ~= nil then return child end
	return inst.props[key]
end
local function instanceNewIndex(inst, key, value)
	if key == "Parent" then
		if value == nil then
			inst:Destroy()
		else
			value:AddChild(inst)
		end
		return
	end
	inst.props[key] = value
end
local makeRemote -- defined in the remotes section below; forward-declared because
-- Instance.new has to dispatch on class, which means it needs the remote factory
-- before that section is textually reached.
local function decorate(node)
	setmetatable(node, { __index = instanceIndex, __newindex = instanceNewIndex })
	return node
end
-- Instance.new has to know the class: Roblox hands back a RemoteEvent with an
-- OnServerEvent and a FireAllClients, and Shared/Remotes creates its whole surface
-- through Instance.new. A flat node there would fail for a reason that has nothing
-- to do with the code under test.
function SIM.createClass(className, name)
	if className == "RemoteEvent" or className == "RemoteFunction" then
		return makeRemote(className, name)
	end
	return decorate(newNode(className, name or className))
end
Instance.new = function(className)
	return SIM.createClass(className)
end
SIM.Instance = Instance
SIM.newNode = function(className, name)
	return decorate(newNode(className, name))
end

-- ======================= remotes =======================
makeRemote = function(className)
	local node = SIM.newNode(className, className)
	local remote = {
		OnServerEvent = Signal.new(),
		OnClientEvent = Signal.new(),
		FireAllClients = function(self, ...)
			COUNTERS.remoteFires = COUNTERS.remoteFires + 1
			self.lastAll = { ... }
			self.allCount = (self.allCount or 0) + 1
		end,
		FireClient = function(self, player, ...)
			COUNTERS.remoteFires = COUNTERS.remoteFires + 1
			self.clientCount = (self.clientCount or 0) + 1
			self.lastClient = { player = player, ... }
		end,
		FireClientReplay = function() end,
		SetSecurityCapabilities = function() end,
	}
	local handlers = {}
	local mt = getmetatable(node)
	setmetatable(node, {
		__index = function(inst, key)
			if remote[key] ~= nil then return remote[key] end
			if handlers[key] ~= nil then return handlers[key] end
			if key == "OnServerInvoke" or key == "OnClientInvoke" then
				return rawget(handlers, key)
			end
			return mt.__index(inst, key)
		end,
		__newindex = function(inst, key, value)
			if key == "OnServerInvoke" or key == "OnClientInvoke" then
				handlers[key] = value
				return
			end
			mt.__newindex(inst, key, value)
		end,
	})
	return node
end

-- ======================= services =======================
local SERVICES = {}
local function service(name, extra)
	local node = SIM.newNode(name, name)
	if extra then
		for key, value in pairs(extra) do
			rawset(node, key, value)
		end
	end
	SERVICES[name] = node
	return node
end

local RunService = service("RunService", {
	IsServer = function() return true end,
	IsClient = function() return false end,
	IsStudio = function() return true end,
	IsRunning = function() return true end,
	Heartbeat = Signal.new(),
	Stepped = Signal.new(),
	RenderStepped = Signal.new(),
})
-- Count Heartbeat connections so "the only loop in the server" is a measurement.
local realConnect = RunService.Heartbeat.Connect
RunService.Heartbeat.Connect = function(self, fn)
	COUNTERS.heartbeatConnections = COUNTERS.heartbeatConnections + 1
	return realConnect(self, fn)
end

local Workspace = service("Workspace", {
	DistributedGameTime = 0,
	Gravity = 196.2,
	Raycast = function() return nil end,
	GetPartBoundsInRadius = function() return {} end,
})
local Players = service("Players", { MaxPlayers = 4 })
Players.list = {}
Players.GetPlayers = function(self) return self.list end
Players.FindFirstPlayerByUserId = function(self, userId)
	for _, player in ipairs(self.list) do
		if player.UserId == userId then return player end
	end
	return nil
end
Players.addPlayer = function(self, name, userId)
	local player = SIM.newNode("Player", name)
	player.UserId = userId or (1000 + #self.list + 1)
	player.Character = nil
	local added = Signal.new()
	player.CharacterAdded = added
	player.leaderstats = SIM.newNode("Folder", "leaderstats")
	self.list[#self.list + 1] = player
	self.PlayerAdded:Fire(player)
	return player
end
Players.removePlayer = function(self, player)
	local list = self.list
	for i = 1, #list do
		if list[i] == player then
			table.remove(list, i)
			break
		end
	end
	self.PlayerRemoving:Fire(player)
end
Players.PlayerAdded = Signal.new()
Players.PlayerRemoving = Signal.new()

-- Lighting: a poisoned object. Reads are counted, writes are impossible.
local Lighting = setmetatable({}, {
	__index = function(_, key)
		COUNTERS.lightingAccess = COUNTERS.lightingAccess + 1
		if key == "GetProperties" then
			return function() return {} end
		end
		return nil
	end,
	__newindex = function(_, key)
		COUNTERS.lightingWrite = COUNTERS.lightingWrite + 1
		error("session harness: server code wrote Lighting." .. tostring(key) .. " (docs/01 §5.1 - clients own Lighting)", 2)
	end,
})

local CollectionService = service("CollectionService", {
	GetTagged = function() return {} end,
	HasTag = function() return false end,
	AddTag = function() end,
})
local PhysicsService = service("PhysicsService", {
	CreateCollisionGroup = function() end,
	SetCollisionGroupRegistered = function() end,
	RegisterCollisionGroup = function() end,
})
local LightingService = Lighting
SERVICES.Lighting = Lighting

local DataStoreService = service("DataStoreService", {})
DataStoreService.stores = {}
DataStoreService.GetDataStore = function(self, name)
	local store = self.stores[name]
	if store == nil then
		store = {
			data = {},
			GetAsync = function(_, key) return store.data[key] end,
			SetAsync = function(_, key, value) store.data[key] = value end,
			UpdateAsync = function(_, key, fn)
				local next = fn(store.data[key])
				store.data[key] = next
				return next
			end,
		}
		self.stores[name] = store
	end
	return store
end

game = setmetatable({
	GetService = function(_, name)
		if name == "Lighting" then
			-- Counted before it is returned: fetching Lighting from a server script is
			-- already the thing docs/06 §9.5 forbids, even if nothing writes through it.
			COUNTERS.lightingAccess = COUNTERS.lightingAccess + 1
			return Lighting
		end
		local existing = SERVICES[name]
		if existing ~= nil then return existing end
		return service(name)
	end,
}, {
	__index = function(_, key)
		if key == "Lighting" then
			COUNTERS.lightingAccess = COUNTERS.lightingAccess + 1
			return Lighting
		end
		return SERVICES[key]
	end,
})
SIM.game = game

-- Characters: enough Humanoid/Part surface that HealthService can be driven for real.
function SIM.makeCharacter(player)
	local character = SIM.newNode("Model", player.Name)
	local humanoid = SIM.newNode("Humanoid", "Humanoid")
	local root = SIM.newNode("Part", "HumanoidRootPart")
	root.Position = { X = 0, Y = 3, Z = 0 }
	humanoid.Health = 100
	humanoid.MaxHealth = 100
	humanoid.RootPart = root
	humanoid.HealthChanged = Signal.new()
	character:AddChild(humanoid)
	character:AddChild(root)
	player.Character = character
	player.CharacterAdded:Fire(character)
	return character, humanoid, root
end
function SIM.setRoot(character, x, y, z)
	local root = character:FindFirstChild("HumanoidRootPart")
	root.Position = { X = x, Y = y, Z = z }
end

-- ======================= the module loader =======================
-- Enums, Vector3 and CFrame are value shims: enough that a Part assignment or a
-- material lookup does not error. Where a service uses one (TimeAnchor's material) the
-- value is only stored and never read back, so a stub is honest here - and it is why
-- this harness does not pretend to test geometry.
local ENUM = setmetatable({}, {
	__index = function(_, kind)
		return setmetatable({}, {
			__index = function(_, key)
				return { Name = key, Value = 0 }
			end,
		})
	end,
})
local SOURCES = __SOURCES__
local TREE = __TREE__

-- Build the Explorer tree first, so 'ServerStorage.Vitriol.Bootstrap' resolves as an
-- Instance whether or not the module has been required.
local created = {}
local NODES = {}
SIM.NODES = NODES
local function ensureFolder(parts, upTo)
	if upTo < 1 then
		return nil
	end
	local key = table.concat(parts, "/", 1, upTo)
	local node = created[key]
	if node ~= nil then return node end
	if upTo == 1 then
		-- The top of every path is a SERVICE, not a Folder: ReplicatedStorage and
		-- ServerStorage have to be the very objects game:GetService hands back, or the
		-- tree the services index would not be the tree this harness built.
		node = SERVICES[parts[1]] or service(parts[1])
		created[key] = node
		return node
	end
	local parent = ensureFolder(parts, upTo - 1)
	node = SIM.newNode("Folder", parts[upTo])
	if parent then parent:AddChild(node) end
	created[key] = node
	return node
end
for _, entry in ipairs(TREE) do
	local parts = {}
	for part in string.gmatch(entry.path, "[^/]+") do
		parts[#parts + 1] = part
	end
	for i = 1, #parts - 1 do
		ensureFolder(parts, i)
	end
	local leafName = parts[#parts]
	local parent = ensureFolder(parts, #parts - 1)
	local node = SIM.newNode(entry.class, leafName)
	node.props.__path = entry.path
	if parent then parent:AddChild(node) end
	created[entry.path] = node
	NODES[entry.path] = node
end
function SIM.pathOf(name)
	for path, node in pairs(NODES) do
		local leaf = string.match(path, "([^/]+)$")
		if leaf == name then return node end
	end
	return nil
end

local LOADED = {}
SIM.LOADED = LOADED
function VitriolRequire(name)
	if LOADED[name] ~= nil then
		return LOADED[name]
	end
	local src = SOURCES[name]
	if src == nil then
		error("session harness: no module named " .. tostring(name) .. " under code/")
	end
	local env = {
		script = SIM.pathOf(name),
		game = game,
		Instance = Instance,
		Enum = ENUM,
		Vector3 = {
			new = function(x, y, z) return { X = x or 0, Y = y or 0, Z = z or 0 } end,
		},
		CFrame = {
			new = function(x, y, z) return { X = x or 0, Y = y or 0, Z = z or 0 } end,
			lookAt = function() return {} end,
		},
		Color3 = { new = function() return {} end, fromRGB = function() return {} end },
		DateTime = {
			now = function()
				return { ToUnixTimeMillis = function() return math.floor(VTIME * 1000) end }
			end,
		},
		print = print,
		warn = warn,
		error = error,
		pcall = pcall,
		xpcall = xpcall,
		select = select,
		type = type,
		tostring = tostring,
		tonumber = tonumber,
		setmetatable = setmetatable,
		getmetatable = getmetatable,
		rawget = rawget,
		rawset = rawset,
		next = next,
		table = table,
		string = string,
		math = math,
		os = os,
		task = task,
		buffer = buffer,
		typeof = function(v) return type(v) end,
		assert = assert,
		ipairs = ipairs,
		pairs = pairs,
		unpack = table.unpack,
		SIM = SIM,
		COUNTERS = COUNTERS,
		VitriolRequire = VitriolRequire,
	}
	-- Standard Lua globals are visible (pairs/ipairs/assert/string.*); the Vitriol
	-- surface is not, unless this file hands it over. That asymmetry is the point: a
	-- module that reaches for a Roblox global the harness never provided fails loudly
	-- instead of quietly running against a stub nobody wrote down.
	env.__index = _G
	local fn, err = load(src, "@" .. name, "t", env)
	if not fn then
		error("session harness: failed to compile " .. name .. ": " .. tostring(err))
	end
	LOADED[name] = true -- guard against a cycle before running
	local ok, result = pcall(fn)
	if not ok then
		LOADED[name] = nil
		error("session harness: " .. name .. " errored: " .. tostring(result))
	end
	LOADED[name] = result
	return result
end

-- ======================= the clock driver =======================
FRAME = 1 / 60
function SIM.step(seconds)
	local remaining = seconds
	local guard = 0
	while remaining > 1e-9 do
		guard = guard + 1
		if guard > 5e6 then
			error("SIM.step: refused to run " .. guard .. " frames for " .. seconds .. " s")
		end
		local dt = math.min(FRAME, remaining)
		VTIME = VTIME + dt
		SIM.VTIME = VTIME
		Workspace.DistributedGameTime = VTIME
		COUNTERS.heartbeats = COUNTERS.heartbeats + 1
		RunService.Heartbeat:Fire(dt)
		remaining = remaining - dt
	end
end
SIM.frame = function() SIM.step(FRAME) end
SIM.time = function() return VTIME end
SIM.Services = SERVICES
SIM.WORKSPACE = Workspace
SIM.PLAYERS = Players
`;

const parts = [
	SHIM.replace("__SOURCES__", (() => {
		const body = [];
		for (const [name, src] of Object.entries(sources)) {
			body.push(`  ${name} = ${luaLongString(1, src)},`);
		}
		return `{\n${body.join("\n")}\n}`;
	})()).replace("__TREE__", "{\n" + nodes.map((n) => `  { path = ${JSON.stringify(n.path)}, class = ${JSON.stringify(n.class)} }`).join(",\n") + "\n}"),
	scenario,
];
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
	fs.writeFileSync("/tmp/vitriol-session-bundle.lua", bundle);
	dumpLog();
	console.error("\nSESSION HARNESS CRASHED:\n" + err);
	process.exit(1);
}

dumpLog();

lua.lua_getglobal(L, to_luastring("SESSION"));
if (lua.lua_type(L, -1) !== lua.LUA_TTABLE) {
	console.error("session.luau did not define a SESSION table");
	process.exit(1);
}
lua.lua_getfield(L, -1, to_luastring("passed"));
const passed = lua.lua_tonumber(L, -1);
lua.lua_pop(L, 1);
lua.lua_getfield(L, -1, to_luastring("failed"));
const failed = lua.lua_tonumber(L, -1);
lua.lua_pop(L, 2);

console.log("");
console.log(`VITRIOL session test: ${passed} passed, ${failed} failed`);
process.exit(failed > 0 ? 1 : 0);
