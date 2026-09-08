# mac-organizer — Design

**Date:** 2026-09-07
**Status:** Approved, pre-implementation

## Purpose

A fully automatic, free system for keeping a Mac tidy. Two loosely-coupled
subsystems in one git repo:

1. **File router** — watches drop-zone folders and moves/renames files to
   permanent homes without user action.
2. **Obsidian vault** — a fresh, structured note-taking setup that the router
   feeds stray Markdown/text files into.

The only coupling between them is a handoff directory: the router drops `.md`
and `.txt` files into the vault's inbox folder.

## Non-goals

- Window / workspace management.
- Interactive or confirm-first sorting (the system is fully automatic).
- A GUI. Configuration is a text file.
- Syncing the vault to iCloud or other services (local files only).
- Deduplication, content analysis, or OCR.

## Repository layout

```
~/.mac-organizer/
├── route.py                 # the router engine
├── rules.toml               # routing rules (user-editable)
├── install.sh               # create folders, render + load launchd agents
├── uninstall.sh             # unload agents, remove them (leaves files/logs)
├── launchd/
│   └── com.mac-organizer.<name>.plist.tmpl   # templates, rendered by install.sh
├── vault-template/          # files copied into ~/Notes on first install
│   ├── _templates/
│   │   ├── daily.md
│   │   ├── project.md
│   │   └── meeting.md
│   └── .obsidian/           # pre-configured core plugin settings
├── tests/
│   └── test_route.py
├── docs/specs/
└── README.md
```

## Subsystem A — file router

### `route.py`

- **Language:** Python 3, standard library only (`tomllib` is stdlib as of
  3.11; target interpreter is Homebrew `python3`, currently 3.14).
- **Invocation:** `route.py [--dry-run] [--once] PATH [PATH ...]`
  Each `PATH` is a directory to process. launchd agents pass a single path;
  a user can pass several for a manual full sweep.
- **Algorithm per directory:**
  1. List immediate children that are files (no recursion, skip dotfiles and
     directories).
  2. For each file, evaluate rules in file order; first match wins.
  3. Apply the matched rule's action. No match → leave the file untouched.
- **Rule matching predicates** (a rule may combine them; all must hold):
  - `ext` — list of lowercased extensions, e.g. `["png", "jpg"]`
  - `glob` — filename glob, e.g. `"Screenshot *"`
  - `min_age_seconds` — file's mtime is at least this old
- **Actions:**
  - `move` — move to `dest` directory (path may contain `~` and the
    strftime tokens `%Y`, `%m`, `%d`; expanded at runtime against the file's
    mtime).
  - `move_and_timestamp` — move to `dest`, renaming the file to
    `%Y-%m-%d_%H-%M-%S<ext>` (from mtime); on name collision append `-2`,
    `-3`, …
  - `skip` — explicitly leave untouched (used to shadow later rules).

### Safety rules (always enforced, not configurable)

- Skip any file whose mtime is under **60 seconds** ago (in-flight writes).
- Skip extensions `.crdownload`, `.part`, `.download`, `.tmp`.
- Never overwrite: if `dest/name` exists, insert ` -2` (etc.) before the
  extension until free.
- All moves are within the user's home; `route.py` refuses a `dest` that
  resolves outside `$HOME`.
- Every action (and every skip-with-reason at `--dry-run`) is appended as one
  line to `~/.local/state/mac-organizer/actions.log`:
  `ISO8601  ACTION  src -> dest`.
- `--dry-run` logs and prints what would happen, moves nothing.

### `rules.toml` (initial content)

Watched folders and their default routing:

| Source | Match | Action | Destination |
|---|---|---|---|
| `~/Pictures/Screenshots` | `glob = "Screenshot *"`, ext png/jpg/jpeg | `move_and_timestamp` | `~/Media/Screenshots/%Y-%m` |
| `~/Pictures/Screenshots` | `glob = "Screen Recording *"`, ext mov/mp4 | `move_and_timestamp` | `~/Media/Screenshots/%Y-%m` |
| `~/Desktop` | any file, `min_age_seconds = 60` | `move` | `~/Inbox` |
| `~/Downloads` | ext md/txt/markdown | `move` | `~/Notes/00-Inbox` |
| `~/Downloads` | ext png/jpg/jpeg/gif/webp/heic | `move` | `~/Media/Images` |
| `~/Downloads` | ext pdf/doc/docx/pages/xls/xlsx/csv/ppt/pptx/key | `move` | `~/Documents/Inbox` |
| `~/Downloads` | ext dmg/pkg/zip/tar/gz/tgz | `move` | `~/Downloads/Installers` |
| `~/Inbox` | ext md/txt/markdown | `move` | `~/Notes/00-Inbox` |
| `~/Inbox` | ext png/jpg/jpeg/gif/webp/heic | `move` | `~/Media/Images` |
| `~/Inbox` | ext pdf/doc/docx/pages/xls/xlsx/csv/ppt/pptx/key | `move` | `~/Documents/Inbox` |
| `~/Inbox` | ext dmg/pkg/zip/tar/gz/tgz | `move` | `~/Downloads/Installers` |

Anything unmatched in `~/Downloads` and `~/Inbox` stays put. The screenshot
source path is read by `install.sh` from
`defaults read com.apple.screencapture location` and written into `rules.toml`
at install time (currently `~/Pictures/Screenshots`).

### launchd agents

One agent per watched directory, all running the same command
(`python3 route.py <dir>`), plus one interval agent:

| Label | Trigger | Purpose |
|---|---|---|
| `com.mac-organizer.downloads` | `WatchPaths` = `~/Downloads` | react to new downloads |
| `com.mac-organizer.desktop` | `WatchPaths` = `~/Desktop` | react to new desktop files |
| `com.mac-organizer.screenshots` | `WatchPaths` = screenshot dir | react to new screenshots |
| `com.mac-organizer.inbox` | `WatchPaths` = `~/Inbox` | react to inbox drops |
| `com.mac-organizer.sweep` | `StartInterval` = 600 | safety net; runs all four dirs, catches age-based rules and any missed event |

Agents live in `~/Library/LaunchAgents/`. `RunAtLoad` is true for the sweep
agent only. `StandardOutPath` / `StandardErrorPath` point into
`~/.local/state/mac-organizer/`.

`WatchPaths` fires on any change to the directory, including the router's own
moves; `route.py` is idempotent (a settled directory produces no actions) so
this converges without loops.

The screenshot agent is only installed when the screenshot folder is not
already one of the watched sources. On a stock Mac `com.apple.screencapture
location` is unset and resolves to `~/Desktop`, so the default install has
four agents, not five, and no folder is watched twice.

### Concurrency model

Single-run idempotence is **not** sufficient: several agents can invoke
`route.py` at the same time (a `WatchPaths` agent and the sweep, and the
router's own moves re-trigger watchers for both the source and the
destination folder). Concurrent processes are the normal case, so safety is
enforced at two independent levels:

1. **Atomic move primitive.** `route.move_no_clobber(src, dst)` claims the
   destination name with `os.link` — atomic, and `FileExistsError` if the
   name is taken — then unlinks the source. On a cross-device move it claims
   the name with an `O_EXCL` create before copying. It walks `dst`, `dst -2`,
   `dst -3`, … until a claim succeeds and returns the path actually used,
   which is what the action log records. Two routers can therefore never
   settle on the same destination name: no file is overwritten or lost even
   if the lock below is bypassed. Existence checks before a move (the old
   `dedupe`-then-`shutil.move`) are TOCTOU-racy and are used only for the
   `--dry-run` preview.
2. **Single-instance lock.** `route.main()` takes a non-blocking exclusive
   `fcntl.flock` on `~/.local/state/mac-organizer/route.lock` for the whole
   run. A second concurrent run prints one line and exits 0 — it would be
   redundant work, and launchd fires again on the next event or sweep.

A failure on one file (vanished between `iterdir()` and the move, locked,
unwritable destination) is logged as an `ERROR` line and the pass continues:
the sweep is the safety net and must not be aborted by one bad file.

### `install.sh`

1. Verify `python3` is on PATH and is 3.11+ (`tomllib`); abort with a
   message if not — `/usr/bin/python3` on a stock Mac is 3.9, and baking it
   into the agents fails silently on every fire.
2. Create: `~/Inbox`, `~/Media/Images`, `~/Media/Screenshots`,
   `~/Documents/Inbox`, `~/Downloads/Installers`,
   `~/.local/state/mac-organizer/`.
3. Read the screenshot location and write `rules.toml` (from a checked-in
   `rules.toml` default, patching the screenshot path).
4. Render each `launchd/*.plist.tmpl` (substituting repo path, python path,
   screenshot dir) into `~/Library/LaunchAgents/` and `launchctl bootstrap`
   (or `load`) each.
5. Run one `route.py --dry-run` sweep and print the result so the user sees
   what the first real run will do.

### `uninstall.sh`

`launchctl bootout` each agent, delete the plists. Leaves created folders,
moved files, `rules.toml`, and logs in place.

## Subsystem B — Obsidian vault

`install.sh` creates `~/Notes` if absent and copies `vault-template/` into it.
Never overwrites existing files.

### Folder structure (PARA)

```
~/Notes/
├── 00-Inbox/        # capture + router drop zone
├── 10-Projects/     # active, time-bound efforts
├── 20-Areas/        # ongoing responsibilities
├── 30-Resources/    # reference material by topic
├── 40-Archive/      # inactive projects/areas
├── _templates/
└── _attachments/
```

### `.obsidian` config (shipped)

- Core plugins enabled: **Templates** (folder `_templates`), **Daily notes**
  (folder `20-Areas/Journal`, template `_templates/daily.md`), **Tags**.
- New-file attachments directory set to `_attachments`.
- Default new-file location: `00-Inbox`.

### Templates

- `daily.md` — date heading, sections: Focus, Notes, Log.
- `project.md` — status, goal, next actions, links.
- `meeting.md` — date, attendees, agenda, decisions, action items.

### Optional (documented in README, not installed)

Templater and QuickAdd for faster capture — left to the user.

## Testing

- **`tests/test_route.py`** (pytest): each case builds a tmp directory tree,
  writes a `rules.toml`, runs the router in-process, asserts final file
  locations and the action log. Cases cover: each action type, first-match
  wins, the 60-second age guard, partial-download extension skip, collision
  renaming, `dest` outside `$HOME` refused, `--dry-run` moves nothing,
  strftime token expansion, idempotency (second run is a no-op).
- **Manual:** `route.py --dry-run ~/Downloads` before enabling agents;
  `launchctl kickstart -k gui/$UID/com.mac-organizer.sweep` to force a run.
- No automated test of launchd itself.

## Rollout

1. `git clone` / already in `~/.mac-organizer`.
2. `./install.sh`.
3. Review the printed dry-run, adjust `rules.toml`, re-run `./install.sh` if
   the screenshot path or folders changed.
4. Point Obsidian at `~/Notes`.

## Open risks

- `WatchPaths` coalesces rapid events; the 10-minute sweep covers anything
  dropped during a burst.
- If the user changes the macOS screenshot location later, `rules.toml` and
  the screenshots agent need updating — `install.sh` re-run handles it;
  documented in README.
- Homebrew Python upgrades change the interpreter path baked into plists;
  README notes re-running `install.sh` after a major Python upgrade.
- The watched folder set is fixed at install time (`setup.WATCHED_SOURCES`
  plus the screenshot folder), so adding a rule with a *new* `source` folder
  requires re-running `install.sh`. Deriving the watch set from `rules.toml`
  is a separate design change; the README documents the current behaviour.
- Rules whose destinations chain back onto an earlier source form a routing
  cycle. `route.main()` warns on stderr (naming the hops) but still runs;
  plain chains such as `~/Desktop` → `~/Inbox` → `~/Documents/Inbox` are
  normal and are not reported.
