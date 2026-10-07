--!nonstrict
--[[
	NightLoop · Flash
	Camera-style flash charges, server authoritative.

	The tool no longer toggles a held beam. Activating it spends one charge and
	fires a single bright burst; charges refill over time. Entities subscribe to
	Flash.Fired and decide what a flash means to them.

		Flash.Fired:Connect(function(player, originCFrame, originPos, spot) end)

	The client only ever *requests* a flash. Charges, cooldown and the burst
	itself are decided here, so there is nothing worth exploiting.
]]

local Players = game:GetService("Players")
local RunService = game:GetService("RunService")

local package = script.Parent
local Config = require(package.Config)
local Signal = require(package.Signal)
local Net = require(package.Net)

local Flash = {}
Flash.Fired = Signal.new()

local stateByPlayer = {}
local started = false

local function settings()
	return Config.Flash
end

local function stateFor(player)
	local s = stateByPlayer[player]
	if not s then
		s = {
			charges = settings().Charges,
			lastFlash = 0,
			recharge = 0,
		}
		stateByPlayer[player] = s
	end
	return s
end

local function pushState(player)
	local s = stateFor(player)
	Net.SendCue(player, {
		kind = "flashState",
		charges = s.charges,
		max = settings().Charges,
		recharge = settings().RechargeTime > 0
			and math.clamp(s.recharge / settings().RechargeTime, 0, 1) or 0,
	})
end

function Flash:GetCharges(player)
	return stateFor(player).charges
end

-- Locate the equipped tool's light. Returns origin CFrame, position, SpotLight.
function Flash:GetOrigin(player)
	local world = Config.World
	local character = player.Character
	if not character then
		return nil
	end

	local tool = character:FindFirstChild(world.FlashlightToolName)
	if not tool or not tool:IsA("Tool") then
		return nil
	end

	local lightPart = tool:FindFirstChild(world.FlashlightLightPart)
	local origin = lightPart and lightPart:FindFirstChild(world.FlashlightAttachment)
	if not origin or not origin:IsA("Attachment") then
		return nil
	end

	local spot = origin:FindFirstChild(world.FlashlightBeamName)
	if not spot or not spot:IsA("Light") then
		return nil
	end

	return origin.WorldCFrame, origin.WorldPosition, spot, lightPart
end

function Flash:Request(player)
	local cfg = settings()
	local s = stateFor(player)
	local now = os.clock()

	if now - s.lastFlash < cfg.Cooldown then
		return false, "cooldown"
	end
	if s.charges <= 0 then
		Net.SendCue(player, { kind = "flashEmpty" })
		return false, "empty"
	end

	local originCFrame, originPos, spot, lightPart = self:GetOrigin(player)
	if not originCFrame then
		return false, "no flashlight"
	end

	s.charges -= 1
	s.lastFlash = now
	s.recharge = 0

	-- the burst itself; server-set so every client sees it.
	-- Remember the resting values so the torch goes back to normal after.
	local restRange, restBrightness, restAngle = spot.Range, spot.Brightness, spot.Angle
	if cfg.BurstRange and cfg.BurstRange > 0 then
		spot.Range = cfg.BurstRange
	end
	if cfg.BurstBrightness and cfg.BurstBrightness > 0 then
		spot.Brightness = cfg.BurstBrightness
	end
	if cfg.BurstAngle and cfg.BurstAngle > 0 then
		spot.Angle = cfg.BurstAngle
	end

	spot.Enabled = true
	local surface = spot.Parent:FindFirstChild("SurfaceLight")
	local shadow = spot.Parent:FindFirstChild("Shadow")
	if surface then surface.Enabled = true end
	if shadow then shadow.Enabled = true end

	local click = lightPart and lightPart:FindFirstChild("Sound")
	if click then
		click:Play()
	end

	task.delay(cfg.BurstDuration, function()
		if spot and spot.Parent then
			spot.Enabled = false
			spot.Range, spot.Brightness, spot.Angle = restRange, restBrightness, restAngle
		end
		if surface and surface.Parent then surface.Enabled = false end
		if shadow and shadow.Parent then shadow.Enabled = false end
	end)

	Net.SendCue(player, { kind = "flashFired", charges = s.charges, max = cfg.Charges })
	pushState(player)

	Flash.Fired:Fire(player, originCFrame, originPos, spot)
	return true
end

function Flash:Start()
	if started then
		return
	end
	started = true

	local remote = Net.FlashRequest()
	remote.OnServerEvent:Connect(function(player)
		Flash:Request(player)
	end)

	Players.PlayerRemoving:Connect(function(player)
		stateByPlayer[player] = nil
	end)

	Players.PlayerAdded:Connect(function(player)
		task.wait(1)
		pushState(player)
	end)
	for _, player in ipairs(Players:GetPlayers()) do
		pushState(player)
	end

	RunService.Heartbeat:Connect(function(dt)
		local cfg = settings()
		if cfg.RechargeTime <= 0 then
			return
		end
		for _, player in ipairs(Players:GetPlayers()) do
			local s = stateFor(player)
			if s.charges < cfg.Charges then
				s.recharge += dt
				if s.recharge >= cfg.RechargeTime then
					s.recharge = 0
					s.charges += 1
					Net.SendCue(player, {
						kind = "flashRecharged",
						charges = s.charges,
						max = cfg.Charges,
					})
					pushState(player)
				end
			end
		end
	end)
end

return Flash
