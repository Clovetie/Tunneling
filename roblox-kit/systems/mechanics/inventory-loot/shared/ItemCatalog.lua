--!nonstrict
local ItemCatalog = {
	MaxSlots = 6,
	Items = {
		medkit = { id = "medkit", name = "Field Medkit", rarity = "Uncommon", maxStack = 3 },
		battery = { id = "battery", name = "High-Volt Battery", rarity = "Common", maxStack = 5 },
		skeleton_key = { id = "skeleton_key", name = "Skeleton Key", rarity = "Rare", maxStack = 1 },
		aegis_charm = { id = "aegis_charm", name = "Aegis Ward Charm", rarity = "Epic", maxStack = 1 },
		chrono_relic = { id = "chrono_relic", name = "Chrono Pocketwatch", rarity = "Legendary", maxStack = 1 },
	},
}

return ItemCatalog
