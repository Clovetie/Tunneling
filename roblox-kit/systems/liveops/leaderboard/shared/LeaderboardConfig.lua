--!nonstrict
-- Every tunable for liveops/leaderboard. Plain data only: LeaderboardMath requires this
-- file, and keeping Roblox types out means the ranking logic (and its spec) needs no
-- DataModel.

local LeaderboardConfig = {
	-- OrderedDataStore values must be integers, and display metadata does not belong in
	-- them, so the board is two kinds of store: one ordered store per board, named
	-- "<STORE_PREFIX>_<board key>_<STORE_VERSION>", plus ONE plain store holding every
	-- player's display name. Never rename these after launch — a new name is a new, empty
	-- board. Bumping STORE_VERSION starts fresh boards and leaves the old ones unread.
	STORE_PREFIX = "Leaderboard",
	STORE_VERSION = "v1",
	NAME_STORE_NAME = "LeaderboardNames_v1",

	-- Boards to publish. Add one here and it gets its own ordered store, its own tab and
	-- its own writes; nothing else changes. `source` says where a player's value comes
	-- from: "leaderstat" reads player.leaderstats[key], "attribute" reads a Player
	-- attribute, "data" reads a field on the core/data profile, "economy" reads
	-- EconomyAPI.GetBalance. Set a board's `enabled = false` to drop it without deleting
	-- its store.
	BOARDS = {
		{
			key = "Score",
			label = "Score",
			source = { kind = "leaderstat", key = "Score" },
			enabled = true,
		},
	},
	BOARD_ORDER = { "Score" },

	-- Sampling and writing. Values are read every SAMPLE_INTERVAL and the best seen is
	-- written to the ordered store at most once per WRITE_INTERVAL per player per board:
	-- a write per currency gain exhausts the DataStore budget and drops the writes that
	-- matter. LEAVE_WRITE forces one final write when a player leaves.
	SAMPLE_INTERVAL = 5,
	WRITE_INTERVAL = 30,
	LEAVE_WRITE = true,

	-- A score below this is not published, so the store holds players, not zeros.
	MIN_SCORE = 1,
	-- Longest name stored beside a score. Names are display only; the store keys are ids.
	MAX_NAME_LENGTH = 24,

	-- Reading. Pages are cached for CACHE_TTL so a board full of players refreshing does
	-- not turn into a read storm, and one ordered-store fetch is at most 100 entries, which
	-- is also the store's own page ceiling. That bounds the page count at MAX_FETCH /
	-- PAGE_SIZE, so a client asking for page 9999 is clamped rather than served.
	CACHE_TTL = 30,
	PAGE_SIZE = 20,
	MAX_FETCH = 100,
	-- How many rows either side of the player the "around me" slice returns.
	AROUND_WINDOW = 4,

	-- Opt-out. A player who opts out stops being written, is dropped from both boards and
	-- has their existing key removed from the ordered store — otherwise the toggle is a
	-- lie. The choice is remembered on the save when core/data is installed, and on the
	-- OPT_OUT_ATTRIBUTE either way. REMOTE_OPT_OUT carries
	-- `{ on = boolean, board = key, scope = "global"|"server", page = number }`.
	OPT_OUT_ATTRIBUTE = "LeaderboardOptOut",
	SAVE_KEY = "leaderboardOptOut",

	-- Requests. One board fetch per player per REQUEST_COOLDOWN, plus NetGuard when it is
	-- installed.
	REMOTE_REQUEST = "LeaderboardRequest",
	REMOTE_STATE = "LeaderboardState",
	REMOTE_OPT_OUT = "LeaderboardOptOut",
	REQUEST_COOLDOWN = 0.5,
	REQUEST_RATE_LIMIT = 4,
	REQUEST_RATE_WINDOW = 10,

	-- What the client refreshes on its own, in seconds. 0 disables it.
	CLIENT_REFRESH = 30,

	WINDOW_KEY = "Leaderboard",
	HUD_ORDER = 30,

	UI_TITLE = "Leaderboard",
	UI_ROWS = 10,
	UI_GLOBAL_TAB = "Global",
	UI_SERVER_TAB = "This server",
	UI_OPT_OUT_LABEL = "Hide me from leaderboards",
	UI_EMPTY = "No scores yet — be the first.",
	-- Shown instead of an error when the DataStore is unavailable, which in Studio with
	-- API access off is the normal case rather than a failure.
	UI_NO_CLOUD = "Global scores are unavailable here. Showing this server only.",
}

return LeaderboardConfig
