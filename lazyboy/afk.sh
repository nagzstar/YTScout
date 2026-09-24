#!/bin/bash
# Unattended loop: one Claude session per issue, verified, then the next.
# Usage: lazyboy/afk.sh [target] [--limit N] [--from NNN] [--push] [--yolo] [--model m] [--dry-run]
#   lazyboy/afk.sh                 every eligible AFK issue, lowest number first
#   lazyboy/afk.sh 012             that issue only
#   lazyboy/afk.sh --dry-run       what would run, what is blocked, what needs you
cd "$(dirname "$0")/.." || exit 1
py=.venv/Scripts/python.exe
[ -x "$py" ] || py=.venv/bin/python
[ -x "$py" ] || py=python
exec "$py" lazyboy/run.py "$@"
