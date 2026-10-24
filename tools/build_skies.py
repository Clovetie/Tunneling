#!/usr/bin/env python3
"""Deterministically build original, CC0, 8-bit equirectangular sky panoramas."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "roblox-bridge"))
from image_pipeline import decode_any_to_rgba, encode_png_rgba  # noqa: E402

OUT = ROOT / "roblox-kit" / "assets" / "images" / "arena_skies"
WIDTH, HEIGHT = 512, 256
PALETTES = [
    ((9, 19, 49), (45, 74, 130), (248, 119, 90)),
    ((7, 16, 37), (23, 47, 93), (88, 126, 173)),
    ((36, 101, 185), (112, 183, 222), (240, 212, 158)),
    ((22, 61, 98), (58, 111, 144), (230, 170, 124)),
    ((12, 25, 66), (72, 94, 165), (220, 115, 173)),
    ((56, 112, 182), (162, 202, 228), (239, 228, 199)),
    ((18, 37, 54), (67, 119, 135), (174, 208, 183)),
    ((5, 12, 27), (17, 35, 62), (62, 86, 130)),
    ((39, 66, 122), (138, 130, 178), (246, 166, 128)),
    ((29, 44, 71), (105, 137, 159), (212, 200, 172)),
]


def _mix(a, b, t: float):
    return tuple(max(0, min(255, int(round(x + (y - x) * t)))) for x, y in zip(a, b))


def _value_noise(rng: random.Random, gx: int = 34, gy: int = 16):
    return [[rng.random() for _ in range(gx + 1)] for _ in range(gy + 1)]


def _sample_noise(grid, x: float, y: float) -> float:
    gy, gx = len(grid) - 1, len(grid[0]) - 1
    fx, fy = x * gx, y * gy
    ix, iy = min(gx - 1, int(fx)), min(gy - 1, int(fy))
    tx, ty = fx - ix, fy - iy
    tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)
    top = grid[iy][ix] * (1 - tx) + grid[iy][ix + 1] * tx
    bottom = grid[iy + 1][ix] * (1 - tx) + grid[iy + 1][ix + 1] * tx
    return top * (1 - ty) + bottom * ty


def render_sky(index: int, seed: int) -> bytes:
    rng = random.Random(seed + index * 7919)
    zenith, mid, horizon = PALETTES[(index - 1) % len(PALETTES)]
    noise = _value_noise(rng)
    night = (index % 5 == 0 or index % 7 == 0)
    sun_u, sun_v = rng.uniform(0.16, 0.84), rng.uniform(0.40, 0.68)
    sun_radius = rng.uniform(0.018, 0.042)
    stars = {}
    for _ in range(180 if night else 0):
        sx, sy = int(rng.random() * WIDTH), int(rng.uniform(0.07, 0.43) * HEIGHT)
        brightness = rng.randint(90, 220)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                stars[((sx + dx) % WIDTH, sy + dy)] = (brightness, brightness, min(255, brightness + 22))
    rgba = bytearray(WIDTH * HEIGHT * 4)
    for y in range(HEIGHT):
        v = y / (HEIGHT - 1)
        if v < 0.52:
            base = _mix(zenith, mid, v / 0.52)
        else:
            base = _mix(mid, horizon, (v - 0.52) / 0.48)
        for x in range(WIDTH):
            u = x / WIDTH
            n = _sample_noise(noise, u, v)
            cloud_band = max(0.0, min(1.0, (n - 0.49) * 2.4)) * max(0.0, 1.0 - abs(v - 0.54) * 2.2)
            col = _mix(base, (210, 222, 230) if night else (248, 244, 228), cloud_band * 0.62)
            du = min(abs(u - sun_u), 1.0 - abs(u - sun_u))
            d = math.sqrt((du * 0.9) ** 2 + ((v - sun_v) * 1.5) ** 2)
            if d < sun_radius * 7:
                glow = max(0.0, 1.0 - d / (sun_radius * 7)) * (0.34 if night else 0.47)
                col = _mix(col, (195, 216, 255) if night else (255, 204, 130), glow)
            if d < sun_radius:
                col = (220, 232, 255) if night else (255, 241, 202)
            if night and v < 0.49:
                col = stars.get((x, y), col)
            i = (y * WIDTH + x) * 4
            rgba[i:i + 4] = bytes((*col, 255))
    return encode_png_rgba(WIDTH, HEIGHT, bytes(rgba))


def cube_faces(panorama_png: bytes, size: int = 256) -> dict[str, bytes]:
    pano_width, pano_height, pano = decode_any_to_rgba(panorama_png)
    face_payloads = {}
    for face in ("bk", "dn", "ft", "lf", "rt", "up"):
        out = bytearray(size * size * 4)
        for y in range(size):
            b = 1.0 - 2.0 * (y + 0.5) / size
            for x in range(size):
                a = 2.0 * (x + 0.5) / size - 1.0
                if face == "rt":
                    dx, dy, dz = 1.0, b, -a
                elif face == "lf":
                    dx, dy, dz = -1.0, b, a
                elif face == "up":
                    dx, dy, dz = a, 1.0, -b
                elif face == "dn":
                    dx, dy, dz = a, -1.0, b
                elif face == "ft":
                    dx, dy, dz = a, b, 1.0
                else:
                    dx, dy, dz = -a, b, -1.0
                length = math.sqrt(dx * dx + dy * dy + dz * dz)
                lon = math.atan2(dz / length, dx / length)
                lat = math.asin(max(-1.0, min(1.0, dy / length)))
                u = (lon / (2 * math.pi) + 0.5) % 1.0
                v = max(0.0, min(1.0, 0.5 - lat / math.pi))
                sx = min(pano_width - 1, int(u * pano_width))
                sy = min(pano_height - 1, int(v * pano_height))
                si = (sy * pano_width + sx) * 4
                di = (y * size + x) * 4
                out[di:di + 4] = pano[si:si + 4]
        face_payloads[face] = encode_png_rgba(size, size, bytes(out))
    return face_payloads


def generate(count: int, seed: int) -> pathlib.Path:
    OUT.mkdir(parents=True, exist_ok=True)
    entries = []
    for index in range(1, count + 1):
        name = f"sky_{index:03d}.png"
        target = OUT / name
        payload = render_sky(index, seed)
        target.write_bytes(payload)
        faces = {}
        for face, face_payload in cube_faces(payload).items():
            face_path = OUT / f"sky_{index:03d}_{face}.png"
            face_path.write_bytes(face_payload)
            faces[face] = {
                "path": face_path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(face_payload).hexdigest(),
                "width": 256,
                "height": 256,
            }
        entries.append({
            "id": f"arena-sky-{index:03d}",
            "path": target.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "license": "CC0-1.0",
            "author": "Tunneling contributors",
            "seed": seed + index * 7919,
            "width": WIDTH,
            "height": HEIGHT,
            "projection": "equirectangular-latlong",
            "dynamicRange": "8-bit LDR (not HDRI)",
            "thumbnail": target.relative_to(ROOT).as_posix(),
            "faces": faces,
        })
    catalog = {"schemaVersion": 1, "generator": "tools/build_skies.py", "baseSeed": seed, "projection": "equirectangular-latlong", "dynamicRange": "8-bit LDR; not HDRI", "skies": entries}
    path = OUT / "catalog.json"
    path.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--seed", type=int, default=4042026)
    args = parser.parse_args()
    if args.count < 1 or args.count > 500:
        parser.error("--count must be between 1 and 500")
    catalog = generate(args.count, args.seed)
    print(f"Generated {args.count} deterministic LDR sky panoramas: {catalog.relative_to(ROOT)}")
    print("These are equirectangular skies, not HDRIs; no HDR radiance claims are made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
