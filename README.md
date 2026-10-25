# mac-organizer

A fully automatic file-tidying system for macOS, plus a ready-to-use Obsidian
vault. Files that land in your drop zones are moved to permanent homes by a
small Python script triggered by `launchd`. No GUI, no Python dependencies
(standard library only). Optionally, a local AI model reads documents and files
them into the right folder in `~/Documents`, entirely on your Mac.

## Requirements

- macOS
- Python 3.11 or newer on your `PATH` (`brew install python`)
- For smart sort and notifications (optional, `install.sh` tells you which are missing):
  - `llama-server` (`brew install llama.cpp`) and a GGUF chat model
  - `pdftotext` (`brew install poppler`) to read PDFs
  - `terminal-notifier` (`brew install terminal-notifier`) to make the
    notification summary clickable (without it, plain macOS notifications are used)

Without these tools the rule-based routing still works; documents that need
the model simply wait in `~/Documents/Inbox`.

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
   `rules.toml` and a personal `rules.local.toml`; installs the `launchd`
   agents (one per watched folder plus a 10-minute sweep — five, or four when
   your screenshots already land in a watched folder); and copies the Obsidian vault template to `~/Notes`
   (never overwriting existing files).
2. Loads the agents with `launchctl`.
3. Warns, with one `brew install ...` line, about any missing external tools.
   (launchd's `PATH` lacks Homebrew, so the check also looks in
   `/opt/homebrew/bin` and `/usr/local/bin`.)
4. Prints a dry-run preview of what the router would do right now.

Then set `model_path` in `~/.mac-organizer/rules.local.toml` (see "Changing the
model"); until you do, smart sort is off and documents wait in the Inbox.

Then open Obsidian and "Open folder as vault" → `~/Notes`.

## What gets moved where

Routing is defined in `rules.toml` (generated from `rules.default.toml`).
Defaults:

| From | What | To |
| --- | --- | --- |
| Screenshots folder | `Screenshot *`, `Screen Recording *` | `~/Media/Screenshots/YYYY-MM/`, renamed `YYYY-MM-DD_HH-MM-SS` |
| `~/Desktop` | `.app` bundles | `~/Downloads/Installers` |
| `~/Desktop` | everything else (after 60s) | `~/Inbox` |
| `~/Downloads`, `~/Inbox` | images (incl. `svg`) | `~/Media/Images` |
| `~/Downloads`, `~/Inbox` | audio | `~/Media/Audio` |
| `~/Downloads`, `~/Inbox` | video | `~/Media/Video` |
| `~/Downloads`, `~/Inbox` | `.md` / `.txt` | `~/Notes/00-Inbox` |
| `~/Downloads`, `~/Inbox` | dmg / pkg | `~/Downloads/Installers` |
| `~/Downloads`, `~/Inbox` | keys and certificates (`pem p12 pfx cer crt asc gpg pgp`) | `~/Documents/Keys` (permissions 700) |
| `~/Downloads`, `~/Inbox` | `.ics` calendar invites | Trash, after 24 hours |
| `~/Downloads`, `~/Inbox` | zip / tar / gz archives containing an installer | `~/Downloads/Installers` |
| `~/Downloads`, `~/Inbox` | other archives, documents (pdf, office, csv, ...) | smart sort (below), else `~/Documents/Inbox` |
| `~/Downloads`, `~/Inbox` | code and notebooks (`py ipynb html js json sh ...`) | smart sort, else `~/Documents/Development` |
| `~/Downloads` only | folders | `.app` bundles to Installers, the rest smart sort |

Archives are opened (read-only, never extracted to disk) to see whether they
hold an installer or code. Anything not matched is left alone. Files are never
overwritten (name collisions get a ` -2` suffix); the only things ever deleted
are byte-identical duplicates and old `.ics` files, and both go to the Trash,
not oblivion. Files newer than 60 seconds and partial downloads (`.crdownload`,
`.part`, `.download`, `.tmp`) are skipped. Folders are handled in `~/Downloads`
only.

## Smart sort

For documents, archives, code and folders, a local model reads a sample of the
content and picks the best existing folder. The candidates are the top-level
folders of `~/Documents` (except `Inbox` and `Keys`) plus the folders one level
inside `~/Documents/Academics` if you have one. **It never creates folders**:
if the model is unsure (confidence below the `threshold`, default 0.6), or no
folder fits, the item goes to `~/Documents/Inbox` (or the rule's own `dest`).

Privacy: everything is on-device. `llama-server` is started for each batch,
listens on `127.0.0.1` only, is called with proxies bypassed, and is stopped
when the batch ends (`--reasoning off`, `--offline`). Nothing is sent over the
network, and nothing stays resident between batches.

Settings live in `rules.local.toml`, which is created on install, git-ignored
and never overwritten. Smart sort stays off until you set the model path
(an unset or missing model counts as "model missing": rule fallbacks apply and
you get at most one notice a day):

```toml
[smart_sort]
model_path = "~/models/your-model.gguf"
```

Rules in `rules.local.toml` are evaluated before the shipped ones, so you can
add personal rules there (see the commented example in the file).

### Changing the model

Any GGUF chat model that `llama-server` can load works. Download one, set
`model_path` under `[smart_sort]` in `rules.local.toml` (also required after a
fresh install), and you are done; the
next batch uses it. To check that a model behaves, run the opt-in live test,
which starts the real server and classifies a few sample documents:

```bash
MAC_ORGANIZER_LIVE=1 python3 -m pytest tests/test_classify_live.py -q -s
```

Smaller models are faster; larger ones are more accurate on ambiguous files.

## Duplicates

A file that is byte-identical to one already at the destination is moved to the
Trash instead of being stored again (and logged).

## Notifications

You get a notification when a smart-sort batch starts and a summary when it
ends. With `terminal-notifier` installed, clicking the summary opens a report
of what went where; without it, mac-organizer falls back to macOS's built-in
`osascript display notification` (not clickable). Batches handled purely by rules are silent. If the model
cannot be started you get one notice saying files will wait in the Inbox.

## Learning from your corrections

If you move a sorted file to a different folder in Finder, mac-organizer
notices (within a 30-day window) and remembers. Similar past corrections and
placements are shown to the model as examples on later runs. "Unsure" verdicts
are cached too, so a file the model could not place is not re-classified on
every sweep; that cache is dropped when the set of candidate folders changes or
you make a new correction. This memory lives in
`~/.local/state/mac-organizer`.

## Disk images

Mounted installer disk images (`.dmg`) are ejected automatically once they have
been mounted for 15 minutes. It is never forced: an image that is busy stays
mounted.

## Cleaning up a backlog

To sort an existing pile in one go, plan first, review, then apply:

```bash
python3 ~/.mac-organizer/route.py --backlog --dry-run ~/Documents/Inbox ~/Downloads
```

This moves nothing and prints the path of a TSV report in
`~/.local/state/mac-organizer/reports/`, one line per proposed action. Open it
and delete any line you do not want (your veto), then:

```bash
python3 ~/.mac-organizer/route.py --backlog --apply ~/.local/state/mac-organizer/reports/backlog-XXXX.tsv
```

Each remaining line is validated again before it runs; malformed lines abort
the apply, and files that changed since the plan was made are skipped.

## Upgrading from v1

Re-running `./install.sh` after upgrading backs up your existing `rules.toml` to
`rules.toml.v1.bak` and generates the v2 rules. Re-apply your own edits through
`rules.local.toml`.

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
