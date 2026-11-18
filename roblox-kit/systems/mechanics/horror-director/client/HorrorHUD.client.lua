--!nonstrict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local player = Players.LocalPlayer
local remotes = ReplicatedStorage:WaitForChild("HorrorRemotes", 15)
if not remotes then
	return
end

local stateEvent = remotes:WaitForChild("HorrorState")
local gui = Instance.new("ScreenGui")
gui.Name = "HorrorDirectorHUD"
gui.ResetOnSpawn = false
gui.Parent = player:WaitForChild("PlayerGui")

local badge = Instance.new("TextLabel")
badge.Name = "PhaseBadge"
badge.AnchorPoint = Vector2.new(0.5, 0)
badge.Position = UDim2.new(0.5, 0, 0, 14)
badge.Size = UDim2.new(0, 260, 0, 32)
badge.BackgroundColor3 = Color3.fromRGB(14, 18, 26)
badge.BackgroundTransparency = 0.25
badge.TextColor3 = Color3.fromRGB(238, 232, 224)
badge.Font = Enum.Font.GothamBold
badge.TextSize = 14
badge.Text = "Phase 1 · Unease"
badge.Parent = gui

stateEvent.OnClientEvent:Connect(function(st)
	if type(st) == "table" then
		badge.Text = string.format("Phase %d · %s (%.0f%%)",
			st.phaseIndex or 1,
			tostring(st.phaseName or "Unease"),
			(tonumber(st.intensity) or 0) * 100
		)
	end
end)
