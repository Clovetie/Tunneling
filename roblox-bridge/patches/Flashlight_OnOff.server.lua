-- Flashlight toggle  (Script, server)  StarterPack.Flashlight."On/Off"
--
-- Fixes: the attachment is named "LightOrigin", not "Attachment". The old line
--   local lightorig = script.Parent.Light.Attachment
-- threw immediately, so Activated was never connected and the light never worked.
--
-- Runs on the server, so the SpotLight's Enabled state is authoritative and the
-- monster logic can read it directly — nothing for a client to spoof.

local tool = script.Parent

local lightPart = tool:WaitForChild("Light")
local origin = lightPart:WaitForChild("LightOrigin")

local beam = origin:WaitForChild("Light")          -- SpotLight, the visible cone
local shadow = origin:WaitForChild("Shadow")       -- SpotLight, shadow caster
local surface = origin:WaitForChild("SurfaceLight")

local clickOn = lightPart:FindFirstChild("Sound")
local clickOff = lightPart:FindFirstChild("Sound2")

local on = false

local function setState(state)
	on = state

	-- all three were toggled asymmetrically before: Shadow was only ever
	-- switched off, never back on
	beam.Enabled = state
	shadow.Enabled = state
	surface.Enabled = state

	-- single source of truth other scripts can read without touching internals
	tool:SetAttribute("LightOn", state)

	local sfx = state and clickOn or clickOff
	if sfx then
		sfx:Play()
	end
end

setState(false)

tool.Activated:Connect(function()
	setState(not on)
end)

-- putting it away kills the beam, so you cannot stash a lit torch in your bag
tool.Unequipped:Connect(function()
	if on then
		setState(false)
	end
end)
