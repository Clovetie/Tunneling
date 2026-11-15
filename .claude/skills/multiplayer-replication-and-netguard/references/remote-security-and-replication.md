# `NetworkGuard` Validation & Client-Side VFX Replication Patterns

## 1. Server Remote Handler with `NetworkGuard`
```lua
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local NetworkGuard = require(ReplicatedStorage.Shared.NetworkGuard)

local limiter = NetworkGuard.CreateRateLimiter(4, 1.0) -- max 4 calls/sec per player

local function onPurchaseItem(player: Player, itemId: unknown)
	if not limiter:Allow(player) then
		return
	end
	if not NetworkGuard.IsCleanString(itemId, 48) then
		return
	end
	-- Look up price from server catalog, never from client arguments
end
```

## 2. Client-Side VFX/SFX Broadcast Pattern
Send compact tuples `(presetName, Vector3)` over a `RemoteEvent` or `UnreliableRemoteEvent` and let clients render `VFXManager` `:Emit(n)` bursts locally inside `workspace.CurrentCamera` (`<= 96` studs distance cull).
