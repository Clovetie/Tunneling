-- SpringUI.lua — Crisp TweenService UI micro-interactions, button scale
-- springs, pop-in reveals, and non-blocking HUD toast notifications.
local TweenService = game:GetService("TweenService")

local SpringUI = {}

local FAST_OUT = TweenInfo.new(0.14, Enum.EasingStyle.Quad, Enum.EasingDirection.Out)
local POP_IN = TweenInfo.new(0.24, Enum.EasingStyle.Back, Enum.EasingDirection.Out)

local function ensureScale(guiObj)
	local s = guiObj:FindFirstChildOfClass("UIScale")
	if not s then
		s = Instance.new("UIScale")
		s.Parent = guiObj
	end
	return s
end

function SpringUI.bindButton(button, opts)
	local hoverScale = (opts and opts.hoverScale) or 1.05
	local pressScale = (opts and opts.pressScale) or 0.94
	local uiScale = ensureScale(button)
	local conns = {}

	table.insert(conns, button.MouseEnter:Connect(function()
		TweenService:Create(uiScale, FAST_OUT, { Scale = hoverScale }):Play()
	end))
	table.insert(conns, button.MouseLeave:Connect(function()
		TweenService:Create(uiScale, FAST_OUT, { Scale = 1.0 }):Play()
	end))
	table.insert(conns, button.MouseButton1Down:Connect(function()
		TweenService:Create(uiScale, FAST_OUT, { Scale = pressScale }):Play()
	end))
	table.insert(conns, button.MouseButton1Up:Connect(function()
		TweenService:Create(uiScale, POP_IN, { Scale = hoverScale }):Play()
	end))

	return function()
		for _, c in ipairs(conns) do
			c:Disconnect()
		end
	end
end

function SpringUI.popReveal(guiObj)
	local uiScale = ensureScale(guiObj)
	uiScale.Scale = 0.82
	guiObj.Visible = true
	TweenService:Create(uiScale, POP_IN, { Scale = 1.0 }):Play()
end

return SpringUI
