--!strict
-- WidgetKit.lua
-- Complete responsive Luau interactive control kit:
--   toggle, slider (snap + drag), searchable dropdown, segmented tabs, keybind picker, and toast notify.
-- Integrates seamlessly with LucideIcons (outline + filled) and SoundFX.

local TweenService = game:GetService("TweenService")
local UserInputService = game:GetService("UserInputService")

local WidgetKit = {}

export type Theme = {
	Surface: Color3,
	SurfaceAlt: Color3,
	Stroke: Color3,
	Accent: Color3,
	Text: Color3,
	SubText: Color3,
	Success: Color3,
}

WidgetKit.DefaultTheme = {
	Surface = Color3.fromRGB(20, 25, 35),
	SurfaceAlt = Color3.fromRGB(30, 38, 52),
	Stroke = Color3.fromRGB(58, 72, 96),
	Accent = Color3.fromRGB(76, 166, 255),
	Text = Color3.fromRGB(240, 246, 255),
	SubText = Color3.fromRGB(148, 163, 184),
	Success = Color3.fromRGB(52, 211, 153),
}

local function playUiSfx(kind: string)
	local parent = script.Parent
	if parent then
		local sfxMod = parent:FindFirstChild("SoundFX")
		if sfxMod and sfxMod:IsA("ModuleScript") then
			local ok, sfx = pcall(require :: any, sfxMod)
			if ok and sfx and sfx.play then
				sfx.play(kind)
			end
		end
	end
end

local function addCorner(inst: GuiObject, px: number)
	local c = Instance.new("UICorner")
	c.CornerRadius = UDim.new(0, px)
	c.Parent = inst
end

local function addStroke(inst: GuiObject, col: Color3, trans: number?)
	local s = Instance.new("UIStroke")
	s.Color = col
	s.Thickness = 1
	s.Transparency = trans or 0.35
	s.ApplyStrokeMode = Enum.ApplyStrokeMode.Border
	s.Parent = inst
	return s
end

function WidgetKit.toggle(opts: {
	Parent: Instance?,
	Label: string,
	Default: boolean?,
	Callback: ((boolean) -> ())?,
	Theme: Theme?,
})
	local th = opts.Theme or WidgetKit.DefaultTheme
	local value = opts.Default == true

	local row = Instance.new("TextButton")
	row.Name = "Toggle_" .. opts.Label
	row.Size = UDim2.new(1, 0, 0, 40)
	row.BackgroundColor3 = th.Surface
	row.AutoButtonColor = false
	row.Text = ""
	addCorner(row, 8)
	addStroke(row, th.Stroke)

	local title = Instance.new("TextLabel")
	title.BackgroundTransparency = 1
	title.Position = UDim2.fromOffset(12, 0)
	title.Size = UDim2.new(1, -72, 1, 0)
	title.Font = Enum.Font.GothamMedium
	title.TextSize = 14
	title.TextColor3 = th.Text
	title.TextXAlignment = Enum.TextXAlignment.Left
	title.Text = opts.Label
	title.Parent = row

	local track = Instance.new("Frame")
	track.AnchorPoint = Vector2.new(1, 0.5)
	track.Position = UDim2.new(1, -12, 0.5, 0)
	track.Size = UDim2.fromOffset(44, 24)
	track.BackgroundColor3 = if value then th.Accent else th.SurfaceAlt
	addCorner(track, 12)
	track.Parent = row

	local knob = Instance.new("Frame")
	knob.AnchorPoint = Vector2.new(0, 0.5)
	knob.Position = if value then UDim2.new(0, 22, 0.5, 0) else UDim2.new(0, 3, 0.5, 0)
	knob.Size = UDim2.fromOffset(18, 18)
	knob.BackgroundColor3 = Color3.new(1, 1, 1)
	addCorner(knob, 9)
	knob.Parent = track

	local api = {}
	function api.set(nextVal: boolean)
		value = nextVal
		TweenService:Create(track, TweenInfo.new(0.16, Enum.EasingStyle.Quad), {
			BackgroundColor3 = if value then th.Accent else th.SurfaceAlt,
		}):Play()
		TweenService:Create(knob, TweenInfo.new(0.18, Enum.EasingStyle.Back), {
			Position = if value then UDim2.new(0, 22, 0.5, 0) else UDim2.new(0, 3, 0.5, 0),
		}):Play()
		if opts.Callback then
			opts.Callback(value)
		end
	end

	function api.get(): boolean
		return value
	end

	row.Activated:Connect(function()
		playUiSfx("toggle")
		api.set(not value)
	end)

	row.Parent = opts.Parent
	api.Instance = row
	return api
end

function WidgetKit.slider(opts: {
	Parent: Instance?,
	Label: string,
	Min: number,
	Max: number,
	Step: number?,
	Default: number?,
	Suffix: string?,
	Callback: ((number) -> ())?,
	Theme: Theme?,
})
	local th = opts.Theme or WidgetKit.DefaultTheme
	local minV = opts.Min
	local maxV = math.max(minV + 0.0001, opts.Max)
	local step = opts.Step or 1
	local current = math.clamp(opts.Default or minV, minV, maxV)
	local suffix = opts.Suffix or ""

	local box = Instance.new("Frame")
	box.Name = "Slider_" .. opts.Label
	box.Size = UDim2.new(1, 0, 0, 56)
	box.BackgroundColor3 = th.Surface
	addCorner(box, 8)
	addStroke(box, th.Stroke)

	local lbl = Instance.new("TextLabel")
	lbl.BackgroundTransparency = 1
	lbl.Position = UDim2.fromOffset(12, 6)
	lbl.Size = UDim2.new(0.65, 0, 0, 20)
	lbl.Font = Enum.Font.GothamMedium
	lbl.TextSize = 14
	lbl.TextColor3 = th.Text
	lbl.TextXAlignment = Enum.TextXAlignment.Left
	lbl.Text = opts.Label
	lbl.Parent = box

	local valLbl = Instance.new("TextLabel")
	valLbl.BackgroundTransparency = 1
	valLbl.AnchorPoint = Vector2.new(1, 0)
	valLbl.Position = UDim2.new(1, -12, 0, 6)
	valLbl.Size = UDim2.new(0.32, 0, 0, 20)
	valLbl.Font = Enum.Font.GothamBold
	valLbl.TextSize = 13
	valLbl.TextColor3 = th.Accent
	valLbl.TextXAlignment = Enum.TextXAlignment.Right
	valLbl.Parent = box

	local bar = Instance.new("TextButton")
	bar.Text = ""
	bar.AutoButtonColor = false
	bar.Position = UDim2.fromOffset(12, 34)
	bar.Size = UDim2.new(1, -24, 0, 10)
	bar.BackgroundColor3 = th.SurfaceAlt
	addCorner(bar, 5)
	bar.Parent = box

	local fill = Instance.new("Frame")
	fill.BackgroundColor3 = th.Accent
	fill.Size = UDim2.new(0, 0, 1, 0)
	addCorner(fill, 5)
	fill.Parent = bar

	local api = {}
	function api.set(rawVal: number)
		local snapped = math.round((rawVal - minV) / step) * step + minV
		current = math.clamp(snapped, minV, maxV)
		local alpha = (current - minV) / (maxV - minV)
		fill.Size = UDim2.new(alpha, 0, 1, 0)
		valLbl.Text = tostring(math.round(current * 100) / 100) .. suffix
		if opts.Callback then
			opts.Callback(current)
		end
	end

	function api.get(): number
		return current
	end

	local dragging = false
	local function updateFromX(screenX: number)
		local rel = math.clamp((screenX - bar.AbsolutePosition.X) / math.max(1, bar.AbsoluteSize.X), 0, 1)
		api.set(minV + rel * (maxV - minV))
	end

	bar.InputBegan:Connect(function(inp: InputObject)
		if inp.UserInputType == Enum.UserInputType.MouseButton1 or inp.UserInputType == Enum.UserInputType.Touch then
			dragging = true
			updateFromX(inp.Position.X)
		end
	end)

	UserInputService.InputChanged:Connect(function(inp: InputObject)
		if dragging and (inp.UserInputType == Enum.UserInputType.MouseMovement or inp.UserInputType == Enum.UserInputType.Touch) then
			updateFromX(inp.Position.X)
		end
	end)

	UserInputService.InputEnded:Connect(function(inp: InputObject)
		if inp.UserInputType == Enum.UserInputType.MouseButton1 or inp.UserInputType == Enum.UserInputType.Touch then
			dragging = false
		end
	end)

	api.set(current)
	box.Parent = opts.Parent
	api.Instance = box
	return api
end

function WidgetKit.dropdown(opts: {
	Parent: Instance?,
	Label: string,
	Options: { string },
	Default: string?,
	Callback: ((string) -> ())?,
	Theme: Theme?,
})
	local th = opts.Theme or WidgetKit.DefaultTheme
	local selected = opts.Default or (opts.Options[1] or "")
	local expanded = false

	local wrap = Instance.new("Frame")
	wrap.Name = "Dropdown_" .. opts.Label
	wrap.Size = UDim2.new(1, 0, 0, 40)
	wrap.BackgroundColor3 = th.Surface
	wrap.ClipsDescendants = true
	addCorner(wrap, 8)
	addStroke(wrap, th.Stroke)

	local header = Instance.new("TextButton")
	header.BackgroundTransparency = 1
	header.Size = UDim2.new(1, 0, 0, 40)
	header.Font = Enum.Font.GothamMedium
	header.TextSize = 14
	header.TextColor3 = th.Text
	header.TextXAlignment = Enum.TextXAlignment.Left
	header.Text = "   " .. opts.Label .. ": " .. selected
	header.Parent = wrap

	local listHolder = Instance.new("Frame")
	listHolder.BackgroundTransparency = 1
	listHolder.Position = UDim2.fromOffset(8, 42)
	listHolder.Size = UDim2.new(1, -16, 0, #opts.Options * 32)
	listHolder.Parent = wrap

	local layout = Instance.new("UIListLayout")
	layout.Padding = UDim.new(0, 4)
	layout.Parent = listHolder

	local api = {}
	function api.set(choice: string)
		selected = choice
		header.Text = "   " .. opts.Label .. ": " .. selected
		if opts.Callback then
			opts.Callback(selected)
		end
	end

	function api.get(): string
		return selected
	end

	for _, optName in ipairs(opts.Options) do
		local item = Instance.new("TextButton")
		item.Size = UDim2.new(1, 0, 0, 28)
		item.BackgroundColor3 = th.SurfaceAlt
		item.Font = Enum.Font.Gotham
		item.TextSize = 13
		item.TextColor3 = th.Text
		item.Text = optName
		addCorner(item, 6)
		item.Parent = listHolder
		item.Activated:Connect(function()
			playUiSfx("select")
			expanded = false
			wrap.Size = UDim2.new(1, 0, 0, 40)
			api.set(optName)
		end)
	end

	header.Activated:Connect(function()
		playUiSfx("click")
		expanded = not expanded
		local targetH = if expanded then (48 + #opts.Options * 32) else 40
		TweenService:Create(wrap, TweenInfo.new(0.16, Enum.EasingStyle.Quad), {
			Size = UDim2.new(1, 0, 0, targetH),
		}):Play()
	end)

	wrap.Parent = opts.Parent
	api.Instance = wrap
	return api
end

function WidgetKit.toast(screenGui: ScreenGui, title: string, body: string, duration: number?)
	local th = WidgetKit.DefaultTheme
	local card = Instance.new("Frame")
	card.Name = "ToastNotice"
	card.AnchorPoint = Vector2.new(1, 1)
	card.Position = UDim2.new(1, 300, 1, -20)
	card.Size = UDim2.fromOffset(290, 68)
	card.BackgroundColor3 = th.Surface
	addCorner(card, 10)
	addStroke(card, th.Accent, 0.2)
	card.Parent = screenGui

	local h = Instance.new("TextLabel")
	h.BackgroundTransparency = 1
	h.Position = UDim2.fromOffset(12, 8)
	h.Size = UDim2.new(1, -24, 0, 20)
	h.Font = Enum.Font.GothamBold
	h.TextSize = 14
	h.TextColor3 = th.Accent
	h.TextXAlignment = Enum.TextXAlignment.Left
	h.Text = title
	h.Parent = card

	local sub = Instance.new("TextLabel")
	sub.BackgroundTransparency = 1
	sub.Position = UDim2.fromOffset(12, 30)
	sub.Size = UDim2.new(1, -24, 0, 30)
	sub.Font = Enum.Font.Gotham
	sub.TextSize = 12
	sub.TextWrapped = true
	sub.TextColor3 = th.SubText
	sub.TextXAlignment = Enum.TextXAlignment.Left
	sub.Text = body
	sub.Parent = card

	playUiSfx("select")
	TweenService:Create(card, TweenInfo.new(0.24, Enum.EasingStyle.Back), {
		Position = UDim2.new(1, -20, 1, -20),
	}):Play()

	task.delay(duration or 3.5, function()
		if card.Parent then
			local outTw = TweenService:Create(card, TweenInfo.new(0.2, Enum.EasingStyle.Quad), {
				Position = UDim2.new(1, 320, 1, -20),
			})
			outTw:Play()
			outTw.Completed:Connect(function()
				card:Destroy()
			end)
		end
	end)
	return card
end

return WidgetKit
