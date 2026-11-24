--!nonstrict
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Remotes = require(ReplicatedStorage:WaitForChild("Remotes"))
local LeaderboardConfig = require(ReplicatedStorage:WaitForChild("LeaderboardConfig"))
local LeaderboardMath = require(ReplicatedStorage:WaitForChild("LeaderboardMath"))

local cfg = LeaderboardConfig
local player = Players.LocalPlayer
local playerGui = player:WaitForChild("PlayerGui")

local requestEvent = Remotes.event(cfg.REMOTE_REQUEST)
local stateEvent = Remotes.event(cfg.REMOTE_STATE)
local optOutEvent = Remotes.event(cfg.REMOTE_OPT_OUT)

-- The client keeps its own copy of the view and asks for a refresh rather than trusting a
-- push it did not request: the server re-sends on every accepted request and never
-- mutates what it already sent.
local boards = {}
local boardKey = nil
local scope = "global"
local page = 1
local pageCount = 1
local total = 0
local firstRank = 0
local rows = {}
local around = {}
local me = {}
local warning = nil
local isOpen = false
local busyUntil = 0
local lastFetch = -math.huge
local render

-- Slightly wider than the server's cooldown, so a request is never dropped for arriving a
-- frame early.
local FETCH_GAP = (tonumber(cfg.REQUEST_COOLDOWN) or 0.5) + 0.05

local BG = Color3.fromRGB(18, 20, 26)
local PANEL = Color3.fromRGB(26, 29, 37)
local FG = Color3.fromRGB(228, 232, 240)
local MUTED = Color3.fromRGB(158, 168, 186)
local ACCENT = Color3.fromRGB(96, 164, 214)
local WARN = Color3.fromRGB(224, 176, 92)

local function make(className, props, parent)
	local instance = Instance.new(className)
	for key, value in pairs(props or {}) do
		instance[key] = value
	end
	if parent then
		instance.Parent = parent
	end
	return instance
end

local NumberFormat = nil
do
	local holder = ReplicatedStorage:FindFirstChild("NumberFormat")
	if holder then
		local ok, module = pcall(require, holder)
		NumberFormat = ok and module or nil
	end
end

local function formatScore(value)
	local score = tonumber(value)
	if not score then
		return "—"
	end
	if type(NumberFormat) == "table" and type(NumberFormat.Short) == "function" then
		local ok, text = pcall(NumberFormat.Short, score)
		if ok and type(text) == "string" then
			return text
		end
	end
	return tostring(score)
end

local function request(force)
	if not boardKey then
		return
	end
	local now = os.clock()
	if not force and (now - lastFetch) < FETCH_GAP then
		return
	end
	lastFetch = now
	requestEvent:FireServer({ board = boardKey, scope = scope, page = page })
end

-- ── the window ────────────────────────────────────────────────────────────────────────

local gui = make("ScreenGui", {
	Name = "LeaderboardUI",
	ResetOnSpawn = false,
	IgnoreGuiInset = true,
	DisplayOrder = 58,
	ZIndexBehavior = Enum.ZIndexBehavior.Sibling,
}, playerGui)

local scrim = make("Frame", {
	Name = "Scrim",
	Size = UDim2.fromScale(1, 1),
	BackgroundColor3 = Color3.new(0, 0, 0),
	BackgroundTransparency = 0.45,
	BorderSizePixel = 0,
	Visible = false,
	ZIndex = 2,
}, gui)

local window = make("Frame", {
	Name = "Window",
	AnchorPoint = Vector2.new(0.5, 0.5),
	Position = UDim2.fromScale(0.5, 0.5),
	Size = UDim2.fromOffset(460, 460),
	BackgroundColor3 = BG,
	BorderSizePixel = 0,
	Visible = false,
	ZIndex = 3,
}, gui)
make("UICorner", { CornerRadius = UDim.new(0, 10), Parent = window })
make("UIStroke", { Color = Color3.fromRGB(48, 54, 68), Thickness = 1, Parent = window })

local title = make("TextLabel", {
	Name = "Title",
	Size = UDim2.new(1, -56, 0, 40),
	Position = UDim2.fromOffset(16, 8),
	BackgroundTransparency = 1,
	Font = Enum.Font.GothamBold,
	TextSize = 18,
	TextXAlignment = Enum.TextXAlignment.Left,
	TextColor3 = FG,
	Text = string.upper(cfg.UI_TITLE or "Leaderboard"),
	ZIndex = 4,
}, window)

local closeButton = make("TextButton", {
	Name = "Close",
	AnchorPoint = Vector2.new(1, 0),
	Position = UDim2.new(1, -12, 0, 12),
	Size = UDim2.fromOffset(28, 28),
	BackgroundColor3 = PANEL,
	BorderSizePixel = 0,
	Font = Enum.Font.GothamBold,
	TextSize = 14,
	TextColor3 = MUTED,
	Text = "X",
	AutoButtonColor = false,
	ZIndex = 4,
}, window)
make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = closeButton })

-- Tabs: one row of board tabs (only drawn when a game publishes more than one board) and
-- one row of scope tabs. Both are rebuilt by render(), so a board list that arrives from
-- the server needs no client edit.
local tabBar = make("Frame", {
	Name = "Tabs",
	Size = UDim2.new(1, -32, 0, 30),
	Position = UDim2.fromOffset(16, 48),
	BackgroundTransparency = 1,
	ZIndex = 4,
}, window)
make("UIListLayout", {
	FillDirection = Enum.FillDirection.Horizontal,
	SortOrder = Enum.SortOrder.LayoutOrder,
	Padding = UDim.new(0, 6),
	Parent = tabBar,
})

local scopeBar = make("Frame", {
	Name = "Scopes",
	Size = UDim2.new(1, -32, 0, 26),
	Position = UDim2.fromOffset(16, 82),
	BackgroundTransparency = 1,
	ZIndex = 4,
}, window)
make("UIListLayout", {
	FillDirection = Enum.FillDirection.Horizontal,
	SortOrder = Enum.SortOrder.LayoutOrder,
	Padding = UDim.new(0, 6),
	Parent = scopeBar,
})

local banner = make("TextLabel", {
	Name = "Warning",
	Size = UDim2.new(1, -32, 0, 30),
	Position = UDim2.fromOffset(16, 112),
	BackgroundColor3 = Color3.fromRGB(40, 34, 22),
	BorderSizePixel = 0,
	Font = Enum.Font.Gotham,
	TextSize = 12,
	TextWrapped = true,
	TextColor3 = WARN,
	Text = cfg.UI_NO_CLOUD or "Global scores are unavailable.",
	Visible = false,
	ZIndex = 4,
}, window)
make("UICorner", { CornerRadius = UDim.new(0, 6), Parent = banner })

local listFrame = make("ScrollingFrame", {
	Name = "Rows",
	Size = UDim2.new(1, -32, 1, -232),
	Position = UDim2.fromOffset(16, 148),
	BackgroundColor3 = PANEL,
	BorderSizePixel = 0,
	ScrollBarThickness = 4,
	CanvasSize = UDim2.new(),
	AutomaticCanvasSize = Enum.AutomaticSize.Y,
	ZIndex = 4,
}, window)
make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = listFrame })
make("UIListLayout", {
	SortOrder = Enum.SortOrder.LayoutOrder,
	Padding = UDim.new(0, 3),
	Parent = listFrame,
})
make("UIPadding", {
	PaddingTop = UDim.new(0, 6),
	PaddingBottom = UDim.new(0, 6),
	PaddingLeft = UDim.new(0, 8),
	PaddingRight = UDim.new(0, 8),
	Parent = listFrame,
})

local rowPool = {}
local function rowAt(index)
	local existing = rowPool[index]
	if existing then
		return existing
	end
	local frame = make("Frame", {
		Name = "Row" .. index,
		Size = UDim2.new(1, -4, 0, 26),
		BackgroundColor3 = Color3.fromRGB(32, 36, 46),
		BackgroundTransparency = 1,
		BorderSizePixel = 0,
		LayoutOrder = index,
		ZIndex = 5,
	}, listFrame)
	make("UICorner", { CornerRadius = UDim.new(0, 6), Parent = frame })
	local rank = make("TextLabel", {
		Name = "Rank",
		Size = UDim2.fromOffset(40, 26),
		BackgroundTransparency = 1,
		Font = Enum.Font.GothamBold,
		TextSize = 13,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextColor3 = MUTED,
		Text = "",
		ZIndex = 6,
	}, frame)
	local name = make("TextLabel", {
		Name = "Name",
		Size = UDim2.new(1, -150, 0, 26),
		Position = UDim2.fromOffset(44, 0),
		BackgroundTransparency = 1,
		Font = Enum.Font.Gotham,
		TextSize = 13,
		TextXAlignment = Enum.TextXAlignment.Left,
		TextTruncate = Enum.TextTruncate.AtEnd,
		TextColor3 = FG,
		Text = "",
		ZIndex = 6,
	}, frame)
	local score = make("TextLabel", {
		Name = "Score",
		AnchorPoint = Vector2.new(1, 0),
		Position = UDim2.new(1, -6, 0, 0),
		Size = UDim2.fromOffset(96, 26),
		BackgroundTransparency = 1,
		Font = Enum.Font.GothamBold,
		TextSize = 13,
		TextXAlignment = Enum.TextXAlignment.Right,
		TextColor3 = ACCENT,
		Text = "",
		ZIndex = 6,
	}, frame)
	local row = { frame = frame, rank = rank, name = name, score = score }
	rowPool[index] = row
	return row
end

local youPanel = make("Frame", {
	Name = "You",
	Size = UDim2.new(1, -32, 0, 44),
	Position = UDim2.new(0, 16, 1, -76),
	BackgroundColor3 = PANEL,
	BorderSizePixel = 0,
	ZIndex = 4,
}, window)
make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = youPanel })
local youLabel = make("TextLabel", {
	Name = "Label",
	Size = UDim2.new(1, -16, 0, 20),
	Position = UDim2.fromOffset(8, 4),
	BackgroundTransparency = 1,
	Font = Enum.Font.GothamBold,
	TextSize = 12,
	TextXAlignment = Enum.TextXAlignment.Left,
	TextColor3 = FG,
	Text = "",
	ZIndex = 5,
}, youPanel)
local youDetail = make("TextLabel", {
	Name = "Detail",
	Size = UDim2.new(1, -16, 0, 16),
	Position = UDim2.fromOffset(8, 24),
	BackgroundTransparency = 1,
	Font = Enum.Font.Gotham,
	TextSize = 11,
	TextXAlignment = Enum.TextXAlignment.Left,
	TextColor3 = MUTED,
	Text = "",
	ZIndex = 5,
}, youPanel)

local pager = make("Frame", {
	Name = "Pager",
	Size = UDim2.new(1, -32, 0, 26),
	Position = UDim2.new(0, 16, 1, -44),
	BackgroundTransparency = 1,
	ZIndex = 4,
}, window)

local function pagerButton(label, anchorX, offsetX)
	local button = make("TextButton", {
		Name = label,
		AnchorPoint = Vector2.new(anchorX, 0),
		Position = UDim2.new(anchorX, offsetX, 0, 0),
		Size = UDim2.fromOffset(70, 26),
		BackgroundColor3 = PANEL,
		BorderSizePixel = 0,
		Font = Enum.Font.GothamBold,
		TextSize = 12,
		TextColor3 = FG,
		Text = label,
		AutoButtonColor = false,
		ZIndex = 5,
	}, pager)
	make("UICorner", { CornerRadius = UDim.new(0, 6), Parent = button })
	return button
end

local prevButton = pagerButton("Previous", 0, 0)
local nextButton = pagerButton("Next", 1, 0)
local pageLabel = make("TextLabel", {
	Name = "PageLabel",
	Size = UDim2.new(1, -160, 0, 26),
	Position = UDim2.fromOffset(80, 0),
	BackgroundTransparency = 1,
	Font = Enum.Font.Gotham,
	TextSize = 12,
	TextColor3 = MUTED,
	Text = "",
	ZIndex = 5,
}, pager)

local optOutButton = make("TextButton", {
	Name = "OptOut",
	Size = UDim2.new(1, -32, 0, 28),
	Position = UDim2.new(0, 16, 1, -110),
	BackgroundColor3 = PANEL,
	BorderSizePixel = 0,
	Font = Enum.Font.Gotham,
	TextSize = 12,
	TextColor3 = FG,
	Text = cfg.UI_OPT_OUT_LABEL or "Hide me from leaderboards",
	AutoButtonColor = false,
	ZIndex = 4,
}, window)
make("UICorner", { CornerRadius = UDim.new(0, 6), Parent = optOutButton })

-- ── rendering ─────────────────────────────────────────────────────────────────────────

local function tabBarButton(parent, label, order, selected, onClick)
	local button = make("TextButton", {
		Name = label,
		Size = UDim2.fromOffset(96, parent == tabBar and 28 or 24),
		BackgroundColor3 = selected and ACCENT or PANEL,
		BorderSizePixel = 0,
		Font = Enum.Font.GothamBold,
		TextSize = 11,
		TextColor3 = selected and BG or MUTED,
		Text = label,
		AutoButtonColor = false,
		LayoutOrder = order,
		ZIndex = 5,
	}, parent)
	make("UICorner", { CornerRadius = UDim.new(0, 6), Parent = button })
	button.Activated:Connect(onClick)
	return button
end

-- Names come off a store the server does not own, so they are trimmed again here: one
-- over-long entry must not stretch every row in the list.
local function displayable(row)
	return LeaderboardMath.trimName(type(row) == "table" and row.name or nil, cfg.MAX_NAME_LENGTH)
end

render = function()
	tabBar:ClearAllChildren()
	make("UIListLayout", {
		FillDirection = Enum.FillDirection.Horizontal,
		SortOrder = Enum.SortOrder.LayoutOrder,
		Padding = UDim.new(0, 6),
		Parent = tabBar,
	})
	if #boards > 1 then
		for index, board in ipairs(boards) do
			local key = board.key
			tabBarButton(tabBar, tostring(board.label or key), index, key == boardKey, function()
				if key == boardKey then
					return
				end
				boardKey = key
				page = 1
				request(true)
			end)
		end
	end

	scopeBar:ClearAllChildren()
	make("UIListLayout", {
		FillDirection = Enum.FillDirection.Horizontal,
		SortOrder = Enum.SortOrder.LayoutOrder,
		Padding = UDim.new(0, 6),
		Parent = scopeBar,
	})
	tabBarButton(scopeBar, cfg.UI_GLOBAL_TAB or "Global", 1, scope == "global", function()
		if scope == "global" then
			return
		end
		scope = "global"
		page = 1
		request(true)
	end)
	tabBarButton(scopeBar, cfg.UI_SERVER_TAB or "This server", 2, scope == "server", function()
		if scope == "server" then
			return
		end
		scope = "server"
		page = 1
		request(true)
	end)

	banner.Visible = scope == "global" and warning ~= nil
	listFrame.CanvasPosition = Vector2.new(0, 0)

	local shown = {}
	for index, row in ipairs(rows) do
		shown[index] = { rank = firstRank + index - 1, name = displayable(row), score = row.score, userId = row.userId }
	end
	-- A ranked player who is not on this page still gets their neighbourhood, so the window
	-- never says "unranked" about someone the server just placed at 412.
	local onThisPage = false
	for _, entry in ipairs(shown) do
		if entry.userId == player.UserId then
			onThisPage = true
			break
		end
	end
	if not onThisPage and me.rank and #around > 0 then
		local base = tonumber(me.aroundFirstRank) or math.max(1, (me.rank or 1) - #around)
		for offset, row in ipairs(around) do
			table.insert(shown, {
				rank = base + offset - 1,
				name = displayable(row),
				score = row.score,
				userId = row.userId,
			})
		end
	end

	for index = 1, math.max(#rowPool, #shown) do
		local row = rowAt(index)
		local entry = shown[index]
		row.frame.Visible = entry ~= nil
		if entry then
			row.frame.BackgroundTransparency = entry.userId == player.UserId and 0 or 1
			row.frame.BackgroundColor3 = entry.userId == player.UserId and Color3.fromRGB(38, 52, 68) or PANEL
			row.rank.Text = tostring(entry.rank)
			row.name.Text = entry.name
			row.score.Text = formatScore(entry.score)
		end
	end

	if #shown == 0 then
		local row = rowAt(1)
		row.frame.Visible = true
		row.frame.BackgroundTransparency = 1
		row.rank.Text = ""
		row.name.Text = tostring(cfg.UI_EMPTY or "No scores yet.")
		row.score.Text = ""
	end

	if me.rank then
		youLabel.Text = ("You are #%d"):format(me.rank)
	else
		youLabel.Text = scope == "global" and "You are unranked on this board" or "You are not on this server's board"
	end
	youDetail.Text = ("Your best: %s%s"):format(formatScore(me.score), total > 0 and ("  ·  %d ranked"):format(total) or "")

	prevButton.Active = page > 1
	prevButton.TextColor3 = page > 1 and FG or MUTED
	nextButton.Active = page < pageCount
	nextButton.TextColor3 = page < pageCount and FG or MUTED
	pageLabel.Text = ("Page %d of %d"):format(page, pageCount)

	local busy = os.clock() < busyUntil
	optOutButton.TextColor3 = busy and MUTED or FG
	optOutButton.Text = busy and "..." or ("%s %s"):format(me.optOut and "[x]" or "[  ]", cfg.UI_OPT_OUT_LABEL or "Hide me from leaderboards")
end

-- ── state ─────────────────────────────────────────────────────────────────────────────

local function applyState(payload)
	if type(payload.boards) == "table" and #payload.boards > 0 then
		boards = payload.boards
	end
	if not boardKey then
		boardKey = type(payload.board) == "string" and payload.board or (boards[1] and boards[1].key) or nil
	elseif type(payload.board) == "string" then
		boardKey = payload.board
	end
	scope = payload.scope == "server" and "server" or "global"
	rows = type(payload.rows) == "table" and payload.rows or {}
	around = type(payload.around) == "table" and payload.around or {}
	me = type(payload.me) == "table" and payload.me or {}
	total = tonumber(payload.total) or #rows
	pageCount = math.max(1, tonumber(payload.pageCount) or 1)
	page = math.clamp(tonumber(payload.page) or 1, 1, pageCount)
	firstRank = tonumber(payload.firstRank) or ((page - 1) * (cfg.PAGE_SIZE or 20) + 1)
	warning = payload.warning
end

stateEvent.OnClientEvent:Connect(function(payload)
	if type(payload) ~= "table" then
		return
	end
	applyState(payload)
	if isOpen then
		render()
	end
end)

local function setOpen(wantOpen)
	isOpen = wantOpen and true or false
	window.Visible = isOpen
	scrim.Visible = isOpen
	if isOpen then
		if not boardKey then
			boardKey = boards[1] and boards[1].key or (cfg.BOARD_ORDER and cfg.BOARD_ORDER[1]) or nil
		end
		request(true)
		render()
	end
end

closeButton.Activated:Connect(function()
	setOpen(false)
end)
scrim.InputBegan:Connect(function(input)
	if input.UserInputType == Enum.UserInputType.MouseButton1 then
		setOpen(false)
	end
end)
prevButton.Activated:Connect(function()
	if page <= 1 then
		return
	end
	page -= 1
	request(true)
	render()
end)
nextButton.Activated:Connect(function()
	if page >= pageCount then
		return
	end
	page += 1
	request(true)
	render()
end)
optOutButton.Activated:Connect(function()
	if os.clock() < busyUntil then
		return
	end
	busyUntil = os.clock() + FETCH_GAP
	optOutEvent:FireServer({ on = not (me.optOut and true or false), board = boardKey, scope = scope, page = page })
	render()
end)

shared.LeaderboardClientAPI = {
	Open = function()
		setOpen(true)
	end,
	Close = function()
		setOpen(false)
	end,
	Toggle = function()
		setOpen(not isOpen)
	end,
	IsOpen = function()
		return isOpen
	end,
	Refresh = function()
		request(true)
		render()
	end,
	ShowBoard = function(key, wantedScope, wantedPage)
		if type(key) == "string" then
			boardKey = key
		end
		scope = wantedScope == "server" and "server" or "global"
		page = math.max(1, math.floor(tonumber(wantedPage) or 1))
		request(true)
		render()
	end,
	State = function()
		return {
			board = boardKey,
			scope = scope,
			page = page,
			pageCount = pageCount,
			total = total,
			rows = rows,
			me = me,
			warning = warning,
			boards = boards,
		}
	end,
}

-- ── optional ui/kit integration ───────────────────────────────────────────────────────
-- With ui/kit installed the window joins UIManager (so UIManager.open("Leaderboard") works
-- and the tutorial lock is honoured) and a HUD tile opens it. Without the kit, this script
-- makes its own corner button, because a leaderboard nobody can open is not a feature.

local function optionalModule(name)
	local holder = script.Parent:FindFirstChild(name) or ReplicatedStorage:FindFirstChild(name)
	if not holder then
		return nil
	end
	local ok, module = pcall(require, holder)
	if not ok then
		warn(("leaderboard: could not require %s (%s)"):format(name, tostring(module)))
		return nil
	end
	return module
end

local UIManager = optionalModule("UIManager")
if UIManager and type(UIManager.register) == "function" then
	UIManager.register(cfg.WINDOW_KEY, window)
	window:GetPropertyChangedSignal("Visible"):Connect(function()
		if window.Visible ~= isOpen then
			setOpen(window.Visible)
		end
	end)
	local originalSetOpen = setOpen
	setOpen = function(wantOpen)
		if wantOpen and type(UIManager.canOpen) == "function" and not UIManager.canOpen(cfg.WINDOW_KEY) then
			return
		end
		originalSetOpen(wantOpen)
	end
end

local opened = false
local UIStyle = optionalModule("UIStyle")
if UIStyle and type(UIStyle.hudButton) == "function" then
	local Icons = optionalModule("Icons")
	local ok, err = pcall(function()
		local tile = UIStyle.hudButton({
			label = cfg.UI_TITLE or "Leaderboard",
			icon = Icons and Icons.Trophy or "#",
			color = UIStyle.Colors and UIStyle.Colors.blue,
			order = cfg.HUD_ORDER,
		})
		tile.Activated:Connect(function()
			shared.LeaderboardClientAPI.Toggle()
		end)
		opened = true
	end)
	if not ok then
		warn(("leaderboard: no HUD tile (%s)"):format(tostring(err)))
	end
end

if not opened then
	local corner = make("TextButton", {
		Name = "LeaderboardButton",
		AnchorPoint = Vector2.new(1, 0),
		Position = UDim2.new(1, -12, 0, 120),
		Size = UDim2.fromOffset(110, 32),
		BackgroundColor3 = BG,
		BorderSizePixel = 0,
		Font = Enum.Font.GothamBold,
		TextSize = 12,
		TextColor3 = FG,
		Text = cfg.UI_TITLE or "Leaderboard",
		AutoButtonColor = false,
		ZIndex = 2,
	}, gui)
	make("UICorner", { CornerRadius = UDim.new(0, 8), Parent = corner })
	make("UIStroke", { Color = Color3.fromRGB(48, 54, 68), Thickness = 1, Parent = corner })
	corner.Activated:Connect(function()
		shared.LeaderboardClientAPI.Toggle()
	end)
end

if math.floor(cfg.CLIENT_REFRESH or 0) > 0 then
	task.spawn(function()
		while true do
			task.wait(cfg.CLIENT_REFRESH)
			if isOpen then
				request(false)
			end
		end
	end)
end

applyState({ board = cfg.BOARD_ORDER and cfg.BOARD_ORDER[1], boards = {} })
render()
setOpen(false)
