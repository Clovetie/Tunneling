# liveops/leaderboard — global and per-server boards

## 1. What it does

**Player-facing:** a LEADERBOARD HUD tile (or a corner button, in a game without `ui/kit`) opens a window with two tabs — **Global** and **This server** — and, when a game publishes more than one board, a row of board tabs. Rows show rank, name and score, with the player's own row lit. Below the list a "You are #N" panel carries their best score and how many players are ranked; when they are ranked but not on the page they are looking at, the rows around them are shown instead, so the window never says "unranked" about someone the server just placed 412th. A pager walks the board, a checkbox hides the player from it for good, and a banner explains — without an error — that global scores are unavailable in this session, which in Studio with API access off is the normal case.

**Technical:**
- **Two kinds of store, because an `OrderedDataStore` value must be an integer.** Scores live in one ordered store per board, named `<STORE_PREFIX>_<board key>_<STORE_VERSION>` and keyed `Player_<UserId>`; display names live in one plain store (`NAME_STORE_NAME`). Never rename either after launch — a new name is a new, empty board. `STORE_VERSION` is the lever for starting fresh: bump it and the old boards stay in the DataStore, unread.
- **Writes are sampled and throttled, never event-driven.** Each board's value is read every `SAMPLE_INTERVAL` (5 s), the best seen is kept in memory, and it reaches the store at most once per `WRITE_INTERVAL` (30 s) per player per board — enforced twice, once by the service and once per key by `DataStoreSafe`. A write per currency gain exhausts the DataStore budget and drops the writes that matter. `LEAVE_WRITE` forces a final write on `PlayerRemoving`, and `BindToClose` flushes everything still pending.
- **Reads are cached and names are resolved lazily.** A fetched board is cached for `CACHE_TTL` (30 s). Names are looked up only for the rows about to be sent — a page of `PAGE_SIZE` (20), cached inside `DataStoreSafe` for 300 s — not for all `MAX_FETCH` (100) rows, so a refresh costs a page of reads at most once per name.
- **Graceful degradation is the whole point.** Without `DataStoreSafe` in `ReplicatedStorage`, the service warns once, keeps the per-server board working, and answers global requests with `warning = "missing_module"`. In Studio with API access off, the first store call fails, `DataStoreSafe` switches to its in-memory fallback with a single warning, and the client shows `UI_NO_CLOUD`. Neither path errors, and neither stops the game.
- **Nothing that comes out of a store is trusted on its way to a client.** Every row goes through `LeaderboardMath.sanitizeRow`, which floors the score, trims the name to whole UTF-8 characters and drops rows with a missing, negative or non-numeric id or score. One corrupt or hand-edited entry costs one row, not the board.
- **Equal scores cannot reshuffle.** Rows are ordered by score descending, then name, then id. Without the tie-breaks two players on the same score swap places on every refresh, which reads as a bug even when nothing is wrong.
- **Opt-out means removed, not hidden.** A player who opts out stops being written, disappears from both boards, and has their existing key deleted from the ordered store and the name store — a checkbox that only stopped *new* writes would leave their old score on the board forever. The choice is mirrored onto the `LeaderboardOptOut` attribute and, when `core/data` is installed, persisted at `data.leaderboardOptOut`, and it is honoured on join *before* the first sample.
- **Where a score comes from is config.** `source.kind` is `leaderstat` (`player.leaderstats[key]`), `attribute` (a Player attribute), `data` (a field on the `core/data` profile) or `economy` (`EconomyAPI.GetBalance`). A game whose scores come from events instead calls `LeaderboardAPI.Submit(player, boardKey, value)`.
- **The live server board is merged onto the cached global one** by `LeaderboardMath.mergeRows`, which dedupes by id and keeps the higher score, so a player sees the score they just earned rather than the one from the last flush. On a tie the cached row wins, which keeps the result independent of the order the two lists were passed in.
- **Requests are validated and rate limited.** A fetch must name a board that exists (string, ≤ 32 chars), the scope must be `global` or `server`, and the page is clamped to the board's real page count. One fetch per player per `REQUEST_COOLDOWN` (0.5 s), plus `NetGuard.allowRate` when that module is installed. The opt-out remote shares the same cooldown, so it cannot be used to force uncached global reads.

## 2. Depends on

- `systems/core`: remotes (`Remotes.event`). Optional: `shared.DataServiceAPI` (opt-out persistence), `shared.EconomyAPI` (the `economy` score source), `NumberFormat` (short score display).
- `modules/DataStoreSafe`: `createOrderedStore` — the throttled, retrying, memory-fallback store this system writes through. Without it, per-server boards only.
- Optional: `modules/NetGuard` for request rate limiting.
- Optional: `systems/ui/kit` (`UIManager`, `UIStyle`, `Icons.Trophy`) for the HUD tile and panel coordination. Without it the client makes its own corner button, because a leaderboard nobody can open is not a feature.
- **Roblox side:** API access enabled (`Game Settings → Security → Enable Studio Access to API Services`) for the global board to work in Studio, and the game published. Two DataStores are created per board set: `Leaderboard_Score_v1` (ordered) and `LeaderboardNames_v1`.

## 3. Install

| File | Destination |
| --- | --- |
| `server/LeaderboardService.server.lua` | `src/server/` |
| `client/LeaderboardUI.client.lua` | `src/client/` |
| `shared/LeaderboardConfig.lua` | `src/shared/` |
| `shared/LeaderboardMath.lua` | `src/shared/` |
| `tests/Leaderboard.spec.lua` | `tests/` |

Also copy `modules/DataStoreSafe.lua` into `ReplicatedStorage` if the game does not have it already.

**DataTemplate:** add `leaderboardOptOut = false`. Missing fields are tolerated, so an existing save needs no migration.

**Config (`LeaderboardConfig.lua`):**

| Key | Default | Meaning |
| --- | --- | --- |
| `BOARDS` | one board, `Score` | `{ key, label, source = { kind, key }, enabled }`. Add an entry and it gets its own ordered store, tab and write throttle. `enabled = false` retires a board without deleting its store. |
| `BOARD_ORDER` | `{ "Score" }` | Tab order. Boards missing from it come after, in `BOARDS` order — the order is deterministic either way. |
| `STORE_PREFIX` / `STORE_VERSION` | `"Leaderboard"` / `"v1"` | Store names are `<prefix>_<board key>_<version>`. Never rename after launch. |
| `NAME_STORE_NAME` | `"LeaderboardNames_v1"` | The one plain store holding display names |
| `SAMPLE_INTERVAL` / `WRITE_INTERVAL` | `5` / `30` | Seconds between reading values, and the minimum gap between writes for one player on one board. `SAMPLE_INTERVAL = 0` turns sampling off entirely, for games that only `Submit`. |
| `MIN_SCORE` | `1` | Below this a score is not published, so the store holds players rather than zeros |
| `MAX_NAME_LENGTH` | `24` | Stored name budget, in bytes, cut on codepoint boundaries |
| `CACHE_TTL` / `PAGE_SIZE` / `MAX_FETCH` | `30` / `20` / `100` | Read cache, rows per page, and how deep the global board is fetched (100 is also the store's page ceiling, so it bounds the page count) |
| `AROUND_WINDOW` | `4` | Rows shown either side of the player when they are off-page |
| `OPT_OUT_ATTRIBUTE` / `SAVE_KEY` | `"LeaderboardOptOut"` / `"leaderboardOptOut"` | Where the opt-out is mirrored and persisted |
| `REQUEST_COOLDOWN` / `REQUEST_RATE_LIMIT` / `REQUEST_RATE_WINDOW` | `0.5` / `4` / `10` | Per-player cooldown, then NetGuard's window |
| `CLIENT_REFRESH` | `30` | Seconds between automatic refreshes while the window is open. `0` disables it. |
| `WINDOW_KEY` / `HUD_ORDER` | `"Leaderboard"` / `30` | `UIManager` name and dock tile order |
| `UI_*` | title, tab labels, empty and no-cloud wording | What the window says |

**Ids to create:** none. Two DataStores appear on first write.

## 4. Remotes

| Remote | Direction | Payload |
| --- | --- | --- |
| `LeaderboardRequest` | client → server | `{ board: string, scope: "global"\|"server", page: number }`. Ignored unless the board exists; the page is clamped; at most one per player per 0.5 s. |
| `LeaderboardState` | server → client | `{ board, scope, page, pageCount, total, firstRank, rows, around, me = { rank, score, optOut, aroundFirstRank }, warning?, boards }`. Rows are `{ userId, name, score }`, already sanitised. Sent as the reply to every accepted request and after an opt-out change. |
| `LeaderboardOptOut` | client → server | `{ on: boolean, board, scope, page }`. Shares the fetch cooldown; replies with the view the client was already looking at, so a toggle does not throw the player back to page 1. |

`warning` is `"missing_module"` when `DataStoreSafe` is not installed and `"datastore_unavailable"` when a store call failed (Studio with API access off, or an outage). The client shows it as a banner; it is never an error.

### `shared.LeaderboardAPI` (server)

| Member | Returns | Notes |
| --- | --- | --- |
| `Boards()` | `{ { key, label } }` | In tab order |
| `GetTop(boardKey, limit?)` | rows | Cached global board, sanitised. `limit` defaults to `PAGE_SIZE` |
| `GetServerTop(boardKey, limit?)` | rows | This server only, live |
| `GetScore(player, boardKey)` | number or `nil` | Best seen this session |
| `GetRank(player, boardKey)` | number or `nil` | Within the fetched top `MAX_FETCH`; `nil` means "outside it", not "last" |
| `Submit(player, boardKey, value)` | boolean | For event-driven scores. Keeps the higher of the old and new value, and reports whether it was published |
| `SetOptOut(player, on)` / `IsOptOut(player)` | boolean | `SetOptOut(true)` also removes the player's stored keys |
| `Refresh(boardKey?)` | — | Drops the read cache, so the next fetch is live |
| `Status()` | `{ cloud, cloudReason, boards, stores, pendingWrites, inServer }` | What to log when someone asks why the board is empty |

### `shared.LeaderboardClientAPI` (client)

`Open()`, `Close()`, `Toggle()`, `IsOpen()`, `Refresh()`, `ShowBoard(key, scope?, page?)`, `State()` → the current view (`board`, `scope`, `page`, `pageCount`, `total`, `rows`, `me`, `warning`, `boards`).

## 5. Verify in Studio (MCP)

1. `start_stop_play`, then `get_console_output`. Expect **warnings, not errors**: without `DataStoreSafe` a `leaderboard: DataStoreSafe is not in ReplicatedStorage…` line, and with API access off a `[DataStoreSafe] Ordered store … memory-fallback mode` line. Both mean the system degraded on purpose.
2. **Open:** `execute_luau` (Client) `shared.LeaderboardClientAPI.Toggle()`, then check `PlayerGui.LeaderboardUI.Window.Visible` is `true` and `shared.LeaderboardClientAPI.IsOpen()` is `true`. With `ui/kit` installed, `PlayerGui.HudDock` has a `LeaderboardSlot`.
3. **Per-server board:** on the Server, `LeaderboardAPI.Submit(player, "Score", 250)` for two players (or give them a `leaderstats.Score` and wait `SAMPLE_INTERVAL`). In the Client, switch to **This server**: both rows appear, ordered by score, and the local player's row is lit.
4. **Global board:** with API access on, `Submit(player, "Score", 250)`, then `LeaderboardAPI.Status()` on the Server: `cloud` is `true`. Read it back with `LeaderboardAPI.GetTop("Score", 10)` — the row appears once the write throttle allows it (up to `WRITE_INTERVAL`; a player leaving forces one, `Submit` does not, and `LeaderboardAPI.Status().pendingWrites` shows what is queued). With API access off, `Status().cloudReason` is `"datastore_unavailable"`, the client shows the banner, and nothing errors.
5. **Throttle:** with API access on, `Submit(player, "Score", 1)` then `Submit(player, "Score", 999)` inside a second: `GetScore` reports `999` (the better value is kept) and `Status().pendingWrites` is `1` — the first score was written, the second is queued for the next interval, so two submissions cost one write. In the memory fallback nothing is queued, so `pendingWrites` stays `0`; that is the point of the fallback.
6. **Pagination:** `#LeaderboardAPI.GetTop("Score", 100)` rows, then in the Client press **Next**: `LeaderboardClientAPI.State().page` is `2` and `firstRank` on the payload is `PAGE_SIZE + 1`. Asking for page 999 returns the last page rather than an empty one.
7. **Off-page rank:** with more than `PAGE_SIZE` rows, look at the "You" panel while on page 1 as a player ranked below it: the panel names the real rank and the rows around them are listed.
8. **Opt-out:** in the Client, tick **Hide me from leaderboards**. On the Server, `LeaderboardAPI.IsOptOut(player)` is `true`, the player's `LeaderboardOptOut` attribute is `true`, `data.leaderboardOptOut` is `true`, and the row is gone from **both** tabs. `Submit` again: `GetScore` still tracks it locally but nothing is published. Untick and the row returns on the next sample.
9. **Forged input:** from the Client, `Remotes.event("LeaderboardRequest"):FireServer(...)` with `"Score"` replaced by `42`, `nil`, `{}`, `string.rep("A", 200)`, `"NoSuchBoard"` and `{ board = "Score", page = "x" }`: no reply, no error in the output. Fire 20 valid requests inside a second: at most `REQUEST_RATE_LIMIT` are answered. Fire `LeaderboardOptOut` with `{ on = "yes" }` and with `42`: ignored, and `IsOptOut` is unchanged.
10. **Corrupt rows:** with API access on, write a bad entry into the ordered store from the Server (`GetOrderedDataStore("Leaderboard_Score_v1"):SetAsync("Player_999999", -5)`) and refresh: the row is dropped by `sanitizeRow` and the rest of the board still renders.
11. **Specs:** set the `RunTests` attribute on `ReplicatedStorage.Tests` and Play. `Leaderboard.spec` passes.

## 6. Origin

New for the Kit (P5-K3). No reference-game file had a leaderboard, so there was nothing to extract; the shape comes from `systems/CONVENTIONS.md` with `liveops/codes` as the exemplar, and from the kit's own lessons file (`.claude/skills/roblox-lessons/references/data.md`): *"OrderedDataStore values must be integers; keep display metadata (name, rarest item) in a normal store; refresh on a timer and on the events that change rank."* That is why this system is two stores plus a timer, and why `core/data` and `DataStoreSafe` existing was not enough — nothing in the kit demonstrated the pattern.

- `modules/DataStoreSafe.lua` gained `createOrderedStore(storeName, options)` for this system: `Set` (throttled per key by `minWriteInterval`, with `force`), `Remove`, `GetTop` (plain rows, not DataStore row objects), `GetName` (cached), `Flush`, `Pending` and `Available`. It follows the module's existing conventions exactly — three retries with backoff, a single `warn` and an in-memory fallback when the cloud is unavailable, and a `BindToClose` flush — and `createProfileStore` is untouched.
- `shared/LeaderboardMath.lua` is pure, so `tests/Leaderboard.spec.lua` (46 cases, 162 assertions) runs without a DataModel. Names are trimmed on codepoint boundaries rather than by byte count: a cut that lands exactly on a boundary would otherwise delete a character that was already complete.
- The window is drawn by this system rather than by `ui/kit`, and joins `UIManager` when the kit is present, so it installs either way.
- `GetRank` reports a rank only within the fetched top `MAX_FETCH`. Returning `nil` for "outside the top 100" is deliberate: an exact global rank would need a scan of the whole store per query.
