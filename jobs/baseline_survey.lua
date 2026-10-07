--!nonstrict
-- jobs/baseline_survey.lua — READ ONLY, safe to re-run any time.
--
-- Run (HANDS MODE, on the user's PC, from roblox-bridge):
--   .\ab.ps1 runfile jobs\baseline_survey.lua
-- or from a connected sandbox as a run_luau job with this source.
--
-- Purpose: baseline the live place against the repo (nightloop/src) so the
-- agent knows exactly what is installed before pushing anything.
-- Expected (repo baseline 2026-10-07):
--   kids contains Bootstrap[Script], Config[ModuleScript], Director,
--   Registry, EntityBase, Signal, Net, Beam, Flash, Atmosphere, MonsterRig,
--   MonsterAnimator, CrawlerRig, Entities[Folder]
--   spots = the 8 spot parts; cfg.showPhase = true (flip false when testing
--   ends); cfg.wh.ids = 0 (Whisperer self-disables until the user supplies
--   audio asset IDs); monsterPreview = false (deleted in phase 8);
--   client = ["NightLoopClient", "NightLoopFirstPerson"]
local H = game:GetService("HttpService")
local sss = game:GetService("ServerScriptService")
local ws = workspace

local nl = sss:FindFirstChild("NightLoop")
local kids = {}
if nl then
	for _, c in ipairs(nl:GetChildren()) do
		table.insert(kids, c.Name .. "[" .. c.ClassName .. "]")
	end
end

local spots = {}
local sp = ws:FindFirstChild("Spots")
if sp then
	for _, c in ipairs(sp:GetChildren()) do
		table.insert(spots, c.Name)
	end
end

local cfg = nil
if nl then
	local ok, m = pcall(function()
		return require(nl:FindFirstChild("Config"))
	end)
	if ok and m and m.Entities and m.Hud then
		local wm = m.Entities.WindowMonster or {}
		local wh = m.Entities.Whisperer or {}
		cfg = {
			showPhase = m.Hud.ShowPhase,
			night = m.NightDuration,
			wm = { on = wm.Enabled, turn = wm.TurnSpeed, ground = wm.RequireGround },
			wh = { on = wh.Enabled, ids = wh.WhisperSoundIds and #wh.WhisperSoundIds or 0 },
			crawl = (m.Entities.Crawl or {}).Enabled,
			knocker = (m.Entities.Knocker or {}).Enabled,
			breathless = (m.Entities.Breathless or {}).Enabled,
			ticking = (m.Entities.TickingMan or {}).Enabled,
		}
	end
end

local spp = game:GetService("StarterPlayer"):FindFirstChild("StarterPlayerScripts")
local cl = {}
if spp then
	for _, c in ipairs(spp:GetChildren()) do
		if c.Name:find("NightLoop") then
			table.insert(cl, c.Name)
		end
	end
end

return H:JSONEncode({
	place = game.Name,
	kids = kids,
	spots = spots,
	cfg = cfg,
	monsterPreview = ws:FindFirstChild("MonsterPreview") ~= nil,
	client = cl,
})
