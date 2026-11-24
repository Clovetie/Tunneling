# ui/settings-menu — player settings, accessibility and key binds

## 1. What it does

**Player-facing:** press `F1` (or the SETTINGS HUD tile, when `ui/kit` is installed) and a window opens with sliders for master/music/SFX volume and camera sensitivity, toggles for invert-Y, reduce motion and on-screen hints, a colour-blind palette picker, a list of rebindable actions, and a "Restore defaults" button that is greyed out until something actually differs from default. Every change applies immediately, so the player hears the volume they just set rather than guessing. Everything survives a rejoin.

**Technical:**
- `SettingsConfig.SCHEMA` is the single source of truth: what the menu renders, what the server accepts, and what a save may contain are all derived from it, so the three cannot drift. `ORDER` and `KEYBIND_ORDER` decide what the player sees; a game reorders, hides or relabels rows without touching the client.
- `SettingsData` (pure, no Roblox types) does every conversion: `snap` clamps to `[min, max]`, snaps to `step`, then rounds at 1e-6 to kill float dust. Snapping is skipped at `max`, because a step that does not divide the range would otherwise make the top of a slider unreachable (a volume control that can never reach 1.0).
- Nothing trusts the client. `normalizeValue` refuses to coerce `"true"` or `1` into a toggle, checks enums against their option list, and returns a *reason* (`unknown_setting`, `not_a_number`, `not_a_boolean`, `not_a_string`, `not_an_option`) instead of silently storing garbage. `normalize` repairs a corrupt or older save: unknown keys are dropped, missing keys get defaults, and a keybind that is not a plausible key name falls back to the default.
- The server is the authority for what is *saved*. It re-normalises every incoming table, writes it to `data.settings` on the live profile (so `core/data` auto-saves it, with its session lock, retries and `BindToClose` flush), and replies with the normalised table so the client converges on the server's answer rather than its own guess. A save costs one request per `SAVE_COOLDOWN` (0.5 s) per player, and one `NetworkGuard` endpoint (1/s, burst 4) when that module is installed.
- A slider drag applies locally on every move but only saves on release. Saving per move would trip the rate limit and the limiter could drop the *final* value, leaving the player looking at 0.4 and stored at 0.2.
- The client applies what it can: `SoundGroup` volumes (`Master`, `Music`, `SFX` — create them under `SoundService` and parent your sounds), `UserInputService.MouseDeltaSensitivity`, invert-Y through `UserGameSettings:SetCameraYInvertVisible()` (it takes no argument, so the current state is read first and flipped only on a mismatch), reduce motion through `SpringUI.SetReduceMotion` / `StudMotion.SetReduceMotion` when those modules are installed, and the palette through one `ColorCorrectionEffect` in `Lighting`.
- Key binds are saved and validated here but *applied* by the game's own input code: read `shared.SettingsAPI.GetKeybind("Sprint")`, or subscribe with `onChanged("Keybinds.Sprint", fn)`. Rebinding to a key another action holds is refused with reason `conflict:<action>`; `nil` clears a binding. `Menu` defaults to `F1`, not `Escape`, because `Escape` opens Roblox's core menu, which this window cannot draw over — the player would press it and see nothing happen.
- The palette is a coarse saturation/tint aid, **not** a simulation of the deficiency: Roblox has no stock LUT effect, and shipping a fake simulation would be worse than saying so. A game with real LUTs should replace `PALETTE_ADJUSTMENTS` or drive its own effect from `onChanged("ColorBlindPalette", fn)`.
- `ShowHints` is stored and broadcast, but the Kit ships no hint renderer — the game's own tip UI should read it.
- When `ui/kit` is installed the window registers with `UIManager` (so `UIManager.open("Settings")` works, one panel shows at a time, and the tutorial lock is honoured instead of opening over a tutorial) and a HUD tile is added. Without the kit the window still works; both hooks are optional.

## 2. Depends on

- `systems/core`: remotes (`Remotes.event`), data (`shared.DataServiceAPI` — settings save into the profile).
- Optional: `systems/ui/kit` (`UIManager`, `UIStyle`, `Icons.Settings`) for the HUD tile and panel coordination.
- Optional: `modules/SpringUI` and `modules/StudMotion` for the reduce-motion switch. Without them the toggle still saves; it just has nothing to suppress.
- Optional: `modules/NetworkGuard` for the schema-checked, token-bucket-limited save endpoint. Without it the cooldown still applies and `SettingsData` still refuses anything malformed, with one Studio warning.
- Optional, game-owned: `SoundService` groups named `Master`, `Music`, `SFX`.

## 3. Install

| File | Destination |
| --- | --- |
| `server/SettingsPersist.server.lua` | `src/server/` |
| `client/SettingsMenu.client.lua` | `src/client/` |
| `shared/SettingsConfig.lua` | `src/shared/` |
| `shared/SettingsData.lua` | `src/shared/` |
| `tests/SettingsData.spec.lua` | `tests/` |

**DataTemplate:** add `settings = {}`. Missing keys are filled from the schema on load, so an existing save needs no migration.

**Config (`SettingsConfig.lua`):**

| Key | Default | Meaning |
| --- | --- | --- |
| `SCHEMA` | 8 settings | `key = { kind, min?, max?, step?, options?, default, label }`. `kind` is `number`, `boolean` or `enum`. Add a key here and it appears in the menu, saves, and validates. |
| `ORDER` | all 8 | Render order. Drop a key from this list to hide it without deleting its save data. |
| `KEYBINDS` / `KEYBIND_ORDER` | `Sprint` `LeftShift`, `Interact` `E`, `Jump` `Space`, `Menu` `F1` | Action → key *name* (`"LeftShift"`, not `Enum.KeyCode`). Names, not enums, keep `SettingsConfig` free of Roblox types so the pure module and its spec need no DataModel. |
| `SOUND_GROUPS` | `{ Master, Music, Sfx = "SFX" }` | `SoundGroup`s the volume sliders drive |
| `PALETTE_ADJUSTMENTS` / `PALETTE_EFFECT_NAME` | 3 entries / `"SettingsPalette"` | Saturation and tint per palette option, applied to one `ColorCorrectionEffect` |
| `SAVE_KEY` | `"settings"` | Field on the save |
| `WINDOW_KEY` / `HUD_ORDER` | `"Settings"` / `90` | `UIManager` name and dock tile order |
| `REMOTE_SAVE` / `REMOTE_LOAD` | `"SaveSettings"` / `"SettingsLoaded"` | Remote names |
| `UI_*` | title, 420 px wide, 34 px rows | Menu geometry and wording |

No ids to create.

## 4. Remotes

| Remote | Direction | Payload |
| --- | --- | --- |
| `SaveSettings` | client → server | the settings table itself (`FireServer(settings)`). Re-normalised server-side; unknown keys dropped, bad values replaced by defaults. At most one save per player per 0.5 s. A non-table is ignored, with no reply. |
| `SettingsLoaded` | server → client | the normalised settings table. Sent on join once data has loaded, and as the reply to every accepted save. |

## 5. Verify in Studio (MCP)

1. `start_stop_play`, then `get_console_output`: no errors. Without `SoundService` groups there is one `settings-menu:` warning about missing groups — expected, and the settings still save.
2. **Open:** `execute_luau` (Client) `shared.SettingsAPI.Toggle()`, then check `PlayerGui` has the settings window and `shared.SettingsAPI.IsOpen()` is `true`. Pressing `F1` in Play does the same.
3. **Apply:** in the Client, `shared.SettingsAPI.Set("MasterVolume", 0.4)` — `SoundService.Master.Volume` becomes `0.4` (if the group exists) and `shared.SettingsAPI.Get("MasterVolume")` is `0.4`. `Set("CameraSensitivity", 1.6)` sets `UserInputService.MouseDeltaSensitivity`.
4. **Snapping:** `Set("MasterVolume", 0.17)` stores `0.15`; `Set("MasterVolume", 99)` stores `1`; `Set("CameraSensitivity", 0)` stores `0.2`.
5. **Reduce motion:** `Set("ReduceMotion", true)`, then hover a kit button — no hover spring. `Set("ReduceMotion", false)` restores it. With `ui/kit` windows, open and close timings are unchanged on purpose: they are the load-bearing part of the feel, not decoration.
6. **Persistence:** with API access on, rejoin and read `shared.SettingsAPI.Get("MasterVolume")` in the Client — still `0.4`. On the Server, `DataServiceAPI.GetData(player).settings.MasterVolume` is `0.4`.
7. **Restore:** after changing two settings, `#SettingsData.diffFromDefaults(shared.SettingsAPI.Get())` is `2` and the button is lit. `shared.SettingsAPI.Reset()` brings that count back to `0` and the button greys out.
8. **Key binds:** `shared.SettingsAPI.Set` is not the path — press the row, then a key. Rebinding `Sprint` to `E` is refused (already `Interact`) and the row keeps `LeftShift`. Backspace clears a binding.
9. **Forged input:** from the Client, `Remotes.event("SaveSettings"):FireServer(42)` — ignored, no reply, no error. Then `FireServer({ MasterVolume = "loud", InvertY = "yes", __Nope = 1, Keybinds = { Jump = 42 } })`: the reply is the normalised table (`MasterVolume` 1, `InvertY` false, `__Nope` absent, `Jump` back to `Space`), and the Server-side `DataServiceAPI.GetData(player).settings` matches it. Then fire 10 saves inside 0.5 s: the extras are dropped without error, and one more after the cooldown succeeds.
10. **Specs:** set the `RunTests` attribute on `ReplicatedStorage.Tests` and Play. `SettingsData.spec` passes.

## 6. Origin

New for the Kit (P5-K2), written against `systems/CONVENTIONS.md` with `liveops/codes` as the exemplar. Nothing in the reference game had a settings menu, so there was nothing to extract; instead:

- The reduce-motion switch landed as `SpringUI.ReduceMotion` / `SpringUI.SetReduceMotion` and `StudMotion.REDUCE_MOTION` / `StudMotion.SetReduceMotion`. Both default to **off**, so a game that never installs this system keeps the reference timings byte-for-byte. Suppressed: hover/press springs, shake, float travel, pop-ins, idle spin/sway/pulse. Deliberately *not* suppressed: `StudMotion.Window` open/close and `SelectTab` slides. `SetReduceMotion(true)` also parks every idle object back at its recorded base before clearing the effect tables, so nothing is left spinning at scale 0.
- Key names are strings rather than `Enum.KeyCode`, and `PALETTE_ADJUSTMENTS` are plain numbers, to keep `SettingsConfig`/`SettingsData` free of Roblox types — that is what lets `tests/SettingsData.spec.lua` run without a DataModel.
- Saving writes the live profile table rather than calling a DataStore directly, so this system inherits `core/data`'s session lock, retry and flush behaviour instead of racing it.
