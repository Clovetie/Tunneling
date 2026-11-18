--!nonstrict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local player = Players.LocalPlayer
local remote = ReplicatedStorage:WaitForChild("RoundSurvivalState", 15)
if not remote then
	return
end

local gui = Instance.new("ScreenGui")
gui.Name = "RoundSurvivalBanner"
gui.ResetOnSpawn = false
gui.Parent = player:WaitForChild("PlayerGui")

local label = Instance.new("TextLabel")
label.AnchorPoint = Vector2.new(0.5, 0)
label.Position = UDim2.new(0.5, 0, 0, 50)
label.Size = UDim2.new(0, 320, 0, 30)
label.BackgroundColor3 = Color3.fromRGB(18, 24, 34)
label.BackgroundTransparency = 0.2
label.TextColor3 = Color3.fromRGB(92, 200, 255)
label.Font = Enum.Font.GothamBold
label.TextSize = 13
label.Text = "Generators: 0 / 3"
label.Parent = gui

remote.OnClientEvent:Connect(function(payload)
	if type(payload) == "table" then
		label.Text = string.format("%s · Generators %d/%d",
			tostring(payload.phase or "Round"),
			tonumber(payload.completedGenerators) or 0,
			tonumber(payload.requiredGenerators) or 3
		)
	end
end)
