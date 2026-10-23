#!/usr/bin/env bash
# Surface genuinely-undefined globals, filtering out the Roblox API surface
# that luau-analyze does not ship types for. This is the check that would have
# caught `placeAt` being deleted out from under its last caller.
KNOWN='game|workspace|script|plugin|version|loadstring|Enum|Instance|CFrame|Vector3|Vector2|Color3|UDim2|UDim|TweenInfo|RaycastParams|Random|NumberSequence|NumberRange|ColorSequence|BrickColor|Ray|Region3|Rect|Font|task|warn|print|wait|spawn|delay|tick|time|typeof|require|shared|settings|DateTime|PhysicalProperties|OverlapParams|Axes|Faces|debug|utf8|bit32|buffer|os|math|string|table|coroutine|select|unpack|newproxy|gcinfo'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANALYZER=""
for cand in \
  "$SCRIPT_DIR/luau-analyze" \
  "$SCRIPT_DIR/../.tools/bin/luau-analyze" \
  "$SCRIPT_DIR/../../roblox-bridge/tools/luau-analyze" \
  "$(command -v luau-analyze 2>/dev/null || true)"; do
  if [ -n "$cand" ] && [ -x "$cand" ]; then
    ANALYZER="$cand"
    break
  fi
done

if [ -z "$ANALYZER" ]; then
  echo "luau-analyze binary not found (run tools/fix-perms.sh first)" >&2
  exit 1
fi

TARGET="${1:-.}"
found=0
for f in $(find "$TARGET" -name '*.lua' -o -name '*.luau' | sort); do
  out=$("$ANALYZER" "$f" 2>&1 \
        | grep "Unknown global" \
        | grep -Ev "Unknown global '($KNOWN)'" || true)
  if [ -n "$out" ]; then echo "$out"; found=1; fi
done
if [ $found -eq 0 ]; then
  echo "clean - no undefined globals"
fi
exit $found
