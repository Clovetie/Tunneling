# Third-Party Open Source Licenses & Attribution

This repository bundles curated public-domain (CC0 1.0), MIT, ISC, and Apache-2.0 open-source game UI/icon/particle/audio assets, Luau component kits, drop-in server/client gameplay systems, and agent skills inside `roblox-kit/` and `.claude/skills/`.

---

## Luau compiler & analyzer (`roblox-bridge/tools/`)

- **Repository**: <https://github.com/luau-lang/luau>
- **License**: MIT. Windows x64 release installer pins official release `0.744` and verifies SHA-256 `fde3e22c557aac6afe20a34e8050b7d8c290fa42638c04274a307ed5d0ef0db8` before installing. See `roblox-bridge/tools/install_luau_windows.ps1`.

## 1. Kenney Game UI, Icons, Input Prompts, Particles & Audio (`roblox-kit/assets/`)

- **Author**: Kenney (<https://www.kenney.nl>)
- **Source Mirrors**:
  - <https://github.com/shorepine/kenney>
  - <https://github.com/Calinou/kenney-interface-sounds>
  - <https://github.com/Calinou/kenney-ui-audio>
- **Included Packs**: *UI Pack*, *Fantasy UI Borders*, *UI Pack — Sci-Fi*, *UI Pack — Adventure*, *Mobile Controls*, *Cursor Pack*, *Game Icons*, *Game Icons Expansion*, *Game Icons Fighter Expansion*, *Board Game Icons*, *Input Prompts (PC/Xbox/PlayStation/Switch/Touch)*, *Medals*, *Ranks Pack*, *Minimap Pack*, *Emote Pack*, *Animal Pack Remastered*, *Prototype Textures*, *Light Masks*, *Particle Pack*, *Interface Sounds*, *UI Audio*
- **License**: **Creative Commons Zero v1.0 Universal (CC0 1.0 — Public Domain)** (<https://creativecommons.org/publicdomain/zero/1.0/>)
- **Pinned asset registry**: source commits, preserved README/license evidence, per-file SHA-256 values, thumbnail metadata, and exact-versus-pack-level provenance states are recorded in `roblox-kit/assets/sources.json`, `registry.json`, and `ATTRIBUTION.md`. The art mirror is pinned to `3694c6879e487c108f55677be7dd2ca75b07cc3b`; the sound mirrors are pinned to their recorded commits in `sources.json`.
- **Verification note**: exact upstream Git blob matches are separately labeled from assets with a pack-level CC0 attestation. `python3 -B tools/verify_licences.py` checks the local evidence and hashes; `python3 -B tools/fetch_assets.py --build-registry --verify-upstream` performs the online pinned-tree match check.
- **Original generated content**: procedural assets, 50 8-bit LDR sky panoramas and their six cube faces are released under CC0-1.0 with a local license notice. They are not HDRIs; no NC, ND, SA, unclear, or marketplace assets are included.

---

## 2. Stud UI Clean Roblox (`StudUI.lua`, `StudMotion.lua`, `CheckUIIcons.lua`, `roblox-stud-ui-design`)

- **Repository**: <https://github.com/TemporaryLooker22/Stud-UI-Clean-Roblox>
- **License**: **MIT License**

---

## 3. Lucide Icons, `nrbx-ts/rbxts-lucide` & `lucideblox` (`LucideIcons.lua`, `LucideFilledIcons.lua`)

- **Repositories**:
  - <https://github.com/nrbx-ts/rbxts-lucide> (Copyright (c) 2023 wad4444, **MIT License** — 1,894 outline & 1,895 solid filled `rbxassetid://` assets)
  - <https://github.com/frappedevs/lucideblox> (Copyright (c) 2021 nanako, **MIT License**)
  - <https://lucide.dev> (**ISC License**)

---

## 4. Roblox Hub Drop-In Systems & Skills (`roblox-kit/systems/*`, `.claude/skills/*`)

- **Repository**: <https://github.com/rodrigo-guerraa/roblox-hub> (Copyright (c) 2026 rodrigo-guerraa, **MIT License**)
- **ProfileStore** (`roblox-kit/systems/core/data/server/ProfileStore.lua`): <https://github.com/MadStudioRoblox/ProfileStore> (**Apache License 2.0** — preserved verbatim in `ProfileStore.LICENSE`)
- **Vendored Skills** (`.claude/skills/{roblox-monetization,roblox-security-economy,game-ui-ux,game-feel,audio-design}`): MIT / Apache-2.0 — each skill folder preserves its upstream `LICENSE`, `NOTICE`, and `SOURCE.md` files verbatim.

```text
MIT License

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
```
