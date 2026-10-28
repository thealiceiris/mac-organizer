# 0005. Atomic no-clobber moves and a single-instance lock

- **Status:** Accepted
- **Date:** 2026-09-07 (folder moves added 2026-10-25)
- **Related:** 0002, `fsops.py`, `route.py`

## Context

Several agents can run `route.py` at once: a folder agent and the sweep, and
the router's own moves re-trigger watchers on both the source and the
destination. Concurrent runs are the normal case, not an edge case. The
first review of v1 found that "check the name is free, then move" could lose
a file when two runs raced.

## Decision

Safety is enforced at two independent levels:

1. **Atomic move primitive.** A move claims the destination name atomically
   and fails if the name is taken: `os.link` then unlink for files, an
   `O_EXCL` create before copying across volumes, and `renamex_np` with
   `RENAME_EXCL` for folders. On a clash it tries `name -2`, `name -3`, …
   and returns the name actually used, which is what the log records.
   Existence checks are used only for `--dry-run` previews.
2. **Single-instance lock.** Each run takes an exclusive `fcntl.flock` on
   `~/.local/state/mac-organizer/route.lock`. A second sorting run exits
   immediately (it would be redundant; launchd fires again). Interactive
   commands that must not be dropped, such as a folder-suggestion answer,
   wait for the lock instead (60 s, and up to 15 minutes while a dialog
   answer is pending) and say "mac-organizer is busy, try again shortly"
   if it never frees.

A failure on one item is logged as `ERROR` and the run continues.

## Consequences

- No file is ever overwritten or lost, even if the lock is bypassed.
- Folders are never merged into an existing folder of the same name; they
  get ` -2` like files.
- Because the lock serialises everything, the model (0009) and the
  suggestion dialog (0019) never run twice at once.

## Alternatives considered

- **Check-then-`shutil.move`:** racy, which is the bug that was fixed.
- **Lock only:** one missed lock (a manual run, a bug) would make data loss
  possible again; the atomic move makes the lock an optimisation, not the
  safety net.
