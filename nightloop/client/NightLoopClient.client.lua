--!nonstrict
--[[
	NightLoop · client HUD
	StarterPlayer > StarterPlayerScripts

	Pure code-built UI, no assets. Shows the in-fiction clock and the current
	phase, and flashes the phase name when it changes.

	Reads Config for display options. If you port the package without the HUD,
	delete this script — nothing on the server depends on it.
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local remotes = ReplicatedStorage:WaitForChild("NightLoopRemotes", 30)
if not remotes then
	return
end
local stateRemote = remotes:WaitForChild("NightState")
local cueRemote = remotes:WaitForChild("NightCue")

local player = Players.LocalPlayer
local camera = workspace.CurrentCamera
local gui = Instance.new("ScreenGui")
gui.Name = "NightLoopHud"
gui.ResetOnSpawn = false
gui.IgnoreGuiInset = true
gui.ZIndexBehavior = Enum.ZIndexBehavior.Sibling
gui.Parent = player:WaitForChild("PlayerGui")

local root = Instance.new("Frame")
root.BackgroundTransparency = 1
root.Size = UDim2.new(0, 260, 0, 64)
root.Position = UDim2.new(0.5, -130, 0, 18)
root.Parent = gui

local clock = Instance.new("TextLabel")
clock.BackgroundTransparency = 1
clock.Size = UDim2.new(1, 0, 0, 38)
clock.Font = Enum.Font.Code
clock.Text = "12:00 AM"
clock.TextSize = 34
clock.TextColor3 = Color3.fromRGB(236, 232, 225)
clock.TextStrokeTransparency = 0.45
clock.TextStrokeColor3 = Color3.new(0, 0, 0)
clock.Parent = root

local phase = Instance.new("TextLabel")
phase.BackgroundTransparency = 1
phase.Position = UDim2.new(0, 0, 0, 36)
phase.Size = UDim2.new(1, 0, 0, 20)
phase.Font = Enum.Font.Gotham
phase.Text = ""
phase.TextSize = 13
phase.TextColor3 = Color3.fromRGB(168, 160, 150)
phase.TextStrokeTransparency = 0.7
phase.Parent = root

-- thin progress bar under the clock
local barBg = Instance.new("Frame")
barBg.BackgroundColor3 = Color3.fromRGB(40, 38, 36)
barBg.BorderSizePixel = 0
barBg.Position = UDim2.new(0, 0, 0, 58)
barBg.Size = UDim2.new(1, 0, 0, 2)
barBg.Parent = root

local bar = Instance.new("Frame")
bar.BackgroundColor3 = Color3.fromRGB(190, 60, 52)
bar.BorderSizePixel = 0
bar.Size = UDim2.new(0, 0, 1, 0)
bar.Parent = barBg

-- flash charge pips, bottom centre
local charges = Instance.new("Frame")
charges.BackgroundTransparency = 1
charges.AnchorPoint = Vector2.new(0.5, 1)
charges.Position = UDim2.new(0.5, 0, 1, -28)
charges.Size = UDim2.new(0, 140, 0, 10)
charges.Parent = gui

local pipLayout = Instance.new("UIListLayout")
pipLayout.FillDirection = Enum.FillDirection.Horizontal
pipLayout.HorizontalAlignment = Enum.HorizontalAlignment.Center
pipLayout.VerticalAlignment = Enum.VerticalAlignment.Center
pipLayout.Padding = UDim.new(0, 6)
pipLayout.Parent = charges

local pips = {}
local function buildPips(count)
	for _, pip in ipairs(pips) do
		pip:Destroy()
	end
	table.clear(pips)
	for i = 1, count do
		local pip = Instance.new("Frame")
		pip.Size = UDim2.new(0, 22, 0, 5)
		pip.BackgroundColor3 = Color3.fromRGB(226, 214, 180)
		pip.BorderSizePixel = 0
		pip.LayoutOrder = i
		local corner = Instance.new("UICorner")
		corner.CornerRadius = UDim.new(0, 2)
		corner.Parent = pip
		pip.Parent = charges
		pips[i] = pip
	end
end

local function setCharges(current, max)
	if #pips ~= max then
		buildPips(max)
	end
	for i, pip in ipairs(pips) do
		TweenService:Create(pip, TweenInfo.new(0.18), {
			BackgroundTransparency = i <= current and 0 or 0.78,
		}):Play()
	end
end

-- strike markers, top right: how many breaches you have left
local strikeRow = Instance.new("Frame")
strikeRow.BackgroundTransparency = 1
strikeRow.AnchorPoint = Vector2.new(1, 0)
strikeRow.Position = UDim2.new(1, -18, 0, 18)
strikeRow.Size = UDim2.new(0, 120, 0, 18)
strikeRow.Parent = gui

local strikeLayout = Instance.new("UIListLayout")
strikeLayout.FillDirection = Enum.FillDirection.Horizontal
strikeLayout.HorizontalAlignment = Enum.HorizontalAlignment.Right
strikeLayout.VerticalAlignment = Enum.VerticalAlignment.Center
strikeLayout.Padding = UDim.new(0, 7)
strikeLayout.Parent = strikeRow

local strikeMarks = {}
local function buildStrikes(count)
	for _, m in ipairs(strikeMarks) do
		m:Destroy()
	end
	table.clear(strikeMarks)
	for i = 1, count do
		local mark = Instance.new("Frame")
		mark.Size = UDim2.new(0, 13, 0, 13)
		mark.BackgroundColor3 = Color3.fromRGB(150, 196, 168)
		mark.BorderSizePixel = 0
		mark.Rotation = 45
		mark.LayoutOrder = i
		mark.Parent = strikeRow
		strikeMarks[i] = mark
	end
end

local function setStrikes(used, max)
	if max and max > 0 and #strikeMarks ~= max then
		buildStrikes(max)
	end
	for i, mark in ipairs(strikeMarks) do
		local spent = i <= used
		TweenService:Create(mark, TweenInfo.new(0.25), {
			BackgroundColor3 = spent and Color3.fromRGB(120, 32, 28)
				or Color3.fromRGB(150, 196, 168),
			BackgroundTransparency = spent and 0.45 or 0,
			Rotation = spent and 45 or 45,
		}):Play()
	end
end

-- end-of-night card
local card = Instance.new("TextLabel")
card.BackgroundTransparency = 1
card.AnchorPoint = Vector2.new(0.5, 0.5)
card.Position = UDim2.new(0.5, 0, 0.44, 0)
card.Size = UDim2.new(1, 0, 0, 70)
card.Font = Enum.Font.GothamBold
card.TextSize = 46
card.TextTransparency = 1
card.TextColor3 = Color3.fromRGB(240, 236, 226)
card.Text = ""
card.Parent = gui

local cardSub = Instance.new("TextLabel")
cardSub.BackgroundTransparency = 1
cardSub.AnchorPoint = Vector2.new(0.5, 0.5)
cardSub.Position = UDim2.new(0.5, 0, 0.44, 48)
cardSub.Size = UDim2.new(1, 0, 0, 28)
cardSub.Font = Enum.Font.Gotham
cardSub.TextSize = 17
cardSub.TextTransparency = 1
cardSub.TextColor3 = Color3.fromRGB(196, 190, 178)
cardSub.Text = ""
cardSub.Parent = gui

local function showCard(title, subtitle, color, holdFor)
	card.Text = title
	card.TextColor3 = color
	cardSub.Text = subtitle or ""
	for _, label in ipairs({ card, cardSub }) do
		label.TextTransparency = 1
		TweenService:Create(label, TweenInfo.new(1.1), { TextTransparency = 0 }):Play()
	end
	task.delay(holdFor or 7, function()
		for _, label in ipairs({ card, cardSub }) do
			TweenService:Create(label, TweenInfo.new(1.6), { TextTransparency = 1 }):Play()
		end
	end)
end

-- full-screen flash used for phase changes and scares
local flash = Instance.new("Frame")
flash.BackgroundColor3 = Color3.new(0, 0, 0)
flash.BackgroundTransparency = 1
flash.Size = UDim2.fromScale(1, 1)
flash.ZIndex = 0
flash.Parent = gui

local function formatClock(fraction)
	-- night runs 12am -> 6am by default
	local startHour, endHour = 0, 6
	local total = (endHour - startHour) * 60
	local minutes = startHour * 60 + fraction * total
	local hour24 = math.floor(minutes / 60) % 24
	local minute = math.floor(minutes % 60)
	local suffix = hour24 < 12 and "AM" or "PM"
	local hour12 = hour24 % 12
	if hour12 == 0 then
		hour12 = 12
	end
	return string.format("%d:%02d %s", hour12, minute, suffix)
end

local lastPhase = nil
local warnActive = false
local baseFov = camera and camera.FieldOfView or 70

-- camera punch: kick the FOV out then ease it back
local function fovKick(amount, outTime, inTime)
	if not camera then
		return
	end
	TweenService:Create(camera, TweenInfo.new(outTime,
		Enum.EasingStyle.Quint, Enum.EasingDirection.Out),
		{ FieldOfView = baseFov + amount }):Play()
	task.delay(outTime, function()
		TweenService:Create(camera, TweenInfo.new(inTime,
			Enum.EasingStyle.Quad, Enum.EasingDirection.Out),
			{ FieldOfView = baseFov }):Play()
	end)
end

stateRemote.OnClientEvent:Connect(function(state)
	if not state or not state.running then
		if state and not state.running and state.remaining ~= nil and state.remaining <= 0 then
			clock.Text = "6:00 AM"
			phase.Text = "you made it"
			bar.Size = UDim2.new(1, 0, 1, 0)
		end
		return
	end

	local flags = state.hud
	if flags then
		gui.Enabled = flags.enabled ~= false
		clock.Visible = flags.showClock ~= false
		phase.Visible = flags.showPhase ~= false
	end

	if state.maxStrikes then
		setStrikes(state.strikes or 0, state.maxStrikes)
	end

	local fraction = math.clamp(state.elapsed / math.max(1, state.duration), 0, 1)
	clock.Text = formatClock(fraction)
	bar.Size = UDim2.new(fraction, 0, 1, 0)

	if state.phaseName ~= lastPhase then
		lastPhase = state.phaseName
		phase.Text = string.format("%s  ·  %d/%d",
			state.phaseName, state.phaseIndex, state.phaseCount)

		flash.BackgroundTransparency = 0.55
		TweenService:Create(flash, TweenInfo.new(1.1), {
			BackgroundTransparency = 1,
		}):Play()
	end
end)

cueRemote.OnClientEvent:Connect(function(cue)
	if not cue then
		return
	end

	if cue.kind == "flashState" then
		setCharges(cue.charges, cue.max)
		return
	end

	if cue.kind == "flashFired" then
		setCharges(cue.charges, cue.max)
		-- the punch-out/ease-in the flash should feel like
		fovKick(14, 0.07, 0.42)
		flash.BackgroundColor3 = Color3.fromRGB(255, 252, 240)
		flash.BackgroundTransparency = 0.74
		TweenService:Create(flash, TweenInfo.new(0.22), {
			BackgroundTransparency = 1,
		}):Play()
		task.delay(0.3, function()
			flash.BackgroundColor3 = Color3.new(0, 0, 0)
		end)
		return
	end

	if cue.kind == "flashRecharged" then
		setCharges(cue.charges, cue.max)
		-- small inward breath so a recharge is felt, not just seen
		fovKick(-5, 0.12, 0.3)
		return
	end

	if cue.kind == "flashEmpty" then
		for _, pip in ipairs(pips) do
			pip.BackgroundColor3 = Color3.fromRGB(190, 70, 60)
		end
		task.delay(0.35, function()
			for _, pip in ipairs(pips) do
				pip.BackgroundColor3 = Color3.fromRGB(226, 214, 180)
			end
		end)
		return
	end

	if cue.kind == "breachWarning" then
		-- urgent but not fatal: a red pulse at the edges
		if not warnActive then
			warnActive = true
			task.spawn(function()
				for _ = 1, 3 do
					flash.BackgroundColor3 = Color3.fromRGB(150, 30, 26)
					flash.BackgroundTransparency = 0.8
					TweenService:Create(flash, TweenInfo.new(0.42), {
						BackgroundTransparency = 1,
					}):Play()
					task.wait(0.52)
				end
				flash.BackgroundColor3 = Color3.new(0, 0, 0)
				warnActive = false
			end)
		end
		return
	end

	if cue.kind == "breach" then
		fovKick(22, 0.08, 0.6)
		flash.BackgroundColor3 = Color3.fromRGB(120, 12, 10)
		flash.BackgroundTransparency = 0.12
		TweenService:Create(flash, TweenInfo.new(0.9), {
			BackgroundTransparency = 1,
		}):Play()
		task.delay(1, function()
			flash.BackgroundColor3 = Color3.new(0, 0, 0)
		end)
		return
	end

	if cue.kind == "strike" then
		setStrikes(cue.strikes, cue.maxStrikes)
		if cue.remaining == 1 then
			showCard("", "one more and the house has you", Color3.fromRGB(200, 90, 80), 3)
		end
		return
	end

	if cue.kind == "nightEnded" then
		setStrikes(cue.strikes or 0, cue.maxStrikes)
		if cue.result == "survived" then
			showCard("6:00 AM", "dawn through the windows — you made it",
				Color3.fromRGB(246, 226, 180), 9)
		elseif cue.result == "failed" then
			flash.BackgroundColor3 = Color3.fromRGB(10, 0, 0)
			flash.BackgroundTransparency = 0
			TweenService:Create(flash, TweenInfo.new(2.4), {
				BackgroundTransparency = 0.25,
			}):Play()
			showCard("THE HOUSE TOOK YOU",
				cue.reason and ("lost to: " .. tostring(cue.reason)) or "",
				Color3.fromRGB(190, 60, 52), 8)
		end
		return
	end

	if cue.kind == "crawlDrop" then
		-- something just landed in the room with you
		fovKick(11, 0.07, 0.5)
		flash.BackgroundColor3 = Color3.fromRGB(8, 6, 10)
		flash.BackgroundTransparency = 0.55
		TweenService:Create(flash, TweenInfo.new(0.7), {
			BackgroundTransparency = 1,
		}):Play()
		task.delay(0.8, function()
			flash.BackgroundColor3 = Color3.new(0, 0, 0)
		end)
		return
	end

	if cue.kind == "crawlHit" then
		fovKick(26, 0.05, 0.75)
		flash.BackgroundColor3 = Color3.fromRGB(96, 10, 10)
		flash.BackgroundTransparency = 0.1
		TweenService:Create(flash, TweenInfo.new(1.1), {
			BackgroundTransparency = 1,
		}):Play()
		task.delay(1.2, function()
			flash.BackgroundColor3 = Color3.new(0, 0, 0)
		end)
		return
	end

	if cue.kind == "knock" then
		fovKick(cue.slam and 9 or 5, 0.06, 0.35)
		return
	end

	if cue.kind == "monsterRepelled" then
		flash.BackgroundColor3 = Color3.fromRGB(255, 255, 255)
		flash.BackgroundTransparency = 0.82
		TweenService:Create(flash, TweenInfo.new(0.45), {
			BackgroundTransparency = 1,
		}):Play()
		task.delay(0.5, function()
			flash.BackgroundColor3 = Color3.new(0, 0, 0)
		end)
	end
end)
