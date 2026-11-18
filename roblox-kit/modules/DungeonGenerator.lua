--!nonstrict
--[[
	DungeonGenerator.lua — Seeded procedural 3D room & corridor layout generator
	with non-overlapping room placement, Prim's Minimum Spanning Tree (MST) +
	loop corridors, room role assignment (`spawn`, `normal`, `treasure`, `puzzle`,
	`saferoom`, `boss`), and optional Workspace geometry builder.
]]

local DungeonGenerator = {}

local ROOM_PALETTE = {
	spawn = Color3.fromRGB(69, 224, 160),
	normal = Color3.fromRGB(95, 110, 130),
	treasure = Color3.fromRGB(255, 200, 87),
	puzzle = Color3.fromRGB(92, 200, 255),
	saferoom = Color3.fromRGB(140, 215, 175),
	boss = Color3.fromRGB(235, 75, 75),
}

function DungeonGenerator.GenerateLayout(opts)
	opts = opts or {}
	local seed = tonumber(opts.seed) or 1337
	local rng = Random.new(seed)
	local roomCount = math.clamp(tonumber(opts.roomCount) or 8, 3, 28)
	local gridStep = tonumber(opts.gridStep) or 36
	local rooms = {}
	local occupied = {}

	local function cellKey(gx, gz)
		return tostring(gx) .. ":" .. tostring(gz)
	end

	-- Place rooms on a discrete grid via seeded random walk + branching
	local frontier = { { gx = 0, gz = 0 } }
	occupied[cellKey(0, 0)] = true

	while #rooms < roomCount and #frontier > 0 do
		local pickIdx = rng:NextInteger(1, #frontier)
		local cell = table.remove(frontier, pickIdx)
		local index = #rooms + 1
		local role = "normal"
		if index == 1 then
			role = "spawn"
		elseif index == roomCount then
			role = "boss"
		elseif index == math.floor(roomCount * 0.5) then
			role = "saferoom"
		elseif index % 3 == 0 then
			role = "treasure"
		elseif index % 4 == 0 then
			role = "puzzle"
		end

		local w = rng:NextInteger(22, 30)
		local d = rng:NextInteger(22, 30)
		local h = tonumber(opts.roomHeight) or 14
		table.insert(rooms, {
			id = index,
			role = role,
			gx = cell.gx,
			gz = cell.gz,
			center = Vector3.new(cell.gx * gridStep, 0, cell.gz * gridStep),
			size = Vector3.new(w, h, d),
		})

		local dirs = {
			{ 1, 0 },
			{ -1, 0 },
			{ 0, 1 },
			{ 0, -1 },
		}
		for _, offset in ipairs(dirs) do
			local nx = cell.gx + offset[1]
			local nz = cell.gz + offset[2]
			local key = cellKey(nx, nz)
			if not occupied[key] then
				occupied[key] = true
				table.insert(frontier, { gx = nx, gz = nz })
			end
		end
	end

	-- Build Prim's Minimum Spanning Tree across rooms
	local connected = { [1] = true }
	local corridors = {}
	local edgeSet = {}

	local function edgeKey(a, b)
		if a > b then
			a, b = b, a
		end
		return tostring(a) .. "-" .. tostring(b)
	end

	for _ = 1, #rooms - 1 do
		local bestA, bestB, bestDist = nil, nil, math.huge
		for i = 1, #rooms do
			if connected[i] then
				for j = 1, #rooms do
					if not connected[j] then
						local dist = (rooms[i].center - rooms[j].center).Magnitude
						if dist < bestDist then
							bestA, bestB, bestDist = i, j, dist
						end
					end
				end
			end
		end
		if bestA and bestB then
			connected[bestB] = true
			edgeSet[edgeKey(bestA, bestB)] = true
			table.insert(corridors, {
				from = bestA,
				to = bestB,
				length = bestDist,
				isLoop = false,
			})
		end
	end

	-- Add optional extra loop edges so players have multiple escape routes
	for i = 1, #rooms do
		for j = i + 1, #rooms do
			local k = edgeKey(i, j)
			if not edgeSet[k] then
				local dist = (rooms[i].center - rooms[j].center).Magnitude
				if dist <= gridStep * 1.45 and rng:NextNumber() < 0.28 then
					edgeSet[k] = true
					table.insert(corridors, {
						from = i,
						to = j,
						length = dist,
						isLoop = true,
					})
				end
			end
		end
	end

	return {
		seed = seed,
		roomCount = #rooms,
		corridorCount = #corridors,
		rooms = rooms,
		corridors = corridors,
	}
end

function DungeonGenerator.BuildInWorkspace(layout, parentInstance)
	local parent = parentInstance or game:GetService("Workspace")
	local rootModel = Instance.new("Model")
	rootModel.Name = "GeneratedDungeon_" .. tostring(layout.seed or 1)

	for _, room in ipairs(layout.rooms or {}) do
		local folder = Instance.new("Model")
		folder.Name = string.format("Room_%02d_%s", room.id, room.role)
		folder:SetAttribute("RoomRole", room.role)

		local floor = Instance.new("Part")
		floor.Name = "Floor"
		floor.Anchored = true
		floor.Material = Enum.Material.Slate
		floor.Color = Color3.fromRGB(38, 42, 48)
		floor.Size = Vector3.new(room.size.X, 1, room.size.Z)
		floor.Position = room.center + Vector3.new(0, 0.5, 0)
		floor.Parent = folder

		local marker = Instance.new("Part")
		marker.Name = "RoleBeacon"
		marker.Anchored = true
		marker.CanCollide = false
		marker.Material = Enum.Material.Neon
		marker.Color = ROOM_PALETTE[room.role] or ROOM_PALETTE.normal
		marker.Size = Vector3.new(2, 0.4, 2)
		marker.Position = room.center + Vector3.new(0, 1.2, 0)
		marker.Parent = folder

		folder.PrimaryPart = floor
		folder.Parent = rootModel
	end

	for idx, corr in ipairs(layout.corridors or {}) do
		local roomA = layout.rooms[corr.from]
		local roomB = layout.rooms[corr.to]
		if roomA and roomB then
			local mid = (roomA.center + roomB.center) * 0.5
			local bridge = Instance.new("Part")
			bridge.Name = string.format("Corridor_%02d", idx)
			bridge.Anchored = true
			bridge.Material = Enum.Material.Cobblestone
			bridge.Color = Color3.fromRGB(52, 58, 66)
			bridge.Size = Vector3.new(6, 0.8, math.max(4, corr.length))
			bridge.CFrame = CFrame.lookAt(mid + Vector3.new(0, 0.4, 0), roomB.center + Vector3.new(0, 0.4, 0))
			bridge.Parent = rootModel
		end
	end

	rootModel.Parent = parent
	return rootModel
end

return DungeonGenerator
