# SFX Libraries, Layered Sound Design & Spatial Mixing Reference

## External CC0 Audio Packs (Kenney & Open Source)

| Pack Name | License | Contents | URL |
|---|---|---|---|
| **Kenney Interface Sounds** | CC0 1.0 | 100 crisp UI clicks, toggles, back, confirmation, error chimes | `https://kenney.nl/assets/interface-sounds` |
| **Kenney RPG Audio** | CC0 1.0 | 50 footsteps, door creaks, cloth rustles, sword draws, coin jingles | `https://kenney.nl/assets/rpg-audio` |
| **Kenney Impact Sounds** | CC0 1.0 | 130 heavy thuds, metal clangs, glass shatters, body hits | `https://kenney.nl/assets/impact-sounds` |
| **Kenney Sci-Fi Sounds** | CC0 1.0 | Lasers, shields, thrusters, forcefields, alarms | `https://kenney.nl/assets/sci-fi-sounds` |
| **sfxr.me Web Synth** | MIT | Instant browser WAV synthesis exportable to `import_audio` | `https://sfxr.me/` |

## Layered SFX Pattern (Transient + Body + Tail)
Instead of relying on a single flat audio clip for major gameplay moments (boss slam, chest unlock, generator trip), trigger 2–3 layered sounds with slight offsets:
1. **Transient (0ms)**: Crisp high-frequency snap (`PlaybackSpeed = 1.15`, `Volume = 0.6`).
2. **Body (15ms)**: Low-mid punch (`PlaybackSpeed = 0.9`, `Volume = 0.85`).
3. **Tail (40ms)**: Reverb shimmer or sub-bass decay (`Volume = 0.45`).
