#!/bin/zsh
set -e
cd "$(dirname "$0")"
for PY in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
  if command -v "$PY" >/dev/null 2>&1; then
    exec "$PY" app.py "$@"
  fi
done
echo "Python 3 was not found." >&2
exit 1
