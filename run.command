#!/bin/zsh
set -e
cd "$(dirname "$0")"
exec /opt/homebrew/bin/python3 app.py "$@"
