#!/usr/bin/env bash
# Full-suite runner for a memory-constrained host (audit 4.3).
#
# Why this exists: the suite must never run in the foreground of an agent tool.
# One kaleido export holds a ~277MB Chromium tree for the life of the
# interpreter, and on a 985MB box that pushes the next allocation into swap —
# where the visible symptom is a stall in kaleido/scopes/base.py:308, not an
# OOM message. Detaching plus a hard per-test deadline keeps that diagnosable.
#
# Usage: tools/run_full_suite.sh [logfile] [pytest args...]
set -u

LOG="${1:-/tmp/full.log}"
shift || true

cd "$(dirname "$0")/.." || exit 1

# Reap any renderer trees orphaned by an earlier crashed run; they are pure
# memory reservation and are the difference between 34% and 93% completion.
pkill -9 -f kaleido >/dev/null 2>&1
sleep 1

{
    echo "=== host state before run ==="
    free -m
    echo "=== $(date -Is) starting ==="
} > "$LOG" 2>&1

timeout 2400 python3 -m pytest tests/ -q \
    --timeout=120 --timeout-method=thread \
    --tb=line -p no:cacheprovider "$@" >> "$LOG" 2>&1
rc=$?

{
    echo "=== $(date -Is) finished rc=$rc ==="
    echo "(rc=124 => outer timeout; rc=137 => OOM-killed; rc=0 => green)"
    echo "=== host state after run ==="
    free -m
} >> "$LOG" 2>&1

echo "$rc" > "${LOG}.rc"
exit "$rc"
