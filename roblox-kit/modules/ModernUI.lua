-- ModernUI.lua — Clean, sleek dark-glass & minimalist UI component builder
-- Designed to pair natively with `LucideIcons` for tactical FPS HUDs, sci-fi
-- terminals, horror menus, settings panels, admin dashboards, and modern shops.
local TweenService = game:GetService("TweenService")

local ModernUI = {}

ModernUI.Tokens = {
	BgBase = Color3.fromRGB(13, 17, 24),
	BgElevated = Color3.fromRGB(20, 26, 36),
	BgCard = Color3.fromRGB(26, 33, 46),
	Border = Color3.fromRGB(48, 60, 82),
	BorderHover = Color3.fromRGB(92, 152, 255),
	TextPrimary = Color3.fromRGB(238, 243, 250),
	TextMuted = Color3.fromRGB(142, 154, 174),
	Accent = Color3.fromRGB(59, 130, 246),
	Success = Color3.fromRGB(34, 197, 94),
	Warning = Color3.fromRGB(245, 158, 11),
	Danger = Color3.fromRGB(239, 68, 68),
}

local FAST = TweenInfo.new(0.14, Enum.EasingStyle.Quad, Enum.EasingDirection.Out)

local function corner(parent, radius)
	local c = Instance.new("UICorner")
	c.CornerRadius = UDim.new(0, radius or 10)
	c.Parent = parent
	return c
end

local function stroke(parent, color, thickness, transparency)
	local s = Instance.new("UIStroke")
	s.Color = color or ModernUI.Tokens.Border
	s.Thickness = thickness or 1
	s.Transparency = transparency or 0.15
	s.ApplyStrokeMode = Enum.ApplyStrokeMode.Border
	s.Parent = parent
	return s
end

function ModernUI.ScreenGui(name, parent, displayOrder)
	local existing = parent and parent:FindFirstChild(name)
	if existing then
		existing:Destroy()
	end
	local gui = Instance.new("ScreenGui")
	gui.Name = name
	gui.ResetOnSpawn = false
	gui.IgnoreGuiInset = true
	gui.ZIndexBehavior = Enum.ZIndexBehavior.Sibling
	gui.DisplayOrder = displayOrder or 8
	local uiScale = Instance.new("UIScale")
	uiScale.Name = "ResponsiveScale"
	uiScale.Scale = 1
	uiScale.Parent = gui
	if parent then
		gui.Parent = parent
	end
	return gui
end

function ModernUI.Modal(parent, opts)
	opts = opts or {}
	local w = opts.Width or 580
	local h = opts.Height or 390
	local root = Instance.new("Frame")
	root.Name = opts.Name or "Modal"
	root.AnchorPoint = Vector2.new(0.5, 0.5)
	root.Position = opts.Position or UDim2.fromScale(0.5, 0.5)
	root.Size = UDim2.fromOffset(w, h)
	root.BackgroundColor3 = ModernUI.Tokens.BgBase
	root.BackgroundTransparency = opts.Transparency or 0.06
	corner(root, 14)
	stroke(root, ModernUI.Tokens.Border, 1.5, 0.1)
	root.Parent = parent

	local header = Instance.new("Frame")
	header.Name = "Header"
	header.Size = UDim2.new(1, 0, 0, 52)
	header.BackgroundColor3 = ModernUI.Tokens.BgElevated
	header.BackgroundTransparency = 0.2
	corner(header, 14)
	header.Parent = root

	local xOffset = 18
	if opts.Icon and opts.Icon ~= "" then
		local ic = Instance.new("ImageLabel")
		ic.Name = "HeaderIcon"
		ic.Position = UDim2.new(0, 16, 0.5, -11)
		ic.Size = UDim2.fromOffset(22, 22)
		ic.BackgroundTransparency = 1
		ic.Image = opts.Icon
		ic.ImageColor3 = opts.Accent or ModernUI.Tokens.Accent
		ic.Parent = header
		xOffset = 46
	end

	local title = Instance.new("TextLabel")
	title.Name = "Title"
	title.Position = UDim2.fromOffset(xOffset, 0)
	title.Size = UDim2.new(1, -(xOffset + 56), 1, 0)
	title.BackgroundTransparency = 1
	title.Text = opts.Title or "Panel"
	title.TextColor3 = ModernUI.Tokens.TextPrimary
	title.TextSize = 18
	title.Font = Enum.Font.GothamBold
	title.TextXAlignment = Enum.TextXAlignment.Left
	title.Parent = header

	local closeBtn = ModernUI.Button(header, {
		Name = "CloseButton",
		Text = "X",
		Variant = "ghost",
		AnchorPoint = Vector2.new(1, 0.5),
		Position = UDim2.new(1, -10, 0.5, 0),
		Size = UDim2.fromOffset(34, 34),
	})

	local body = Instance.new("Frame")
	body.Name = "Body"
	body.Position = UDim2.fromOffset(16, 64)
	body.Size = UDim2.new(1, -32, 1, -80)
	body.BackgroundTransparency = 1
	body.Parent = root

	return {
		Root = root,
		Header = header,
		Body = body,
		CloseButton = closeBtn,
	}
end

function ModernUI.Button(parent, opts)
	opts = opts or {}
	local variant = opts.Variant or "primary"
	local bg = ModernUI.Tokens.Accent
	if variant == "secondary" then
		bg = ModernUI.Tokens.BgCard
	elseif variant == "success" then
		bg = ModernUI.Tokens.Success
	elseif variant == "danger" then
		bg = ModernUI.Tokens.Danger
	elseif variant == "ghost" then
		bg = ModernUI.Tokens.BgElevated
	end

	local btn = Instance.new("TextButton")
	btn.Name = opts.Name or "Button"
	btn.AnchorPoint = opts.AnchorPoint or Vector2.new(0, 0)
	btn.Position = opts.Position or UDim2.fromOffset(0, 0)
	btn.Size = opts.Size or UDim2.fromOffset(152, 40)
	btn.BackgroundColor3 = bg
	btn.BackgroundTransparency = variant == "ghost" and 0.35 or 0
	btn.AutoButtonColor = false
	btn.Text = ""
	corner(btn, opts.Radius or 9)
	local st = stroke(btn, variant == "primary" and Color3.fromRGB(115, 172, 255) or ModernUI.Tokens.Border, 1, 0.2)

	local padLeft = 0
	if opts.Icon and opts.Icon ~= "" then
		local ic = Instance.new("ImageLabel")
		ic.Name = "Icon"
		ic.Position = UDim2.new(0, 12, 0.5, -9)
		ic.Size = UDim2.fromOffset(18, 18)
		ic.BackgroundTransparency = 1
		ic.Image = opts.Icon
		ic.ImageColor3 = ModernUI.Tokens.TextPrimary
		ic.Parent = btn
		padLeft = 24
	end

	local lbl = Instance.new("TextLabel")
	lbl.Name = "Label"
	lbl.Position = UDim2.fromOffset(padLeft, 0)
	lbl.Size = UDim2.new(1, -padLeft, 1, 0)
	lbl.BackgroundTransparency = 1
	lbl.Text = opts.Text or "Action"
	lbl.TextColor3 = ModernUI.Tokens.TextPrimary
	lbl.TextSize = opts.TextSize or 15
	lbl.Font = Enum.Font.GothamSemibold
	lbl.Parent = btn

	btn.MouseEnter:Connect(function()
		TweenService:Create(st, FAST, { Color = ModernUI.Tokens.BorderHover, Transparency = 0 }):Play()
	end)
	btn.MouseLeave:Connect(function()
		TweenService:Create(st, FAST, { Color = ModernUI.Tokens.Border, Transparency = 0.2 }):Play()
	end)
	btn.Parent = parent
	return btn
end

function ModernUI.StatPill(parent, opts)
	opts = opts or {}
	local pill = Instance.new("Frame")
	pill.Name = opts.Name or "StatPill"
	pill.Position = opts.Position or UDim2.fromOffset(0, 0)
	pill.Size = opts.Size or UDim2.fromOffset(146, 38)
	pill.BackgroundColor3 = ModernUI.Tokens.BgElevated
	pill.BackgroundTransparency = 0.14
	corner(pill, 19)
	stroke(pill, ModernUI.Tokens.Border, 1, 0.2)

	if opts.Icon and opts.Icon ~= "" then
		local ic = Instance.new("ImageLabel")
		ic.Name = "Icon"
		ic.Position = UDim2.new(0, 12, 0.5, -9)
		ic.Size = UDim2.fromOffset(18, 18)
		ic.BackgroundTransparency = 1
		ic.Image = opts.Icon
		ic.ImageColor3 = opts.IconColor or ModernUI.Tokens.Warning
		ic.Parent = pill
	end

	local val = Instance.new("TextLabel")
	val.Name = "Value"
	val.Position = UDim2.fromOffset(38, 0)
	val.Size = UDim2.new(1, -48, 1, 0)
	val.BackgroundTransparency = 1
	val.Text = tostring(opts.Value or "0")
	val.TextColor3 = ModernUI.Tokens.TextPrimary
	val.TextSize = 15
	val.Font = Enum.Font.GothamBold
	val.TextXAlignment = Enum.TextXAlignment.Left
	val.Parent = pill

	pill.Parent = parent
	return pill
end

return ModernUI
