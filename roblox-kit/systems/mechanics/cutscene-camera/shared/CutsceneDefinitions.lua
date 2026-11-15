--!strict
-- CutsceneDefinitions.lua — Shared registry of keyframed camera cutscenes (v6.0.0)

local CutsceneDefinitions = {
	BossAwakening = {
		id = "BossAwakening",
		title = "THE ENTITY AWAKENS",
		skippable = true,
		letterbox = true,
		keyframes = {
			{
				cframe = CFrame.lookAt(Vector3.new(0, 14, 34), Vector3.new(0, 6, 0)),
				fov = 68,
				duration = 1.6,
				caption = "Something stirs beneath the sanctuary...",
			},
			{
				cframe = CFrame.lookAt(Vector3.new(-10, 8, 16), Vector3.new(0, 8, 0)),
				fov = 52,
				duration = 1.4,
				holdSeconds = 0.6,
				caption = "Hold your flashlight steady.",
			},
			{
				cframe = CFrame.lookAt(Vector3.new(0, 5, 10), Vector3.new(0, 9, 0)),
				fov = 42,
				duration = 0.9,
				holdSeconds = 0.8,
				caption = "SURVIVE UNTIL 6 AM",
			},
		},
	},
	SafeRoomUnlock = {
		id = "SafeRoomUnlock",
		title = "SAFE ROOM POWER RESTORED",
		skippable = true,
		letterbox = true,
		keyframes = {
			{
				cframe = CFrame.lookAt(Vector3.new(18, 9, 22), Vector3.new(0, 4, 0)),
				fov = 62,
				duration = 1.2,
				caption = "Emergency generators online.",
			},
			{
				cframe = CFrame.lookAt(Vector3.new(6, 5, 12), Vector3.new(0, 4, 0)),
				fov = 54,
				duration = 1.1,
				holdSeconds = 0.5,
				caption = "Shelter doors unlocked.",
			},
		},
	},
}

return CutsceneDefinitions
