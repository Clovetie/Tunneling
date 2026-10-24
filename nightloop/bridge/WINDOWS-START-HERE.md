# Windows Quickstart (v3.0 — MCP + Bridge + HANDS MODE)

Works with **Claude Code, Claude Desktop, Cursor, Codex CLI, VS Code Copilot**, **Arena (via tunnel or HANDS MODE)**, and direct PowerShell CLI commands.

## 1. Install the Plugin & Start the Bridge (One Command)

Open PowerShell in `roblox-bridge` (or `nightloop\bridge`) and run:

```powershell
python setup.py --write-mcp
```
*(Need a public URL for a remote agent? Run `python setup.py --tunnel --write-mcp` instead.)*

What this does automatically:
- Creates `bridge.token` — **or automatically recovers your existing token** from `%LOCALAPPDATA%\Roblox\Plugins\ArenaBridge.lua` if you unzipped a new branch folder, preventing `HTTP 401` mismatches.
- Installs `ArenaBridge.lua` **v3.0** into `%LOCALAPPDATA%\Roblox\Plugins` with the token baked in.
- Writes `.mcp.json` for MCP clients.
- Starts `server.py` v3.0 on `http://127.0.0.1:8077` (Dashboard + REST API + `POST http://127.0.0.1:8077/mcp`).

## 2. Restart Roblox Studio Once

1. **Restart Roblox Studio** once after installing or updating `ArenaBridge.lua` to v3.0.
2. Open your place — **ArenaBridge v3.0 auto-connects on load** (no toolbar click needed).
3. Verify in PowerShell:
   ```powershell
   .\ab.ps1 ping
   ```
   You should see `[ok] ping` with `"pluginVersion": "3.0"` and `"dm": "edit"`.

## 3. Optional: Add to Any MCP Client (`stdio`)

`mcp_server.py` works even without keeping a separate terminal open — if `server.py` is already running it attaches automatically; if not, it starts an embedded background bridge thread inside itself!

```powershell
# Claude Code:
claude mcp add roblox-studio -- python C:\Real\Path\To\roblox-bridge\mcp_server.py --auto-install-plugin

# Codex CLI:
codex mcp add roblox-studio -- python C:\Real\Path\To\roblox-bridge\mcp_server.py --auto-install-plugin
```

For **Read-Only Inspector Mode** (blocks every mutating tool/job): pass `--read-only`.

## 4. High-Speed Package Sync & Drift Checks (No Manual 50 KB Paste Files Needed!)

From any project folder, you can validate, snapshot, and sync a local source directory directly into Roblox Studio in **one single `Ctrl+Z` undo step**:

```powershell
.\ab.ps1 check .\src
.\ab.ps1 drift .\src ServerScriptService.App
.\ab.ps1 sync  .\src ServerScriptService.App
```

### Troubleshooting

| Symptom | Fix |
|---|---|
| `"pluginVersion": "2.0"` in `.\ab.ps1 ping` | Run `python setup.py --install-only` and restart Roblox Studio once to load v3.0. |
| Dashboard stuck on *Waiting for Studio* | Open a place in Studio (or click **Arena MCP** under the **Plugins** tab if auto-connect was toggled off). |
| `poll HTTP 401` in Studio Output | Run `python setup.py --install-only` against the same `bridge.token` as the running server and restart Studio. |
| Stopping when finished | Press `Ctrl+C` in the server terminal; run `python setup.py --rotate` to invalidate old tokens. |
