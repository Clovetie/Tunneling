#!/usr/bin/env bash
# Pre-push gate for the Rojo-over-git route (see nightloop/docs/ROJO-SYNC.md).
# Run it before every push: whatever is pushed here is what the user's pull
# loop hands to Rojo, and from there into Studio.
#
#   1. Rojo layout  node tools/validate-project.js
#   2. Syntax       luau-compile --binary on every .lua in the synced trees
#   3. Globals      luau-analyze, minus the Roblox API surface. Same filter as
#                   tools/check_globals.sh, but that script assumes the binaries
#                   sit next to a copy of the package, so this one finds them itself.
#
# Usage (from anywhere):  bash nightloop/tools/precheck.sh
# Override the binaries:  LUAU_BIN_DIR=/path/to/dir bash nightloop/tools/precheck.sh
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # .../nightloop/tools
PKG="$(cd "$HERE/.." && pwd)"                          # .../nightloop
REPO="$(cd "$PKG/.." && pwd)"                          # repository root

BIN_DIR="${LUAU_BIN_DIR:-}"
if [ -z "$BIN_DIR" ]; then
  for cand in "$REPO/roblox-bridge/tools" "$HERE"; do
    if [ -f "$cand/luau-compile" ] && [ -f "$cand/luau-analyze" ]; then
      BIN_DIR="$cand"
      break
    fi
  done
fi
if [ -z "$BIN_DIR" ] || [ ! -x "$BIN_DIR/luau-compile" ] || [ ! -x "$BIN_DIR/luau-analyze" ]; then
  echo "precheck: luau-compile / luau-analyze missing or not executable." >&2
  echo "precheck: run  bash roblox-bridge/tools/fix-perms.sh  and try again." >&2
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "precheck: node is required for tools/validate-project.js" >&2
  exit 1
fi

KNOWN='game|workspace|script|Enum|Instance|CFrame|Vector3|Vector2|Color3|UDim2|UDim|TweenInfo|RaycastParams|Random|NumberSequence|NumberRange|ColorSequence|BrickColor|Ray|Region3|Rect|Font|task|warn|print|wait|spawn|delay|tick|time|typeof|require|shared|settings|DateTime|PhysicalProperties|OverlapParams|Axes|Faces|debug|utf8|bit32|buffer|os|math|string|table|coroutine|select|unpack|newproxy|gcinfo'

cd "$PKG" || exit 1
fail=0

echo "== 1/3 Rojo layout"
if ! node tools/validate-project.js; then
  fail=1
fi

echo "== 2/3 syntax (luau-compile)"
files=0
while IFS= read -r -d '' f; do
  files=$((files + 1))
  if ! err=$("$BIN_DIR/luau-compile" --binary "$f" 2>&1 >/dev/null); then
    echo "$err"
    fail=1
  fi
done < <(find src client tool -name '*.lua' -print0 | sort -z)
echo "checked $files file(s)"

echo "== 3/3 undefined globals (luau-analyze)"
gfail=0
while IFS= read -r -d '' f; do
  out=$("$BIN_DIR/luau-analyze" "$f" 2>&1 \
        | grep -E 'SyntaxError|Unknown global' \
        | grep -Ev "Unknown global '($KNOWN)'" || true)
  if [ -n "$out" ]; then
    echo "$out"
    gfail=1
  fi
done < <(find src client tool -name '*.lua' -print0 | sort -z)
[ $gfail -eq 0 ] && echo "clean - no undefined globals"
[ $gfail -ne 0 ] && fail=1

if [ $fail -ne 0 ]; then
  echo "precheck: FAILED. Do not push."
  exit 1
fi
echo "precheck: passed"
exit 0
