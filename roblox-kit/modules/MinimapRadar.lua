--!strict
-- MinimapRadar.lua
-- Real-time top-right circular radar HUD tracking CollectionService tags ("QuestPin", "ShopPin", "EnemyPin").

local CollectionService = game:GetService("CollectionService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local MinimapRadar = {}

export type RadarConfig = {
	Parent: ScreenGui,
	RangeStuds: number?,
	SizePx: number?,
	TagIcons: { [string]: string }?,
	TagColors: { [string]: Color3 }?,
}

function MinimapRadar.mount(cfg: RadarConfig)
	local sizePx = cfg.SizePx or 154
	local rangeStuds = cfg.RangeStuds or 140
	local radiusPx = (sizePx * 0.5) - 12

	local frame = Instance.new("Frame")
	frame.Name = "MinimapRadar"
	frame.AnchorPoint = Vector2.new(1, 0)
	frame.Position = UDim2.new(1, -16, 0, 16)
	frame.Size = UDim2.fromOffset(sizePx, sizePx)
	frame.BackgroundColor3 = Color3.fromRGB(14, 19, 28)
	frame.BackgroundTransparency = 0.14
	frame.Parent = cfg.Parent

	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(1, 0)
	corner.Parent = frame

	local stroke = Instance.new("UIStroke")
	stroke.Color = Color3.fromRGB(96, 165, 250)
	stroke.Thickness = 2
	stroke.Transparency = 0.25
	stroke.Parent = frame

	local centerDot = Instance.new("Frame")
	centerDot.Name = "PlayerCenter"
	centerDot.AnchorPoint = Vector2.new(0.5, 0.5)
	centerDot.Position = UDim2.fromScale(0.5, 0.5)
	centerDot.Size = UDim2.fromOffset(10, 10)
	centerDot.BackgroundColor3 = Color3.fromRGB(56, 224, 148)
	local cc = Instance.new("UICorner")
	cc.CornerRadius = UDim.new(1, 0)
	cc.Parent = centerDot
	centerDot.Parent = frame

	local tagColors = cfg.TagColors or {
		QuestPin = Color3.fromRGB(250, 204, 21),
		ShopPin = Color3.fromRGB(56, 189, 248),
		EnemyPin = Color3.fromRGB(248, 113, 113),
	}

	local blips: { [Instance]: Frame } = {}

	local function getWorldPos(inst: Instance): Vector3?
		if inst:IsA("BasePart") then
			return inst.Position
		elseif inst:IsA("Model") then
			return inst:GetPivot().Position
		end
		return nil
	end

	local conn = RunService.RenderStepped:Connect(function()
		local lp = Players.LocalPlayer
		local cam = workspace.CurrentCamera
		if not lp or not cam then
			return
		end
		local char = lp.Character
		local root = char and char:FindFirstChild("HumanoidRootPart")
		local origin = if root and root:IsA("BasePart") then root.Position else cam.CFrame.Position
		local camLook = cam.CFrame.LookVector
		local heading = math.atan2(-camLook.X, -camLook.Z)

		local seen: { [Instance]: boolean } = {}
		for tagName, tint in pairs(tagColors) do
			for _, inst in ipairs(CollectionService:GetTagged(tagName)) do
				local wp = getWorldPos(inst)
				if wp then
					seen[inst] = true
					local blip = blips[inst]
					if not blip then
						blip = Instance.new("Frame")
						blip.AnchorPoint = Vector2.new(0.5, 0.5)
						blip.Size = UDim2.fromOffset(8, 8)
						blip.BackgroundColor3 = tint
						local bc = Instance.new("UICorner")
						bc.CornerRadius = UDim.new(1, 0)
						bc.Parent = blip
						blip.Parent = frame
						blips[inst] = blip
					end
					local dx = wp.X - origin.X
					local dz = wp.Z - origin.Z
					local cosH = math.cos(-heading)
					local sinH = math.sin(-heading)
					local rx = (dx * cosH - dz * sinH) / rangeStuds
					local rz = (dx * sinH + dz * cosH) / rangeStuds
					local dist = math.sqrt(rx * rx + rz * rz)
					if dist > 1 then
						rx /= dist
						rz /= dist
					end
					blip.Position = UDim2.new(0.5, rx * radiusPx, 0.5, rz * radiusPx)
				end
			end
		end

		for inst, blip in pairs(blips) do
			if not seen[inst] then
				blip:Destroy()
				blips[inst] = nil
			end
		end
	end)

	return {
		Frame = frame,
		destroy = function()
			conn:Disconnect()
			frame:Destroy()
		end,
	}
end

return MinimapRadar
