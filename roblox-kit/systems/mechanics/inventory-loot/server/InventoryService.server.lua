--!nonstrict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local ItemCatalog = require(Shared:WaitForChild("ItemCatalog"))

local syncRemote = ReplicatedStorage:FindFirstChild("InventorySync")
if not syncRemote then
	syncRemote = Instance.new("RemoteEvent")
	syncRemote.Name = "InventorySync"
	syncRemote.Parent = ReplicatedStorage
end

local inventories = {}

local function getBag(player)
	local bag = inventories[player]
	if not bag then
		bag = {}
		inventories[player] = bag
	end
	return bag
end

local function grantItem(player, itemId, qty)
	local spec = ItemCatalog.Items[itemId]
	if not spec then
		return false
	end
	local bag = getBag(player)
	for _, slot in ipairs(bag) do
		if slot.id == itemId and slot.qty < spec.maxStack then
			slot.qty = math.min(spec.maxStack, slot.qty + (qty or 1))
			syncRemote:FireClient(player, bag)
			return true
		end
	end
	if #bag >= ItemCatalog.MaxSlots then
		return false
	end
	table.insert(bag, { id = itemId, name = spec.name, rarity = spec.rarity, qty = qty or 1 })
	syncRemote:FireClient(player, bag)
	return true
end

Players.PlayerAdded:Connect(function(player)
	grantItem(player, "battery", 2)
end)

Players.PlayerRemoving:Connect(function(player)
	inventories[player] = nil
end)
