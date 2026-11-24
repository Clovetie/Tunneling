#!/usr/bin/env bash
# Leak guard (P3-C3): fail if a credential, a private key or an owner token is
# tracked. This repo has already had a token in its history - SESSION.md, deleted in
# 6.1.0, was a tracked file titled "live connector credentials" - so the guard checks
# the *tree*, and history is the operator's problem (see the note below).
#
# Exit codes: 0 clean, 1 something is tracked that must not be.
set -uo pipefail
cd "$(dirname "$0")/.."

status=0

# 1. Files that are runtime state or credentials, wherever --state-dir puts them.
FORBIDDEN='(^|/)(bridge\.token|tokens\.json|audit\.jsonl|fleet\.json|fleet\.json\.corrupt-[0-9]+|bus\.json|tunnel\.json|SESSION\.md)$|(^|/)bridge_state/|\.pem$'
tracked=$(git ls-files | grep -E "$FORBIDDEN" || true)
if [ -n "$tracked" ]; then
    echo "FAIL: runtime state or credentials are tracked:"
    echo "$tracked" | sed 's/^/    /'
    echo "  Untrack with: git rm --cached <path>   (and add it to .gitignore)"
    status=1
else
    echo "no credential or state file is tracked"
fi

# 2. Owner-token literals. auth_core mints `arb_` + token_urlsafe(30), so a real token
#    is 44+ characters; the floor of 20 keeps prose (`arb_…` in OPS.md), the generator
#    itself and test assertions (`startswith("arb_")`) out of the match.
tokens=$(git grep -InE 'arb_[A-Za-z0-9_-]{20,}' -- . || true)
if [ -n "$tokens" ]; then
    echo "FAIL: something that looks like a live owner token is committed:"
    echo "$tokens" | cut -c1-160 | sed 's/^/    /'
    echo "  Rotate it: python3 roblox-bridge/setup.py --rotate"
    status=1
else
    echo "no owner-token literal in any tracked file"
fi

# 3. Private keys. `git grep -I` skips binary files (the kit's 4,320 assets). The label
#    is restricted to [A-Z0-9 ] because prose about this very guard is tracked:
#    handoff/NEXT-AGENT-6.1.md asks for a job that fails on a
#    `-----BEGIN … PRIVATE KEY-----` block, and a loose `.*` label matched its own
#    request. A real PEM header (RSA, EC, OPENSSH, ENCRYPTED, or bare) still matches.
keys=$(git grep -Il -e '-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----' -- . || true)
if [ -n "$keys" ]; then
    echo "FAIL: a private key block is tracked:"
    echo "$keys" | sed 's/^/    /'
    status=1
else
    echo "no private key block in any tracked file"
fi

if [ $status -eq 0 ]; then
    echo
    echo "Note: this guards the current tree, not history. A secret that was ever committed"
    echo "stays in history until the repo is rewritten, so rotate it rather than deleting it:"
    echo "    python3 roblox-bridge/setup.py --rotate"
fi
exit $status
