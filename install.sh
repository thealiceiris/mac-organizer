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

# Smart sort needs three external tools. launchd's PATH lacks Homebrew, so look
# in the usual Homebrew dirs as well (MAC_ORGANIZER_TOOL_DIRS overrides them in
# tests). A missing tool only degrades features, so this never fails the install.
TOOL_DIRS="${MAC_ORGANIZER_TOOL_DIRS:-/opt/homebrew/bin:/usr/local/bin}"
have_tool() {
  command -v "$1" >/dev/null 2>&1 && return 0
  local dir
  local IFS=:
  for dir in $TOOL_DIRS; do
    [ -x "$dir/$1" ] && return 0
  done
  return 1
}
MISSING=""
have_tool llama-server || MISSING="$MISSING llama.cpp"
have_tool pdftotext || MISSING="$MISSING poppler"
have_tool terminal-notifier || MISSING="$MISSING terminal-notifier"
if [ -n "$MISSING" ]; then
  echo "warning: optional tools missing (smart sort, PDF reading or notifications will be limited):"
  echo "  brew install${MISSING}"
fi

# On a busy Mac, ask before the sorter starts moving things on its own.
# MAC_ORGANIZER_FORCE_TTY=1 stands in for a terminal in tests; ASSUME_YES skips the question.
LOAD_AGENTS=1
if { [ -t 0 ] || [ -n "${MAC_ORGANIZER_FORCE_TTY:-}" ]; } && [ -z "${MAC_ORGANIZER_ASSUME_YES:-}" ]; then
  DL_COUNT=0
  if [ -d "$HOME/Downloads" ]; then
    DL_COUNT=$(find "$HOME/Downloads" -mindepth 1 -maxdepth 1 ! -name '.*' 2>/dev/null | wc -l | tr -d ' ')
  fi
  DOC_COUNT=0
  if [ -d "$HOME/Documents" ]; then
    DOC_COUNT=$(find "$HOME/Documents" -mindepth 1 -maxdepth 1 -type f ! -name '.*' 2>/dev/null | wc -l | tr -d ' ')
  fi
  if [ "$DL_COUNT" -gt 50 ] || [ "$DOC_COUNT" -gt 20 ]; then
    echo "You have $DL_COUNT items in Downloads and $DOC_COUNT loose files in Documents."
    echo "Recommended: preview first, nothing moves until you approve:"
    echo "  python3 setup.py && python3 route.py --backlog ~/Downloads ~/Documents"
    printf 'Load the background sorter now? [y/N] '
    ANSWER=""
    read -r ANSWER || ANSWER=""
    echo
    case "$(printf '%s' "$ANSWER" | tr '[:upper:]' '[:lower:]')" in
      y|yes) ;;
      *) LOAD_AGENTS=0 ;;
    esac
  fi
fi

# Unload anything from a previous install before setup.py rewrites the set, so
# re-running is safe and agents that are no longer generated do not linger.
for plist in "$AGENTS_DIR"/com.mac-organizer.*.plist; do
  [ -e "$plist" ] || continue
  "$LAUNCHCTL" bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
done

if [ "$LOAD_AGENTS" = 0 ]; then
  # no agent plists left behind: launchd would load them at next login
  "$PYTHON" "$REPO/setup.py" --no-agents
else
  "$PYTHON" "$REPO/setup.py"
fi

if [ "$LOAD_AGENTS" = 0 ]; then
  echo
  echo "The background sorter is not loaded. Preview with:"
  echo "  python3 route.py --backlog ~/Downloads ~/Documents"
  echo "When you are ready, run ./install.sh to load it."
  exit 0
fi

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
