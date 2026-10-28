# 0011. Byte-identical duplicates go to the Trash; nothing is deleted

- **Status:** Accepted
- **Date:** 2026-10-25
- **Related:** 0005, 0006, `dedupe.py`

## Context

Re-downloading the same file produced ` -2`, ` -3` copies; there were 24 in
the old Documents Inbox alone. The owner chose to skip identical duplicates.
At the same time, a tool acting unattended must never destroy data.

## Decision

Before every move, the destination is checked for an identical copy:

- **Files:** same size, then the same SHA-256.
- **Folders:** the same relative paths, sizes and file hashes.

On a match, the incoming item is moved to `~/.Trash` (with ` -2` on a name
clash), and the log records `DUPLICATE src == existing`. Different content
with the same name still gets ` -2`. Only the destination folder is hashed,
never unrelated siblings. Nothing in mac-organizer deletes permanently.

## Consequences

- Re-downloads stop piling up, and every removal is restorable from the
  Trash.
- Hashing costs a read of same-size candidates only.
- Expired `.ics` invites (24 hours old) also go to the Trash under the same
  rule.

## Alternatives considered

- **Delete duplicates:** saves a step, but irreversible.
- **Keep ` -2` copies:** the original problem.
- **Match by name:** different versions of a document often share a name.
