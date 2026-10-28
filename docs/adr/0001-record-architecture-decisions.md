# 0001. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-10-28 (records 0002–0018 are written retroactively from the specs, plans and git history)
- **Related:** `docs/specs/`, `docs/plans/`

## Context

mac-organizer grew in three releases in under two months: a rule router
(v1), on-device smart sorting (v2), and suggest-and-ask folders (v2.2). The
reasoning behind each choice lived in long design specs and chat sessions.
The repo is now public and friends are testing it, so newcomers need a short
way to learn why things are the way they are, and the owner needs a place
to look before undoing an earlier choice by accident.

## Decision

Keep architecture decision records in `docs/adr/`, numbered in order, one
decision per file, in the short Context / Decision / Consequences /
Alternatives format. Accepted records are not edited; a new record amends or
supersedes them. Decisions made but not yet built are recorded as
**Proposed**.

The records quote the owner's own words where a decision came from them,
and mark the builder's own calls as such, the same convention the specs use.

## Consequences

- One place answers "why does it do that?" without reading 900 lines of spec.
- Specs stay the detailed source of behaviour; ADRs link to them rather than
  repeat them.
- Records must stay free of personal paths and model names, like every other
  published file (see 0015).

## Alternatives considered

- **Specs only.** They mix decisions with detail, and amendments are buried
  at the end of each one.
- **Git history only.** Commit messages say what changed, rarely what was
  rejected.
