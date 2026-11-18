#!/usr/bin/env python3
"""Build the curated source distribution from the current Git worktree.

Only explicitly selected tracked paths and named in-progress files are packaged.
Ignored tokens, local configuration and runtime state are never picked up.
"""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "arena-roblox-bridge/"
NEW_FILES = (
    "roblox-bridge/localization_auditor.py", "roblox-bridge/onboarding_auditor.py",
    "roblox-bridge/docs/OVERHAUL.md", "roblox-bridge/docs/SKILLS_ROADMAP.md",
    "roblox-bridge/docs/RELEASE.md", "roblox-bridge/tools/build_release.py",
    ".claude/skills/roblox-cross-platform-accessibility/SKILL.md",
    ".claude/skills/roblox-data-reliability/SKILL.md",
    ".claude/skills/roblox-localization/SKILL.md",
    ".claude/skills/roblox-multiplayer-interactions/SKILL.md",
    ".claude/skills/roblox-performance-profiling/SKILL.md",
    ".claude/skills/roblox-onboarding-progression/SKILL.md",
    ".claude/skills/roblox-onboarding-progression/references/milestone-matrix.md",
    ".claude/skills/roblox-world-streaming/SKILL.md",
    ".claude/skills/roblox-world-streaming/references/streaming-test-matrix.md",
)


def selected_files():
    result = subprocess.check_output(["git", "ls-files", "-z", "--", "roblox-bridge", "roblox-kit", ".claude/skills", "tools", "jobs", "THIRD_PARTY_LICENSES.md"], cwd=ROOT)
    tracked = [p.decode("utf-8") for p in result.split(b"\0") if p]
    chosen = set(NEW_FILES)
    for path in tracked:
        parts = Path(path).parts
        if path.startswith(("roblox-kit/", ".claude/skills/")) or path == "THIRD_PARTY_LICENSES.md":
            chosen.add(path)
        elif path.startswith("tools/") and len(parts) == 2 and path.endswith(".py"):
            chosen.add(path)
        elif path in ("jobs/measure_tunnel_latency.py", "jobs/live_acceptance.py", "jobs/drill_studio_kill.py"):
            chosen.add(path)
        elif path.startswith("roblox-bridge/"):
            if len(parts) == 2 and (path.endswith(".py") or parts[-1] in ("README.md", "CHANGELOG.md", "ArenaBridge.lua", "ab.ps1")):
                chosen.add(path)
            elif path.startswith("roblox-bridge/docs/") and path.endswith(".md"):
                chosen.add(path)
            elif path in ("roblox-bridge/tools/luau-compile", "roblox-bridge/tools/luau-analyze", "roblox-bridge/tools/fix-perms.sh", "roblox-bridge/tools/install_luau_windows.ps1"):
                chosen.add(path)
    for rel in sorted(chosen):
        path = ROOT / rel
        if not path.is_file() or any((ROOT / Path(*Path(rel).parts[:i])).is_symlink() for i in range(1, len(Path(rel).parts) + 1)):
            raise ValueError(f"Missing or symlinked distribution input: {rel}")
        yield rel, path


def build(output):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    entries = list(selected_files())
    entries.insert(0, ("START_HERE.md", ROOT / "roblox-bridge/docs/RELEASE.md"))
    hashes = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for rel, source in entries:
            data = source.read_bytes()
            info = zipfile.ZipInfo(PREFIX + rel, (2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = ((0o100755 if os.access(source, os.X_OK) else 0o100644) << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
            hashes.append(f"{hashlib.sha256(data).hexdigest()}  {rel}")
        info = zipfile.ZipInfo(PREFIX + "MANIFEST.sha256", (2020, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, "\n".join(hashes) + "\n")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    checksum = output.with_name(output.name + ".sha256")
    checksum.write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    print(f"Built {output} ({len(entries)} files); SHA-256 {digest}")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(ROOT / "release-artifacts" / "arena-roblox-bridge.zip"))
    build(parser.parse_args().output)
