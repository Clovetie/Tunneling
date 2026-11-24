--!nonstrict
-- Every tunable for mechanics/checkpoint-respawn. No Roblox types in here on purpose:
-- CheckpointOrder requires this file, and keeping it plain means the pure ordering logic
-- (and its spec) has no DataModel to stand up.

local CheckpointConfig = {
	-- How checkpoints are found. Tag the claim Part with CHECKPOINT_TAG, or put the Parts
	-- in Workspace.<CHECKPOINT_FOLDER>; in both cases the id is the ID_ATTRIBUTE or the
	-- instance name, and the order is the ORDER_ATTRIBUTE or the sibling position.
	CHECKPOINT_TAG = "Checkpoint",
	CHECKPOINT_FOLDER = "Checkpoints",
	ID_ATTRIBUTE = "CheckpointId",
	ORDER_ATTRIBUTE = "CheckpointOrder",

	-- Progression. STRICT_ORDER means only the next unclaimed checkpoint can be claimed,
	-- so touching checkpoint 7 on the way to checkpoint 2 does not skip the level.
	-- DEFAULT_CHECKPOINT_ID is where a player respawns before claiming anything; nil means
	-- "leave the game's own SpawnLocations alone".
	STRICT_ORDER = true,
	DEFAULT_CHECKPOINT_ID = nil,

	-- Claiming. Touch is detected on the server, so it needs no client trust; the remote
	-- path exists for games that claim by proximity or prompt instead, and is validated by
	-- distance, cooldown and a NetGuard rate limit.
	TOUCH_TO_CLAIM = true,
	CLAIM_RADIUS = 24,
	CLAIM_COOLDOWN = 0.5,
	CLAIM_RATE_LIMIT = 8,
	CLAIM_RATE_WINDOW = 2,

	-- Respawn: move the new character to the highest claimed checkpoint, this far above it
	-- so it does not spawn intersecting the platform.
	RESPAWN_AT_CHECKPOINT = true,
	RESPAWN_OFFSET_Y = 3,

	-- Persistence. data[SAVE_KEY] = { claimed = { [id] = true } }. With
	-- REQUIRE_DATA_SERVICE false the system still runs when core/data is not installed,
	-- keeping progress in memory for the session and warning once about it.
	SAVE_KEY = "checkpoints",
	REQUIRE_DATA_SERVICE = false,

	-- Party sharing, off by default: when on, a claim also grants the same checkpoint to
	-- every player within PARTY_RADIUS, so a group progresses together.
	PARTY_SHARE = false,
	PARTY_RADIUS = 60,

	-- Remote names (created through core's Remotes.event).
	REMOTE_CLAIM = "CheckpointClaim",
	REMOTE_STATE = "CheckpointState",

	-- UI wording and placement, as plain numbers. A game renames TITLE to its own noun;
	-- nothing here is game-specific.
	UI_ENABLED = true,
	UI_TITLE = "Checkpoint",
	UI_ANCHOR_X = 0.5,
	UI_ANCHOR_Y = 0.84,
	UI_TOAST_SECONDS = 2.5,
	UI_PROGRESS_FORMAT = "%d / %d",
	UI_REACHED_FORMAT = "%s reached",
}

return CheckpointConfig
