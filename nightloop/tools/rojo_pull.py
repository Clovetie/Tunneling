#!/usr/bin/env python3
"""Keep a local clone current so `rojo serve` can sync it into Roblox Studio.

Run this on the machine that runs Studio, in its own terminal, from anywhere
inside the clone:

    python nightloop/tools/rojo_pull.py           # check every 15 s; Ctrl+C stops
    python nightloop/tools/rojo_pull.py --once    # one check, then exit

Safety rules. This script never discards work:
  * Fast-forward only. It never merges, rebases, resets, or stashes.
  * It does nothing while tracked files have local edits, or while the branch
    has commits that the remote does not have.
  * It never waits on a sign-in prompt. If git needs you to sign in, it says so
    and retries later. Run `git pull` once by hand to sign in.

Rojo watches the folder on disk. This script only moves git. Keep
`rojo serve` running in nightloop/ as described in nightloop/docs/ROJO-SYNC.md.
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

# Folders that nightloop/default.project.json maps into Studio.
SYNCED = ("nightloop/src/", "nightloop/client/")

_last_message = None


def say(msg, force=False):
    """Print a status line. Repeats of the previous line are suppressed."""
    global _last_message
    if force or msg != _last_message:
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)
        _last_message = msg


def git(args, cwd, timeout=90):
    """Run git without ever prompting. A hang becomes a failed result."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "timed out")


def find_root(start):
    r = git(["rev-parse", "--show-toplevel"], start)
    if r.returncode != 0:
        sys.exit("rojo_pull: this script must live inside a git clone of the repository")
    return r.stdout.strip()


def first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return "(no output)"


def count(root, rev_range):
    r = git(["rev-list", "--count", rev_range], root)
    return int(r.stdout.strip() or 0) if r.returncode == 0 else -1


def check_once(root):
    up = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root)
    if up.returncode != 0:
        say("this branch has no upstream. Check out the session branch first: "
            "git fetch, then git checkout arena/04020e4e-tunneling")
        return
    upstream = up.stdout.strip()

    fetch = git(["fetch", "--quiet"], root)
    if fetch.returncode != 0:
        say("fetch failed, retrying: " + first_line(fetch.stderr)
            + " (if git asks you to sign in, run git pull once in this folder)")
        return

    behind = count(root, f"HEAD..{upstream}")
    ahead = count(root, f"{upstream}..HEAD")
    if behind < 0 or ahead < 0:
        say("could not compare with " + upstream + "; retrying")
        return
    if behind == 0 and ahead == 0:
        say(f"up to date with {upstream}")
        return
    if behind == 0:
        say(f"{ahead} local commit(s) not on {upstream}; nothing to pull")
        return
    if ahead > 0:
        say(f"diverged: {ahead} local and {behind} remote commit(s). "
            "Not pulling. Resolve by hand.")
        return

    dirty = git(["status", "--porcelain", "--untracked-files=no"], root).stdout.strip()
    if dirty:
        say(f"{behind} new commit(s) on {upstream}, but tracked files have local "
            "edits. Not pulling. Commit, stash, or undo those edits first.")
        return

    before = git(["rev-parse", "HEAD"], root).stdout.strip()
    merge = git(["merge", "--ff-only", upstream], root)
    if merge.returncode != 0:
        say("fast-forward refused: " + first_line(merge.stderr))
        return
    after = git(["rev-parse", "HEAD"], root).stdout.strip()

    changed = []
    for line in git(["diff", "--name-status", before, after], root).stdout.splitlines():
        paths = line.split("\t")[1:]
        if any(p.startswith(SYNCED) for p in paths):
            changed.append(line)
    say(f"pulled {behind} commit(s); {len(changed)} file(s) in the Rojo trees changed",
        force=True)
    for line in changed:
        say("  " + line, force=True)


def main():
    ap = argparse.ArgumentParser(
        description="Fast-forward the local clone so Rojo can sync it into Studio.")
    ap.add_argument("--interval", type=int, default=15,
                    help="seconds between checks (default 15, minimum 5)")
    ap.add_argument("--once", action="store_true",
                    help="check and pull once, then exit")
    args = ap.parse_args()

    root = find_root(os.path.dirname(os.path.abspath(__file__)))
    say(f"watching {root}. Rojo syncs whatever this pulls.", force=True)

    if args.once:
        check_once(root)
        return 0

    try:
        while True:
            try:
                check_once(root)
            except Exception as exc:  # keep the loop alive; report and retry
                say(f"error, retrying: {exc}")
            time.sleep(max(5, args.interval))
    except KeyboardInterrupt:
        say("stopped", force=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
