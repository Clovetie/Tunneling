--!nonstrict
local Lighting = game:GetService("Lighting")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local TweenService = game:GetService("TweenService")

local Shared = ReplicatedStorage:WaitForChild("Shared")
local WeatherConfig = require(Shared:WaitForChild("WeatherConfig"))

local weatherRemote = ReplicatedStorage:FindFirstChild("WeatherStateEvent")
if not weatherRemote then
	weatherRemote = Instance.new("RemoteEvent")
	weatherRemote.Name = "WeatherStateEvent"
	weatherRemote.Parent = ReplicatedStorage
end

local currentIndex = 1

local function applyWeather(stateName)
	local spec = WeatherConfig.States[stateName]
	if not spec then
		return
	end
	Lighting:SetAttribute("WeatherState", stateName)
	TweenService:Create(Lighting, TweenInfo.new(4.0, Enum.EasingStyle.Sine, Enum.EasingDirection.Out), {
		Brightness = spec.Brightness,
		FogEnd = spec.FogEnd,
		ClockTime = spec.ClockTime,
		Ambient = spec.Ambient,
	}):Play()
	weatherRemote:FireAllClients({ state = stateName })
end

task.spawn(function()
	while true do
		local name = WeatherConfig.Order[currentIndex]
		applyWeather(name)
		task.wait(WeatherConfig.CycleDuration)
		currentIndex = (currentIndex % #WeatherConfig.Order) + 1
	end
end)
