#!/bin/bash
# Interactive run of one issue (you answer any prompts; the guard still applies). The
# prompt is built by run.py, so it is the same brief the unattended loop gives. This is
# how Active issues get worked: you in the room, Claude doing the typing.
#
# Usage: lazyboy/once.sh 012
cd "$(dirname "$0")/.." || exit 1
target="$1"
[ -z "$target" ] && { echo "usage: lazyboy/once.sh <NNN|issues/NNN>"; exit 1; }
py=.venv/Scripts/python.exe
[ -x "$py" ] || py=.venv/bin/python
[ -x "$py" ] || py=python
prompt=$("$py" lazyboy/run.py "$target" --print-prompt --include-active) || exit 1
claude --permission-mode acceptEdits --settings lazyboy/settings.json "$prompt"
