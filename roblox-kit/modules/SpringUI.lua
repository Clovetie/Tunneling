-- SpringUI.lua — Crisp TweenService UI micro-interactions, button scale
-- springs, pop-in reveals, and non-blocking HUD toast notifications.
local TweenService = game:GetService("TweenService")

local SpringUI = {}

local FAST_OUT = TweenInfo.new(0.14, Enum.EasingStyle.Quad, Enum.EasingDirection.Out)
local POP_IN = TweenInfo.new(0.24, Enum.EasingStyle.Back, Enum.EasingDirection.Out)

-- Accessibility, v6.1.0: `ui/settings-menu` calls SetReduceMotion when a player asks for
-- it. Off by default, so nothing above changes for a game that never installs that system.
SpringUI.ReduceMotion = false

function SpringUI.SetReduceMotion(on)
	SpringUI.ReduceMotion = on and true or false
end

-- One place decides whether a scale change animates or lands immediately.
local function scaleTo(uiScale, info, target)
	if SpringUI.ReduceMotion then
		uiScale.Scale = target
		return
	end
	TweenService:Create(uiScale, info, { Scale = target }):Play()
end

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
		scaleTo(uiScale, FAST_OUT, hoverScale)
	end))
	table.insert(conns, button.MouseLeave:Connect(function()
		scaleTo(uiScale, FAST_OUT, 1.0)
	end))
	table.insert(conns, button.MouseButton1Down:Connect(function()
		scaleTo(uiScale, FAST_OUT, pressScale)
	end))
	table.insert(conns, button.MouseButton1Up:Connect(function()
		scaleTo(uiScale, POP_IN, hoverScale)
	end))

	return function()
		for _, c in ipairs(conns) do
			c:Disconnect()
		end
	end
end

function SpringUI.popReveal(guiObj)
	local uiScale = ensureScale(guiObj)
	guiObj.Visible = true
	if SpringUI.ReduceMotion then
		-- Reveal without the pop: starting at 0.82 and jumping would still be a jump.
		uiScale.Scale = 1.0
		return
	end
	uiScale.Scale = 0.82
	TweenService:Create(uiScale, POP_IN, { Scale = 1.0 }):Play()
end

return SpringUI
