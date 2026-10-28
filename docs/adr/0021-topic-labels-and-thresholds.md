# 0021. Topic labels, normalisation and suggestion limits

- **Status:** Accepted
- **Date:** 2026-10-26
- **Related:** 0019, `topics.py`, `suggest.py`

## Context

Suggestions need a way to tell that several Misc files belong together,
without reading Documents automatically (0020), without new network use
(0008), and without nagging (an explicit anti-goal).

## Decision

- **Topic:** whenever the model's answer is unsure, it also returns a 1–3
  word topic ("Taxes", "Car"). Topics are recorded in `misc-topics.jsonl`
  only for items that reach Misc through a model verdict, from v2.2 onward.
- **Normalisation** (the builder's call): accents folded (NFKD), lower case,
  digits and punctuation removed, simple plurals stripped, whitespace
  collapsed. "Tax", "taxes" and "Taxes 2024" are the same topic.
- **When a topic qualifies:** ≥ 3 items currently in Misc (`min_items`,
  configurable); not a near-duplicate of an existing top-level or `expand`
  folder; not on the "never" list; not snoozed.
- **Limits:** at most one suggestion notification per 24 hours, counted from
  when it is sent, showing the largest topic first. "Not now" snoozes a
  topic for 7 days; an unanswered suggestion expires after 7 days and counts
  as "Not now"; "Never" is permanent per topic.
- **What moves:** the file list is recomputed at review time from what is
  still in Misc; only listed files move, and only within `$HOME`.

## Consequences

- No extra model calls: topics come free with the unsure verdict.
- The existing Misc pile has no topics until the user runs the Misc preview.
- At most one question a day, so suggestions stay calm.

## Alternatives considered

- **Cluster by filename similarity:** cheap, but misses "W-2.pdf" and
  "2023_return.pdf" being the same topic.
- **Embeddings:** better clustering, but another model and more machinery
  than a threshold of 3 needs.
