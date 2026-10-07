# Windows quickstart

Four steps. Copy this whole `roblox-bridge` folder to your PC first.

## 1. Install cloudflared (one time)

```powershell
winget install --id Cloudflare.cloudflared
```

Close and reopen your terminal afterwards so it lands on your PATH.
No winget? Skip it — step 2 will tell you to use `ngrok http 8077` instead.

## 2. Start the bridge

```powershell
cd path\to\roblox-bridge
python setup.py --tunnel
```

This generates a token, drops `ArenaBridge.lua` into
`%LOCALAPPDATA%\Roblox\Plugins` with the token already baked in, starts the
server on port 8077, and opens a public tunnel.

If `python` isn't recognised, try `py setup.py --tunnel`.

## 3. Turn on the plugin in Studio

1. **Fully restart Roblox Studio** (it only loads plugins at startup)
2. Open the place you want me to work on
3. **Plugins** tab → click **Arena Bridge**
4. Studio may ask permission to contact `127.0.0.1` — allow it
5. <http://localhost:8077> should now read **Studio connected**

## 4. Send me two lines

Your terminal prints a green box:

```
  Paste these two lines to Arena:
    URL:   https://some-random-words.trycloudflare.com
    TOKEN: 82a46e452573e8776c5c6994
```

Paste those to me and I'll immediately run a full `survey` of the place —
every script, the instance census, remotes, tags, GUI, lighting — work out
what the game is, and tell you what I think before I change anything.

---

### If something's off

| Symptom | Fix |
|---|---|
| No **Arena Bridge** button | Studio wasn't fully restarted. Check `%LOCALAPPDATA%\Roblox\Plugins\ArenaBridge.lua` exists. |
| Dashboard stuck on *Waiting for Studio* | Click the toolbar button — it's a toggle. Check Studio's Output for `[Arena]` lines. |
| `poll failed` in Output | Server isn't running, or the port differs. Re-run `python setup.py --tunnel`. |
| `cloudflared not found` | Install per step 1, or run `ngrok http 8077` in a second terminal and send me that URL. |
| Tunnel URL changed | Free tunnels get a new URL each restart. Send me the new one. |

### When you're done

`Ctrl+C` in the terminal kills the server and the tunnel. The URL dies with it.
`python setup.py --rotate` issues a fresh token if you ever want to invalidate
the old one.

### What this lets me do

Run Luau in your session, create/edit/delete instances and scripts, and read
your place. That is remote code execution into Studio — keep the tunnel up only
while we're working, and prefer a test place for the first run. Every change I
make is wrapped in a single undo step, so `Ctrl+Z` reverses any job.
