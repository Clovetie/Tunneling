--!strict
-- OverheadHUD.lua
-- World-space 3D BillboardGui toolkit for NPC speech/emote bubbles (Kenney CC0 emotes),
-- overhead boss/enemy health bars, ranked medal nameplates, and floating combat numbers.

local TweenService = game:GetService("TweenService")
local Debris = game:GetService("Debris")

local OverheadHUD = {}

function OverheadHUD.attachHealthBar(adornee: BasePart | Model, opts: {
	Name: string?,
	RankBadgeAsset: string?,
	MaxHealth: number?,
	StudsOffset: Vector3?,
}?)
	local cfg = opts or {}
	local maxHp = cfg.MaxHealth or 100

	local gui = Instance.new("BillboardGui")
	gui.Name = "OverheadBar"
	gui.Size = UDim2.fromOffset(170, 46)
	gui.StudsOffset = cfg.StudsOffset or Vector3.new(0, 3.6, 0)
	gui.MaxDistance = 95
	gui.AlwaysOnTop = false
	gui.Adornee = adornee :: any

	local nameLbl = Instance.new("TextLabel")
	nameLbl.BackgroundTransparency = 1
	nameLbl.Size = UDim2.new(1, 0, 0, 22)
	nameLbl.Font = Enum.Font.GothamBold
	nameLbl.TextScaled = true
	nameLbl.TextColor3 = Color3.fromRGB(255, 255, 255)
	nameLbl.TextStrokeTransparency = 0.35
	nameLbl.Text = cfg.Name or adornee.Name
	nameLbl.Parent = gui

	if cfg.RankBadgeAsset then
		local badge = Instance.new("ImageLabel")
		badge.BackgroundTransparency = 1
		badge.Size = UDim2.fromOffset(22, 22)
		badge.Position = UDim2.fromOffset(-6, 0)
		badge.Image = cfg.RankBadgeAsset
		badge.Parent = gui
	end

	local bg = Instance.new("Frame")
	bg.Position = UDim2.fromOffset(10, 26)
	bg.Size = UDim2.new(1, -20, 0, 12)
	bg.BackgroundColor3 = Color3.fromRGB(18, 22, 30)
	local c1 = Instance.new("UICorner")
	c1.CornerRadius = UDim.new(1, 0)
	c1.Parent = bg
	bg.Parent = gui

	local fill = Instance.new("Frame")
	fill.Size = UDim2.fromScale(1, 1)
	fill.BackgroundColor3 = Color3.fromRGB(72, 219, 118)
	local c2 = Instance.new("UICorner")
	c2.CornerRadius = UDim.new(1, 0)
	c2.Parent = fill
	fill.Parent = bg

	gui.Parent = adornee

	return {
		Gui = gui,
		setHealth = function(hp: number, newMax: number?)
			if newMax then
				maxHp = math.max(1, newMax)
			end
			local ratio = math.clamp(hp / maxHp, 0, 1)
			local col = if ratio > 0.5
				then Color3.fromRGB(72, 219, 118)
				elseif ratio > 0.25 then Color3.fromRGB(250, 190, 56)
				else Color3.fromRGB(244, 67, 54)
			TweenService:Create(fill, TweenInfo.new(0.18, Enum.EasingStyle.Quad), {
				Size = UDim2.fromScale(ratio, 1),
				BackgroundColor3 = col,
			}):Play()
		end,
	}
end

function OverheadHUD.popEmoteBubble(adornee: BasePart | Model, emoteImageUri: string, duration: number?): BillboardGui
	local bb = Instance.new("BillboardGui")
	bb.Name = "EmoteBubble"
	bb.Size = UDim2.fromOffset(10, 10)
	bb.StudsOffset = Vector3.new(0, 4.4, 0)
	bb.MaxDistance = 80
	bb.AlwaysOnTop = true
	bb.Adornee = adornee :: any

	local img = Instance.new("ImageLabel")
	img.BackgroundTransparency = 1
	img.AnchorPoint = Vector2.new(0.5, 0.5)
	img.Position = UDim2.fromScale(0.5, 0.5)
	img.Size = UDim2.fromScale(1, 1)
	img.ScaleType = Enum.ScaleType.Fit
	img.Image = emoteImageUri
	img.Parent = bb

	bb.Parent = adornee
	TweenService:Create(bb, TweenInfo.new(0.24, Enum.EasingStyle.Back), {
		Size = UDim2.fromOffset(64, 64),
		StudsOffset = Vector3.new(0, 5.2, 0),
	}):Play()

	Debris:AddItem(bb, duration or 2.5)
	return bb
end

function OverheadHUD.popDamageNumber(worldPos: Vector3, amount: number, isCrit: boolean?)
	local anchor = Instance.new("Part")
	anchor.Name = "DmgAnchor"
	anchor.Anchored = true
	anchor.CanCollide = false
	anchor.CanQuery = false
	anchor.CanTouch = false
	anchor.Transparency = 1
	anchor.Size = Vector3.new(0.2, 0.2, 0.2)
	anchor.Position = worldPos
	anchor.Parent = workspace

	local bb = Instance.new("BillboardGui")
	bb.Size = if isCrit then UDim2.fromOffset(120, 48) else UDim2.fromOffset(92, 34)
	bb.AlwaysOnTop = true
	bb.Adornee = anchor
	bb.Parent = anchor

	local txt = Instance.new("TextLabel")
	txt.BackgroundTransparency = 1
	txt.Size = UDim2.fromScale(1, 1)
	txt.Font = Enum.Font.FredokaOne
	txt.TextScaled = true
	txt.Text = if isCrit then ("CRIT " .. tostring(amount) .. "!") else tostring(amount)
	txt.TextColor3 = if isCrit then Color3.fromRGB(255, 208, 46) else Color3.fromRGB(255, 255, 255)
	txt.TextStrokeTransparency = 0.15
	txt.Parent = bb

	TweenService:Create(anchor, TweenInfo.new(0.75, Enum.EasingStyle.Quad, Enum.EasingDirection.Out), {
		Position = worldPos + Vector3.new(0, 3.4, 0),
	}):Play()
	TweenService:Create(txt, TweenInfo.new(0.75, Enum.EasingStyle.Linear), {
		TextTransparency = 1,
		TextStrokeTransparency = 1,
	}):Play()
	Debris:AddItem(anchor, 0.85)
end

return OverheadHUD
