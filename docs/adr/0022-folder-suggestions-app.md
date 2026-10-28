# 0022. A Folder Suggestions app instead of notification clicks

- **Status:** Accepted
- **Date:** 2026-10-27
- **Related:** 0013, 0019, `setup.py`, `dialog.py`

## Context

The first design opened the review dialog when the user clicked the
suggestion notification (`terminal-notifier -execute`). In the live test on
macOS 27 the click produced nothing that could be confirmed, and the owner
couldn't recall what they clicked ("it happened but i do not remember what i
clikced"). terminal-notifier is also unmaintained. A feature that depends on
a click that may silently do nothing is not dependable.

## Decision

Reviewing never depends on a notification click:

- `./install.sh` (`setup.py --agents`) builds
  `~/Applications/Folder Suggestions.app` with `osacompile`. The app runs
  `route.py --review-pending` with absolute paths (no PATH or shell profile).
- `--review-pending` opens the waiting suggestion, or a due one (ignoring
  the daily limit, since the user asked), or says
  "No folder suggestions right now." Errors, a missing setup, or a busy
  sorter are shown as a plain OK dialog, never a raw AppleScript error.
- The notification text becomes
  `<n> files in Misc look like <Name>. Open Folder Suggestions to review.`
  Clicking still works where macOS allows it.
- The installer replaces the app only if it is its own (it checks for the
  compiled script) and never deletes a foreign app; `uninstall.sh` removes
  it.
- The command-line equivalents (`--suggestions`, `--accept-suggestion`,
  `--decline-suggestion`) remain for terminal users.

## Consequences

- One dependable way in: Spotlight → "Folder Suggestions".
- One more thing on the user's Mac, created by the installer and removed
  by the uninstaller.

## Alternatives considered

- **Keep relying on clicks:** unreliable on current macOS.
- **Pop the dialog up unprompted:** an explicit anti-goal (nagging).
- **A full native app:** far more code for one dialog.
