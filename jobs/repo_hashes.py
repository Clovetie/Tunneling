#!/usr/bin/env python3
"""
Compute FNV-1a 32 hashes + byte sizes for the NightLoop package, the same way
jobs/drift_audit.lua does on the live side, so `live vs repo` is a one-line
diff. Prints one line per file, in the format of
jobs/repo_hashes_2026-10-07.txt:

    NightLoop.Config<TAB>2306692021<TAB>7348

Usage:
    python3 jobs/repo_hashes.py                # print
    python3 jobs/repo_hashes.py --write        # refresh the recorded table
    python3 jobs/repo_hashes.py --check FILE   # compare against a recorded table
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TABLE = os.path.join(ROOT, "jobs", "repo_hashes_2026-10-07.txt")

# (repo path, live key) — exactly the files that belong in the place.
MANIFEST = [
    ("nightloop/src/Atmosphere.lua", "NightLoop.Atmosphere"),
    ("nightloop/src/Beam.lua", "NightLoop.Beam"),
    ("nightloop/src/Bootstrap.server.lua", "NightLoop.Bootstrap"),
    ("nightloop/src/Config.lua", "NightLoop.Config"),
    ("nightloop/src/CrawlerRig.lua", "NightLoop.CrawlerRig"),
    ("nightloop/src/Director.lua", "NightLoop.Director"),
    ("nightloop/src/EntityBase.lua", "NightLoop.EntityBase"),
    ("nightloop/src/Flash.lua", "NightLoop.Flash"),
    ("nightloop/src/MonsterAnimator.lua", "NightLoop.MonsterAnimator"),
    ("nightloop/src/MonsterRig.lua", "NightLoop.MonsterRig"),
    ("nightloop/src/Net.lua", "NightLoop.Net"),
    ("nightloop/src/Registry.lua", "NightLoop.Registry"),
    ("nightloop/src/Signal.lua", "NightLoop.Signal"),
    ("nightloop/src/Entities/Breathless.lua", "NightLoop.Entities.Breathless"),
    ("nightloop/src/Entities/Crawl.lua", "NightLoop.Entities.Crawl"),
    ("nightloop/src/Entities/Knocker.lua", "NightLoop.Entities.Knocker"),
    ("nightloop/src/Entities/TickingMan.lua", "NightLoop.Entities.TickingMan"),
    ("nightloop/src/Entities/Whisperer.lua", "NightLoop.Entities.Whisperer"),
    ("nightloop/src/Entities/WindowMonster.lua", "NightLoop.Entities.WindowMonster"),
    ("nightloop/src/Entities/_Template.lua", "NightLoop.Entities._Template"),
    ("nightloop/client/NightLoopClient.client.lua", "NightLoopClient"),
    ("nightloop/client/NightLoopFirstPerson.client.lua", "NightLoopFirstPerson"),
]

OFFSET = 2166136261
PRIME = 16777619


def fnv1a32(data):
    h = OFFSET
    for byte in data:
        h ^= byte
        h = (h * PRIME) & 0xFFFFFFFF
    return h


def digest(rel):
    with open(os.path.join(ROOT, rel), "rb") as fh:
        raw = fh.read()
    return fnv1a32(raw), len(raw)


def table():
    return [(key, *digest(rel)) for rel, key in MANIFEST]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", default=None)
    args = ap.parse_args()

    rows = table()
    if args.check:
        recorded = {}
        with open(args.check, encoding="utf-8") as fh:
            for line in fh:
                parts = line.strip().split("\t")
                if len(parts) == 3:
                    recorded[parts[0]] = (int(parts[1]), int(parts[2]))
        bad = 0
        for key, h, n in rows:
            old = recorded.get(key)
            if old is None:
                print(f"+ {key}\t{h}\t{n}   (not in {os.path.basename(args.check)})")
                bad += 1
            elif old != (h, n):
                print(f"! {key}  recorded {old[0]}/{old[1]}  now {h}/{n}")
                bad += 1
        for key in recorded:
            if key not in {k for k, _, _ in rows}:
                print(f"- {key}   (recorded but not in the manifest)")
                bad += 1
        print(f"{len(rows)} files, {bad} difference(s)")
        return 1 if bad else 0

    text = "".join(f"{key}\t{h}\t{n}\n" for key, h, n in rows)
    if args.write:
        target = DEFAULT_TABLE
        with open(target, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"wrote {target} ({len(rows)} files)")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
