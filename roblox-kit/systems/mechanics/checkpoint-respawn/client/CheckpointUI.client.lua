--!nonstrict
local CollectionService = game:GetService("CollectionService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local TweenService = game:GetService("TweenService")
local Workspace = game:GetService("Workspace")

local Remotes = require(ReplicatedStorage:WaitForChild("Remotes"))
local CheckpointConfig = require(ReplicatedStorage:WaitForChild("CheckpointConfig"))

local player = Players.LocalPlayer
local playerGui = player:WaitForChild("PlayerGui")

local stateEvent = Remotes.event(CheckpointConfig.REMOTE_STATE)
local claimEvent = Remotes.event(CheckpointConfig.REMOTE_CLAIM)

local state = { current = nil, currentOrder = nil, done = 0, total = 0, claimed = {} }

local gui = Instance.new("ScreenGui")
gui.Name = "CheckpointUI"
gui.ResetOnSpawn = false
gui.IgnoreGuiInset = true
gui.DisplayOrder = 20
gui.Parent = playerGui

local pill = Instance.new("Frame")
pill.Name = "Pill"
pill.AnchorPoint = Vector2.new(0.5, 1)
pill.Position = UDim2.new(CheckpointConfig.UI_ANCHOR_X, 0, CheckpointConfig.UI_ANCHOR_Y, 0)
pill.Size = UDim2.fromOffset(220, 46)
pill.BackgroundColor3 = Color3.fromRGB(18, 20, 26)
pill.BackgroundTransparency = 0.18
pill.BorderSizePixel = 0
pill.Visible = CheckpointConfig.UI_ENABLED
pill.Parent = gui

local corner = Instance.new("UICorner")
corner.CornerRadius = UDim.new(0, 12)
corner.Parent = pill

local stroke = Instance.new("UIStroke")
stroke.Color = Color3.fromRGB(255, 255, 255)
stroke.Transparency = 0.82
stroke.Thickness = 1
stroke.Parent = pill

local title = Instance.new("TextLabel")
title.Name = "Title"
title.Size = UDim2.new(1, -24, 0, 18)
title.Position = UDim2.new(0, 12, 0, 5)
title.BackgroundTransparency = 1
title.Font = Enum.Font.GothamBold
title.TextSize = 13
title.TextXAlignment = Enum.TextXAlignment.Left
title.TextColor3 = Color3.fromRGB(228, 232, 240)
title.Text = CheckpointConfig.UI_TITLE
title.Parent = pill

local counter = Instance.new("TextLabel")
counter.Name = "Counter"
counter.Size = UDim2.new(1, -24, 0, 18)
counter.Position = UDim2.new(0, 12, 0, 23)
counter.BackgroundTransparency = 1
counter.Font = Enum.Font.Gotham
counter.TextSize = 13
counter.TextXAlignment = Enum.TextXAlignment.Left
counter.TextColor3 = Color3.fromRGB(158, 168, 186)
counter.Text = CheckpointConfig.UI_PROGRESS_FORMAT:format(0, 0)
counter.Parent = pill

local barBack = Instance.new("Frame")
barBack.Name = "BarBack"
barBack.Size = UDim2.new(1, -24, 0, 3)
barBack.Position = UDim2.new(0, 12, 1, -6)
barBack.BackgroundColor3 = Color3.fromRGB(255, 255, 255)
barBack.BackgroundTransparency = 0.86
barBack.BorderSizePixel = 0
barBack.Parent = pill

local barFill = Instance.new("Frame")
barFill.Name = "BarFill"
barFill.Size = UDim2.fromScale(0, 1)
barFill.BackgroundColor3 = Color3.fromRGB(96, 214, 147)
barFill.BorderSizePixel = 0
barFill.Parent = barBack

local toast = Instance.new("TextLabel")
toast.Name = "Toast"
toast.AnchorPoint = Vector2.new(0.5, 1)
toast.Position = UDim2.new(CheckpointConfig.UI_ANCHOR_X, 0, CheckpointConfig.UI_ANCHOR_Y - 0.07, 0)
toast.Size = UDim2.fromOffset(280, 30)
toast.BackgroundTransparency = 1
toast.Font = Enum.Font.GothamBold
toast.TextSize = 16
toast.TextColor3 = Color3.fromRGB(255, 255, 255)
toast.TextStrokeTransparency = 0.55
toast.TextTransparency = 1
toast.Text = ""
toast.Parent = gui

local function render()
	pill.Visible = CheckpointConfig.UI_ENABLED and state.total > 0
	counter.Text = CheckpointConfig.UI_PROGRESS_FORMAT:format(state.done, state.total)
	local fraction = state.total > 0 and (state.done / state.total) or 0
	TweenService:Create(barFill, TweenInfo.new(0.28, Enum.EasingStyle.Quart), {
		Size = UDim2.fromScale(fraction, 1),
	}):Play()
end

local toastThread = nil
local function showToast(text)
	toast.Text = text
	TweenService:Create(toast, TweenInfo.new(0.18), { TextTransparency = 0 }):Play()
	if toastThread then
		task.cancel(toastThread)
	end
	toastThread = task.delay(CheckpointConfig.UI_TOAST_SECONDS, function()
		TweenService:Create(toast, TweenInfo.new(0.4), { TextTransparency = 1 }):Play()
		toastThread = nil
	end)
end

stateEvent.OnClientEvent:Connect(function(payload)
	if type(payload) ~= "table" then
		return
	end
	local previous = state.current
	state.current = payload.current
	state.currentOrder = payload.currentOrder
	state.done = payload.done or 0
	state.total = payload.total or 0
	state.claimed = type(payload.claimed) == "table" and payload.claimed or {}
	render()
	if state.current and state.current ~= previous then
		showToast(CheckpointConfig.UI_REACHED_FORMAT:format(
			state.currentOrder and ("%s %d"):format(CheckpointConfig.UI_TITLE, state.currentOrder) or state.current))
	end
end)

-- With TOUCH_TO_CLAIM off, the client asks; the server still decides (distance, cooldown,
-- rate limit and progression are all checked there), so a forged request gains nothing.
local function nearbyCheckpointIds()
	local found = {}
	for _, instance in ipairs(CollectionService:GetTagged(CheckpointConfig.CHECKPOINT_TAG)) do
		table.insert(found, instance)
	end
	local folder = Workspace:FindFirstChild(CheckpointConfig.CHECKPOINT_FOLDER)
	if folder then
		for _, instance in ipairs(folder:GetChildren()) do
			table.insert(found, instance)
		end
	end
	return found
end

local lastScan = 0
RunService.Heartbeat:Connect(function()
	if CheckpointConfig.TOUCH_TO_CLAIM then
		return
	end
	local now = os.clock()
	if now - lastScan < 0.2 then
		return
	end
	lastScan = now
	local character = player.Character
	local root = character and character:FindFirstChild("HumanoidRootPart")
	if not root then
		return
	end
	for _, instance in ipairs(nearbyCheckpointIds()) do
		if instance:IsA("BasePart") and (instance.Position - root.Position).Magnitude <= CheckpointConfig.CLAIM_RADIUS then
			local id = instance:GetAttribute(CheckpointConfig.ID_ATTRIBUTE)
			if type(id) ~= "string" or id == "" then
				id = instance.Name
			end
			if not state.claimed[id] then
				claimEvent:FireServer(id)
			end
		end
	end
end)

render()
