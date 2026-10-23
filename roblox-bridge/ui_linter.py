#!/usr/bin/env python3
"""
ui_linter.py — Multi-Device Responsive Roblox UI Layout Solver & UX Linter (v3.3)

Evaluates Roblox `ScreenGui` / `GuiObject` trees across multiple target viewports:
  - Desktop HD (`1920x1080`)
  - Tablet / iPad (`1024x768`)
  - Mobile Landscape (`844x390`)

Supports:
  - `UDim2` (`[xs, xo, ys, yo]`), `AnchorPoint` (`[ax, ay]`), `UIScale` (`Scale`)
  - `UIPadding`, `UIListLayout`, `UIGridLayout`, `UIAspectRatioConstraint`
  - Visual render descriptors (colors, corners, strokes, text, images) for the
    Control Center HTML5 multi-device preview stage
  - Actionable UX & layout lint checks:
    * `viewport_overflow`: element extends outside screen bounds
    * `missing_responsive_scale`: large pixel layout without a `UIScale` ancestor
    * `reset_on_spawn_enabled`: `ScreenGui.ResetOnSpawn == true` on persistent UI
    * `small_touch_target`: interactive `GuiButton` smaller than `36x36` on mobile
    * `sibling_overlap`: manual sibling elements unintentionally colliding
    * `stretched_9slice_risk`: wide button/panel texture without `ScaleType.Slice`
"""

VIEWPORT_PRESETS = {
    "desktop": (1920.0, 1080.0),
    "tablet": (1024.0, 768.0),
    "mobile": (844.0, 390.0),
}

GUI_OBJECT_CLASSES = {
    "Frame", "ScrollingFrame", "CanvasGroup", "TextLabel", "TextButton",
    "TextBox", "ImageLabel", "ImageButton", "ViewportFrame", "VideoFrame",
}
BUTTON_CLASSES = {"TextButton", "ImageButton"}


def _unwrap_vec(val, default):
    if isinstance(val, dict) and "v" in val and isinstance(val["v"], list):
        return [float(x) for x in val["v"]]
    if isinstance(val, list):
        return [float(x) for x in val]
    return list(default)


def _color_css(val, alpha: float = 1.0) -> str:
    rgb = _unwrap_vec(val, [1.0, 1.0, 1.0])
    if len(rgb) >= 3:
        mx = max(rgb[0], rgb[1], rgb[2])
        r = int(round(rgb[0] * (255.0 if mx <= 1.0 else 1.0)))
        g = int(round(rgb[1] * (255.0 if mx <= 1.0 else 1.0)))
        b = int(round(rgb[2] * (255.0 if mx <= 1.0 else 1.0)))
        return f"rgba({max(0,min(255,r))},{max(0,min(255,g))},{max(0,min(255,b))},{max(0.0,min(1.0,alpha)):.3f})"
    return f"rgba(255,255,255,{alpha:.3f})"


def _find_child_class(node: dict, cls: str):
    for ch in node.get("children") or []:
        if ch.get("className") == cls:
            return ch
    return None


def _enum_str(val) -> str:
    if isinstance(val, dict) and val.get("__t") == "Enum":
        return str(val.get("v") or "")
    return str(val or "")


def audit_tree_on_viewport(tree: list, vw: float, vh: float, viewport_name: str = "desktop") -> dict:
    elements = []
    issues = []

    def add_issue(severity: str, code: str, path: str, message: str, rect=None):
        issues.append({
            "severity": severity,
            "code": code,
            "viewport": viewport_name,
            "path": path,
            "message": message,
            "rect": [round(x, 1) for x in rect] if rect else None,
        })

    def walk_gui(node: dict, path: str, parent_rect: tuple, eff_scale: float, has_scale_anc: bool):
        cls = node.get("className") or "Instance"
        props = {**(node.get("properties") or {}), **(node.get("structuredProperties") or {})}
        children = node.get("children") or []

        local_scale_node = _find_child_class(node, "UIScale")
        if local_scale_node:
            has_scale_anc = True
            sc_val = float((local_scale_node.get("properties") or {}).get("Scale", 1.0))
            eff_scale = eff_scale * sc_val

        if cls == "ScreenGui":
            if props.get("ResetOnSpawn", True) is True:
                add_issue(
                    "warning",
                    "reset_on_spawn_enabled",
                    path,
                    "ScreenGui has ResetOnSpawn=true; player respawn will destroy runtime UI state unless intentional.",
                )
            for ch in children:
                walk_gui(ch, f"{path}.{ch.get('name','Child')}", (0.0, 0.0, vw, vh), eff_scale, has_scale_anc)
            return

        if cls not in GUI_OBJECT_CLASSES:
            for ch in children:
                walk_gui(ch, f"{path}.{ch.get('name','Child')}", parent_rect, eff_scale, has_scale_anc)
            return

        if props.get("Visible") is False:
            return

        px, py, pw, ph = parent_rect
        pos = _unwrap_vec(props.get("Position"), [0, 0, 0, 0])
        sz = _unwrap_vec(props.get("Size"), [0, 100, 0, 100])
        anc = _unwrap_vec(props.get("AnchorPoint"), [0, 0])

        w = pw * sz[0] + sz[1] * eff_scale
        h = ph * sz[2] + sz[3] * eff_scale
        aspect_node = _find_child_class(node, "UIAspectRatioConstraint")
        if aspect_node:
            ratio = float((aspect_node.get("properties") or {}).get("AspectRatio", 1.0))
            if ratio > 0 and h > 0:
                w = min(w, h * ratio)

        x = px + pw * pos[0] + pos[1] * eff_scale - anc[0] * w
        y = py + ph * pos[2] + pos[3] * eff_scale - anc[1] * h

        if not has_scale_anc and (sz[1] >= 540 or sz[3] >= 360) and viewport_name == "mobile":
            add_issue(
                "warning",
                "missing_responsive_scale",
                path,
                f"Fixed offset size ({int(sz[1])}x{int(sz[3])}px) has no ancestor UIScale for mobile viewports.",
                (x, y, w, h),
            )

        if x < -4 or y < -4 or (x + w) > (vw + 4) or (y + h) > (vh + 4):
            add_issue(
                "error" if viewport_name == "mobile" else "warning",
                "viewport_overflow",
                path,
                f"Bounds [{round(x)}, {round(y)}, {round(w)}x{round(h)}] overflow {int(vw)}x{int(vh)} {viewport_name} viewport.",
                (x, y, w, h),
            )

        if cls in BUTTON_CLASSES and viewport_name == "mobile" and (w < 36 or h < 36):
            add_issue(
                "warning",
                "small_touch_target",
                path,
                f"Button touch target is {round(w)}x{round(h)}px (< 36x36px mobile minimum).",
                (x, y, w, h),
            )

        img_uri = str(props.get("Image") or "")
        scale_type = _enum_str(props.get("ScaleType"))
        if img_uri and any(k in img_uri.lower() for k in ("btn_", "button", "panel", "border", "banner")):
            if w > 120 and "Slice" not in scale_type and w / max(h, 1.0) > 2.4:
                add_issue(
                    "info",
                    "stretched_9slice_risk",
                    path,
                    "Wide button/panel image uses default Stretch instead of ScaleType.Slice; corners may distort.",
                    (x, y, w, h),
                )

        bg_trans = float(props.get("BackgroundTransparency", 0.0 if cls in ("Frame", "TextButton", "TextBox") else 1.0))
        corner_node = _find_child_class(node, "UICorner")
        radius_px = 0
        if corner_node:
            cr = _unwrap_vec((corner_node.get("properties") or {}).get("CornerRadius"), [0, 8])
            radius_px = int(round(min(w, h) * cr[0] + cr[1] * eff_scale))
        stroke_node = _find_child_class(node, "UIStroke")
        stroke_css = None
        if stroke_node:
            sp = stroke_node.get("properties") or {}
            st_alpha = 1.0 - float(sp.get("Transparency", 0.0))
            stroke_css = f"{max(1, round(float(sp.get('Thickness', 1.0))))}px solid {_color_css(sp.get('Color', [1,1,1]), st_alpha)}"

        elements.append({
            "path": path,
            "name": node.get("name") or cls,
            "className": cls,
            "rect": [round(x, 1), round(y, 1), round(w, 1), round(h, 1)],
            "bg": _color_css(props.get("BackgroundColor3", [0.12, 0.15, 0.22]), 1.0 - bg_trans),
            "radius": radius_px,
            "border": stroke_css,
            "text": props.get("Text"),
            "textColor": _color_css(props.get("TextColor3", [1, 1, 1]), 1.0 - float(props.get("TextTransparency", 0.0))) if props.get("Text") is not None else None,
            "textSize": int(props.get("TextSize", 15)),
            "image": img_uri or None,
        })

        # Layout children (`UIListLayout` / `UIGridLayout` or free positioning)
        pad_node = _find_child_class(node, "UIPadding")
        pad_l = pad_t = pad_r = pad_b = 0.0
        if pad_node:
            pp = pad_node.get("properties") or {}
            pad_l = _unwrap_vec(pp.get("PaddingLeft"), [0, 0])[1] * eff_scale
            pad_r = _unwrap_vec(pp.get("PaddingRight"), [0, 0])[1] * eff_scale
            pad_t = _unwrap_vec(pp.get("PaddingTop"), [0, 0])[1] * eff_scale
            pad_b = _unwrap_vec(pp.get("PaddingBottom"), [0, 0])[1] * eff_scale

        inner_rect = (x + pad_l, y + pad_t, max(0.0, w - pad_l - pad_r), max(0.0, h - pad_t - pad_b))
        list_layout = _find_child_class(node, "UIListLayout")
        grid_layout = _find_child_class(node, "UIGridLayout")

        gui_kids = [c for c in children if (c.get("className") in GUI_OBJECT_CLASSES and (c.get("properties") or {}).get("Visible") is not False)]

        if grid_layout:
            gp = grid_layout.get("properties") or {}
            csz = _unwrap_vec(gp.get("CellSize"), [0, 100, 0, 100])
            cpad = _unwrap_vec(gp.get("CellPadding"), [0, 8, 0, 8])
            cw = inner_rect[2] * csz[0] + csz[1] * eff_scale
            ch_h = inner_rect[3] * csz[2] + csz[3] * eff_scale
            gx = inner_rect[2] * cpad[0] + cpad[1] * eff_scale
            gy = inner_rect[3] * cpad[2] + cpad[3] * eff_scale
            cols = max(1, int((inner_rect[2] + gx) // max(1.0, cw + gx)))
            for idx, ch in enumerate(gui_kids):
                col = idx % cols
                row = idx // cols
                cp_copy = dict(ch)
                cp_props = dict(ch.get("properties") or {})
                cp_props["Position"] = {"__t": "UDim2", "v": [0, col * (cw + gx) / max(eff_scale, 0.001), 0, row * (ch_h + gy) / max(eff_scale, 0.001)]}
                cp_props["Size"] = {"__t": "UDim2", "v": [0, cw / max(eff_scale, 0.001), 0, ch_h / max(eff_scale, 0.001)]}
                cp_props["AnchorPoint"] = {"__t": "Vector2", "v": [0, 0]}
                cp_copy["properties"] = cp_props
                walk_gui(cp_copy, f"{path}.{ch.get('name','Item')}", inner_rect, eff_scale, has_scale_anc)
        elif list_layout:
            lp = list_layout.get("properties") or {}
            dir_str = _enum_str(lp.get("FillDirection"))
            gap_ud = _unwrap_vec(lp.get("Padding"), [0, 0])
            horiz = "Horizontal" in dir_str
            gap = (inner_rect[2] if horiz else inner_rect[3]) * gap_ud[0] + gap_ud[1] * eff_scale
            cursor = 0.0
            for ch in gui_kids:
                cp_copy = dict(ch)
                cp_props = dict(ch.get("properties") or {})
                ksz = _unwrap_vec(cp_props.get("Size"), [0, 100, 0, 32])
                kw = inner_rect[2] * ksz[0] + ksz[1] * eff_scale
                kh = inner_rect[3] * ksz[2] + ksz[3] * eff_scale
                cp_props["AnchorPoint"] = {"__t": "Vector2", "v": [0, 0]}
                if horiz:
                    cp_props["Position"] = {"__t": "UDim2", "v": [0, cursor / max(eff_scale, 0.001), 0, 0]}
                    cursor += kw + gap
                else:
                    cp_props["Position"] = {"__t": "UDim2", "v": [0, 0, 0, cursor / max(eff_scale, 0.001)]}
                    cursor += kh + gap
                cp_copy["properties"] = cp_props
                walk_gui(cp_copy, f"{path}.{ch.get('name','Row')}", inner_rect, eff_scale, has_scale_anc)
        else:
            start_idx = len(elements)
            child_rects = []
            for ch in gui_kids:
                before_len = len(elements)
                walk_gui(ch, f"{path}.{ch.get('name','Child')}", inner_rect, eff_scale, has_scale_anc)
                if len(elements) > before_len:
                    child_rects.append(elements[before_len])
            # Check unintentional sibling collisions among non-full-overlay siblings
            for i in range(len(child_rects)):
                ra = child_rects[i]["rect"]
                area_a = max(1.0, ra[2] * ra[3])
                if ra[2] >= inner_rect[2] * 0.92 and ra[3] >= inner_rect[3] * 0.92:
                    continue
                for j in range(i + 1, len(child_rects)):
                    rb = child_rects[j]["rect"]
                    if rb[2] >= inner_rect[2] * 0.92 and rb[3] >= inner_rect[3] * 0.92:
                        continue
                    ix = max(0.0, min(ra[0] + ra[2], rb[0] + rb[2]) - max(ra[0], rb[0]))
                    iy = max(0.0, min(ra[1] + ra[3], rb[1] + rb[3]) - max(ra[1], rb[1]))
                    inter = ix * iy
                    if inter > 0.35 * min(area_a, max(1.0, rb[2] * rb[3])):
                        add_issue(
                            "warning",
                            "sibling_overlap",
                            f"{child_rects[i]['path']} <-> {child_rects[j]['path']}",
                            "Sibling UI elements overlap by >35% without UIListLayout/UIGridLayout or full-frame overlay sizing.",
                            ra,
                        )
            _ = start_idx

    for root_node in tree:
        rname = root_node.get("name") or root_node.get("className") or "Root"
        walk_gui(root_node, rname, (0.0, 0.0, vw, vh), 1.0, False)

    return {
        "viewport": viewport_name,
        "width": int(vw),
        "height": int(vh),
        "elementCount": len(elements),
        "issueCount": len(issues),
        "issues": issues,
        "elements": elements,
    }


def run_multi_viewport_audit(tree: list, viewports: list = None) -> dict:
    chosen = viewports or ["desktop", "tablet", "mobile"]
    reports = {}
    total_issues = 0
    for vp in chosen:
        w, h = VIEWPORT_PRESETS.get(vp, (1920.0, 1080.0))
        rep = audit_tree_on_viewport(tree, w, h, vp)
        reports[vp] = rep
        total_issues += rep["issueCount"]
    return {
        "ok": total_issues == 0,
        "totalIssues": total_issues,
        "viewports": reports,
    }
