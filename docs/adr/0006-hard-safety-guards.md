# 0006. Hard-coded safety guards

- **Status:** Accepted; amended by 0024 (proposed) for fresh downloads
- **Date:** 2026-09-07 (extended 2026-10-25)
- **Related:** 0005, 0011

## Context

A tool that moves files on its own must never grab a file that is still
being written, never leave the user's home folder, and always leave a trail.
These must not be things a config typo can switch off.

## Decision

The following are enforced in code and are not configurable:

- **Age guard:** files modified less than **60 seconds** ago are skipped
  (`MIN_AGE_SECONDS`). In v2, folders in Downloads are skipped until nothing
  inside has changed for **5 minutes** (the builder's call).
- **Partial downloads:** `.crdownload`, `.part`, `.download`, `.tmp` and
  dotfiles are never touched.
- **Stay in `$HOME`:** a destination that resolves outside the home folder
  is refused.
- **Never into its own destinations:** folders that are a rule's
  destination (e.g. `~/Downloads/Installers`) are not treated as items.
- **Audit log:** every action is one line in
  `~/.local/state/mac-organizer/actions.log`
  (`timestamp ACTION src -> dest`). `--dry-run` prints what would happen and
  moves nothing.
- **Nothing is deleted:** "discard" always means "move to the Trash" (0011).

## Consequences

- A download in progress is safe; so is a folder being unzipped.
- Every move can be traced and undone by hand from the log.
- The 60-second guard is long enough for safety but short enough that a
  fresh download can vanish before the user opens it. The owner hit exactly
  this, which is what 0024 addresses.

## Alternatives considered

- **Configurable guards:** flexible, but one bad edit could make the tool
  dangerous; safety stays in code, preferences stay in rules.
