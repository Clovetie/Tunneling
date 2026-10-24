"""
Zero-Dependency 3D Place Viewport & HUD Rasterizer + Native Capture Fallback.

Why this exists:
- Multimodal AI agents (Claude, GPT, Gemini, Arena) perform dramatically better when
  `capture_viewport` returns a real `image/png` MCP block so the model can *see* part
  proportions, alignment, colors, lighting mood, camera framing, and HUD overlay balance.
- Relying solely on OS desktop window screenshots breaks whenever Roblox Studio is
  minimized, occluded behind an IDE, running on Linux/headless containers, or running
  against `mock_studio.py`.
- `viewport_renderer.py` captures native Studio screenshots when requested & available,
  and ALWAYS provides a fast, deterministic 3D perspective/orthographic painter + 2D HUD
  compositor written in 100% Python stdlib (`math`, `zlib`, `struct`).
"""

from __future__ import annotations

import base64
import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from image_pipeline import decode_any_to_rgba, encode_png_rgba, find_kit_root
from ui_linter import audit_tree_on_viewport


# ---------------------------------------------------------------------------
# Property value unpackers (compatible with both ArenaBridge & mock_studio)
# ---------------------------------------------------------------------------

def _vec3(val, default=(0.0, 0.0, 0.0)) -> Tuple[float, float, float]:
    if isinstance(val, dict):
        v = val.get("v") or val.get("value")
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            return float(v[0]), float(v[1]), float(v[2])
        if "x" in val:
            return float(val.get("x", 0)), float(val.get("y", 0)), float(val.get("z", 0))
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        return float(val[0]), float(val[1]), float(val[2])
    return default


def _cframe_pos_axes(val) -> Optional[Tuple[Tuple[float, float, float], Tuple[Tuple[float, float, float], ...]]]:
    if isinstance(val, dict):
        v = val.get("v") or val.get("value")
    elif isinstance(val, (list, tuple)):
        v = val
    else:
        return None
    if not isinstance(v, (list, tuple)):
        return None
    if len(v) >= 12:
        pos = (float(v[0]), float(v[1]), float(v[2]))
        axes = (
            (float(v[3]), float(v[6]), float(v[9])),
            (float(v[4]), float(v[7]), float(v[10])),
            (float(v[5]), float(v[8]), float(v[11])),
        )
        return pos, axes
    if len(v) >= 3:
        return (float(v[0]), float(v[1]), float(v[2])), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    return None


def _color_rgb(val, default=(163, 162, 165)) -> Tuple[int, int, int]:
    if isinstance(val, dict):
        v = val.get("v") or val.get("value")
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            mx = max(float(v[0]), float(v[1]), float(v[2]))
            scale = 255.0 if mx <= 1.001 else 1.0
            return (
                max(0, min(255, int(round(float(v[0]) * scale)))),
                max(0, min(255, int(round(float(v[1]) * scale)))),
                max(0, min(255, int(round(float(v[2]) * scale)))),
            )
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        mx = max(float(val[0]), float(val[1]), float(val[2]))
        scale = 255.0 if mx <= 1.001 else 1.0
        return (
            max(0, min(255, int(round(float(val[0]) * scale)))),
            max(0, min(255, int(round(float(val[1]) * scale)))),
            max(0, min(255, int(round(float(val[2]) * scale)))),
        )
    if isinstance(val, str) and val.startswith("#") and len(val) == 7:
        return int(val[1:3], 16), int(val[3:5], 16), int(val[5:7], 16)
    return default


def _v_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _v_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _v_mul(a, s: float):
    return (a[0] * s, a[1] * s, a[2] * s)


def _v_dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _v_cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _v_norm(a):
    ln = math.sqrt(max(1e-12, _v_dot(a, a)))
    return (a[0] / ln, a[1] / ln, a[2] / ln)


# ---------------------------------------------------------------------------
# Fast scanline convex polygon filler into preallocated bytearray RGBA buffer
# ---------------------------------------------------------------------------

def _fill_poly_alpha(buf: bytearray, w: int, h: int, pts: List[Tuple[float, float]], rgba: Tuple[int, int, int, int]) -> None:
    if len(pts) < 3:
        return
    r, g, b, a = rgba
    if a <= 0:
        return
    min_y = max(0, int(math.floor(min(p[1] for p in pts))))
    max_y = min(h - 1, int(math.ceil(max(p[1] for p in pts))))
    if min_y > max_y:
        return

    n = len(pts)
    inv_a = 255 - a
    for y in range(min_y, max_y + 1):
        py = y + 0.5
        xs = []
        j = n - 1
        for i in range(n):
            yi, yj = pts[i][1], pts[j][1]
            if (yi <= py < yj) or (yj <= py < yi):
                xi, xj = pts[i][0], pts[j][0]
                xs.append(xi + (py - yi) * (xj - xi) / (yj - yi))
            j = i
        if len(xs) < 2:
            continue
        xs.sort()
        for k in range(0, len(xs) - 1, 2):
            x0 = max(0, int(math.floor(xs[k])))
            x1 = min(w - 1, int(math.ceil(xs[k + 1])))
            row_off = y * w * 4
            if a >= 255:
                for x in range(x0, x1 + 1):
                    idx = row_off + x * 4
                    buf[idx] = r
                    buf[idx + 1] = g
                    buf[idx + 2] = b
                    buf[idx + 3] = 255
            else:
                for x in range(x0, x1 + 1):
                    idx = row_off + x * 4
                    buf[idx] = (r * a + buf[idx] * inv_a) // 255
                    buf[idx + 1] = (g * a + buf[idx + 1] * inv_a) // 255
                    buf[idx + 2] = (b * a + buf[idx + 2] * inv_a) // 255
                    buf[idx + 3] = 255


def _draw_line(buf: bytearray, w: int, h: int, x0: float, y0: float, x1: float, y1: float, rgba: Tuple[int, int, int, int]) -> None:
    dx = x1 - x0
    dy = y1 - y0
    steps = int(max(abs(dx), abs(dy)))
    if steps <= 0 or steps > 4000:
        return
    sx = dx / steps
    sy = dy / steps
    r, g, b, a = rgba
    inv_a = 255 - a
    x, y = x0, y0
    for _ in range(steps + 1):
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < w and 0 <= iy < h:
            idx = (iy * w + ix) * 4
            buf[idx] = (r * a + buf[idx] * inv_a) // 255
            buf[idx + 1] = (g * a + buf[idx + 1] * inv_a) // 255
            buf[idx + 2] = (b * a + buf[idx + 2] * inv_a) // 255
        x += sx
        y += sy


def _draw_rect_border(buf: bytearray, w: int, h: int, rx: int, ry: int, rw: int, rh: int, rgba: Tuple[int, int, int, int]) -> None:
    _draw_line(buf, w, h, rx, ry, rx + rw, ry, rgba)
    _draw_line(buf, w, h, rx + rw, ry, rx + rw, ry + rh, rgba)
    _draw_line(buf, w, h, rx + rw, ry + rh, rx, ry + rh, rgba)
    _draw_line(buf, w, h, rx, ry + rh, rx, ry, rgba)


# Minimal crisp 3x5 bitmap font so HUD text & part labels render legibly in pure stdlib
_TINY_FONT = {
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "110", "100", "110", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "101", "101", "101"),
    "N": ("101", "111", "111", "101", "101"),
    "O": ("010", "101", "101", "101", "010"),
    "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "011", "001"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "101", "111", "101"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "110", "010", "010", "111"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "011", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
    "+": ("000", "010", "111", "010", "000"),
    "-": ("000", "000", "111", "000", "000"),
    ".": ("000", "000", "000", "000", "010"),
    ":": ("000", "010", "000", "010", "000"),
    "/": ("001", "001", "010", "100", "100"),
    "!": ("010", "010", "010", "000", "010"),
    "$": ("011", "110", "010", "011", "110"),
    "%": ("101", "001", "010", "100", "101"),
}


def _draw_text(buf: bytearray, w: int, h: int, x: int, y: int, text: str, rgb: Tuple[int, int, int], scale: int = 2) -> None:
    # Layout-derived GUI positions can be fractional at scaled viewport sizes.
    # Pixel indices stay integral; placement is rounded once at rasterization.
    cx = int(round(x))
    y = int(round(y))
    scale = max(1, int(round(scale)))
    r, g, b = rgb
    for ch in text.upper()[:34]:
        if ch == " ":
            cx += 4 * scale
            continue
        glyph = _TINY_FONT.get(ch)
        if not glyph:
            cx += 3 * scale
            continue
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit == "1":
                    for sy in range(scale):
                        py = y + gy * scale + sy
                        if 0 <= py < h:
                            for sx in range(scale):
                                px = cx + gx * scale + sx
                                if 0 <= px < w:
                                    idx = (py * w + px) * 4
                                    buf[idx] = r
                                    buf[idx + 1] = g
                                    buf[idx + 2] = b
                                    buf[idx + 3] = 255
        cx += 4 * scale


def _blit_scaled_rgba(
    dst: bytearray, dw: int, dh: int,
    src: bytes, sw: int, sh: int,
    tx: int, ty: int, tw: int, th: int,
) -> None:
    if tw <= 0 or th <= 0 or sw <= 0 or sh <= 0:
        return
    # Roblox UI layout can produce fractional pixel rectangles at non-integer
    # viewport scales. Keep the sampling math fractional, but raster bounds must
    # be integers or range() raises TypeError.
    top = max(0, math.floor(ty))
    bottom = min(dh, math.ceil(ty + th))
    left = max(0, math.floor(tx))
    right = min(dw, math.ceil(tx + tw))
    for y in range(top, bottom):
        sy = min(sh - 1, int((y - ty) * sh / th))
        s_row = sy * sw * 4
        d_row = y * dw * 4
        for x in range(left, right):
            sx = min(sw - 1, int((x - tx) * sw / tw))
            si = s_row + sx * 4
            sa = src[si + 3]
            if sa <= 0:
                continue
            di = d_row + x * 4
            inv = 255 - sa
            dst[di] = (src[si] * sa + dst[di] * inv) // 255
            dst[di + 1] = (src[si + 1] * sa + dst[di + 1] * inv) // 255
            dst[di + 2] = (src[si + 2] * sa + dst[di + 2] * inv) // 255
            dst[di + 3] = 255


# ---------------------------------------------------------------------------
# Extract 3D Parts from an inspected Studio hierarchy
# ---------------------------------------------------------------------------

_PART_CLASSES = {
    "Part", "WedgePart", "CornerWedgePart", "TrussPart", "MeshPart",
    "UnionOperation", "SpawnLocation", "Seat", "VehicleSeat",
}


def collect_parts_from_tree(node: dict, out: Optional[list] = None) -> list:
    if out is None:
        out = []
    cls = node.get("className") or ""
    props = {**(node.get("properties") or {}), **(node.get("structuredProperties") or {})}
    if cls in _PART_CLASSES:
        trans = float(props.get("Transparency", 0.0) or 0.0)
        if trans < 0.99:
            size = _vec3(props.get("Size"), (4.0, 1.0, 2.0))
            cf = _cframe_pos_axes(props.get("CFrame"))
            if cf:
                pos, axes = cf
            else:
                pos = _vec3(props.get("Position"), (0.0, size[1] * 0.5, 0.0))
                axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
            mat = str(props.get("Material") or "SmoothPlastic")
            is_neon = "Neon" in mat
            out.append({
                "name": node.get("name") or cls,
                "path": node.get("path") or node.get("name") or cls,
                "className": cls,
                "pos": pos,
                "size": (max(0.05, abs(size[0])), max(0.05, abs(size[1])), max(0.05, abs(size[2]))),
                "axes": axes,
                "color": _color_rgb(props.get("Color"), (91, 154, 76) if cls == "SpawnLocation" else (155, 162, 175)),
                "transparency": max(0.0, min(0.98, trans)),
                "neon": is_neon,
            })
    for ch in node.get("children") or []:
        collect_parts_from_tree(ch, out)
    return out


_CUBE_VERTS = [
    (-0.5, -0.5, -0.5),
    ( 0.5, -0.5, -0.5),
    ( 0.5,  0.5, -0.5),
    (-0.5,  0.5, -0.5),
    (-0.5, -0.5,  0.5),
    ( 0.5, -0.5,  0.5),
    ( 0.5,  0.5,  0.5),
    (-0.5,  0.5,  0.5),
]

_CUBE_FACES = [
    ((0, 3, 2, 1), ( 0.0,  0.0, -1.0)),  # -Z
    ((4, 5, 6, 7), ( 0.0,  0.0,  1.0)),  # +Z
    ((0, 1, 5, 4), ( 0.0, -1.0,  0.0)),  # -Y bottom
    ((3, 7, 6, 2), ( 0.0,  1.0,  0.0)),  # +Y top
    ((0, 4, 7, 3), (-1.0,  0.0,  0.0)),  # -X
    ((1, 2, 6, 5), ( 1.0,  0.0,  0.0)),  # +X
]


def _compute_auto_camera(parts: list, preset: str) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], bool]:
    # Ignore giant baseplates (> 256 studs wide) when framing foreground builds so structures fill the shot cleanly
    fg = [p for p in parts if max(p["size"][0], p["size"][2]) <= 220.0]
    focus_parts = fg if fg else parts
    if not focus_parts:
        center = (0.0, 4.0, 0.0)
        radius = 28.0
    else:
        min_x = min(p["pos"][0] - p["size"][0] * 0.5 for p in focus_parts)
        max_x = max(p["pos"][0] + p["size"][0] * 0.5 for p in focus_parts)
        min_y = min(p["pos"][1] - p["size"][1] * 0.5 for p in focus_parts)
        max_y = max(p["pos"][1] + p["size"][1] * 0.5 for p in focus_parts)
        min_z = min(p["pos"][2] - p["size"][2] * 0.5 for p in focus_parts)
        max_z = max(p["pos"][2] + p["size"][2] * 0.5 for p in focus_parts)
        center = ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5, (min_z + max_z) * 0.5)
        span = max(14.0, max_x - min_x, max_y - min_y, max_z - min_z)
        radius = min(190.0, span * 1.18 + 8.0)

    p = (preset or "perspective").lower()
    if p == "top_blueprint":
        return (center[0], center[1] + radius * 1.35, center[2] + 0.01), center, True
    if p == "front":
        return (center[0], center[1] + radius * 0.28, center[2] + radius * 1.25), center, False
    if p == "iso_nw":
        return (center[0] - radius * 0.9, center[1] + radius * 0.72, center[2] - radius * 0.9), center, False
    if p == "iso_ne":
        return (center[0] + radius * 0.9, center[1] + radius * 0.72, center[2] - radius * 0.9), center, False
    return (center[0] + radius * 0.85, center[1] + radius * 0.65, center[2] + radius * 0.95), center, False


def render_scene_png(
    workspace_tree: dict,
    gui_tree: Optional[list] = None,
    lighting_props: Optional[dict] = None,
    width: int = 800,
    height: int = 450,
    camera_preset: str = "perspective",
    explicit_cam_pos: Optional[Tuple[float, float, float]] = None,
    explicit_look_at: Optional[Tuple[float, float, float]] = None,
    fov_deg: float = 70.0,
    include_hud: bool = True,
    show_labels: bool = True,
    explicit_ortho: bool = False,
    show_grid: bool = True,
) -> Tuple[bytes, dict]:
    w = max(240, min(1280, int(width)))
    h = max(160, min(800, int(height)))
    parts = collect_parts_from_tree(workspace_tree)

    # Sky & horizon gradient tuned from Lighting properties
    lprops = lighting_props or {}
    clock_time = float(lprops.get("ClockTime", 14.0) or 14.0)
    is_night = clock_time < 6.2 or clock_time > 18.8
    if is_night:
        sky_top = (10, 15, 32)
        sky_hor = (26, 39, 72)
        gnd_col = (14, 20, 32)
    elif 17.0 <= clock_time <= 18.8 or 6.2 <= clock_time <= 7.5:
        sky_top = (44, 52, 98)
        sky_hor = (232, 134, 88)
        gnd_col = (34, 36, 48)
    else:
        sky_top = (54, 128, 224)
        sky_hor = (168, 214, 248)
        gnd_col = (44, 58, 72)

    buf = bytearray(w * h * 4)
    horizon_y = int(h * 0.56)
    for y in range(h):
        if y <= horizon_y:
            t = y / max(1, horizon_y)
            r = int(sky_top[0] * (1 - t) + sky_hor[0] * t)
            g = int(sky_top[1] * (1 - t) + sky_hor[1] * t)
            b = int(sky_top[2] * (1 - t) + sky_hor[2] * t)
        else:
            t = (y - horizon_y) / max(1, h - horizon_y)
            r = int(sky_hor[0] * (1 - t) * 0.35 + gnd_col[0] * (0.65 + 0.35 * t))
            g = int(sky_hor[1] * (1 - t) * 0.35 + gnd_col[1] * (0.65 + 0.35 * t))
            b = int(sky_hor[2] * (1 - t) * 0.35 + gnd_col[2] * (0.65 + 0.35 * t))
        row_bytes = bytes((r, g, b, 255)) * w
        buf[y * w * 4 : (y + 1) * w * 4] = row_bytes

    if explicit_cam_pos and explicit_look_at:
        cam_pos, look_at, ortho = tuple(map(float, explicit_cam_pos)), tuple(map(float, explicit_look_at)), bool(explicit_ortho)
    else:
        cam_pos, look_at, ortho = _compute_auto_camera(parts, camera_preset)
        ortho = bool(ortho or explicit_ortho)

    fwd = _v_norm(_v_sub(look_at, cam_pos))
    up_hint = (0.0, 0.0, -1.0) if abs(fwd[1]) > 0.985 else (0.0, 1.0, 0.0)
    right = _v_norm(_v_cross(fwd, up_hint))
    cam_up = _v_norm(_v_cross(right, fwd))

    focal = (h * 0.5) / math.tan(math.radians(max(25.0, min(110.0, fov_deg))) * 0.5)
    ortho_scale = (h * 0.42) / max(8.0, math.sqrt(_v_dot(_v_sub(look_at, cam_pos), _v_sub(look_at, cam_pos))) * 0.55)

    def project_camera(c):
        cx, cy, cz = c
        if ortho:
            return (w * 0.5 + cx * ortho_scale, h * 0.5 - cy * ortho_scale, cz)
        if cz <= 0.25:
            return None
        inv = focal / cz
        return (w * 0.5 + cx * inv, h * 0.5 - cy * inv, cz)

    def camera_coords(pt):
        rel = _v_sub(pt, cam_pos)
        return (_v_dot(rel, right), _v_dot(rel, cam_up), _v_dot(rel, fwd))

    def project(pt):
        return project_camera(camera_coords(pt))

    def clip_near(poly):
        if ortho:
            return poly
        clipped = []
        for i, cur in enumerate(poly):
            prev = poly[i - 1]
            cin, pin = cur[2] > 0.25, prev[2] > 0.25
            if cin != pin:
                t = (0.25 - prev[2]) / (cur[2] - prev[2])
                clipped.append(tuple(prev[k] + t * (cur[k] - prev[k]) for k in range(3)))
            if cin:
                clipped.append(cur)
        return clipped

    # Subtle world grid lines on Y = 0 plane so scale & alignment are visually crystal clear
    if show_grid:
        grid_step = 8
        grid_col = (255, 255, 255, 24)
        gx_center = round(look_at[0] / grid_step) * grid_step
        gz_center = round(look_at[2] / grid_step) * grid_step
        for i in range(-8, 9):
            p0 = project((gx_center + i * grid_step, 0.02, gz_center - 8 * grid_step))
            p1 = project((gx_center + i * grid_step, 0.02, gz_center + 8 * grid_step))
            if p0 and p1:
                _draw_line(buf, w, h, p0[0], p0[1], p1[0], p1[1], grid_col)
            q0 = project((gx_center - 8 * grid_step, 0.02, gz_center + i * grid_step))
            q1 = project((gx_center + 8 * grid_step, 0.02, gz_center + i * grid_step))
            if q0 and q1:
                _draw_line(buf, w, h, q0[0], q0[1], q1[0], q1[1], grid_col)

    sun_dir = _v_norm((0.46, 0.78, 0.42))
    draw_faces = []
    label_anchors = []

    for p in parts:
        px, py, pz = p["pos"]
        sx, sy, sz = p["size"]
        ax, ay, az = p["axes"]
        wverts = []
        cam_verts = []
        for lx, ly, lz in _CUBE_VERTS:
            wx = px + ax[0] * (lx * sx) + ay[0] * (ly * sy) + az[0] * (lz * sz)
            wy = py + ax[1] * (lx * sx) + ay[1] * (ly * sy) + az[1] * (lz * sz)
            wz = pz + ax[2] * (lx * sx) + ay[2] * (ly * sy) + az[2] * (lz * sz)
            wv = (wx, wy, wz)
            wverts.append(wv)
            cam_verts.append(camera_coords(wv))

        alpha = max(35, min(255, int(round((1.0 - p["transparency"]) * 255))))
        base_r, base_g, base_b = p["color"]

        if p["name"] != "Baseplate" and max(sx, sz) < 120.0:
            top_pr = project((px, py + sy * 0.5 + 1.2, pz))
            if top_pr and 10 <= top_pr[0] <= w - 60 and 10 <= top_pr[1] <= h - 20:
                label_anchors.append((top_pr[2], int(top_pr[0]), int(top_pr[1]), p["name"]))

        for idxs, lnrm in _CUBE_FACES:
            wnrm = _v_norm((
                ax[0] * lnrm[0] + ay[0] * lnrm[1] + az[0] * lnrm[2],
                ax[1] * lnrm[0] + ay[1] * lnrm[1] + az[1] * lnrm[2],
                ax[2] * lnrm[0] + ay[2] * lnrm[1] + az[2] * lnrm[2],
            ))
            fc = (
                sum(wverts[i][0] for i in idxs) * 0.25,
                sum(wverts[i][1] for i in idxs) * 0.25,
                sum(wverts[i][2] for i in idxs) * 0.25,
            )
            if not ortho:
                to_cam = _v_sub(cam_pos, fc)
                if _v_dot(wnrm, to_cam) <= 0.0:
                    continue
            elif _v_dot(wnrm, fwd) >= 0.0:
                continue
            camera_poly = clip_near([cam_verts[i] for i in idxs])
            projected = [project_camera(v) for v in camera_poly]
            projected = [v for v in projected if v is not None]
            if len(projected) < 3:
                continue

            if p["neon"]:
                shade = 1.18
            else:
                ndotl = max(0.0, _v_dot(wnrm, sun_dir))
                hemi = 0.52 + 0.14 * wnrm[1]
                shade = hemi + 0.56 * ndotl

            cr = max(0, min(255, int(round(base_r * shade))))
            cg = max(0, min(255, int(round(base_g * shade))))
            cb = max(0, min(255, int(round(base_b * shade))))
            depth = sum(v[2] for v in projected) / len(projected)
            pts2d = [(v[0], v[1]) for v in projected]
            draw_faces.append((depth, pts2d, (cr, cg, cb, alpha), p["neon"]))

    # Painter's sort back-to-front
    draw_faces.sort(key=lambda item: item[0], reverse=True)
    for _, pts2d, rgba, is_neon in draw_faces:
        _fill_poly_alpha(buf, w, h, pts2d, rgba)
        edge_rgba = (255, 255, 255, 110) if is_neon else (12, 16, 24, 70)
        for i in range(len(pts2d)):
            p0 = pts2d[i]
            p1 = pts2d[(i + 1) % len(pts2d)]
            _draw_line(buf, w, h, p0[0], p0[1], p1[0], p1[1], edge_rgba)

    # Draw crisp callout tags on nearest foreground objects
    if show_labels:
        label_anchors.sort(key=lambda x: x[0])
        for _, lx, ly, nm in label_anchors[:10]:
            tag = nm[:16]
            tw = len(tag) * 8 + 8
            _fill_poly_alpha(buf, w, h, [(lx - 4, ly - 9), (lx + tw, ly - 9), (lx + tw, ly + 4), (lx - 4, ly + 4)], (10, 14, 22, 185))
            _draw_text(buf, w, h, lx, ly - 6, tag, (226, 238, 255), scale=1)

    # Optional 2D ScreenGui HUD overlay right on top of the 3D viewport!
    hud_count = 0
    hud_audit = None
    if include_hud and gui_tree:
        kit_images = find_kit_root() / "assets" / "images"
        hud_audit = audit_tree_on_viewport(gui_tree, w, h, "render")
        audit = hud_audit
        img_cache: Dict[str, Tuple[int, int, bytes]] = {}
        for el in audit.get("elements") or []:
            rx, ry, rw, rh = el["rect"]
            if rw <= 1 or rh <= 1:
                continue
            hud_count += 1
            bg = el.get("bg", "")
            if bg.startswith("rgba("):
                nums = [float(v.strip()) for v in bg[5:-1].split(",")]
                _fill_poly_alpha(
                    buf, w, h,
                    [(rx, ry), (rx + rw, ry), (rx + rw, ry + rh), (rx, ry + rh)],
                    (int(nums[0]), int(nums[1]), int(nums[2]), int(round(nums[3] * 255))),
                )
            im_uri = el.get("image") or ""
            if im_uri.startswith("rbxasset://arena_assets/"):
                rel = im_uri[len("rbxasset://arena_assets/") :]
                if rel not in img_cache:
                    fp = kit_images / rel
                    if fp.is_file():
                        try:
                            iw, ih, irgba = decode_any_to_rgba(fp.read_bytes())
                            img_cache[rel] = (iw, ih, irgba)
                        except Exception:
                            img_cache[rel] = (0, 0, b"")
                sw, sh, sbytes = img_cache.get(rel, (0, 0, b""))
                if sw > 0 and sbytes:
                    _blit_scaled_rgba(buf, w, h, sbytes, sw, sh, rx, ry, rw, rh)
            if el.get("border"):
                _draw_rect_border(buf, w, h, rx, ry, rw, rh, (120, 180, 255, 160))
            if el.get("text"):
                _draw_text(
                    buf, w, h,
                    rx + 6,
                    ry + max(2, rh // 2 - 5),
                    str(el["text"]),
                    _color_rgb(el.get("textColor"), (255, 255, 255)),
                    scale=2 if rh >= 28 else 1,
                )

    # Top-left HUD telemetry bar
    banner = f"CAM:{camera_preset.upper()}  PARTS:{len(parts)}  HUD:{hud_count}  {w}X{h}"
    bw = len(banner) * 6 + 14
    _fill_poly_alpha(buf, w, h, [(8, 8), (8 + bw, 8), (8 + bw, 24), (8, 24)], (9, 13, 21, 195))
    _draw_text(buf, w, h, 14, 13, banner, (125, 211, 252), scale=1)

    png_bytes = encode_png_rgba(w, h, bytes(buf))
    meta = {
        "width": w,
        "height": h,
        "renderer": "software_3d_hud_rasterizer",
        "cameraPreset": camera_preset,
        "cameraPosition": [round(x, 2) for x in cam_pos],
        "cameraLookAt": [round(x, 2) for x in look_at],
        "partsRendered": len(parts),
        "facesRasterized": len(draw_faces),
        "hudElementsComposited": hud_count,
        "bytes": len(png_bytes),
    }
    if hud_audit is not None:
        meta["hudMeasuredFrom"] = hud_audit.get("measuredFrom", "derived")
        meta["safeArea"] = hud_audit.get("safeArea", [])
        if hud_audit.get("warnings"):
            meta["warnings"] = list(hud_audit["warnings"])
    return png_bytes, meta


def try_native_os_window_capture(out_path: Path) -> bool:
    """Optional desktop screenshot capture when running on macOS/Windows host with visible Studio."""
    try:
        if sys.platform == "darwin":
            subprocess.run(["screencapture", "-x", str(out_path)], check=True, timeout=5)
            return out_path.is_file() and out_path.stat().st_size > 1024
        if sys.platform == "win32":
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                "$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds; "
                "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
                "$g = [System.Drawing.Graphics]::FromImage($bmp); "
                "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size); "
                f"$bmp.Save('{str(out_path)}', [System.Drawing.Imaging.ImageFormat]::Png)"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, timeout=8)
            return out_path.is_file() and out_path.stat().st_size > 1024
    except Exception:
        return False
    return False
