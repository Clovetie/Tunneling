--!nonstrict
local Lighting = game:GetService("Lighting")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local SoundService = game:GetService("SoundService")
local TweenService = game:GetService("TweenService")
local UserInputService = game:GetService("UserInputService")

local Remotes = require(ReplicatedStorage:WaitForChild("Remotes"))
local SettingsConfig = require(ReplicatedStorage:WaitForChild("SettingsConfig"))
local SettingsData = require(ReplicatedStorage:WaitForChild("SettingsData"))

local player = Players.LocalPlayer
local playerGui = player:WaitForChild("PlayerGui")
local saveEvent = Remotes.event(SettingsConfig.REMOTE_SAVE)
local loadEvent = Remotes.event(SettingsConfig.REMOTE_LOAD)

local settings = SettingsData.defaults()
local listeners = {}
local rows = {}
local render
-- Assigned below, only if ui/kit is installed. Declared here so setOpen can respect the
-- kit's tutorial lock without the two files being loaded in a particular order.
local UIManager
local captureFor = nil
local isOpen = false
local warnedAboutGroups = false

local BG = Color3.fromRGB(18, 20, 26)
local FG = Color3.fromRGB(228, 232, 240)
local MUTED = Color3.fromRGB(158, 168, 186)
local ACCENT = Color3.fromRGB(96, 164, 214)

local function make(className, props, parent)
	local instance = Instance.new(className)
	for key, value in pairs(props or {}) do
		instance[key] = value
	end
	if parent then
		instance.Parent = parent
	end
	return instance
end

-- ── applying a setting ────────────────────────────────────────────────────────────────

local function soundGroup(name)
	if type(name) ~= "string" or name == "" then
		return nil
	end
	local found = SoundService:FindFirstChild(name)
	if found and found:IsA("SoundGroup") then
		return found
	end
	return nil
end

local function applyVolumes()
	local wanted = { Master = settings.MasterVolume, Music = settings.MusicVolume, Sfx = settings.SfxVolume }
	local missing = {}
	for slot, name in pairs(SettingsConfig.SOUND_GROUPS) do
		local group = soundGroup(name)
		if group then
			group.Volume = wanted[slot] or 1
		else
			table.insert(missing, tostring(name))
		end
	end
	-- A game with no SoundGroups still gets working settings; it just has nothing to apply
	-- the volumes to. Say so once, in Studio, where somebody can act on it.
	if #missing > 0 and not warnedAboutGroups then
		warnedAboutGroups = true
		if RunService:IsStudio() then
			warn(("settings-menu: no SoundGroup named %s in SoundService, so the volume sliders have nothing to drive"):format(table.concat(missing, ", ")))
		end
	end
end

local function applySensitivity()
	UserInputService.MouseDeltaSensitivity = math.clamp(settings.CameraSensitivity or 1, 0.1, 10)
end

local function applyInvertY()
	local userGameSettings = UserSettings():GetService("UserGameSettings")
	-- SetCameraYInvertVisible takes no argument, so read first and only flip on a mismatch.
	if userGameSettings.GetCameraYInvertVisible and userGameSettings:GetCameraYInvertVisible() ~= (settings.InvertY and true or false) then
		userGameSettings:SetCameraYInvertVisible()
	end
end

local function applyReduceMotion()
	local on = settings.ReduceMotion and true or false
	for _, name in ipairs({ "SpringUI", "StudMotion" }) do
		local found = ReplicatedStorage:FindFirstChild(name)
		if found then
			local ok, mod = pcall(require, found)
			if ok and type(mod) == "table" and type(mod.SetReduceMotion) == "function" then
				mod.SetReduceMotion(on)
			end
		end
	end
end

local function applyPalette()
	local name = SettingsConfig.PALETTE_EFFECT_NAME
	local effect = Lighting:FindFirstChild(name)
	local adjustment = SettingsConfig.PALETTE_ADJUSTMENTS[settings.ColorBlindPalette or "None"]
	if not adjustment then
		if effect then
			effect:Destroy()
		end
		return
	end
	if not effect then
		effect = make("ColorCorrectionEffect", { Name = name }, Lighting)
	end
	effect.Saturation = adjustment.saturation or 0
	local tint = adjustment.tint or { 1, 1, 1 }
	effect.TintColor = Color3.fromRGB(
		math.clamp(math.floor((tint[1] or 1) * 255), 0, 255),
		math.clamp(math.floor((tint[2] or 1) * 255), 0, 255),
		math.clamp(math.floor((tint[3] or 1) * 255), 0, 255)
	)
end

local function notify(key)
	local fns = listeners[key]
	if not fns then
		return
	end
	for _, fn in ipairs(fns) do
		local ok, err = pcall(fn, settings[key], settings)
		if not ok then
			warn(("settings-menu: listener for %s failed: %s"):format(tostring(key), tostring(err)))
		end
	end
end

local function applyAll()
	applyVolumes()
	applySensitivity()
	applyInvertY()
	applyReduceMotion()
	applyPalette()
end

local function adopt(updated, changedKey)
	settings = SettingsData.normalize(updated)
	applyAll()
	if changedKey then
		notify(changedKey)
	end
	render()
end

local function save()
	saveEvent:FireServer(settings)
end

-- ── the window ────────────────────────────────────────────────────────────────────────

local gui = make("ScreenGui", {
	Name = "SettingsMenu",
	ResetOnSpawn = false,
	IgnoreGuiInset = true,
	DisplayOrder = 60,
	ZIndexBehavior = Enum.ZIndexBehavior.Sibling,
}, playerGui)

local scrim = make("Frame", {
	Name = "Scrim",
	Size = UDim2.fromScale(1, 1),
	BackgroundColor3 = Color3.new(0, 0, 0),
	BackgroundTransparency = 0.45,
	BorderSizePixel = 0,
	Visible = false,
	ZIndex = 1,
}, gui)

local window = make("Frame", {
	Name = "Window",
	AnchorPoint = Vector2.new(SettingsConfig.UI_ANCHOR_X, SettingsConfig.UI_ANCHOR_Y),
	Position = UDim2.new(SettingsConfig.UI_ANCHOR_X, 0, SettingsConfig.UI_ANCHOR_Y, 0),
	Size = UDim2.fromOffset(SettingsConfig.UI_WIDTH, 460),
	BackgroundColor3 = BG,
	BorderSizePixel = 0,
	Visible = false,
	ZIndex = 2,
}, gui)
make("UICorner", { CornerRadius = UDim.new(0, 14), Parent = window })
make("UIStroke", { Color = Color3.new(1, 1, 1), Transparency = 0.85, Thickness = 1, Parent = window })

local title = make("TextLabel", {
	Name = "Title",
	Size = UDim2.new(1, -32, 0, 34),
	Position = UDim2.new(0, 16, 0, 8),
	BackgroundTransparency = 1,
	Font = Enum.Font.GothamBold,
	TextSize = 17,
	TextXAlignment = Enum.TextXAlignment.Left,
	TextColor3 = FG,
	Text = SettingsConfig.UI_TITLE,
	ZIndex = 3,
}, window)

local closeButton = make("TextButton", {
	Name = "Close",
	AnchorPoint = Vector2.new(1, 0),
	Position = UDim2.new(1, -12, 0, 10),
	Size = UDim2.fromOffset(30, 30),
	BackgroundColor3 = Color3.fromRGB(38, 42, 52),
	BorderSizePixel = 0,
	Font = Enum.Font.GothamBold,
	TextSize = 15,
	TextColor3 = MUTED,
	Text = "X",
	AutoButtonColor = true,
	ZIndex = 3,
}, window)
make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = closeButton })
closeButton.SelectionGroup = true

local body = make("ScrollingFrame", {
	Name = "Body",
	Size = UDim2.new(1, -24, 1, -96),
	Position = UDim2.new(0, 12, 0, 48),
	BackgroundTransparency = 1,
	BorderSizePixel = 0,
	ScrollBarThickness = 4,
	CanvasSize = UDim2.new(),
	AutomaticCanvasSize = Enum.AutomaticSize.Y,
	ZIndex = 3,
}, window)
make("UIListLayout", {
	SortOrder = Enum.SortOrder.LayoutOrder,
	Padding = UDim.new(0, 6),
	Parent = body,
})

local footer = make("Frame", {
	Name = "Footer",
	AnchorPoint = Vector2.new(0, 1),
	Position = UDim2.new(0, 12, 1, -12),
	Size = UDim2.new(1, -24, 0, 34),
	BackgroundTransparency = 1,
	ZIndex = 3,
}, window)

local restoreButton = make("TextButton", {
	Name = "Restore",
	Size = UDim2.fromOffset(160, 32),
	BackgroundColor3 = Color3.fromRGB(38, 42, 52),
	BorderSizePixel = 0,
	Font = Enum.Font.GothamMedium,
	TextSize = 13,
	TextColor3 = FG,
	Text = SettingsConfig.UI_RESTORE_LABEL,
	ZIndex = 4,
}, footer)
make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = restoreButton })

local function label(text, order, parent)
	return make("TextLabel", {
		Size = UDim2.new(0.52, -8, 1, 0),
		BackgroundTransparency = 1,
		Font = Enum.Font.Gotham,
		TextSize = 13,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextColor3 = FG,
		Text = text,
		LayoutOrder = order,
		ZIndex = 4,
	}, parent)
end

local function rowFrame(key, order)
	local row = make("Frame", {
		Name = key,
		Size = UDim2.new(1, -4, 0, SettingsConfig.UI_ROW_HEIGHT),
		BackgroundColor3 = Color3.fromRGB(26, 29, 37),
		BorderSizePixel = 0,
		LayoutOrder = order,
		ZIndex = 3,
	}, body)
	make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = row })
	make("UIPadding", {
		PaddingLeft = UDim.new(0, 10),
		PaddingRight = UDim.new(0, 10),
		Parent = row,
	})
	return row
end

local function stepButton(text, order, parent)
	local button = make("TextButton", {
		Name = text,
		Size = UDim2.fromOffset(26, 24),
		BackgroundColor3 = Color3.fromRGB(44, 49, 61),
		BorderSizePixel = 0,
		Font = Enum.Font.GothamBold,
		TextSize = 15,
		TextColor3 = FG,
		Text = text,
		LayoutOrder = order,
		ZIndex = 5,
	}, parent)
	make("UICorner", { CornerRadius = UDim.new(0, 6), Parent = button })
	return button
end

local function sliderRow(key, rule, order)
	local row = rowFrame(key, order)
	label(rule.label or key, 1, row)

	local minus = stepButton("-", 2, row)
	minus.AnchorPoint = Vector2.new(1, 0.5)
	minus.Position = UDim2.new(1, -108, 0.5, 0)

	local track = make("TextButton", {
		Name = "Track",
		AnchorPoint = Vector2.new(0, 0.5),
		Position = UDim2.new(1, -100, 0.5, 0),
		Size = UDim2.fromOffset(64, 16),
		BackgroundColor3 = Color3.fromRGB(44, 49, 61),
		BorderSizePixel = 0,
		Text = "",
		AutoButtonColor = false,
		LayoutOrder = 3,
		ZIndex = 5,
	}, row)
	make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = track })

	local fill = make("Frame", {
		Name = "Fill",
		Size = UDim2.fromScale(1, 1),
		BackgroundColor3 = ACCENT,
		BorderSizePixel = 0,
		ZIndex = 6,
	}, track)
	make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = fill })

	local plus = stepButton("+", 4, row)
	plus.AnchorPoint = Vector2.new(0, 0.5)
	plus.Position = UDim2.new(1, -32, 0.5, 0)

	local value = make("TextLabel", {
		Name = "Value",
		AnchorPoint = Vector2.new(1, 0.5),
		Position = UDim2.new(1, -112, 0.5, 0),
		Size = UDim2.fromOffset(52, 20),
		BackgroundTransparency = 1,
		Font = Enum.Font.GothamMedium,
		TextSize = 12,
		TextXAlignment = Enum.TextXAlignment.Right,
		TextColor3 = MUTED,
		Text = "",
		ZIndex = 5,
	}, row)

	local function fractionOf(x)
		local span = rule.max - rule.min
		if span <= 0 then
			return 0
		end
		return math.clamp((x - rule.min) / span, 0, 1)
	end

	local dragging = false
	local function fromPosition(x)
		local relative = math.clamp((x - track.AbsolutePosition.X) / math.max(1, track.AbsoluteSize.X), 0, 1)
		return rule.min + relative * (rule.max - rule.min)
	end

	-- shouldSave is false while dragging: a save per mouse-move would trip the server's
	-- rate limit and could drop the *final* position, storing something stale.
	local function commit(rawValue, shouldSave)
		local updated, ok = SettingsData.set(settings, key, rawValue)
		if ok then
			adopt(updated, key)
			if shouldSave then
				save()
			end
		end
	end

	track.MouseButton1Down:Connect(function()
		dragging = true
		commit(fromPosition(UserInputService:GetMouseLocation().X), false)
	end)
	minus.Activated:Connect(function()
		commit(SettingsData.step(key, settings[key], -1), true)
	end)
	plus.Activated:Connect(function()
		commit(SettingsData.step(key, settings[key], 1), true)
	end)

	UserInputService.InputChanged:Connect(function(input)
		if dragging and input.UserInputType == Enum.UserInputType.MouseMovement then
			commit(fromPosition(input.Position.X), false)
		end
	end)
	UserInputService.InputEnded:Connect(function(input)
		if dragging and input.UserInputType == Enum.UserInputType.MouseButton1 then
			dragging = false
			save()
		end
	end)

	rows[key] = {
		kind = "number",
		refresh = function()
			local current = settings[key] or rule.default
			fill.Size = UDim2.fromScale(fractionOf(current), 1)
			value.Text = ("%.2f"):format(current)
		end,
	}
end

local function toggleRow(key, rule, order)
	local row = rowFrame(key, order)
	label(rule.label or key, 1, row)

	local button = make("TextButton", {
		Name = "Toggle",
		AnchorPoint = Vector2.new(1, 0.5),
		Position = UDim2.new(1, 0, 0.5, 0),
		Size = UDim2.fromOffset(64, 24),
		BackgroundColor3 = Color3.fromRGB(44, 49, 61),
		BorderSizePixel = 0,
		Font = Enum.Font.GothamBold,
		TextSize = 12,
		TextColor3 = FG,
		Text = "",
		ZIndex = 5,
	}, row)
	make("UICorner", { CornerRadius = UDim.new(0, 12), Parent = button })

	button.Activated:Connect(function()
		local updated, ok = SettingsData.set(settings, key, not settings[key])
		if ok then
			adopt(updated, key)
			save()
		end
	end)

	rows[key] = {
		kind = "boolean",
		refresh = function()
			local on = settings[key] and true or false
			button.Text = on and "On" or "Off"
			button.BackgroundColor3 = on and ACCENT or Color3.fromRGB(44, 49, 61)
			button.TextColor3 = on and Color3.fromRGB(12, 16, 22) or MUTED
		end,
	}
end

local function enumRow(key, rule, order)
	local row = rowFrame(key, order)
	label(rule.label or key, 1, row)

	local button = make("TextButton", {
		Name = "Cycle",
		AnchorPoint = Vector2.new(1, 0.5),
		Position = UDim2.new(1, 0, 0.5, 0),
		Size = UDim2.fromOffset(132, 24),
		BackgroundColor3 = Color3.fromRGB(44, 49, 61),
		BorderSizePixel = 0,
		Font = Enum.Font.GothamMedium,
		TextSize = 12,
		TextColor3 = FG,
		Text = "",
		ZIndex = 5,
	}, row)
	make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = button })

	button.Activated:Connect(function()
		local options = rule.options or {}
		local current = settings[key]
		local index = 1
		for i, option in ipairs(options) do
			if option == current then
				index = i
				break
			end
		end
		local nextOption = options[(index % #options) + 1]
		local updated, ok = SettingsData.set(settings, key, nextOption)
		if ok then
			adopt(updated, key)
			save()
		end
	end)

	rows[key] = {
		kind = "enum",
		refresh = function()
			button.Text = tostring(settings[key] or rule.default)
		end,
	}
end

local function keybindRow(action, order)
	local row = rowFrame("Keybind_" .. action, order)
	label(action, 1, row)

	local button = make("TextButton", {
		Name = "Rebind",
		AnchorPoint = Vector2.new(1, 0.5),
		Position = UDim2.new(1, 0, 0.5, 0),
		Size = UDim2.fromOffset(110, 24),
		BackgroundColor3 = Color3.fromRGB(44, 49, 61),
		BorderSizePixel = 0,
		Font = Enum.Font.GothamMedium,
		TextSize = 12,
		TextColor3 = FG,
		Text = "",
		ZIndex = 5,
	}, row)
	make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = button })

	button.Activated:Connect(function()
		captureFor = captureFor == action and nil or action
		render()
	end)

	rows["Keybind_" .. action] = {
		kind = "keybind",
		refresh = function()
			local bound = (settings.Keybinds or {})[action]
			if captureFor == action then
				button.Text = SettingsConfig.UI_KEYBIND_HINT
				button.BackgroundColor3 = ACCENT
				button.TextColor3 = Color3.fromRGB(12, 16, 22)
			else
				button.Text = bound or "—"
				button.BackgroundColor3 = Color3.fromRGB(44, 49, 61)
				button.TextColor3 = bound and FG or MUTED
			end
		end,
	}
end

local order = 0
for _, key in ipairs(SettingsConfig.ORDER) do
	local rule = SettingsData.SCHEMA[key]
	if rule then
		order += 1
		if rule.kind == "number" then
			sliderRow(key, rule, order)
		elseif rule.kind == "boolean" then
			toggleRow(key, rule, order)
		elseif rule.kind == "enum" then
			enumRow(key, rule, order)
		end
	end
end

order += 1
make("TextLabel", {
	Name = "KeybindHeader",
	Size = UDim2.new(1, -4, 0, 22),
	BackgroundTransparency = 1,
	Font = Enum.Font.GothamBold,
	TextSize = 12,
	TextXAlignment = Enum.TextXAlignment.Left,
	TextColor3 = MUTED,
	Text = "Key binds",
	LayoutOrder = order,
	ZIndex = 4,
}, body)

for _, action in ipairs(SettingsConfig.KEYBIND_ORDER) do
	order += 1
	keybindRow(action, order)
end

render = function()
	for _, row in pairs(rows) do
		row.refresh()
	end
	restoreButton.TextColor3 = SettingsData.isDefault(settings) and MUTED or FG
end

restoreButton.Activated:Connect(function()
	adopt(SettingsData.defaults())
	save()
end)

local function setOpen(wantOpen)
	-- ui/kit's tutorial lock suppresses panels that are not on its allow list. Honour it
	-- when the kit is installed rather than opening over a tutorial.
	if wantOpen and UIManager and type(UIManager.canOpen) == "function" then
		if not UIManager.canOpen(SettingsConfig.WINDOW_KEY) then
			return
		end
	end
	isOpen = wantOpen and true or false
	window.Visible = isOpen
	scrim.Visible = isOpen
	if not isOpen then
		captureFor = nil
	end
	render()
end

closeButton.Activated:Connect(function()
	setOpen(false)
end)
scrim.InputBegan:Connect(function(input)
	if input.UserInputType == Enum.UserInputType.MouseButton1 then
		setOpen(false)
	end
end)

shared.SettingsAPI = {
	Get = function(key)
		if key == nil then
			return settings
		end
		return settings[key]
	end,
	GetKeybind = function(action)
		return (settings.Keybinds or {})[action]
	end,
	Set = function(key, value)
		local updated, ok, reason = SettingsData.set(settings, key, value)
		if not ok then
			return false, reason
		end
		adopt(updated, key)
		save()
		return true, nil
	end,
	Reset = function()
		adopt(SettingsData.defaults())
		save()
	end,
	Open = function()
		setOpen(true)
	end,
	Close = function()
		setOpen(false)
	end,
	Toggle = function()
		setOpen(not isOpen)
	end,
	IsOpen = function()
		return isOpen
	end,
	onChanged = function(key, fn)
		if type(key) ~= "string" or type(fn) ~= "function" then
			return { Disconnect = function() end }
		end
		local list = listeners[key]
		if not list then
			list = {}
			listeners[key] = list
		end
		table.insert(list, fn)
		return {
			Disconnect = function()
				for index, existing in ipairs(list) do
					if existing == fn then
						table.remove(list, index)
						break
					end
				end
			end,
		}
	end,
}

UserInputService.InputBegan:Connect(function(input, gameProcessed)
	if captureFor then
		if input.UserInputType == Enum.UserInputType.Keyboard then
			local action = captureFor
			captureFor = nil
			if input.KeyCode == Enum.KeyCode.Backspace then
				local updated, ok = SettingsData.bindKey(settings, action, nil)
				if ok then
					adopt(updated, "Keybinds." .. action)
					save()
				end
			else
				local updated, ok, reason = SettingsData.bindKey(settings, action, input.KeyCode.Name)
				if ok then
					adopt(updated, "Keybinds." .. action)
					save()
				elseif reason and reason:match("^conflict:") and RunService:IsStudio() then
					warn(("settings-menu: %s is already bound to %s"):format(input.KeyCode.Name, reason:sub(10)))
				end
				render()
			end
		end
		return
	end
	local menuKey = shared.SettingsAPI.GetKeybind("Menu")
	if menuKey and input.UserInputType == Enum.UserInputType.Keyboard and not gameProcessed then
		if input.KeyCode.Name == menuKey then
			shared.SettingsAPI.Toggle()
		end
	end
end)

loadEvent.OnClientEvent:Connect(function(payload)
	local updated = SettingsData.normalize(payload)
	local changed = {}
	for key in pairs(SettingsData.SCHEMA) do
		if updated[key] ~= settings[key] then
			table.insert(changed, key)
		end
	end
	adopt(updated)
	for _, key in ipairs(changed) do
		notify(key)
	end
end)

-- ── optional ui/kit integration ──────────────────────────────────────────────────────
-- This system draws its own window so it can be installed on its own. When ui/kit is also
-- installed it joins it: the window registers with UIManager (so UIManager.open("Settings")
-- works, one panel shows at a time, and the tutorial lock applies) and a HUD tile makes the
-- menu reachable without knowing the key bind. Neither is required.

local function optionalModule(name)
	local holder = script.Parent:FindFirstChild(name) or ReplicatedStorage:FindFirstChild(name)
	if not holder then
		return nil
	end
	local ok, module = pcall(require, holder)
	if not ok then
		warn(("settings-menu: could not require %s (%s)"):format(name, tostring(module)))
		return nil
	end
	return module
end

UIManager = optionalModule("UIManager")
if UIManager and type(UIManager.register) == "function" then
	UIManager.register(SettingsConfig.WINDOW_KEY, window)
	-- UIManager.open/close/toggle write Visible directly. Keep isOpen and the scrim in step
	-- with it, so the window cannot be opened by one path and left half-closed by the other.
	window:GetPropertyChangedSignal("Visible"):Connect(function()
		if window.Visible ~= isOpen then
			setOpen(window.Visible)
		end
	end)
end

local UIStyle = optionalModule("UIStyle")
if UIStyle and type(UIStyle.hudButton) == "function" then
	local Icons = optionalModule("Icons")
	local ok, err = pcall(function()
		local tile = UIStyle.hudButton({
			label = SettingsConfig.UI_TITLE,
			icon = Icons and Icons.Settings or "S",
			color = UIStyle.Colors and UIStyle.Colors.grey,
			order = SettingsConfig.HUD_ORDER,
		})
		tile.Activated:Connect(function()
			shared.SettingsAPI.Toggle()
		end)
	end)
	if not ok then
		warn(("settings-menu: no HUD tile (%s) — press %s to open the menu"):format(tostring(err), tostring(SettingsConfig.KEYBINDS.Menu)))
	end
end

applyAll()
render()
setOpen(false)
