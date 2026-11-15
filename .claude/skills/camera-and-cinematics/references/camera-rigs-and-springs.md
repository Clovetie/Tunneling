# Spring-Damped Camera Rigs & Trauma Shake Reference

## 1. Frame-Rate Independent Spring Follow + Raycast Wall Avoidance
```lua
local RunService = game:GetService("RunService")
local Workspace = game:GetService("Workspace")

local function bindActionCamera(getSubjectPosition: () -> Vector3?, offset: Vector3, exclusionList: { Instance })
	local camera = Workspace.CurrentCamera
	local smoothedFocus: Vector3? = nil
	local rayParams = RaycastParams.new()
	rayParams.FilterType = Enum.RaycastFilterType.Exclude
	rayParams.FilterDescendantsInstances = exclusionList

	RunService:BindToRenderStep("ArenaActionCam", Enum.RenderPriority.Camera.Value + 1, function(dt: number)
		local targetPos = getSubjectPosition()
		if not targetPos then
			return
		end
		local alpha = 1.0 - math.exp(-14.0 * dt)
		smoothedFocus = if smoothedFocus then smoothedFocus:Lerp(targetPos, alpha) else targetPos

		local desiredPos = smoothedFocus + offset
		local dir = desiredPos - smoothedFocus
		local hit = Workspace:Raycast(smoothedFocus, dir, rayParams)
		local finalPos = if hit then (hit.Position - dir.Unit * 0.5) else desiredPos

		camera.CameraType = Enum.CameraType.Scriptable
		camera.CFrame = CFrame.lookAt(finalPos, smoothedFocus)
	end)
end
```
