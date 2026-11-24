--!nonstrict
local SettingsConfig = require(script.Parent:WaitForChild("SettingsConfig"))

local SettingsData = {}

SettingsData.SCHEMA = SettingsConfig.SCHEMA
SettingsData.KEYBINDS = SettingsConfig.KEYBINDS

local PRECISION = 1000000

-- Float dust is what makes a saved 0.15 come back as 0.15000000000000002 and a "changed
-- from default" check fire forever, so every number that leaves this module is rounded.
local function round(value)
	return math.floor(value * PRECISION + 0.5) / PRECISION
end

function SettingsData.isKnown(key)
	return type(key) == "string" and SettingsData.SCHEMA[key] ~= nil
end

function SettingsData.defaults()
	local out = {}
	for key, rule in pairs(SettingsData.SCHEMA) do
		out[key] = rule.default
	end
	out.Keybinds = {}
	for action, name in pairs(SettingsData.KEYBINDS) do
		out.Keybinds[action] = name
	end
	return out
end

function SettingsData.snap(key, value)
	local rule = SettingsData.SCHEMA[key]
	if not rule or rule.kind ~= "number" then
		return nil
	end
	if type(value) ~= "number" or value ~= value or value == math.huge or value == -math.huge then
		return nil
	end
	local min, max = rule.min, rule.max
	local step = rule.step or 0
	local clamped = math.clamp(value, min, max)
	-- Snap only below max: when (max - min) is not a whole number of steps, snapping the
	-- top of the range would land under max and a volume slider could never reach 1.0.
	if step > 0 and clamped < max then
		clamped = math.clamp(min + math.floor((clamped - min) / step + 0.5) * step, min, max)
	end
	return round(clamped)
end

function SettingsData.normalizeValue(key, value)
	local rule = SettingsData.SCHEMA[key]
	if not rule then
		return nil, "unknown_setting"
	end
	if rule.kind == "number" then
		local snapped = SettingsData.snap(key, value)
		if snapped == nil then
			return nil, "not_a_number"
		end
		return snapped, nil
	end
	if rule.kind == "boolean" then
		if type(value) ~= "boolean" then
			return nil, "not_a_boolean"
		end
		return value, nil
	end
	if rule.kind == "enum" then
		if type(value) ~= "string" then
			return nil, "not_a_string"
		end
		for _, option in ipairs(rule.options or {}) do
			if option == value then
				return value, nil
			end
		end
		return nil, "not_an_option"
	end
	return nil, "unsupported_kind"
end

-- A key name is an Enum.KeyCode name. Anything else (a raw string from a client, a
-- 400-character paste) is refused rather than stored in somebody's save.
function SettingsData.isKeyName(value)
	return type(value) == "string" and #value <= 32 and value:match("^%u[%w_]*$") ~= nil
end

-- Turns whatever a save or a client produced into a complete, valid settings table:
-- unknown keys dropped, missing keys defaulted, numbers clamped and snapped.
function SettingsData.normalize(saved)
	local out = SettingsData.defaults()
	if type(saved) ~= "table" then
		return out
	end
	for key, value in pairs(saved) do
		if key == "Keybinds" then
			if type(value) == "table" then
				for action, name in pairs(value) do
					if SettingsData.KEYBINDS[action] ~= nil and SettingsData.isKeyName(name) then
						out.Keybinds[action] = name
					end
				end
			end
		elseif SettingsData.isKnown(key) then
			local normalized = SettingsData.normalizeValue(key, value)
			if normalized ~= nil then
				out[key] = normalized
			end
		end
	end
	return out
end

-- Returns a new table: the caller's is never mutated, so a refused change cannot
-- half-apply to a live profile.
function SettingsData.set(settings, key, value)
	local out = SettingsData.normalize(settings)
	local normalized, reason = SettingsData.normalizeValue(key, value)
	if normalized == nil then
		return out, false, reason
	end
	out[key] = normalized
	return out, true, nil
end

-- One slider notch, for arrow keys and gamepad on a row that is already focused.
function SettingsData.step(key, value, direction)
	local rule = SettingsData.SCHEMA[key]
	if not rule or rule.kind ~= "number" then
		return nil
	end
	local step = rule.step or 0.1
	local current = SettingsData.snap(key, value)
	if current == nil then
		current = rule.default
	end
	return SettingsData.snap(key, current + step * ((direction or 1) < 0 and -1 or 1))
end

function SettingsData.diffFromDefaults(settings)
	local normalized = SettingsData.normalize(settings)
	local defaults = SettingsData.defaults()
	local changed = {}
	for key in pairs(SettingsData.SCHEMA) do
		if normalized[key] ~= defaults[key] then
			table.insert(changed, key)
		end
	end
	for action, name in pairs(SettingsData.KEYBINDS) do
		if normalized.Keybinds[action] ~= name then
			table.insert(changed, "Keybinds." .. action)
		end
	end
	table.sort(changed)
	return changed
end

function SettingsData.isDefault(settings)
	return #SettingsData.diffFromDefaults(settings) == 0
end

function SettingsData.bindKey(settings, action, keyName)
	local out = SettingsData.normalize(settings)
	if type(SettingsData.KEYBINDS[action]) ~= "string" then
		return out, false, "unknown_action"
	end
	if keyName ~= nil and not SettingsData.isKeyName(keyName) then
		return out, false, "bad_key"
	end
	for other, bound in pairs(out.Keybinds) do
		if other ~= action and bound == keyName then
			return out, false, "conflict:" .. tostring(other)
		end
	end
	-- nil clears the binding, which is why the conflict loop above can never match it.
	out.Keybinds[action] = keyName
	return out, true, nil
end

return SettingsData
