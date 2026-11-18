--!nonstrict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local player = Players.LocalPlayer
local syncRemote = ReplicatedStorage:WaitForChild("InventorySync", 15)
if not syncRemote then
	return
end

local gui = Instance.new("ScreenGui")
gui.Name = "HotbarInventoryHUD"
gui.ResetOnSpawn = false
gui.Parent = player:WaitForChild("PlayerGui")

local label = Instance.new("TextLabel")
label.AnchorPoint = Vector2.new(0.5, 1)
label.Position = UDim2.new(0.5, 0, 1, -16)
label.Size = UDim2.new(0, 360, 0, 34)
label.BackgroundColor3 = Color3.fromRGB(14, 20, 28)
label.BackgroundTransparency = 0.2
label.TextColor3 = Color3.fromRGB(232, 239, 247)
label.Font = Enum.Font.Code
label.TextSize = 13
label.Text = "Inventory: [Empty]"
label.Parent = gui

syncRemote.OnClientEvent:Connect(function(bag)
	if type(bag) ~= "table" or #bag == 0 then
		label.Text = "Inventory: [Empty]"
		return
	end
	local parts = {}
	for i, slot in ipairs(bag) do
		table.insert(parts, string.format("[%d] %s x%d", i, tostring(slot.name), tonumber(slot.qty) or 1))
	end
	label.Text = table.concat(parts, "  ")
end)
