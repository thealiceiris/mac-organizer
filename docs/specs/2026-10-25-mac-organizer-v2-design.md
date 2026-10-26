# mac-organizer v2 — Design

**Date:** 2026-10-25
**Status:** Approved 2026-10-25
**Builds on:** `2026-09-07-mac-organizer-design.md` (v1)

## Why

v1 works mechanically. In seven weeks it logged no errors. The filing system
it produced is still a mess, because it routes by file extension only:

- Every PDF, Word, slide deck and spreadsheet goes to `~/Documents/Inbox`,
  which now holds about 130 files. Course work, CVs, readings and
  competition CSVs all sit side by side, even though `~/Documents` already
  has the right folders (`Academics/<course>`, `Career`, `Development`, …).
- Re-downloading a file creates ` -2`, ` -3` copies. There are 24 in
  `Documents/Inbox` alone.
- Folders are never touched, and many file types have no rule. About 55
  items are left in `~/Downloads` (project folders, audio, video, code, keys,
  `.ics`, `.svg`, unzipped admissions packages).

v2 makes the router **read what a file is** and file it into the folder the
user already keeps for it, entirely on-device.

## Goals

1. **Content-aware sorting.** A document, zip or folder whose name and type
   don't place it is read, and is filed into an existing folder under
   `~/Documents`.
2. **No folder sprawl.** The sorter only chooses among folders that already
   exist. It never creates a destination folder. (The user said "we do not
   want to be overwhelmed with folders".)
3. **Private.** No file content leaves the Mac. Classification uses a local
   model through llama.cpp. (The user rejected cloud APIs as "too much data".)
4. **No duplicate pile-up.** A byte-identical copy of something already at
   the destination goes to the Trash instead of becoming ` -2`.
5. **Cover the leftovers.** Rules for audio, video, code and notebooks, keys
   and certs, `.ics`, `.svg`, and whole folders.
6. **Calm notifications.** Show one heads-up when a batch starts and one
   summary when it ends.
7. **Learns from corrections.** Dragging a misfiled item to another folder
   teaches the sorter.
8. **Backlog clean-up** of `~/Documents/Inbox` and `~/Downloads`, previewed as
   a dry run and approved by the user before anything moves.
9. **Release on GitHub as v1.0 and v2.0.**

## Non-goals

- Creating new folders, renaming files, or restructuring existing folders.
- Sorting inside `~/Documents` beyond the explicit Inbox backlog run. Files
  the user filed by hand are never touched.
- Unzipping. Zips are filed still zipped (the user's decision).
- OCR of scanned PDFs or images. A PDF with no extractable text is judged on
  its filename alone.
- A GUI or settings app.
- Deleting anything permanently. "Discard" always means "move to the Trash".

## Anti-goals (failure even if it "works")

- A confident wrong guess. Misfiling into the wrong course is worse than
  leaving the item in the Inbox. When unsure, use the Inbox.
- Any network call carrying file content.
- Notification spam, such as one notification per file or one per
  screenshot.
- The model sitting in memory all day. The Mac has 16 GB of RAM and the
  model is about 5 GB.
- Breaking v1 guarantees: no overwrite, atomic moves, the single-instance
  lock, never leaving `$HOME`, and leaving files under 60 s old alone.

## Decisions made with the user

| Topic | Decision |
|---|---|
| How documents are sorted | By course where possible. Otherwise an "intelligent" read of the content picks the folder. |
| Folders | Sorted in `~/Downloads` only. The Desktop stays files-only. |
| Installer disks | Mounted `.dmg` volumes are ejected automatically, safely (never forced, never real drives) |
| Course codes | None configured. The AI handles course sorting. |
| Allowed destinations | Any existing folder in `~/Documents`. None are excluded. |
| Where the AI runs | On-device only, using the user's own models. Their desktop LLM app has no API, so llama.cpp runs the GGUF files directly. llama.cpp was installed with Homebrew (0.7.0) on 2026-10-25. |
| Duplicates | Byte-identical copies → Trash. Different content still gets ` -2`. |
| New rules | Audio/video, code/notebooks, keys/certs, `.ics`/`.svg` |
| Zips | Stay zipped |
| Notifications | "Files are being sorted" when a batch starts, then a summary of where things went. Rule-only moves (screenshots, installers) are silent. |
| Fixing mistakes | Drag the item in Finder. The sorter learns from that. |
| Backlog | Dry run first. Nothing moves until the user approves. |
| Publishing | GitHub, tagged v1.0 (today's code) and v2.0. The user picks public or private at publish time. |

## How a new item is routed

These steps apply to each top-level item in a watched source (`~/Downloads`,
`~/Desktop`, `~/Inbox`, screenshots). The order is fixed. The first step
that places the item wins.

1. **Safety skips (from v1, plus additions).** Skip dotfiles, partial
   downloads, and files under 60 s old. Skip **folders** whose newest
   contained mtime is under 5 minutes old (*my call*), and folders that are
   the router's own destinations (`~/Downloads/Installers`, and anything a
   rule's `dest` points into).
2. **Type rules (instant, no AI).** These come from `rules.toml` and cover
   screenshots, images, audio, video, installers, keys, `.ics`, `.svg`
   (table below).
3. **Name rules (instant, no AI).** These are filename keyword → folder rules
   in `rules.toml`, e.g. `CS101` → `Academics/Intro to Computer Science`, or
   `CV|Resume|Cover Letter` → `Career`. They apply to documents, code, zips
   and folders.
4. **Smart sort (local model).** It covers documents, spreadsheets, slides,
   code files, notebooks, non-installer zips and folders that steps 2 and 3
   didn't place. It extracts a short text sample and asks the model to pick
   exactly one folder from the candidate list, or "unsure".
5. **Fallback.** Unplaced documents go to `~/Documents/Inbox`. Anything else
   unplaced stays where it is, as in v1.

Before every move, the **duplicate check** runs (see below).

### Type rules for Downloads and Inbox (new or changed rows are bold)

| Match | Destination |
|---|---|
| images (png jpg jpeg gif webp heic) | `~/Media/Images` |
| **svg** | `~/Media/Images` |
| **audio (m4a mp3 wav aac flac)** | **`~/Media/Audio`** |
| **video (mov mp4 m4v)** | **`~/Media/Video`** |
| md / txt | `~/Notes/00-Inbox` (unchanged) |
| dmg, pkg, **`.app` bundles** | `~/Downloads/Installers` |
| **zip / tar / gz / tgz** | **Inspect the listing. If it contains a `.app`/`.pkg`/`.dmg`, use Installers. Otherwise go to step 3/4.** |
| **pem, asc, gpg, p12, cer, crt** | **`~/Documents/Keys`, folder mode 700** (*my call on location*). `.key` is excluded because it is Keynote. |
| **ics** | **Trash once it is 24 h old** (*my call*: by then it has been opened in Calendar) |
| pdf, office docs, csv | step 3/4 (no longer straight to Inbox) |
| **code & notebooks (ipynb py m slx html php js ts json yaml sql sh)** | **step 3/4. If nothing matches, `~/Documents/Development`** |

Screenshot rules and the Desktop → `~/Inbox` rule are unchanged.

**Folder handling applies to `~/Downloads` only** (the user's decision).
`~/Desktop`, `~/Inbox` and the screenshots folder stay files-only, as in v1.
The exception is `.app` bundles on the Desktop, which count as installers and
go to `~/Downloads/Installers`.

### Ejecting installer disks (the user's request)

When a `.dmg` is opened, macOS mounts it and shows it on the Desktop. The
user wants these cleared away. On each sweep:

- List mounts with `hdiutil info`. Consider **only disk images**, never USB,
  external, SD or Time Machine volumes, and never system images such as
  Recovery or simulator runtimes. The image path must be a `.dmg` under
  `$HOME`.
- Eject one only after it has been mounted for at least **15 minutes**
  (*my call*), using a plain `hdiutil detach` (**never `-force`**). If macOS
  reports it busy, leave it and retry on the next sweep.
- Log each eject. Ejects are silent (no notification).
- The `.dmg` file itself is never touched.

## Smart sort

### Candidate folders

The candidate list is built fresh on each run from disk, so a folder the user
creates or renames is picked up automatically:

- every top-level folder in `~/Documents` (except `Inbox`, `Keys` and
  hidden folders), and
- every first-level folder in `~/Documents/Academics` (the courses).

Deeper folders (e.g. individual projects under `Development`) are not
candidates. This keeps the choice small and easy for the model (*my call*).

The model also gets a one-line hint per candidate: up to 5 filenames already
in it. These come cheaply from a directory listing. They are not file
contents.

### What the model reads

| Item | Sample sent to the model |
|---|---|
| PDF | Filename plus the first ~1,500 words of text, from `pdftotext` (poppler, Homebrew) (*my call*) |
| docx, pptx, xlsx | Filename plus text from the XML inside the file (stdlib `zipfile`) |
| doc, rtf, pages | Filename plus `textutil -convert txt` (built into macOS). For `.pages`, the filename only. |
| code, notebook | Filename plus the first ~150 lines (for notebooks, the markdown and code cells) |
| zip | Filename plus up to 200 member paths (no extraction) |
| folder | Folder name plus up to 200 relative paths, plus the text sample of up to 3 documents inside |

### The model call

- **Engine:** `llama-server` (Homebrew llama.cpp), bound to `127.0.0.1` only.
- **Model:** set in config. The default is
  a ~5 GB instruction-tuned GGUF chat model the user already has on disk,
  used in place rather than copied. Larger local models can be swapped in
  through config.
- **Lifecycle:** the server starts only when a batch contains at least one
  smart-sort item, handles the whole batch, and is stopped when the batch
  ends. It is never left running.
- **Output:** constrained with a JSON schema (`json_schema` on llama.cpp's
  completion endpoint): `{"folder": <one of the candidates> | "unsure",
  "confidence": 0–1, "reason": <short string>}`. Below a confidence
  threshold (default 0.6, configurable), the result counts as unsure.
- **Model missing or server fails:** the item takes the fallback route, the
  error goes to the log, and the summary notification says "AI sorting
  unavailable". This matters because the app that downloaded the model
  could move or delete it during an update.

### Learning from corrections

- Every smart-sort move is recorded in `~/.local/state/mac-organizer/placements.jsonl`
  (path, size, inode, chosen folder, sample summary, time).
- On each sweep, placements from the last 30 days are checked. If an item is
  no longer at its recorded path, it is looked up (by inode, then by name
  through Spotlight `mdfind`). If it now sits in a different candidate
  folder, the sweep records `{filename, sample summary, wrong folder, right
  folder}` in `corrections.jsonl`.
- The most recent corrections (up to ~10, most similar first by filename
  overlap) are added to the prompt as examples.
- Items that were moved out of `~/Documents` or deleted are ignored, because
  they are not corrections.

## Duplicate check

This runs before every move, for files and folders, whatever the route.

- **File:** the destination folder is scanned for files of the same size.
  Any candidates are compared by SHA-256. On a match, the incoming file goes
  to the Trash and the log records `DUPLICATE src == existing`.
- **Folder:** two folders count as identical if their relative paths, sizes
  and file hashes all match. The incoming one goes to the Trash.
- **Trash** means a move into `~/.Trash`, with a ` -2` suffix on name clash,
  so it can be restored from Finder.
- Content that differs but has the same name still gets ` -2`, as in v1.

## Notifications

These use `terminal-notifier` (Homebrew) so the summary can be clicked
(*my call*: AppleScript notifications can't run anything on click).

- **Start:** "Files are being sorted…". It is sent once per batch, and only if
  the batch has at least one smart-sort or Inbox-fallback item.
- **End:** "7 files sorted: 5 → Natural Language Processing, 1 →
  Development, 1 left in Inbox". It also notes duplicates sent to the Trash.
  Clicking it opens a short batch report (a text or HTML file in the state
  folder) listing each item, where it went and the model's reason.
- **Silent:** batches made only of type-rule moves (screenshots, images,
  installers, …).

## Backlog clean-up (one-off)

- `route.py --backlog --dry-run ~/Documents/Inbox ~/Downloads` runs the full
  v2 routing over the existing items and writes a reviewable report (item →
  destination → reason, plus duplicates to Trash). Nothing moves.
- The user reviews the report. They may veto individual lines (*builder's
  call* on the mechanism, e.g. an editable list).
- `route.py --backlog --apply <report>` performs exactly the approved lines.
- `~/Documents/Inbox` is a source **only** in backlog mode. It is never
  watched, so whatever is left there stays put afterwards.
- Leftover structure from before v1 in `~/Downloads` (`Installers & Archives`,
  `Documents`, `Images`, `Keys`) is unpacked by contents (see "Settled after
  the builder review").

## Install, config and migration

- `install.sh` checks for `llama-server`, `pdftotext` and `terminal-notifier`
  and prints the `brew install` line for any that are missing. It does not
  install them itself.
- Config is layered: `rules.toml` (generated from `rules.default.toml`, as in
  v1) holds generic rules and a `[smart_sort]` table (enabled flag,
  confidence threshold). `rules.local.toml` (git-ignored, never overwritten)
  holds the model path and any personal name rules (see below).
- Migration: the user's existing `rules.toml` was generated by v1.
  `install.sh` backs it up to `rules.toml.v1.bak` and regenerates it from the
  v2 default (*my call*). The v1 rules were never hand-edited beyond the
  generated defaults.
- Must still run on Homebrew Python 3.14 with no PyPI dependencies at runtime.
  External tools are called as subprocesses.

## Versioning and release

- Tag the current `HEAD` as `v1.0.0` before any v2 change.
- Do the v2 work on a branch, merge it, and tag `v2.0.0` once tests pass and
  the backlog run has been approved and applied.
- Publish to GitHub only after the user confirms. They also choose public or
  private at that point, and they must run `gh auth login`. Personal
  `rules.toml`, state and logs stay git-ignored.
- Update the README's non-goals, which currently list "deduplication,
  content analysis".

## Settled after the builder review (*my calls*)

- **Personal config is separate from shipped defaults.** `rules.default.toml`
  (tracked, published) holds only generic rules. Course-code name rules and
  the model path go in a git-ignored `rules.local.toml`, which `setup.py`
  generates on first install and later installs never overwrite. Effective
  rules are the local rules first, then the defaults.
- **No course-code table** (the user's decision). Course sorting is left
  entirely to the smart sort, which sees the course folder names and the
  filenames already in them. Name rules remain available in
  `rules.local.toml` for the user to add later, but none ship by default.
- **Verdict cache.** An item the model judged "unsure" that stays in place is
  recorded in `verdicts.jsonl`, keyed by path, size and mtime, together with
  a hash of the candidate list. The next sweep does not judge it again,
  so the model doesn't reload and no notification is sent. It is retried
  only when the item changes, the candidate folders change, or a new
  correction is recorded.
- **Pre-v1 leftover folders in `~/Downloads`** (`Keys`, `Images`,
  `Documents`, `Installers & Archives`) are **unpacked by contents** in the
  backlog run rather than moved whole. Each file inside is routed like a new
  download, so keys end up in `~/Documents/Keys`. The folders are removed
  only if they end up empty.
- **Loose project files.** In the backlog run, a top-level project marker in
  `~/Downloads` (`package.json`, `pyproject.toml`, `pubspec.yaml`, …) groups
  its recognisable siblings (`app/`, `components/`, `lib/`, `public/`, config
  and lock files). The group is proposed as a single item moved into
  `~/Documents/Development/<name>`, named after the `name` field in the
  marker. This is the one place a folder is created, and only if the user
  approves that line. Outside the backlog, the normal per-item rules apply.
- **Build location.** `~/.mac-organizer` is the live install launchd runs, so
  v2 is developed in a separate git worktree and switched in only when
  finished.
- **Missing tools.** `pdftotext` (poppler) and `terminal-notifier` are not
  installed yet. They are installed with Homebrew as part of the build.

## Left to the builder

- Module layout. `route.py` is 400 lines and can be split, e.g.
  `extract.py`, `classify.py`, `dedupe.py`, `notify.py`.
- Prompt wording, sample sizes, and how examples are selected.
- How llama-server ports are picked, and readiness polling.
- Batch report format and the veto mechanism.
- Test strategy. The existing 58 tests must stay green. Model calls are
  stubbed in tests, plus one opt-in test against the real model.

## Amendment (2026-10-25, after the backlog run): Misc folder

The user asked that "if the AI isn't sure it should be in a misc folder".
`~/Documents/Misc` replaces `~/Documents/Inbox` as the fallback:

- Every smart rule (documents, code and notebooks, archives, Downloads folders)
  falls back to `~/Documents/Misc` when the model is unsure, disabled or
  unavailable.
- Files in `~/Downloads` and `~/Inbox` that no rule places (e.g. `.epub`,
  `.class`) also go to `~/Documents/Misc`. An `.ics` younger than a day waits,
  so the 24 h trash rule still applies to it.
- `Misc` is never a smart-sort candidate, so the model can't choose it.
- Notifications say "left in Misc".
- Backlog: unsure `~/Documents/Inbox` items are planned as MOVE to Misc instead
  of staying.

Vetoed items (the keep-list) still stay where they are.
