# 0016. Backlog clean-up: dry run, veto, apply

- **Status:** Accepted
- **Date:** 2026-10-25
- **Related:** `backlog.py`, 0019, 0020

## Context

Switching on v2 meant dealing with the existing mess: about 130 files in the
old Documents Inbox and about 55 items in Downloads. Moving hundreds of
existing files unattended is a different risk from sorting a new download.
The owner wanted a dry run first.

## Decision

- `route.py --backlog --dry-run PATHS` plans with exactly the same decision
  code as live sorting (`plan_item`) and writes a tab-separated report:
  action, source, destination, reason (and later folder and topic). Nothing
  moves.
- The user vetoes a line by deleting it or commenting it out with `#`.
  Vetoed items are added to a **keep-list**, which live sweeps and later
  plans also honour.
- `route.py --backlog --apply REPORT` performs only the remaining lines, and
  re-validates each one: an item that changed since planning, or a line that
  no longer makes sense, is skipped.
- Pre-v1 leftover folders in Downloads are unpacked by contents rather than
  moved whole. A loose project in Downloads (a `package.json` and its
  siblings) can be proposed as one `GROUP` line into `Development/<name>`;
  this is the only folder the backlog creates, and only if that line stays.
- Folder suggestions were later added as opt-in `#NEWFOLDER` lines (0019).

## Consequences

- A first run on a long-used Mac moves nothing without line-by-line
  approval.
- A report is plain text, editable in any editor, and auditable afterwards.
- The flow is technical (a file of tab-separated lines); the plain README
  walks non-technical users through it step by step.

## Alternatives considered

- **Let live sweeps clean up the backlog:** hundreds of unreviewed moves.
- **An interactive per-file prompt:** tiring for hundreds of files and
  impossible to review as a whole.
