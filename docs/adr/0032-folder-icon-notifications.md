# 0032. Notifications with a folder icon, from our own copy of terminal-notifier

- **Status:** Accepted
- **Date:** 2026-11-03
- **Related:** 0013, 0022

## Context

Notifications showed the Terminal icon, because terminal-notifier.app ships
with it. The owner asked for a folder icon instead. terminal-notifier 3.x
removed `-appIcon` and `-sender`: macOS takes the icon from the bundle that
sends the notification and offers no per-notification override.

## Decision

- During `./install.sh` (`setup.py --agents`), copy the installed
  `terminal-notifier.app` to `~/.local/state/mac-organizer/mac-organizer.app`,
  set the system folder icon (`GenericFolderIcon.icns`), the id
  `com.mac-organizer.notifier` and the name `mac-organizer`, ad-hoc sign it,
  and register it with Launch Services.
- `notify.py` sends through that copy when it exists, else through
  terminal-notifier, else `osascript` as before.
- The copy is rebuilt only when terminal-notifier's version changes, so macOS
  does not ask for permission again on every install. A different app at that
  path is never replaced. `uninstall.sh` removes the copy.
- The copy lives in the state folder, not `~/Applications`: it is not
  something to open.

## Consequences

- Notifications show a folder and are listed as **mac-organizer** in System
  Settings → Notifications, which asks for permission once (the README's
  "Say Allow" step).
- A failed copy or signature only costs the icon; notifications still arrive.
- Script tests must not register their throwaway copies, so `lsregister` can
  be replaced with `MAC_ORGANIZER_LSREGISTER` (like `MAC_ORGANIZER_OSACOMPILE`).

## Alternatives considered

- `-appIcon` / `-sender`: removed from terminal-notifier; never reliable on
  recent macOS.
- `-contentImage`: adds a picture inside the notification, but the Terminal
  icon stays.
- Our own notifier app in Swift: full control, but needs a compiler and
  maintenance; the copy reuses a tool users already install.
