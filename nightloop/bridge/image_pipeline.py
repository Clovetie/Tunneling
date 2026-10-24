#!/usr/bin/env python3
"""
image_pipeline.py — Zero-dependency Image Import, Procedural Asset Generator,
Local `rbxasset://` Studio Content Installer, `EditableImage` RGBA Encoder,
and Roblox Open Cloud Assets v1 Publisher.

Works on bare Python 3.9+ standard library (`zlib`, `struct`, `math`, `urllib`)
with zero pip installs required, automatically accelerating via Pillow (`PIL`),
macOS `sips`, or Windows `.NET System.Drawing` when decoding non-PNG/BMP formats.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import tempfile
import time
from typing import Dict, List, Optional, Tuple
import urllib.error
import urllib.request
import zlib


HERE = Path(__file__).resolve().parent


def find_kit_root() -> Path:
    """Locate `<repo>/roblox-kit` regardless of whether running from
    `roblox-bridge/` or `nightloop/bridge/`.
    """
    for cand in (HERE.parent / "roblox-kit", HERE.parent.parent / "roblox-kit"):
        if cand.exists():
            return cand
    default = HERE.parent / "roblox-kit"
    default.mkdir(parents=True, exist_ok=True)
    return default


# ============================================================================
# 1. Pure-Python PNG & BMP Codec + Bilinear Resizer
# ============================================================================

PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def encode_png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    """Encode raw 8-bit RGBA bytes (`width * height * 4`) into a valid PNG."""
    expected = width * height * 4
    if len(rgba) != expected:
        raise ValueError(f"RGBA byte length mismatch: got {len(rgba)}, expected {expected}")
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    stride = width * 4
    raw_rows = bytearray((stride + 1) * height)
    pos = 0
    for y in range(height):
        raw_rows[pos] = 0  # Filter type 0 (None)
        pos += 1
        row_start = y * stride
        raw_rows[pos : pos + stride] = rgba[row_start : row_start + stride]
        pos += stride
    compressed = zlib.compress(bytes(raw_rows), level=6)
    return PNG_SIG + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", compressed) + _png_chunk(b"IEND", b"")


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_rgba(data: bytes) -> Tuple[int, int, bytes]:
    """Pure-Python PNG decoder supporting RGB, RGBA, Grayscale, Grayscale+Alpha,
    and 8-bit Palette (+ tRNS alpha) with all 5 standard scanline filter modes.
    """
    if data[:8] != PNG_SIG:
        raise ValueError("Not a valid PNG signature")
    offset = 8
    width = height = bit_depth = color_type = interlace = 0
    palette: List[Tuple[int, int, int]] = []
    trns = b""
    idat_parts: List[bytes] = []

    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        ctype = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif ctype == b"PLTE":
            palette = [(chunk[i], chunk[i + 1], chunk[i + 2]) for i in range(0, len(chunk), 3)]
        elif ctype == b"tRNS":
            trns = chunk
        elif ctype == b"IDAT":
            idat_parts.append(chunk)
        elif ctype == b"IEND":
            break

    if interlace != 0 or bit_depth != 8:
        raise ValueError(f"Unsupported PNG bit_depth={bit_depth} interlace={interlace} for pure decoder")

    channels_map = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
    if color_type not in channels_map:
        raise ValueError(f"Unsupported PNG color_type={color_type}")

    bpp = channels_map[color_type]
    stride = width * bpp
    raw = zlib.decompress(b"".join(idat_parts))

    prev_row = bytearray(stride)
    cur_row = bytearray(stride)
    out = bytearray(width * height * 4)
    in_pos = 0
    out_pos = 0

    for _y in range(height):
        ftype = raw[in_pos]
        in_pos += 1
        scan = raw[in_pos : in_pos + stride]
        in_pos += stride

        if ftype == 0:
            cur_row[:] = scan
        elif ftype == 1:
            for i in range(stride):
                left = cur_row[i - bpp] if i >= bpp else 0
                cur_row[i] = (scan[i] + left) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                cur_row[i] = (scan[i] + prev_row[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = cur_row[i - bpp] if i >= bpp else 0
                cur_row[i] = (scan[i] + ((left + prev_row[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = cur_row[i - bpp] if i >= bpp else 0
                up = prev_row[i]
                up_left = prev_row[i - bpp] if i >= bpp else 0
                cur_row[i] = (scan[i] + _paeth(left, up, up_left)) & 0xFF
        else:
            raise ValueError(f"Invalid PNG scanline filter {ftype}")

        if color_type == 6:
            out[out_pos : out_pos + stride] = cur_row
            out_pos += stride
        elif color_type == 2:
            for x in range(0, stride, 3):
                out[out_pos] = cur_row[x]
                out[out_pos + 1] = cur_row[x + 1]
                out[out_pos + 2] = cur_row[x + 2]
                out[out_pos + 3] = 255
                out_pos += 4
        elif color_type == 0:
            for x in range(width):
                g = cur_row[x]
                out[out_pos] = g
                out[out_pos + 1] = g
                out[out_pos + 2] = g
                out[out_pos + 3] = 255
                out_pos += 4
        elif color_type == 4:
            for x in range(0, stride, 2):
                g = cur_row[x]
                a = cur_row[x + 1]
                out[out_pos] = g
                out[out_pos + 1] = g
                out[out_pos + 2] = g
                out[out_pos + 3] = a
                out_pos += 4
        elif color_type == 3:
            for x in range(width):
                idx = cur_row[x]
                r, g, b = palette[idx] if idx < len(palette) else (0, 0, 0)
                a = trns[idx] if idx < len(trns) else 255
                out[out_pos] = r
                out[out_pos + 1] = g
                out[out_pos + 2] = b
                out[out_pos + 3] = a
                out_pos += 4

        prev_row[:] = cur_row

    return width, height, bytes(out)


def decode_bmp_rgba(data: bytes) -> Tuple[int, int, bytes]:
    """Decode uncompressed 24-bit or 32-bit BMP bytes into RGBA."""
    if data[:2] != b"BM":
        raise ValueError("Not a BMP file")
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bpp = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if compression != 0 or bpp not in (24, 32):
        raise ValueError(f"Unsupported BMP bpp={bpp} compression={compression}")
    top_down = height < 0
    abs_h = abs(height)
    bytes_pp = bpp // 8
    row_padded = ((width * bytes_pp + 3) // 4) * 4
    out = bytearray(width * abs_h * 4)
    for row_idx in range(abs_h):
        src_row = row_idx if top_down else (abs_h - 1 - row_idx)
        base = pixel_offset + src_row * row_padded
        dst_base = row_idx * width * 4
        for x in range(width):
            p = base + x * bytes_pp
            b_val = data[p]
            g_val = data[p + 1]
            r_val = data[p + 2]
            a_val = data[p + 3] if bytes_pp == 4 else 255
            d = dst_base + x * 4
            out[d] = r_val
            out[d + 1] = g_val
            out[d + 2] = b_val
            out[d + 3] = a_val
    return width, abs_h, bytes(out)


def decode_any_to_rgba(data: bytes, filename_hint: str = "image.png") -> Tuple[int, int, bytes]:
    """Decode PNG, BMP, JPEG, WebP, GIF into `(width, height, rgba_bytes)`.
    Uses Pillow if installed; otherwise pure-Python PNG/BMP decoders with
    automatic OS converter fallback (`System.Drawing` on Windows, `sips` on macOS).
    """
    try:
        from PIL import Image  # type: ignore
        import io
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        w, h = im.size
        return w, h, im.tobytes()
    except Exception:
        pass

    if data[:8] == PNG_SIG:
        try:
            return decode_png_rgba(data)
        except Exception:
            pass

    if data[:2] == b"BM":
        return decode_bmp_rgba(data)

    # OS helper conversion to 8-bit non-interlaced PNG -> pure-Python decode
    suffix = Path(filename_hint).suffix or ".img"
    with tempfile.TemporaryDirectory() as tmp:
        in_file = Path(tmp) / f"in{suffix}"
        out_png = Path(tmp) / "out.png"
        in_file.write_bytes(data)
        sys_name = platform.system()
        if sys_name == "Windows":
            ps_cmd = (
                "Add-Type -AssemblyName System.Drawing; "
                f"$img = [System.Drawing.Image]::FromFile('{in_file}'); "
                f"$img.Save('{out_png}', [System.Drawing.Imaging.ImageFormat]::Png); "
                "$img.Dispose()"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], check=True, timeout=15)
        elif sys_name == "Darwin":
            subprocess.run(["sips", "-s", "format", "png", str(in_file), "--out", str(out_png)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15)
        elif shutil.which("ffmpeg"):
            subprocess.run(["ffmpeg", "-y", "-i", str(in_file), str(out_png)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15)
        if out_png.is_file():
            return decode_png_rgba(out_png.read_bytes())

    raise RuntimeError(
        "Could not decode image pixels for EditableImage (provide PNG/BMP or install Pillow for JPEG/WebP on headless Linux)."
    )


def resize_rgba(width: int, height: int, rgba: bytes, target_w: int, target_h: int) -> bytes:
    """Clean bilinear RGBA resize for downscaling large textures cleanly."""
    if width == target_w and height == target_h:
        return rgba
    out = bytearray(target_w * target_h * 4)
    x_ratio = (width - 1) / max(target_w - 1, 1)
    y_ratio = (height - 1) / max(target_h - 1, 1)

    for ty in range(target_h):
        sy = ty * y_ratio
        y0 = int(sy)
        y1 = min(y0 + 1, height - 1)
        wy = sy - y0
        wy0 = 1.0 - wy
        for tx in range(target_w):
            sx = tx * x_ratio
            x0 = int(sx)
            x1 = min(x0 + 1, width - 1)
            wx = sx - x0
            wx0 = 1.0 - wx

            i00 = (y0 * width + x0) * 4
            i10 = (y0 * width + x1) * 4
            i01 = (y1 * width + x0) * 4
            i11 = (y1 * width + x1) * 4

            o = (ty * target_w + tx) * 4
            for c in range(4):
                top = rgba[i00 + c] * wx0 + rgba[i10 + c] * wx
                bot = rgba[i01 + c] * wx0 + rgba[i11 + c] * wx
                out[o + c] = max(0, min(255, int(top * wy0 + bot * wy + 0.5)))
    return bytes(out)


# ============================================================================
# 2. Procedural Vector / Shader Studio Asset Generator (26 Presets)
# ============================================================================

def _parse_hex_color(s: str, default: Tuple[int, int, int] = (255, 196, 48)) -> Tuple[int, int, int]:
    if not s:
        return default
    h = s.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) == 6:
        try:
            return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        except ValueError:
            pass
    return default


def _clamp01(v: float) -> float:
    return 0.0 if v <= 0.0 else (1.0 if v >= 1.0 else v)


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = _clamp01((x - e0) / (e1 - e0 if e1 != e0 else 1e-6))
    return t * t * (3.0 - 2.0 * t)


def _sd_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    pax, pay = px - ax, py - ay
    bax, bay = bx - ax, by - ay
    denom = bax * bax + bay * bay
    h = _clamp01((pax * bax + pay * bay) / denom) if denom > 1e-8 else 0.0
    dx, dy = pax - bax * h, pay - bay * h
    return math.hypot(dx, dy)


def _hash_noise(ix: int, iy: int) -> float:
    n = (ix * 374761393 + iy * 668265263) & 0xFFFFFFFF
    n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((n ^ (n >> 16)) & 0xFFFF) / 65535.0


def _value_noise(x: float, y: float) -> float:
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    ux = fx * fx * (3.0 - 2.0 * fx)
    uy = fy * fy * (3.0 - 2.0 * fy)
    a = _hash_noise(ix, iy)
    b = _hash_noise(ix + 1, iy)
    c = _hash_noise(ix, iy + 1)
    d = _hash_noise(ix + 1, iy + 1)
    return (a * (1 - ux) + b * ux) * (1 - uy) + (c * (1 - ux) + d * ux) * uy


def _fbm(x: float, y: float, octaves: int = 4) -> float:
    val = 0.0
    amp = 0.5
    for _ in range(octaves):
        val += amp * _value_noise(x, y)
        x *= 2.0
        y *= 2.0
        amp *= 0.5
    return val


PROCEDURAL_PRESETS: Dict[str, Tuple[str, str, int]] = {
    # UI Icons (crisp transparent PNGs)
    "icon_coin": ("#FFD43B", "#F08C00", 64),
    "icon_gem": ("#4DABF7", "#1971C2", 64),
    "icon_shield": ("#74C0FC", "#1864AB", 64),
    "icon_sword": ("#DEE2E6", "#F59F00", 64),
    "icon_heart": ("#FF6B6B", "#C92A2A", 64),
    "icon_star": ("#FFE066", "#F59F00", 64),
    "icon_bolt": ("#FFE066", "#FF922B", 64),
    "icon_lock": ("#CED4DA", "#495057", 64),
    "icon_gear": ("#ADB5BD", "#495057", 64),
    "icon_crown": ("#FFD43B", "#E67700", 64),
    "icon_check": ("#51CF66", "#2B8A3E", 64),
    "icon_close": ("#FF6B6B", "#C92A2A", 64),
    "panel_glass_9slice": ("#1E293B", "#94A3B8", 64),
    "vignette_soft": ("#000000", "#000000", 128),
    "crosshair_dot": ("#FFFFFF", "#38BDF8", 64),
    "radial_ring": ("#38BDF8", "#0284C7", 64),
    # VFX Sprites
    "particle_soft_glow": ("#FFFFFF", "#93C5FD", 64),
    "particle_spark_star": ("#FFFBEB", "#FBBF24", 64),
    "particle_smoke_puff": ("#E5E7EB", "#9CA3AF", 64),
    "particle_shockwave_ring": ("#E0F2FE", "#38BDF8", 64),
    "beam_energy_streak": ("#FFFFFF", "#60A5FA", 64),
    # World Surface Textures (opaque seamless tiles)
    "grid_dark_1m": ("#1E242E", "#4C6EF5", 128),
    "grid_accent_orange": ("#2B2118", "#FF922B", 128),
    "hazard_stripes": ("#F59F00", "#212529", 128),
    "brushed_metal_noise": ("#868E96", "#495057", 128),
    "scifi_panel_tile": ("#243040", "#3BC9DB", 128),
}


def generate_procedural_rgba(
    preset: str,
    width: int = 0,
    height: int = 0,
    primary_hex: Optional[str] = None,
    secondary_hex: Optional[str] = None,
) -> Tuple[int, int, bytes]:
    """Synthesize an anti-aliased RGBA image for any built-in Studio preset."""
    def_p, def_s, def_sz = PROCEDURAL_PRESETS.get(preset, ("#58A6FF", "#1F6FEB", 64))
    w = max(8, min(width or def_sz, 512))
    h = max(8, min(height or def_sz, 512))
    c1 = _parse_hex_color(primary_hex or def_p)
    c2 = _parse_hex_color(secondary_hex or def_s)
    aa = 2.2 / max(w, h)

    buf = bytearray(w * h * 4)
    idx = 0

    for py in range(h):
        ny = (py + 0.5) / h
        cy = (ny - 0.5) * 2.0
        for px in range(w):
            nx = (px + 0.5) / w
            cx = (nx - 0.5) * 2.0
            dist = math.hypot(cx, cy)
            ang = math.atan2(cy, cx)
            r = g = b = 0.0
            a = 0.0

            if preset == "icon_coin":
                outer = _smoothstep(0.86 + aa, 0.86 - aa, dist)
                inner = _smoothstep(0.66 + aa, 0.66 - aa, dist)
                slot = 1.0 if (abs(cx) < 0.11 and abs(cy) < 0.34) else 0.0
                shade = _clamp01(0.5 - cx * 0.35 - cy * 0.45)
                mix = shade * (1.0 - inner * 0.28)
                r = c2[0] + (c1[0] - c2[0]) * mix + slot * 45
                g = c2[1] + (c1[1] - c2[1]) * mix + slot * 40
                b = c2[2] + (c1[2] - c2[2]) * mix + slot * 25
                a = outer * 255.0

            elif preset == "icon_gem":
                # Faceted diamond polygon |cx|*1.25 + |cy| <= 0.92
                d_poly = abs(cx) * 1.2 + abs(cy) * 0.95
                mask = _smoothstep(0.90 + aa, 0.90 - aa, d_poly)
                facet = 0.85 if (cx + cy < 0) else (0.55 if cx < 0 else 0.3)
                spec = 1.0 if (abs(cx + 0.22) + abs(cy + 0.22) < 0.24) else 0.0
                r = c2[0] + (c1[0] - c2[0]) * facet + spec * 80
                g = c2[1] + (c1[1] - c2[1]) * facet + spec * 80
                b = c2[2] + (c1[2] - c2[2]) * facet + spec * 80
                a = mask * 255.0

            elif preset == "icon_shield":
                w_curve = max(0.0, 0.72 - max(0.0, cy + 0.1) * 0.62)
                inside = (abs(cx) <= w_curve) and (cy >= -0.78) and (cy <= 0.86)
                edge_d = min(w_curve - abs(cx), cy + 0.78, 0.86 - cy)
                mask = _smoothstep(-aa, aa, edge_d) if inside or edge_d > -aa else 0.0
                trim = 1.0 if edge_d < 0.10 else 0.0
                t = 0.75 if cx < 0 else 0.38
                r = c2[0] + (c1[0] - c2[0]) * max(t, trim)
                g = c2[1] + (c1[1] - c2[1]) * max(t, trim)
                b = c2[2] + (c1[2] - c2[2]) * max(t, trim)
                a = mask * 255.0

            elif preset == "icon_sword":
                d_blade = _sd_segment(cx, cy, -0.35, 0.35, 0.68, -0.68)
                d_guard = _sd_segment(cx, cy, -0.52, 0.08, -0.08, 0.52)
                d_grip = _sd_segment(cx, cy, -0.68, 0.68, -0.32, 0.32)
                m_blade = _smoothstep(0.11 + aa, 0.11 - aa, d_blade)
                m_guard = _smoothstep(0.085 + aa, 0.085 - aa, d_guard)
                m_grip = _smoothstep(0.075 + aa, 0.075 - aa, d_grip)
                if m_guard > m_blade or m_grip > m_blade:
                    r, g, b = c2
                else:
                    r, g, b = c1
                a = max(m_blade, m_guard, m_grip) * 255.0

            elif preset == "icon_heart":
                hx, hy = cx * 1.15, -cy * 1.15 + 0.15
                eq = (hx * hx + hy * hy - 0.52) ** 3 - hx * hx * (hy ** 3)
                mask = _smoothstep(0.02, -0.02, eq)
                t = _clamp01(0.75 - cx * 0.25 - cy * 0.35)
                r = c2[0] + (c1[0] - c2[0]) * t
                g = c2[1] + (c1[1] - c2[1]) * t
                b = c2[2] + (c1[2] - c2[2]) * t
                a = mask * 255.0

            elif preset == "icon_star":
                seg = math.pi / 5.0
                a_mod = ((ang + math.pi / 2.0) % (2.0 * seg)) - seg
                rad = 0.84 * math.cos(seg) / max(math.cos(a_mod * 0.68), 0.25) * 0.62
                star_r = 0.42 + 0.42 * math.cos(5.0 * (ang + math.pi / 2.0))
                mask = _smoothstep(star_r + aa, star_r - aa, dist)
                t = _clamp01(1.0 - dist * 0.85)
                r = c2[0] + (c1[0] - c2[0]) * t
                g = c2[1] + (c1[1] - c2[1]) * t
                b = c2[2] + (c1[2] - c2[2]) * t
                a = mask * 255.0 * (1.0 if rad else 1.0)

            elif preset == "icon_bolt":
                d1 = _sd_segment(cx, cy, 0.18, -0.78, -0.26, 0.04)
                d2 = _sd_segment(cx, cy, -0.26, 0.04, 0.22, -0.04)
                d3 = _sd_segment(cx, cy, 0.22, -0.04, -0.18, 0.78)
                d_min = min(d1, d2, d3)
                mask = _smoothstep(0.13 + aa, 0.13 - aa, d_min)
                t = _clamp01(1.0 - d_min * 4.5)
                r = c2[0] + (c1[0] - c2[0]) * t
                g = c2[1] + (c1[1] - c2[1]) * t
                b = c2[2] + (c1[2] - c2[2]) * t
                a = mask * 255.0

            elif preset == "icon_lock":
                body = (abs(cx) < 0.56) and (-0.05 < cy < 0.78)
                shackle_d = abs(math.hypot(cx, cy + 0.12) - 0.36)
                shackle = (cy <= -0.02) and (shackle_d < 0.10)
                hole = math.hypot(cx, cy + 0.32 - 0.62) < 0.11
                mask = 1.0 if ((body or shackle) and not hole) else 0.0
                t = 0.8 if shackle else 0.45
                r = c2[0] + (c1[0] - c2[0]) * t
                g = c2[1] + (c1[1] - c2[1]) * t
                b = c2[2] + (c1[2] - c2[2]) * t
                a = mask * 255.0

            elif preset == "icon_gear":
                tooth = 0.72 + 0.13 * math.cos(8.0 * ang)
                outer = _smoothstep(tooth + aa, tooth - aa, dist)
                hole = _smoothstep(0.30 - aa, 0.30 + aa, dist)
                mask = outer * hole
                t = _clamp01(0.8 - cy * 0.35)
                r = c2[0] + (c1[0] - c2[0]) * t
                g = c2[1] + (c1[1] - c2[1]) * t
                b = c2[2] + (c1[2] - c2[2]) * t
                a = mask * 255.0

            elif preset == "icon_crown":
                top_y = -0.15 - 0.42 * abs(math.cos(cx * math.pi * 1.4))
                inside = (abs(cx) < 0.76) and (cy > top_y) and (cy < 0.58)
                mask = 1.0 if inside else 0.0
                t = _clamp01(0.85 - (cy - top_y) * 0.6)
                r = c2[0] + (c1[0] - c2[0]) * t
                g = c2[1] + (c1[1] - c2[1]) * t
                b = c2[2] + (c1[2] - c2[2]) * t
                a = mask * 255.0

            elif preset == "icon_check":
                d = min(_sd_segment(cx, cy, -0.58, 0.05, -0.15, 0.48), _sd_segment(cx, cy, -0.15, 0.48, 0.64, -0.46))
                mask = _smoothstep(0.14 + aa, 0.14 - aa, d)
                r, g, b = c1
                a = mask * 255.0

            elif preset == "icon_close":
                d = min(_sd_segment(cx, cy, -0.55, -0.55, 0.55, 0.55), _sd_segment(cx, cy, -0.55, 0.55, 0.55, -0.55))
                mask = _smoothstep(0.14 + aa, 0.14 - aa, d)
                r, g, b = c1
                a = mask * 255.0

            elif preset == "panel_glass_9slice":
                # Rounded 9-slice panel with crisp border stroke
                rx = max(abs(cx) - 0.68, 0.0)
                ry = max(abs(cy) - 0.68, 0.0)
                rr = math.hypot(rx, ry)
                outer = _smoothstep(0.24 + aa, 0.24 - aa, rr)
                border = _smoothstep(0.17, 0.22, rr)
                r = c1[0] + (c2[0] - c1[0]) * border
                g = c1[1] + (c2[1] - c1[1]) * border
                b = c1[2] + (c2[2] - c1[2]) * border
                a = outer * (215.0 + border * 40.0)

            elif preset == "vignette_soft":
                r, g, b = c1
                a = (_clamp01((dist - 0.42) / 0.85) ** 1.8) * 235.0

            elif preset == "crosshair_dot":
                center = _smoothstep(0.11 + aa, 0.11 - aa, dist)
                ring = _smoothstep(0.08 + aa, 0.08 - aa, abs(dist - 0.56))
                ticks = 1.0 if ((abs(cx) < 0.05 or abs(cy) < 0.05) and 0.30 < dist < 0.78) else 0.0
                m = max(center, ring * 0.85, ticks)
                r, g, b = c1 if center > 0.5 else c2
                a = m * 255.0

            elif preset == "radial_ring":
                ring = _smoothstep(0.11 + aa, 0.11 - aa, abs(dist - 0.72))
                t = (ang + math.pi) / (2.0 * math.pi)
                r = c2[0] + (c1[0] - c2[0]) * t
                g = c2[1] + (c1[1] - c2[1]) * t
                b = c2[2] + (c1[2] - c2[2]) * t
                a = ring * 255.0

            elif preset == "particle_soft_glow":
                falloff = (_clamp01(1.0 - dist)) ** 2.4
                r = c2[0] + (c1[0] - c2[0]) * falloff
                g = c2[1] + (c1[1] - c2[1]) * falloff
                b = c2[2] + (c1[2] - c2[2]) * falloff
                a = falloff * 255.0

            elif preset == "particle_spark_star":
                cross = max(0.0, 1.0 - abs(cx) * 6.5) * max(0.0, 1.0 - abs(cy) * 1.1) + \
                        max(0.0, 1.0 - abs(cy) * 6.5) * max(0.0, 1.0 - abs(cx) * 1.1)
                core = (_clamp01(1.0 - dist * 1.6)) ** 2.0
                val = _clamp01(max(cross, core))
                r = c2[0] + (c1[0] - c2[0]) * core
                g = c2[1] + (c1[1] - c2[1]) * core
                b = c2[2] + (c1[2] - c2[2]) * core
                a = val * 255.0

            elif preset == "particle_smoke_puff":
                n = _fbm(nx * 5.0 + 3.1, ny * 5.0 + 7.4)
                falloff = _clamp01(1.0 - dist * 1.08) ** 1.5
                val = _clamp01(falloff * (0.45 + 0.85 * n))
                r = c2[0] + (c1[0] - c2[0]) * n
                g = c2[1] + (c1[1] - c2[1]) * n
                b = c2[2] + (c1[2] - c2[2]) * n
                a = val * 220.0

            elif preset == "particle_shockwave_ring":
                ring = math.exp(-((dist - 0.72) ** 2) / 0.012) * _clamp01(1.0 - max(0.0, dist - 0.85) * 6.0)
                r, g, b = c1
                a = _clamp01(ring) * 255.0

            elif preset == "beam_energy_streak":
                band = math.exp(-(cy * cy) / 0.08) * (0.65 + 0.35 * _fbm(nx * 3.0, ny * 8.0))
                r = c2[0] + (c1[0] - c2[0]) * band
                g = c2[1] + (c1[1] - c2[1]) * band
                b = c2[2] + (c1[2] - c2[2]) * band
                a = _clamp01(band) * 255.0

            elif preset in ("grid_dark_1m", "grid_accent_orange"):
                major = (px < 2 or py < 2 or px >= w - 1 or py >= h - 1)
                sub = (px % (w // 4) == 0) or (py % (h // 4) == 0)
                t = 1.0 if major else (0.35 if sub else 0.0)
                r = c1[0] + (c2[0] - c1[0]) * t
                g = c1[1] + (c2[1] - c1[1]) * t
                b = c1[2] + (c2[2] - c1[2]) * t
                a = 255.0

            elif preset == "hazard_stripes":
                stripe = ((px + py) // max(w // 8, 4)) % 2
                r, g, b = c1 if stripe == 0 else c2
                a = 255.0

            elif preset == "brushed_metal_noise":
                n = _fbm(nx * 2.5, ny * 42.0, octaves=3)
                r = c2[0] + (c1[0] - c2[0]) * n
                g = c2[1] + (c1[1] - c2[1]) * n
                b = c2[2] + (c1[2] - c2[2]) * n
                a = 255.0

            elif preset == "scifi_panel_tile":
                bevel = (px < 3 or py < 3 or px > w - 4 or py > h - 4)
                seam = abs(px - w // 2) < 2 and (h // 5 < py < 4 * h // 5)
                glow = seam or (abs(px - w // 6) < 3 and abs(py - h // 6) < 3)
                if glow:
                    r, g, b = c2
                elif bevel:
                    r, g, b = c1[0] * 0.55, c1[1] * 0.55, c1[2] * 0.55
                else:
                    r, g, b = c1
                a = 255.0

            else:
                # Generic smooth gradient badge
                mask = _smoothstep(0.92 + aa, 0.92 - aa, dist)
                t = _clamp01((nx + ny) * 0.5)
                r = c2[0] + (c1[0] - c2[0]) * t
                g = c2[1] + (c1[1] - c2[1]) * t
                b = c2[2] + (c1[2] - c2[2]) * t
                a = mask * 255.0

            buf[idx] = max(0, min(255, int(r + 0.5)))
            buf[idx + 1] = max(0, min(255, int(g + 0.5)))
            buf[idx + 2] = max(0, min(255, int(b + 0.5)))
            buf[idx + 3] = max(0, min(255, int(a + 0.5)))
            idx += 4

    return w, h, bytes(buf)


# ============================================================================
# 3. Local Studio `content/arena_assets` Discovery + Open Cloud Assets v1
# ============================================================================

def find_roblox_studio_content_dirs(extra_dir: Optional[str] = None) -> List[Path]:
    """Find installed Roblox Studio `content` folders so copying an asset to
    `content/arena_assets/<name>.png` makes `rbxasset://arena_assets/<name>.png`
    immediately usable inside Studio with zero API keys and zero moderation wait.
    """
    found: List[Path] = []
    env_override = extra_dir or os.environ.get("ROBLOX_STUDIO_CONTENT_DIR")
    if env_override:
        p = Path(env_override)
        p.mkdir(parents=True, exist_ok=True)
        found.append(p)

    sys_name = platform.system()
    if sys_name == "Windows":
        roots = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Roblox" / "Versions",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Roblox" / "Versions",
            Path(os.environ.get("PROGRAMFILES", "")) / "Roblox" / "Versions",
        ]
        for vroot in roots:
            if vroot.is_dir():
                for vdir in sorted(vroot.glob("version-*"), key=lambda x: x.stat().st_mtime, reverse=True):
                    if (vdir / "RobloxStudioBeta.exe").exists() and (vdir / "content").is_dir():
                        found.append(vdir / "content")
        direct_studio = Path(os.environ.get("LOCALAPPDATA", "")) / "Roblox Studio" / "content"
        if direct_studio.is_dir():
            found.append(direct_studio)
    elif sys_name == "Darwin":
        for app in (
            Path("/Applications/RobloxStudio.app/Contents/Resources/content"),
            Path.home() / "Applications/RobloxStudio.app/Contents/Resources/content",
        ):
            if app.is_dir():
                found.append(app)

    # Deduplicate preserving order
    unique: List[Path] = []
    seen = set()
    for p in found:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def install_into_studio_content(raw_bytes: bytes, rel_filename: str, extra_dir: Optional[str] = None) -> dict:
    """Copy asset bytes into `<Studio>/content/arena_assets/<rel_filename>` across
    all detected local Roblox Studio installations.
    """
    clean_rel = rel_filename.replace("\\", "/").lstrip("/")
    content_dirs = find_roblox_studio_content_dirs(extra_dir=extra_dir)
    installed_paths: List[str] = []
    errors: List[str] = []

    for cdir in content_dirs:
        dest = cdir / "arena_assets" / clean_rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(raw_bytes)
            installed_paths.append(str(dest))
        except OSError as exc:
            errors.append(f"{dest}: {exc}")

    uri = f"rbxasset://arena_assets/{clean_rel}"
    return {
        "installed": bool(installed_paths),
        "uri": uri,
        "installedPaths": installed_paths,
        "errors": errors,
    }


def upload_open_cloud_image(
    raw_bytes: bytes,
    filename: str,
    display_name: str,
    description: str = "Uploaded via Roblox Studio MCP Bridge",
    api_key: Optional[str] = None,
    creator_id: Optional[str] = None,
    creator_type: str = "user",
    asset_type: str = "Image",
) -> dict:
    """Upload PNG/JPEG/BMP/TGA directly to Roblox Open Cloud Assets API v1 and
    poll the returned operation until `done == true` to obtain permanent `rbxassetid://<id>`.
    """
    key = (api_key or os.environ.get("ROBLOX_API_KEY") or "").strip()
    cid = str(creator_id or os.environ.get("ROBLOX_CREATOR_ID") or os.environ.get("ROBLOX_USER_ID") or "").strip()
    ctype_mode = (creator_type or os.environ.get("ROBLOX_CREATOR_TYPE") or "user").lower()

    if not key or not cid:
        raise ValueError(
            "Open Cloud upload requires ROBLOX_API_KEY and ROBLOX_CREATOR_ID (or pass apiKey + creatorId)."
        )

    ext = Path(filename).suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".bmp": "image/bmp",
        ".tga": "image/tga",
    }
    mime = mime_map.get(ext, "image/png")
    creator_block = {"groupId": cid} if ctype_mode == "group" else {"userId": cid}

    req_meta = json.dumps({
        "assetType": asset_type if asset_type in ("Image", "Decal") else "Image",
        "displayName": display_name[:50],
        "description": description[:900],
        "creationContext": {"creator": creator_block},
    }).encode("utf-8")

    boundary = "----ArenaStudioMCPBoundary" + hashlib.md5(raw_bytes[:64] + str(time.time()).encode()).hexdigest()
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("ascii"))
    body.extend(b'Content-Disposition: form-data; name="request"\r\nContent-Type: application/json\r\n\r\n')
    body.extend(req_meta)
    body.extend(f"\r\n--{boundary}\r\n".encode("ascii"))
    body.extend(
        f'Content-Disposition: form-data; name="fileContent"; filename="{Path(filename).name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
    )
    body.extend(raw_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("ascii"))

    req = urllib.request.Request(
        "https://apis.roblox.com/assets/v1/assets",
        data=bytes(body),
        method="POST",
        headers={
            "x-api-key": key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        op_data = json.loads(resp.read().decode("utf-8"))

    op_path = op_data.get("path") or ""
    deadline = time.time() + 60.0
    while not op_data.get("done") and op_path and time.time() < deadline:
        time.sleep(1.5)
        poll_url = f"https://apis.roblox.com/assets/v1/{op_path.lstrip('/')}"
        poll_req = urllib.request.Request(poll_url, headers={"x-api-key": key})
        with urllib.request.urlopen(poll_req, timeout=20) as p_resp:
            op_data = json.loads(p_resp.read().decode("utf-8"))

    if "error" in op_data and op_data["error"]:
        raise RuntimeError(f"Open Cloud asset error: {op_data['error']}")

    asset_id = str((op_data.get("response") or {}).get("assetId") or "")
    if not asset_id:
        raise RuntimeError(f"Open Cloud operation did not yield assetId in time: {op_data}")

    return {
        "assetId": int(asset_id) if asset_id.isdigit() else asset_id,
        "uri": f"rbxassetid://{asset_id}",
        "operation": op_path,
    }


# ============================================================================
# 4. Repo Asset Registry (`asset-lock.json` + Generated `Assets.lua`)
# ============================================================================

def _slugify_key(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", Path(name).stem)
    if not clean or clean[0].isdigit():
        clean = "img_" + clean
    return clean


def update_repo_asset_manifest(entry: Optional[dict] = None) -> dict:
    """Record or refresh all repo assets in `roblox-kit/assets/asset-lock.json`
    and regenerate `roblox-kit/modules/Assets.lua` cleanly.
    """
    kit = find_kit_root()
    assets_dir = kit / "assets"
    images_dir = assets_dir / "images"
    modules_dir = kit / "modules"
    images_dir.mkdir(parents=True, exist_ok=True)
    modules_dir.mkdir(parents=True, exist_ok=True)

    lock_path = assets_dir / "asset-lock.json"
    lock_data: dict = {"version": 1, "images": {}}
    if lock_path.is_file():
        try:
            lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if entry and entry.get("key"):
        lock_data.setdefault("images", {})[entry["key"]] = entry

    # Also auto-index any PNG/JPG/BMP files sitting in `roblox-kit/assets/images/**`
    for img_file in sorted(images_dir.rglob("*")):
        if img_file.is_file() and img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
            rel = img_file.relative_to(images_dir).as_posix()
            key = _slugify_key(img_file.name)
            raw = img_file.read_bytes()
            sha = hashlib.sha256(raw).hexdigest()[:16]
            prev = lock_data["images"].get(key, {})
            w = prev.get("width", 0)
            h = prev.get("height", 0)
            if (not w or prev.get("sha256") != sha) and raw[:8] == PNG_SIG:
                w, h = struct.unpack(">II", raw[16:24])
            lock_data["images"][key] = {
                "key": key,
                "relPath": rel,
                "sha256": sha,
                "bytes": len(raw),
                "width": w,
                "height": h,
                "localUri": f"rbxasset://arena_assets/{rel}",
                "cloudUri": prev.get("cloudUri") or (entry.get("cloudUri") if entry and entry.get("key") == key else None),
            }

    # Also auto-index any WAV/OGG/MP3 audio files sitting in `roblox-kit/assets/audio/**`
    audio_dir = kit / "assets" / "audio"
    lock_data.setdefault("audio", {})
    if audio_dir.is_dir():
        for aud_file in sorted(audio_dir.rglob("*")):
            if aud_file.is_file() and aud_file.suffix.lower() in (".wav", ".ogg", ".mp3"):
                rel_a = aud_file.relative_to(audio_dir).as_posix()
                akey = _slugify_key(aud_file.name)
                raw_a = aud_file.read_bytes()
                prev_a = lock_data["audio"].get(akey, {})
                lock_data["audio"][akey] = {
                    "key": akey,
                    "relPath": rel_a,
                    "sha256": hashlib.sha256(raw_a).hexdigest()[:16],
                    "bytes": len(raw_a),
                    "localUri": f"rbxasset://arena_assets/audio/{rel_a}",
                    "cloudUri": prev_a.get("cloudUri"),
                }

    lock_path.write_text(json.dumps(lock_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Emit clean zero-warning Luau `Assets.lua` module
    lines = [
        "-- Auto-generated by image_pipeline.py — maps friendly keys to Cloud `rbxassetid://`",
        "-- when published or instant local `rbxasset://arena_assets/...` URIs in Studio.",
        "local Assets = {}",
        "",
        "Assets.Images = {",
    ]
    for key in sorted(lock_data["images"].keys()):
        item = lock_data["images"][key]
        preferred = item.get("cloudUri") or item.get("localUri")
        lines.append(f'\t{key} = "{preferred}",')
    lines.append("}")
    lines.append("")
    lines.append("Assets.Audio = {")
    for akey in sorted(lock_data["audio"].keys()):
        aitem = lock_data["audio"][akey]
        preferred_a = aitem.get("cloudUri") or aitem.get("localUri")
        lines.append(f'\t{akey} = "{preferred_a}",')
    lines.append("}")
    lines.append("")
    lines.append("Assets.Meta = {")
    for key in sorted(lock_data["images"].keys()):
        item = lock_data["images"][key]
        lines.append(
            f'\t{key} = {{ width = {int(item.get("width") or 0)}, height = {int(item.get("height") or 0)}, '
            f'relPath = "{item["relPath"]}" }},'
        )
    lines.append("}")
    lines.append("")
    lines.append("function Assets.get(name)")
    lines.append("\treturn Assets.Images[name] or Assets.Audio[name]")
    lines.append("end")
    lines.append("")
    lines.append("return Assets")
    lines.append("")

    assets_lua_path = modules_dir / "Assets.lua"
    assets_lua_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "lockFile": str(lock_path),
        "moduleFile": str(assets_lua_path),
        "count": len(lock_data["images"]),
        "audioCount": len(lock_data["audio"]),
        "images": lock_data["images"],
        "audio": lock_data["audio"],
    }


def seed_builtin_repo_assets(force: bool = False) -> dict:
    """Generate all 26 crisp built-in UI icons, VFX sprites, and world surface
    PNGs into `roblox-kit/assets/images/{ui,vfx,textures}/` and write `Assets.lua`.
    """
    kit = find_kit_root()
    images_dir = kit / "assets" / "images"
    generated = 0
    for preset in PROCEDURAL_PRESETS:
        if preset.startswith("particle_") or preset.startswith("beam_"):
            sub = "vfx"
        elif preset.startswith("grid_") or preset in ("hazard_stripes", "brushed_metal_noise", "scifi_panel_tile"):
            sub = "textures"
        else:
            sub = "ui"
        out_file = images_dir / sub / f"{preset}.png"
        if force or not out_file.is_file():
            out_file.parent.mkdir(parents=True, exist_ok=True)
            w, h, rgba = generate_procedural_rgba(preset)
            out_file.write_bytes(encode_png_rgba(w, h, rgba))
            generated += 1
    manifest = update_repo_asset_manifest()
    return {"generatedCount": generated, "totalAssets": manifest["count"], "manifest": manifest["moduleFile"]}


if __name__ == "__main__":
    info = seed_builtin_repo_assets(force=True)
    print(json.dumps(info, indent=2))
