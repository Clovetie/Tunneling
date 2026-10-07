#!/usr/bin/env bash
# Run before committing.
#
# setup.py rewrites bridge/ArenaBridge.lua in place, baking in the live tunnel
# URL and token. That is correct behaviour for installing the plugin, but it
# means the working copy of a tracked file can pick up a real credential. This
# refuses to let that reach a commit.
set -u
cd "$(dirname "$0")/.."
bad=0

# a baked tunnel URL in the plugin
if git ls-files -z | xargs -0 grep -nIE 'https://[a-z0-9-]+\.trycloudflare\.com' 2>/dev/null \
     | grep -v 'some-random-words' | grep -v '<the-tunnel>' | grep -v 'TUNNEL_RE'; then
  echo "!! a live tunnel URL is present in a tracked file"; bad=1
fi

# a token that is neither empty nor the documented demo placeholder
tok=$(grep -oP 'local BRIDGE_TOKEN = "\K[^"]*' bridge/ArenaBridge.lua 2>/dev/null || true)
if [ -n "${tok:-}" ] && [ "$tok" != "arena-demo-7f3a" ]; then
  echo "!! bridge/ArenaBridge.lua has a non-placeholder token baked in: $tok"
  echo "   restore the placeholder before committing:"
  echo "   sed -i 's|local BRIDGE_TOKEN = \"$tok\"|local BRIDGE_TOKEN = \"arena-demo-7f3a\"|' bridge/ArenaBridge.lua"
  bad=1
fi

if git ls-files | grep -qE '(^|/)(bridge\.token|SESSION\.md|\.env)$'; then
  echo "!! a credential file is tracked"; bad=1
fi

[ $bad -eq 0 ] && echo "clean - no credentials in tracked files"
exit $bad
