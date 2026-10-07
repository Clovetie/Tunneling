local on = false
local lightorig = script.Parent.Light.Attachment
script.Parent.Activated:Connect(function()
	if on == false then
		on = true
	lightorig.Light.Enabled = true
		lightorig.Sound:Play()
		lightorig.SurfaceLight.Enabled = true
	elseif on == true then
		on = false
		lightorig.Shadow.Enabled = false
		lightorig.Light.Enabled = false
		lightorig.Sound2:Play()
		lightorig.SurfaceLight.Enabled = false
	end
end)
