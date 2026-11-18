---
name: sfx-and-spatial-audio
description: Sound effects (SFX) libraries, procedural WAV synthesis, pitch jitter, 3D spatial rolloff, and SoundGroup mixing for Roblox Studio.
---

# Roblox SFX Libraries, Procedural Synth & Spatial Audio Skill

Use this skill to source, synthesize, and wire crisp gameplay sound effects (UI clicks, footsteps, weapon impacts, horror stingers, loot fanfare, and ambient emitters).

## 1. Curated Commercial-Safe SFX Libraries & Built-In Synth
- **Built-in 45 CC0 WAV Assets (`roblox-kit/assets/audio/`)**:
  - Browse via `search_assets(assetType="audio")` and import into `SoundService` or a 3D part via `import_audio(fileName="click_001.wav", targetPath="SoundService")`.
- **Built-in Zero-Dependency 16-Bit PCM WAV Synthesizer (`synthesize_sound_effect`)**:
  - Call `synthesize_sound_effect(preset=..., targetPath="SoundService")` for instant custom `.wav` generation across 12 presets (`horror_heartbeat`, `horror_clock_tick`, `horror_whisper_drone`, `horror_jumpscare_stinger`, `horror_breaker_trip`, `flash_camera_strobe`, `ui_click_crisp`, `ui_quest_complete`, `loot_chest_legendary`, `combat_hit_impact`, `combat_shield_break`, `ambient_Geiger_click`).
- **External CC0 SFX Libraries (Direct Links)**:
  - **Kenney Digital / Interface / RPG / Impact Audio Packs (CC0)**: `https://kenney.nl/assets/category:Audio`
  - **Jsfxr / Sfxr Web Procedural Sound Generator (MIT/CC0)**: `https://sfxr.me/`
  - **Sonny / Freesound CC0 Game SFX Filter**: `https://freesound.org/browse/tags/cc0/`
  - **OpenGameArt CC0 Sound Effects**: `https://opengameart.org/art-search-advanced?keys=&field_art_type_tid%5B%5D=13&field_art_licenses_tid%5B%5D=4`

## 2. Anti-Robotic Audio Rules
1. **Always Apply Pitch & Volume Micro-Jitter on Repeated SFX**:
   - Footsteps, UI hovers, coin pickups, and sword swings become fatiguing if played at identical pitch. Randomize `sound.PlaybackSpeed = 0.94 + math.random() * 0.12` before `:Play()`.
2. **Use 3D Spatial RollOff on World Objects**:
   - Parent world sounds inside a 3D `MeshPart` or `Attachment` with `RollOffMode = Enum.RollOffMode.InverseTapered`, `RollOffMinDistance = 8`, and `RollOffMaxDistance = 85`.
3. **Route Through `SoundGroup` Buses (`Master`, `SFX`, `UI`, `Ambience`)**:
   - Attach a `CompressorSoundEffect` on `SFX` so overlapping combat hits never clip, and duck `Ambience` volume during major stingers or cutscenes.

## Verification
- Test synthesized or imported sounds using `synthesize_sound_effect` or `import_audio`, and verify zero broken `SoundId` warnings in `get_console_output`.
