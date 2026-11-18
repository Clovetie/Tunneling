--!strict
--[[
	WebUIParser.lua
	Runtime Web / Dashboard declarative UI importer and De-Slop sanitizer for Roblox Studio.
	Converts compact web-style component specs (card, row, grid, badge, button, progress)
	into responsive UDim2-scale ScreenGui / Frame hierarchies while stripping AI-slop
	artifacts (em-dashes `—`, filler connectors, and wall-of-text dumps).
]]

local WebUIParser = {}

export type WebNodeSpec = {
	tag: string?,
	name: string?,
	text: string?,
	icon: string?,
	bgColor: Color3?,
	textColor: Color3?,
	accentColor: Color3?,
	size: UDim2?,
	cornerRadius: number?,
	children: { WebNodeSpec }?,
}

local FILLER_PATTERNS = {
	"%f[%a][Ff]urthermore%f[%A]",
	"%f[%a][Mm]oreover%f[%A]",
	"%f[%a][Dd]elve%f[%A]",
	"%f[%a][Tt]apestry%f[%A]",
	"%f[%a][Ss]eainless%f[%A]",
	"%f[%a][Ss]eamless%f[%A]",
}

function WebUIParser.DeSlopText(raw: string?, maxChars: number?): string
	local text = tostring(raw or "")
	local limit = maxChars or 72
	-- Replace UTF-8 em-dash (\226\128\148) and en-dash (\226\128\147) with clean middle dot
	text = string.gsub(text, "\226\128\148", " \194\183 ")
	text = string.gsub(text, "\226\128\147", "-")
	text = string.gsub(text, " %-%- ", " \194\183 ")
	for _, pat in ipairs(FILLER_PATTERNS) do
		text = string.gsub(text, pat, "")
	end
	text = string.gsub(text, "%s%s+", " ")
	text = string.gsub(text, "^[%s,;:%-]+", "")
	if #text > limit then
		text = string.sub(text, 1, math.max(12, limit - 3)) .. "..."
	end
	return text
end

local function addCornerAndStroke(parent: GuiObject, radiusPx: number, strokeColor: Color3?)
	local corner = Instance.new("UICorner")
	corner.CornerRadius = UDim.new(0, radiusPx)
	corner.Parent = parent

	if strokeColor then
		local stroke = Instance.new("UIStroke")
		stroke.Color = strokeColor
		stroke.Thickness = 1.5
		stroke.Transparency = 0.25
		stroke.Parent = parent
	end
end

function WebUIParser.BuildNode(spec: WebNodeSpec, parent: Instance?): GuiObject
	local tag = string.lower(spec.tag or "div")
	local accent = spec.accentColor or Color3.fromRGB(56, 189, 248)
	local bg = spec.bgColor or Color3.fromRGB(15, 23, 42)
	local fg = spec.textColor or Color3.fromRGB(241, 245, 249)
	local cleanText = WebUIParser.DeSlopText(spec.text, 72)

	if tag == "button" then
		local btn = Instance.new("TextButton")
		btn.Name = spec.name or "WebActionButton"
		btn.Size = spec.size or UDim2.fromScale(0.94, 0.22)
		btn.BackgroundColor3 = accent
		btn.BackgroundTransparency = 0.1
		btn.TextColor3 = fg
		btn.TextScaled = true
		btn.Font = Enum.Font.GothamBold
		btn.Text = cleanText
		addCornerAndStroke(btn, spec.cornerRadius or 8, fg)

		local icon = Instance.new("ImageLabel")
		icon.Name = "IconBadge"
		icon.Size = UDim2.fromScale(0.12, 0.72)
		icon.Position = UDim2.fromScale(0.03, 0.14)
		icon.BackgroundTransparency = 1.0
		icon.Image = spec.icon or "rbxassetid://6031094678"
		icon.Parent = btn

		if parent then
			btn.Parent = parent
		end
		return btn
	elseif tag == "span" or tag == "h1" or tag == "h2" or tag == "h3" or tag == "p" then
		local lbl = Instance.new("TextLabel")
		lbl.Name = spec.name or "WebTextLabel"
		lbl.Size = spec.size or UDim2.fromScale(0.94, 0.18)
		lbl.BackgroundTransparency = 1.0
		lbl.TextColor3 = if (tag == "h1" or tag == "h2" or tag == "h3") then accent else fg
		lbl.TextScaled = true
		lbl.Font = if (tag == "h1" or tag == "h2" or tag == "h3") then Enum.Font.GothamBlack else Enum.Font.GothamMedium
		lbl.Text = cleanText
		if parent then
			lbl.Parent = parent
		end
		return lbl
	else
		local frame = Instance.new("Frame")
		frame.Name = spec.name or "WebCardFrame"
		frame.Size = spec.size or UDim2.fromScale(0.42, 0.34)
		frame.AnchorPoint = Vector2.new(0.5, 0.5)
		frame.Position = UDim2.fromScale(0.5, 0.5)
		frame.BackgroundColor3 = bg
		frame.BackgroundTransparency = 0.08
		addCornerAndStroke(frame, spec.cornerRadius or 12, accent)

		if tag == "grid" then
			local grid = Instance.new("UIGridLayout")
			grid.CellSize = UDim2.fromScale(0.46, 0.36)
			grid.CellPadding = UDim2.fromScale(0.04, 0.06)
			grid.Parent = frame
		else
			local list = Instance.new("UIListLayout")
			list.Padding = UDim.new(0, 8)
			list.HorizontalAlignment = Enum.HorizontalAlignment.Center
			list.Parent = frame
		end

		for _, childSpec in ipairs(spec.children or {}) do
			WebUIParser.BuildNode(childSpec, frame)
		end

		if parent then
			frame.Parent = parent
		end
		return frame
	end
end

return WebUIParser
