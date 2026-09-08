#!/bin/bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHCTL="${LAUNCHCTL:-launchctl}"
AGENTS_DIR="$HOME/Library/LaunchAgents"

PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ]; then
  echo "error: python3 not found on PATH" >&2
  exit 1
fi

# /usr/bin/python3 on a stock Mac is 3.9 and has no tomllib. Catch it here:
# otherwise setup.py bakes that interpreter into the agents and every launchd
# fire fails silently into the error log.
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "error: $PYTHON is $("$PYTHON" -V 2>&1); mac-organizer needs Python 3.11+ (brew install python)" >&2
  exit 1
fi

# Unload anything from a previous install before setup.py rewrites the set, so
# re-running is safe and agents that are no longer generated do not linger.
for plist in "$AGENTS_DIR"/com.mac-organizer.*.plist; do
  [ -e "$plist" ] || continue
  "$LAUNCHCTL" bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
done

"$PYTHON" "$REPO/setup.py"

for plist in "$AGENTS_DIR"/com.mac-organizer.*.plist; do
  [ -e "$plist" ] || continue
  "$LAUNCHCTL" bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
  "$LAUNCHCTL" bootstrap "gui/$(id -u)" "$plist" 2>/dev/null \
    || "$LAUNCHCTL" load -w "$plist" 2>/dev/null \
    || echo "warning: could not load $plist" >&2
done

echo
echo "Dry-run preview of what the router would do now:"
"$PYTHON" "$REPO/route.py" --dry-run
