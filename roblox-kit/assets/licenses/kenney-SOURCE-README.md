# kenney

The full [Kenney](https://kenney.nl) game-asset library (CC0), organized
so the Tulip asset service can serve any file by a stable path at a
pinned commit.

    2d/     35,998 sprite PNGs, every 2D pack
    ui/      4,686 UI element PNGs
    icons/   7,508 icon PNGs
    3d/      4,812 glTF-binary models across 49 kits, with the textures
             they reference beside them (a model's own relative URI —
             "Textures/colormap.png" — resolves inside its kit folder)
    index.tsv    path, description, WxH, bytes — one row per image
    3d/kits.tsv  slug, title, model count — one row per kit

Built by `tools/build_tree.py` from a Kenney all-in-1 dump. Excluded:
spritesheets/tilemaps/previews/vectors (packagings of sprites already
here individually), files over 2 MB (nothing in the current dump is),
and FBX-only skinned-character kits (the consumer is a static
flat-shading rasterizer).

The `2d/` and `ui/` path scheme is identical to the culled copy this
replaced (surfer's `assets/kenney/lib`) for every file that copy had:
the Tulip index derives sprite ids as path hashes and keys 38k vision
labels by path, so paths are load-bearing. Do not reorganize.

All art is Kenney's, released under Creative Commons Zero (see
LICENSE.txt). Support them at https://kenney.nl/donate.
