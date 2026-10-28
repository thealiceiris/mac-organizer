# 0010. The sorter chooses only among existing folders

- **Status:** Accepted; amended by 0017 (fallback) and 0019 (suggestions, configurable expansion)
- **Date:** 2026-10-25
- **Related:** 0008, `classify.py`

## Context

The owner's words: "we do not want to be overwhelmed with folders". Their
`~/Documents` already had the right structure (a folder per area of life,
and one per course under an academics folder). The problem was getting files
into it, not inventing new places.

## Decision

The smart sort picks **exactly one existing folder** or answers unsure. It
never creates a destination folder. The candidate list is rebuilt from disk
on every run:

- every top-level folder in `~/Documents` except `Inbox`, `Keys`, `Misc` and
  hidden ones, and
- the first-level subfolders of the folders listed in
  `[smart_sort] expand` (originally hard-coded to one folder, made
  configurable in v2.2).

Each candidate comes with up to 5 filenames already in it as a hint
(names only, from a directory listing). Before the model, instant type
rules and optional name rules (keyword → folder) run first.

## Consequences

- No folder sprawl: the user's structure is the menu.
- A folder the user creates or renames is picked up on the next run.
- A brand-new user with an empty Documents gets nothing but the fallback,
  which is the gap 0019 fills with suggestions and starter folders.
- Deeper folders are not candidates, keeping the choice small enough for a
  small model.

## Alternatives considered

- **Let the model propose new folders and create them:** rejected as
  sprawl, and later reframed as suggest-and-ask (0019).
- **A fixed taxonomy shipped with the tool:** fits nobody's existing
  Documents.
