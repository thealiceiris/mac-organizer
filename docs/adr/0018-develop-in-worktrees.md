# 0018. The repo is the live install; build in worktrees

- **Status:** Accepted
- **Date:** 2026-10-25
- **Related:** 0002, `docs/plans/`

## Context

launchd runs `route.py` straight from `~/.mac-organizer`, which is also the
git repo. Editing code there edits the running tool; a half-finished change
would start moving real files within ten minutes.

## Decision

- All changes are built in a git worktree under `.worktrees/<branch>`
  (git-ignored), with the full test suite (pytest, model and dialog faked)
  green before merging.
- **Switch-over** is a deliberate, owner-approved step: unload the agents
  (`launchctl bootout`), fast-forward `main`, back up `rules.toml`,
  regenerate it (keeping `rules.local.toml`), run the tests in the live
  repo, then `./install.sh` to reload.
- When a change affects existing files, a backlog dry run is reviewed and
  applied **before** agents are reloaded, so nothing moves before approval.
- Features follow a design spec (`docs/specs/`) and an implementation plan
  (`docs/plans/`), with a review after each task and a whole-branch review
  before merge.

## Consequences

- The live sorter keeps working while new versions are built.
- Every release is a fast-forward of reviewed commits.
- A little ceremony per change, worth it for a tool that moves real files.

## Alternatives considered

- **Install to a separate folder (copy on release):** two copies to keep in
  sync, and friends' installs would differ from the repo layout.
- **Edit in place with agents unloaded:** easy to forget to reload, or to
  reload half-done work.
