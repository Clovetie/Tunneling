-- Place recon, run via run_luau so no plugin reinstall is needed.
-- Every property read is pcall-guarded: Studio blocks some reads from plugins
-- (e.g. Lighting.Technology needs the RobloxScript capability).
local HttpService = game:GetService("HttpService")
local CollectionService = game:GetService("CollectionService")

local function safe(fn, default)
	local ok, value = pcall(fn)
	if ok and value ~= nil then
		return value
	end
	return default
end

local function fullName(inst)
	return safe(function()
		return inst:GetFullName()
	end, inst.Name)
end

local SERVICES = {
	"Workspace", "Players", "Lighting", "ReplicatedStorage", "ReplicatedFirst",
	"ServerScriptService", "ServerStorage", "StarterGui", "StarterPack",
	"StarterPlayer", "SoundService", "Teams", "TextChatService",
	"MaterialService", "Chat",
}

local census, scriptList, remotes, serviceStats = {}, {}, {}, {}
local visited, cap, hitCap = 0, 60000, false

for _, svcName in ipairs(SERVICES) do
	local svc = safe(function()
		return game:GetService(svcName)
	end, nil)
	if svc then
		local seen = 0
		local stack = { svc }
		while #stack > 0 do
			local inst = table.remove(stack)
			if visited >= cap then
				hitCap = true
				break
			end
			visited += 1
			seen += 1

			local cls = inst.ClassName
			census[cls] = (census[cls] or 0) + 1

			if inst:IsA("LuaSourceContainer") then
				local src = safe(function()
					return inst.Source
				end, "")
				local _, newlines = string.gsub(src, "\n", "\n")
				table.insert(scriptList, {
					path = fullName(inst),
					className = cls,
					bytes = #src,
					lines = newlines + 1,
					disabled = safe(function()
						return inst.Disabled
					end, false),
					runContext = safe(function()
						return inst.RunContext.Name
					end, nil),
				})
			elseif inst:IsA("RemoteEvent") or inst:IsA("RemoteFunction")
				or inst:IsA("BindableEvent") or inst:IsA("BindableFunction")
				or cls == "UnreliableRemoteEvent" then
				table.insert(remotes, { path = fullName(inst), className = cls })
			end

			for _, child in ipairs(inst:GetChildren()) do
				table.insert(stack, child)
			end
		end
		table.insert(serviceStats, {
			name = svcName,
			descendants = seen - 1,
			children = #svc:GetChildren(),
		})
	end
end

-- top level of Workspace
local topLevel = {}
for _, child in ipairs(workspace:GetChildren()) do
	local parts = 0
	for _, d in ipairs(safe(function()
		return child:GetDescendants()
	end, {})) do
		if d:IsA("BasePart") then
			parts += 1
		end
	end
	local entry = {
		name = child.Name,
		className = child.ClassName,
		parts = parts + (child:IsA("BasePart") and 1 or 0),
	}
	local extents = safe(function()
		return child:GetExtentsSize()
	end, nil)
	if extents then
		entry.size = string.format("%.0f x %.0f x %.0f", extents.X, extents.Y, extents.Z)
	end
	local pos = safe(function()
		return child:GetPivot().Position
	end, nil)
	if pos then
		entry.at = string.format("%.0f, %.0f, %.0f", pos.X, pos.Y, pos.Z)
	end
	table.insert(topLevel, entry)
	if #topLevel >= 80 then
		break
	end
end

-- StarterGui
local guis = {}
for _, child in ipairs(safe(function()
	return game:GetService("StarterGui"):GetChildren()
end, {})) do
	table.insert(guis, {
		name = child.Name,
		className = child.ClassName,
		descendants = #safe(function()
			return child:GetDescendants()
		end, {}),
	})
end

-- CollectionService tags
local tags = {}
for _, tag in ipairs(safe(function()
	return CollectionService:GetAllTags()
end, {})) do
	tags[tag] = #safe(function()
		return CollectionService:GetTagged(tag)
	end, {})
end

-- StarterPlayer children counts
local starterPlayer = {}
for _, child in ipairs(safe(function()
	return game:GetService("StarterPlayer"):GetChildren()
end, {})) do
	table.insert(starterPlayer, {
		name = child.Name,
		children = #child:GetChildren(),
	})
end

local lighting = game:GetService("Lighting")

return HttpService:JSONEncode({
	place = {
		name = game.Name,
		placeId = game.PlaceId,
		gameId = game.GameId,
		creatorId = safe(function()
			return game.CreatorId
		end, 0),
	},
	services = serviceStats,
	census = census,
	scripts = scriptList,
	remotes = remotes,
	workspaceTopLevel = topLevel,
	starterPlayer = starterPlayer,
	gui = guis,
	tags = tags,
	settings = {
		gravity = safe(function()
			return tostring(workspace.Gravity)
		end, "?"),
		streamingEnabled = safe(function()
			return tostring(workspace.StreamingEnabled)
		end, "?"),
		clockTime = safe(function()
			return tostring(lighting.ClockTime)
		end, "?"),
		ambient = safe(function()
			return tostring(lighting.Ambient)
		end, "?"),
		technology = safe(function()
			return tostring(lighting.Technology)
		end, "blocked"),
		hasTerrain = safe(function()
			return workspace:FindFirstChildOfClass("Terrain") ~= nil
		end, false),
		teams = #safe(function()
			return game:GetService("Teams"):GetChildren()
		end, {}),
	},
	totals = {
		instancesVisited = visited,
		scripts = #scriptList,
		visitCapHit = hitCap,
	},
})
