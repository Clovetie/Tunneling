--!strict
-- FeatureFlags.lua — stage a system without editing code.
--
-- Flags live as **attributes on `ReplicatedStorage.FeatureFlags`**, so the server can
-- flip one at runtime and every client sees it through normal attribute replication —
-- no remote, no polling, no reload. A flag that has never been set resolves through the
-- default table, so a fresh place behaves exactly like the code says it does.
--
--   local Flags = require(path.FeatureFlags)
--   Flags.configure(require(path.FlagsConfig))      -- shared/, so both sides agree
--   if Flags.get("PartyPlay") then ... end
--   Flags.set("PartyPlay", false)                   -- server only; clients read
--   local conn = Flags.onChanged("PartyPlay", function(on) ... end)
--   conn:Disconnect()
--
-- Convention for a system that ships flags: put the defaults in `shared/FlagsConfig.lua`
-- as a plain `{ FlagName = boolean }` table and call `Flags.configure(...)` from the
-- system's server script. The kit never hardcodes a game's flags, and `configure` may be
-- called more than once (later calls win per key) so several systems can share the root.
--
-- Reading is safe before the folder exists and before it replicates: `get` falls back to
-- the defaults rather than yielding, so a flag can never stall a client's first frame.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")

export type FlagTable = { [string]: boolean }

local ROOT_NAME = "FeatureFlags"

local Flags = {}

local defaults: FlagTable = {}
local listeners: { [string]: { (boolean) -> () } } = {}
local root: Instance? = nil
local wired = false
local watching = false

-- Pure: the effective value of every flag, given a default table and the attributes a
-- root currently carries. Exposed so a spec can check precedence without a DataModel.
function Flags.resolve(defaultTable: FlagTable?, attributes: { [string]: any }?): FlagTable
	local out: FlagTable = {}
	-- `get` and `configure` both refuse an empty name, so `resolve` must too: an attribute
	-- named "" is reachable through GetAttributes and would otherwise appear in all().
	local function usable(name: any, value: any): boolean
		return type(name) == "string" and name ~= "" and type(value) == "boolean"
	end
	if type(defaultTable) == "table" then
		for name, value in pairs(defaultTable :: FlagTable) do
			if usable(name, value) then
				out[name :: string] = value :: boolean
			end
		end
	end
	if type(attributes) == "table" then
		for name, value in pairs(attributes :: { [string]: any }) do
			-- A non-boolean attribute is ignored rather than coerced: a string "false"
			-- that turned a feature *on* would be the worst possible failure here.
			if usable(name, value) then
				out[name :: string] = value :: boolean
			end
		end
	end
	return out
end

-- Reading never creates the root: a place that never sets a flag should not grow a
-- Folder just because something asked what the flags are.
local function readRoot(): Instance?
	if root and root.Parent then
		return root
	end
	local found = ReplicatedStorage:FindFirstChild(ROOT_NAME)
	root = found
	return found
end

local function useRoot(): Instance?
	local found = readRoot()
	if found then
		return found
	end
	if not RunService:IsServer() then
		return nil
	end
	local folder = Instance.new("Folder")
	folder.Name = ROOT_NAME
	folder.Parent = ReplicatedStorage
	root = folder
	return folder
end

local function dispatch(name: string)
	local fns = listeners[name]
	if not fns then
		return
	end
	local value = Flags.get(name)
	for _, fn in ipairs(fns) do
		-- One bad listener must not stop the others from hearing about the change.
		local ok, err = pcall(fn, value)
		if not ok then
			warn(("FeatureFlags: listener for %q failed: %s"):format(name, tostring(err)))
		end
	end
end

local function wireRoot(holder: Instance)
	root = holder
	wired = true
	holder.AttributeChanged:Connect(function(name: string)
		dispatch(name)
	end)
	-- Attributes that were already set when the root appeared never fire AttributeChanged,
	-- so a listener registered before replication would miss them. Prime once instead.
	for name in pairs(listeners) do
		dispatch(name)
	end
end

local function ensureWired()
	if wired then
		return
	end
	local holder = ReplicatedStorage:FindFirstChild(ROOT_NAME)
	if holder then
		wireRoot(holder)
		return
	end
	if RunService:IsServer() or watching then
		return
	end
	-- Client, folder not replicated yet: attach as soon as it appears so listeners still
	-- fire. `get` never waits on this — it falls back to the defaults meanwhile.
	watching = true
	task.spawn(function()
		local appeared = ReplicatedStorage:WaitForChild(ROOT_NAME, 30)
		watching = false
		if appeared and not wired then
			wireRoot(appeared)
		end
	end)
end

function Flags.configure(defaultTable: FlagTable?): number
	if type(defaultTable) ~= "table" then
		return 0
	end
	local applied = 0
	for name, value in pairs(defaultTable :: FlagTable) do
		if type(name) == "string" and name ~= "" and type(value) == "boolean" then
			defaults[name] = value
			applied += 1
		end
	end
	return applied
end

function Flags.get(name: string): boolean
	if type(name) ~= "string" or name == "" then
		return false
	end
	local holder = readRoot()
	if holder then
		local value = holder:GetAttribute(name)
		if type(value) == "boolean" then
			return value
		end
	end
	local fallback = defaults[name]
	return if type(fallback) == "boolean" then fallback else false
end

function Flags.set(name: string, value: boolean): boolean
	if not RunService:IsServer() then
		warn("FeatureFlags.set is server-authoritative; clients read the replicated attribute")
		return false
	end
	if type(name) ~= "string" or name == "" or type(value) ~= "boolean" then
		return false
	end
	local holder = useRoot()
	if not holder then
		return false
	end
	holder:SetAttribute(name, value)
	return true
end

function Flags.onChanged(name: string, fn: (boolean) -> ()): { Disconnect: (any) -> () }
	assert(type(name) == "string" and name ~= "", "FeatureFlags.onChanged needs a flag name")
	assert(type(fn) == "function", "FeatureFlags.onChanged needs a callback")
	local list = listeners[name]
	if not list then
		list = {}
		listeners[name] = list
	end
	table.insert(list, fn)
	ensureWired()
	local disconnected = false
	return {
		Disconnect = function()
			if disconnected then
				return
			end
			disconnected = true
			for index, existing in ipairs(list) do
				if existing == fn then
					table.remove(list, index)
					break
				end
			end
		end,
	}
end

function Flags.all(): FlagTable
	local attributes: { [string]: any } = {}
	local holder = readRoot()
	if holder then
		for name, value in pairs(holder:GetAttributes()) do
			attributes[name] = value
		end
	end
	return Flags.resolve(defaults, attributes)
end

function Flags.rootName(): string
	return ROOT_NAME
end

return Flags
