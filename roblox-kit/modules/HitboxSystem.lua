--!strict
--[[
	HitboxSystem.lua
	Deterministic spatial hitbox queries (GetPartBoundsInBox, GetPartBoundsInRadius)
	with per-swing target deduplication, server reach validation, and optional
	sculpted 3D SpecialMesh debug visualization.
]]

local Workspace = game:GetService("Workspace")
local Debris = game:GetService("Debris")

local HitboxSystem = {}

export type HitboxOptions = {
	Exclude: { Instance }?,
	MaxParts: number?,
	RequireAliveHumanoid: boolean?,
	VisualizeDuration: number?,
	VisualizeColor: Color3?,
}

local function buildOverlapParams(exclude: { Instance }?, maxParts: number?): OverlapParams
	local params = OverlapParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	params.FilterDescendantsInstances = exclude or {}
	params.MaxParts = maxParts or 48
	return params
end

local function spawnDebugMesh(cframe: CFrame, size: Vector3, isSphere: boolean, duration: number, color: Color3?)
	local part = Instance.new("Part")
	part.Name = "__HitboxDebugMesh"
	part.Anchored = true
	part.CanCollide = false
	part.CanQuery = false
	part.CanTouch = false
	part.Material = Enum.Material.Neon
	part.Color = color or Color3.fromRGB(56, 189, 248)
	part.Transparency = 0.65
	part.Size = size
	part.CFrame = cframe

	local mesh = Instance.new("SpecialMesh")
	mesh.MeshType = if isSphere then Enum.MeshType.Sphere else Enum.MeshType.Brick
	mesh.Parent = part

	part.Parent = Workspace
	Debris:AddItem(part, duration)
end

local function collectUniqueModels(parts: { BasePart }, requireAlive: boolean): { Model }
	local seen: { [Model]: boolean } = {}
	local out: { Model } = {}
	for _, part in ipairs(parts) do
		local model = part:FindFirstAncestorOfClass("Model")
		if model and not seen[model] then
			if requireAlive then
				local hum = model:FindFirstChildOfClass("Humanoid")
				if hum and hum.Health > 0 then
					seen[model] = true
					table.insert(out, model)
				end
			else
				seen[model] = true
				table.insert(out, model)
			end
		end
	end
	return out
end

function HitboxSystem.QueryBox(boxCFrame: CFrame, boxSize: Vector3, options: HitboxOptions?): { Model }
	local opts: HitboxOptions = options or {}
	local params = buildOverlapParams(opts.Exclude, opts.MaxParts)
	local parts = Workspace:GetPartBoundsInBox(boxCFrame, boxSize, params)
	if opts.VisualizeDuration and opts.VisualizeDuration > 0 then
		spawnDebugMesh(boxCFrame, boxSize, false, opts.VisualizeDuration, opts.VisualizeColor)
	end
	local requireAlive = if opts.RequireAliveHumanoid == nil then true else opts.RequireAliveHumanoid
	return collectUniqueModels(parts, requireAlive)
end

function HitboxSystem.QueryRadius(center: Vector3, radius: number, options: HitboxOptions?): { Model }
	local opts: HitboxOptions = options or {}
	local params = buildOverlapParams(opts.Exclude, opts.MaxParts)
	local parts = Workspace:GetPartBoundsInRadius(center, radius, params)
	if opts.VisualizeDuration and opts.VisualizeDuration > 0 then
		local diam = radius * 2
		spawnDebugMesh(CFrame.new(center), Vector3.new(diam, diam, diam), true, opts.VisualizeDuration, opts.VisualizeColor)
	end
	local requireAlive = if opts.RequireAliveHumanoid == nil then true else opts.RequireAliveHumanoid
	return collectUniqueModels(parts, requireAlive)
end

function HitboxSystem.ValidateServerHit(attacker: Model?, target: Model?, maxReach: number): boolean
	if not (attacker and target and attacker ~= target) then
		return false
	end
	local aRoot = attacker:FindFirstChild("HumanoidRootPart")
	local tRoot = target:FindFirstChild("HumanoidRootPart")
	local tHum = target:FindFirstChildOfClass("Humanoid")
	if not (aRoot and aRoot:IsA("BasePart") and tRoot and tRoot:IsA("BasePart")) then
		return false
	end
	if not (tHum and tHum.Health > 0) then
		return false
	end
	local dist = (tRoot.Position - aRoot.Position).Magnitude
	return dist <= maxReach
end

return HitboxSystem
