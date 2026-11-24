#!/usr/bin/env bash
# Lint gate for the bridge. Two rules, one command, same string locally and in CI:
#
#   1. `ruff check` must be clean under pyproject.toml (E, F, B — everything except
#      the two rules documented as ignored there).
#   2. The blind-except count (BLE001) may only go *down*. It is not zero today, and
#      pretending otherwise by ignoring it silently is how P1-B6 stayed invisible;
#      so the number is committed in tools/lint_ble_baseline.txt and this script
#      fails when a change adds a handler. Lower the baseline when you remove one.
#
# Exit codes: 0 clean, 1 lint failure, 2 ruff not installed.
set -uo pipefail
cd "$(dirname "$0")/.."

PATHS=(roblox-bridge jobs tools)
BASELINE_FILE="tools/lint_ble_baseline.txt"

RUFF="${RUFF_BIN:-}"
if [ -z "$RUFF" ]; then
    if command -v ruff >/dev/null 2>&1; then
        RUFF="ruff"
    elif python3 -m ruff --version >/dev/null 2>&1; then
        RUFF="python3 -m ruff"
    else
        echo "ruff is not installed. It is a lint-time dependency only; the bridge runtime stays stdlib-only."
        echo "Install it with one of:"
        echo "    python3 -m venv .venv && .venv/bin/pip install ruff && RUFF_BIN=.venv/bin/ruff bash $0"
        echo "    pip install --break-system-packages ruff        # PEP 668 hosts"
        exit 2
    fi
fi

status=0
echo "== gate 1: ruff check (pyproject.toml) =="
if ! $RUFF check "${PATHS[@]}"; then
    echo "FAIL: ruff found violations above."
    status=1
else
    echo "clean"
fi

echo
echo "== gate 2: BLE001 ratchet =="
baseline=$(tr -dc '0-9' < "$BASELINE_FILE")
# --isolated so pyproject's ignore list cannot hide the rule being counted.
current=$($RUFF check --isolated --target-version py310 --select BLE001 \
    --output-format concise "${PATHS[@]}" 2>/dev/null | grep -c 'BLE001' || true)
echo "blind 'except Exception' handlers: $current (baseline $baseline)"
if [ "$current" -gt "$baseline" ]; then
    echo "FAIL: $((current - baseline)) new blind except handler(s)."
    echo "Either handle the specific exception, or log-and-continue with a comment"
    echo "explaining why the broad catch is correct - then raise $BASELINE_FILE deliberately."
    status=1
elif [ "$current" -lt "$baseline" ]; then
    echo "ratchet improved - lower $BASELINE_FILE to $current so the gain is kept."
fi

exit $status
