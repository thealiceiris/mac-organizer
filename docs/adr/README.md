# Architecture decision records

This folder records **why** mac-organizer is built the way it is: one short
file per decision, from the first version to today. The design specs in
`docs/specs/` say *what* each release does; these records say *why*, and
what was given up.

Each record has a **status**:

- **Accepted**: in force.
- **Amended by NNNN**: still in force, but a later record changes part of it.
- **Superseded by NNNN**: replaced; kept for history.
- **Proposed**: discussed and agreed in principle, not built yet.

Records are never rewritten after acceptance. A changed mind gets a new
record that supersedes or amends the old one.

## Index

### v1: the extension router (2026-09-07)

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-launchd-agents-not-a-daemon.md) | Run as launchd agents, not a resident app | Accepted |
| [0003](0003-python-stdlib-only.md) | Python standard library only | Accepted |
| [0004](0004-declarative-toml-rules.md) | Declarative TOML rules, first match wins | Amended by 0015 |
| [0005](0005-atomic-no-clobber-moves-and-lock.md) | Atomic no-clobber moves and a single-instance lock | Accepted |
| [0006](0006-hard-safety-guards.md) | Hard-coded safety guards | Amended by 0024 (proposed) |
| [0007](0007-obsidian-vault-handoff.md) | An Obsidian vault fed through one handoff folder | Accepted |

### v2: on-device smart sort (2026-10-25)

| # | Decision | Status |
|---|---|---|
| [0008](0008-on-device-ai-only.md) | Content-aware sorting with an on-device model only | Accepted |
| [0009](0009-llama-cpp-server-per-batch.md) | llama.cpp server started per batch, constrained output | Accepted |
| [0010](0010-choose-only-existing-folders.md) | The sorter chooses only among existing folders | Amended by 0017, 0019 |
| [0011](0011-duplicates-to-trash.md) | Byte-identical duplicates go to the Trash; nothing is deleted | Accepted |
| [0012](0012-learn-from-finder-corrections.md) | Learn from Finder corrections; cache unsure verdicts | Accepted |
| [0013](0013-calm-batch-notifications.md) | Calm batch notifications | Amended by 0022 |
| [0014](0014-eject-installer-disks.md) | Eject installer disk images, safely | Accepted |
| [0015](0015-layered-config-and-public-repo.md) | Layered config; a public repo with nothing personal in it | Accepted |
| [0016](0016-backlog-dry-run-veto-apply.md) | Backlog clean-up: dry run, veto, apply | Accepted |
| [0017](0017-misc-fallback.md) | Unplaced items go to Documents/Misc | Accepted |
| [0018](0018-develop-in-worktrees.md) | The repo is the live install; build in worktrees | Accepted |

### v2.2: suggest-and-ask folders (2026-10-26 to 2026-10-27)

| # | Decision | Status |
|---|---|---|
| [0019](0019-suggest-and-ask-folders.md) | Suggest new folders and ask; never create them unasked | Accepted |
| [0020](0020-live-sorter-never-touches-documents.md) | The live sorter never touches ~/Documents | Accepted |
| [0021](0021-topic-labels-and-thresholds.md) | Topic labels, normalisation and suggestion limits | Accepted |
| [0022](0022-folder-suggestions-app.md) | A Folder Suggestions app instead of notification clicks | Accepted |
| [0023](0023-plain-readme-advanced-reference.md) | A plain-language README; technical detail in ADVANCED.md | Accepted |

### Next: finding where a file went (proposed, 2026-10-28)

| # | Decision | Status |
|---|---|---|
| [0024](0024-grace-period-for-fresh-downloads.md) | Leave fresh downloads alone for 5 minutes, and never move an open file | Proposed |
| [0025](0025-recently-sorted-folder.md) | A "Recently Sorted" folder of shortcuts in Downloads | Proposed |

## Template

```markdown
# NNNN. Title

- **Status:** Proposed | Accepted | Amended by NNNN | Superseded by NNNN
- **Date:** YYYY-MM-DD
- **Related:** NNNN, docs/specs/…

## Context
What forced a decision, in the owner's words where possible.

## Decision
What was decided.

## Consequences
What gets easier, what gets harder, what it costs.

## Alternatives considered
What else was on the table and why it lost.
```
