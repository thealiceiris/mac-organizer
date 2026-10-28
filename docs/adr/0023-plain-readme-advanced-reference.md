# 0023. A plain-language README; technical detail in ADVANCED.md

- **Status:** Accepted
- **Date:** 2026-10-27
- **Related:** `README.md`, `ADVANCED.md`, `tests/test_readme.py`

## Context

The friends testing mac-organizer are not all technical. The owner's
feedback: **"the readme is a bit too technical"**. The reviewer also found
traps in the old walkthrough, such as editing TOML in TextEdit (which turns
quotes curly and breaks the file) and folder names that didn't match what
the tool creates.

## Decision

- `README.md` is a plain walkthrough with no jargon: what you need,
  install in five steps (including downloading a model and
  `python3 setup.py --model …` instead of editing config), how to tell it's
  working, an "every day" table, where things are, folder suggestions,
  cleaning up a messy Mac, updating, turning it off, privacy.
- `ADVANCED.md` holds the technical reference: rules, config layers, the
  backlog report format, the CLI, state files, changing the model.
- Tests check the README's sections, keep it under 260 lines and free of
  listed jargon, and check that ADVANCED.md still covers the technical
  topics.

## Consequences

- A friend can install and use it by following the README alone.
- Two documents to keep in step; the tests catch the most likely drift.

## Alternatives considered

- **One README with a "for developers" section:** newcomers still meet the
  jargon first.
- **A website or wiki:** more to host and maintain for a handful of testers.
