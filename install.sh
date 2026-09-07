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

"$PYTHON" "$REPO/setup.py"

for plist in "$AGENTS_DIR"/com.mac-organizer.*.plist; do
  [ -e "$plist" ] || continue
  "$LAUNCHCTL" bootstrap "gui/$(id -u)" "$plist" 2>/dev/null \
    || "$LAUNCHCTL" load -w "$plist"
done

echo
echo "Dry-run preview of what the router would do now:"
"$PYTHON" "$REPO/route.py" --dry-run
