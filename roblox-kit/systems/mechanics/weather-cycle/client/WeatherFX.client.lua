--!nonstrict
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local SoundService = game:GetService("SoundService")

local weatherRemote = ReplicatedStorage:WaitForChild("WeatherStateEvent", 15)
if not weatherRemote then
	return
end

weatherRemote.OnClientEvent:Connect(function(payload)
	if type(payload) == "table" and payload.state then
		SoundService:SetAttribute("ClientWeatherState", payload.state)
	end
end)
