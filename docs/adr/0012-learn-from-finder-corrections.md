# 0012. Learn from Finder corrections; cache unsure verdicts

- **Status:** Accepted
- **Date:** 2026-10-25
- **Related:** 0009, `memory.py`

## Context

A small model will sometimes be wrong. The owner wanted fixing mistakes to
be as easy as "drag the item in Finder", with the sorter learning from it,
and no settings screen to manage. Separately, an item the model is unsure
about would otherwise be re-judged on every 10-minute sweep, reloading the
model each time.

## Decision

- **Placements:** every smart-sort move is appended to `placements.jsonl`
  (path, size, inode, folder, sample summary, time).
- **Corrections:** each sweep checks placements from the last 30 days. If an
  item has moved, it is found by inode, then by name through Spotlight
  (`mdfind`, with a 24-hour grace period for Spotlight to catch up). If it
  now sits in a different candidate folder, the correction is recorded.
  Moves out of Documents or into the Trash are not corrections.
- **Few-shot examples:** up to ~10 recent corrections, most similar by
  filename first, are added to the prompt.
- **Verdict cache:** an unsure verdict is cached in `verdicts.jsonl`, keyed by
  path, size, mtime and a hash of the candidate list. The item is judged
  again only if it changes, the folders change, or a new correction arrives.

## Consequences

- Correcting is the normal Finder gesture; nothing new to learn.
- Learning is prompt-only: no model fine-tuning, nothing leaves the Mac.
- Quiet sweeps really are quiet: no model loads, no notifications.
- State files are compacted safely even while another process appends.

## Alternatives considered

- **A "wrong folder" button or command:** an extra step for the user.
- **Fine-tuning the model:** heavy, slow, and needs far more examples.
