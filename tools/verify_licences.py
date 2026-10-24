#!/usr/bin/env python3
"""Fail-closed validation of the bundled asset provenance registry."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from fetch_assets import ALLOWED, ASSETS, REGISTRY_FILE, SOURCES_FILE, build_registry, load_sources  # noqa: E402


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def git_blob_sha1(path: pathlib.Path) -> str:
    h = hashlib.sha1()
    h.update(f"blob {path.stat().st_size}\0".encode("ascii"))
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fail(message: str) -> None:
    raise SystemExit(f"licence verification failed: {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true", help="Fetch pinned GitHub trees and verify every recorded exact upstream match.")
    args = parser.parse_args()
    if not SOURCES_FILE.is_file() or not REGISTRY_FILE.is_file():
        fail("missing sources.json or registry.json")
    try:
        source_doc = load_sources()
    except Exception as exc:
        fail(str(exc))
    allowed = set(source_doc.get("allowedLicenses", [])) & ALLOWED
    source_map = {source["id"]: source for source in source_doc.get("sources", [])}
    generated_source = source_map.get("arena-generated", {})
    for evidence_path in (generated_source.get("sourceEvidence") or "").split(" and "):
        if evidence_path and not (ROOT / evidence_path.strip()).exists():
            fail(f"generated-asset source record points to missing evidence: {evidence_path}")
    registry = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    if registry.get("schemaVersion") != 1 or not isinstance(registry.get("assets"), list):
        fail("unsupported registry schema")
    try:
        current = build_registry(verify_upstream=args.online)
    except Exception as exc:
        fail(f"could not rebuild expected asset registry: {exc}")
    expected = {item["path"]: item for item in current["assets"]}
    seen = set()
    for item in registry["assets"]:
        rel = item.get("path")
        if not isinstance(rel, str) or rel.startswith("/") or ".." in pathlib.PurePosixPath(rel).parts:
            fail(f"unsafe registry path: {rel!r}")
        if rel in seen:
            fail(f"duplicate registry path: {rel}")
        seen.add(rel)
        path = ROOT / rel
        if not path.is_file():
            fail(f"missing bundled asset: {rel}")
        source = source_map.get(item.get("sourceId"))
        if not source:
            fail(f"undeclared source for {rel}")
        license_id = item.get("license")
        if license_id not in allowed or license_id != source.get("license"):
            fail(f"disallowed or mismatched SPDX licence for {rel}: {license_id!r}")
        if re.search(r"(^|[-+])(NC|ND|SA)([-+]|$)", license_id, re.I):
            fail(f"restricted CC term found for {rel}: {license_id}")
        if item.get("sourceCommit") != source.get("commit"):
            fail(f"source commit drift for {rel}")
        actual_sha = sha256_file(path)
        if item.get("sha256") != actual_sha:
            fail(f"asset hash mismatch: {rel}")
        baseline = expected.get(rel)
        if not baseline:
            fail(f"asset not admitted by the deterministic scanner: {rel}")
        provenance = item.get("provenance")
        if args.online:
            live_match = baseline.get("provenance") == "content-hash-match"
            if live_match != (provenance == "content-hash-match"):
                fail(f"stale exact-match provenance for {rel}; rebuild with --verify-upstream")
            if live_match:
                for field in ("sourceId", "sourceCommit", "upstreamPath", "upstreamBlobSha1"):
                    if item.get(field) != baseline.get(field):
                        fail(f"pinned upstream {field} mismatch for {rel}")
        for field in ("assetType", "category", "sourceId", "license", "sha256"):
            if provenance == "content-hash-match" and field in {"sourceId", "license"}:
                continue
            if item.get(field) != baseline.get(field):
                fail(f"stale registry {field} for {rel}")
        if provenance not in {"content-hash-match", "pack-license-attestation", "first-party-generated"}:
            fail(f"unknown or unverified provenance for {rel}: {provenance!r}")
        if provenance == "content-hash-match":
            if not item.get("upstreamPath") or item.get("upstreamBlobSha1") != git_blob_sha1(path):
                fail(f"upstream blob claim is invalid for {rel}")
        elif item.get("upstreamPath") or item.get("upstreamBlobSha1"):
            fail(f"unmatched asset carries an inconsistent upstream hash claim: {rel}")
        thumb = item.get("thumbnail")
        if not thumb or not (ROOT / thumb).is_file():
            fail(f"missing thumbnail for {rel}")
    missing = sorted(set(expected) - seen)
    if missing:
        fail(f"{len(missing)} files are not registered (first: {missing[0]})")
    print(f"Licence registry OK: {len(seen)} assets, {len(source_map)} pinned source declarations.")
    by_type, categories = {}, set()
    for item in registry["assets"]:
        by_type[item["assetType"]] = by_type.get(item["assetType"], 0) + 1
        categories.add(item["category"])
    print("Asset types: " + ", ".join(f"{name}={n}" for name, n in sorted(by_type.items())))
    print(f"Distinct categories: {len(categories)}")
    if args.online:
        print("Pinned GitHub trees verified live; every recorded exact-match path and blob hash agrees with its declared commit.")
    else:
        print("Offline mode verifies local evidence and recorded blob claims; use --online to re-fetch pinned source trees.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
