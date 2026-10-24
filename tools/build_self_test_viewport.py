#!/usr/bin/env python3
"""Create the documented peer-free viewport self-test screenshot."""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "roblox-bridge"))
from rbxmx_pipeline import parse_rbxmx_to_tree  # noqa: E402
from viewport_renderer import render_scene_png  # noqa: E402


def main() -> int:
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
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
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
    out.with_suffix(".json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Self-test viewport screenshot -> {out.relative_to(ROOT)}")
    print(f"Rendered {stats['partsRendered']} parts, {stats['facesRasterized']} faces, {stats['hudElementsComposited']} HUD elements; offline, not a live Studio capture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
