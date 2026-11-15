--!strict
-- DialogueSystem.lua — Branching NPC & Lore Terminal Dialogue Tree Engine (v6.0.0)
-- Supports conditional choices, state flags, quest/action triggers, and typewriter progression.

export type DialogueChoice = {
	id: string,
	text: string,
	nextNode: string?,
	requireFlag: string?,
	setFlag: string?,
	actionTag: string?,
}

export type DialogueNode = {
	id: string,
	speaker: string,
	portrait: string?,
	emotion: string?,
	text: string,
	soundPreset: string?,
	nextNode: string?,
	choices: { DialogueChoice }?,
}

export type DialogueTree = {
	id: string,
	rootNode: string,
	nodes: { [string]: DialogueNode },
}

local DialogueSystem = {}
DialogueSystem.__index = DialogueSystem

function DialogueSystem.new()
	local self = setmetatable({}, DialogueSystem)
	self._trees = {} :: { [string]: DialogueTree }
	self._flags = {} :: { [string]: boolean }
	self._activeTreeId = nil :: string?
	self._activeNodeId = nil :: string?
	self._actionListeners = {} :: { (string, string, string) -> () }
	return self
end

function DialogueSystem:RegisterTree(tree: DialogueTree)
	assert(type(tree) == "table" and type(tree.id) == "string", "DialogueTree requires an id")
	assert(type(tree.rootNode) == "string" and tree.nodes[tree.rootNode] ~= nil, "DialogueTree requires a valid rootNode")
	self._trees[tree.id] = tree
end

function DialogueSystem:SetFlag(flag: string, value: boolean)
	self._flags[flag] = value == true
end

function DialogueSystem:HasFlag(flag: string): boolean
	return self._flags[flag] == true
end

function DialogueSystem:OnAction(callback: (string, string, string) -> ())
	table.insert(self._actionListeners, callback)
end

function DialogueSystem:StartDialogue(treeId: string): DialogueNode?
	local tree = self._trees[treeId]
	if not tree then
		warn("[DialogueSystem] Unknown tree:", tostring(treeId))
		return nil
	end
	self._activeTreeId = tree.id
	self._activeNodeId = tree.rootNode
	return self:GetCurrentNode()
end

function DialogueSystem:GetCurrentNode(): DialogueNode?
	if not self._activeTreeId or not self._activeNodeId then
		return nil
	end
	local tree = self._trees[self._activeTreeId]
	if not tree then
		return nil
	end
	return tree.nodes[self._activeNodeId]
end

function DialogueSystem:GetAvailableChoices(): { DialogueChoice }
	local node = self:GetCurrentNode()
	if not node or not node.choices then
		return {}
	end
	local out = {}
	for _, choice in ipairs(node.choices) do
		if not choice.requireFlag or self:HasFlag(choice.requireFlag) then
			table.insert(out, choice)
		end
	end
	return out
end

function DialogueSystem:SelectChoice(choiceId: string): DialogueNode?
	local node = self:GetCurrentNode()
	if not node then
		return nil
	end
	if not node.choices or #node.choices == 0 then
		if node.nextNode then
			self._activeNodeId = node.nextNode
			return self:GetCurrentNode()
		end
		self._activeTreeId = nil
		self._activeNodeId = nil
		return nil
	end

	for _, choice in ipairs(self:GetAvailableChoices()) do
		if choice.id == choiceId then
			if choice.setFlag then
				self:SetFlag(choice.setFlag, true)
			end
			if choice.actionTag and self._activeTreeId then
				for _, fn in ipairs(self._actionListeners) do
					task.spawn(fn, self._activeTreeId, node.id, choice.actionTag)
				end
			end
			if choice.nextNode then
				self._activeNodeId = choice.nextNode
				return self:GetCurrentNode()
			end
			self._activeTreeId = nil
			self._activeNodeId = nil
			return nil
		end
	end
	return node
end

return DialogueSystem
