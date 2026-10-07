#!/usr/bin/env bash
# Workspace snapshots do not preserve the executable bit. Run this first.
chmod +x "$(dirname "$0")"/luau-compile "$(dirname "$0")"/luau-analyze \
         "$(dirname "$0")"/*.sh 2>/dev/null
echo "tools executable"
