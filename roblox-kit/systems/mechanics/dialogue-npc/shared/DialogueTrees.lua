--!strict
-- DialogueTrees.lua — Shared branching NPC & lore terminal dialogue definitions (v6.0.0)

local DialogueTrees = {
	CaretakerIntro = {
		id = "CaretakerIntro",
		rootNode = "greet",
		nodes = {
			greet = {
				id = "greet",
				speaker = "Old Caretaker",
				emotion = "grim",
				text = "The generator in Sector B is failing. Keep your flashlight charged before midnight.",
				choices = {
					{ id = "ask_fuses", text = "Where are the spare fuses?", nextNode = "fuses_hint", setFlag = "knows_fuses" },
					{ id = "ask_entity", text = "What is hunting us out there?", nextNode = "entity_warning" },
					{ id = "leave", text = "I'm heading in now.", actionTag = "start_patrol" },
				},
			},
			fuses_hint = {
				id = "fuses_hint",
				speaker = "Old Caretaker",
				emotion = "urgent",
				text = "Check the basement breaker station and the safe room locker. Never repair in the dark.",
				nextNode = "greet",
			},
			entity_warning = {
				id = "entity_warning",
				speaker = "Old Caretaker",
				emotion = "whisper",
				text = "If you hear ticking, freeze. If you hear your own footsteps behind you, flash the Mimic immediately.",
				nextNode = "greet",
			},
		},
	},
}

return DialogueTrees
