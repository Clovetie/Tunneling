--!nonstrict
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local AbilityDefs = require(Shared:WaitForChild("AbilityDefs"))

local castRemote = ReplicatedStorage:FindFirstChild("CastAbilityEvent")
if not castRemote then
	castRemote = Instance.new("RemoteEvent")
	castRemote.Name = "CastAbilityEvent"
	castRemote.Parent = ReplicatedStorage
end

local cooldowns = {}

castRemote.OnServerEvent:Connect(function(player, abilityId, aimDirection)
	local def = AbilityDefs[abilityId]
	if not def or typeof(aimDirection) ~= "Vector3" then
		return
	end
	local char = player.Character
	local root = char and char:FindFirstChild("HumanoidRootPart")
	if not root then
		return
	end
	local now = os.clock()
	local pcd = cooldowns[player]
	if not pcd then
		pcd = {}
		cooldowns[player] = pcd
	end
	if now < (pcd[abilityId] or 0) then
		return
	end
	pcd[abilityId] = now + def.cooldown

	local dir = aimDirection.Magnitude > 0.001 and aimDirection.Unit or root.CFrame.LookVector
	local params = RaycastParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	params.FilterDescendantsInstances = { char }
	local hit = Workspace:Raycast(root.Position, dir * def.range, params)
	if hit and hit.Instance then
		local model = hit.Instance:FindFirstAncestorOfClass("Model")
		local hum = model and model:FindFirstChildOfClass("Humanoid")
		if hum and hum.Health > 0 then
			hum:TakeDamage(def.baseDamage)
			model:SetAttribute("ActiveStatus", def.status)
		end
	end
end)
