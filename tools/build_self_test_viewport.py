#!/usr/bin/env python3
"""Create the documented peer-free viewport self-test screenshot."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "roblox-bridge"))
from rbxmx_pipeline import parse_rbxmx_to_tree  # noqa: E402
from viewport_renderer import render_scene_png  # noqa: E402


def main() -> int:
    # `--help` used to fall through to the default action and rewrite two *tracked* kit
    # artifacts, so asking a script what it does was a source of kit churn (P3-T7).
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="render and compare against the committed artifact; write nothing "
                         "(exit 1 on drift)")
    args = ap.parse_args()

    model_dir = ROOT / "roblox-kit" / "models"
    world, world_stats = parse_rbxmx_to_tree((model_dir / "spawn_plaza.rbxmx").read_text(encoding="utf-8"), strip_scripts=True)
    hud, hud_stats = parse_rbxmx_to_tree((model_dir / "hud_modern_shell.rbxmx").read_text(encoding="utf-8"), strip_scripts=True)
    png, stats = render_scene_png(
        {"name": "Workspace", "className": "Workspace", "children": world},
        gui_tree=hud,
        width=960,
        height=540,
        camera_preset="perspective",
        explicit_cam_pos=(52, 38, 68),
        explicit_look_at=(0, 5, 0),
        include_hud=True,
        show_labels=True,
        show_grid=True,
    )
    if stats.get("partsRendered", 0) <= 0 or stats.get("facesRasterized", 0) <= 0:
        raise SystemExit(f"viewport self-test produced no rendered geometry: {stats}")
    if stats.get("hudElementsComposited", 0) <= 0:
        raise SystemExit(f"viewport self-test produced no HUD: {stats}")
    out = ROOT / "roblox-kit" / "assets" / "screenshots" / "self-test-viewport.png"
    metadata = {
        "ok": True,
        "captureMode": "deterministic-offline-software-renderer",
        "liveStudioScreenshot": False,
        "peer": "none; intentionally peer-free regression artifact",
        "worldModel": "roblox-kit/models/spawn_plaza.rbxmx",
        "hudModel": "roblox-kit/models/hud_modern_shell.rbxmx",
        "worldItemsParsed": world_stats.get("itemsParsed"),
        "hudItemsParsed": hud_stats.get("itemsParsed"),
        "renderer": stats,
        "image": out.relative_to(ROOT).as_posix(),
    }
    if args.check:
        # Compare decoded pixels, not compressed bytes: zlib output is not guaranteed
        # stable across builds, so a byte comparison would make this platform-sensitive.
        from image_pipeline import decode_png_rgba  # noqa: PLC0415 - sys.path set above

        drift = []
        if not out.is_file():
            drift.append(f"{out.relative_to(ROOT)} is missing")
        else:
            if decode_png_rgba(out.read_bytes()) != decode_png_rgba(png):
                drift.append("pixels differ from a fresh render")
        sidecar = out.with_suffix(".json")
        if not sidecar.is_file():
            drift.append(f"{sidecar.relative_to(ROOT)} is missing")
        else:
            committed = json.loads(sidecar.read_text(encoding="utf-8"))
            for key in tuple(committed.get("renderer", {})):
                if key.endswith(("Ms", "Sec")):
                    committed["renderer"].pop(key)   # wall-clock, never reproducible
            for key in tuple(metadata.get("renderer", {})):
                if key.endswith(("Ms", "Sec")):
                    metadata["renderer"].pop(key)
            if committed != metadata:
                differing = sorted(k for k in set(committed) | set(metadata) if committed.get(k) != metadata.get(k))
                drift.append("metadata differs from a fresh render: "
                             + ", ".join(f"{k}={committed.get(k)!r}->{metadata.get(k)!r}" for k in differing))
        if drift:
            print("\n".join(f"self-test viewport drift: {d}" for d in drift))
            print(f"regenerate with: python3 -B {pathlib.Path(__file__).name}")
            return 1
        print(f"committed self-test viewport matches a fresh render "
              f"({stats['partsRendered']} parts, {stats['facesRasterized']} faces, "
              f"{stats['hudElementsComposited']} HUD elements)")
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    out.with_suffix(".json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Self-test viewport screenshot -> {out.relative_to(ROOT)}")
    print(f"Rendered {stats['partsRendered']} parts, {stats['facesRasterized']} faces, {stats['hudElementsComposited']} HUD elements; offline, not a live Studio capture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
