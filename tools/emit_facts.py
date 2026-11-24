#!/usr/bin/env python3
"""Generate `docs/FACTS.md` from the code, then check the prose docs against it.

P4-D2. Every count in this repo's docs used to be typed by hand, and they drifted:
README.md claimed 90 tools while `TOOL_SPECS` held 100, OPS.md claimed 11
resources while `RESOURCES` held 12, roblox-bridge/README.md described the plugin
as v4.1.0 and AGENTS.md as 20 prefabs while the kit held 36. So the numbers are
computed here instead, and the docs are checked against them:

    python3 -B tools/emit_facts.py             # rewrite docs/FACTS.md
    python3 -B tools/emit_facts.py --check     # fail if FACTS.md or a doc disagrees

`--check` runs three gates:

1. **FACTS.md is current.** The committed file must be byte-identical to a fresh
   generation, so a stale FACTS.md fails rather than quietly lying.
2. **The release is one version.** `server.VERSION`, `mcp_core.MCP_SERVER_VERSION`,
   `confirm_gate.GATE_VERSION`, `auth_core.AUTH_VERSION`, `bus_core.BUS_VERSION`,
   `telemetry.TELEMETRY_VERSION`, `tunnel_manager.TUNNEL_VERSION`,
   `mock_studio.PLUGIN_VERSION` and the `PLUGIN_VERSION` literal in
   `ArenaBridge.lua` must all agree. A half-bumped release lies to peers about
   which protocol era they are negotiating, and the plugin version matrix rejects
   the mismatch at connect time — long after the docs were believed.
3. **The docs agree with the code.** Every version claim and every count claim in
   README.md, CONNECT.md, AGENTS.md, roblox-bridge/README.md and
   roblox-bridge/docs/OPS.md must match a computed fact. A claim pattern that
   stops matching anything is *also* a failure, so this guard cannot go blind the
   way the old one did (it reported "carries no version claim any more" only after
   the docs had already drifted).

Historical text is left alone on purpose: a doc whose claim is dated belongs to
that release. CONNECT.md keeps descending dated notes, so only its newest note is
treated as a current claim; OPS.md §7 records what each v5.x release changed and
is not scanned for counts.

Stdlib-only, offline, no Studio peer, writes nothing under `--check`.
Exit 0 clean, 1 on any disagreement.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "roblox-bridge"
KIT = ROOT / "roblox-kit"
FACTS_PATH = ROOT / "docs" / "FACTS.md"

DOCS = ("README.md", "CONNECT.md", "AGENTS.md", "roblox-bridge/README.md",
        "roblox-bridge/docs/OPS.md")

# Version stamps that must move together. Parsed rather than imported where the
# module has import-time side effects (server.py binds defaults, reads env).
VERSION_STAMPS = (
    ("server.py", r'^VERSION = "([0-9.]+)"'),
    ("mcp_core.py", r'^MCP_SERVER_VERSION = "([0-9.]+)"'),
    ("confirm_gate.py", r'^GATE_VERSION = "([0-9.]+)"'),
    ("auth_core.py", r'^AUTH_VERSION = "([0-9.]+)"'),
    ("bus_core.py", r'^BUS_VERSION = "([0-9.]+)"'),
    ("telemetry.py", r'^TELEMETRY_VERSION = "([0-9.]+)"'),
    ("tunnel_manager.py", r'^TUNNEL_VERSION = "([0-9.]+)"'),
    ("mock_studio.py", r'^PLUGIN_VERSION = os\.environ\.get\("MOCK_PLUGIN_VERSION", "([0-9.]+)"\)'),
    ("ArenaBridge.lua", r'^local PLUGIN_VERSION = "([0-9.]+)"'),
)

# Version claims in prose. Each pattern captures the claimed version; the doc must
# either match one of these (a *precise* claim) or fall through to FALLBACK.
VERSION_CLAIMS = (
    r"bridge[^\d\n]{0,10}?v?(\d+\.\d+\.\d+)",
    r"server\.py[^\d\n]{0,4}?v?(\d+\.\d+\.\d+)",
    r"ArenaBridge\.lua[^\d\n]{0,40}?v?(\d+\.\d+\.\d+)",
    r"^#{1,3} .*?`?v(\d+\.\d+\.\d+)`?",
)
# The lookarounds keep 127.0.0.1 (and any 4-part number) out of a bare match; a
# naive `v?\d+\.\d+\.\d+` fallback flags every loopback URL in these docs.
FALLBACK = r"(?<![\d.])v?(\d+\.\d+\.\d+)(?![\d.])"

# Count claims in prose, matched against whitespace-normalised text so a claim
# wrapped across lines (a blockquote note, a table cell) still matches. Each entry
# is (fact key, pattern with exactly one capture group).
COUNT_CLAIMS = (
    ("tools", r"\*\*(\d+) Roblox Studio Tools"),
    ("tools", r"\*\*(\d+) MCP tools\*\*"),
    ("resources", r"Roblox Studio Tools, (\d+) Resources"),
    ("resources", r"MCP tools\*\*, (\d+) resources"),
    ("resourceTemplates", r"resources, (\d+) resource\s+templates"),
    ("prompts", r"Resources, (\d+) Prompts\*\*"),
    ("prompts", r"templates, (\d+) prompts"),
    ("skills", r"(\d+) bundled skills"),
    ("modules", r"\*\*(\d+) Luau `ModuleScript`s\*\*"),
    ("modules", r"(\d+) Luau gameplay/UI/utility modules"),
    ("modules", r"(\d+) Luau modules"),
    ("systems", r"\*\*(\d+) drop-in game systems\*\*"),
    ("systems", r"(\d+) server/client systems"),
    ("systems", r"(\d+) drop-in\s+systems"),
    ("uiTemplates", r"(\d+) UI templates"),
    ("prefabs", r"\*\*(\d+) prefabs\*\*"),
    ("prefabs", r"and (\d+) prefabs"),
    ("prefabs", r"(\d+) JSON prefabs"),
    ("prefabs", r"\*\*(\d+) ready-to-spawn JSON prefabs\*\*"),
    ("prefabs", r"\*\*(\d+) Ready-to-Spawn Prefabs"),
    ("models", r"\(\+\s*(\d+) `\.rbxmx` models\)"),
    ("models", r"\*\*(\d+) standalone `\.rbxmx` XML models"),
    ("assets", r"\*\*([\d,]+) hash-verified"),
    ("assets", r"asset registry \(`([\d,]+)` files\)"),
    ("assets", r"\*\*([\d,]+) licensed assets\*\*"),
    ("assets", r"([\d,]+) licensed kit assets"),
    ("tests", r"\((\d+) tests,"),
    ("tests", r"# (\d+) tests"),
)


def stamps() -> dict:
    """Every version literal in the release, keyed by file."""
    found = {}
    for name, pattern in VERSION_STAMPS:
        path = BRIDGE / name
        match = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
        if not match:
            raise SystemExit(f"cannot find the version literal in {name} ({pattern!r})")
        found[name] = match.group(1)
    return found


def facts() -> dict:
    """The numbers, computed. Nothing here is typed twice."""
    sys.path.insert(0, str(BRIDGE))
    import mcp_core  # noqa: PLC0415 - imported after sys.path is set

    def uri(item):
        return item.get("uri") or item.get("uriTemplate") or item.get("name")

    registry_assets = len(json.loads(
        (KIT / "assets" / "registry.json").read_text(encoding="utf-8"))["assets"])
    categories = sorted(p.name for p in (KIT / "systems").iterdir() if p.is_dir())
    systems = sum(1 for c in categories for p in (KIT / "systems" / c).iterdir() if p.is_dir())
    on_disk = sum(1 for p in (KIT / "assets").rglob("*") if p.is_file())
    version = stamps()

    return {
        "bridgeVersion": version["server.py"],
        "pluginVersion": version["ArenaBridge.lua"],
        "tools": len(mcp_core.TOOL_SPECS),
        "resources": len(mcp_core.RESOURCES),
        "resourceTemplates": len(mcp_core.RESOURCE_TEMPLATES),
        "prompts": len(mcp_core.PROMPTS),
        "skills": mcp_core.search_skills_helper("", 0, 100)["total"],
        "modules": len(list((KIT / "modules").glob("*.lua"))),
        "systemCategories": ", ".join(categories),
        "systems": systems,
        "uiTemplates": len(list((KIT / "templates" / "screens").glob("*.lua"))),
        "prefabs": len(list((KIT / "prefabs").glob("*.json"))),
        "models": len(list((KIT / "models").glob("*.rbxmx"))),
        "assets": registry_assets,
        "assetFilesOnDisk": on_disk,
        "jobs": len(list((ROOT / "jobs").glob("*.py"))),
        "tests": len(re.findall(r"^    def test_",
                                (BRIDGE / "test_bridge_mcp.py").read_text(encoding="utf-8"), re.M)),
        "resourceUris": ", ".join(uri(i) for i in mcp_core.RESOURCES),
        "templateUris": ", ".join(uri(i) for i in mcp_core.RESOURCE_TEMPLATES),
        "promptNames": ", ".join(uri(i) for i in mcp_core.PROMPTS),
        "versionStamps": ", ".join(f"{k} {v}" for k, v in sorted(version.items())),
    }


SOURCES = {
    "bridgeVersion": "`server.py` VERSION",
    "pluginVersion": "`ArenaBridge.lua` PLUGIN_VERSION",
    "tools": "`len(mcp_core.TOOL_SPECS)`",
    "resources": "`len(mcp_core.RESOURCES)`",
    "resourceTemplates": "`len(mcp_core.RESOURCE_TEMPLATES)`",
    "prompts": "`len(mcp_core.PROMPTS)`",
    "skills": "`search_skills_helper()` total",
    "modules": "`roblox-kit/modules/*.lua`",
    "systemCategories": "`roblox-kit/systems/*` directories",
    "systems": "system directories under those categories",
    "uiTemplates": "`roblox-kit/templates/screens/*.lua`",
    "prefabs": "`roblox-kit/prefabs/*.json`",
    "models": "`roblox-kit/models/*.rbxmx`",
    "assets": "`roblox-kit/assets/registry.json` entries",
    "assetFilesOnDisk": "files under `roblox-kit/assets/` (includes docs and contact sheets)",
    "jobs": "`jobs/*.py`",
    "tests": "`def test_` methods in `test_bridge_mcp.py`",
    "resourceUris": "`mcp_core.RESOURCES`",
    "templateUris": "`mcp_core.RESOURCE_TEMPLATES`",
    "promptNames": "`mcp_core.PROMPTS`",
    "versionStamps": "every version literal in the release",
}

ORDER = ("bridgeVersion", "pluginVersion", "versionStamps", "tools", "resources",
         "resourceTemplates", "resourceUris", "templateUris", "prompts", "promptNames",
         "skills", "modules", "systemCategories", "systems", "uiTemplates", "prefabs",
         "models", "assets", "assetFilesOnDisk", "jobs", "tests")


def render(f: dict) -> str:
    """Deterministic output: no timestamp, no commit id, so regeneration is a no-op."""
    rows = "\n".join(
        f"| `{key}` | {f[key] if not isinstance(f[key], int) else f'{f[key]:,}'} | {SOURCES[key]} |"
        for key in ORDER)
    return f"""# FACTS — generated, do not edit

Regenerate with `python3 -B tools/emit_facts.py`. CI and `make docs-check` run
`python3 -B tools/emit_facts.py --check`, which fails when this file is stale, when
the release carries more than one version, or when README.md, CONNECT.md,
AGENTS.md, roblox-bridge/README.md or roblox-bridge/docs/OPS.md states a number
that disagrees with the table below.

This file exists because hand-typed counts drift (P4-D2): the docs claimed 90 tools
against 100 in `TOOL_SPECS`, 11 resources against 12 in `RESOURCES`, a v4.1.0
plugin against a 6.1.0 one, and 20 prefabs against 36.

| Fact | Value | Source |
|---|---|---|
{rows}

## Which prose is a *current* claim

Dated history is not checked, because a count in a release note belongs to that
release. CONNECT.md keeps descending dated notes and only its newest one is scanned;
`roblox-bridge/docs/OPS.md` §7 ("MCP surface changes") records what each v5.x
release changed and is likewise left alone. Everything else in those five documents
is treated as a claim about today.
"""


NOTE = re.compile(r"^> \*\*v[0-9.]+ note")


def current_lines(doc: str, text: str) -> list:
    """(lineno, line) for the part of a doc that claims something about *today*.

    CONNECT.md keeps descending dated notes, so only the newest one is a current
    claim; the rest are history and their counts belong to their release. Blockquote
    markers are stripped because a claim wrapped across two `> ` lines is still one
    claim ("36 JSON prefabs (+20 `.rbxmx` models)" wraps in the middle)."""
    lines = text.splitlines()
    if doc != "CONNECT.md":
        return [(i, line) for i, line in enumerate(lines, 1)]
    starts = [i for i, line in enumerate(lines) if NOTE.match(line)]
    end = starts[1] if len(starts) > 1 else len(lines)
    return [(i + 1, re.sub(r"^>\s?", "", lines[i])) for i in range(end)]


def claim_text(doc: str, text: str) -> str:
    return "\n".join(line for _, line in current_lines(doc, text))


def check(f: dict) -> list:
    problems = []

    # Gate 2: one release, one version.
    version = f["bridgeVersion"]
    stamps_map = dict(re.findall(r"(\S+) ([0-9.]+)", f["versionStamps"]))
    for name, value in sorted(stamps_map.items()):
        if value != version:
            problems.append(f"version split: {name} says {value}, server.py says {version}")

    for doc in DOCS:
        text = (ROOT / doc).read_text(encoding="utf-8")

        # Gate 3a: version claims (current region only).
        precise = 0
        for lineno, line in current_lines(doc, text):
            for pattern in VERSION_CLAIMS:
                m = re.search(pattern, line)
                if not m:
                    continue
                precise += 1
                if m.group(1) != version:
                    problems.append(f"{doc}:{lineno}: claims {m.group(1)}, release is {version} :: {line.strip()[:100]}")
        if not precise:
            # No precise claim in this doc: fall back to every bare X.Y.Z so a
            # reworded version line cannot drop the doc out of the guard silently.
            for m in re.finditer(FALLBACK, claim_text(doc, text)):
                if m.group(1) != version:
                    problems.append(f"{doc}: bare version {m.group(0)} (doc carries no precise claim)")

        # Gate 3b: count claims.
        flat = re.sub(r"\s+", " ", claim_text(doc, text))
        for key, pattern in COUNT_CLAIMS:
            for m in re.finditer(pattern, flat):
                claimed = int(m.group(1).replace(",", ""))
                if claimed != f[key]:
                    problems.append(f"{doc}: claims {m.group(1)} {key}, tree has {f[key]:,} :: …{flat[max(0, m.start() - 40):m.end() + 30]}…")

    # Gate 3c: a pattern that matches nothing anywhere is a blind guard.
    flat_all = {doc: re.sub(r"\s+", " ", claim_text(doc, (ROOT / doc).read_text(encoding="utf-8"))) for doc in DOCS}
    for key, pattern in COUNT_CLAIMS:
        if not any(re.search(pattern, flat) for flat in flat_all.values()):
            problems.append(f"count pattern for {key} matches no doc any more: {pattern}")

    # Gate 1: the committed FACTS.md must be what the generator produces now.
    fresh = render(f)
    if not FACTS_PATH.is_file():
        problems.append(f"{FACTS_PATH.relative_to(ROOT)} does not exist - run `python3 -B tools/emit_facts.py`")
    elif FACTS_PATH.read_text(encoding="utf-8") != fresh:
        problems.append(f"{FACTS_PATH.relative_to(ROOT)} is stale - run `python3 -B tools/emit_facts.py`")
    return problems


def main(argv: list) -> int:
    # argparse, not a hand-rolled scan of argv: `--help` used to fall through to the
    # default action and rewrite docs/FACTS.md, which is the opposite of inert (P3-T7).
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="verify docs/FACTS.md and every doc claim; write nothing (exit 1 on drift)")
    ap.add_argument("--json", action="store_true",
                    help="print the computed facts as JSON instead of writing Markdown")
    args = ap.parse_args(argv)

    f = facts()
    if args.json:
        print(json.dumps(f, indent=2, sort_keys=True))
        return 0
    if args.check:
        problems = check(f)
        if problems:
            print("\n".join(problems))
            print(f"docs/FACTS.md check FAILED: {len(problems)} problem(s)")
            return 1
        print(f"docs agree with the tree: bridge {f['bridgeVersion']}, plugin {f['pluginVersion']}, "
              f"{f['tools']} tools / {f['resources']} resources / {f['resourceTemplates']} templates / "
              f"{f['prompts']} prompts / {f['skills']} skills / {f['modules']} modules / {f['systems']} systems / "
              f"{f['prefabs']} prefabs / {f['assets']:,} assets / {f['tests']} tests")
        return 0
    FACTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FACTS_PATH.write_text(render(f), encoding="utf-8")
    print(f"wrote {FACTS_PATH.relative_to(ROOT)} ({len(ORDER)} facts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
