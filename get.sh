#!/bin/bash
# One-line install:
#   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/thealiceiris/mac-organizer/main/get.sh)"
# Downloads mac-organizer to ~/.mac-organizer (or updates it) and runs install.sh.
set -euo pipefail

REPO_URL="${MAC_ORGANIZER_REPO_URL:-https://github.com/thealiceiris/mac-organizer.git}"
DIR="${MAC_ORGANIZER_DIR:-$HOME/.mac-organizer}"
# Homebrew's bin is not always on PATH yet right after installing it.
BREW_DIRS="${MAC_ORGANIZER_BREW_DIRS:-/opt/homebrew/bin:/usr/local/bin}"

fail() {
  echo "error: $*" >&2
  exit 1
}

# `curl ... | bash` gives install.sh a pipe instead of the terminal, so its
# "Load the background sorter now?" question would be skipped and files would
# start moving unasked. MAC_ORGANIZER_FORCE_TTY=1 stands in for a terminal in
# tests; MAC_ORGANIZER_ASSUME_YES=1 opts out of the questions on purpose.
INTERACTIVE=0
if [ -t 0 ] || [ -n "${MAC_ORGANIZER_FORCE_TTY:-}" ]; then
  INTERACTIVE=1
fi
if [ "$INTERACTIVE" = 0 ] && [ -z "${MAC_ORGANIZER_ASSUME_YES:-}" ]; then
  fail 'run this in a terminal, like this:
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/thealiceiris/mac-organizer/main/get.sh)"'
fi

[ "$(uname -s)" = Darwin ] || fail "mac-organizer only runs on macOS"

git --version >/dev/null 2>&1 \
  || fail "git is not available; install Apple's command line tools first: xcode-select --install"

python_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

find_python() {
  local candidate dir
  candidate="$(command -v python3 || true)"
  if [ -n "$candidate" ] && python_ok "$candidate"; then
    echo "$candidate"
    return 0
  fi
  local IFS=:
  for dir in $BREW_DIRS; do
    if [ -x "$dir/python3" ] && python_ok "$dir/python3"; then
      echo "$dir/python3"
      return 0
    fi
  done
  return 1
}

find_brew() {
  local dir
  command -v brew && return 0
  local IFS=:
  for dir in $BREW_DIRS; do
    [ -x "$dir/brew" ] && echo "$dir/brew" && return 0
  done
  return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  BREW="$(find_brew || true)"
  [ -n "$BREW" ] || fail "mac-organizer needs Python 3.11 or newer. Install Homebrew first (https://brew.sh), then run this again."
  ANSWER=""
  if [ "$INTERACTIVE" = 1 ]; then
    echo "mac-organizer needs Python 3.11 or newer."
    printf 'Install Python with Homebrew now? [y/N] '
    read -r ANSWER || ANSWER=""
    echo
  fi
  case "$(printf '%s' "$ANSWER" | tr '[:upper:]' '[:lower:]')" in
    y|yes) "$BREW" install python ;;
    *) fail "mac-organizer needs Python 3.11 or newer. Install it with: brew install python" ;;
  esac
  PYTHON="$(find_python || true)"
  [ -n "$PYTHON" ] || fail "Python 3.11 or newer still not found after brew install python"
fi

if [ -d "$DIR/.git" ]; then
  echo "Updating mac-organizer in $DIR"
  git -C "$DIR" pull --ff-only -q
elif [ -e "$DIR" ]; then
  fail "$DIR already exists and is not a mac-organizer download; move it away and run this again"
else
  echo "Downloading mac-organizer to $DIR"
  git clone -q "$REPO_URL" "$DIR"
fi

# install.sh uses the first python3 on PATH, so put the one found here first.
PATH="$(dirname "$PYTHON"):$PATH"
export PATH
exec bash "$DIR/install.sh"
