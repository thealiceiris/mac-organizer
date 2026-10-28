# 0025. A "Recently Sorted" folder of shortcuts in Downloads

- **Status:** Proposed (agreed in principle 2026-10-28; not built)
- **Date:** 2026-10-28
- **Related:** 0013, 0024

## Context

See 0024: the owner wants to see what moved where without searching. Today
the only full record is `actions.log`, which is technical; notifications
name folders but not files, and rule-only moves are silent.

## Decision (proposed)

- Keep a **Recently Sorted** folder that holds a shortcut to every item
  sorted in the past 7 days, named after the file. The owner agreed:
  "a recently sorted makes more sense".
- It lives **inside `~/Downloads`** (the builder's suggestion), because that
  is where people look for a download, so the answer is where they already
  look. The sorter treats it as its own folder and never sorts it.
- Double-clicking a shortcut opens the file where it now lives;
  right-click → Show Original reveals its folder.
- Shortcuts older than 7 days are removed automatically. Only shortcuts are
  ever removed; the real files are never touched.
- Summary notifications point to it, e.g.
  "Sorted 3 files. See Recently Sorted in Downloads."

## Consequences

- One place answers "where did my file go?" for every route, including
  silent rule moves and Misc.
- The README's "every day" walkthrough gains a step: open Downloads →
  Recently Sorted.
- One more folder in Downloads, and the sorter must skip it.

## Open questions (deferred by the owner: "lets tackle them later")

- Whether a new user would get lost anywhere in the overall flow.
- Whether to add an optional, batched review of "files I wasn't sure
  about" (Misc) inside Folder Suggestions, instead of per-file prompts.
- Whether 3 similar files is the right point to suggest a folder.
