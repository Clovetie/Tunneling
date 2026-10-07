--!nonstrict
-- jobs/hash_diagnostic.lua — READ ONLY. One-shot diagnostic.
--
-- Distinguishes the two explanations for the all-hashes-differ pattern:
--  (a) hash arithmetic diverges between Studio and the repo-side Python
--      (e.g. the FNV multiply losing double precision) — mul1mod/mul2mod
--      reveal this: they must equal 84696351 / 4278189677 exactly.
--  (b) the live .Source content differs from the repo files — head/tail
--      byte samples of Config.lua reveal this (repo head64 starts
--      45,45,33,110,111,110,115,116,114,105,99,116,10,45,45,91,91,10,9,
--      78,105,103,104,116,76,111,111,112,32,194,183,32,67,... and ends
--      ...,102,97,108,115,101,10,10,114,101,116,117,114,110,32,67,111,110,
--      102,105,103,10).
local H = game:GetService("HttpService")
local cfg = game:GetService("ServerScriptService").NightLoop.Config
local src = cfg.Source

local head = {}
for i = 1, math.min(64, #src) do
	table.insert(head, src:byte(i))
end
local tail = {}
for i = math.max(1, #src - 31), #src do
	table.insert(tail, src:byte(i))
end

return H:JSONEncode({
	mul1mod = (2166136261 * 16777619) % 4294967296,
	mul2mod = (4294967295 * 16777619) % 4294967296,
	cfgBytes = #src,
	head = head,
	tail = tail,
})
