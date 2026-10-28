# 0017. Unplaced items go to Documents/Misc

- **Status:** Accepted
- **Date:** 2026-10-25
- **Related:** 0010, 0019, 0021

## Context

After the first backlog run, unsure documents went back to the old
`~/Documents/Inbox`, and files with no matching rule (`.epub`, `.class`, …)
stayed in Downloads. The owner said: **"if the ai isn't sure it should be in
a misc folder"**, and chose that everything unplaced should go there.

## Decision

`~/Documents/Misc` replaces `~/Documents/Inbox` as the single fallback:

- every smart rule (documents, code, archives, Downloads folders) falls back
  to Misc when the model is unsure, disabled or unavailable;
- files in Downloads and `~/Inbox` that no rule places also go to Misc
  (an `.ics` younger than a day waits for its 24-hour Trash rule);
- Misc is never a candidate, so the model cannot choose it;
- notifications say "left in Misc".

Vetoed items (the keep-list) still stay where they are. Setup stopped
creating the unused `~/Documents/Inbox` in v2.2.

## Consequences

- Downloads ends up empty apart from Installers; nothing is left in limbo.
- "Unsure" never means "wrong folder"; a confident wrong guess stays the
  worse outcome.
- Misc can fill up, especially for new users, which is what folder
  suggestions (0019) feed on.

## Alternatives considered

- **Leave unsure items in Downloads:** the original clutter.
- **Ask the user about every unsure file:** rejected on 2026-10-28 as making
  the user the sorter again; an optional, batched "files I wasn't sure
  about" review was offered and is still open.
