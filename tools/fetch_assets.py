#!/usr/bin/env python3
"""Licence-first asset fetcher and deterministic registry builder.

The default command only refreshes the local registry and contact-sheet index. Network
fetching is opt-in (`--fetch`) and accepts a source only after its pinned, local
licence evidence is verified. Downloads are cached with HTTP Range resume support.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import pathlib
import re
import shutil
import struct
import sys
import wave
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "roblox-bridge"))
KIT = ROOT / "roblox-kit"
ASSETS = KIT / "assets"
SOURCES_FILE = ASSETS / "sources.json"
REGISTRY_FILE = ASSETS / "registry.json"
CACHE = ROOT / ".cache" / "arena-assets"
ALLOWED = {"CC0-1.0", "MIT", "ISC", "Apache-2.0"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
MODEL_EXTS = {".glb", ".gltf", ".obj", ".fbx"}
AUDIO_EXTS = {".wav", ".ogg", ".mp3"}
FETCHED_MATCHES: dict[str, tuple[str, str]] = {}


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load_sources() -> dict:
    data = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    allowed = set(data.get("allowedLicenses") or []) & ALLOWED
    for source in data.get("sources", []):
        license_id = source.get("license")
        if license_id not in allowed or any(term in str(license_id).upper() for term in ("NC", "ND", "SA")):
            raise ValueError(f"Source {source.get('id')} has a disallowed or unrecognized licence: {license_id!r}")
        for key in ("licenseEvidence", "sourceEvidence"):
            rel = source.get(key)
            expected = source.get(key + "Sha256")
            if not rel or not expected:
                if source.get("id") == "arena-generated" and key == "sourceEvidence":
                    continue
                raise ValueError(f"Source {source.get('id')} is missing {key} evidence")
            evidence = ROOT / rel
            if not evidence.is_file() or sha256_file(evidence) != expected:
                raise ValueError(f"Source evidence missing or hash mismatch: {rel}")
        if license_id == "CC0-1.0":
            evidence_text = (ROOT / source["licenseEvidence"]).read_text(encoding="utf-8", errors="replace")
            if not re.search(r"CC0|Creative Commons Zero", evidence_text, re.I):
                raise ValueError(f"CC0 claim is not explicit in the pinned licence evidence for {source.get('id')}")
            if source.get("id") != "arena-generated":
                source_text = (ROOT / source["sourceEvidence"]).read_text(encoding="utf-8", errors="replace")
                if not re.search(r"CC0|Creative Commons Zero", source_text, re.I):
                    raise ValueError(f"CC0 claim is not explicit in the pinned source documentation for {source.get('id')}")
        archive_url = source.get("archiveUrl")
        if archive_url:
            parsed_archive = urllib.parse.urlparse(archive_url)
            if parsed_archive.scheme != "https" or parsed_archive.hostname != "codeload.github.com" or source.get("commit") not in parsed_archive.path:
                raise ValueError(f"Archive URL is not pinned to an allowed codeload commit for {source.get('id')}")
    return data


def classify(path: pathlib.Path) -> tuple[str, str, str]:
    rel = path.relative_to(KIT).as_posix()
    parts = pathlib.PurePosixPath(rel).parts
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTS:
        is_sky_dir = len(parts) > 2 and parts[2] == "arena_skies"
        is_panorama = is_sky_dir and re.fullmatch(r"sky_\d{3}", path.stem) is not None
        is_face = is_sky_dir and re.fullmatch(r"sky_\d{3}_(bk|dn|ft|lf|rt|up)", path.stem) is not None
        asset_type = "sky" if is_panorama else "image"
        category = "skies" if is_panorama else "skybox_faces" if is_face else (parts[2] if len(parts) > 2 else "images")
        if len(parts) > 3 and parts[2] == "kenney_fetched":
            category = parts[4] if len(parts) > 4 else parts[3]
        if len(parts) > 2 and (parts[2].startswith("kenney_") or parts[2] == "kenney_fetched"):
            source_id, provenance = "kenney-art", "pack-license-attestation"
        else:
            source_id, provenance = "arena-generated", "first-party-generated"
    elif suffix in AUDIO_EXTS:
        asset_type = "audio"
        category = "audio"
        stem = path.stem.lower()
        ui_audio_names = {"click1", "click2", "click3", "rollover1", "rollover2", "rollover3", "rollover4", "switch1", "switch2"}
        source_id = "kenney-ui-audio" if stem in ui_audio_names else "kenney-interface-sounds"
        provenance = "pack-license-attestation"
    elif suffix in MODEL_EXTS or suffix == ".rbxmx":
        asset_type = "model"
        if len(parts) > 2 and parts[0] == "models" and parts[1] == "kenney_fetched":
            category = parts[3] if len(parts) > 3 else parts[2]
            source_id, provenance = "kenney-art", "pack-license-attestation"
        else:
            category = "models"
            source_id, provenance = "arena-generated", "first-party-generated"
    else:
        raise ValueError(f"Unsupported asset file type: {path}")
    return asset_type, category, source_id + "|" + provenance


def thumbnail_path_for(path: pathlib.Path, kind: str) -> pathlib.Path:
    digest = sha256_file(path)[:12]
    return ASSETS / "thumbnails" / kind / f"{path.stem}-{digest}.png"


def scan_assets() -> list[dict]:
    records = []
    roots = [ASSETS / "images", ASSETS / "audio", KIT / "models"]
    for folder in roots:
        if not folder.is_dir():
            continue
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            if path.suffix.lower() not in IMAGE_EXTS | AUDIO_EXTS | MODEL_EXTS | {".rbxmx"}:
                continue
            asset_type, category, source_and_status = classify(path)
            source_id, provenance = source_and_status.split("|", 1)
            rel = path.relative_to(ROOT).as_posix()
            thumb = rel if asset_type in {"image", "sky"} else None
            if asset_type in {"audio", "model"}:
                candidate = thumbnail_path_for(path, asset_type)
                if candidate.is_file():
                    thumb = candidate.relative_to(ROOT).as_posix()
            records.append({
                "path": rel,
                "assetType": asset_type,
                "category": category,
                "name": path.stem,
                "sourceId": source_id,
                "provenance": provenance,
                "license": None,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "thumbnail": thumb,
                "thumbnailKind": (
                    "equirectangular-panorama" if asset_type == "sky" else
                    "skybox-face" if category == "skybox_faces" else
                    "source-image" if asset_type == "image" else
                    "waveform" if asset_type == "audio" else
                    "offline-model-render" if path.suffix.lower() == ".rbxmx" else
                    "neutral-format-placeholder"
                ),
                "derivation": (
                    "WAV conversion from the pinned Kenney Ogg pack; see source README" if asset_type == "audio" else
                    "Deterministically generated equirectangular panorama by tools/build_skies.py" if asset_type == "sky" else
                    "Deterministically generated cube face from the panorama by tools/build_skies.py" if category == "skybox_faces" else
                    "Repository-authored model XML" if asset_type == "model" and path.suffix.lower() == ".rbxmx" else
                    "Repository procedural image output" if provenance == "first-party-generated" and asset_type == "image" else
                    "Exact bytes from the pinned upstream Git tree" if provenance == "content-hash-match" else
                    "Pinned pack-level attribution; exact source-file bytes not matched"
                ),
            })
    return records


def upstream_blob_maps(sources: dict) -> dict[str, dict[str, str]]:
    maps = {}
    for source in sources["sources"]:
        repository, commit = source.get("repository"), source.get("commit")
        if not repository or not commit or not re.match(r"^[0-9a-f]{40}$", commit):
            continue
        parsed = urllib.parse.urlparse(repository)
        if parsed.hostname != "github.com":
            raise ValueError(f"Refusing non-GitHub upstream for hash verification: {repository}")
        owner_repo = parsed.path.strip("/").split("/")[:2]
        api = f"https://api.github.com/repos/{owner_repo[0]}/{owner_repo[1]}"
        headers = {"User-Agent": "Tunneling-licence-first-asset-registry", "Accept": "application/vnd.github+json"}
        def get_json(url: str) -> dict:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.load(response)
        commit_info = get_json(f"{api}/commits/{commit}")
        tree_info = get_json(f"{api}/git/trees/{commit_info['commit']['tree']['sha']}?recursive=1")
        if tree_info.get("truncated"):
            raise RuntimeError(f"Upstream Git tree was truncated for {source['id']}; cannot claim complete hash matching")
        maps[source["id"]] = {entry["sha"]: entry["path"] for entry in tree_info.get("tree", []) if entry.get("type") == "blob"}
    return maps


def git_blob_sha1(path: pathlib.Path) -> str:
    h = hashlib.sha1()
    size = path.stat().st_size
    h.update(f"blob {size}\0".encode("ascii"))
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def build_registry(verify_upstream: bool = False) -> dict:
    sources = load_sources()
    by_id = {s["id"]: s for s in sources["sources"]}
    assets = scan_assets()
    upstream_maps = upstream_blob_maps(sources) if verify_upstream else {}
    for record in assets:
        source_id = record["sourceId"]
        src = by_id.get(source_id)
        if not src:
            raise ValueError(f"No source declaration for {record['path']}: {source_id}")
        record["upstreamPath"] = None
        record["upstreamBlobSha1"] = None
        local_path = ROOT / record["path"]
        match = None
        fetched = FETCHED_MATCHES.get(record["path"])
        if fetched:
            match = (source_id, fetched[0], fetched[1])
        elif upstream_maps:
            blob_sha = git_blob_sha1(local_path)
            for upstream_id, tree in upstream_maps.items():
                upstream_path = tree.get(blob_sha)
                if upstream_path:
                    match = (upstream_id, upstream_path, blob_sha)
                    break
        if match:
            source_id, upstream_path, blob_sha = match
            src = by_id[source_id]
            record["sourceId"] = source_id
            record["upstreamPath"] = upstream_path
            record["upstreamBlobSha1"] = blob_sha
            record["provenance"] = "content-hash-match"
            record["derivation"] = "Exact bytes from the pinned upstream Git tree"
        record["license"] = src["license"]
        record["sourceUrl"] = src.get("repository") or src.get("homepage")
        record["sourceCommit"] = src.get("commit")
    return {"schemaVersion": 1, "generatedBy": "tools/fetch_assets.py", "assets": assets}


def save_registry(registry: dict) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_png(path: pathlib.Path, width: int, height: int, rgba: bytearray) -> None:
    from image_pipeline import encode_png_rgba
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_png_rgba(width, height, bytes(rgba)))


def _audio_thumbnail(path: pathlib.Path, target: pathlib.Path) -> None:
    width, height = 480, 180
    pixels = bytearray([18, 27, 39, 255] * width * height)
    with wave.open(str(path), "rb") as stream:
        channels, sample_width, frames = stream.getnchannels(), stream.getsampwidth(), stream.getnframes()
        raw = stream.readframes(frames)
    if sample_width not in (1, 2, 3, 4) or channels < 1:
        raise ValueError(f"Unsupported PCM format in {path}")
    frame_bytes = sample_width * channels
    count = len(raw) // frame_bytes
    amplitude = float(1 << (sample_width * 8 - 1))
    mid = height // 2
    for x in range(width):
        start = int(x * count / width)
        end = max(start + 1, int((x + 1) * count / width))
        peak = 0
        for frame_i in range(start, min(end, count)):
            begin = frame_i * frame_bytes
            sample = raw[begin:begin + sample_width]
            if sample_width == 1:
                value = sample[0] - 128
            else:
                value = int.from_bytes(sample, "little", signed=True)
            peak = max(peak, abs(value))
        bar = max(1, min(mid - 8, round((peak / amplitude) * (mid - 8))))
        for y in range(mid - bar, mid + bar + 1):
            if 0 <= y < height:
                idx = (y * width + x) * 4
                pixels[idx:idx + 4] = bytes((74, 205, 183, 255))
    for x in range(width):
        idx = (mid * width + x) * 4
        pixels[idx:idx + 4] = bytes((76, 104, 127, 255))
    _write_png(target, width, height, pixels)


def _model_thumbnail(path: pathlib.Path, target: pathlib.Path) -> None:
    if path.suffix.lower() == ".rbxmx":
        from rbxmx_pipeline import parse_rbxmx_to_tree
        from viewport_renderer import render_scene_png
        tree, _ = parse_rbxmx_to_tree(path.read_text(encoding="utf-8", errors="replace"), strip_scripts=True)
        image, _stats = render_scene_png(
            {"className": "Workspace", "children": tree}, gui_tree=tree,
            width=480, height=270, camera_preset="isometric", include_hud=True,
            show_grid=False, show_labels=False,
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(image)
        return
    # Honest neutral thumbnail for imported formats the local renderer cannot parse.
    w, h = 480, 270
    px = bytearray([18, 27, 39, 255] * w * h)
    def put(x: int, y: int, c=(74, 205, 183, 255)):
        if 0 <= x < w and 0 <= y < h:
            i = (y * w + x) * 4
            px[i:i + 4] = bytes(c)
    for y in range(72, 211):
        for x in range(156, 324):
            if (x + y) % 11 < 2:
                put(x, y, (39, 61, 77, 255))
    for step in range(86):
        for offset in range(-step, step + 1):
            put(240 + offset, 141 - step, (74, 205, 183, 255))
            put(240 + offset, 141 + step, (74, 205, 183, 255))
    _write_png(target, w, h, px)


def build_thumbnails() -> int:
    count = 0
    for folder in (ASSETS / "audio", KIT / "models"):
        if not folder.is_dir():
            continue
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            if path.suffix.lower() not in AUDIO_EXTS | MODEL_EXTS | {".rbxmx"}:
                continue
            kind = "audio" if path.suffix.lower() in AUDIO_EXTS else "model"
            target = thumbnail_path_for(path, kind)
            if kind == "audio":
                _audio_thumbnail(path, target)
            else:
                _model_thumbnail(path, target)
            count += 1
    return count


def build_attribution(sources: dict) -> pathlib.Path:
    lines = ["# Asset attributions and licences", "", "Only sources listed below are admitted to the asset registry. Restricted, unclear, NC, ND, and SA content is rejected by the registry verifier.", ""]
    for source in sorted(sources["sources"], key=lambda item: item["id"]):
        lines.extend([
            f"## {source['id']}",
            f"- Author: {source.get('author', 'Unknown')}",
            f"- SPDX licence: {source['license']}",
            f"- Repository: {source.get('repository') or '—'}",
            f"- Pinned revision: {source.get('commit') or '—'}",
            f"- Licence evidence: `{source['licenseEvidence']}`",
            f"- Source evidence: `{source.get('sourceEvidence') or '—'}`",
        ])
        if source.get("evidenceNote"):
            lines.append(f"- Evidence note: {source['evidenceNote']}")
        lines.append("")
    path = ASSETS / "ATTRIBUTION.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def build_contact_sheets(registry: dict, page_size: int = 60) -> pathlib.Path:
    out_dir = ASSETS / "contact-sheets"
    out_dir.mkdir(parents=True, exist_ok=True)
    grouped = defaultdict(list)
    for item in registry["assets"]:
        grouped[item["category"]].append(item)
    links = []
    for category, entries in sorted(grouped.items()):
        for page, start in enumerate(range(0, len(entries), page_size), start=1):
            slug = re.sub(r"[^a-z0-9_-]+", "-", category.lower()).strip("-") or "assets"
            dest = out_dir / f"{slug}-{page:03d}.html"
            cards = []
            for item in entries[start:start + page_size]:
                thumb = item.get("thumbnail")
                thumb_url = "../../" + pathlib.PurePosixPath(thumb).relative_to("roblox-kit").as_posix() if thumb else ""
                title = html.escape(item["name"])
                kind = html.escape(item["thumbnailKind"])
                asset_type = html.escape(item["assetType"])
                cards.append(f'<figure><a href="../../{html.escape(pathlib.PurePosixPath(item["path"]).relative_to("roblox-kit").as_posix())}"><img loading="lazy" src="{html.escape(thumb_url)}" alt="{title}"></a><figcaption>{title}<small>{asset_type} · {kind}</small></figcaption></figure>')
            dest.write_text("<!doctype html><meta charset=\"utf-8\"><title>" + html.escape(category) + " assets</title><style>body{font:14px sans-serif;background:#101721;color:#e7edf5}main{display:grid;grid-template-columns:repeat(6,minmax(120px,1fr));gap:12px}figure{margin:0;background:#1a2635;padding:8px}img{width:100%;height:120px;object-fit:contain}figcaption{overflow-wrap:anywhere}small{display:block;opacity:.7;font-size:10px}</style><h1>" + html.escape(category) + f" — {start+1}-{min(start+page_size,len(entries))} of {len(entries)}</h1><main>" + "".join(cards) + "</main>\n", encoding="utf-8")
            links.append(f'<li><a href="{dest.name}">{html.escape(category)} — page {page}</a></li>')
    (out_dir / "index.html").write_text("<!doctype html><meta charset=\"utf-8\"><title>Asset contact sheets</title><h1>Asset contact sheets</h1><ul>" + "".join(links) + "</ul>\n", encoding="utf-8")
    return out_dir / "index.html"


def download_resumable(url: str, target: pathlib.Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "Tunneling-licence-first-asset-fetcher"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=180) as response:
        append = offset > 0 and getattr(response, "status", 200) == 206
        if not append:
            offset = 0
        with partial.open("ab" if append else "wb") as stream:
            shutil.copyfileobj(response, stream, length=1024 * 1024)
    partial.replace(target)


def fetch_source_archive(source: dict, max_files: int, asset_types: set[str]) -> int:
    archive_url = source.get("archiveUrl")
    if not archive_url:
        raise ValueError(f"Source {source['id']} has no pinned archiveUrl")
    cache_file = CACHE / f"{source['id']}-{source['commit']}.zip"
    if not cache_file.is_file():
        download_resumable(archive_url, cache_file)
    include_prefixes = tuple(source.get("includePrefixes") or [])
    ext_map = {"image": IMAGE_EXTS, "model": MODEL_EXTS}
    if not asset_types or not asset_types <= set(ext_map):
        raise ValueError("This fetcher currently accepts only --types image,model; audio source packs are license-registered but not archive-fetched here")
    allowed_exts = set().union(*(ext_map[t] for t in asset_types))
    chosen = []
    upstream = upstream_blob_maps({"sources": [source]}).get(source["id"], {})
    with zipfile.ZipFile(cache_file) as archive:
        for info in archive.infolist():
            rel = pathlib.PurePosixPath(info.filename)
            if info.is_dir() or not any(str(rel).find(prefix) >= 0 for prefix in include_prefixes):
                continue
            if rel.suffix.lower() not in allowed_exts:
                continue
            chosen.append(info)
        groups = defaultdict(list)
        for info in sorted(chosen, key=lambda i: i.filename.lower()):
            bits = pathlib.PurePosixPath(info.filename).parts
            root_index = next((i for i, part in enumerate(bits) if part in {"2d", "ui", "icons", "3d"}), 0)
            group = bits[root_index + 1] if len(bits) > root_index + 1 else "misc"
            groups[group].append(info)
        selected = []
        group_names = sorted(groups)
        while len(selected) < max_files:
            added = False
            for group in group_names:
                if groups[group]:
                    selected.append(groups[group].pop(0))
                    added = True
                    if len(selected) >= max_files:
                        break
            if not added:
                break
        for info in selected:
            rel = pathlib.PurePosixPath(info.filename)
            parts = rel.parts
            try:
                root_index = next(i for i, part in enumerate(parts) if part in {"2d", "ui", "icons", "3d"})
            except StopIteration:
                continue
            source_rel = pathlib.PurePosixPath(*parts[root_index:])
            if source_rel.parts[0] == "3d" and source_rel.suffix.lower() in MODEL_EXTS:
                target_rel = pathlib.PurePosixPath("roblox-kit", "models", "kenney_fetched", *source_rel.parts)
            else:
                target_rel = pathlib.PurePosixPath("roblox-kit", "assets", "images", "kenney_fetched", *source_rel.parts)
            safe_target = (ROOT / target_rel).resolve()
            if ROOT.resolve() not in safe_target.parents:
                raise ValueError("Archive path escaped the repository root")
            safe_target.parent.mkdir(parents=True, exist_ok=True)
            payload = archive.read(info)
            blob_sha = hashlib.sha1(f"blob {len(payload)}\0".encode("ascii") + payload).hexdigest()
            upstream_path = upstream.get(blob_sha)
            if not upstream_path:
                raise ValueError(f"Fetched content does not match pinned Git tree: {info.filename}")
            FETCHED_MATCHES[target_rel.as_posix()] = (upstream_path, blob_sha)
            if not safe_target.is_file() or safe_target.read_bytes() != payload:
                safe_target.write_bytes(payload)
    return len(selected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-registry", action="store_true", help="Rebuild deterministic per-file hashes and attributions.")
    parser.add_argument("--verify-upstream", action="store_true", help="Compare local Git blob hashes against pinned upstream Git trees.")
    parser.add_argument("--rebuild-thumbnails", action="store_true", help="Regenerate audio waveforms and model previews.")
    parser.add_argument("--contact-sheets", action="store_true", help="Build paginated local HTML contact sheets.")
    parser.add_argument("--attribution", action="store_true", help="Regenerate the source attribution document.")
    parser.add_argument("--fetch", action="store_true", help="Fetch from a pinned, licence-verified source archive.")
    parser.add_argument("--source", help="Source ID to fetch; must have a pinned archiveUrl in sources.json.")
    parser.add_argument("--max-files", type=int, default=3000)
    parser.add_argument("--types", default="image", help="Comma-separated image,model types; audio is registered but not fetched by this archive tool.")
    parser.add_argument("--no-rebuild-module", action="store_true")
    parser.add_argument("--rebuild-module", action="store_true", help="Refresh asset-lock.json and Assets.lua after registry changes.")
    args = parser.parse_args()
    if args.rebuild_thumbnails:
        count = build_thumbnails()
        print(f"Rebuilt {count} audio/model thumbnails")
    sources = load_sources()
    requested_types = {x.strip() for x in args.types.split(",") if x.strip()}
    if args.fetch and (not requested_types or not requested_types <= {"image", "model"}):
        parser.error("--fetch currently accepts --types image,model; audio packs are registered but not archive-fetched")
    registry = build_registry(verify_upstream=args.verify_upstream)
    if args.fetch:
        source = next((s for s in sources["sources"] if s["id"] == args.source), None)
        if not source:
            raise SystemExit("--fetch requires a valid --source ID")
        count = fetch_source_archive(source, max(1, min(args.max_files, 100000)), requested_types)
        print(f"Fetched {count} licensed candidates from {args.source}; review and regenerate thumbnails/registry before use.")
        if not args.no_rebuild_module or args.rebuild_module:
            from image_pipeline import update_repo_asset_manifest
            update_repo_asset_manifest()
        registry = build_registry(verify_upstream=args.verify_upstream)
    elif args.rebuild_module:
        from image_pipeline import update_repo_asset_manifest
        update_repo_asset_manifest()
        print("Rebuilt roblox-kit/modules/Assets.lua and asset-lock.json")
    if args.build_registry or args.fetch or args.verify_upstream:
        save_registry(registry)
        print(f"Registry: {len(registry['assets'])} files -> {REGISTRY_FILE.relative_to(ROOT)}")
    if args.contact_sheets:
        index = build_contact_sheets(registry)
        print(f"Contact sheets -> {index.relative_to(ROOT)}")
    if args.attribution:
        path = build_attribution(sources)
        print(f"Attribution -> {path.relative_to(ROOT)}")
    if not (args.build_registry or args.contact_sheets or args.fetch or args.verify_upstream or args.attribution or args.rebuild_module):
        save_registry(registry)
        print(f"Registry refreshed: {len(registry['assets'])} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
