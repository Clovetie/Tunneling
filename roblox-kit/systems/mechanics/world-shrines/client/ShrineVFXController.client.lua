--!strict
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local remote = ReplicatedStorage:WaitForChild("ShrineActivatedRemote", 10)
if remote and remote:IsA("RemoteEvent") then
	remote.OnClientEvent:Connect(function(_shrineId: string, targetPart: BasePart?)
		if targetPart and targetPart:IsA("BasePart") then
			for _, desc in ipairs(targetPart:GetDescendants()) do
				if desc:IsA("ParticleEmitter") then
					desc:Emit(28)
				end
			end
		end
	end)
end
