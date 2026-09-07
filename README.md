# mac-organizer

A fully automatic file-tidying system for macOS, plus a ready-to-use Obsidian
vault. Files that land in your drop zones are moved to permanent homes by a
small Python script triggered by `launchd`. No GUI, no third-party
dependencies.

## Requirements

- macOS
- Python 3.11 or newer on your `PATH` (`brew install python`)

## Install

```bash
git clone <this repo> ~/.mac-organizer
cd ~/.mac-organizer
./install.sh
```

`install.sh`:

1. Runs `setup.py`, which creates `~/Inbox`, `~/Media/Images`,
   `~/Media/Screenshots`, `~/Documents/Inbox`, `~/Downloads/Installers`, and
   the state directory; detects your screenshot location; generates
   `rules.toml`; installs the `launchd` agents (one per watched folder plus a
   10-minute sweep — five, or four when your screenshots already land in a
   watched folder); and copies the Obsidian vault template to `~/Notes`
   (never overwriting existing files).
2. Loads the agents with `launchctl`.
3. Prints a dry-run preview of what the router would do right now.

Then open Obsidian and "Open folder as vault" → `~/Notes`.

## What gets moved where

Routing is defined in `rules.toml` (generated from `rules.default.toml`).
Defaults:

| From | What | To |
| --- | --- | --- |
| Screenshots folder | `Screenshot *`, `Screen Recording *` | `~/Media/Screenshots/YYYY-MM/`, renamed `YYYY-MM-DD_HH-MM-SS` |
| `~/Desktop` | everything (after 60s) | `~/Inbox` |
| `~/Downloads` | images | `~/Media/Images` |
| `~/Downloads` | pdf / office docs | `~/Documents/Inbox` |
| `~/Downloads` | `.md` / `.txt` | `~/Notes/00-Inbox` |
| `~/Downloads` | dmg / pkg / archives | `~/Downloads/Installers` |
| `~/Inbox` | same rules as Downloads | permanent homes |

Anything not matched is left alone. Files are only ever moved, never deleted
or overwritten (name collisions get a ` -2` suffix). Files newer than 60
seconds and partial downloads (`.crdownload`, `.part`, `.download`, `.tmp`)
are skipped.

## Changing the rules

Edit `~/.mac-organizer/rules.toml`. Each `[[rule]]` has a `source` folder, an
`action` (`move`, `move_and_timestamp`, or `skip`), a `dest`, and optional
`ext`, `glob`, and `min_age_seconds` filters. Rules are evaluated top to
bottom per source folder; the first match wins.

Changing the filters or the `dest` of a rule needs no restart — the next run
picks up the changes. **Adding a rule with a new `source` folder does need
`./install.sh` re-run**: the agents watch a fixed set of folders
(`~/Downloads`, `~/Desktop`, `~/Inbox`, and your screenshot folder), so a
folder nothing watches is never visited. `dest` folders may be anywhere under
your home directory; a destination outside `$HOME` is refused and logged.

To preview without moving anything:

```bash
python3 ~/.mac-organizer/route.py --dry-run
```

## Logs

Every move is logged to `~/.local/state/mac-organizer/actions.log`, one line
per action, naming the path the file actually landed on. Agent stdout/stderr
go to `~/.local/state/mac-organizer/com.mac-organizer.*.log`.

Several agents can fire at once, so each run takes an exclusive lock on
`~/.local/state/mac-organizer/route.lock`; a run that finds the lock held
exits immediately (the next trigger or the 10-minute sweep picks the work
up). Destination names are claimed atomically, so even concurrent runs never
overwrite a file — collisions get a ` -2` suffix.

## After changing your screenshot location or upgrading Python

Re-run `./install.sh`. It regenerates the agents (the Python path is baked
into them) and, if `rules.toml` does not yet exist, regenerates it with the
new screenshot path. To force `rules.toml` regeneration, delete it first
(your edits will be lost).

## Uninstall

```bash
cd ~/.mac-organizer
./uninstall.sh
```

Removes the `launchd` agents only. Your files, `rules.toml`, `~/Notes`, and
logs are left in place.

## Optional Obsidian plugins

The vault ships with core plugins configured (Templates, Daily Notes, Tags).
For faster capture, consider the community plugins **Templater** and
**QuickAdd** — install them from within Obsidian.

## Development

```bash
python3 -m pytest -q
```
