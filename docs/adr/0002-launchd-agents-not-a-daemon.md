# 0002. Run as launchd agents, not a resident app

- **Status:** Accepted
- **Date:** 2026-09-07
- **Related:** `docs/specs/2026-09-07-mac-organizer-design.md`, 0005

## Context

The goal was "a fully automatic, free system for keeping a Mac tidy": files
landing in Downloads, Desktop, the screenshot folder and `~/Inbox` should be
moved without the user doing anything, and the tool should cost nothing when
idle.

## Decision

Use macOS's own scheduler. `setup.py` renders one launchd agent per watched
folder (`com.mac-organizer.<name>`, triggered by `WatchPaths`) plus one
`sweep` agent that runs every 600 seconds and at login. Every agent runs the
same short-lived command, `python3 route.py <folder>`, which exits when the
folder is settled. The screenshot agent is installed only when the
screenshot folder is not already watched (on a stock Mac it is the Desktop).

## Consequences

- Nothing runs between events: no memory or battery cost when idle.
- launchd restarts nothing and needs no supervision; a crash is just one
  missed run that the sweep repeats.
- `WatchPaths` fires on the router's own moves too, so `route.py` must be
  idempotent: a settled folder produces no actions.
- `WatchPaths` coalesces bursts and does not fire again when a file merely
  ages, so age-based rules rely on the 10-minute sweep. A file can wait up
  to one sweep longer than its minimum age.
- The Python path is baked into the plists; a major Homebrew Python upgrade
  needs `./install.sh` again.
- The set of watched folders is fixed at install time.

## Alternatives considered

- **A menu-bar or background app** (Hazel-style): more polish, but a
  resident process, a build toolchain and code signing for a personal tool.
- **cron:** no folder triggers, and deprecated on macOS.
- **Folder Actions (AppleScript):** fragile, per-folder setup, hard to test.
