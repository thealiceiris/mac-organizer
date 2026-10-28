# 0020. The live sorter never touches ~/Documents

- **Status:** Accepted
- **Date:** 2026-10-26
- **Related:** 0016, 0019

## Context

While designing suggestions, loose files sitting directly in `~/Documents`
came up: should the background sorter tidy them too? Apps save into
Documents and people work on files there; a file moving out from under an
open editor, or an app's save folder emptying itself, would be alarming. The
owner asked why, weighed it, and decided: **"docs should be untouched for
now"**.

## Decision

`~/Documents` is a destination only. The live sorter never moves anything
that is already inside it, including loose top-level files and the contents
of Misc. Files already in Documents are handled only through the backlog
preview, line by line, with the user's approval (the Misc preview included).
The only automatic writes into Documents are new arrivals from the watched
folders, and folders the user accepted.

## Consequences

- Anything the user filed by hand stays exactly where they put it.
- Loose Documents files stay loose until the user runs a preview.
- "For now" leaves room for a later, opt-in decision.

## Alternatives considered

- **Watch Documents like Downloads:** tidier, but risks moving working files.
- **Watch only loose top-level files:** the same risk for app save folders.
