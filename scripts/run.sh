#!/bin/sh
# Find a working Python 3.8+ and run ayt.py with the given arguments.
# On Windows, `python3` is often the Microsoft Store stub and `py` can point at
# a removed install, so each candidate is actually executed before use.
# No Python at all: exit 0 so Claude Code carries on as if the plugin weren't there.
here=$(dirname "$0")
for py in "$ARE_YOU_THERE_PYTHON" python3 python py; do
  [ -n "$py" ] || continue
  if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    exec "$py" "$here/ayt.py" "$@"
  fi
done
echo "are-you-there: Python 3.8+ not found, skipping" >&2
exit 0
