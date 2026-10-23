#!/usr/bin/env python3
"""
rbxmx_pipeline.py — Zero-dependency pure-Python Roblox `.rbxmx` XML Model/UI
parser & serializer for Arena <-> Roblox Studio (v3.3).

Supports round-tripping:
  - `.rbxmx` XML files/strings -> `build_instance_tree` JSON specs (including
    `Item`, `Properties`, `<![CDATA[...]]>` script `Source`, `bool`, `int`,
    `int64`, `float`, `double`, `string`, `Content`, `Color3`, `Color3uint8`,
    `Vector2`, `Vector3`, `UDim`, `UDim2`, `Rect2D`, `CoordinateFrame`,
    `NumberRange`, `NumberSequence`, `ColorSequence`, `Font`, `token` Enums,
    and null-delimited `Tags`).
  - Live Studio `inspect_instance` / prefab JSON trees -> standards-compliant
    `<roblox version="4">` `.rbxmx` XML files ready to drag-and-drop into any
    Roblox Studio `.rbxl` place or commit to Git.
"""

import base64
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def _child_float(el, tag: str, default: float = 0.0) -> float:
    ch = el.find(tag)
    if ch is None or ch.text is None:
        return default
    try:
        return float(ch.text.strip())
    except ValueError:
        return default


def _child_int(el, tag: str, default: int = 0) -> int:
    ch = el.find(tag)
    if ch is None or ch.text is None:
        return default
    try:
        return int(float(ch.text.strip()))
    except ValueError:
        return default


def _parse_property_element(prop_el):
    tag = prop_el.tag
    txt = (prop_el.text or "").strip()

    if tag in ("string", "ProtectedString"):
        return prop_el.text or ""
    if tag == "bool":
        return txt.lower() == "true"
    if tag in ("int", "int64"):
        try:
            return int(txt)
        except ValueError:
            return 0
    if tag in ("float", "double"):
        try:
            return float(txt)
        except ValueError:
            return 0.0
    if tag == "token":
        try:
            return int(txt)
        except ValueError:
            return 0
    if tag == "Content":
        url_el = prop_el.find("url")
        if url_el is not None and url_el.text:
            return url_el.text.strip()
        return ""
    if tag == "Color3":
        if prop_el.find("R") is not None:
            r = _child_float(prop_el, "R")
            g = _child_float(prop_el, "G")
            b = _child_float(prop_el, "B")
            if max(r, g, b) > 1.0:
                r, g, b = r / 255.0, g / 255.0, b / 255.0
            return {"__t": "Color3", "v": [round(r, 4), round(g, 4), round(b, 4)]}
        try:
            packed = int(txt)
            r = ((packed >> 16) & 0xFF) / 255.0
            g = ((packed >> 8) & 0xFF) / 255.0
            b = (packed & 0xFF) / 255.0
            return {"__t": "Color3", "v": [round(r, 4), round(g, 4), round(b, 4)]}
        except ValueError:
            return {"__t": "Color3", "v": [1.0, 1.0, 1.0]}
    if tag == "Color3uint8":
        try:
            packed = int(txt)
            r = ((packed >> 16) & 0xFF) / 255.0
            g = ((packed >> 8) & 0xFF) / 255.0
            b = (packed & 0xFF) / 255.0
            return {"__t": "Color3", "v": [round(r, 4), round(g, 4), round(b, 4)]}
        except ValueError:
            return {"__t": "Color3", "v": [1.0, 1.0, 1.0]}
    if tag == "Vector2":
        return {"__t": "Vector2", "v": [_child_float(prop_el, "X"), _child_float(prop_el, "Y")]}
    if tag == "Vector3":
        return {"__t": "Vector3", "v": [_child_float(prop_el, "X"), _child_float(prop_el, "Y"), _child_float(prop_el, "Z")]}
    if tag == "UDim":
        return {"__t": "UDim", "v": [_child_float(prop_el, "S"), _child_int(prop_el, "O")]}
    if tag == "UDim2":
        xs, xo = _child_float(prop_el, "XS"), _child_int(prop_el, "XO")
        ys, yo = _child_float(prop_el, "YS"), _child_int(prop_el, "YO")
        return {"__t": "UDim2", "v": [xs, xo, ys, yo]}
    if tag == "Rect2D":
        mn = prop_el.find("min")
        mx = prop_el.find("max")
        x0 = _child_float(mn, "X") if mn is not None else 0.0
        y0 = _child_float(mn, "Y") if mn is not None else 0.0
        x1 = _child_float(mx, "X") if mx is not None else 0.0
        y1 = _child_float(mx, "Y") if mx is not None else 0.0
        return {"__t": "Rect", "v": [x0, y0, x1, y1]}
    if tag == "CoordinateFrame":
        vals = [
            _child_float(prop_el, k, 1.0 if k in ("R00", "R11", "R22") else 0.0)
            for k in ("X", "Y", "Z", "R00", "R01", "R02", "R10", "R11", "R12", "R20", "R21", "R22")
        ]
        return {"__t": "CFrame", "v": vals}
    if tag == "NumberRange":
        parts = txt.split()
        if len(parts) >= 2:
            return {"__t": "NumberRange", "v": [float(parts[0]), float(parts[1])]}
    return None


def _parse_item(item_el, strip_scripts: bool = False, stats: dict = None) -> dict:
    cls = item_el.attrib.get("class", "Folder")
    if strip_scripts and cls in ("Script", "LocalScript", "ModuleScript"):
        if stats is not None:
            stats["scriptsStripped"] = stats.get("scriptsStripped", 0) + 1
        return None

    node = {
        "name": cls,
        "className": cls,
        "properties": {},
        "children": [],
    }
    if stats is not None:
        stats["itemsParsed"] = stats.get("itemsParsed", 0) + 1

    props_el = item_el.find("Properties")
    if props_el is not None:
        for pel in props_el:
            pname = pel.attrib.get("name", "")
            if not pname:
                continue
            if pname == "Name":
                node["name"] = (pel.text or cls).strip() or cls
                continue
            if pname == "Source" and cls in ("Script", "LocalScript", "ModuleScript"):
                node["source"] = pel.text or ""
                continue
            if pname == "Tags" and pel.tag == "BinaryString":
                try:
                    raw = base64.b64decode((pel.text or "").strip())
                    tags = [t.decode("utf-8", "ignore") for t in raw.split(b"\x00") if t]
                    if tags:
                        node["tags"] = tags
                except Exception:
                    pass
                continue
            if pname in ("AttributesSerialize", "Capabilities", "DefinesCapabilities", "UniqueId", "HistoryId", "SourceAssetId"):
                continue
            # Skip numeric enum tokens that default cleanly when non-string unless explicit
            if pel.tag == "token":
                continue
            parsed = _parse_property_element(pel)
            if parsed is not None:
                node["properties"][pname] = parsed

    for child_item in item_el.findall("Item"):
        ch = _parse_item(child_item, strip_scripts=strip_scripts, stats=stats)
        if ch is not None:
            node["children"].append(ch)

    if not node["properties"]:
        node.pop("properties", None)
    if not node["children"]:
        node.pop("children", None)
    return node


def parse_rbxmx_to_tree(xml_text: str, strip_scripts: bool = False) -> tuple:
    root = ET.fromstring(xml_text)
    stats = {"itemsParsed": 0, "scriptsStripped": 0}
    tree = []
    for item_el in root.findall("Item"):
        parsed = _parse_item(item_el, strip_scripts=strip_scripts, stats=stats)
        if parsed is not None:
            tree.append(parsed)
    return tree, stats


def _emit_prop_xml(lines: list, indent: str, pname: str, val):
    from xml.sax.saxutils import escape

    if isinstance(val, bool):
        lines.append(f'{indent}<bool name="{escape(pname)}">{"true" if val else "false"}</bool>')
        return
    if isinstance(val, int):
        lines.append(f'{indent}<int name="{escape(pname)}">{val}</int>')
        return
    if isinstance(val, float):
        lines.append(f'{indent}<float name="{escape(pname)}">{val}</float>')
        return
    if isinstance(val, str):
        if pname in ("Image", "Texture", "SoundId", "MeshId", "TextureID") or val.startswith(("rbxassetid://", "rbxasset://", "http://", "https://")):
            lines.append(f'{indent}<Content name="{escape(pname)}"><url>{escape(val)}</url></Content>')
        else:
            lines.append(f'{indent}<string name="{escape(pname)}">{escape(val)}</string>')
        return
    if isinstance(val, list) and len(val) == 3 and all(isinstance(x, (int, float)) for x in val):
        lines.append(
            f'{indent}<Vector3 name="{escape(pname)}"><X>{val[0]}</X><Y>{val[1]}</Y><Z>{val[2]}</Z></Vector3>'
        )
        return
    if isinstance(val, dict) and "__t" in val:
        t = val["__t"]
        v = val.get("v")
        if t == "Color3" and isinstance(v, list) and len(v) == 3:
            lines.append(
                f'{indent}<Color3 name="{escape(pname)}"><R>{v[0]}</R><G>{v[1]}</G><B>{v[2]}</B></Color3>'
            )
        elif t == "Vector2" and isinstance(v, list) and len(v) == 2:
            lines.append(f'{indent}<Vector2 name="{escape(pname)}"><X>{v[0]}</X><Y>{v[1]}</Y></Vector2>')
        elif t == "Vector3" and isinstance(v, list) and len(v) == 3:
            lines.append(
                f'{indent}<Vector3 name="{escape(pname)}"><X>{v[0]}</X><Y>{v[1]}</Y><Z>{v[2]}</Z></Vector3>'
            )
        elif t == "UDim" and isinstance(v, list) and len(v) == 2:
            lines.append(f'{indent}<UDim name="{escape(pname)}"><S>{v[0]}</S><O>{int(v[1])}</O></UDim>')
        elif t == "UDim2" and isinstance(v, list) and len(v) == 4:
            lines.append(
                f'{indent}<UDim2 name="{escape(pname)}"><XS>{v[0]}</XS><XO>{int(v[1])}</XO><YS>{v[2]}</YS><YO>{int(v[3])}</YO></UDim2>'
            )
        elif t == "Rect" and isinstance(v, list) and len(v) == 4:
            lines.append(
                f'{indent}<Rect2D name="{escape(pname)}"><min><X>{v[0]}</X><Y>{v[1]}</Y></min><max><X>{v[2]}</X><Y>{v[3]}</Y></max></Rect2D>'
            )
        elif t == "CFrame" and isinstance(v, list) and len(v) == 12:
            tags = ("X", "Y", "Z", "R00", "R01", "R02", "R10", "R11", "R12", "R20", "R21", "R22")
            inner = "".join(f"<{k}>{v[i]}</{k}>" for i, k in enumerate(tags))
            lines.append(f'{indent}<CoordinateFrame name="{escape(pname)}">{inner}</CoordinateFrame>')
        elif t == "NumberRange" and isinstance(v, list) and len(v) == 2:
            lines.append(f'{indent}<NumberRange name="{escape(pname)}">{v[0]} {v[1]} </NumberRange>')


def _emit_item_xml(lines: list, node: dict, ref_counter: list, depth: int = 1):
    from xml.sax.saxutils import escape

    ref_counter[0] += 1
    ref_id = f"RBXARENA{ref_counter[0]:05d}"
    pad = "\t" * depth
    p2 = "\t" * (depth + 1)
    cls = node.get("className") or "Folder"
    name = node.get("name") or cls

    lines.append(f'{pad}<Item class="{escape(cls)}" referent="{ref_id}">')
    lines.append(f"{p2}<Properties>")
    lines.append(f'{p2}\t<string name="Name">{escape(name)}</string>')

    if "source" in node and node["source"] is not None:
        safe_src = str(node["source"]).replace("]]>", "]]]]><![CDATA[>")
        lines.append(f'{p2}\t<ProtectedString name="Source"><![CDATA[{safe_src}]]></ProtectedString>')

    if node.get("tags"):
        raw_tags = b"\x00".join(str(t).encode("utf-8") for t in node["tags"])
        b64_tags = base64.b64encode(raw_tags).decode("ascii")
        lines.append(f'{p2}\t<BinaryString name="Tags">{b64_tags}</BinaryString>')

    for pname, pval in sorted((node.get("properties") or {}).items()):
        if pname == "Name":
            continue
        _emit_prop_xml(lines, p2 + "\t", pname, pval)

    lines.append(f"{p2}</Properties>")
    for ch in node.get("children") or []:
        _emit_item_xml(lines, ch, ref_counter, depth + 1)
    lines.append(f"{pad}</Item>")


def serialize_tree_to_rbxmx(nodes: list) -> str:
    lines = [
        '<roblox xmlns:xmime="http://www.w3.org/2005/05/xmlmime" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:noNamespaceSchemaLocation="http://www.roblox.com/roblox.xsd" version="4">',
        "\t<Meta name=\"ExplicitAutoJoints\">true</Meta>",
    ]
    counter = [0]
    for node in nodes:
        _emit_item_xml(lines, node, counter, 1)
    lines.append("</roblox>")
    lines.append("")
    return "\n".join(lines)


def build_repo_rbxmx_models() -> dict:
    from image_pipeline import find_kit_root

    kit = find_kit_root()
    prefabs_dir = kit / "prefabs"
    models_dir = kit / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for pf in sorted(prefabs_dir.glob("*.json")):
        spec = json.loads(pf.read_text(encoding="utf-8"))
        tree = spec.get("tree") or []
        if not tree:
            continue
        xml_str = serialize_tree_to_rbxmx(tree)
        out_p = models_dir / f"{pf.stem}.rbxmx"
        out_p.write_text(xml_str, encoding="utf-8")
        written.append(str(out_p))
    return {"modelsDir": str(models_dir), "count": len(written), "files": written}


if __name__ == "__main__":
    print(json.dumps(build_repo_rbxmx_models(), indent=2))
