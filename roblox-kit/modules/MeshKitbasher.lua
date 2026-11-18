--!strict
--[[
	MeshKitbasher.lua
	3D Mesh-First prop kitbashing helper for Roblox Studio.
	Builds sculpted SpecialMesh / MeshPart hierarchies (runic obelisks, crystal
	lanterns, treasure shrines, forge anvils) instead of blocky Part cubes,
	and snaps models cleanly to ground surfaces via Workspace:Raycast.
]]

local Workspace = game:GetService("Workspace")

local MeshKitbasher = {}

export type KitbashPreset = "runic_obelisk" | "crystal_lantern" | "treasure_shrine" | "forge_anvil"

local function createSculptedPart(
	name: string,
	size: Vector3,
	pos: Vector3,
	color: Color3,
	mat: Enum.Material,
	meshType: Enum.MeshType,
	meshScale: Vector3,
	parent: Instance
): Part
	local part = Instance.new("Part")
	part.Name = name
	part.Anchored = true
	part.CanCollide = true
	part.Size = size
	part.Position = pos
	part.Color = color
	part.Material = mat

	local sm = Instance.new("SpecialMesh")
	sm.Name = "SculptedSpecialMesh"
	sm.MeshType = meshType
	sm.Scale = meshScale
	sm.Parent = part

	part.Parent = parent
	return part
end

function MeshKitbasher.SnapModelToGround(model: Model, origin: Vector3, maxDropStuds: number?): Vector3
	local rayParams = RaycastParams.new()
	rayParams.FilterType = Enum.RaycastFilterType.Exclude
	rayParams.FilterDescendantsInstances = { model }

	local drop = maxDropStuds or 120.0
	local hit = Workspace:Raycast(origin + Vector3.new(0, 12, 0), Vector3.new(0, -drop, 0), rayParams)
	local groundY = if hit then hit.Position.Y else origin.Y
	local extents = model:GetExtentsSize()
	local targetPos = Vector3.new(origin.X, groundY + extents.Y * 0.5, origin.Z)
	model:PivotTo(CFrame.new(targetPos))
	return targetPos
end

function MeshKitbasher.SpawnProp(preset: KitbashPreset, position: Vector3, scale: number?, parent: Instance?): Model
	local s = math.clamp(scale or 1.0, 0.2, 20.0)
	local model = Instance.new("Model")
	model.Name = "MeshProp_" .. tostring(preset)

	local baseColor = Color3.fromRGB(51, 65, 85)
	local accentColor = Color3.fromRGB(56, 189, 248)
	if preset == "treasure_shrine" then
		accentColor = Color3.fromRGB(251, 191, 36)
	elseif preset == "forge_anvil" then
		accentColor = Color3.fromRGB(249, 115, 22)
	end

	local basePart = createSculptedPart(
		"PedestalBaseMesh",
		Vector3.new(4.4 * s, 1.2 * s, 4.4 * s),
		position + Vector3.new(0, 0.6 * s, 0),
		baseColor,
		Enum.Material.Basalt,
		Enum.MeshType.Cylinder,
		Vector3.new(1, 1, 1),
		model
	)

	local corePart = createSculptedPart(
		"SculptedCoreMesh",
		Vector3.new(2.4 * s, 4.6 * s, 2.4 * s),
		position + Vector3.new(0, 3.2 * s, 0),
		accentColor,
		Enum.Material.Neon,
		Enum.MeshType.Sphere,
		Vector3.new(0.85, 1.25, 0.85),
		model
	)

	local light = Instance.new("PointLight")
	light.Name = "MeshAccentLight"
	light.Color = accentColor
	light.Brightness = 2.2
	light.Range = 16.0 * s
	light.Parent = corePart

	model.PrimaryPart = basePart
	if parent then
		model.Parent = parent
	end
	return model
end

return MeshKitbasher
