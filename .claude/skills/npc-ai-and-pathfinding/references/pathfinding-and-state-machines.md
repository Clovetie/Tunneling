# `PathfindingService` & NPC Finite State Machine Reference

## 1. Throttled Waypoint Follower with `Blocked` Recovery
```lua
local PathfindingService = game:GetService("PathfindingService")

local function createNpcNavigator(npcModel: Model, agentRadius: number?)
	local humanoid = npcModel:FindFirstChildOfClass("Humanoid")
	local root = npcModel:FindFirstChild("HumanoidRootPart")
	local path = PathfindingService:CreatePath({
		AgentRadius = agentRadius or 2.2,
		AgentHeight = 5.5,
		AgentCanJump = true,
		WaypointSpacing = 4.0,
	})

	local lastCompute = 0
	local lastGoal: Vector3? = nil

	local function stepToward(goalPos: Vector3)
		if not (humanoid and root) then
			return
		end
		local now = os.clock()
		local movedFar = (not lastGoal) or ((goalPos - lastGoal).Magnitude > 6.0)
		if now - lastCompute < 0.45 and not movedFar then
			return
		end
		lastCompute = now
		lastGoal = goalPos

		local ok = pcall(function()
			path:ComputeAsync(root.Position, goalPos)
		end)
		if ok and path.Status == Enum.PathStatus.Success then
			local waypoints = path:GetWaypoints()
			local nextWp = waypoints[2] or waypoints[1]
			if nextWp then
				if nextWp.Action == Enum.PathWaypointAction.Jump then
					humanoid.Jump = true
				end
				humanoid:MoveTo(nextWp.Position)
			end
		else
			humanoid:MoveTo(goalPos)
		end
	end

	return { StepToward = stepToward }
end
```
