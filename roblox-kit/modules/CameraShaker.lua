--!strict
--[[
	CameraShaker.lua
	Trauma-squared 3D camera shake and FOV impulse controller.
	Provides smooth rotational + positional offsets for combat hits, explosions,
	and boss footsteps without jarring linear snaps.
]]

local RunService = game:GetService("RunService")
local Workspace = game:GetService("Workspace")

local CameraShaker = {}
CameraShaker.__index = CameraShaker

export type ShakerConfig = {
	DecayRate: number?,
	MaxAngleDeg: number?,
	MaxOffsetStuds: number?,
	Frequency: number?,
}

function CameraShaker.new(config: ShakerConfig?)
	local cfg: ShakerConfig = config or {}
	local self = setmetatable({
		_trauma = 0.0,
		_decay = cfg.DecayRate or 1.65,
		_maxAngleRad = math.rad(cfg.MaxAngleDeg or 4.5),
		_maxOffset = cfg.MaxOffsetStuds or 0.85,
		_freq = cfg.Frequency or 18.0,
		_time = 0.0,
		_fovImpulse = 0.0,
		_boundName = nil :: string?,
	}, CameraShaker)
	return self
end

function CameraShaker:AddTrauma(amount: number)
	local clamped = math.clamp(amount, 0.0, 1.0)
	self._trauma = math.clamp(self._trauma + clamped, 0.0, 1.0)
end

function CameraShaker:PulseFOV(deltaFov: number)
	self._fovImpulse = math.clamp(self._fovImpulse + deltaFov, -18.0, 18.0)
end

function CameraShaker:SampleOffset(dt: number): (CFrame, number)
	self._time += dt * self._freq
	if self._trauma > 0 then
		self._trauma = math.max(0.0, self._trauma - self._decay * dt)
	end
	if math.abs(self._fovImpulse) > 0.01 then
		self._fovImpulse *= math.exp(-10.0 * dt)
	else
		self._fovImpulse = 0.0
	end

	local shake = self._trauma * self._trauma
	if shake <= 0.0001 then
		return CFrame.identity, self._fovImpulse
	end

	local t = self._time
	local nx = math.sin(t * 1.13) * math.cos(t * 0.71)
	local ny = math.sin(t * 1.37 + 1.2) * math.cos(t * 0.89)
	local nz = math.cos(t * 1.53 + 2.4) * math.sin(t * 0.63)

	local ox = nx * self._maxOffset * shake
	local oy = ny * self._maxOffset * shake
	local pitch = nx * self._maxAngleRad * shake
	local yaw = ny * self._maxAngleRad * shake * 0.7
	local roll = nz * self._maxAngleRad * shake * 0.85

	return CFrame.new(ox, oy, 0) * CFrame.Angles(pitch, yaw, roll), self._fovImpulse
end

function CameraShaker:BindToRenderStep(bindName: string?)
	local name = bindName or "ArenaCameraShaker"
	self._boundName = name
	RunService:BindToRenderStep(name, Enum.RenderPriority.Camera.Value + 2, function(dt: number)
		local cam = Workspace.CurrentCamera
		if not cam then
			return
		end
		local offsetCf, _ = self:SampleOffset(dt)
		if offsetCf ~= CFrame.identity then
			cam.CFrame = cam.CFrame * offsetCf
		end
	end)
end

function CameraShaker:Unbind()
	if self._boundName then
		pcall(function()
			RunService:UnbindFromRenderStep(self._boundName :: string)
		end)
		self._boundName = nil
	end
end

return CameraShaker
