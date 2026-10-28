# 0004. Declarative TOML rules, first match wins

- **Status:** Amended by 0015 (local overlay) and 0017 (Misc fallback)
- **Date:** 2026-09-07
- **Related:** `rules.default.toml`, 0010

## Context

Where each kind of file goes is a matter of taste and changes over time. It
should be editable without touching code, and the order of checks must be
predictable.

## Decision

Routing lives in TOML as an ordered list of `[[rule]]` tables. Each rule
names a `source` folder, optional predicates (`ext`, `glob`, `name_regex`,
`min_age_seconds`) that must all hold, an `action` (`move`,
`move_and_timestamp`, `skip`, later `trash` and the smart actions), and a
`dest` that may use `~` and `%Y`/`%m`/`%d` from the file's modification time.
For each item, rules are evaluated top to bottom and the **first match
wins**. No match means the item is left alone (until 0017).

The shipped table is `rules.default.toml`. `setup.py` generates the
effective `rules.toml` from it, patching in the screenshot location read
from `defaults read com.apple.screencapture`. Bad rules fail when the file is
loaded, not halfway through a sweep.

v1 sent every document to a single `~/Documents/Inbox`. That pile (about 130
files after seven weeks) is what motivated v2.

## Consequences

- Behaviour is readable in one file, and testable by writing a small rules
  file in a temporary folder.
- First-match-wins makes `skip` rules a simple way to shadow later ones.
- `route.main()` warns about rules that chain back onto an earlier source;
  ordinary chains such as Desktop → Inbox → Documents are allowed.
- A regenerated `rules.toml` loses hand edits, which led to the separate
  personal file in 0015.

## Alternatives considered

- **Rules in Python code:** more flexible, but every change is a code change.
- **YAML or JSON:** YAML needs a third-party parser (0003); JSON has no
  comments.
