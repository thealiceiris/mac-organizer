# 0024. Leave fresh downloads alone for 5 minutes, and never move an open file

- **Status:** Proposed (agreed in principle 2026-10-28; not built)
- **Date:** 2026-10-28
- **Related:** 0006, 0025

## Context

The owner: "although we want an efficient sorting system, i realise
sometimes you do not know where a file has been sorted to". They had to find
a file with a global (Spotlight) search. Asked which moment bothers them,
whether a fresh download vanishing before they open it (sometimes with the
browser saying "file not found") or not seeing what moved where, they said
**"yes both"**. This record covers the first; 0025 covers the second.

Today a file in Downloads is moved once it is 60 seconds old (0006).

## Decision (proposed)

- New items in `~/Downloads` wait **5 minutes** before they are sorted.
  The owner suggested 5 minutes; it covers "download, then open straight
  away", while anything opened later is found through 0025. The value will
  be configurable.
- **A file that is open in an app is never moved** (the builder's
  suggestion), whatever its age.
- The 60-second guard stays for the other watched folders.

## Consequences

- Fresh downloads stay where the browser put them long enough to use them.
- Because folder events don't fire again when a file ages, items actually
  leave between 5 and about 15 minutes after arriving (the next sweep),
  unless a follow-up run is scheduled.
- Checking for open files costs a little time per sweep.

## Open questions

- Should the delay also apply to the Desktop?
- Should the 5–15 minute window be tightened with a follow-up run?
