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
  and ALWAYS provides a fast, deterministic 3D perspective/orthographic Z-buffered
  rasterizer + 2D HUD compositor written in 100% Python stdlib (`math`, `zlib`, `struct`).
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
# Property value unpackers (compatible with ArenaBridge structured AND string
# properties as well as mock_studio & prefab JSON)
# ---------------------------------------------------------------------------

_BRICKCOLOR_RGB: Dict[str, Tuple[int, int, int]] = {
    "white": (242, 243, 243),
    "institutional white": (248, 248, 248),
    "lily white": (237, 234, 234),
    "grey": (161, 165, 162),
    "medium stone grey": (163, 162, 165),
    "dark stone grey": (99, 95, 98),
    "black": (27, 42, 53),
    "really black": (17, 17, 17),
    " smoky grey": (91, 93, 105),
    "smoky grey": (91, 93, 105),
    "bright red": (196, 40, 28),
    "really red": (255, 0, 0),
    "crimson": (151, 0, 0),
    "maroon": (117, 0, 0),
    "bright blue": (13, 105, 172),
    "really blue": (0, 0, 255),
    "navy blue": (0, 32, 96),
    "cyan": (4, 175, 236),
    "toothpaste": (0, 255, 255),
    "bright green": (75, 151, 75),
    "dark green": (40, 127, 71),
    "earth green": (39, 70, 45),
    "sand green": (120, 144, 130),
    "slime green": (80, 109, 84),
    "lime green": (0, 255, 0),
    "camo": (58, 125, 21),
    "bright yellow": (245, 205, 48),
    "new yeller": (255, 255, 0),
    "gold": (220, 188, 129),
    "deep orange": (255, 176, 0),
    "bright orange": (218, 133, 65),
    "neon orange": (213, 115, 61),
    "reddish brown": (105, 64, 40),
    "brown": (124, 92, 70),
    "dirt brown": (86, 66, 54),
    "burnt sienna": (106, 57, 9),
    "nougat": (204, 142, 105),
    "burlap": (199, 172, 120),
    "brick yellow": (215, 197, 154),
    "sand yellow": (149, 138, 115),
    "bright violet": (107, 50, 124),
    "royal purple": (98, 37, 209),
    "hot pink": (255, 0, 191),
    "magenta": (170, 0, 170),
}


def _parse_float_tokens(val) -> Optional[List[float]]:
    if isinstance(val, str):
        cleaned = val.strip().translate(str.maketrans("{}[]()", "      "))
        if not cleaned or cleaned.startswith("#"):
            return None
        parts = [p.strip() for p in cleaned.split(",") if p.strip()]
        if not parts:
            return None
        try:
            return [float(x) for x in parts]
        except (TypeError, ValueError):
            return None
    return None


def _vec3(val, default=(0.0, 0.0, 0.0)) -> Tuple[float, float, float]:
    if isinstance(val, dict):
        v = val.get("v") or val.get("value")
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            return float(v[0]), float(v[1]), float(v[2])
        if "x" in val:
            return float(val.get("x", 0)), float(val.get("y", 0)), float(val.get("z", 0))
    if isinstance(val, (list, tuple)) and len(val) >= 3:
        return float(val[0]), float(val[1]), float(val[2])
    if isinstance(val, str):
        nums = _parse_float_tokens(val)
        if nums and len(nums) >= 3:
            return float(nums[0]), float(nums[1]), float(nums[2])
    return default


def _orientation_axes(
    ori_deg: Tuple[float, float, float]
) -> Tuple[Tuple[float, float, float], Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    rx = math.radians(ori_deg[0])
    ry = math.radians(ori_deg[1])
    rz = math.radians(ori_deg[2])
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # Roblox Orientation is intrinsic YXZ (R_y * R_x * R_z)
    r00 = cy * cz + sy * sx * sz
    r01 = -cy * sz + sy * sx * cz
    r02 = sy * cx
    r10 = cx * sz
    r11 = cx * cz
    r12 = -sx
    r20 = -sy * cz + cy * sx * sz
    r21 = sy * sz + cy * sx * cz
    r22 = cy * cx
    return ((r00, r10, r20), (r01, r11, r21), (r02, r12, r22))


def _cframe_pos_axes(
    val,
    pos_val=None,
    ori_val=None,
) -> Optional[Tuple[Tuple[float, float, float], Tuple[Tuple[float, float, float], ...]]]:
    v = None
    if isinstance(val, dict):
        v = val.get("v") or val.get("value")
    elif isinstance(val, (list, tuple)):
        v = val
    elif isinstance(val, str):
        v = _parse_float_tokens(val)

    default_axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    if ori_val is not None:
        ori = _vec3(ori_val, (0.0, 0.0, 0.0))
        if abs(ori[0]) > 1e-4 or abs(ori[1]) > 1e-4 or abs(ori[2]) > 1e-4:
            default_axes = _orientation_axes(ori)

    if isinstance(v, (list, tuple)):
        if len(v) >= 12:
            pos = (float(v[0]), float(v[1]), float(v[2]))
            axes = (
                (float(v[3]), float(v[6]), float(v[9])),
                (float(v[4]), float(v[7]), float(v[10])),
                (float(v[5]), float(v[8]), float(v[11])),
            )
            return pos, axes
        if len(v) >= 3:
            return (float(v[0]), float(v[1]), float(v[2])), default_axes

    if pos_val is not None:
        p = _vec3(pos_val, None)
        if p is not None:
            return p, default_axes
    return None


def _color_rgb(val, default=(163, 162, 165), brick_color=None) -> Tuple[int, int, int]:
    arr = None
    if isinstance(val, dict):
        v = val.get("v") or val.get("value")
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            arr = [float(v[0]), float(v[1]), float(v[2])]
        elif all(k in val for k in ("r", "g", "b")):
            arr = [float(val["r"]), float(val["g"]), float(val["b"])]
    elif isinstance(val, (list, tuple)) and len(val) >= 3:
        arr = [float(val[0]), float(val[1]), float(val[2])]
    elif isinstance(val, str):
        s = val.strip()
        if s.startswith("#") and len(s) == 7:
            try:
                return int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16)
            except ValueError:
                pass
        if s.lower().startswith("rgb(") and s.endswith(")"):
            s = s[4:-1]
        nums = _parse_float_tokens(s)
        if nums and len(nums) >= 3:
            arr = nums[:3]
        elif s.lower() in _BRICKCOLOR_RGB:
            return _BRICKCOLOR_RGB[s.lower()]

    if arr is not None:
        mx = max(arr[0], arr[1], arr[2])
        scale = 255.0 if mx <= 1.001 else 1.0
        return (
            max(0, min(255, int(round(arr[0] * scale)))),
            max(0, min(255, int(round(arr[1] * scale)))),
            max(0, min(255, int(round(arr[2] * scale)))),
        )

    if brick_color:
        bc_name = str(brick_color.get("v") if isinstance(brick_color, dict) else brick_color).strip().lower()
        if bc_name in _BRICKCOLOR_RGB:
            return _BRICKCOLOR_RGB[bc_name]
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
# Fast scanline convex polygon filler with optional per-pixel Z-buffer
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


def _fill_poly_zbuf(
    buf: bytearray,
    zbuf: List[float],
    w: int,
    h: int,
    pts3d: List[Tuple[float, float, float]],
    rgba: Tuple[int, int, int, int],
    depth_bias: float = 0.0,
    write_z: bool = True,
) -> None:
    if len(pts3d) < 3:
        return
    r, g, b, a = rgba
    if a <= 0:
        return
    min_y = max(0, int(math.floor(min(p[1] for p in pts3d))))
    max_y = min(h - 1, int(math.ceil(max(p[1] for p in pts3d))))
    if min_y > max_y:
        return

    n = len(pts3d)
    inv_a = 255 - a
    opaque = a >= 250 and write_z
    for y in range(min_y, max_y + 1):
        py = y + 0.5
        xzs: List[Tuple[float, float]] = []
        j = n - 1
        for i in range(n):
            yi, yj = pts3d[i][1], pts3d[j][1]
            if (yi <= py < yj) or (yj <= py < yi):
                t = (py - yi) / (yj - yi)
                xi = pts3d[i][0] + t * (pts3d[j][0] - pts3d[i][0])
                zi = pts3d[i][2] + t * (pts3d[j][2] - pts3d[i][2])
                xzs.append((xi, zi))
            j = i
        if len(xzs) < 2:
            continue
        xzs.sort(key=lambda item: item[0])
        row_pix = y * w
        for k in range(0, len(xzs) - 1, 2):
            xa, za = xzs[k]
            xb, zb = xzs[k + 1]
            x0 = max(0, int(math.floor(xa)))
            x1 = min(w - 1, int(math.ceil(xb)))
            if x0 > x1:
                continue
            span = xb - xa
            dz = (zb - za) / span if span > 1e-5 else 0.0
            z_cur = za + ((x0 + 0.5) - xa) * dz + depth_bias
            if opaque:
                for x in range(x0, x1 + 1):
                    pidx = row_pix + x
                    if z_cur <= zbuf[pidx]:
                        zbuf[pidx] = z_cur
                        idx = pidx * 4
                        buf[idx] = r
                        buf[idx + 1] = g
                        buf[idx + 2] = b
                        buf[idx + 3] = 255
                    z_cur += dz
            else:
                for x in range(x0, x1 + 1):
                    pidx = row_pix + x
                    if z_cur <= zbuf[pidx]:
                        if write_z and a >= 220:
                            zbuf[pidx] = z_cur
                        idx = pidx * 4
                        buf[idx] = (r * a + buf[idx] * inv_a) // 255
                        buf[idx + 1] = (g * a + buf[idx + 1] * inv_a) // 255
                        buf[idx + 2] = (b * a + buf[idx + 2] * inv_a) // 255
                        buf[idx + 3] = 255
                    z_cur += dz


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


def _draw_line_zbuf(
    buf: bytearray,
    zbuf: List[float],
    w: int,
    h: int,
    p0: Tuple[float, float, float],
    p1: Tuple[float, float, float],
    rgba: Tuple[int, int, int, int],
    depth_bias: float = -0.08,
) -> None:
    x0, y0, z0 = p0
    x1, y1, z1 = p1
    dx = x1 - x0
    dy = y1 - y0
    steps = int(max(abs(dx), abs(dy)))
    if steps <= 0 or steps > 4000:
        return
    sx = dx / steps
    sy = dy / steps
    sz = (z1 - z0) / steps
    r, g, b, a = rgba
    inv_a = 255 - a
    x, y, z = x0, y0, z0 + depth_bias
    for _ in range(steps + 1):
        ix, iy = int(round(x)), int(round(y))
        if 0 <= ix < w and 0 <= iy < h:
            pidx = iy * w + ix
            if z <= zbuf[pidx]:
                idx = pidx * 4
                buf[idx] = (r * a + buf[idx] * inv_a) // 255
                buf[idx + 1] = (g * a + buf[idx + 1] * inv_a) // 255
                buf[idx + 2] = (b * a + buf[idx + 2] * inv_a) // 255
        x += sx
        y += sy
        z += sz


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
    "_": ("000", "000", "000", "000", "111"),
    ".": ("000", "000", "000", "000", "010"),
    ":": ("000", "010", "000", "010", "000"),
    "/": ("001", "001", "010", "100", "100"),
    "!": ("010", "010", "010", "000", "010"),
    "$": ("011", "110", "010", "011", "110"),
    "%": ("101", "001", "010", "100", "101"),
}


def _draw_text(buf: bytearray, w: int, h: int, x: int, y: int, text: str, rgb: Tuple[int, int, int], scale: int = 2) -> None:
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

_GROUND_NAMES = {
    "baseplate", "terrain", "ground", "floorplate", "base", "water", "ocean", "sea",
}


def _extract_shape(cls: str, props: dict) -> str:
    if cls == "WedgePart":
        return "Wedge"
    if cls == "CornerWedgePart":
        return "CornerWedge"
    raw = props.get("Shape")
    if isinstance(raw, dict):
        raw = raw.get("v") or raw.get("value") or ""
    s = str(raw or "").lower()
    if "ball" in s or "sphere" in s:
        return "Ball"
    if "cylinder" in s:
        return "Cylinder"
    if "wedge" in s and "corner" in s:
        return "CornerWedge"
    if "wedge" in s:
        return "Wedge"
    return "Block"


def collect_parts_from_tree(
    node: dict,
    out: Optional[list] = None,
    parent_model: Optional[str] = None,
    inside_character: bool = False,
) -> list:
    if out is None:
        out = []
    if not isinstance(node, dict):
        return out
    cls = node.get("className") or ""
    name = node.get("name") or cls
    children = node.get("children") or []

    cur_model = parent_model
    is_char = inside_character
    if cls == "Model":
        cur_model = name
        if any((c.get("className") == "Humanoid") for c in children if isinstance(c, dict)):
            is_char = True

    props = {**(node.get("properties") or {}), **(node.get("structuredProperties") or {})}
    if cls in _PART_CLASSES:
        if is_char and name == "HumanoidRootPart":
            pass
        else:
            try:
                trans = float(props.get("Transparency", 0.0) or 0.0)
            except (TypeError, ValueError):
                trans = 0.0
            if trans < 0.99:
                size = _vec3(props.get("Size"), (4.0, 1.0, 2.0))
                sx, sy, sz = max(0.05, abs(size[0])), max(0.05, abs(size[1])), max(0.05, abs(size[2]))
                cf = _cframe_pos_axes(
                    props.get("CFrame"),
                    pos_val=props.get("Position"),
                    ori_val=props.get("Orientation") or props.get("Rotation"),
                )
                if cf:
                    pos, axes = cf
                else:
                    pos = _vec3(props.get("Position"), (0.0, sy * 0.5, 0.0))
                    axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

                mat_raw = props.get("Material")
                if isinstance(mat_raw, dict):
                    mat_raw = mat_raw.get("v") or mat_raw.get("value")
                mat = str(mat_raw or "SmoothPlastic")
                is_neon = "Neon" in mat
                name_l = name.lower()
                is_ground = (
                    name_l in _GROUND_NAMES
                    or (sy <= 24.0 and max(sx, sz) >= 180.0 and pos[1] <= 4.0)
                )
                default_col = (
                    (91, 154, 76) if cls == "SpawnLocation"
                    else (78, 84, 96) if name_l == "baseplate"
                    else (155, 162, 175)
                )
                out.append({
                    "name": name,
                    "path": node.get("path") or name,
                    "className": cls,
                    "modelName": cur_model,
                    "pos": pos,
                    "size": (sx, sy, sz),
                    "axes": axes,
                    "color": _color_rgb(props.get("Color"), default_col, brick_color=props.get("BrickColor")),
                    "transparency": max(0.0, min(0.98, trans)),
                    "neon": is_neon,
                    "shape": _extract_shape(cls, props),
                    "isGround": is_ground,
                })
    for ch in children:
        collect_parts_from_tree(ch, out, parent_model=cur_model, inside_character=is_char)
    return out


# ---------------------------------------------------------------------------
# 3D Primitive Mesh Generators (Block, Wedge, CornerWedge, Cylinder, Ball)
# ---------------------------------------------------------------------------

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
    ((0, 3, 2, 1), ( 0.0,  0.0, -1.0)),  # -Z back
    ((4, 5, 6, 7), ( 0.0,  0.0,  1.0)),  # +Z front
    ((0, 1, 5, 4), ( 0.0, -1.0,  0.0)),  # -Y bottom
    ((3, 7, 6, 2), ( 0.0,  1.0,  0.0)),  # +Y top
    ((0, 4, 7, 3), (-1.0,  0.0,  0.0)),  # -X left
    ((1, 2, 6, 5), ( 1.0,  0.0,  0.0)),  # +X right
]

# WedgePart: slopes from +Z bottom up to -Z top
_WEDGE_VERTS = [
    (-0.5, -0.5, -0.5),  # 0 back-bottom-left
    ( 0.5, -0.5, -0.5),  # 1 back-bottom-right
    ( 0.5,  0.5, -0.5),  # 2 back-top-right
    (-0.5,  0.5, -0.5),  # 3 back-top-left
    (-0.5, -0.5,  0.5),  # 4 front-bottom-left
    ( 0.5, -0.5,  0.5),  # 5 front-bottom-right
]
_WEDGE_FACES = [
    ((0, 3, 2, 1), (0.0, 0.0, -1.0)),                   # -Z vertical back
    ((0, 1, 5, 4), (0.0, -1.0, 0.0)),                   # -Y bottom
    ((3, 4, 5, 2), (0.0, 0.70710678, 0.70710678)),      # sloped ramp face
    ((0, 4, 3),    (-1.0, 0.0, 0.0)),                   # -X triangle
    ((1, 2, 5),    ( 1.0, 0.0, 0.0)),                   # +X triangle
]

# CornerWedgePart: apex at (+0.5, +0.5, -0.5)
_CORNER_WEDGE_VERTS = [
    (-0.5, -0.5, -0.5),  # 0
    ( 0.5, -0.5, -0.5),  # 1
    (-0.5, -0.5,  0.5),  # 2
    ( 0.5, -0.5,  0.5),  # 3
    ( 0.5,  0.5, -0.5),  # 4 apex
]
_CORNER_WEDGE_FACES = [
    ((0, 1, 3, 2), (0.0, -1.0, 0.0)),
    ((0, 4, 1),    (0.0, 0.0, -1.0)),
    ((1, 4, 3),    (1.0, 0.0, 0.0)),
    ((0, 2, 4),    (-0.7071, 0.7071, 0.0)),
    ((2, 3, 4),    (0.0, 0.7071, 0.7071)),
]


def _build_cylinder_mesh(segments: int = 8):
    # Roblox Cylinder axis is local X (-0.5 .. +0.5); circular cross-section in YZ
    verts = []
    for x in (-0.5, 0.5):
        for i in range(segments):
            ang = (2.0 * math.pi * i) / segments
            verts.append((x, 0.5 * math.cos(ang), 0.5 * math.sin(ang)))
    faces = []
    for i in range(segments):
        nxt = (i + 1) % segments
        ang_mid = (2.0 * math.pi * (i + 0.5)) / segments
        nrm = (0.0, math.cos(ang_mid), math.sin(ang_mid))
        faces.append(((i, nxt, segments + nxt, segments + i), nrm))
    faces.append((tuple(range(segments - 1, -1, -1)), (-1.0, 0.0, 0.0)))
    faces.append((tuple(range(segments, 2 * segments)), (1.0, 0.0, 0.0)))
    return verts, faces


def _build_ball_mesh():
    # Low-poly UV sphere (4 latitude bands x 8 longitude segments)
    lat_bands = 4
    lon_segs = 8
    verts = [(0.0, -0.5, 0.0), (0.0, 0.5, 0.0)]  # 0 = south pole, 1 = north pole
    for lat in range(1, lat_bands):
        theta = -math.pi * 0.5 + (math.pi * lat) / lat_bands
        y = 0.5 * math.sin(theta)
        r = 0.5 * math.cos(theta)
        for lon in range(lon_segs):
            phi = (2.0 * math.pi * lon) / lon_segs
            verts.append((r * math.cos(phi), y, r * math.sin(phi)))
    faces = []
    # South cap
    for lon in range(lon_segs):
        nxt = (lon + 1) % lon_segs
        v0, v1, v2 = 0, 2 + nxt, 2 + lon
        cx = (verts[v0][0] + verts[v1][0] + verts[v2][0]) / 3.0
        cy = (verts[v0][1] + verts[v1][1] + verts[v2][1]) / 3.0
        cz = (verts[v0][2] + verts[v1][2] + verts[v2][2]) / 3.0
        faces.append(((v0, v1, v2), _v_norm((cx, cy, cz))))
    # Middle quads
    for lat in range(lat_bands - 2):
        base = 2 + lat * lon_segs
        top = base + lon_segs
        for lon in range(lon_segs):
            nxt = (lon + 1) % lon_segs
            idxs = (base + lon, base + nxt, top + nxt, top + lon)
            cx = sum(verts[i][0] for i in idxs) * 0.25
            cy = sum(verts[i][1] for i in idxs) * 0.25
            cz = sum(verts[i][2] for i in idxs) * 0.25
            faces.append((idxs, _v_norm((cx, cy, cz))))
    # North cap
    top_base = 2 + (lat_bands - 2) * lon_segs
    for lon in range(lon_segs):
        nxt = (lon + 1) % lon_segs
        v0, v1, v2 = top_base + lon, top_base + nxt, 1
        cx = (verts[v0][0] + verts[v1][0] + verts[v2][0]) / 3.0
        cy = (verts[v0][1] + verts[v1][1] + verts[v2][1]) / 3.0
        cz = (verts[v0][2] + verts[v1][2] + verts[v2][2]) / 3.0
        faces.append(((v0, v1, v2), _v_norm((cx, cy, cz))))
    return verts, faces


_CYLINDER_VERTS, _CYLINDER_FACES = _build_cylinder_mesh(8)
_BALL_VERTS, _BALL_FACES = _build_ball_mesh()


def _mesh_for_shape(shape: str):
    if shape == "Wedge":
        return _WEDGE_VERTS, _WEDGE_FACES
    if shape == "CornerWedge":
        return _CORNER_WEDGE_VERTS, _CORNER_WEDGE_FACES
    if shape == "Cylinder":
        return _CYLINDER_VERTS, _CYLINDER_FACES
    if shape == "Ball":
        return _BALL_VERTS, _BALL_FACES
    return _CUBE_VERTS, _CUBE_FACES


def _compute_auto_camera(
    parts: list,
    preset: str,
    yaw_offset_deg: float = 0.0,
    pitch_offset_deg: float = 0.0,
    zoom: float = 1.0,
) -> Tuple[Tuple[float, float, float], Tuple[float, float, float], bool]:
    # Exclude giant baseplates/grounds when framing foreground structures
    fg = [
        p for p in parts
        if not p.get("isGround")
        and max(p["size"][0], p["size"][2]) <= 180.0
        and not (p["size"][1] <= 2.5 and max(p["size"][0], p["size"][2]) > 85.0)
    ]
    focus_parts = fg if fg else [p for p in parts if max(p["size"][0], p["size"][2]) <= 260.0] or parts

    # Filter out isolated far-flung parked templates/outliers (e.g. ArchivedPalette 500+ studs away)
    near_origin = [
        p for p in focus_parts
        if abs(p["pos"][0]) <= 400.0 and -80.0 <= p["pos"][1] <= 400.0 and abs(p["pos"][2]) <= 400.0
    ]
    if near_origin:
        focus_parts = near_origin

    if len(focus_parts) >= 3:
        xs = sorted(p["pos"][0] for p in focus_parts)
        ys = sorted(p["pos"][1] for p in focus_parts)
        zs = sorted(p["pos"][2] for p in focus_parts)
        mid = len(focus_parts) // 2
        med = (xs[mid], ys[mid], zs[mid])
        dists = sorted(
            math.sqrt((p["pos"][0] - med[0]) ** 2 + (p["pos"][1] - med[1]) ** 2 + (p["pos"][2] - med[2]) ** 2)
            for p in focus_parts
        )
        ref_dist = dists[min(len(dists) - 1, max(1, int(len(dists) * 0.65)))]
        cutoff = max(85.0, ref_dist * 2.6)
        clustered = [
            p for p in focus_parts
            if math.sqrt((p["pos"][0] - med[0]) ** 2 + (p["pos"][1] - med[1]) ** 2 + (p["pos"][2] - med[2]) ** 2) <= cutoff
        ]
        if len(clustered) >= max(2, len(focus_parts) // 2):
            focus_parts = clustered

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
        radius = min(195.0, span * 0.92 + 10.0)

    zoom_clamped = max(0.25, min(3.5, float(zoom or 1.0)))
    radius *= zoom_clamped

    p = (preset or "perspective").lower()
    if p == "top_blueprint":
        return (center[0], center[1] + radius * 1.35, center[2] + 0.01), center, True

    if p == "front":
        base_yaw, base_pitch, dist_mult = 0.0, 14.0, 1.25
    elif p == "iso_nw":
        base_yaw, base_pitch, dist_mult = -135.0, 32.0, 1.35
    elif p == "iso_ne":
        base_yaw, base_pitch, dist_mult = 135.0, 32.0, 1.35
    else:
        base_yaw, base_pitch, dist_mult = 42.0, 27.0, 1.38

    yaw_rad = math.radians(base_yaw + float(yaw_offset_deg or 0.0))
    pitch_rad = math.radians(max(-75.0, min(82.0, base_pitch + float(pitch_offset_deg or 0.0))))
    dist = radius * dist_mult
    horiz = dist * math.cos(pitch_rad)
    cam_pos = (
        center[0] + horiz * math.sin(yaw_rad),
        center[1] + dist * math.sin(pitch_rad),
        center[2] + horiz * math.cos(yaw_rad),
    )
    return cam_pos, center, False


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
    yaw_offset_deg: float = 0.0,
    pitch_offset_deg: float = 0.0,
    zoom: float = 1.0,
) -> Tuple[bytes, dict]:
    w = max(240, min(1280, int(width)))
    h = max(160, min(800, int(height)))
    parts = collect_parts_from_tree(workspace_tree)

    # Sky & horizon gradient tuned from Lighting properties
    lprops = lighting_props or {}
    try:
        clock_time = float(lprops.get("ClockTime", 14.0) or 14.0)
    except (TypeError, ValueError):
        clock_time = 14.0
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
    zbuf = [1e30] * (w * h)
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
        cam_pos, look_at, ortho = _compute_auto_camera(
            parts,
            camera_preset,
            yaw_offset_deg=yaw_offset_deg,
            pitch_offset_deg=pitch_offset_deg,
            zoom=zoom,
        )
        ortho = bool(ortho or explicit_ortho)

    fwd = _v_norm(_v_sub(look_at, cam_pos))
    up_hint = (0.0, 0.0, -1.0) if abs(fwd[1]) > 0.985 else (0.0, 1.0, 0.0)
    right = _v_norm(_v_cross(fwd, up_hint))
    cam_up = _v_norm(_v_cross(right, fwd))

    focal = (h * 0.5) / math.tan(math.radians(max(25.0, min(110.0, fov_deg))) * 0.5)
    cam_dist = max(8.0, math.sqrt(_v_dot(_v_sub(look_at, cam_pos), _v_sub(look_at, cam_pos))))
    ortho_scale = (h * 0.42) / (cam_dist * 0.55)

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

    # Determine ground plane elevation (top of Baseplate or 0.0)
    ground_y = 0.0
    for p in parts:
        if p.get("isGround"):
            ground_y = max(ground_y, p["pos"][1] + p["size"][1] * 0.5)

    sun_dir = _v_norm((0.46, 0.78, 0.42))
    opaque_faces = []
    trans_faces = []
    shadow_polys = []
    model_anchors: Dict[str, Tuple[float, float, float, float]] = {}
    part_anchors: List[Tuple[float, float, float, str]] = []

    for p in parts:
        px, py, pz = p["pos"]
        sx, sy, sz = p["size"]
        ax, ay, az = p["axes"]
        is_ground = bool(p.get("isGround"))

        # Clamp oversized baseplate geometry around look_at so near-plane clipping stays tight
        eff_sx, eff_sy, eff_sz = sx, sy, sz
        eff_px, eff_py, eff_pz = px, py, pz
        if is_ground and max(sx, sz) > 360.0:
            half_cap = max(180.0, min(320.0, cam_dist * 1.8))
            min_gx = max(px - sx * 0.5, look_at[0] - half_cap)
            max_gx = min(px + sx * 0.5, look_at[0] + half_cap)
            min_gz = max(pz - sz * 0.5, look_at[2] - half_cap)
            max_gz = min(pz + sz * 0.5, look_at[2] + half_cap)
            if max_gx > min_gx and max_gz > min_gz:
                eff_px = (min_gx + max_gx) * 0.5
                eff_pz = (min_gz + max_gz) * 0.5
                eff_sx = max_gx - min_gx
                eff_sz = max_gz - min_gz

        mesh_verts, mesh_faces = _mesh_for_shape(p.get("shape", "Block"))
        wverts = []
        cam_verts = []
        for lx, ly, lz in mesh_verts:
            wx = eff_px + ax[0] * (lx * eff_sx) + ay[0] * (ly * eff_sy) + az[0] * (lz * eff_sz)
            wy = eff_py + ax[1] * (lx * eff_sx) + ay[1] * (ly * eff_sy) + az[1] * (lz * eff_sz)
            wz = eff_pz + ax[2] * (lx * eff_sx) + ay[2] * (ly * eff_sy) + az[2] * (lz * eff_sz)
            wv = (wx, wy, wz)
            wverts.append(wv)
            cam_verts.append(camera_coords(wv))

        alpha = max(35, min(255, int(round((1.0 - p["transparency"]) * 255))))
        base_r, base_g, base_b = p["color"]

        if not is_ground and max(sx, sz) < 120.0:
            top_y = py + sy * 0.5
            mdl = p.get("modelName")
            if mdl and mdl not in ("Workspace", "Map", "World", "Folder"):
                prev = model_anchors.get(mdl)
                if prev is None or top_y > prev[1]:
                    model_anchors[mdl] = (px, top_y, pz, sy)
            elif p["name"] not in ("Part", "MeshPart", "WedgePart", "Union", "Baseplate", "Terrain"):
                part_anchors.append((px, top_y + 1.1, pz, p["name"]))

            # Soft ground drop-shadow for foreground objects near ground_y
            bot_y = py - sy * 0.5
            if not ortho and -0.5 <= (bot_y - ground_y) <= 24.0 and max(sx, sz) <= 65.0 and alpha >= 180:
                h_above = max(0.1, bot_y - ground_y)
                sh_dx = -0.32 * h_above
                sh_dz = -0.28 * h_above
                sh_corners = [
                    (px - sx * 0.48 + sh_dx, ground_y + 0.02, pz - sz * 0.48 + sh_dz),
                    (px + sx * 0.48 + sh_dx, ground_y + 0.02, pz - sz * 0.48 + sh_dz),
                    (px + sx * 0.48 + sh_dx, ground_y + 0.02, pz + sz * 0.48 + sh_dz),
                    (px - sx * 0.48 + sh_dx, ground_y + 0.02, pz + sz * 0.48 + sh_dz),
                ]
                sh_cam = clip_near([camera_coords(pt) for pt in sh_corners])
                sh_proj = [project_camera(v) for v in sh_cam]
                sh_proj = [v for v in sh_proj if v is not None]
                if len(sh_proj) >= 3:
                    shadow_polys.append(sh_proj)

        for idxs, lnrm in mesh_faces:
            wnrm = _v_norm((
                ax[0] * lnrm[0] + ay[0] * lnrm[1] + az[0] * lnrm[2],
                ax[1] * lnrm[0] + ay[1] * lnrm[1] + az[1] * lnrm[2],
                ax[2] * lnrm[0] + ay[2] * lnrm[1] + az[2] * lnrm[2],
            ))
            inv_n = 1.0 / len(idxs)
            fc = (
                sum(wverts[i][0] for i in idxs) * inv_n,
                sum(wverts[i][1] for i in idxs) * inv_n,
                sum(wverts[i][2] for i in idxs) * inv_n,
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
                hemi = 0.54 + 0.14 * wnrm[1]
                shade = hemi + 0.54 * ndotl

            cr = max(0, min(255, int(round(base_r * shade))))
            cg = max(0, min(255, int(round(base_g * shade))))
            cb = max(0, min(255, int(round(base_b * shade))))
            depth = sum(v[2] for v in projected) / len(projected)
            face_item = (depth, projected, (cr, cg, cb, alpha), p["neon"], is_ground)
            if alpha >= 245:
                opaque_faces.append(face_item)
            else:
                trans_faces.append(face_item)

    # 1. Rasterize ground/baseplate planes first (background layer, write_z=False so ground never occludes 3D builds)
    ground_faces = [item for item in opaque_faces if item[4]]
    fg_opaque_faces = [item for item in opaque_faces if not item[4]]
    ground_faces.sort(key=lambda item: item[0], reverse=True)
    for _, pts3d, rgba, is_neon, _ in ground_faces:
        _fill_poly_zbuf(buf, zbuf, w, h, pts3d, rgba, depth_bias=0.0, write_z=False)

    # 2. Ground contact shadows (drawn on top of ground plane before foreground 3D geometry)
    for sh_pts3d in shadow_polys[:80]:
        _fill_poly_zbuf(buf, zbuf, w, h, sh_pts3d, (8, 12, 20, 48), depth_bias=0.0, write_z=False)

    # 3. World grid on the ground plane (visible on Baseplate, naturally painted over by 3D objects!)
    if show_grid:
        grid_step = 8 if cam_dist < 140 else 16
        grid_col = (220, 235, 255, 42)
        axis_x_col = (248, 113, 113, 95)
        axis_z_col = (96, 165, 250, 95)
        gx_center = round(look_at[0] / grid_step) * grid_step
        gz_center = round(look_at[2] / grid_step) * grid_step
        grid_y = ground_y + 0.04
        for i in range(-10, 11):
            gx = gx_center + i * grid_step
            gz = gz_center + i * grid_step
            seg_a = clip_near([camera_coords((gx, grid_y, gz_center - 10 * grid_step)), camera_coords((gx, grid_y, gz_center + 10 * grid_step))])
            if len(seg_a) >= 2:
                p0, p1 = project_camera(seg_a[0]), project_camera(seg_a[-1])
                if p0 and p1:
                    col = axis_z_col if abs(gx) < 0.1 else grid_col
                    _draw_line(buf, w, h, p0[0], p0[1], p1[0], p1[1], col)
            seg_b = clip_near([camera_coords((gx_center - 10 * grid_step, grid_y, gz)), camera_coords((gx_center + 10 * grid_step, grid_y, gz))])
            if len(seg_b) >= 2:
                q0, q1 = project_camera(seg_b[0]), project_camera(seg_b[-1])
                if q0 and q1:
                    col = axis_x_col if abs(gz) < 0.1 else grid_col
                    _draw_line(buf, w, h, q0[0], q0[1], q1[0], q1[1], col)

    # 4. Rasterize foreground opaque 3D faces with per-pixel Z-buffer
    fg_opaque_faces.sort(key=lambda item: item[0])
    for _, pts3d, rgba, is_neon, _ in fg_opaque_faces:
        _fill_poly_zbuf(buf, zbuf, w, h, pts3d, rgba, depth_bias=0.0, write_z=True)
        edge_rgba = (255, 255, 255, 115) if is_neon else (12, 16, 24, 78)
        for i in range(len(pts3d)):
            _draw_line_zbuf(buf, zbuf, w, h, pts3d[i], pts3d[(i + 1) % len(pts3d)], edge_rgba, depth_bias=-0.12)

    # 5. Translucent faces sorted back-to-front and depth-tested against opaque Z-buffer
    trans_faces.sort(key=lambda item: item[0], reverse=True)
    for _, pts3d, rgba, is_neon, is_ground in trans_faces:
        _fill_poly_zbuf(buf, zbuf, w, h, pts3d, rgba, depth_bias=0.0, write_z=False)
        edge_rgba = (255, 255, 255, 95) if is_neon else (18, 24, 36, 55)
        for i in range(len(pts3d)):
            _draw_line_zbuf(buf, zbuf, w, h, pts3d[i], pts3d[(i + 1) % len(pts3d)], edge_rgba, depth_bias=-0.06)

    # 5. Non-overlapping callout tags on nearest foreground models/parts
    if show_labels:
        label_candidates = []
        for mdl_name, (mx, my, mz, _) in model_anchors.items():
            pr = project((mx, my + 1.4, mz))
            if pr and 12 <= pr[0] <= w - 70 and 28 <= pr[1] <= h - 24:
                label_candidates.append((pr[2], int(pr[0]), int(pr[1]), mdl_name))
        for px, py_top, pz, pname in part_anchors:
            pr = project((px, py_top, pz))
            if pr and 12 <= pr[0] <= w - 70 and 28 <= pr[1] <= h - 24:
                label_candidates.append((pr[2], int(pr[0]), int(pr[1]), pname))

        label_candidates.sort(key=lambda x: x[0])
        placed_boxes: List[Tuple[int, int, int, int]] = []
        seen_names = set()
        for cz, lx, ly, nm in label_candidates:
            tag = nm[:16]
            if tag.lower() in seen_names:
                continue
            tw = len(tag) * 4 + 10
            box = (lx - 5, ly - 10, lx + tw, ly + 5)
            if any(
                not (box[2] + 4 < b[0] or box[0] - 4 > b[2] or box[3] + 3 < b[1] or box[1] - 3 > b[3])
                for b in placed_boxes
            ):
                continue
            placed_boxes.append(box)
            seen_names.add(tag.lower())
            _fill_poly_alpha(buf, w, h, [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])], (10, 14, 22, 195))
            _draw_rect_border(buf, w, h, box[0], box[1], box[2] - box[0], box[3] - box[1], (88, 166, 255, 120))
            _draw_text(buf, w, h, lx, ly - 6, tag, (226, 238, 255), scale=1)
            if len(placed_boxes) >= 10:
                break

    # 6. 3D XYZ Orientation Gizmo in bottom-left corner
    giz_cx, giz_cy = 32, h - 28
    giz_len = 18.0
    _fill_poly_alpha(buf, w, h, [(8, h - 52), (62, h - 52), (62, h - 6), (8, h - 6)], (9, 13, 21, 165))
    for axis_vec, col, lbl in (
        ((1.0, 0.0, 0.0), (248, 113, 113, 235), "X"),
        ((0.0, 1.0, 0.0), (74, 222, 128, 235), "Y"),
        ((0.0, 0.0, 1.0), (96, 165, 250, 235), "Z"),
    ):
        sx = giz_cx + _v_dot(axis_vec, right) * giz_len
        sy = giz_cy - _v_dot(axis_vec, cam_up) * giz_len
        _draw_line(buf, w, h, giz_cx, giz_cy, sx, sy, col)
        _draw_text(buf, w, h, int(sx) + 2, int(sy) - 3, lbl, col[:3], scale=1)

    # 7. Optional 2D ScreenGui HUD overlay on top of the 3D viewport
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
                fill_a = int(round(nums[3] * 255))
                # Cap full-screen modal/title frames so they never black out the 3D scene
                if rw * rh >= 0.55 * w * h and fill_a > 72:
                    fill_a = 72
                if fill_a > 0:
                    _fill_poly_alpha(
                        buf, w, h,
                        [(rx, ry), (rx + rw, ry), (rx + rw, ry + rh), (rx, ry + rh)],
                        (int(nums[0]), int(nums[1]), int(nums[2]), fill_a),
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

    total_faces = len(opaque_faces) + len(trans_faces)
    # Top-left HUD telemetry bar
    banner = f"CAM:{camera_preset.upper()}  PARTS:{len(parts)}  FACES:{total_faces}  HUD:{hud_count}  {w}X{h}"
    bw = len(banner) * 4 + 16
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
        "facesRasterized": total_faces,
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
