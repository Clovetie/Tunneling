--!strict
--[[
	VFXManager.lua — Deterministic Particle Burst, Hit-Flash Highlight, & 3D Mesh Shockwave Engine
	Part of the Arena Roblox Kit (v6.0.0)
]]

local Debris = game:GetService("Debris")
local TweenService = game:GetService("TweenService")
local Workspace = game:GetService("Workspace")

local VFXManager = {}

export type ShockwaveOptions = {
	position: Vector3,
	color: Color3?,
	startRadius: number?,
	endRadius: number?,
	duration: number?,
}

function VFXManager.EmitAll(root: Instance, overrideCount: number?): number
	local emitted = 0
	for _, desc in ipairs(root:GetDescendants()) do
		if desc:IsA("ParticleEmitter") then
			local countAttr = desc:GetAttribute("BurstCount")
			local n = overrideCount or (if type(countAttr) == "number" then countAttr else 16)
			desc:Emit(n)
			emitted += 1
		end
	end
	return emitted
end

function VFXManager.FlashHighlight(target: Instance, fillColor: Color3?, outlineColor: Color3?, duration: number?)
	local dur = duration or 0.16
	local hl = Instance.new("Highlight")
	hl.Name = "__ArenaVFXFlash"
	hl.Adornee = target
	hl.FillColor = fillColor or Color3.fromRGB(255, 255, 255)
	hl.OutlineColor = outlineColor or Color3.fromRGB(255, 210, 90)
	hl.FillTransparency = 0.25
	hl.OutlineTransparency = 0.0
	hl.DepthMode = Enum.HighlightDepthMode.Occluded
	hl.Parent = target

	local tween = TweenService:Create(
		hl,
		TweenInfo.new(dur, Enum.EasingStyle.Quad, Enum.EasingDirection.Out),
		{ FillTransparency = 1.0, OutlineTransparency = 1.0 }
	)
	tween:Play()
	Debris:AddItem(hl, dur + 0.05)
end

function VFXManager.SpawnMeshShockwave(opts: ShockwaveOptions)
	local startR = opts.startRadius or 2.0
	local endR = opts.endRadius or 18.0
	local dur = opts.duration or 0.35
	local col = opts.color or Color3.fromRGB(90, 210, 255)

	local part = Instance.new("Part")
	part.Name = "__ArenaMeshShockwave"
	part.Anchored = true
	part.CanCollide = false
	part.CanQuery = false
	part.CanTouch = false
	part.CastShadow = false
	part.Material = Enum.Material.Neon
	part.Color = col
	part.Transparency = 0.15
	part.Size = Vector3.new(startR, 0.4, startR)
	part.CFrame = CFrame.new(opts.position)

	local mesh = Instance.new("SpecialMesh")
	mesh.MeshType = Enum.MeshType.Sphere
	mesh.Scale = Vector3.new(1, 0.18, 1)
	mesh.Parent = part

	part.Parent = Workspace

	local tween = TweenService:Create(
		part,
		TweenInfo.new(dur, Enum.EasingStyle.Quart, Enum.EasingDirection.Out),
		{
			Size = Vector3.new(endR, 0.15, endR),
			Transparency = 1.0,
		}
	)
	tween:Play()
	Debris:AddItem(part, dur + 0.05)
end

return VFXManager
