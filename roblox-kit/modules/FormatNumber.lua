-- FormatNumber.lua — Compact currency, comma grouping, countdown timer,
-- ordinal rank, and Roman numeral formatters for HUDs, shops, and leaderboards.
local FormatNumber = {}

local SUFFIXES = {
	{ 1e18, "Qi" },
	{ 1e15, "Qa" },
	{ 1e12, "T" },
	{ 1e9, "B" },
	{ 1e6, "M" },
	{ 1e3, "K" },
}

function FormatNumber.compact(n, decimals)
	local num = tonumber(n) or 0
	local sign = num < 0 and "-" or ""
	local absVal = math.abs(num)
	local places = decimals ~= nil and decimals or 2
	for _, entry in ipairs(SUFFIXES) do
		if absVal >= entry[1] then
			local scaled = absVal / entry[1]
			local fmt = string.format("%." .. tostring(places) .. "f", scaled)
			fmt = string.gsub(fmt, "%.?0+$", "")
			return sign .. fmt .. entry[2]
		end
	end
	return sign .. tostring(math.floor(absVal + 0.5))
end

function FormatNumber.commas(n)
	local num = math.floor((tonumber(n) or 0) + 0.5)
	local sign = num < 0 and "-" or ""
	local s = tostring(math.abs(num))
	local rev = string.reverse(s)
	local grouped = string.gsub(rev, "(%d%d%d)", "%1,")
	local out = string.reverse(grouped)
	out = string.gsub(out, "^,", "")
	return sign .. out
end

function FormatNumber.timer(seconds)
	local total = math.max(0, math.floor(tonumber(seconds) or 0))
	local hrs = math.floor(total / 3600)
	local mins = math.floor((total % 3600) / 60)
	local secs = total % 60
	if hrs > 0 then
		return string.format("%02d:%02d:%02d", hrs, mins, secs)
	end
	return string.format("%02d:%02d", mins, secs)
end

function FormatNumber.ordinal(rank)
	local r = math.floor(tonumber(rank) or 0)
	local mod100 = r % 100
	if mod100 >= 11 and mod100 <= 13 then
		return tostring(r) .. "th"
	end
	local mod10 = r % 10
	if mod10 == 1 then
		return tostring(r) .. "st"
	elseif mod10 == 2 then
		return tostring(r) .. "nd"
	elseif mod10 == 3 then
		return tostring(r) .. "rd"
	end
	return tostring(r) .. "th"
end

return FormatNumber
