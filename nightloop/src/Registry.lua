--!nonstrict
--[[
	NightLoop · Registry
	Discovers entity modules, filters by Config, validates dependencies.

	An entity that fails Validate() is dropped with a clear reason rather than
	erroring the night — that is what makes the package safe to drop into a
	place that lacks some of the props.
]]

local Registry = {}
Registry.__index = Registry

function Registry.new(config, entitiesFolder)
	return setmetatable({
		_config = config,
		_folder = entitiesFolder,
		_entities = {},
	}, Registry)
end

function Registry:Load(ctx)
	table.clear(self._entities)

	for _, module in ipairs(self._folder:GetChildren()) do
		if module:IsA("ModuleScript") and string.sub(module.Name, 1, 1) ~= "_" then
			local settings = self._config.Entities[module.Name]

			if not settings then
				warn(("[NightLoop] %s has no Config.Entities entry — skipped")
					:format(module.Name))
			elseif not settings.Enabled then
				-- silent: disabled on purpose
			else
				local ok, entity = pcall(require, module)
				if not ok then
					warn(("[NightLoop] %s failed to load: %s")
						:format(module.Name, tostring(entity)))
				elseif type(entity) ~= "table" or type(entity.Start) ~= "function" then
					warn(("[NightLoop] %s is not a valid entity (needs .Start)")
						:format(module.Name))
				else
					entity.Settings = settings
					entity.FirstPhase = settings.FirstPhase or 1

					local valid, reason = true, nil
					if type(entity.Validate) == "function" then
						valid, reason = entity:Validate(ctx)
					end

					if valid then
						table.insert(self._entities, entity)
					else
						warn(("[NightLoop] %s disabled — %s")
							:format(module.Name, tostring(reason or "validation failed")))
					end
				end
			end
		end
	end

	table.sort(self._entities, function(a, b)
		return a.FirstPhase < b.FirstPhase
	end)

	return self._entities
end

function Registry:Active(phaseIndex)
	local out = {}
	for _, entity in ipairs(self._entities) do
		if phaseIndex >= entity.FirstPhase then
			table.insert(out, entity)
		end
	end
	return out
end

function Registry:All()
	return self._entities
end

return Registry
