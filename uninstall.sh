#!/bin/bash
set -euo pipefail

LAUNCHCTL="${LAUNCHCTL:-launchctl}"
AGENTS_DIR="$HOME/Library/LaunchAgents"

for plist in "$AGENTS_DIR"/com.mac-organizer.*.plist; do
  [ -e "$plist" ] || continue
  "$LAUNCHCTL" bootout "gui/$(id -u)" "$plist" 2>/dev/null \
    || "$LAUNCHCTL" unload -w "$plist" 2>/dev/null || true
  rm -f "$plist"
done

echo "Removed mac-organizer launchd agents. Files, rules.toml, and logs are untouched."
