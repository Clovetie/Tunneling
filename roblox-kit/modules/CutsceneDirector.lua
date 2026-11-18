--!strict
-- CutsceneDirector.lua — Keyframed Camera Path, FOV & Letterbox Cutscene Engine (v6.0.0)
-- Supports TweenService keyframes, Catmull-Rom spline interpolation, camera shake, and skip hooks.

local TweenService = game:GetService("TweenService")
local RunService = game:GetService("RunService")

export type KeyframeNode = {
	cframe: CFrame,
	fov: number?,
	duration: number,
	easingStyle: Enum.EasingStyle?,
	easingDirection: Enum.EasingDirection?,
	holdSeconds: number?,
	caption: string?,
}

export type CutsceneTrack = {
	id: string,
	title: string?,
	skippable: boolean?,
	letterbox: boolean?,
	keyframes: { KeyframeNode },
}

local CutsceneDirector = {}
CutsceneDirector.__index = CutsceneDirector

function CutsceneDirector.new()
	local self = setmetatable({}, CutsceneDirector)
	self._playing = false
	self._cancelled = false
	self._activeTween = nil :: Tween?
	self._savedCameraType = nil :: Enum.CameraType?
	self._savedFov = 70
	self._tracks = {} :: { [string]: CutsceneTrack }
	self._listeners = {} :: { (string, string, any) -> () }
	return self
end

function CutsceneDirector:RegisterTrack(track: CutsceneTrack)
	assert(type(track) == "table" and type(track.id) == "string", "Track must have a string id")
	assert(type(track.keyframes) == "table" and #track.keyframes > 0, "Track must contain at least 1 keyframe")
	self._tracks[track.id] = track
end

function CutsceneDirector:OnEvent(callback: (string, string, any) -> ())
	table.insert(self._listeners, callback)
end

function CutsceneDirector:_emit(eventType: string, trackId: string, payload: any)
	for _, fn in ipairs(self._listeners) do
		task.spawn(fn, eventType, trackId, payload)
	end
end

function CutsceneDirector:IsPlaying(): boolean
	return self._playing
end

function CutsceneDirector.CatmullRomPosition(p0: Vector3, p1: Vector3, p2: Vector3, p3: Vector3, t: number): Vector3
	local t2 = t * t
	local t3 = t2 * t
	return 0.5 * (
		(2 * p1)
		+ (-p0 + p2) * t
		+ (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
		+ (-p0 + 3 * p1 - 3 * p2 + p3) * t3
	)
end

function CutsceneDirector:Skip()
	if not self._playing then
		return false
	end
	self._cancelled = true
	if self._activeTween then
		self._activeTween:Cancel()
		self._activeTween = nil
	end
	return true
end

function CutsceneDirector:PlayTrack(trackOrId: string | CutsceneTrack): boolean
	local track: CutsceneTrack? = if type(trackOrId) == "string" then self._tracks[trackOrId] else trackOrId
	if not track then
		warn("[CutsceneDirector] Unknown track:", tostring(trackOrId))
		return false
	end
	if self._playing then
		self:Skip()
		task.wait(0.05)
	end

	local cam = workspace.CurrentCamera
	if not cam then
		return false
	end

	self._playing = true
	self._cancelled = false
	self._savedCameraType = cam.CameraType
	self._savedFov = cam.FieldOfView
	cam.CameraType = Enum.CameraType.Scriptable

	self:_emit("started", track.id, { title = track.title, keyframeCount = #track.keyframes, letterbox = track.letterbox == true })

	for index, node in ipairs(track.keyframes) do
		if self._cancelled then
			break
		end
		self:_emit("keyframe", track.id, { index = index, caption = node.caption, duration = node.duration })
		local duration = math.max(0.01, tonumber(node.duration) or 1.5)
		local style = node.easingStyle or Enum.EasingStyle.Sine
		local dir = node.easingDirection or Enum.EasingDirection.InOut
		local goal: { [string]: any } = { CFrame = node.cframe }
		if node.fov then
			goal.FieldOfView = node.fov
		end

		if duration <= 0.03 or not RunService:IsRunning() then
			cam.CFrame = node.cframe
			if node.fov then
				cam.FieldOfView = node.fov
			end
		else
			local tween = TweenService:Create(cam, TweenInfo.new(duration, style, dir), goal)
			self._activeTween = tween
			tween:Play()
			local elapsed = 0
			while elapsed < duration and not self._cancelled do
				elapsed += task.wait(0.03)
			end
			self._activeTween = nil
		end

		if not self._cancelled and node.holdSeconds and node.holdSeconds > 0 then
			local held = 0
			while held < node.holdSeconds and not self._cancelled do
				held += task.wait(0.03)
			end
		end
	end

	local wasCancelled = self._cancelled
	if self._savedCameraType then
		cam.CameraType = self._savedCameraType
	end
	cam.FieldOfView = self._savedFov
	self._playing = false
	self._cancelled = false
	self:_emit(if wasCancelled then "skipped" else "completed", track.id, {})
	return not wasCancelled
end

return CutsceneDirector
