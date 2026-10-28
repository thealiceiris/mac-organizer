# 0019. Suggest new folders and ask; never create them unasked

- **Status:** Accepted
- **Date:** 2026-10-26
- **Related:** `docs/specs/2026-10-26-suggest-folders-design.md`, 0010, 0021, 0022

## Context

0010 works for the owner, whose Documents was already organised. It does not
work for the friends now testing the public repo: a new user with an empty
Documents sees everything land in Misc, and a long-time user's folders don't
match their files. The owner first wondered whether the organiser should
just create folders ("to make things easy for the user"), but also wanted to
avoid "an influx of folders", and settled it with a principle:

> **"suggest and ask is always better than assuming the user's cognitive
> capabilities"**

## Decision

mac-organizer may **propose** folders but never creates one without an
explicit yes. A folder can be created only by:

1. **Create & move** in the Folder Suggestions review (the user may rename
   first);
2. `route.py --accept-suggestion KEY [--name NAME]`;
3. an un-commented `#NEWFOLDER` line on backlog apply (0016);
4. a "yes" to the starter-folder question at install;
5. an approved backlog `GROUP` line.

Three moments:

- **Day to day:** when ≥ 3 files in Misc share a topic, one calm
  notification (at most daily) points to the review (0021, 0022).
- **Long-time users:** the backlog preview lists suggested folders, off by
  default; removing the `#` accepts one. A one-off
  `route.py --backlog ~/Documents/Misc` produces suggestions for an
  existing Misc pile.
- **New users:** if Documents is empty, install asks once whether to create
  starter folders (Work, School, Finance, Health, Personal, Travel; default
  No; names configurable). A busy Mac (more than 50 items in Downloads or
  20 loose files in Documents) is asked whether to preview first before the
  background sorter starts.

The review dialog offers **Create & move**, **Not now** and **Never suggest
this**, and warns when a similar folder already exists ("Use 'Tax'
instead?").

## Consequences

- No folder ever appears that the user didn't confirm; structure grows from
  real clusters of the user's own files.
- Folders are top-level in Documents only; nesting is out of scope.
- New subfolder candidates come from `[smart_sort] expand`, so other
  people's `School/…` folders work, not just the owner's layout.

## Alternatives considered

- **Create folders automatically:** easiest, but decides for the user and
  risks sprawl.
- **Prompt for a folder on every file:** makes the user the sorter again.
