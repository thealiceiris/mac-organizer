# 0007. An Obsidian vault fed through one handoff folder

- **Status:** Accepted
- **Date:** 2026-09-07
- **Related:** `vault-template/`

## Context

Stray notes (`.md`, `.txt`) were scattered across Downloads and the Desktop.
The owner also wanted a fresh, structured place to write notes. The two
needs are related but should not depend on each other.

## Decision

Ship two loosely coupled parts in one repo:

- the **file router**, and
- an **Obsidian vault template** copied to `~/Notes` on first install
  (never overwriting existing files), laid out as PARA: `00-Inbox`,
  `10-Projects`, `20-Areas`, `30-Resources`, `40-Archive`, plus
  `_templates` (daily, project, meeting) and `_attachments`, with only core
  Obsidian plugins enabled.

The only coupling is one folder: the router drops text and Markdown files
into `~/Notes/00-Inbox`.

## Consequences

- Either part can be used or removed without the other.
- Notes are plain local files; nothing is synced (an explicit non-goal).
- Users who don't use Obsidian still get their notes in one folder.

## Alternatives considered

- **A separate repo for the vault:** cleaner on paper, but two installs for
  one person's setup.
- **Community plugins (Templater, QuickAdd):** documented as optional, not
  installed, to keep the vault working with a stock Obsidian.
