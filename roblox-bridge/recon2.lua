-- Structural deep-dive on the pieces the two monster scripts depend on.
local HttpService = game:GetService("HttpService")
local ServerStorage = game:GetService("ServerStorage")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local StarterPack = game:GetService("StarterPack")

local function safe(fn, d)
	local ok, v = pcall(fn)
	if ok and v ~= nil then return v end
	return d
end

local function tree(inst, depth, maxKids)
	if not inst then return nil end
	local node = { n = inst.Name, c = inst.ClassName }
	if inst:IsA("BasePart") then
		node.size = safe(function()
			return string.format("%.1f x %.1f x %.1f", inst.Size.X, inst.Size.Y, inst.Size.Z)
		end, nil)
		node.anchored = safe(function() return inst.Anchored end, nil)
	end
	if inst:IsA("Animation") then
		node.animId = safe(function() return inst.AnimationId end, "")
	end
	if inst:IsA("Light") then
		node.enabled = safe(function() return inst.Enabled end, nil)
		node.color = safe(function() return tostring(inst.Color) end, nil)
	end
	if depth > 0 then
		local kids = {}
		for i, child in ipairs(inst:GetChildren()) do
			if i > (maxKids or 25) then
				table.insert(kids, { n = "...more", c = "" })
				break
			end
			table.insert(kids, tree(child, depth - 1, maxKids))
		end
		if #kids > 0 then node.kids = kids end
	else
		node.childCount = #inst:GetChildren()
	end
	return node
end

local baseModel = workspace:FindFirstChild("BASEmodel")
local baseInfo = nil
if baseModel then
	baseInfo = {
		primaryPart = safe(function()
			return baseModel.PrimaryPart and baseModel.PrimaryPart.Name
		end, "<<NONE>>"),
		primaryPartSize = safe(function()
			local p = baseModel.PrimaryPart
			return p and string.format("%.1f x %.1f x %.1f", p.Size.X, p.Size.Y, p.Size.Z)
		end, nil),
		hasHumanoid = safe(function()
			return baseModel:FindFirstChildOfClass("Humanoid") ~= nil
		end, false),
		hasAnimationsFolder = safe(function()
			return baseModel:FindFirstChild("Animations") ~= nil
		end, false),
		tree = tree(baseModel, 3, 25),
	}
end

local rig = workspace:FindFirstChild("Rig")
local spots = workspace:FindFirstChild("Spots")
local flashlight = StarterPack:FindFirstChild("Flashlight")
local jumpscare = ServerStorage:FindFirstChild("jumpscare")

return HttpService:JSONEncode({
	baseModel = baseInfo,
	rig = rig and {
		primaryPart = safe(function()
			return rig.PrimaryPart and rig.PrimaryPart.Name
		end, "<<NONE>>"),
		hasHumanoid = rig:FindFirstChildOfClass("Humanoid") ~= nil,
		tree = tree(rig, 2, 30),
	} or nil,
	spots = spots and {
		count = #spots:GetChildren(),
		kids = (function()
			local out = {}
			for _, s in ipairs(spots:GetChildren()) do
				table.insert(out, {
					n = s.Name, c = s.ClassName,
					at = safe(function()
						return string.format("%.0f, %.0f, %.0f", s.Position.X, s.Position.Y, s.Position.Z)
					end, "?"),
				})
			end
			return out
		end)(),
	} or nil,
	jumpscare = jumpscare and {
		primaryPart = safe(function()
			return jumpscare.PrimaryPart and jumpscare.PrimaryPart.Name
		end, "<<NONE>>"),
		tree = tree(jumpscare, 2, 20),
	} or { missing = true },
	serverStorage = tree(ServerStorage, 1, 20),
	replicatedStorage = tree(ReplicatedStorage, 2, 20),
	flashlight = flashlight and tree(flashlight, 3, 20) or { missing = true },
	detectPlayers = (function()
		local d = workspace:FindFirstChild("DetectPlayers")
		return d and tree(d, 2, 10) or nil
	end)(),
	lighting = tree(game:GetService("Lighting"), 1, 20),
})
