-- Capture every animator pose as raw part geometry.
-- Clones the package first: require() caches per ModuleScript INSTANCE, so
-- editing .Source in place does not invalidate it. Cloning makes new
-- instances, which forces a fresh require of the edited source.
local HttpService = game:GetService("HttpService")

local original = game:GetService("ServerScriptService").NightLoop
local sandbox = Instance.new("Folder")
sandbox.Name = "__poseSandbox"
sandbox.Parent = game:GetService("ServerStorage")

local pkg = original:Clone()
pkg.Parent = sandbox

local MonsterRig = require(pkg.MonsterRig)
local MonsterAnimator = require(pkg.MonsterAnimator)

local rig = MonsterRig.build({ name = "__poseCapture" })
rig.Parent = workspace
rig:PivotTo(CFrame.new(0, 2000, 0))
local anim = MonsterAnimator.new(rig)

local order = { "idle", "watch", "twitch", "recoil", "lunge", "retreat" }
local out = { poses = {}, order = order }
local root = rig.PrimaryPart

for _, name in ipairs(order) do
	anim:Play(name, true)
	for _ = 1, 8 do
		anim:Update(1 / 60, Vector3.new(6, 2006, -10))
	end
	local parts = {}
	for _, d in ipairs(rig:GetDescendants()) do
		if d:IsA("BasePart") and d.Name ~= "Root" then
			local rel = root.CFrame:ToObjectSpace(d.CFrame)
			local _, _, _, r00, r01, r02, r10, r11, r12, r20, r21, r22 = rel:GetComponents()
			table.insert(parts, {
				n = d.Name,
				p = { rel.Position.X, rel.Position.Y, rel.Position.Z },
				r = { r00, r01, r02, r10, r11, r12, r20, r21, r22 },
				s = { d.Size.X, d.Size.Y, d.Size.Z },
				c = { math.floor(d.Color.R*255), math.floor(d.Color.G*255), math.floor(d.Color.B*255) },
				neon = d.Material == Enum.Material.Neon,
			})
		end
	end
	out.poses[name] = parts
end

anim:Destroy()
rig:Destroy()
sandbox:Destroy()
return HttpService:JSONEncode(out)
