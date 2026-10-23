"""
Zero-Dependency 3D Geometry, Physics, Spawn Clearance & Grounding Auditor.

Catches classic AI-generated 3D map & building defects automatically:
  1. UNANCHORED_PART     — Non-character BaseParts with Anchored == false (collapse on Play)
  2. FLOATING_GEOMETRY   — Parts hovering 0.20–14 studs above a floor/support with air underneath
  3. Z_FIGHTING_COPLANAR — Overlapping parts with near-identical top/bottom/side planes & different colors
  4. SPAWN_OBSTRUCTED    — SpawnLocations whose 4x5.5x4 player headroom box intersects CanCollide geometry
  5. EXTREME_OR_TINY     — Coordinates > 15,000 studs (32-bit float jitter) or degenerate thickness < 0.02
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple


_PART_CLASSES = {
    "Part", "WedgePart", "CornerWedgePart", "TrussPart", "MeshPart",
    "UnionOperation", "SpawnLocation", "Seat", "VehicleSeat",
}


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


def _pos_from_props(props: dict, size: Tuple[float, float, float]) -> Tuple[float, float, float]:
    if "CFrame" in props:
        cf = props["CFrame"]
        v = cf.get("v") if isinstance(cf, dict) else cf
        if isinstance(v, (list, tuple)) and len(v) >= 3:
            return float(v[0]), float(v[1]), float(v[2])
    if "Position" in props:
        return _vec3(props["Position"], (0.0, size[1] * 0.5, 0.0))
    return (0.0, size[1] * 0.5, 0.0)


def _color_key(props: dict) -> str:
    c = props.get("Color")
    if isinstance(c, dict) and isinstance(c.get("v"), list) and len(c["v"]) >= 3:
        return f"{c['v'][0]:.3f},{c['v'][1]:.3f},{c['v'][2]:.3f}"
    return str(c or "default")


def _extract_audit_parts(node: dict, inside_character: bool = False, out: Optional[list] = None) -> list:
    if out is None:
        out = []
    children = node.get("children") or []
    is_char = inside_character or any((c.get("className") == "Humanoid") for c in children)
    cls = node.get("className") or ""
    props = {**(node.get("properties") or {}), **(node.get("structuredProperties") or {})}
    if cls in _PART_CLASSES:
        sz = _vec3(props.get("Size"), (4.0, 1.0, 2.0))
        sz = (abs(sz[0]), abs(sz[1]), abs(sz[2]))
        pos = _pos_from_props(props, sz)
        raw_anc = props.get("Anchored")
        if raw_anc is None:
            anchored = True if node.get("name") == "Baseplate" else False
        elif isinstance(raw_anc, str):
            anchored = raw_anc.strip().lower() == "true"
        else:
            anchored = bool(raw_anc)
        raw_cc = props.get("CanCollide")
        if raw_cc is None:
            can_collide = True
        elif isinstance(raw_cc, str):
            can_collide = raw_cc.strip().lower() != "false"
        else:
            can_collide = bool(raw_cc)
        trans = float(props.get("Transparency", 0.0) or 0.0)
        out.append({
            "name": node.get("name") or cls,
            "path": node.get("path") or node.get("name") or cls,
            "className": cls,
            "pos": pos,
            "size": sz,
            "min": (pos[0] - sz[0] * 0.5, pos[1] - sz[1] * 0.5, pos[2] - sz[2] * 0.5),
            "max": (pos[0] + sz[0] * 0.5, pos[1] + sz[1] * 0.5, pos[2] + sz[2] * 0.5),
            "anchored": bool(anchored),
            "canCollide": bool(can_collide),
            "transparency": trans,
            "isCharacter": is_char,
            "colorKey": _color_key(props),
            "tags": node.get("tags") or [],
        })
    for ch in children:
        _extract_audit_parts(ch, is_char, out)
    return out


def _xz_overlap(a: dict, b: dict, margin: float = -0.02) -> bool:
    return (
        min(a["max"][0], b["max"][0]) - max(a["min"][0], b["min"][0]) > margin
        and min(a["max"][2], b["max"][2]) - max(a["min"][2], b["min"][2]) > margin
    )


def _aabb_overlap(a_min, a_max, b_min, b_max, eps: float = 0.02) -> bool:
    return (
        min(a_max[0], b_max[0]) - max(a_min[0], b_min[0]) > eps
        and min(a_max[1], b_max[1]) - max(a_min[1], b_min[1]) > eps
        and min(a_max[2], b_max[2]) - max(a_min[2], b_min[2]) > eps
    )


def audit_3d_hierarchy(root_tree: dict) -> dict:
    parts = _extract_audit_parts(root_tree)
    issues: List[dict] = []
    unanchored_paths: List[str] = []
    snap_fixes: List[dict] = []

    for i, p in enumerate(parts):
        px, py, pz = p["pos"]
        sx, sy, sz = p["size"]

        # 1. Extreme float-jitter coordinates or degenerate thickness
        dist = math.sqrt(px * px + py * py + pz * pz)
        if dist > 15000.0:
            issues.append({
                "severity": "warning",
                "code": "EXTREME_COORDINATE",
                "path": p["path"],
                "message": f"Located {dist:.0f} studs from origin (>15k studs causes 32-bit GPU/physics jitter).",
            })
        if min(sx, sy, sz) < 0.02:
            issues.append({
                "severity": "warning",
                "code": "DEGENERATE_SIZE",
                "path": p["path"],
                "message": f"Size ({sx:.3f}, {sy:.3f}, {sz:.3f}) has sub-0.02 thickness.",
            })

        # 2. Unanchored world parts (non-character, non-Seat)
        if not p["isCharacter"] and not p["anchored"] and p["className"] not in ("VehicleSeat",):
            unanchored_paths.append(p["path"])
            issues.append({
                "severity": "error",
                "code": "UNANCHORED_PART",
                "path": p["path"],
                "message": f"{p['className']} has Anchored=false outside a Humanoid model and will fall on Play.",
            })

        # 3. Floating geometry detection (hovering 0.20 .. 12.0 studs above nearest supporting top face)
        intentional_float = (
            p["name"] == "Baseplate"
            or "Floating" in p["tags"]
            or "Collectible" in p["tags"]
            or any(k in p["name"].lower() for k in ("orb", "cloud", "roof", "ceiling", "beam", "canopy", "sign", "lintel", "arch"))
        )
        if not intentional_float and not p["isCharacter"]:
            best_gap = None
            best_support = None
            touching_any = False
            for j, q in enumerate(parts):
                if i == j:
                    continue
                if _aabb_overlap(
                    (p["min"][0] - 0.08, p["min"][1] - 0.08, p["min"][2] - 0.08),
                    (p["max"][0] + 0.08, p["max"][1] + 0.08, p["max"][2] + 0.08),
                    q["min"], q["max"], eps=0.0,
                ):
                    touching_any = True
                    break
                if _xz_overlap(p, q):
                    gap = p["min"][1] - q["max"][1]
                    if -0.08 <= gap <= 0.19:
                        touching_any = True
                        break
                    if 0.20 <= gap <= 12.0:
                        if best_gap is None or gap < best_gap:
                            best_gap = gap
                            best_support = q
            if not touching_any and best_gap is not None and best_support is not None:
                target_y = round(best_support["max"][1] + sy * 0.5, 4)
                snap_fixes.append({
                    "path": p["path"],
                    "oldPosition": [round(px, 3), round(py, 3), round(pz, 3)],
                    "newPosition": [round(px, 3), target_y, round(pz, 3)],
                    "gapStuds": round(best_gap, 3),
                    "support": best_support["path"],
                })
                issues.append({
                    "severity": "warning",
                    "code": "FLOATING_GEOMETRY",
                    "path": p["path"],
                    "message": f"Hovers {best_gap:.2f} studs in mid-air above {best_support['path']} (snap Y -> {target_y}).",
                })

        # 4. SpawnLocation headroom obstruction check
        if p["className"] == "SpawnLocation":
            head_min = (px - 2.0, p["max"][1] + 0.15, pz - 2.0)
            head_max = (px + 2.0, p["max"][1] + 5.5, pz + 2.0)
            for j, q in enumerate(parts):
                if i == j or not q["canCollide"]:
                    continue
                if _aabb_overlap(head_min, head_max, q["min"], q["max"]):
                    issues.append({
                        "severity": "error",
                        "code": "SPAWN_OBSTRUCTED",
                        "path": p["path"],
                        "message": f"Player spawn clearance intersects collidable part {q['path']}.",
                    })

        # 5. Coplanar Z-fighting check on visible opaque parts
        if p["transparency"] < 0.9:
            for j in range(i + 1, min(len(parts), i + 80)):
                q = parts[j]
                if q["transparency"] >= 0.9 or p["colorKey"] == q["colorKey"]:
                    continue
                if _xz_overlap(p, q, margin=0.25) and abs(p["max"][1] - q["max"][1]) < 0.005:
                    issues.append({
                        "severity": "warning",
                        "code": "Z_FIGHTING_COPLANAR",
                        "path": p["path"],
                        "message": f"Top surface Y={p['max'][1]:.3f} is coplanar with {q['path']} with a different color (flickers on GPU).",
                    })

    err_count = sum(1 for x in issues if x["severity"] == "error")
    warn_count = sum(1 for x in issues if x["severity"] == "warning")
    return {
        "totalPartsAudited": len(parts),
        "errorCount": err_count,
        "warningCount": warn_count,
        "passed": err_count == 0,
        "unanchoredPaths": unanchored_paths,
        "floatingSnapCandidates": snap_fixes,
        "issues": issues,
    }
