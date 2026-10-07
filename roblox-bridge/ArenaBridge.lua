--!nonstrict
--[[
	ArenaBridge — Roblox Studio local plugin
	Connects your open Studio place to the bridge server running on your machine.

	Install:
	  1. Run the bridge on your computer:  python3 server.py --token YOURTOKEN
	  2. Studio -> Plugins tab -> Plugins Folder
  3. Drop this file (ArenaBridge.lua) in that folder (or: python setup.py --install-only)
  4. Restart Studio. A new "Arena" toolbar button appears.
     v2+ auto-connects if server.py is already running — no click needed.

	The plugin polls the LOCAL bridge for jobs, runs them inside Studio, and posts
	results back. Roblox explicitly allows plugins to talk to localhost / 127.0.0.1,
	which is the same mechanism Rojo uses.

	Every mutation is wrapped in a ChangeHistoryService recording, so a single
	Ctrl+Z undoes a whole job.
]]

------------------------------------------------------------------------------
-- CONFIG
------------------------------------------------------------------------------
local BRIDGE_URL = "http://127.0.0.1:8077"  -- the bridge on YOUR machine
local BRIDGE_TOKEN = "arena-demo-7f3a"      -- must match the server's --token
local POLL_INTERVAL = 1.0                   -- seconds between polls when idle
local AUTO_CONNECT = true                   -- v2: connect on load if the bridge is up
local PLUGIN_VERSION = "2.0"
------------------------------------------------------------------------------

local HttpService = game:GetService("HttpService")
local ChangeHistoryService = game:GetService("ChangeHistoryService")
local CollectionService = game:GetService("CollectionService")
local LogService = game:GetService("LogService")
local Selection = game:GetService("Selection")
local ServerStorage = game:GetService("ServerStorage")

local toolbar = plugin:CreateToolbar("Arena")
local button = toolbar:CreateButton(
	"ArenaBridge",
	"Connect this place to your Arena agent",
	"",
	"Arena Bridge"
)
button.ClickableWhenViewportHidden = true

-- persisted settings so you do not have to edit the file every session
BRIDGE_URL = plugin:GetSetting("ArenaBridgeUrl") or BRIDGE_URL
BRIDGE_TOKEN = plugin:GetSetting("ArenaBridgeToken") or BRIDGE_TOKEN

local running = false
local logs: { string } = {}

local function log(fmt: string, ...: any)
	local msg = select("#", ...) > 0 and string.format(fmt, ...) or fmt
	table.insert(logs, msg)
	print("[Arena] " .. msg)
end

------------------------------------------------------------------------------
-- Instance path helpers:  "game.Workspace.Folder.Part" or "Workspace.Part"
------------------------------------------------------------------------------

local function resolve(path: string?): Instance?
	if not path or path == "" or path == "game" then
		return game
	end
	local node: Instance = game
	for segment in string.gmatch(path, "[^%.]+") do
		if segment == "game" then
			node = game
		else
			local ok, child = pcall(function()
				return (node :: any):FindFirstChild(segment)
			end)
			if not ok or not child then
				-- services may not exist as children until requested
				local okSvc, svc = pcall(function()
					return game:GetService(segment :: any)
				end)
				if okSvc and svc then
					child = svc
				else
					return nil
				end
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
	local ok, name = pcall(function()
		return inst:GetFullName()
	end)
	return ok and name or inst.Name
end

------------------------------------------------------------------------------
-- Property coercion: JSON -> Roblox datatypes
--   {"__t":"Vector3","v":[0,5,0]}  /  {"__t":"Color3","v":[1,0,0]}
--   {"__t":"CFrame","v":[x,y,z]}   /  {"__t":"Enum","v":"Material.Neon"}
--   {"__t":"UDim2","v":[0,100,0,50]}
------------------------------------------------------------------------------

local function coerce(value: any): any
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
		return Color3.new(v[1], v[2], v[3])
	elseif tag == "BrickColor" then
		return BrickColor.new(v)
	elseif tag == "CFrame" then
		if #v >= 12 then
			return CFrame.new(table.unpack(v))
		end
		return CFrame.new(v[1], v[2], v[3])
	elseif tag == "UDim2" then
		return UDim2.new(v[1], v[2], v[3], v[4])
	elseif tag == "UDim" then
		return UDim.new(v[1], v[2])
	elseif tag == "Enum" then
		local enumName, itemName = string.match(v, "^([%w_]+)%.([%w_]+)$")
		if enumName and itemName then
			return (Enum :: any)[enumName][itemName]
		end
		return v
	elseif tag == "Instance" then
		return resolve(v)
	end
	return value
end

local function applyProps(inst: Instance, props: { [string]: any }?)
	if not props then
		return
	end
	for key, raw in pairs(props) do
		local ok, err = pcall(function()
			(inst :: any)[key] = coerce(raw)
		end)
		if not ok then
			log("  ! could not set %s.%s (%s)", inst.Name, key, tostring(err))
		end
	end
end

------------------------------------------------------------------------------
-- Build a tree of instances from a JSON spec
------------------------------------------------------------------------------

local function buildNode(spec: any, parent: Instance): Instance
	local className = spec.className or spec.class or "Part"
	local inst: Instance

	if spec.replaceExisting and spec.name then
		local existing = parent:FindFirstChild(spec.name)
		if existing then
			existing:Destroy()
		end
	end

	inst = Instance.new(className)
	if spec.name then
		inst.Name = spec.name
	end
	applyProps(inst, spec.properties)

	if spec.source and (inst:IsA("LuaSourceContainer")) then
		(inst :: any).Source = spec.source
	end
	if spec.attributes then
		for k, v in pairs(spec.attributes) do
			inst:SetAttribute(k, coerce(v))
		end
	end

	inst.Parent = parent

	for _, childSpec in ipairs(spec.children or {}) do
		buildNode(childSpec, inst)
	end
	return inst
end

------------------------------------------------------------------------------
-- Luau execution inside Studio.
-- Prefers loadstring (available to local plugins); falls back to a temporary
-- ModuleScript + require, which runs under the same plugin identity.
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

	-- fallback path
	local module = Instance.new("ModuleScript")
	module.Name = "ArenaBridgeJob_" .. tostring(math.random(1e6, 9e6))
	module.Source = "return function()\n" .. code .. "\nend"
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

------------------------------------------------------------------------------
-- Describe the data model for the agent
------------------------------------------------------------------------------

local INTERESTING = {
	"ClassName", "Name", "Size", "Position", "Anchored", "CanCollide",
	"Material", "BrickColor", "Transparency", "Text", "Enabled", "Value",
}

local function describe(inst: Instance, depth: number): any
	local node: any = {
		name = inst.Name,
		className = inst.ClassName,
		path = fullName(inst),
	}
	local props: { [string]: any } = {}
	for _, key in ipairs(INTERESTING) do
		if key ~= "Name" and key ~= "ClassName" then
			local ok, value = pcall(function()
				return (inst :: any)[key]
			end)
			if ok and value ~= nil then
				props[key] = tostring(value)
			end
		end
	end
	if next(props) then
		node.properties = props
	end
	if inst:IsA("LuaSourceContainer") then
		local ok, src = pcall(function()
			return (inst :: any).Source
		end)
		node.sourceLines = ok and select(2, string.gsub(src, "\n", "\n")) + 1 or nil
	end
	if depth > 0 then
		local kids = {}
		for _, child in ipairs(inst:GetChildren()) do
			table.insert(kids, describe(child, depth - 1))
			if #kids >= 100 then
				break
			end
		end
		if #kids > 0 then
			node.children = kids
		end
	else
		node.childCount = #inst:GetChildren()
	end
	return node
end

------------------------------------------------------------------------------
-- Job handlers
------------------------------------------------------------------------------

local handlers: { [string]: (any) -> any } = {}

handlers["run_luau"] = function(p)
	local result = executeLuau(p.code)
	return { returned = result ~= nil and tostring(result) or "nil" }
end

handlers["build"] = function(p)
	local parent = mustResolve(p.parent or "Workspace")
	local created = {}
	for _, spec in ipairs(p.tree or { p }) do
		local inst = buildNode(spec, parent)
		table.insert(created, fullName(inst))
	end
	if p.select ~= false then
		local sel = {}
		for _, path in ipairs(created) do
			local inst = resolve(path)
			if inst then
				table.insert(sel, inst)
			end
		end
		Selection:Set(sel)
	end
	return { created = created }
end

handlers["write_script"] = function(p)
	local parent = mustResolve(p.parent or "ServerScriptService")
	local className = p.className or "Script"
	local name = p.name or "ArenaScript"
	local existing = parent:FindFirstChild(name)
	local target: Instance
	if existing and existing:IsA("LuaSourceContainer") then
		target = existing
	else
		if existing then
			existing:Destroy()
		end
		target = Instance.new(className)
		target.Name = name
		target.Parent = parent
	end
	(target :: any).Source = p.source or ""
	applyProps(target, p.properties)
	Selection:Set({ target })
	return { path = fullName(target), bytes = #(p.source or "") }
end

handlers["read_script"] = function(p)
	local inst = mustResolve(p.path)
	if not inst:IsA("LuaSourceContainer") then
		error("not a script: " .. fullName(inst), 0)
	end
	return { path = fullName(inst), source = (inst :: any).Source }
end

handlers["inspect"] = function(p)
	local inst = mustResolve(p.path or "game")
	return describe(inst, tonumber(p.depth) or 2)
end

handlers["set_properties"] = function(p)
	local inst = mustResolve(p.path)
	applyProps(inst, p.properties)
	return { path = fullName(inst) }
end

handlers["delete"] = function(p)
	local inst = mustResolve(p.path)
	local name = fullName(inst)
	inst:Destroy()
	return { deleted = name }
end

handlers["selection"] = function(_)
	local out = {}
	for _, inst in ipairs(Selection:Get()) do
		table.insert(out, { path = fullName(inst), className = inst.ClassName })
	end
	return { selection = out }
end

handlers["ping"] = function(_)
	local okVer, ver = pcall(function()
		return version()
	end)
	return {
		place = game.Name,
		placeId = game.PlaceId,
		studio = okVer and ver or "unknown",
		pluginVersion = PLUGIN_VERSION,
	}
end

------------------------------------------------------------------------------
-- survey: read the whole place in one job
------------------------------------------------------------------------------

local SURVEY_SERVICES = {
	"Workspace", "Players", "Lighting", "ReplicatedStorage", "ReplicatedFirst",
	"ServerScriptService", "ServerStorage", "StarterGui", "StarterPack",
	"StarterPlayer", "SoundService", "Teams", "TextChatService", "MaterialService",
}

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

	-- iterative walk (no recursion limits on deep hierarchies)
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

	-- scripts: metadata for all, source for as many as the budget allows
	table.sort(scriptInsts, function(a, b)
		local okA, srcA = pcall(function() return (a :: any).Source end)
		local okB, srcB = pcall(function() return (b :: any).Source end)
		return #(okA and srcA or "") < #(okB and srcB or "")
	end)

	local scripts = {}
	local spent = 0
	local withheld = 0
	for _, inst in ipairs(scriptInsts) do
		local okSrc, src = pcall(function()
			return (inst :: any).Source
		end)
		src = okSrc and src or ""
		local _, newlines = string.gsub(src, "\n", "\n")

		local entry: any = {
			path = fullName(inst),
			className = inst.ClassName,
			bytes = #src,
			lines = newlines + 1,
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

	-- top-level Workspace contents
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

	-- GUI
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

	-- CollectionService tags
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
	-- Known defect fixed in v2: Lighting.Technology throws
	-- "lacking capability RobloxScript" on a plugin thread. Guard it.
	local okTech, tech = pcall(function()
		return lighting.Technology
	end)

	return {
		place = {
			name = game.Name,
			placeId = game.PlaceId,
			gameId = game.GameId,
		},
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

handlers["read_scripts"] = function(p)
	local budget = tonumber(p.maxBytes) or 150000
	local spent = 0
	local out = {}
	for _, path in ipairs(p.paths or {}) do
		local inst = resolve(path)
		if inst and inst:IsA("LuaSourceContainer") then
			local okSrc, src = pcall(function()
				return (inst :: any).Source
			end)
			src = okSrc and src or ""
			local slice = src
			local truncated = false
			if spent + #slice > budget then
				slice = string.sub(slice, 1, math.max(0, budget - spent))
				truncated = true
			end
			spent += #slice
			table.insert(out, {
				path = fullName(inst),
				bytes = #src,
				source = slice,
				truncated = truncated,
			})
		else
			table.insert(out, { path = path, error = "not found or not a script" })
		end
	end
	return { scripts = out, bytesReturned = spent }
end

handlers["console"] = function(p)
	local limit = tonumber(p.limit) or 120
	local okHist, history = pcall(function()
		return LogService:GetLogHistory()
	end)
	if not okHist or not history then
		return { lines = {}, error = "log history unavailable" }
	end
	local out = {}
	local first = math.max(1, #history - limit + 1)
	for i = first, #history do
		local entry = history[i]
		table.insert(out, {
			type = entry.messageType and entry.messageType.Name or "Output",
			message = entry.message,
		})
	end
	return { lines = out, total = #history }
end

local READ_ONLY = {
	inspect = true, read_script = true, read_scripts = true,
	selection = true, ping = true, survey = true, console = true,
}

local function runJob(job: any): (boolean, any, string?)
	local handler = handlers[job.type]
	if not handler then
		return false, nil, "unknown job type: " .. tostring(job.type)
	end

	if READ_ONLY[job.type] then
		local ok, result = pcall(handler, job.payload)
		if ok then
			return true, result, nil
		end
		return false, nil, tostring(result)
	end

	-- mutating job: one undo step for the whole thing
	local recording = ChangeHistoryService:TryBeginRecording(
		"Arena: " .. job.type,
		"Arena " .. job.type
	)
	local ok, result = pcall(handler, job.payload)
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
	local extra = "place=" .. HttpService:UrlEncode(game.Name) .. "&client=studio"
	local ok, response = pcall(function()
		return HttpService:RequestAsync({
			Url = endpoint("/api/poll", extra),
			Method = "GET",
		})
	end)

	if not ok then
		log("poll failed: %s", tostring(response))
		task.wait(3)
		return
	end
	if not response.Success then
		log("poll HTTP %d", response.StatusCode)
		task.wait(3)
		return
	end

	local decoded
	local decodedOk
	decodedOk, decoded = pcall(function()
		return HttpService:JSONDecode(response.Body)
	end)
	if not decodedOk or not decoded.job then
		return
	end

	local job = decoded.job
	logs = {}
	log("job %s (%s)", job.id, job.type)
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
	button:SetActive(state)
	if state then
		if BRIDGE_URL == "" then
			warn("[Arena] Set BRIDGE_URL at the top of ArenaBridge.lua first.")
			running = false
			button:SetActive(false)
			return
		end
		log("connected to %s", BRIDGE_URL)
		task.spawn(loop)
	else
		log("disconnected")
	end
end

button.Click:Connect(function()
	setRunning(not running)
end)

plugin.Unloading:Connect(function()
	running = false
end)

------------------------------------------------------------------------------
-- v2: auto-connect on load, so a Studio restart no longer requires the
-- toolbar click. Safe even if server.py is not up yet: the poll loop
-- tolerates a down bridge (retries every few seconds) and latches on the
-- moment it starts. Multiple Studio windows are fine — the server hands a
-- job to exactly one poller.
-- Opt out (persisted): plugin:SetSetting("AutoConnect", false)
-- Toggle any time: click the Arena button.
------------------------------------------------------------------------------
local autoConnect = plugin:GetSetting("AutoConnect")
if autoConnect ~= false then
	setRunning(true)
else
	log("ArenaBridge loaded (auto-connect off). Click the Arena button to connect.")
end
