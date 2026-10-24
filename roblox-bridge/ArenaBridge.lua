--!nonstrict
--[[
	ArenaBridge v4.0.0 — Roblox Studio local MCP & bridge plugin
	Connects your open Studio place (Edit, Server, and Client DataModels) to the
	bridge & Model Context Protocol (MCP) server running on your machine.

	Install:
	  1. Run setup on your computer:  python3 setup.py   (or --tunnel / --install-only)
	  2. Or start the MCP server via Claude Code / Cursor / Claude Desktop / Codex
	     (mcp_server.py auto-starts or attaches to the local bridge automatically).
	  3. Restart Studio once when installing or rotating tokens.
	     v2+/v3+ auto-connects on DataModel load — no toolbar click needed.

	Key v4.0.0 upgrades:
	  - Multi-DataModel context routing: identifies Edit vs Play Server peers cleanly,
	    eliminating Edit/Server race conditions during playtests.
	  - Silent Client-Peer RPC bridge (__ArenaBridgeClientRPC): Client DataModels no
	    longer fail with HTTP errors during Play Solo; client-side Luau, GUI, camera,
	    and console inspections proxy transparently through the Server peer.
	  - Editor-safe script reads & writes via ScriptEditorService:GetEditorSource() and
	    ScriptEditorService:UpdateSourceAsync() with CRLF normalization (>200 KB safe,
	    preserves open Script Editor tabs), optimistic FNV-1a hash locks, atomic
	    multi_edit, and replaceInstance / require_fresh cache busting.
	  - Rich MCP tool handlers: search_tree, compare_instances, script_search,
	    script_grep, find_replace_scripts, get_properties, mass_set_properties,
	    clone_instance, reparent_instance, attributes, tags, camera, history (undo/redo),
	    terrain, insert_asset (with backdoor script scan/strip), and StudioTestService
	    non-blocking playtest control.
	  - Every Edit-mode mutation is wrapped in a single ChangeHistoryService recording
	    so one Ctrl+Z undoes the entire operation.
]]

------------------------------------------------------------------------------
-- CONFIG (keep exact assignment shape on the next two lines for setup.py)
------------------------------------------------------------------------------
local BRIDGE_URL = "http://127.0.0.1:8077"  -- the bridge on YOUR machine
local BRIDGE_TOKEN = "arena-demo-7f3a"      -- must match the server's --token
local POLL_INTERVAL = 0.5                   -- idle pause between long-polls
local AUTO_CONNECT = true                   -- connect on load if bridge is up
local PLUGIN_VERSION = "4.0.0"
local CLIENT_RPC_NAME = "__ArenaBridgeClientRPC"
------------------------------------------------------------------------------

local HttpService = game:GetService("HttpService")
local ChangeHistoryService = game:GetService("ChangeHistoryService")
local CollectionService = game:GetService("CollectionService")
local InsertService = game:GetService("InsertService")
local LogService = game:GetService("LogService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local Selection = game:GetService("Selection")
local ServerStorage = game:GetService("ServerStorage")

local okSES, ScriptEditorService = pcall(function()
	return game:GetService("ScriptEditorService")
end)
local okSTS, StudioTestService = pcall(function()
	return game:GetService("StudioTestService")
end)
local okStats, StatsService = pcall(function()
	return game:GetService("Stats")
end)

------------------------------------------------------------------------------
-- DataModel identity (Edit vs Play Server vs Play Client)
------------------------------------------------------------------------------

local function detectDataModelMode(): string
	local okEdit, isEdit = pcall(function()
		return RunService:IsEdit()
	end)
	if okEdit and isEdit then
		return "edit"
	end
	local okSrv, isSrv = pcall(function()
		return RunService:IsServer()
	end)
	if okSrv and isSrv then
		return "server"
	end
	return "client"
end

local DM_MODE = detectDataModelMode()
local okGuid, rawGuid = pcall(function()
	return HttpService:GenerateGUID(false)
end)
local PEER_ID = DM_MODE .. "-" .. string.sub(okGuid and rawGuid or tostring(math.random(100000, 999999)), 1, 8)

-- Persisted settings override baked defaults when present
BRIDGE_URL = plugin:GetSetting("ArenaBridgeUrl") or BRIDGE_URL
BRIDGE_TOKEN = plugin:GetSetting("ArenaBridgeToken") or BRIDGE_TOKEN

local toolbar = nil
local button = nil
if DM_MODE == "edit" then
	toolbar = plugin:CreateToolbar("Arena")
	button = toolbar:CreateButton(
		"ArenaBridge",
		"Connect this place to your Arena / MCP agent (v4.0.0)",
		"",
		"Arena MCP"
	)
	button.ClickableWhenViewportHidden = true
end

local running = false
local logs: { string } = {}
local activePlaytestRunning = false
local lastPlaytestResult: any = nil
local playtestDiedReason: string? = nil
local clientRpc: RemoteFunction? = nil

local function log(fmt: string, ...: any)
	local msg = select("#", ...) > 0 and string.format(fmt, ...) or fmt
	table.insert(logs, msg)
	print("[Arena] " .. msg)
end

------------------------------------------------------------------------------
-- Exact 32-bit FNV-1a hash (decomposed multiply < 2^41 + nibble XOR table)
-- Matches Python fnv1a_32 bit-for-bit without exceeding IEEE-754 53-bit exact
-- integer range or requiring post-2022 Luau bitwise operators.
------------------------------------------------------------------------------

local X4 = {
	0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
	1, 0, 3, 2, 5, 4, 7, 6, 9, 8, 11, 10, 13, 12, 15, 14,
	2, 3, 0, 1, 6, 7, 4, 5, 10, 11, 8, 9, 14, 15, 12, 13,
	3, 2, 1, 0, 7, 6, 5, 4, 11, 10, 9, 8, 15, 14, 13, 12,
	4, 5, 6, 7, 0, 1, 2, 3, 12, 13, 14, 15, 8, 9, 10, 11,
	5, 4, 7, 6, 1, 0, 3, 2, 13, 12, 15, 14, 9, 8, 11, 10,
	6, 7, 4, 5, 2, 3, 0, 1, 14, 15, 12, 13, 10, 11, 8, 9,
	7, 6, 5, 4, 3, 2, 1, 0, 15, 14, 13, 12, 11, 10, 9, 8,
	8, 9, 10, 11, 12, 13, 14, 15, 0, 1, 2, 3, 4, 5, 6, 7,
	9, 8, 11, 10, 13, 12, 15, 14, 1, 0, 3, 2, 5, 4, 7, 6,
	10, 11, 8, 9, 14, 15, 12, 13, 2, 3, 0, 1, 6, 7, 4, 5,
	11, 10, 9, 8, 15, 14, 13, 12, 3, 2, 1, 0, 7, 6, 5, 4,
	12, 13, 14, 15, 8, 9, 10, 11, 4, 5, 6, 7, 0, 1, 2, 3,
	13, 12, 15, 14, 9, 8, 11, 10, 5, 4, 7, 6, 1, 0, 3, 2,
	14, 15, 12, 13, 10, 11, 8, 9, 6, 7, 4, 5, 2, 3, 0, 1,
	15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0,
}

local function xor8(a: number, b: number): number
	local hi = X4[math.floor(a / 16) * 16 + math.floor(b / 16) + 1]
	local lo = X4[(a % 16) * 16 + (b % 16) + 1]
	return hi * 16 + lo
end

local function fnv32(s: string): number
	local h = 2166136261
	for i = 1, #s do
		local lo = h % 256
		h = h - lo + xor8(lo, string.byte(s, i))
		h = (h * 147 + h * 256 + (h % 256) * 16777216) % 4294967296
	end
	return h
end

local function fnvHex(s: string): string
	return string.format("%08x", fnv32(s))
end

------------------------------------------------------------------------------
-- Instance path helpers:
-- Supports "game.Workspace.Folder.Part", "Workspace.Part", "workspace.Part",
-- "ServerScriptService/NightLoop/Config", and bracketed segments.
------------------------------------------------------------------------------

local function splitPath(path: string): { string }
	local normalized = string.gsub(path, "/", ".")
	normalized = string.gsub(normalized, "%[\"(.-)\"%]", ".%1")
	normalized = string.gsub(normalized, "%['(.-)'%]", ".%1")
	local parts = {}
	for seg in string.gmatch(normalized, "[^%.]+") do
		table.insert(parts, seg)
	end
	return parts
end

local function resolve(path: string?): Instance?
	if not path or path == "" or path == "game" then
		return game
	end
	if path == "workspace" or path == "Workspace" then
		return workspace
	end
	local segments = splitPath(path)
	local node: Instance = game
	for idx, segment in ipairs(segments) do
		if idx == 1 and segment == "game" then
			node = game
		elseif node == game and (segment == "workspace" or segment == "Workspace") then
			node = workspace
		else
			local ok, child = pcall(function()
				return (node :: any):FindFirstChild(segment)
			end)
			if (not ok or not child) and node == game then
				local okSvc, svc = pcall(function()
					return game:GetService(segment :: any)
				end)
				if okSvc and svc then
					child = svc
				end
			end
			if not child then
				return nil
			end
			node = child
		end
	end
	return node
end

local function mustResolve(path: string?): Instance
	local inst = resolve(path)
	if not inst then
		error(string.format("path not found: %s", tostring(path)), 0)
	end
	return inst
end

local function fullName(inst: Instance): string
	if inst == game then
		return "game"
	end
	local ok, name = pcall(function()
		return inst:GetFullName()
	end)
	return ok and name or inst.Name
end

------------------------------------------------------------------------------
-- Editor-safe script read & write helpers (ScriptEditorService + normalization)
------------------------------------------------------------------------------

local function normalizeNewlines(s: string): string
	local clean = string.gsub(s, "\r\n", "\n")
	clean = string.gsub(clean, "\r", "\n")
	return clean
end

local function readScriptSource(inst: Instance): string
	if okSES and ScriptEditorService then
		local okEd, edSrc = pcall(function()
			return ScriptEditorService:GetEditorSource(inst :: any)
		end)
		if okEd and type(edSrc) == "string" then
			return edSrc
		end
	end
	local okSrc, src = pcall(function()
		return (inst :: any).Source
	end)
	return okSrc and type(src) == "string" and src or ""
end

local function writeScriptSource(inst: Instance, rawSource: string)
	local clean = normalizeNewlines(rawSource or "")
	if okSES and ScriptEditorService and inst.Parent ~= nil then
		local okUpd, updErr = pcall(function()
			ScriptEditorService:UpdateSourceAsync(inst :: any, function(_oldContent)
				return clean
			end)
		end)
		if okUpd then
			return
		end
		log("  note: UpdateSourceAsync fallback to .Source on %s (%s)", inst.Name, tostring(updErr))
	end
	(inst :: any).Source = clean
end

local function replaceScriptInstance(inst: Instance, newSource: string): Instance
	local parent = inst.Parent
	local cls = inst.ClassName
	local name = inst.Name
	local attrs = inst:GetAttributes()
	local tags = CollectionService:GetTags(inst)

	local replacement = Instance.new(cls)
	replacement.Name = name
	if inst:IsA("BaseScript") then
		pcall(function()
			(replacement :: any).Disabled = (inst :: any).Disabled
		end)
		pcall(function()
			(replacement :: any).RunContext = (inst :: any).RunContext
		end)
	end
	for k, v in pairs(attrs) do
		pcall(function()
			replacement:SetAttribute(k, v)
		end)
	end
	for _, child in ipairs(inst:GetChildren()) do
		child.Parent = replacement
	end
	inst:Destroy()
	replacement.Parent = parent
	writeScriptSource(replacement, newSource)
	for _, tag in ipairs(tags) do
		pcall(function()
			CollectionService:AddTag(replacement, tag)
		end)
	end
	return replacement
end

------------------------------------------------------------------------------
-- Property coercion & serialization (JSON <-> Roblox datatypes)
------------------------------------------------------------------------------

local function parseHexColor(hex: string): Color3?
	local clean = string.gsub(hex, "^#", "")
	if #clean == 6 then
		local r = tonumber(string.sub(clean, 1, 2), 16)
		local g = tonumber(string.sub(clean, 3, 4), 16)
		local b = tonumber(string.sub(clean, 5, 6), 16)
		if r and g and b then
			return Color3.fromRGB(r, g, b)
		end
	end
	return nil
end

local function coerce(value: any): any
	if typeof(value) == "string" and string.sub(value, 1, 1) == "#" and #value == 7 then
		local col = parseHexColor(value)
		if col then
			return col
		end
	end
	if typeof(value) ~= "table" then
		return value
	end
	local tag = value.__t
	local v = value.v
	if tag == "Vector3" then
		return Vector3.new(v[1], v[2], v[3])
	elseif tag == "Vector2" then
		return Vector2.new(v[1], v[2])
	elseif tag == "Color3" then
		if type(v) == "string" then
			local parsed = parseHexColor(v)
			if not parsed then error("invalid Color3 hex value: " .. v, 0) end
			return parsed
		end
		if type(v) ~= "table" or #v ~= 3 then error("Color3 requires a hex string or three numeric components", 0) end
		for i = 1, 3 do
			if type(v[i]) ~= "number" or v[i] ~= v[i] or v[i] < 0 or v[i] > 1 then
				error("Color3 components must be finite normalized numbers in [0, 1]", 0)
			end
		end
		return Color3.new(v[1], v[2], v[3])
	elseif tag == "Color3RGB" then
		return Color3.fromRGB(v[1], v[2], v[3])
	elseif tag == "BrickColor" then
		return BrickColor.new(v)
	elseif tag == "CFrame" then
		if type(v) == "table" and v.position then
			local pos = Vector3.new(v.position[1], v.position[2], v.position[3])
			if v.lookAt then
				return CFrame.lookAt(pos, Vector3.new(v.lookAt[1], v.lookAt[2], v.lookAt[3]))
			elseif v.angles then
				return CFrame.new(pos)
					* CFrame.Angles(math.rad(v.angles[1]), math.rad(v.angles[2]), math.rad(v.angles[3]))
			end
			return CFrame.new(pos)
		end
		if #v >= 12 then
			return CFrame.new(table.unpack(v))
		end
		return CFrame.new(v[1], v[2], v[3])
	elseif tag == "UDim2" then
		return UDim2.new(v[1], v[2], v[3], v[4])
	elseif tag == "UDim" then
		return UDim.new(v[1], v[2])
	elseif tag == "NumberRange" then
		if type(v) == "number" then
			return NumberRange.new(v)
		end
		return NumberRange.new(v[1], v[2])
	elseif tag == "NumberSequence" then
		if type(v) == "number" then
			return NumberSequence.new(v)
		end
		return NumberSequence.new(v[1], v[2])
	elseif tag == "Rect" then
		return Rect.new(v[1], v[2], v[3], v[4])
	elseif tag == "PhysicalProperties" then
		return PhysicalProperties.new(v[1], v[2], v[3], v[4] or 1, v[5] or 1)
	elseif tag == "Enum" then
		local cleaned = string.gsub(tostring(v), "^Enum%.", "")
		local enumName, itemName = string.match(cleaned, "^([%w_]+)%.([%w_]+)$")
		if not enumName or not itemName then error("invalid Enum value: " .. tostring(v), 0) end
		local enumType = (Enum :: any)[enumName]
		if not enumType then error("unknown Enum type: " .. enumName, 0) end
		local item = enumType[itemName]
		if not item then error("unknown Enum item: " .. cleaned, 0) end
		return item
	elseif tag == "Instance" then
		local inst = resolve(v)
		if not inst then error("Instance reference did not resolve: " .. tostring(v), 0) end
		return inst
	elseif tag ~= nil then
		error("unsupported property coercion tag: " .. tostring(tag), 0)
	end
	return value
end

local function serializeValue(val: any): any
	local t = typeof(val)
	if t == "nil" or t == "boolean" or t == "number" or t == "string" then
		if t == "number" and (val ~= val or val == math.huge or val == -math.huge) then
			return tostring(val)
		end
		return val
	elseif t == "Vector3" then
		return { __t = "Vector3", v = { val.X, val.Y, val.Z } }
	elseif t == "Vector2" then
		return { __t = "Vector2", v = { val.X, val.Y } }
	elseif t == "Color3" then
		return { __t = "Color3", v = { val.R, val.G, val.B } }
	elseif t == "BrickColor" then
		return { __t = "BrickColor", v = val.Name }
	elseif t == "UDim2" then
		return { __t = "UDim2", v = { val.X.Scale, val.X.Offset, val.Y.Scale, val.Y.Offset } }
	elseif t == "UDim" then
		return { __t = "UDim", v = { val.Scale, val.Offset } }
	elseif t == "CFrame" then
		local x, y, z, r00, r01, r02, r10, r11, r12, r20, r21, r22 = val:GetComponents()
		return { __t = "CFrame", v = { x, y, z, r00, r01, r02, r10, r11, r12, r20, r21, r22 } }
	elseif t == "EnumItem" then
		return { __t = "Enum", v = tostring(val) }
	elseif t == "NumberRange" then
		return { __t = "NumberRange", v = { val.Min, val.Max } }
	elseif t == "Instance" then
		return { __t = "Instance", v = fullName(val) }
	elseif t == "table" then
		local copy = {}
		local count = 0
		for k, sub in pairs(val) do
			count += 1
			if count > 200 then
				break
			end
			copy[tostring(k)] = serializeValue(sub)
		end
		return copy
	end
	return tostring(val)
end

local function applyProps(inst: Instance, props: { [string]: any }?): ({ string }, { [string]: string })
	local applied = {}
	local failed: { [string]: string } = {}
	if not props then
		return applied, failed
	end
	for key, raw in pairs(props) do
		local ok, err = pcall(function()
			local coerced = coerce(raw)
			-- Auto-convert plain 3-element arrays for Vector3/Color3 properties
			if type(coerced) == "table" and coerced.__t == nil then
				local okCur, curVal = pcall(function()
					return (inst :: any)[key]
				end)
				if okCur then
					local ct = typeof(curVal)
					if ct == "Vector3" and #coerced == 3 then
						coerced = Vector3.new(coerced[1], coerced[2], coerced[3])
					elseif ct == "Vector2" and #coerced == 2 then
						coerced = Vector2.new(coerced[1], coerced[2])
					elseif ct == "Color3" and #coerced == 3 then
						local mx = math.max(coerced[1], coerced[2], coerced[3])
						coerced = mx > 1 and Color3.fromRGB(coerced[1], coerced[2], coerced[3]) or Color3.new(coerced[1], coerced[2], coerced[3])
					elseif ct == "UDim2" and #coerced == 4 then
						coerced = UDim2.new(coerced[1], coerced[2], coerced[3], coerced[4])
					elseif ct == "CFrame" and #coerced == 3 then
						coerced = CFrame.new(coerced[1], coerced[2], coerced[3])
					elseif ct == "CFrame" and #coerced >= 12 then
						coerced = CFrame.new(table.unpack(coerced))
					elseif ct == "NumberRange" and (#coerced == 1 or #coerced == 2) then
						coerced = #coerced == 1 and NumberRange.new(coerced[1]) or NumberRange.new(coerced[1], coerced[2])
					elseif ct == "Rect" and #coerced == 4 then
						coerced = Rect.new(coerced[1], coerced[2], coerced[3], coerced[4])
					end
				end
			elseif type(coerced) == "string" then
				local okCur, curVal = pcall(function()
					return (inst :: any)[key]
				end)
				local numbers = {}
				for token in string.gmatch(coerced, "[^,]+") do
					local n = tonumber(string.gsub(token, "^%s*(.-)%s*$", "%1"))
					if n == nil then numbers = {}; break end
					table.insert(numbers, n)
				end
				if okCur and typeof(curVal) == "Vector3" and #numbers == 3 then
					coerced = Vector3.new(numbers[1], numbers[2], numbers[3])
				elseif okCur and typeof(curVal) == "Vector2" and #numbers == 2 then
					coerced = Vector2.new(numbers[1], numbers[2])
				elseif okCur and typeof(curVal) == "Color3" and #numbers == 3 then
					local mx = math.max(numbers[1], numbers[2], numbers[3])
					coerced = mx > 1 and Color3.fromRGB(numbers[1], numbers[2], numbers[3]) or Color3.new(numbers[1], numbers[2], numbers[3])
				elseif okCur and typeof(curVal) == "UDim2" and #numbers == 4 then
					coerced = UDim2.new(numbers[1], numbers[2], numbers[3], numbers[4])
				elseif okCur and typeof(curVal) == "CFrame" and (#numbers == 3 or #numbers >= 12) then
					coerced = #numbers >= 12 and CFrame.new(table.unpack(numbers)) or CFrame.new(numbers[1], numbers[2], numbers[3])
				elseif okCur and typeof(curVal) == "NumberRange" and (#numbers == 1 or #numbers == 2) then
					coerced = #numbers == 1 and NumberRange.new(numbers[1]) or NumberRange.new(numbers[1], numbers[2])
				elseif okCur and typeof(curVal) == "Rect" and #numbers == 4 then
					coerced = Rect.new(numbers[1], numbers[2], numbers[3], numbers[4])
				end
				if okCur and typeof(curVal) == "EnumItem" then
					local enumType = curVal.EnumType
					local itemName = string.match(coerced, "([%w_]+)$")
					if itemName and (enumType :: any)[itemName] then
						coerced = (enumType :: any)[itemName]
					end
				end
			end
			(inst :: any)[key] = coerced
		end)
		if ok then
			table.insert(applied, key)
		else
			failed[key] = tostring(err)
			log("  ! could not set %s.%s (%s)", inst.Name, key, tostring(err))
		end
	end
	return applied, failed
end

------------------------------------------------------------------------------
-- Build a tree of instances from a JSON spec
------------------------------------------------------------------------------

local function buildNode(spec: any, parent: Instance, replaceQueue: { Instance }): Instance
	local className = spec.className or spec.class or "Part"	local existingToReplace: Instance? = nil
	if spec.replaceExisting and spec.name then
		existingToReplace = parent:FindFirstChild(spec.name)
	end

	local inst = Instance.new(className)
	if spec.name then inst.Name = spec.name end
	local okBuild, buildErr = pcall(function()
		local _, failedProps = applyProps(inst, spec.properties)
		if next(failedProps) ~= nil then
			error("build node " .. tostring(spec.name or className) .. " has invalid properties: " .. HttpService:JSONEncode(failedProps), 0)
		end

		if spec.attributes then
			for k, v in pairs(spec.attributes) do
				inst:SetAttribute(k, coerce(v))
			end
		end

		inst.Parent = parent
		if spec.source and inst:IsA("LuaSourceContainer") then
			writeScriptSource(inst, spec.source)
		end
		if spec.tags then
			for _, tag in ipairs(spec.tags) do
				CollectionService:AddTag(inst, tostring(tag))
			end
		end
		for _, childSpec in ipairs(spec.children or {}) do
			buildNode(childSpec, inst, replaceQueue)
		end
	end)
	if not okBuild then
		pcall(function() inst:Destroy() end)
		error("build node " .. tostring(spec.name or className) .. " failed: " .. tostring(buildErr), 0)
	end
	if existingToReplace then
		table.insert(replaceQueue, existingToReplace)
	end
	return inst
end

------------------------------------------------------------------------------
-- Luau execution inside Studio (loadstring preferred; temp ModuleScript fallback)
------------------------------------------------------------------------------

local function executeLuau(code: string): any
	local chunk, compileErr
	if type(loadstring) == "function" then
		chunk, compileErr = (loadstring :: any)(code, "ArenaBridgeJob")
	end

	if chunk then
		local results = table.pack(pcall(chunk))
		if not results[1] then
			error(tostring(results[2]), 0)
		end
		return results[2]
	end

	local module = Instance.new("ModuleScript")
	module.Name = "ArenaBridgeJob_" .. tostring(math.random(1000000, 9999999))
	module.Source = "return function()\n" .. code .. "\nend"
	module.Archivable = false
	module.Parent = ServerStorage

	local okReq, fnOrErr = pcall(require, module)
	if not okReq then
		module:Destroy()
		error("could not execute code (" .. tostring(compileErr or fnOrErr) .. ")", 0)
	end
	local okRun, result = pcall(fnOrErr :: any)
	module:Destroy()
	if not okRun then
		error(tostring(result), 0)
	end
	return result
end

local function executePlayProbe(code: string, playerName: string?, timeout: number?): any
	local nonce = string.gsub(tostring(os.clock()) .. tostring(math.random(100000, 999999)), "[^%w]", "")
	local marker = "[[ARENA_PROBE:" .. nonce .. "]]"
	local source = table.concat({
		"local HttpService = game:GetService('HttpService')",
		"local function runUserCode()",
		code,
		"end",
		"local okRun, value = pcall(runUserCode)",
		"local payload = {ok=okRun, result=value}",
		"local okJson, encoded = pcall(function() return HttpService:JSONEncode(payload) end)",
		"if not okJson then encoded = HttpService:JSONEncode({ok=false, error='result_not_json: ' .. tostring(value)}) end",
		"print(" .. string.format("%q", marker) .. " .. encoded)",
		"script:Destroy()",
	}, "\n")

	local target: Instance
	if DM_MODE == "server" then
		target = game:GetService("ServerScriptService")
	else
		local selected = playerName and Players:FindFirstChild(playerName) or Players:GetPlayers()[1]
		if not selected then error("no_player: client probe requires a joined Player", 0) end
		target = selected:FindFirstChildOfClass("PlayerGui") :: Instance
		if not target then error("no_player_gui: client probe cannot find PlayerGui", 0) end
	end

	local probe = Instance.new(if DM_MODE == "server" then "Script" else "LocalScript")
	probe.Name = "__ArenaProbe_" .. nonce
	local created = false
	local okCreate, createErr = pcall(function()
		(probe :: any).Source = source
		probe.Parent = target
		created = true
	end)
	if not okCreate then
		probe:Destroy()
		error("probe_injection_failed: " .. tostring(createErr), 0)
	end

	local deadline = os.clock() + math.clamp(tonumber(timeout) or 30, 1, 120)
	local result = nil
	local history = nil
	while os.clock() < deadline do
		local okRead, rows = pcall(function() return LogService:GetLogHistory() end)
		if okRead and rows then history = rows end
		for _, entry in ipairs(history or {}) do
			local msg = tostring(entry.message or "")
			local at = string.find(msg, marker, 1, true)
			if at then
				local encoded = string.sub(msg, at + #marker)
				local okDecode, parsed = pcall(function() return HttpService:JSONDecode(encoded) end)
				if okDecode then result = parsed end
				break
			end
		end
		if result then break end
		task.wait(0.05)
	end
	if created then pcall(function() probe:Destroy() end) end
	if not result then error("probe_timeout: timed out waiting for runtime result; probe was cleaned up", 0) end
	if result.ok ~= true then error(tostring(result.error or "play probe failed"), 0) end
	return result
end

------------------------------------------------------------------------------
-- Describe the data model for the agent
------------------------------------------------------------------------------

local INTERESTING = {
	"ClassName", "Name", "Archivable", "Size", "Position", "Orientation",
	"CFrame", "Color", "BrickColor", "Material", "MaterialVariant",
	"Transparency", "Reflectance", "Anchored", "CanCollide", "CanTouch",
	"CanQuery", "Massless", "CastShadow", "Shape", "MeshId", "TextureID",
	"SoundId", "Volume", "PlaybackSpeed", "Looped", "Playing",
	"Brightness", "Range", "Angle", "Shadows",
	"Visible", "Active", "AnchorPoint", "ZIndex", "LayoutOrder",
	"AbsolutePosition", "AbsoluteSize", "ScreenInsets", "IgnoreGuiInset", "AutomaticSize", "ClipsDescendants",
	"Text", "TextColor3", "TextSize", "BackgroundColor3", "BackgroundTransparency",
	"Image", "ImageColor3", "ImageTransparency",
	"Enabled", "Disabled", "RunContext", "Value", "MaxHealth", "Health",
	"WalkSpeed", "JumpPower", "JumpHeight", "DisplayName",
	"ClockTime", "Ambient", "OutdoorAmbient", "FogEnd", "FogStart", "FogColor",
}

local function readInstanceProperties(inst: Instance, requested: { string }?): ({ [string]: string }, { [string]: any })
	local propStrings: { [string]: string } = {}
	local structured: { [string]: any } = {}
	local keys = requested or INTERESTING
	local seen: { [string]: boolean } = {}
	for _, key in ipairs(keys) do
		if not seen[key] and key ~= "Name" and key ~= "ClassName" then
			seen[key] = true
			local ok, value = pcall(function()
				return (inst :: any)[key]
			end)
			if ok and value ~= nil then
				propStrings[key] = tostring(value)
				structured[key] = serializeValue(value)
			end
		end
	end
	return propStrings, structured
end

local function readAttributesMap(inst: Instance): { [string]: any }
	local out: { [string]: any } = {}
	local ok, attrs = pcall(function()
		return inst:GetAttributes()
	end)
	if ok and attrs then
		for k, v in pairs(attrs) do
			out[k] = serializeValue(v)
		end
	end
	return out
end

local function describe(inst: Instance, depth: number, maxChildren: number?, includeStructured: boolean?, structuredDescendants: boolean?): any
	local childCap = maxChildren or 100
	local node: any = {
		name = inst.Name,
		className = inst.ClassName,
		path = fullName(inst),
	}
	local propStrings, structured = readInstanceProperties(inst, nil)
	if next(propStrings) then
		node.properties = propStrings
		if includeStructured then
			node.structuredProperties = structured
		end
	end
	local attrs = readAttributesMap(inst)
	if next(attrs) then
		node.attributes = attrs
	end
	local okTags, instTags = pcall(function()
		return CollectionService:GetTags(inst)
	end)
	if okTags and instTags and #instTags > 0 then
		node.tags = instTags
	end
	if inst:IsA("LuaSourceContainer") then
		local src = readScriptSource(inst)
		local _, newlines = string.gsub(src, "\n", "\n")
		node.sourceLines = newlines + 1
		node.sourceBytes = #src
		node.hash = fnvHex(src)
	end
	local allChildren = inst:GetChildren()
	node.childCount = #allChildren
	if depth > 0 then
		local kids = {}
		for _, child in ipairs(allChildren) do
			table.insert(kids, describe(child, depth - 1, childCap, structuredDescendants == true, structuredDescendants == true))
			if #kids >= childCap then
				node.childrenTruncated = true
				break
			end
		end
		if #kids > 0 then
			node.children = kids
		end
	end
	return node
end

------------------------------------------------------------------------------
-- Script line helpers
------------------------------------------------------------------------------

local function splitLines(src: string): { string }
	local lines = {}
	local startIdx = 1
	while true do
		local nl = string.find(src, "\n", startIdx, true)
		if not nl then
			table.insert(lines, string.sub(src, startIdx))
			break
		end
		table.insert(lines, string.sub(src, startIdx, nl - 1))
		startIdx = nl + 1
	end
	return lines
end

local SURVEY_SERVICES = {
	"Workspace", "Players", "Lighting", "ReplicatedStorage", "ReplicatedFirst",
	"ServerScriptService", "ServerStorage", "StarterGui", "StarterPack",
	"StarterPlayer", "SoundService", "Teams", "TextChatService", "MaterialService",
}

local function collectAllScripts(rootPath: string?): { Instance }
	local results = {}
	local roots: { Instance } = {}
	if rootPath and rootPath ~= "" and rootPath ~= "game" then
		local r = mustResolve(rootPath)
		table.insert(roots, r)
	else
		for _, svcName in ipairs(SURVEY_SERVICES) do
			local ok, svc = pcall(function()
				return game:GetService(svcName)
			end)
			if ok and svc then
				table.insert(roots, svc)
			end
		end
	end
	for _, root in ipairs(roots) do
		if root:IsA("LuaSourceContainer") then
			table.insert(results, root)
		end
		for _, desc in ipairs(root:GetDescendants()) do
			if desc:IsA("LuaSourceContainer") then
				table.insert(results, desc)
			end
		end
	end
	return results
end

------------------------------------------------------------------------------
-- Job handlers
------------------------------------------------------------------------------

local handlers: { [string]: (any) -> any } = {}

handlers["ping"] = function(_)
	local okVer, ver = pcall(function()
		return version()
	end)
	local okUndo, canUndo = pcall(function()
		return ChangeHistoryService:GetCanUndo()
	end)
	local okRedo, canRedo = pcall(function()
		return ChangeHistoryService:GetCanRedo()
	end)
	return {
		place = game.Name,
		placeId = game.PlaceId,
		gameId = game.GameId,
		studio = okVer and ver or "unknown",
		pluginVersion = PLUGIN_VERSION,
		dm = DM_MODE,
		peerId = PEER_ID,
		hasClient = DM_MODE == "server" and (#Players:GetPlayers() > 0 or RunService:IsClient()) or false,
		playerCount = #Players:GetPlayers(),
		canUndo = okUndo and canUndo or false,
		canRedo = okRedo and canRedo or false,
	}
end

handlers["studio_state"] = function(_)
	local okVer, ver = pcall(function()
		return version()
	end)
	local okUndo, canUndo, undoName = pcall(function()
		return ChangeHistoryService:GetCanUndo()
	end)
	local okRedo, canRedo, redoName = pcall(function()
		return ChangeHistoryService:GetCanRedo()
	end)
	local okMem, memMb = pcall(function()
		return (okStats and StatsService) and StatsService:GetTotalMemoryUsageMb() or 0
	end)
	local okFps, physicsFps = pcall(function()
		return workspace:GetRealPhysicsFPS()
	end)
	local camInfo: any = nil
	local cam = workspace.CurrentCamera
	if cam then
		camInfo = {
			cframe = serializeValue(cam.CFrame),
			fov = cam.FieldOfView,
			viewportSize = serializeValue(cam.ViewportSize),
		}
	end
	local playerList = {}
	for _, plr in ipairs(Players:GetPlayers()) do
		table.insert(playerList, {
			name = plr.Name,
			displayName = plr.DisplayName,
			userId = plr.UserId,
			hasCharacter = plr.Character ~= nil,
		})
	end
	return {
		place = {
			name = game.Name,
			placeId = game.PlaceId,
			gameId = game.GameId,
		},
		studio = okVer and ver or "unknown",
		pluginVersion = PLUGIN_VERSION,
		dm = DM_MODE,
		peerId = PEER_ID,
		isEdit = DM_MODE == "edit",
		isRunning = RunService:IsRunning(),
		playtestActive = activePlaytestRunning,
		lastPlaytestResult = lastPlaytestResult,
		players = playerList,
		selectionCount = #Selection:Get(),
		canUndo = okUndo and canUndo or false,
		nextUndo = okUndo and undoName or nil,
		canRedo = okRedo and canRedo or false,
		nextRedo = okRedo and redoName or nil,
		memoryMb = okMem and math.floor(memMb * 10) / 10 or nil,
		physicsFps = okFps and math.floor(physicsFps * 10) / 10 or nil,
		camera = camInfo,
	}
end

handlers["mesh_capabilities"] = function(_p)
	local okVer, studioVersion = pcall(function()
		return version()
	end)
	local assetService = game:GetService("AssetService")
	local createEditableMesh = (assetService :: any).CreateEditableMesh
	local editable: any = {
		apiPresent = typeof(createEditableMesh) == "function",
		creationAttempted = false,
		creationSucceeded = nil,
		status = "api_missing",
		detail = nil,
	}
	if editable.apiPresent then
		editable.creationAttempted = true
		local okCreate, result = pcall(function()
			return createEditableMesh(assetService)
		end)
		if okCreate and result ~= nil then
			local okDestroy, destroyErr = pcall(function()
				(result :: any):Destroy()
			end)
			editable.creationSucceeded = true
			editable.status = "creation_verified"
			if not okDestroy then
				editable.detail = "Created successfully; cleanup reported: " .. tostring(destroyErr)
			end
		elseif okCreate then
			editable.creationSucceeded = false
			editable.status = "creation_returned_nil"
		else
			editable.creationSucceeded = false
			editable.status = "present_but_creation_failed"
			editable.detail = tostring(result)
		end
	end
	local meshPartOk, meshPart = pcall(function()
		return Instance.new("MeshPart")
	end)
	if meshPartOk and meshPart then
		pcall(function() meshPart:Destroy() end)
	end
	return {
		ok = true,
		studioVersion = okVer and studioVersion or "unknown",
		peerId = PEER_ID,
		meshPartClass = { available = meshPartOk, creationError = not meshPartOk and tostring(meshPart) or nil },
		editableMesh = editable,
		verifiedAtUnix = os.time(),
		warnings = { "Capability results are runtime-probe evidence for this peer only; no support is inferred from the Studio version string." },
	}
end

handlers["run_luau"] = function(p)
	local result
	if DM_MODE == "edit" then
		result = executeLuau(p.code)
	else
		result = executePlayProbe(p.code, p._playerName, p._timeout)
	end
	local returnedStr: string
	local structuredVal: any = nil
	if result == nil then
		returnedStr = "nil"
	elseif typeof(result) == "string" then
		returnedStr = result
	elseif typeof(result) == "table" then
		structuredVal = serializeValue(result)
		local okJson, encoded = pcall(function()
			return HttpService:JSONEncode(structuredVal)
		end)
		returnedStr = okJson and encoded or tostring(result)
	else
		returnedStr = tostring(result)
		structuredVal = serializeValue(result)
	end
	return {
		returned = returnedStr,
		value = structuredVal,
		dm = DM_MODE,
	}
end

handlers["require_fresh"] = function(p)
	local inst = mustResolve(p.path)
	if not inst:IsA("ModuleScript") then
		error("not a ModuleScript: " .. fullName(inst), 0)
	end

	-- Find top-most package root under the service so relative sibling/parent
	-- requires inside the package resolve against fresh clones too.
	local rootToClone: Instance = inst
	if p.packageRoot and p.packageRoot ~= "" then
		rootToClone = mustResolve(p.packageRoot)
	else
		local cursor: Instance = inst
		while cursor.Parent and cursor.Parent ~= game do
			local parentParent = cursor.Parent.Parent
			if parentParent == game then
				break
			end
			cursor = cursor.Parent :: Instance
		end
		rootToClone = cursor
	end

	local relChain = {}
	local step: Instance? = inst
	while step and step ~= rootToClone do
		table.insert(relChain, 1, step.Name)
		step = step.Parent
	end

	local sandbox = Instance.new("Folder")
	sandbox.Name = "__ArenaFreshSandbox_" .. tostring(math.random(100000, 999999))
	sandbox.Archivable = false
	sandbox.Parent = ServerStorage

	local clonedRoot = rootToClone:Clone()
	clonedRoot.Parent = sandbox

	local targetClone: Instance = clonedRoot
	for _, segName in ipairs(relChain) do
		local nextChild = targetClone:FindFirstChild(segName)
		if not nextChild then
			sandbox:Destroy()
			error("failed to resolve cloned child segment: " .. segName, 0)
		end
		targetClone = nextChild
	end

	local okEval, evalOut = pcall(function()
		local mod = require(targetClone :: any)
		if p.code and p.code ~= "" then
			local wrapperCode = "return function(MODULE, MODULE_INSTANCE, PACKAGE_ROOT)\n"
				.. p.code .. "\nend"
			local fn = executeLuau(wrapperCode)
			return (fn :: any)(mod, targetClone, clonedRoot)
		end
		return mod
	end)

	pcall(function()
		sandbox:Destroy()
	end)

	if not okEval then
		error(tostring(evalOut), 0)
	end

	local serialized = serializeValue(evalOut)
	local okJson, jsonText = pcall(function()
		return HttpService:JSONEncode(serialized)
	end)
	return {
		path = fullName(inst),
		packageRoot = fullName(rootToClone),
		returned = typeof(evalOut) == "string" and evalOut or (okJson and jsonText or tostring(evalOut)),
		value = serialized,
	}
end

handlers["build"] = function(p)
	local parent = mustResolve(p.parent or "Workspace")
	local created = {}
	local replaceQueue = {}
	local okBuild, buildErr = pcall(function()
		for _, spec in ipairs(p.tree or { p }) do
			local inst = buildNode(spec, parent, replaceQueue)
			table.insert(created, fullName(inst))
		end
	end)
	if not okBuild then
		for _, path in ipairs(created) do
			local inst = resolve(path)
			if inst then pcall(function() inst:Destroy() end) end
		end
		error("build transaction rolled back: " .. tostring(buildErr), 0)
	end
	for _, oldInst in ipairs(replaceQueue) do
		if oldInst.Parent then oldInst:Destroy() end
	end
	if p.select ~= false and DM_MODE == "edit" then
		local sel = {}
		for _, path in ipairs(created) do
			local inst = resolve(path)
			if inst then
				table.insert(sel, inst)
			end
		end
		Selection:Set(sel)
	end
	return { created = created, count = #created }
end

handlers["write_script"] = function(p)
	local parent = mustResolve(p.parent or "ServerScriptService")
	local className = p.className or "Script"
	local name = p.name or "ArenaScript"
	local cleanSource = normalizeNewlines(p.source or "")
	local existing = parent:FindFirstChild(name)
	local target: Instance
	local previousHash: string? = nil

	if existing and existing:IsA("LuaSourceContainer") then
		local oldSrc = readScriptSource(existing)
		previousHash = fnvHex(oldSrc)
		if p.expectedHash and p.expectedHash ~= "" and previousHash ~= p.expectedHash then
			error(string.format(
				"hash mismatch on %s: expected %s, live has %s",
				fullName(existing), tostring(p.expectedHash), previousHash
			), 0)
		end
		if p.replaceInstance then
			target = replaceScriptInstance(existing, cleanSource)
		else
			target = existing
			writeScriptSource(target, cleanSource)
		end
	else
		if existing then
			existing:Destroy()
		end
		target = Instance.new(className)
		target.Name = name
		target.Parent = parent
		writeScriptSource(target, cleanSource)
	end

	applyProps(target, p.properties)
	if p.attributes then
		for k, v in pairs(p.attributes) do
			pcall(function()
				target:SetAttribute(k, coerce(v))
			end)
		end
	end
	if p.tags then
		for _, tag in ipairs(p.tags) do
			pcall(function()
				CollectionService:AddTag(target, tostring(tag))
			end)
		end
	end
	if p.select ~= false and DM_MODE == "edit" then
		Selection:Set({ target })
	end
	local finalSrc = readScriptSource(target)
	local _, newlines = string.gsub(finalSrc, "\n", "\n")
	return {
		path = fullName(target),
		bytes = #finalSrc,
		lines = newlines + 1,
		previousHash = previousHash,
		hash = fnvHex(finalSrc),
		replacedInstance = p.replaceInstance == true,
	}
end

handlers["batch_write"] = function(p)
	local results = {}
	for index, spec in ipairs(p.files or {}) do
		if type(spec.parent) ~= "string" or type(spec.name) ~= "string" or type(spec.source) ~= "string" then
			error("batch_write files[" .. tostring(index) .. "] requires parent, name, and source", 0)
		end
		local result = handlers["write_script"]({
			parent = spec.parent, name = spec.name, className = spec.className or "ModuleScript",
			source = spec.source, expectedHash = spec.expectedHash,
			replaceInstance = spec.replaceInstance ~= false, properties = spec.properties,
			attributes = spec.attributes, tags = spec.tags, select = false,
		})
		table.insert(results, result)
	end
	return {ok = true, written = results, count = #results}
end

handlers["multi_edit"] = function(p)
	local target = resolve(p.path)
	if not target then
		if p.createIfMissing then
			local parts = splitPath(p.path)
			local scriptName = table.remove(parts)
			local parentPath = table.concat(parts, ".")
			local parentInst = mustResolve(parentPath ~= "" and parentPath or "ServerScriptService")
			target = Instance.new(p.className or "ModuleScript")
			target.Name = scriptName or "NewScript"
			target.Parent = parentInst
			writeScriptSource(target, "")
		else
			error("script not found: " .. tostring(p.path), 0)
		end
	end
	if not target:IsA("LuaSourceContainer") then
		error("not a script: " .. fullName(target), 0)
	end

	local working = normalizeNewlines(readScriptSource(target))
	local previousHash = fnvHex(working)
	if p.expectedHash and p.expectedHash ~= "" and previousHash ~= p.expectedHash then
		error(string.format(
			"hash mismatch on %s: expected %s, live has %s",
			fullName(target), tostring(p.expectedHash), previousHash
		), 0)
	end

	local edits = p.edits or {}
	local appliedCount = 0
	for idx, edit in ipairs(edits) do
		if edit.startLine ~= nil and edit.endLine ~= nil then
			local lines = splitLines(working)
			local sLine = math.max(1, tonumber(edit.startLine) or 1)
			local eLine = tonumber(edit.endLine) or sLine
			if sLine > #lines + 1 then
				error(string.format("edit #%d startLine %d out of range (%d lines)", idx, sLine, #lines), 0)
			end
			local newChunkLines = splitLines(normalizeNewlines(edit.newText or ""))
			local rebuilt = {}
			for i = 1, sLine - 1 do
				table.insert(rebuilt, lines[i])
			end
			for _, nl in ipairs(newChunkLines) do
				table.insert(rebuilt, nl)
			end
			for i = eLine + 1, #lines do
				table.insert(rebuilt, lines[i])
			end
			working = table.concat(rebuilt, "\n")
			appliedCount += 1
		elseif edit.oldText ~= nil then
			local needle = normalizeNewlines(tostring(edit.oldText))
			local replacement = normalizeNewlines(tostring(edit.newText or ""))
			if needle == "" then
				working = replacement .. working
				appliedCount += 1
			elseif edit.replaceAll then
				local startPos = 1
				local chunks = {}
				local count = 0
				while true do
					local s, e = string.find(working, needle, startPos, true)
					if not s then
						table.insert(chunks, string.sub(working, startPos))
						break
					end
					table.insert(chunks, string.sub(working, startPos, s - 1))
					table.insert(chunks, replacement)
					startPos = e + 1
					count += 1
				end
				if count == 0 then
					error(string.format("edit #%d oldText not found in %s", idx, fullName(target)), 0)
				end
				working = table.concat(chunks, "")
				appliedCount += count
			else
				local s, e = string.find(working, needle, 1, true)
				if not s then
					error(string.format("edit #%d oldText not found in %s", idx, fullName(target)), 0)
				end
				local second = string.find(working, needle, e + 1, true)
				if second and not edit.allowFirst then
					error(string.format(
						"edit #%d oldText is ambiguous (multiple matches) in %s; provide more context or set replaceAll=true",
						idx, fullName(target)
					), 0)
				end
				working = string.sub(working, 1, s - 1) .. replacement .. string.sub(working, e + 1)
				appliedCount += 1
			end
		else
			error(string.format("edit #%d requires oldText or startLine/endLine", idx), 0)
		end
	end

	if p.replaceInstance then
		target = replaceScriptInstance(target, working)
	else
		writeScriptSource(target, working)
	end

	local finalSrc = readScriptSource(target)
	local _, newlines = string.gsub(finalSrc, "\n", "\n")
	return {
		path = fullName(target),
		editsApplied = appliedCount,
		bytes = #finalSrc,
		lines = newlines + 1,
		previousHash = previousHash,
		hash = fnvHex(finalSrc),
		replacedInstance = p.replaceInstance == true,
	}
end

handlers["read_script"] = function(p)
	local inst = mustResolve(p.path)
	if not inst:IsA("LuaSourceContainer") then
		error("not a script: " .. fullName(inst), 0)
	end
	local src = readScriptSource(inst)
	local fullHash = fnvHex(src)
	local allLines = splitLines(src)
	local totalLines = #allLines
	local sLine = tonumber(p.startLine)
	local eLine = tonumber(p.endLine)

	local outSource = src
	local actualStart = 1
	local actualEnd = totalLines
	if sLine or eLine then
		actualStart = math.clamp(sLine or 1, 1, math.max(1, totalLines))
		actualEnd = math.clamp(eLine or totalLines, actualStart, math.max(1, totalLines))
		local slice = {}
		for i = actualStart, actualEnd do
			if p.withLineNumbers then
				table.insert(slice, string.format("%4d | %s", i, allLines[i] or ""))
			else
				table.insert(slice, allLines[i] or "")
			end
		end
		outSource = table.concat(slice, "\n")
	elseif p.withLineNumbers then
		local numbered = {}
		for i = 1, totalLines do
			table.insert(numbered, string.format("%4d | %s", i, allLines[i]))
		end
		outSource = table.concat(numbered, "\n")
	end

	return {
		path = fullName(inst),
		className = inst.ClassName,
		bytes = #src,
		totalLines = totalLines,
		startLine = actualStart,
		endLine = actualEnd,
		hash = fullHash,
		source = outSource,
	}
end

handlers["read_scripts"] = function(p)
	local budget = tonumber(p.maxBytes) or 200000
	local spent = 0
	local out = {}
	for _, path in ipairs(p.paths or {}) do
		local inst = resolve(path)
		if inst and inst:IsA("LuaSourceContainer") then
			local src = readScriptSource(inst)
			local slice = src
			local truncated = false
			if spent + #slice > budget then
				slice = string.sub(slice, 1, math.max(0, budget - spent))
				truncated = true
			end
			spent += #slice
			local _, newlines = string.gsub(src, "\n", "\n")
			table.insert(out, {
				path = fullName(inst),
				className = inst.ClassName,
				bytes = #src,
				lines = newlines + 1,
				hash = fnvHex(src),
				source = slice,
				truncated = truncated,
			})
		else
			table.insert(out, { path = path, error = "not found or not a script" })
		end
	end
	return { scripts = out, bytesReturned = spent }
end

handlers["hash_scripts"] = function(p)
	local list = collectAllScripts(p.root)
	local entries = {}
	for _, inst in ipairs(list) do
		local src = readScriptSource(inst)
		local _, newlines = string.gsub(src, "\n", "\n")
		table.insert(entries, {
			path = fullName(inst),
			name = inst.Name,
			className = inst.ClassName,
			bytes = #src,
			lines = newlines + 1,
			hash = fnvHex(src),
			hashInt = fnv32(src),
		})
	end
	table.sort(entries, function(a, b)
		return a.path < b.path
	end)
	return {
		root = p.root or "game",
		count = #entries,
		scripts = entries,
	}
end

handlers["script_search"] = function(p)
	local query = string.lower(tostring(p.query or ""))
	local limit = tonumber(p.limit) or 25
	local scripts = collectAllScripts(p.root)
	local scored = {}

	for _, inst in ipairs(scripts) do
		local path = fullName(inst)
		local lowerName = string.lower(inst.Name)
		local lowerPath = string.lower(path)
		local score = 0
		if query == "" then
			score = 1
		elseif lowerName == query then
			score = 100
		elseif string.sub(lowerName, 1, #query) == query then
			score = 80
		elseif string.find(lowerName, query, 1, true) then
			score = 60
		elseif string.find(lowerPath, query, 1, true) then
			score = 40
		else
			-- Subsequence fuzzy match
			local qi = 1
			for ci = 1, #lowerName do
				if string.sub(lowerName, ci, ci) == string.sub(query, qi, qi) then
					qi += 1
					if qi > #query then
						score = 20
						break
					end
				end
			end
		end
		if score > 0 then
			local src = readScriptSource(inst)
			local _, newlines = string.gsub(src, "\n", "\n")
			table.insert(scored, {
				score = score,
				path = path,
				name = inst.Name,
				className = inst.ClassName,
				lines = newlines + 1,
				bytes = #src,
				hash = fnvHex(src),
			})
		end
	end

	table.sort(scored, function(a, b)
		if a.score ~= b.score then
			return a.score > b.score
		end
		return a.path < b.path
	end)

	local results = {}
	for i = 1, math.min(#scored, limit) do
		table.insert(results, scored[i])
	end
	return { matches = results, totalMatched = #scored }
end

handlers["script_grep"] = function(p)
	local pattern = tostring(p.pattern or "")
	if pattern == "" then
		error("pattern is required", 0)
	end
	local plain = p.isPattern ~= true
	local caseSensitive = p.caseSensitive ~= false
	local contextLines = math.clamp(tonumber(p.contextLines) or 1, 0, 10)
	local maxMatches = math.clamp(tonumber(p.maxMatches) or 50, 1, 500)
	local scripts = collectAllScripts(p.root)

	local needle = caseSensitive and pattern or string.lower(pattern)
	local matches = {}
	local truncated = false

	for _, inst in ipairs(scripts) do
		local path = fullName(inst)
		local src = readScriptSource(inst)
		local lines = splitLines(src)
		for lineNum, lineText in ipairs(lines) do
			local hay = caseSensitive and lineText or string.lower(lineText)
			local okMatch, found = pcall(string.find, hay, needle, 1, plain)
			if okMatch and found then
				local ctx = {}
				if contextLines > 0 then
					local cStart = math.max(1, lineNum - contextLines)
					local cEnd = math.min(#lines, lineNum + contextLines)
					for ci = cStart, cEnd do
						table.insert(ctx, { line = ci, text = lines[ci] })
					end
				end
				table.insert(matches, {
					path = path,
					className = inst.ClassName,
					line = lineNum,
					text = lineText,
					context = contextLines > 0 and ctx or nil,
				})
				if #matches >= maxMatches then
					truncated = true
					break
				end
			end
		end
		if truncated then
			break
		end
	end

	return {
		pattern = pattern,
		matches = matches,
		count = #matches,
		truncated = truncated,
		scriptsScanned = #scripts,
	}
end

handlers["find_replace_scripts"] = function(p)
	local needle = normalizeNewlines(tostring(p.find or ""))
	if needle == "" then
		error("find text cannot be empty", 0)
	end
	local replacement = normalizeNewlines(tostring(p.replace or ""))
	local dryRun = p.dryRun == true
	local scripts = collectAllScripts(p.root)
	local affected = {}
	local totalReplacements = 0

	for _, inst in ipairs(scripts) do
		local src = normalizeNewlines(readScriptSource(inst))
		local startPos = 1
		local chunks = {}
		local count = 0
		while true do
			local s, e = string.find(src, needle, startPos, true)
			if not s then
				table.insert(chunks, string.sub(src, startPos))
				break
			end
			table.insert(chunks, string.sub(src, startPos, s - 1))
			table.insert(chunks, replacement)
			startPos = e + 1
			count += 1
		end
		if count > 0 then
			local updated = table.concat(chunks, "")
			totalReplacements += count
			local finalInst = inst
			if not dryRun then
				if p.replaceInstance then
					finalInst = replaceScriptInstance(inst, updated)
				else
					writeScriptSource(inst, updated)
				end
			end
			table.insert(affected, {
				path = fullName(finalInst),
				replacements = count,
				previousHash = fnvHex(src),
				newHash = fnvHex(updated),
			})
		end
	end

	return {
		dryRun = dryRun,
		scriptsModified = #affected,
		totalReplacements = totalReplacements,
		files = affected,
	}
end

handlers["open_script"] = function(p)
	if not okSES or not ScriptEditorService then
		error("ScriptEditorService unavailable", 0)
	end
	if p.listOnly then
		local openTabs = {}
		for _, doc in ipairs(ScriptEditorService:GetScriptDocuments()) do
			local isCmd = false
			pcall(function()
				isCmd = doc:IsCommandBar()
			end)
			if not isCmd then
				local scr = nil
				pcall(function()
					scr = doc:GetScript()
				end)
				table.insert(openTabs, {
					name = doc.Name,
					path = scr and fullName(scr) or doc.Name,
					lineCount = doc:GetLineCount(),
				})
			end
		end
		return { openScripts = openTabs, count = #openTabs }
	end

	local inst = mustResolve(p.path)
	if not inst:IsA("LuaSourceContainer") then
		error("not a script: " .. fullName(inst), 0)
	end
	local okOpen, docOrErr = pcall(function()
		return ScriptEditorService:OpenScriptDocumentAsync(inst :: any)
	end)
	if not okOpen then
		error("could not open script: " .. tostring(docOrErr), 0)
	end
	if p.line and docOrErr then
		local lineNum = math.max(1, tonumber(p.line) or 1)
		pcall(function()
			(docOrErr :: any):RequestSetSelectionAsync(lineNum, 1, lineNum, 1)
		end)
	end
	return { opened = fullName(inst), line = p.line or 1 }
end

handlers["inspect"] = function(p)
	local inst = mustResolve(p.path or "game")
	local out = describe(
		inst,
		tonumber(p.depth) or 2,
		tonumber(p.maxChildren) or 100,
		p.includeStructured ~= false,
		p.includeStructuredDescendants == true
	)
	if p.descendantSummary then
		local classCounts: { [string]: number } = {}
		local totalDesc = 0
		for _, d in ipairs(inst:GetDescendants()) do
			totalDesc += 1
			classCounts[d.ClassName] = (classCounts[d.ClassName] or 0) + 1
		end
		out.totalDescendants = totalDesc
		out.descendantClasses = classCounts
	end
	return out
end

handlers["search_tree"] = function(p)
	local root = resolve(p.root or "game") or game
	local query = p.query and string.lower(tostring(p.query)) or nil
	local exactClass = p.className and tostring(p.className) or nil
	local isAClass = p.isA and tostring(p.isA) or nil
	local reqTag = p.tag and tostring(p.tag) or nil
	local reqAttr = p.attribute and tostring(p.attribute) or nil
	local propName = p.property and tostring(p.property) or nil
	local propVal = p.propertyValue ~= nil and tostring(p.propertyValue) or nil
	local maxDepth = tonumber(p.maxDepth) or 12
	local limit = math.clamp(tonumber(p.limit) or 60, 1, 500)

	local matches = {}
	local visited = 0
	local queue = {}

	if root == game then
		for _, svcName in ipairs(SURVEY_SERVICES) do
			local ok, svc = pcall(function()
				return game:GetService(svcName)
			end)
			if ok and svc then
				table.insert(queue, { inst = svc, depth = 1 })
			end
		end
	else
		table.insert(queue, { inst = root, depth = 0 })
	end

	local head = 1
	while head <= #queue do
		local item = queue[head]
		head += 1
		visited += 1
		if visited > 50000 then
			break
		end

		local inst = item.inst
		local okMatch = true

		if exactClass and inst.ClassName ~= exactClass then
			okMatch = false
		end
		if okMatch and isAClass then
			local okIsA, res = pcall(function()
				return inst:IsA(isAClass)
			end)
			if not okIsA or not res then
				okMatch = false
			end
		end
		if okMatch and query and query ~= "" then
			if not string.find(string.lower(inst.Name), query, 1, true)
				and not string.find(string.lower(inst.ClassName), query, 1, true) then
				okMatch = false
			end
		end
		if okMatch and reqTag then
			if not CollectionService:HasTag(inst, reqTag) then
				okMatch = false
			end
		end
		if okMatch and reqAttr then
			if inst:GetAttribute(reqAttr) == nil then
				okMatch = false
			end
		end
		if okMatch and propName then
			local okProp, pv = pcall(function()
				return (inst :: any)[propName]
			end)
			if not okProp or pv == nil then
				okMatch = false
			elseif propVal ~= nil and tostring(pv) ~= propVal then
				okMatch = false
			end
		end

		if okMatch and not (root ~= game and inst == root and p.includeRoot == false) then
			table.insert(matches, {
				name = inst.Name,
				className = inst.ClassName,
				path = fullName(inst),
				childCount = #inst:GetChildren(),
			})
			if #matches >= limit then
				break
			end
		end

		if item.depth < maxDepth then
			for _, child in ipairs(inst:GetChildren()) do
				table.insert(queue, { inst = child, depth = item.depth + 1 })
			end
		end
	end

	return {
		matches = matches,
		count = #matches,
		visited = visited,
		limitHit = #matches >= limit,
	}
end

handlers["compare_instances"] = function(p)
	local a = mustResolve(p.pathA)
	local b = mustResolve(p.pathB)

	local propsA, _ = readInstanceProperties(a, nil)
	local propsB, _ = readInstanceProperties(b, nil)
	local propDiffs = {}
	local checkedProps: { [string]: boolean } = {}
	for k, va in pairs(propsA) do
		checkedProps[k] = true
		if propsB[k] ~= va then
			table.insert(propDiffs, { property = k, a = va, b = propsB[k] })
		end
	end
	for k, vb in pairs(propsB) do
		if not checkedProps[k] then
			table.insert(propDiffs, { property = k, a = nil, b = vb })
		end
	end

	local attrsA = readAttributesMap(a)
	local attrsB = readAttributesMap(b)
	local attrDiffs = {}
	local checkedAttrs: { [string]: boolean } = {}
	for k, va in pairs(attrsA) do
		checkedAttrs[k] = true
		local ja = HttpService:JSONEncode(va)
		local jb = HttpService:JSONEncode(attrsB[k])
		if ja ~= jb then
			table.insert(attrDiffs, { attribute = k, a = va, b = attrsB[k] })
		end
	end
	for k, vb in pairs(attrsB) do
		if not checkedAttrs[k] then
			table.insert(attrDiffs, { attribute = k, a = nil, b = vb })
		end
	end

	local scriptComparison: any = nil
	if a:IsA("LuaSourceContainer") and b:IsA("LuaSourceContainer") then
		local sa = readScriptSource(a)
		local sb = readScriptSource(b)
		scriptComparison = {
			hashA = fnvHex(sa),
			hashB = fnvHex(sb),
			bytesA = #sa,
			bytesB = #sb,
			identical = sa == sb,
		}
	end

	local kidsA: { [string]: string } = {}
	local onlyInA = {}
	local onlyInB = {}
	for _, c in ipairs(a:GetChildren()) do
		kidsA[c.Name] = c.ClassName
	end
	local seenB: { [string]: boolean } = {}
	for _, c in ipairs(b:GetChildren()) do
		seenB[c.Name] = true
		if not kidsA[c.Name] then
			table.insert(onlyInB, c.Name .. " (" .. c.ClassName .. ")")
		end
	end
	for k, cls in pairs(kidsA) do
		if not seenB[k] then
			table.insert(onlyInA, k .. " (" .. cls .. ")")
		end
	end

	return {
		pathA = fullName(a),
		pathB = fullName(b),
		classA = a.ClassName,
		classB = b.ClassName,
		sameClass = a.ClassName == b.ClassName,
		propertyDiffs = propDiffs,
		attributeDiffs = attrDiffs,
		scriptComparison = scriptComparison,
		childrenOnlyInA = onlyInA,
		childrenOnlyInB = onlyInB,
	}
end

handlers["get_properties"] = function(p)
	local targetPaths = p.paths or (p.path and { p.path } or {})
	local requested = p.properties
	local results = {}
	for _, path in ipairs(targetPaths) do
		local inst = resolve(path)
		if inst then
			local propStrings, structured = readInstanceProperties(inst, requested)
			table.insert(results, {
				path = fullName(inst),
				name = inst.Name,
				className = inst.ClassName,
				properties = propStrings,
				structured = structured,
			})
		else
			table.insert(results, { path = path, error = "not found" })
		end
	end
	return { instances = results, count = #results }
end

handlers["set_properties"] = function(p)
	if type(p.properties) ~= "table" or next(p.properties) == nil then
		error("set_properties requires a non-empty properties object", 0)
	end
	local inst = mustResolve(p.path)
	local applied, failed = applyProps(inst, p.properties)
	local ok = next(failed) == nil
	return {
		ok = ok,
		path = fullName(inst),
		applied = applied,
		failed = next(failed) and failed or nil,
		warnings = ok and {} or { "One or more properties were not applied." },
	}
end

handlers["mass_set_properties"] = function(p)
	local paths = p.paths or {}
	if #paths == 0 then error("mass_set_properties requires at least one path", 0) end
	if (type(p.properties) ~= "table" or next(p.properties) == nil) and (type(p.relative) ~= "table" or next(p.relative) == nil) then
		error("mass_set_properties requires non-empty properties or relative deltas", 0)
	end
	local updated = {}
	local errors = {}
	local anyFailed = false
	for _, path in ipairs(paths) do
		local inst = resolve(path)
		if inst then
			local applied, failed = applyProps(inst, p.properties)
			if next(failed) ~= nil then anyFailed = true end
			if p.relative then
				for propName, deltaRaw in pairs(p.relative) do
					local okRel, relErr = pcall(function()
						local cur = (inst :: any)[propName]
						local delta = coerce(deltaRaw)
						if typeof(cur) == "Vector3" and type(delta) == "table" then
							delta = Vector3.new(delta[1], delta[2], delta[3])
						end
						(inst :: any)[propName] = cur + delta
					end)
					if okRel then
						table.insert(applied, propName)
					else
						failed[propName] = tostring(relErr)
						anyFailed = true
					end
				end
			end
			if next(failed) ~= nil then anyFailed = true end
			table.insert(updated, {
				path = fullName(inst),
				applied = applied,
				failed = next(failed) and failed or nil,
			})
		else
			anyFailed = true
			table.insert(errors, { path = path, error = "not found" })
		end
	end
	return {
		ok = not anyFailed,
		updated = updated,
		count = #updated,
		errors = #errors > 0 and errors or nil,
		warnings = anyFailed and { "One or more paths or properties failed; inspect updated[].failed and errors." } or {},
	}
end

handlers["clone_instance"] = function(p)
	local source = mustResolve(p.path)
	local parent = p.parent and mustResolve(p.parent) or (source.Parent or workspace)
	local count = math.clamp(tonumber(p.count) or 1, 1, 500)
	local offsetVec: Vector3? = nil
	if p.offset then
		local c = coerce(p.offset)
		if typeof(c) == "Vector3" then
			offsetVec = c
		elseif type(c) == "table" and #c >= 3 then
			offsetVec = Vector3.new(c[1], c[2], c[3])
		end
	end

	local created = {}
	for i = 1, count do
		local copy = source:Clone()
		if not copy then
			error("instance cannot be cloned (Archivable=false?): " .. fullName(source), 0)
		end
		if p.newName then
			if count > 1 then
				if string.find(p.newName, "{i}", 1, true) then
					copy.Name = string.gsub(p.newName, "{i}", tostring(i))
				else
					copy.Name = p.newName .. "_" .. tostring(i)
				end
			else
				copy.Name = tostring(p.newName)
			end
		end
		if offsetVec then
			pcall(function()
				if copy:IsA("PVInstance") then
					copy:PivotTo(copy:GetPivot() + offsetVec * i)
				end
			end)
		end
		applyProps(copy, p.properties)
		copy.Parent = parent
		table.insert(created, fullName(copy))
	end
	return { created = created, count = #created }
end

handlers["scatter_props"] = function(p)
	local source = mustResolve(p.sourcePath)
	if not source:IsA("PVInstance") then error("scatter_props source must be a BasePart or Model", 0) end
	local parent = mustResolve(p.parent or "Workspace")
	local points = p.points or {}
	if #points < 1 or #points > 500 then error("scatter_props requires 1-500 positions", 0) end
	local made = {}
	local paths = {}
	local okScatter, scatterErr = pcall(function()
		for index, point in ipairs(points) do
			if type(point) ~= "table" or #point < 4 then error("invalid scatter point at index " .. tostring(index), 0) end
			local x, y, z, yaw = tonumber(point[1]), tonumber(point[2]), tonumber(point[3]), tonumber(point[4])
			if not x or not y or not z or not yaw then error("scatter coordinates must be numeric", 0) end
			local copy = source:Clone()
			if not copy then error("source cannot be cloned (Archivable=false?)", 0) end
			table.insert(made, copy)
			copy.Name = tostring(p.namePrefix or "Scatter") .. string.format("_%03d", index)
			if p.stripScripts ~= false then
				for _, desc in ipairs(copy:GetDescendants()) do
					if desc:IsA("LuaSourceContainer") then desc:Destroy() end
				end
			end
			copy:PivotTo(CFrame.new(x, y, z) * CFrame.Angles(0, math.rad(yaw), 0))
			copy.Parent = parent
			table.insert(paths, fullName(copy))
		end
	end)
	if not okScatter then
		for _, copy in ipairs(made) do pcall(function() copy:Destroy() end) end
		error("scatter transaction rolled back: " .. tostring(scatterErr), 0)
	end
	return { ok = true, created = paths, count = #paths, scriptsStripped = p.stripScripts ~= false, warnings = {} }
end

handlers["reparent_instance"] = function(p)
	local paths = p.paths or (p.path and { p.path } or {})
	local newParent = p.newParent and mustResolve(p.newParent) or nil
	local moved = {}
	for _, path in ipairs(paths) do
		local inst = mustResolve(path)
		local savedPivot: CFrame? = nil
		if p.preserveWorldPosition and inst:IsA("PVInstance") then
			pcall(function()
				savedPivot = inst:GetPivot()
			end)
		end
		if p.newName and #paths == 1 then
			inst.Name = tostring(p.newName)
		end
		if newParent then
			inst.Parent = newParent
		end
		if savedPivot and inst:IsA("PVInstance") then
			pcall(function()
				inst:PivotTo(savedPivot :: CFrame)
			end)
		end
		table.insert(moved, fullName(inst))
	end
	return { moved = moved, count = #moved }
end

handlers["delete"] = function(p)
	local targets = p.paths or (p.path and { p.path } or {})
	local deleted = {}
	local missing = {}
	for _, path in ipairs(targets) do
		local okFind, inst = pcall(function() return resolve(path) end)
		if not okFind or not inst then
			table.insert(missing, tostring(path))
		else
			local name = fullName(inst)
			local okDestroy = pcall(function() inst:Destroy() end)
			if okDestroy then table.insert(deleted, name) else table.insert(missing, tostring(path)) end
		end
	end
	return {
		ok = #missing == 0,
		deleted = deleted,
		paths = deleted,
		missing = missing,
		count = #deleted,
	}
end

handlers["attributes"] = function(p)
	local action = p.action or "get"
	local paths = p.paths or (p.path and { p.path } or {})
	local out = {}
	for _, path in ipairs(paths) do
		local inst = mustResolve(path)
		if action == "set" then
			for k, v in pairs(p.attributes or {}) do
				inst:SetAttribute(k, coerce(v))
			end
		elseif action == "delete" then
			for _, k in ipairs(p.keys or (p.key and { p.key } or {})) do
				inst:SetAttribute(k, nil)
			end
		end
		table.insert(out, {
			path = fullName(inst),
			attributes = readAttributesMap(inst),
		})
	end
	return { results = out, count = #out }
end

handlers["tags"] = function(p)
	local action = p.action or "list_all"
	if action == "list_all" then
		local all = {}
		for _, tag in ipairs(CollectionService:GetAllTags()) do
			all[tag] = #CollectionService:GetTagged(tag)
		end
		return { tags = all }
	elseif action == "find_tagged" then
		local tag = tostring(p.tag or "")
		local limit = math.clamp(tonumber(p.limit) or 100, 1, 1000)
		local items = CollectionService:GetTagged(tag)
		local paths = {}
		for i = 1, math.min(#items, limit) do
			table.insert(paths, {
				path = fullName(items[i]),
				className = items[i].ClassName,
				name = items[i].Name,
			})
		end
		return { tag = tag, instances = paths, total = #items }
	end

	local paths = p.paths or (p.path and { p.path } or {})
	local tagList = p.tags or (p.tag and { p.tag } or {})
	local results = {}
	for _, path in ipairs(paths) do
		local inst = mustResolve(path)
		if action == "add" then
			for _, t in ipairs(tagList) do
				CollectionService:AddTag(inst, tostring(t))
			end
		elseif action == "remove" then
			for _, t in ipairs(tagList) do
				CollectionService:RemoveTag(inst, tostring(t))
			end
		end
		table.insert(results, {
			path = fullName(inst),
			tags = CollectionService:GetTags(inst),
		})
	end
	return { results = results }
end

handlers["selection"] = function(p)
	local action = (p and p.action) or "get"
	if action == "clear" then
		Selection:Set({})
	elseif action == "set" or action == "add" then
		local nextSel = action == "add" and Selection:Get() or {}
		for _, path in ipairs(p.paths or {}) do
			local inst = resolve(path)
			if inst then
				table.insert(nextSel, inst)
			end
		end
		Selection:Set(nextSel)
	end
	local out = {}
	for _, inst in ipairs(Selection:Get()) do
		table.insert(out, {
			path = fullName(inst),
			name = inst.Name,
			className = inst.ClassName,
		})
	end
	return { selection = out, count = #out }
end

handlers["camera"] = function(p)
	local cam = workspace.CurrentCamera
	if not cam then
		error("CurrentCamera is nil", 0)
	end
	local action = p.action or "get"
	if action == "focus" then
		local target = mustResolve(p.path)
		local pivot = CFrame.new(0, 10, 0)
		local size = Vector3.new(8, 8, 8)
		if target:IsA("Model") then
			pivot = target:GetPivot()
			size = target:GetExtentsSize()
		elseif target:IsA("BasePart") then
			pivot = target.CFrame
			size = target.Size
		end
		local dist = math.max(12, size.Magnitude * (tonumber(p.distanceMultiplier) or 1.6))
		local camPos = pivot.Position + Vector3.new(dist * 0.65, dist * 0.5, dist * 0.65)
		cam.CFrame = CFrame.lookAt(camPos, pivot.Position)
		cam.Focus = pivot
	elseif action == "set" then
		if p.fov then
			cam.FieldOfView = tonumber(p.fov) or cam.FieldOfView
		end
		if p.position then
			local pos = Vector3.new(p.position[1], p.position[2], p.position[3])
			if p.lookAt then
				local look = Vector3.new(p.lookAt[1], p.lookAt[2], p.lookAt[3])
				cam.CFrame = CFrame.lookAt(pos, look)
				cam.Focus = CFrame.new(look)
			else
				cam.CFrame = CFrame.new(pos)
			end
		elseif p.cframe then
			cam.CFrame = coerce({ __t = "CFrame", v = p.cframe })
		end
	end
	return {
		cframe = serializeValue(cam.CFrame),
		focus = serializeValue(cam.Focus),
		fov = cam.FieldOfView,
		viewportSize = serializeValue(cam.ViewportSize),
	}
end

handlers["history"] = function(p)
	local action = p.action or "status"
	if action == "undo" then
		local steps = math.clamp(tonumber(p.steps) or 1, 1, 20)
		for _ = 1, steps do
			if ChangeHistoryService:GetCanUndo() then
				ChangeHistoryService:Undo()
			end
		end
	elseif action == "redo" then
		local steps = math.clamp(tonumber(p.steps) or 1, 1, 20)
		for _ = 1, steps do
			if ChangeHistoryService:GetCanRedo() then
				ChangeHistoryService:Redo()
			end
		end
	elseif action == "waypoint" then
		ChangeHistoryService:SetWaypoint(tostring(p.name or "Arena Waypoint"))
	end
	local canUndo, undoName = ChangeHistoryService:GetCanUndo()
	local canRedo, redoName = ChangeHistoryService:GetCanRedo()
	return {
		action = action,
		canUndo = canUndo,
		nextUndo = undoName,
		canRedo = canRedo,
		nextRedo = redoName,
	}
end

handlers["terrain"] = function(p)
	local terrain = workspace.Terrain
	local action = p.action or "materials"
	if action == "materials" then
		local mats = {}
		for _, item in ipairs(Enum.Material:GetEnumItems()) do
			table.insert(mats, item.Name)
		end
		table.sort(mats)
		return { materials = mats, count = #mats }
	elseif action == "clear" then
		if p.region then
			local c = p.region.center or { 0, 0, 0 }
			local s = p.region.size or { 64, 64, 64 }
			terrain:FillBlock(CFrame.new(c[1], c[2], c[3]), Vector3.new(s[1], s[2], s[3]), Enum.Material.Air)
			return { cleared = "region" }
		end
		if not p.confirmClearAll then
			error("set confirmClearAll=true to wipe all Workspace.Terrain", 0)
		end
		terrain:Clear()
		return { cleared = "all" }
	end

	local matName = tostring(p.material or "Grass")
	local matItem = (Enum.Material :: any)[matName] or Enum.Material.Grass
	local pos = p.position or { 0, 0, 0 }
	local rot = p.rotation or { 0, 0, 0 }
	local cf = CFrame.new(pos[1], pos[2], pos[3])
		* CFrame.Angles(math.rad(rot[1]), math.rad(rot[2]), math.rad(rot[3]))

	if action == "fill_block" then
		local sz = p.size or { 32, 8, 32 }
		terrain:FillBlock(cf, Vector3.new(sz[1], sz[2], sz[3]), matItem)
	elseif action == "fill_ball" then
		local radius = tonumber(p.radius) or 16
		terrain:FillBall(Vector3.new(pos[1], pos[2], pos[3]), radius, matItem)
	elseif action == "fill_cylinder" then
		local height = tonumber(p.height) or 16
		local radius = tonumber(p.radius) or 12
		terrain:FillCylinder(cf, height, radius, matItem)
	elseif action == "fill_wedge" then
		local sz = p.size or { 16, 16, 16 }
		terrain:FillWedge(cf, Vector3.new(sz[1], sz[2], sz[3]), matItem)
	elseif action == "replace" then
		local srcMat = (Enum.Material :: any)[tostring(p.sourceMaterial or "Grass")]
		local dstMat = (Enum.Material :: any)[tostring(p.targetMaterial or "Rock")]
		local sz = p.size or { 128, 64, 128 }
		local half = Vector3.new(sz[1] / 2, sz[2] / 2, sz[3] / 2)
		local center = Vector3.new(pos[1], pos[2], pos[3])
		local region = Region3.new(center - half, center + half):ExpandToGrid(4)
		terrain:ReplaceMaterial(region, 4, srcMat, dstMat)
	else
		error("unknown terrain action: " .. tostring(action), 0)
	end
	return { action = action, material = matItem.Name }
end

handlers["insert_asset"] = function(p)
	local assetId = tonumber(p.assetId)
	if not assetId then
		error("numeric assetId is required", 0)
	end
	local parent = mustResolve(p.parent or "Workspace")
	local stripScripts = p.stripScripts ~= false

	local loaded: { Instance } = {}
	local okGetObj, objs = pcall(function()
		return game:GetObjects("rbxassetid://" .. tostring(assetId))
	end)
	if okGetObj and objs and #objs > 0 then
		loaded = objs
	else
		local okIns, container = pcall(function()
			return InsertService:LoadAsset(assetId)
		end)
		if not okIns or not container then
			error("failed to load asset " .. tostring(assetId) .. ": " .. tostring(container or objs), 0)
		end
		loaded = container:GetChildren()
	end

	local createdPaths = {}
	local embeddedScripts = {}
	for _, root in ipairs(loaded) do
		local toCheck = { root }
		for _, d in ipairs(root:GetDescendants()) do
			table.insert(toCheck, d)
		end
		for _, item in ipairs(toCheck) do
			if item:IsA("LuaSourceContainer") then
				table.insert(embeddedScripts, {
					name = item.Name,
					className = item.ClassName,
					bytes = #readScriptSource(item),
					stripped = stripScripts,
				})
				if stripScripts and item ~= root then
					item:Destroy()
				end
			end
		end
		if p.name and #loaded == 1 then
			root.Name = tostring(p.name)
		end
		if p.position and root:IsA("PVInstance") then
			root:PivotTo(CFrame.new(p.position[1], p.position[2], p.position[3]))
		end
		root.Parent = parent
		table.insert(createdPaths, fullName(root))
	end

	return {
		assetId = assetId,
		inserted = createdPaths,
		embeddedScriptsFound = #embeddedScripts,
		scriptsStripped = stripScripts,
		embeddedScripts = embeddedScripts,
	}
end

handlers["playtest"] = function(p)
	if not okSTS or not StudioTestService then
		error("StudioTestService is unavailable in this Studio build", 0)
	end
	local action = p.action or "status"
	if action == "status" then
		return {
			dm = DM_MODE,
			isRunning = RunService:IsRunning(),
			playtestActive = activePlaytestRunning,
			diedReason = playtestDiedReason,
			lastResult = lastPlaytestResult,
		}
	elseif action == "stop" then
		local okEnd, endErr = pcall(function()
			StudioTestService:EndTest(p.reason or "stopped_by_mcp")
		end)
		activePlaytestRunning = false
			if not okEnd then playtestDiedReason = tostring(endErr) end
		return {
			stopped = okEnd,
			detail = okEnd and "EndTest called" or tostring(endErr),
			dm = DM_MODE,
		}
	elseif action == "start_play" or action == "start_run" then
		if DM_MODE ~= "edit" then
			return { started = false, alreadyRunning = true, dm = DM_MODE }
		end
		if activePlaytestRunning then
			return { started = false, alreadyRunning = true, dm = DM_MODE }
		end
		activePlaytestRunning = true
		lastPlaytestResult = nil
		playtestDiedReason = nil
		local testArgs = p.args or { source = "ArenaBridgeMCP", startedAt = os.time() }
		task.spawn(function()
			local okRun, res = pcall(function()
				if action == "start_play" then
					return StudioTestService:ExecutePlayModeAsync(testArgs)
				else
					return StudioTestService:ExecuteRunModeAsync(testArgs)
				end
			end)
			activePlaytestRunning = false
			if not okRun then playtestDiedReason = tostring(res) end
			lastPlaytestResult = {
				ok = okRun,
				result = serializeValue(res),
				finishedAt = os.time(),
			}
		end)
		return {
			started = true,
			mode = action == "start_play" and "play" or "run",
			dm = DM_MODE,
		}
	end
	error("unknown playtest action: " .. tostring(action), 0)
end

------------------------------------------------------------------------------
-- survey: read the whole place in one job
------------------------------------------------------------------------------

handlers["survey"] = function(p)
	local budget = tonumber(p.maxScriptBytes) or 120000
	local perScript = tonumber(p.maxBytesPerScript) or 14000
	local includeSource = p.includeSource ~= false
	local visitCap = tonumber(p.visitCap) or 40000

	local census = {}
	local scriptInsts = {}
	local remotes = {}
	local visited = 0
	local hitCap = false
	local serviceStats = {}

	local function walk(root)
		local seen = 0
		local stack = { root }
		while #stack > 0 do
			local inst = table.remove(stack)
			if visited >= visitCap then
				hitCap = true
				break
			end
			visited += 1
			seen += 1

			local cls = inst.ClassName
			census[cls] = (census[cls] or 0) + 1

			if inst:IsA("LuaSourceContainer") then
				table.insert(scriptInsts, inst)
			elseif inst:IsA("RemoteEvent") or inst:IsA("RemoteFunction")
				or inst:IsA("BindableEvent") or inst:IsA("BindableFunction")
				or cls == "UnreliableRemoteEvent" then
				table.insert(remotes, { path = fullName(inst), className = cls })
			end

			for _, child in ipairs(inst:GetChildren()) do
				table.insert(stack, child)
			end
		end
		return seen
	end

	for _, name in ipairs(SURVEY_SERVICES) do
		local ok, service = pcall(function()
			return game:GetService(name)
		end)
		if ok and service then
			local count = walk(service)
			table.insert(serviceStats, {
				name = name,
				descendants = count - 1,
				children = #service:GetChildren(),
			})
		end
	end

	table.sort(scriptInsts, function(a, b)
		return #readScriptSource(a) < #readScriptSource(b)
	end)

	local scripts = {}
	local spent = 0
	local withheld = 0
	for _, inst in ipairs(scriptInsts) do
		local src = readScriptSource(inst)
		local _, newlines = string.gsub(src, "\n", "\n")

		local entry: any = {
			path = fullName(inst),
			className = inst.ClassName,
			bytes = #src,
			lines = newlines + 1,
			hash = fnvHex(src),
		}
		local okRun, runContext = pcall(function()
			return (inst :: any).RunContext
		end)
		if okRun and runContext then
			entry.runContext = runContext.Name
		end
		local okDis, disabled = pcall(function()
			return (inst :: any).Disabled
		end)
		if okDis and disabled then
			entry.disabled = true
		end

		if includeSource and spent < budget then
			local slice = src
			if #slice > perScript then
				slice = string.sub(slice, 1, perScript)
				entry.truncated = true
			end
			if spent + #slice > budget then
				slice = string.sub(slice, 1, math.max(0, budget - spent))
				entry.truncated = true
			end
			entry.source = slice
			spent += #slice
		else
			entry.sourceWithheld = true
			withheld += 1
		end
		table.insert(scripts, entry)
	end

	local models = {}
	for _, child in ipairs(workspace:GetChildren()) do
		if child:IsA("Model") or child:IsA("Folder") then
			local parts = 0
			for _, d in ipairs(child:GetDescendants()) do
				if d:IsA("BasePart") then
					parts += 1
				end
			end
			local entry: any = { name = child.Name, className = child.ClassName, parts = parts }
			local okExt, extents = pcall(function()
				return (child :: any):GetExtentsSize()
			end)
			if okExt and extents then
				entry.size = string.format("%.0f x %.0f x %.0f",
					extents.X, extents.Y, extents.Z)
			end
			table.insert(models, entry)
			if #models >= 60 then
				break
			end
		end
	end

	local guis = {}
	local okGui, starterGui = pcall(function()
		return game:GetService("StarterGui")
	end)
	if okGui and starterGui then
		for _, child in ipairs(starterGui:GetChildren()) do
			table.insert(guis, {
				name = child.Name,
				className = child.ClassName,
				descendants = #child:GetDescendants(),
			})
		end
	end

	local tags = {}
	local okTags, allTags = pcall(function()
		return CollectionService:GetAllTags()
	end)
	if okTags and allTags then
		for _, tag in ipairs(allTags) do
			local okGet, tagged = pcall(function()
				return CollectionService:GetTagged(tag)
			end)
			tags[tag] = okGet and #tagged or 0
		end
	end

	local lighting = game:GetService("Lighting")
	local okTerrain, terrain = pcall(function()
		return workspace.Terrain
	end)
	local okTech, tech = pcall(function()
		return lighting.Technology
	end)

	return {
		place = {
			name = game.Name,
			placeId = game.PlaceId,
			gameId = game.GameId,
		},
		dm = DM_MODE,
		pluginVersion = PLUGIN_VERSION,
		services = serviceStats,
		census = census,
		scripts = scripts,
		remotes = remotes,
		workspaceTopLevel = models,
		gui = guis,
		tags = tags,
		settings = {
			gravity = tostring(workspace.Gravity),
			streamingEnabled = tostring(workspace.StreamingEnabled),
			clockTime = tostring(lighting.ClockTime),
			ambient = tostring(lighting.Ambient),
			technology = okTech and tostring(tech) or "n/a (plugin thread)",
			hasTerrain = okTerrain and terrain ~= nil,
		},
		totals = {
			instancesVisited = visited,
			scripts = #scriptInsts,
			sourceBytesReturned = spent,
			scriptsWithheld = withheld,
			visitCapHit = hitCap,
		},
	}
end

handlers["console"] = function(p)
	local limit = math.clamp(tonumber(p.limit) or 120, 1, 2000)
	local sinceIndex = tonumber(p.sinceIndex) or 0
	local levelFilter = p.level and string.lower(tostring(p.level)) or nil
	local searchFilter = p.search and string.lower(tostring(p.search)) or nil
	local sourceFilter = string.lower(tostring(p.source or "game"))

	local okHist, history = pcall(function()
		return LogService:GetLogHistory()
	end)
	if not okHist or not history then
		return { lines = {}, total = 0, nextIndex = 0, error = "log history unavailable" }
	end

	local matched = {}
	local startAt = math.max(1, sinceIndex + 1)
	for i = startAt, #history do
		local entry = history[i]
		local msgType = entry.messageType and entry.messageType.Name or "Output"
		local msg = tostring(entry.message or "")
		local keep = true
		local lowerMsg = string.lower(msg)
		local isBridge = string.sub(lowerMsg, 1, 6) == "[arena" or string.find(lowerMsg, "arenabridge", 1, true) ~= nil
		if sourceFilter == "game" and isBridge then keep = false end
		if sourceFilter == "bridge" and not isBridge then keep = false end
		if levelFilter and levelFilter ~= "" and levelFilter ~= "all" then
			if not string.find(string.lower(msgType), levelFilter, 1, true) then
				keep = false
			end
		end
		if keep and searchFilter and searchFilter ~= "" then
			if not string.find(string.lower(msg), searchFilter, 1, true) then
				keep = false
			end
		end
		if keep then
			table.insert(matched, {
				index = i,
				type = msgType,
				message = msg,
				timestamp = entry.timestamp,
			})
		end
	end

	local out = {}
	local first = math.max(1, #matched - limit + 1)
	for i = first, #matched do
		table.insert(out, matched[i])
	end

	return {
		lines = out,
		returned = #out,
		total = #matched,
		totalHistory = #history,
		nextIndex = #history,
		dm = DM_MODE,
	}
end

------------------------------------------------------------------------------
-- Direct Image Import (Local `rbxasset://`, `EditableImage` RGBA, Cloud Publish)
------------------------------------------------------------------------------

local B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
local B64_DEC: { [number]: number } = {}
for idx = 1, #B64_ALPHABET do
	B64_DEC[string.byte(B64_ALPHABET, idx)] = idx - 1
end

local function decodeBase64ToBuffer(b64: string, expectedBytes: number): buffer
	local buf = buffer.create(expectedBytes)
	local outIdx = 0
	local acc = 0
	local vals = 0
	local n = #b64
	for i = 1, n do
		local c = B64_DEC[string.byte(b64, i)]
		if c ~= nil then
			acc = acc * 64 + c
			vals = vals + 1
			if vals == 4 then
				local b1 = math.floor(acc / 65536) % 256
				local b2 = math.floor(acc / 256) % 256
				local b3 = acc % 256
				if outIdx < expectedBytes then
					buffer.writeu8(buf, outIdx, b1)
					outIdx = outIdx + 1
				end
				if outIdx < expectedBytes then
					buffer.writeu8(buf, outIdx, b2)
					outIdx = outIdx + 1
				end
				if outIdx < expectedBytes then
					buffer.writeu8(buf, outIdx, b3)
					outIdx = outIdx + 1
				end
				acc = 0
				vals = 0
			end
		end
	end
	if vals == 3 and outIdx < expectedBytes then
		local t = acc * 64
		buffer.writeu8(buf, outIdx, math.floor(t / 65536) % 256)
		outIdx = outIdx + 1
		if outIdx < expectedBytes then
			buffer.writeu8(buf, outIdx, math.floor(t / 256) % 256)
		end
	elseif vals == 2 and outIdx < expectedBytes then
		local t = acc * 4096
		buffer.writeu8(buf, outIdx, math.floor(t / 65536) % 256)
	end
	return buf
end

local function applyImageToGuiOrSurface(inst: Instance, finalUri: string, editable: any, preferEditable: boolean)
	if inst:IsA("ImageLabel") or inst:IsA("ImageButton") then
		if editable ~= nil and (preferEditable or finalUri == "") then
			pcall(function()
				(inst :: any).ImageContent = Content.fromObject(editable)
			end)
		end
		if finalUri ~= "" and not preferEditable then
			(inst :: any).Image = finalUri
		end
	elseif inst:IsA("Decal") or inst:IsA("Texture") or inst:IsA("ParticleEmitter") then
		if editable ~= nil and (preferEditable or finalUri == "") and inst:IsA("Decal") then
			pcall(function()
				(inst :: any).ColorMapContent = Content.fromObject(editable)
			end)
		end
		if finalUri ~= "" and not preferEditable then
			(inst :: any).Texture = finalUri
		end
	elseif inst:IsA("MeshPart") then
		if editable ~= nil and (preferEditable or finalUri == "") then
			pcall(function()
				(inst :: any).TextureContent = Content.fromObject(editable)
			end)
		elseif finalUri ~= "" then
			(inst :: any).TextureID = finalUri
		end
	end
end

handlers["import_image"] = function(p)
	local AssetService = game:GetService("AssetService")
	local finalUri = tostring(p.uri or "")
	local preferEditable = p.preferEditable == true
	local editable: any = nil
	local publishedAssetId: any = nil
	local publishWarning: string? = nil

	if type(p.rgbaBase64) == "string" and #p.rgbaBase64 > 0 then
		local w = math.clamp(math.floor(tonumber(p.width) or 64), 1, 1024)
		local h = math.clamp(math.floor(tonumber(p.height) or 64), 1, 1024)
		local pixBuf = decodeBase64ToBuffer(p.rgbaBase64, w * h * 4)
		local okEd, createdEd = pcall(function()
			local ed = AssetService:CreateEditableImage({ Size = Vector2.new(w, h) })
			ed:WritePixelsBuffer(Vector2.new(0, 0), Vector2.new(w, h), pixBuf)
			return ed
		end)
		if okEd and createdEd then
			editable = createdEd
			if p.publishStudioAsset == true then
				local okPub, statusOrErr, newAssetId = pcall(function()
					return AssetService:CreateAssetAsync(editable, Enum.AssetType.Image, {
						Name = tostring(p.displayName or "ImportedImage"),
						Description = tostring(p.description or "Imported via Studio MCP"),
					})
				end)
				if okPub and newAssetId then
					publishedAssetId = newAssetId
					finalUri = "rbxassetid://" .. tostring(newAssetId)
					preferEditable = false
				else
					publishWarning = tostring(statusOrErr)
				end
			end
		else
			publishWarning = tostring(createdEd)
		end
	end

	local appliedTo: string? = nil
	if type(p.targetPath) == "string" and p.targetPath ~= "" then
		local target = mustResolve(p.targetPath)
		local mode = tostring(p.applyMode or "auto")
		local instName = tostring(p.instanceName or "ImportedImage")
		local faceEnum = (Enum.NormalId :: any)[tostring(p.face or "Front")] or Enum.NormalId.Front

		if mode == "decal" or (mode == "auto" and target:IsA("BasePart")) then
			local existing = target:FindFirstChild(instName)
			local decal = (existing and existing:IsA("Decal")) and existing or Instance.new("Decal")
			decal.Name = instName
			decal.Face = faceEnum
			decal.Parent = target
			applyImageToGuiOrSurface(decal, finalUri, editable, preferEditable)
			appliedTo = fullName(decal)
		elseif mode == "texture" then
			local existing = target:FindFirstChild(instName)
			local tex = (existing and existing:IsA("Texture")) and existing or Instance.new("Texture")
			tex.Name = instName
			tex.Face = faceEnum
			tex.StudsPerTileU = tonumber(p.studsPerTileU) or 4
			tex.StudsPerTileV = tonumber(p.studsPerTileV) or 4
			tex.Parent = target
			applyImageToGuiOrSurface(tex, finalUri, editable, preferEditable)
			appliedTo = fullName(tex)
		elseif mode == "billboard" then
			local existing = target:FindFirstChild(instName)
			if existing then
				existing:Destroy()
			end
			local bb = Instance.new("BillboardGui")
			bb.Name = instName
			bb.Size = UDim2.new(0, tonumber(p.pixelWidth) or 128, 0, tonumber(p.pixelHeight) or 128)
			bb.StudsOffset = Vector3.new(0, tonumber(p.studsOffsetY) or 4, 0)
			bb.AlwaysOnTop = true
			bb.Parent = target
			local lbl = Instance.new("ImageLabel")
			lbl.Name = "Icon"
			lbl.Size = UDim2.new(1, 0, 1, 0)
			lbl.BackgroundTransparency = 1
			lbl.ScaleType = Enum.ScaleType.Fit
			lbl.Parent = bb
			applyImageToGuiOrSurface(lbl, finalUri, editable, preferEditable)
			appliedTo = fullName(lbl)
		elseif mode == "surface_gui" then
			local existing = target:FindFirstChild(instName)
			if existing then
				existing:Destroy()
			end
			local sg = Instance.new("SurfaceGui")
			sg.Name = instName
			sg.Face = faceEnum
			sg.SizingMode = Enum.SurfaceGuiSizingMode.PixelsPerStud
			sg.PixelsPerStud = 64
			sg.Parent = target
			local lbl = Instance.new("ImageLabel")
			lbl.Name = "Poster"
			lbl.Size = UDim2.new(1, 0, 1, 0)
			lbl.BackgroundTransparency = 1
			lbl.ScaleType = Enum.ScaleType.Fit
			lbl.Parent = sg
			applyImageToGuiOrSurface(lbl, finalUri, editable, preferEditable)
			appliedTo = fullName(lbl)
		elseif mode == "screen_gui" or (mode == "auto" and target == game:GetService("StarterGui")) then
			local guiParent = target:IsA("ScreenGui") and target or (function()
				local found = target:FindFirstChild("ImportedImagesGui")
				if found and found:IsA("ScreenGui") then
					return found
				end
				local g = Instance.new("ScreenGui")
				g.Name = "ImportedImagesGui"
				g.ResetOnSpawn = false
				g.Parent = target
				return g
			end)()
			local old = guiParent:FindFirstChild(instName)
			if old then
				old:Destroy()
			end
			local lbl = Instance.new("ImageLabel")
			lbl.Name = instName
			lbl.AnchorPoint = Vector2.new(0.5, 0.5)
			lbl.Position = UDim2.new(0.5, 0, 0.5, 0)
			lbl.Size = UDim2.new(0, tonumber(p.pixelWidth) or 192, 0, tonumber(p.pixelHeight) or 192)
			lbl.BackgroundTransparency = 1
			lbl.ScaleType = Enum.ScaleType.Fit
			lbl.Parent = guiParent
			applyImageToGuiOrSurface(lbl, finalUri, editable, preferEditable)
			appliedTo = fullName(lbl)
		else
			applyImageToGuiOrSurface(target, finalUri, editable, preferEditable)
			appliedTo = fullName(target)
		end
	end

	return {
		uri = finalUri,
		editableCreated = editable ~= nil,
		publishedAssetId = publishedAssetId,
		appliedTo = appliedTo,
		warning = publishWarning,
	}
end

------------------------------------------------------------------------------
-- Read-only classification & ChangeHistoryService wrapper
------------------------------------------------------------------------------

local READ_ONLY = {
	ping = true,
	studio_state = true,
	inspect = true,
	search_tree = true,
	compare_instances = true,
	get_properties = true,
	read_script = true,
	read_scripts = true,
	hash_scripts = true,
	script_search = true,
	script_grep = true,
	survey = true,
	console = true,
	require_fresh = true,
	playtest = true,
	history = true,
}

local function isJobReadOnly(job: any): boolean
	if READ_ONLY[job.type] then
		return true
	end
	local p = job.payload or {}
	if job.type == "selection" and ((p.action or "get") == "get") then
		return true
	end
	if job.type == "camera" and ((p.action or "get") == "get") then
		return true
	end
	if job.type == "attributes" and ((p.action or "get") == "get") then
		return true
	end
	if job.type == "tags" then
		local act = p.action or "list_all"
		if act == "list_all" or act == "find_tagged" or act == "get" then
			return true
		end
	end
	if job.type == "open_script" and p.listOnly then
		return true
	end
	if job.type == "find_replace_scripts" and p.dryRun == true then
		return true
	end
	return false
end

local function forwardJobToClient(job: any): (boolean, any, string?)
	-- Legacy single-VM Play Solo runs Server + Client in the same VM
	if RunService:IsClient() then
		local copyPayload = {}
		for k, v in pairs(job.payload or {}) do
			if k ~= "_targetDm" then
				copyPayload[k] = v
			end
		end
		local localJob = { id = job.id, type = job.type, payload = copyPayload }
		local handler = handlers[localJob.type]
		if not handler then
			return false, nil, "unknown job type: " .. tostring(localJob.type)
		end
		local ok, res = pcall(handler, localJob.payload)
		if ok then
			return true, res, nil
		end
		return false, nil, tostring(res)
	end

	if not clientRpc then
		return false, nil, "client RPC bridge is not initialized on server DataModel"
	end

	local targetPlayer: Player? = nil
	local wantName = job.payload and job.payload._playerName
	if wantName and wantName ~= "" then
		targetPlayer = Players:FindFirstChild(tostring(wantName)) :: Player?
	else
		local list = Players:GetPlayers()
		targetPlayer = list[1]
	end
	if not targetPlayer then
		return false, nil, "no connected Player client in Play session yet"
	end

	local done = false
	local rpcOk = false
	local rpcResp: any = nil

	task.spawn(function()
		rpcOk, rpcResp = pcall(function()
			return (clientRpc :: RemoteFunction):InvokeClient(targetPlayer :: Player, {
				id = job.id,
				type = job.type,
				payload = job.payload or {},
			})
		end)
		done = true
	end)

	local deadline = os.clock() + (tonumber(job.payload and job.payload._clientTimeout) or 20)
	while not done and os.clock() < deadline do
		task.wait(0.05)
	end

	if not done then
		return false, nil, "client RPC timed out waiting for client DataModel"
	end
	if not rpcOk or type(rpcResp) ~= "table" then
		return false, nil, "client RPC error: " .. tostring(rpcResp)
	end
	for _, line in ipairs(rpcResp.logs or {}) do
		table.insert(logs, "[client] " .. tostring(line))
	end
	return rpcResp.ok == true, rpcResp.result, rpcResp.error
end

local function runJob(job: any): (boolean, any, string?)
	if DM_MODE == "server" and job.payload and job.payload._targetDm == "client" then
		return forwardJobToClient(job)
	end

	local handler = handlers[job.type]
	if not handler then
		return false, nil, "unknown job type: " .. tostring(job.type)
	end

	if isJobReadOnly(job) or DM_MODE ~= "edit" then
		local ok, result = pcall(handler, job.payload or {})
		if ok then
			return true, result, nil
		end
		return false, nil, tostring(result)
	end

	-- Edit-mode mutating job: single atomic Ctrl+Z undo recording
	local recording = ChangeHistoryService:TryBeginRecording(
		"Arena: " .. job.type,
		"Arena " .. job.type
	)
	local ok, result = pcall(handler, job.payload or {})
	if recording then
		ChangeHistoryService:FinishRecording(
			recording,
			ok and Enum.FinishRecordingOperation.Commit
				or Enum.FinishRecordingOperation.Cancel
		)
	end
	if ok then
		return true, result, nil
	end
	return false, nil, tostring(result)
end

------------------------------------------------------------------------------
-- Networking
------------------------------------------------------------------------------

local function endpoint(path: string, extra: string?): string
	local base = string.gsub(BRIDGE_URL, "/+$", "")
	local url = base .. path
	local query = {}
	if BRIDGE_TOKEN ~= "" then
		table.insert(query, "token=" .. HttpService:UrlEncode(BRIDGE_TOKEN))
	end
	if extra then
		table.insert(query, extra)
	end
	if #query > 0 then
		url = url .. "?" .. table.concat(query, "&")
	end
	return url
end

local function postResult(job: any, ok: boolean, result: any, err: string?)
	local body = HttpService:JSONEncode({
		id = job.id,
		ok = ok,
		result = result,
		error = err,
		logs = logs,
		peerId = PEER_ID,
		dm = DM_MODE,
	})
	local sent, sendErr = pcall(function()
		return HttpService:RequestAsync({
			Url = endpoint("/api/result"),
			Method = "POST",
			Headers = { ["content-type"] = "application/json" },
			Body = body,
		})
	end)
	if not sent then
		log("failed to post result: %s", tostring(sendErr))
	end
end

local function pollOnce()
	local hasClient = (DM_MODE == "server" and (#Players:GetPlayers() > 0 or RunService:IsClient())) and "1" or "0"
	local okVer, ver = pcall(function()
		return version()
	end)
	local extra = table.concat({
		"place=" .. HttpService:UrlEncode(game.Name),
		"placeId=" .. tostring(game.PlaceId),
		"client=studio",
		"peer=" .. HttpService:UrlEncode(PEER_ID),
		"dm=" .. DM_MODE,
		"hasClient=" .. hasClient,
		"players=" .. tostring(#Players:GetPlayers()),
		"pv=" .. PLUGIN_VERSION,
		"sv=" .. HttpService:UrlEncode(okVer and ver or "unknown"),
	}, "&")

	local ok, response = pcall(function()
		return HttpService:RequestAsync({
			Url = endpoint("/api/poll", extra),
			Method = "GET",
		})
	end)

	if not ok then
		task.wait(3)
		return
	end
	if not response.Success then
		log("poll HTTP %d", response.StatusCode)
		task.wait(3)
		return
	end

	local decodedOk, decoded = pcall(function()
		return HttpService:JSONDecode(response.Body)
	end)
	if not decodedOk or not decoded.job then
		return
	end

	local job = decoded.job
	logs = {}
	log("job %s (%s) [%s]", job.id, job.type, DM_MODE)
	local success, result, err = runJob(job)
	if success then
		log("job %s ok", job.id)
	else
		log("job %s failed: %s", job.id, tostring(err))
	end
	postResult(job, success, result, err)
end

local function loop()
	while running do
		pollOnce()
		if running then
			task.wait(POLL_INTERVAL)
		end
	end
end

local function setRunning(state: boolean)
	running = state
	if button then
		button:SetActive(state)
	end
	if state then
		if BRIDGE_URL == "" then
			warn("[Arena] Set BRIDGE_URL at the top of ArenaBridge.lua first.")
			running = false
			if button then
				button:SetActive(false)
			end
			return
		end
		log("connected to %s (%s peer %s, plugin v%s)", BRIDGE_URL, DM_MODE, PEER_ID, PLUGIN_VERSION)
		task.spawn(loop)
	else
		log("disconnected (%s)", DM_MODE)
	end
end

if button then
	button.Click:Connect(function()
		setRunning(not running)
	end)
end

plugin.Unloading:Connect(function()
	running = false
	if clientRpc then
		pcall(function()
			clientRpc:Destroy()
		end)
		clientRpc = nil
	end
end)

------------------------------------------------------------------------------
-- Startup per DataModel mode
------------------------------------------------------------------------------

if DM_MODE == "client" then
	-- Client DataModels in Play Solo cannot issue HttpService requests.
	-- Instead of failing HTTP polls, attach silently to the transient RPC
	-- created by the Play Server peer so client-side MCP queries work cleanly.
	task.spawn(function()
		local rpc = ReplicatedStorage:WaitForChild(CLIENT_RPC_NAME, 30)
		if rpc and rpc:IsA("RemoteFunction") then
			(rpc :: RemoteFunction).OnClientInvoke = function(job: any)
				logs = {}
				local ok, res, err = runJob(job)
				return {
					ok = ok,
					result = res,
					error = err,
					logs = logs,
				}
			end
		end
	end)
else
	if DM_MODE == "server" then
		local existingRpc = ReplicatedStorage:FindFirstChild(CLIENT_RPC_NAME)
		if existingRpc then
			existingRpc:Destroy()
		end
		local rf = Instance.new("RemoteFunction")
		rf.Name = CLIENT_RPC_NAME
		rf.Archivable = false
		rf.Parent = ReplicatedStorage
		clientRpc = rf
	end

	local persistedAuto = plugin:GetSetting("AutoConnect")
	local shouldConnect = if persistedAuto ~= nil then persistedAuto else AUTO_CONNECT
	if shouldConnect ~= false then
		setRunning(true)
	else
		log("ArenaBridge v%s loaded (auto-connect off). Click Arena MCP to connect.", PLUGIN_VERSION)
	end
end
