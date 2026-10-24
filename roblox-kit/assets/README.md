# Licensed asset inventory

The asset set is governed by `sources.json` (pinned source/license declarations) and `registry.json` (one row per file with SHA-256, source ID, revision, SPDX license, provenance status, derivation/transformation note, category, and thumbnail metadata). `ATTRIBUTION.md` is generated from the source declarations; upstream license/README snapshots are preserved under `licenses/`.

## Current inventory

- **4,320 registered files** across 226 categories: 3,705 raster images, 50 sky assets, 520 3D models, and 45 audio files.
- The 3D-model count includes **500 Kenney GLB source models** and 20 repository-authored `.rbxmx` examples.
- The 50 generated skies are deterministic **8-bit LDR equirectangular panoramas**, each with six generated cube-face PNGs for skybox construction. They are not HDRIs; the generator deliberately does not make a high-dynamic-range claim. To assign a face to Roblox `Sky`, publish/upload the face and pass its resulting `rbxassetid://` ID to `build_skybox`; the source PNG and its local Studio URI are not themselves a cloud asset ID.
- The 500 imported GLB files have neutral format-placeholder thumbnails, explicitly marked `neutral-format-placeholder`. These are not rendered model previews. The `.rbxmx` examples use offline-rendered previews; WAV files use waveform thumbnails.
- Sources are currently CC0-1.0. Exact upstream Git blob matches are recorded where possible; remaining Kenney-pack assets use the pinned pack-level license declaration and are labeled `pack-license-attestation`, not described as exact content matches.

## Verification and rebuild

From the repository root:

```bash
# Offline: source-evidence hashes, per-file SHA-256, allowlisted SPDX IDs,
# registered-file completeness, and thumbnail existence.
python3 -B tools/verify_licences.py

# CI-equivalent: re-fetch pinned GitHub tree metadata and verify every exact match.
python3 -B tools/verify_licences.py --online

# Online: compare each local asset's Git blob SHA-1 against pinned GitHub trees,
# then refresh the provenance registry, attribution, and browsable contact sheets.
python3 -B tools/fetch_assets.py --build-registry --verify-upstream --attribution --contact-sheets

# Deterministically rebuild the fifty LDR skies.
python3 -B tools/build_skies.py --count 50 --seed 4042026

# Resume a pinned-source fetch (the source archive cache is ignored by Git).
python3 -B tools/fetch_assets.py --fetch --source kenney-art --max-files 3000 --types image --no-rebuild-module
python3 -B tools/fetch_assets.py --fetch --source kenney-art --max-files 500 --types model --no-rebuild-module

# Refresh previews, registry, Studio image index, and contact sheets after fetches.
python3 -B tools/fetch_assets.py --rebuild-thumbnails --build-registry --verify-upstream --attribution --contact-sheets
python3 -B tools/fetch_assets.py --rebuild-module
```

The fetcher verifies every extracted archive member against the pinned Git tree before writing it. A `.part` archive is resumed with HTTP Range requests; the completed archive stays in `.cache/arena-assets/` and is not committed. The CI workflow `.github/workflows/verify-licences.yml` runs the online fail-closed verifier. It fetches only pinned GitHub tree metadata (never the asset archive) and checks every recorded exact-match path/blob against its declared commit.

`roblox-kit/assets/contact-sheets/index.html` links the generated, paginated local contact sheets. `roblox-kit/assets/images/arena_skies/catalog.json` records sky seeds, hashes, projection, dynamic range, cube faces, and license. `roblox-kit/assets/screenshots/self-test-viewport.png` is the deterministic peer-free viewport self-test artifact; its JSON sidecar explicitly says it is not a live Studio capture.

## Studio and mesh limitations

`Assets.lua` points to repository-local `rbxasset://arena_assets/...` images for Studio use. The bridge includes an additive `mesh_capabilities` tool that probes the *connected* Studio runtime by checking MeshPart construction and attempting a temporary EditableMesh creation/cleanup. A version string alone is never treated as evidence of EditableMesh support. No live Studio peer was available for this repository-side verification, so no result is claimed for Studio 0.744 or any other specific Studio build. GLB import/conversion into Roblox instances remains a separate Studio/import workflow; the current thumbnails are clearly labeled placeholders.
