#!/usr/bin/env bash
# Surface genuinely-undefined globals, filtering out the Roblox API surface
# that luau-analyze does not ship types for. This is the check that would have
# caught `placeAt` being deleted out from under its last caller.
KNOWN='game|workspace|script|Enum|Instance|CFrame|Vector3|Vector2|Color3|UDim2|UDim|TweenInfo|RaycastParams|Random|NumberSequence|NumberRange|ColorSequence|BrickColor|Ray|Region3|Rect|Font|task|warn|print|wait|spawn|delay|tick|time|typeof|require|shared|settings|DateTime|PhysicalProperties|OverlapParams|Axes|Faces|debug|utf8|bit32|buffer|os|math|string|table|coroutine|select|unpack|newproxy|gcinfo'
found=0
for f in $(find . -name '*.lua' | sort); do
  out=$(../tools/luau-analyze "$f" 2>&1 \
        | grep "Unknown global" \
        | grep -Ev "Unknown global '($KNOWN)'" || true)
  if [ -n "$out" ]; then echo "$out"; found=1; fi
done
[ $found -eq 0 ] && echo "clean - no undefined globals"
exit 0
