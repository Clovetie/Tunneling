"""Asset-reference auditor: what a place points at, versus what the kit actually ships.

The classic defect this catches is an image that was deleted from `roblox-kit/` while a
place still points at it: `rbxasset://arena_assets/<rel>` resolves to nothing, and Studio
renders it as a missing-asset placeholder rather than failing loudly.

Two reference shapes are recognised:

* `rbxassetid://<digits>` — a Roblox cloud asset. The kit's manifest carries **no** cloud
  ids (`cloudUri` is null for all 3,800 lock entries and `registry.json` records fetch
  provenance, not Roblox ids), so these are reported *informationally*: a cloud id the kit
  does not know is the normal case for anything inserted from the Toolbox. The report says
  so explicitly via `manifestTracksCloudIds`, rather than reporting thousands of false
  positives or silently pretending to have checked them.
* `rbxasset://arena_assets/<rel>` — a kit asset. These are checked against
  `assets/asset-lock.json` *and* against the files on disk, and a miss is a finding.

Pure and offline: no Studio peer, no network, nothing written. The live path lives in
`mcp_core.audit_asset_references_helper`, which fetches a tree and script sources and hands
them here, so the analysis an agent gets is the analysis a test can replay.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CLOUD_ID_RE = re.compile(r"rbxassetid://(\d+)")
# Kit paths contain spaces (`kenney_fetched/2d/Flag Pack/AD.png`), so a URI cannot be
# delimited by whitespace. Inside free text it is delimited by a quote; a bare property
# value *is* the whole URI. Two shapes, extracted by the context they were found in.
KIT_URI_QUOTED_RE = re.compile(r"""["']rbxasset://arena_assets/([^"'\n]+)["']""")
# Unquoted, so whitespace-delimited: in Luau source a real URI with spaces is inside
# quotes, and a bare one in prose should not swallow the rest of the sentence.
KIT_URI_BARE_RE = re.compile(r"rbxasset://arena_assets/([^\"'\s\n)\]}<>,;]+)")
KIT_URI_PREFIX = "rbxasset://arena_assets/"
SCRIPT_CLASSES = frozenset({"Script", "LocalScript", "ModuleScript"})
# Properties that hold an asset reference in normal Roblox use. Anything else is still
# scanned — a game can stash an id in a StringValue or an attribute — but these are the
# ones the report groups first, because they are the ones that render.
ASSET_PROPERTIES = frozenset({
    "Texture", "TextureId", "Image", "ImageContent", "SkyboxBk", "SkyboxDn", "SkyboxFt",
    "SkyboxLf", "SkyboxRt", "SkyboxUp", "SoundId", "SoundContent", "MeshId", "MeshContent",
    "AnimationId", "Video", "Graphic", "Icon", "Decal", "Face", "StudsTexture",
})


def _clip(items: list, limit) -> tuple:
    total = len(items)
    try:
        cap = int(limit)
    except Exception:  # noqa: BLE001 - a nonsense limit means the default cap
        cap = 25
    if cap <= 0:
        return [], total
    return list(items[:cap]), total


def _text_of(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value)
        except Exception:  # noqa: BLE001 - an unserialisable property still has a str()
            return str(value)
    return "" if value is None else str(value)


def load_kit_manifest(kit_root=None) -> dict:
    """The kit's own description of what it ships: `rbxasset://arena_assets/…` paths."""
    repo_root = Path(__file__).resolve().parent.parent
    kit = Path(kit_root).resolve() if kit_root else repo_root / "roblox-kit"
    manifest = {
        "kitRoot": kit.as_posix(),
        "kitUris": set(),
        "filesOnDisk": set(),
        "cloudIds": set(),
        "byCloudId": {},
        "manifestTracksCloudIds": False,
        "errors": [],
    }
    lock_path = kit / "assets" / "asset-lock.json"
    if not lock_path.is_file():
        manifest["errors"].append("assets/asset-lock.json is missing")
        return manifest
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a corrupt manifest is a finding, not a crash
        manifest["errors"].append(f"assets/asset-lock.json could not be read: {exc}")
        return manifest
    for bucket in ("images", "audio"):
        entries = lock.get(bucket)
        if not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            uri = entry.get("localUri")
            rel = uri.split("arena_assets/", 1)[-1] if isinstance(uri, str) and "arena_assets/" in uri else None
            if not rel:
                manifest["errors"].append(f"lock {bucket}/{key} has no arena_assets localUri")
                continue
            manifest["kitUris"].add(rel)
            cloud = entry.get("cloudUri")
            for cid in CLOUD_ID_RE.findall(_text_of(cloud)):
                manifest["cloudIds"].add(cid)
                manifest["byCloudId"].setdefault(cid, []).append(rel)
                manifest["manifestTracksCloudIds"] = True
            for prefix in ("assets/images", "assets"):
                candidate = kit / prefix / rel
                if candidate.is_file():
                    manifest["filesOnDisk"].add(rel)
                    break
    return manifest


def collect_references(tree=None, scripts=None) -> list:
    """Every asset reference in an inspected tree and/or a list of `{path, source}` scripts."""
    refs = []

    def add(kind, value, where, prop, origin, class_name=None, line=None, comment=False):
        refs.append({
            "kind": kind, "value": value, "where": where, "property": prop,
            "origin": origin, "className": class_name, "line": line, "comment": comment,
        })

    def kit_uri_spans(text, context):
        """`[(rel, start_offset)]` so the caller can tell a comment from a live reference."""
        spans = []
        if context == "value":
            stripped = text.strip()
            if stripped.startswith(KIT_URI_PREFIX):
                rest = stripped[len(KIT_URI_PREFIX):].strip()
                if rest:
                    spans.append((rest, text.find(KIT_URI_PREFIX) + len(KIT_URI_PREFIX)))
                return spans
        for match in KIT_URI_QUOTED_RE.finditer(text):
            value = match.group(1).strip()
            if value:
                spans.append((value, match.start(1)))
        quoted = [m.span(1) for m in KIT_URI_QUOTED_RE.finditer(text)]
        for match in KIT_URI_BARE_RE.finditer(text):
            if any(start <= match.start(1) < end for start, end in quoted):
                continue
            value = match.group(1).strip()
            if value:
                spans.append((value, match.start(1)))
        return spans

    def kit_uris(text, context):
        """`context="value"`: the text *is* one property value. `"text"`: free text, where a
        URI is quoted (source) or bare and delimited by punctuation (a JSON dump)."""
        return [rel for rel, _ in kit_uri_spans(text, context)]

    def scan_text(text, where, prop, origin, class_name=None, line_offset=0, context="text"):
        if not text:
            return
        for cid in CLOUD_ID_RE.findall(text):
            add("cloud", cid, where, prop, origin, class_name, None)
        for rel in kit_uris(text, context):
            add("kit", rel, where, prop, origin, class_name, None)
        if line_offset:
            for number, line in enumerate(text.splitlines(), start=1):
                if "rbxassetid://" not in line and "rbxasset://arena_assets/" not in line:
                    continue
                # A reference inside a Luau comment is not a live one. Reporting it as a
                # missing asset would be a false positive; dropping it silently would hide
                # the reason a place still mentions the file, so it is tagged instead.
                marker = line.find("--")
                for match in CLOUD_ID_RE.finditer(line):
                    commented = marker != -1 and match.start() > marker
                    add("cloud", match.group(1), where, prop, origin, class_name, number, commented)
                for rel, start in kit_uri_spans(line, context):
                    commented = marker != -1 and start > marker
                    add("kit", rel, where, prop, origin, class_name, number, commented)

    def walk(node):
        if not isinstance(node, dict):
            return
        where = node.get("path") or node.get("name") or "<unknown>"
        class_name = node.get("className")
        for holder in ("properties", "structuredProperties", "attributes"):
            values = node.get(holder)
            if isinstance(values, dict):
                for prop, value in values.items():
                    text = _text_of(value)
                    if "rbxasset" not in text:
                        continue
                    # A string property value is the whole URI, so it may contain spaces; a
                    # structured one is serialised to JSON, where the URI is quoted.
                    scan_text(text, where, prop, holder, class_name,
                              context="value" if isinstance(value, str) else "text")
        source = node.get("source")
        if isinstance(source, str) and source:
            scan_text(source, where, None, "source", class_name, line_offset=1)
        for child in node.get("children") or []:
            walk(child)

    if isinstance(tree, dict):
        walk(tree)
    elif isinstance(tree, list):
        for node in tree:
            walk(node)

    for entry in scripts or []:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if not isinstance(source, str) or not source:
            continue
        scan_text(source, entry.get("path") or "<script>", None, "script",
                  entry.get("className"), line_offset=1)

    # A script source is scanned twice: once as a whole (no line numbers) and once per line.
    # The whole-text copy of a reference the line pass also found is redundant, so it goes.
    # Two *line-tagged* hits are not redundant: the same asset required on three lines is
    # three places to fix, and collapsing them made `duplicateReferences` report a count of
    # one wherever a script mentioned an asset more than once.
    tagged = {(ref["kind"], ref["value"], ref["where"], ref["property"])
              for ref in refs if ref.get("line") is not None}
    kept = []
    seen_unlined = set()
    for ref in refs:
        key = (ref["kind"], ref["value"], ref["where"], ref["property"])
        if ref.get("line") is not None:
            kept.append(ref)
            continue
        if key in tagged or key in seen_unlined:
            continue
        seen_unlined.add(key)
        kept.append(ref)
    return kept


def audit_asset_reference_report(tree=None, scripts=None, kit_root=None, limit: int = 25,
                                 manifest: dict = None) -> dict:
    """Cross-check a place's asset references against the kit. Read-only and offline."""
    kit_manifest = manifest if isinstance(manifest, dict) else load_kit_manifest(kit_root)
    refs = collect_references(tree, scripts)

    kit_uris = kit_manifest.get("kitUris") or set()
    files_on_disk = kit_manifest.get("filesOnDisk") or set()
    known_cloud = kit_manifest.get("cloudIds") or set()
    tracks_cloud = bool(kit_manifest.get("manifestTracksCloudIds"))

    missing_kit = {}
    unresolved_files = {}
    unknown_cloud = {}
    cloud_seen = {}
    locations = {}
    assets_by_reference = {}

    commented = [ref for ref in refs if ref.get("comment")]
    for ref in refs:
        if ref.get("comment"):
            continue
        value = ref["value"]
        where = f"{ref['where']}.{ref['property']}" if ref.get("property") else str(ref["where"])
        if ref.get("line"):
            where = f"{where}:{ref['line']}"
        locations.setdefault((ref["kind"], value), []).append(where)
        if ref["kind"] == "kit":
            assets_by_reference.setdefault(value, set()).add(value)
            if value not in kit_uris:
                missing_kit.setdefault(value, {"kind": "kit", "value": value,
                                               "uri": f"rbxasset://arena_assets/{value}",
                                               "problem": "asset-lock.json does not list it"})
            elif value not in files_on_disk:
                unresolved_files.setdefault(value, {
                    "kind": "kit", "value": value,
                    "uri": f"rbxasset://arena_assets/{value}",
                    "problem": "the lock lists it but no file resolves on disk"})
        else:
            cloud_seen[value] = cloud_seen.get(value, 0) + 1
            if tracks_cloud and value not in known_cloud:
                unknown_cloud.setdefault(value, {"kind": "cloud", "value": value, "id": value,
                                                 "problem": "no kit asset is published under this id"})

    # A finding with no location is half a finding: "this asset is missing" tells an agent
    # something is wrong, "and here are the three places that reference it" tells it what to
    # edit. `locations` was already collected for `duplicateReferences`; hand it to the rest.
    for bucket, kind in ((missing_kit, "kit"), (unresolved_files, "kit"), (unknown_cloud, "cloud")):
        for entry in bucket.values():
            wheres = locations.get((kind, entry.get("value"))) or []
            entry["where"] = wheres[:5]
            entry["references"] = len(wheres)

    duplicates = []
    for (kind, value), wheres in sorted(locations.items()):
        if len(wheres) > 1:
            duplicates.append({"kind": kind, "value": value, "count": len(wheres),
                               "where": wheres[:5]})
    duplicates.sort(key=lambda d: (-d["count"], str(d["kind"]), str(d["value"])))

    # One kit asset addressed by two different cloud ids: the manifest can only say this
    # when it tracks cloud ids at all, so the check is honest about being unavailable.
    conflicting = []
    if tracks_cloud:
        by_cloud = kit_manifest.get("byCloudId") or {}
        per_asset = {}
        for cid, rels in by_cloud.items():
            for rel in rels:
                per_asset.setdefault(rel, set()).add(cid)
        for rel, cids in sorted(per_asset.items()):
            referenced = {c for c in cloud_seen if c in cids}
            if len(referenced) > 1:
                conflicting.append({"asset": rel, "ids": sorted(referenced)})

    report = {
        "ok": not (missing_kit or unresolved_files or unknown_cloud or conflicting
                   or kit_manifest.get("errors")),
        "kitRoot": kit_manifest.get("kitRoot"),
        "manifestTracksCloudIds": tracks_cloud,
        "errors": list(kit_manifest.get("errors") or []),
        "scanned": {
            "instances": 1 if isinstance(tree, dict) else len(tree or []),
            "scripts": len(scripts or []),
            "references": len(refs),
            "kitReferences": sum(1 for r in refs if r["kind"] == "kit"),
            "cloudReferences": sum(1 for r in refs if r["kind"] == "cloud"),
            "distinctCloudIds": len(cloud_seen),
            "kitAssetsInManifest": len(kit_uris),
            "commentedReferences": len(commented),
        },
        "missingKitAssets": [], "missingKitAssetsTotal": 0,
        "unresolvedKitFiles": [], "unresolvedKitFilesTotal": 0,
        "unknownCloudIds": [], "unknownCloudIdsTotal": 0,
        "duplicateReferences": [], "duplicateReferencesTotal": 0,
        "conflictingIds": [], "conflictingIdsTotal": 0,
        "cloudIds": [], "cloudIdsTotal": 0,
        "commentedReferences": [], "commentedReferencesTotal": 0,
    }
    report["missingKitAssets"], report["missingKitAssetsTotal"] = _clip(
        sorted(missing_kit.values(), key=lambda d: d["uri"]), limit)
    report["unresolvedKitFiles"], report["unresolvedKitFilesTotal"] = _clip(
        sorted(unresolved_files.values(), key=lambda d: d["uri"]), limit)
    report["unknownCloudIds"], report["unknownCloudIdsTotal"] = _clip(
        sorted(unknown_cloud.values(), key=lambda d: d["id"]), limit)
    report["duplicateReferences"], report["duplicateReferencesTotal"] = _clip(duplicates, limit)
    report["conflictingIds"], report["conflictingIdsTotal"] = _clip(conflicting, limit)
    report["commentedReferences"], report["commentedReferencesTotal"] = _clip(
        [{"kind": r["kind"], "value": r["value"], "where": r["where"], "line": r.get("line")}
         for r in commented], limit)
    report["cloudIds"], report["cloudIdsTotal"] = _clip(
        [{"id": cid, "count": count} for cid, count in
         sorted(cloud_seen.items(), key=lambda kv: (-kv[1], kv[0]))], limit)
    if not tracks_cloud and cloud_seen:
        report["note"] = (
            "The kit manifest publishes no Roblox cloud ids (every asset-lock entry has "
            "cloudUri: null), so rbxassetid:// references are listed but not judged. A "
            "missing rbxasset://arena_assets/... reference is still a finding.")
    return report
