--!nonstrict
-- jobs/drift_audit.lua — READ ONLY. Safe to re-run any time.
--
-- Hashes every script in the live NightLoop package (FNV-1a 32; the XOR step
-- uses a nibble table because the repo toolchain binary predates the bitwise
-- operators) and returns {name, hash, bytes} so the agent can diff live vs
-- repo in one shot. Expected: 23 entries — 14 under NightLoop (incl. the
-- Entities folder marker), 7 under NightLoop.Entities, 2 client scripts.
local H = game:GetService("HttpService")
local nl = game:GetService("ServerScriptService"):FindFirstChild("NightLoop")
local spp = game:GetService("StarterPlayer"):FindFirstChild("StarterPlayerScripts")

-- X4[i * 16 + j] = i XOR j, i, j in 0..15 (generated + verified, do not edit)
local X4 = {
	0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,
	1,0,3,2,5,4,7,6,9,8,11,10,13,12,15,14,
	2,3,0,1,6,7,4,5,10,11,8,9,14,15,12,13,
	3,2,1,0,7,6,5,4,11,10,9,8,15,14,13,12,
	4,5,6,7,0,1,2,3,12,13,14,15,8,9,10,11,
	5,4,7,6,1,0,3,2,13,12,15,14,9,8,11,10,
	6,7,4,5,2,3,0,1,14,15,12,13,10,11,8,9,
	7,6,5,4,3,2,1,0,15,14,13,12,11,10,9,8,
	8,9,10,11,12,13,14,15,0,1,2,3,4,5,6,7,
	9,8,11,10,13,12,15,14,1,0,3,2,5,4,7,6,
	10,11,8,9,14,15,12,13,2,3,0,1,6,7,4,5,
	11,10,9,8,15,14,13,12,3,2,1,0,7,6,5,4,
	12,13,14,15,8,9,10,11,4,5,6,7,0,1,2,3,
	13,12,15,14,9,8,11,10,5,4,7,6,1,0,3,2,
	14,15,12,13,10,11,8,9,6,7,4,5,2,3,0,1,
	15,14,13,12,11,10,9,8,7,6,5,4,3,2,1,0
}

local function xor8(a, b)
	local hi = X4[(a // 16) * 16 + (b // 16) + 1]
	local lo = X4[(a % 16) * 16 + (b % 16) + 1]
	return hi * 16 + lo
end

local function fnv(s)
	local h = 2166136261
	for i = 1, #s do
		local lo = h % 256
		h = h - lo + xor8(lo, s:byte(i))
		h = (h * 16777619) % 4294967296
	end
	return h
end

local out = { selfCheckA = fnv("a") == 3826002220, selfCheckHello = fnv("hello") == 1335831723 }
local function hashDir(parent, prefix)
	for _, c in ipairs(parent:GetChildren()) do
		local entry = { name = prefix .. c.Name }
		if c:IsA("LuaSourceContainer") then
			local ok, src = pcall(function()
				return c.Source
			end)
			if ok then
				entry.hash = fnv(src)
				entry.bytes = #src
			else
				entry.error = "source read: " .. tostring(src)
			end
		elseif c:IsA("Folder") then
			entry.folder = true
		end
		table.insert(out, entry)
	end
end

if nl then
	hashDir(nl, "NightLoop.")
	local ents = nl:FindFirstChild("Entities")
	if ents then
		hashDir(ents, "NightLoop.Entities.")
	end
else
	table.insert(out, { name = "NightLoop", missing = true })
end
if spp then
	for _, name in ipairs({ "NightLoopClient", "NightLoopFirstPerson" }) do
		local c = spp:FindFirstChild(name)
		if c and c:IsA("LuaSourceContainer") then
			local ok, src = pcall(function()
				return c.Source
			end)
			if ok then
				table.insert(out, { name = name, hash = fnv(src), bytes = #src })
			end
		else
			table.insert(out, { name = name, missing = true })
		end
	end
end

return H:JSONEncode(out)
