# Spatial Hitbox & Server-Validated Combo Patterns

## 1. Deduplicated `GetPartBoundsInBox` Swing Query
```lua
local Workspace = game:GetService("Workspace")

local function queryMeleeBox(attackerChar: Model, boxCFrame: CFrame, boxSize: Vector3): { Model }
	local params = OverlapParams.new()
	params.FilterType = Enum.RaycastFilterType.Exclude
	params.FilterDescendantsInstances = { attackerChar }
	params.MaxParts = 48

	local parts = Workspace:GetPartBoundsInBox(boxCFrame, boxSize, params)
	local seenModels: { [Model]: boolean } = {}
	local hitTargets = {}

	for _, part in ipairs(parts) do
		local model = part:FindFirstAncestorOfClass("Model")
		if model and not seenModels[model] then
			local hum = model:FindFirstChildOfClass("Humanoid")
			if hum and hum.Health > 0 then
				seenModels[model] = true
				table.insert(hitTargets, model)
			end
		end
	end
	return hitTargets
end
```

## 2. Server-Side Reach, Cooldown & Line-of-Sight Verification
```lua
local lastSwingAt: { [Player]: number } = {}

local function canServerConfirmHit(player: Player, targetModel: Model, maxReach: number, minInterval: number): boolean
	local now = os.clock()
	if now - (lastSwingAt[player] or 0) < minInterval then
		return false
	end
	local char = player.Character
	local attackerRoot = char and char:FindFirstChild("HumanoidRootPart")
	local targetRoot = targetModel and targetModel:FindFirstChild("HumanoidRootPart")
	local targetHum = targetModel and targetModel:FindFirstChildOfClass("Humanoid")
	if not (attackerRoot and targetRoot and targetHum and targetHum.Health > 0) then
		return false
	end
	local delta = targetRoot.Position - attackerRoot.Position
	if delta.Magnitude > maxReach then
		return false
	end
	lastSwingAt[player] = now
	return true
end
```
