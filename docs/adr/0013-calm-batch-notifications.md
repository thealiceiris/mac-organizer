# 0013. Calm batch notifications

- **Status:** Accepted; amended by 0022 (clicks no longer relied on), and by 0025 (proposed) for the summary wording
- **Date:** 2026-10-25
- **Related:** `notify.py`, `batch.py`

## Context

Moving files silently can be confusing, but one notification per file (or
per screenshot) would be spam, an explicit anti-goal. The owner asked for a
"files are being sorted" heads-up and a summary.

## Decision

- **Start:** "Files are being sorted…", once per batch, only when the batch
  has at least one item that goes through the model or to Misc.
- **End:** one summary, e.g. "7 files sorted: 5 → Development, 1 → Career,
  1 left in Misc", also noting duplicates sent to the Trash. A short batch
  report (each item, where it went, the model's reason) is written to the
  state folder and kept 30 days.
- **Silent:** batches made only of type-rule moves (screenshots, images,
  installers) and disk ejects.
- **Delivery:** `terminal-notifier` when installed, so the summary can open
  the report; plain `osascript` notifications otherwise.

## Consequences

- At most two notifications per sorting batch, and none for routine moves.
- The summary says *which folders*, but not where a single file went; silent
  rule moves leave no visible trace at all. This gap is what 0025 proposes
  to close.

## Alternatives considered

- **No notifications:** files disappear with no explanation.
- **One per file:** spam.
- **AppleScript notifications only:** cannot run anything on click.
