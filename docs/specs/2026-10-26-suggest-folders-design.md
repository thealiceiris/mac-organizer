# mac-organizer: suggest-and-ask folders — Design

**Date:** 2026-10-26
**Status:** Draft, awaiting approval
**Builds on:** `2026-10-25-mac-organizer-v2-design.md` (v2, live; plus the Misc amendment)

## Why

v2 never creates folders. The smart sort only picks among folders that already
exist in `~/Documents`. That suits the author, who already has a structured
`Documents`. It doesn't suit the friends now testing the public repo:

- A **new user** with an empty `~/Documents` sees everything land in
  `~/Documents/Misc`.
- A **long-time user** has years of loose files and folders that don't match
  the themes in their files, so `Misc` fills up.
- The one level of subfolder expansion is hard-coded to a folder named
  `Academics` (the author's), so other people's `School/…` or `Work/…`
  subfolders are never candidates.

The user's principle, in their words: **"suggest and ask is always better than
assuming the user's cognitive capabilities."** mac-organizer may propose
folders, but it never creates one without an explicit yes. It also must not
cause "an influx of folders".

## Goals

1. **Suggest new folders, never create them unasked.** Proposals come from
   real clusters of unplaced files. They are created only after the user
   confirms, and the user can rename first.
2. **Day-to-day suggestions** for files collecting in `~/Documents/Misc`: at
   most one calm notification a day, then a click-to-review dialog.
3. **First-run suggestions** for long-time users, inside the backlog preview
   report and off by default.
4. **A starter set for brand-new users,** offered once at install as a yes/no
   question.
5. **Loose files directly in `~/Documents`** are included in the first-run
   preview (the user's decision), and only there.
6. **Configurable subfolder expansion,** replacing the hard-coded
   `Academics`. The author's `Academics` keeps working.
7. **Setup stops creating the unused `~/Documents/Inbox`.**
8. **The README gets a "Setting up your folders" section.**

## Non-goals

- **No automatic moves inside `~/Documents`.** The live sorter never touches
  `~/Documents`, including loose top-level files (the user's decision: apps
  save there and people work on files there). Loose `Documents` files are
  only ever handled through the backlog preview, line by line.
- Nested folder suggestions. Suggestions are top-level `~/Documents` folders
  only.
- Renaming, merging or deleting existing folders.
- Any new network use. Theme detection uses the same local model.

## Anti-goals

- **A folder appears that the user didn't confirm.**
- **Nagging:** more than one suggestion notification a day, re-suggesting a
  theme the user declined with "Never", or a modal dialog popping up
  unprompted. The dialog opens only when the user clicks the notification.
- **Suggesting near-duplicates** of folders the user already has, e.g.
  "Taxes" when "Tax" or "Taxes 2024" exists.
- **Making the user's existing structure change.** Existing folders are used
  as they are.

## Decisions made with the user

| Topic | Decision |
|---|---|
| Create folders automatically vs suggest | **Suggest and ask** |
| Long-time users | First-run backlog preview with suggested folders off by default; accept a suggestion by removing the `#` |
| Loose files directly in `~/Documents` | Included in the first-run preview; never touched by the live sorter |
| Notification style | One calm notification, at most daily; clicking opens a review dialog |

## How it works

### 1. Theme labels (shared by everything below)

When the model returns **unsure** for an item, it also returns a short
**topic** label: 1–3 words in Title Case, e.g. "Taxes" or "Car". This extends
the existing JSON schema with an optional `topic` string. Topics are stored
with the item in a new state file, `misc-topics.jsonl`, with fields
`{path, size, mtime, topic, at}`, written when the item lands in `Misc`.

Topics are compared in a **normalised** form: lower case, simple plural
stripped, punctuation removed (*my call*). "Tax", "taxes" and "Taxes 2024"
all normalise to `tax`.

### 2. Day-to-day suggestions (live sweeps)

- **Trigger:** at the end of a sweep, count the items **currently in
  `~/Documents/Misc`** by normalised topic. A topic qualifies when it has
  **≥ 3 items** (*my call*, configurable) and passes these checks:
  - it doesn't match the normalised name of any existing top-level
    `~/Documents` folder
  - it isn't on the user's "never" list
  - it hasn't been suggested in the last 7 days after "Not now" (*my call*)
- **Frequency:** at most **one suggestion notification per 24 h**. If
  several topics qualify, only the one with the most items is shown, and the
  rest wait.
- **Notification copy:** `4 files in Misc look like Taxes. Click to review.`
  With terminal-notifier, a click runs a command (`-execute`). Without it,
  there's no clickable notification, so the suggestion is listed instead by
  `route.py --suggestions` (see below).
- **Review dialog** (opened only by the click):
  - It's an AppleScript `display dialog`, run by
    `route.py --review-suggestion <id>`.
  - **Text:** `Create a "Taxes" folder in Documents?`, then the list of files
    (up to 10, then "and N more").
  - **Name field:** a text field prefilled with the topic.
  - **Buttons:** `Not now`, `Never suggest this`, `Create & move` (the
    default).
  - **Create & move:**
    - checks the edited name (non-empty, no `/`, no leading `.`, not an
      existing folder)
    - creates `~/Documents/<name>` and moves the listed items there
    - uses the existing no-clobber moves; items that have since left `Misc`
      are skipped
    - logs each move (verb `SUGGEST`)
  - **Not now:** snooze this topic for 7 days.
  - **Never suggest this:** add the normalised topic to `never.json`.
- **CLI fallback:** `route.py --suggestions` prints the pending suggestions,
  and `route.py --accept-suggestion <id> [--name NAME]` /
  `--decline-suggestion <id> [--never]` act on them. This works without
  terminal-notifier and for anyone who prefers the terminal.

### 3. First-run suggestions (backlog preview)

- The plan also covers **loose files directly in `~/Documents`**: files only,
  no folders, and never the excluded folders (`Misc`, `Keys`). They're
  routed with the `~/Downloads` file rules.
- After planning, items whose verdict was unsure are clustered by topic as
  above. Topics with ≥ 3 items become **suggestion lines at the top of the
  report, commented out:**

  ```
  # Suggested new folders. To accept one, remove the # at the start of its line.
  # You may edit the name. Accepted folders are created on apply and their files move in.
  #NEWFOLDER	tax	Taxes	9	2023_return.pdf, W-2.pdf, …
  ```

  Each suggested item's own line is a normal `MOVE … -> ~/Documents/Misc/…`
  line with a `topic` column, so the user sees where it goes if they don't
  accept.
- **On apply:** for each un-commented `NEWFOLDER` line, the folder is
  created under the given (possibly edited) name, validated as above. Every
  planned `MOVE` to `Misc` with that topic is redirected into the new folder.
  `NEWFOLDER` lines are validated as strictly as other lines: top-level
  `~/Documents` only, and the topic must exist in the plan.
- Vetoes, the keep-list and duplicates behave as they do today.
- **Existing Misc contents** (the user's decision): `route.py --backlog ~/Documents/Misc`
  is a one-off preview run by hand. It reads the files already in Misc to get
  their topics. The report contains only `NEWFOLDER` suggestions; Misc items
  without an accepted suggestion get no line and stay where they are.
  Nothing in `~/Documents` is ever read automatically.

### 4. Starter folders (install)

- When `install.sh` runs `setup.py` interactively (stdin is a terminal) and
  `~/Documents` has **no visible folders besides the ones mac-organizer
  creates** (`Misc`, `Keys`), it asks once: `Your Documents folder is empty.
  Create starter folders Work, School, Finance, Health, Personal, Travel?
  [y/N]`
- The default is **No**. The question is skipped when not interactive.
  The answer is remembered (state file) so it's never asked twice.
- The starter names live in `rules.default.toml` (`[setup] starter_folders`),
  so users can change them.

### 5. First-run hint for busy Macs

When `install.sh` runs interactively and `~/Downloads` has more than 50
visible items, or `~/Documents` has more than 20 loose files, it prints:

```
You have 340 items in Downloads and 85 loose files in Documents.
Recommended: preview first, nothing moves until you approve:
  python3 setup.py && python3 route.py --backlog ~/Downloads ~/Documents
```

It then asks whether to continue loading the background sorter now
(`[y/N]`). Answering no leaves the agents unloaded until `./install.sh` is
run again. *(My call: this keeps "nothing moves before approval" true for
long-time users.)*

### 6. Configurable subfolder expansion

- New setting: `[smart_sort] expand = ["Academics"]` in
  `rules.default.toml`. Every listed top-level folder contributes its
  first-level subfolders as candidates.
- The shipped default keeps `["Academics"]` so existing installs behave
  identically.
- The README tells users to set it in `rules.local.toml`, e.g.
  `expand = ["School", "Work"]`.
- `classify.COURSES_DIR` is removed.

### 7. Housekeeping

- Remove `~/Documents/Inbox` from `setup.DIRS_TO_CREATE`. Existing folders
  are left alone, and the backlog still accepts `~/Documents/Inbox` as a path.
- README: add a "Setting up your folders" section (new vs long-time user,
  suggestions, `expand`). Update Quick start step 4 to include `~/Documents`.

## Constraints

- All v2 global constraints still hold: stdlib only, localhost-only model,
  no overwrite, no merge, Trash instead of delete, never leave `$HOME`, the
  model is never left running.
- **A folder may be created only by:** (a) Create & move in the dialog,
  (b) `--accept-suggestion`, (c) an un-commented `NEWFOLDER` line on backlog
  apply, (d) a "y" to the starter question, (e) an approved backlog `GROUP`
  (existing behaviour). Nothing else.
- Suggestion state lives in `~/.local/state/mac-organizer/`
  (`misc-topics.jsonl`, `suggestions.json`, `never.json`).
- Tests use fakes for the model, the notifier and the dialog runner. No real
  dialogs or notifications in tests.

## Settled after the builder review (*my calls*)

1. **`NEWFOLDER` line format:**
   `NEWFOLDER<TAB><topic key><TAB><folder name><TAB><count><TAB><examples>`.
   - The topic key is the normalised topic. It must not be edited, and it links
     the line to its items.
   - Only the folder name is editable.
   - Item lines get an optional 6th column, `topic` (the topic key). Reports
     without it (v2 reports) still parse.
2. **Topics are forward-looking** in live sweeps. They're recorded only for
   items that reach Misc through a model verdict from now on. Items placed
   in Misc by a type rule, an outage or an error have no topic. The existing
   pile is handled by the Misc backlog preview (above).
3. **Verdict cache:** cached unsure entries also store the topic. Older entries
   without one simply don't count toward clusters.
4. **When a topic is recorded:** whenever the outcome is "unsure", either
   because the model said "unsure" or because its confidence was below the
   threshold, and the model supplied a topic. The prompt asks for a topic
   every time. The ≥ 3 threshold filters out noise.
5. **Identity and near-duplicates:**
   - A Misc item is identified by its path. Items the user renamed or moved
     drop out when `misc-topics.jsonl` is pruned.
   - Normalisation: lower case, digits and punctuation removed, a trailing
     "s" stripped, whitespace collapsed. "Taxes 2024" becomes `tax`.
   - The near-duplicate check compares against every top-level `~/Documents`
     folder **and** every `expand` subfolder.
   - Create & move applies the same near-duplicate check to the name the user
     typed. On a match, the dialog asks: `A similar folder "Tax" exists. Move
     the files there instead?` [Cancel] [Use "Tax"].
6. **The click command** is built from the interpreter and repo path that
   `setup.py` bakes into the launchd plists. Clicks have no PATH or shell
   profile, so absolute paths are used.
   - `--review-suggestion` takes the router's single-instance lock, waiting up
     to 60 s; if it still can't get it, it says "busy, try again shortly".
   - It never starts the model.
7. **Daily limit and expiry:**
   - The 24 h limit counts from when the notification is **sent**.
   - A suggestion is stored in `suggestions.json` with its id (the topic key)
     and the item list as it was when sent.
   - At click time the list is recomputed from the items still in Misc, and
     those are what move.
   - A suggestion not answered within 7 days expires, which counts as
     "Not now".
   - Snoozes and "never" are per topic key.
8. **Loose `~/Documents` files in the backlog preview:**
   - They follow the `~/Downloads` file rules, so an image may be planned to
     go to `~/Media`. Every line still needs the user's approval, and the
     "never touch Documents" rule is about the live sorter only.
   - A smart MOVE can target an existing `Documents` folder.
   - **Install order:** Python guard, tool check, the busy-Mac question, then
     `setup.py` (which asks the starter-folder question; it only creates
     folders and config, it never moves files), then load the agents only if
     the user said yes, or if the Mac isn't busy.
   - For the "empty Documents" check, `Misc`, `Keys` and `Inbox` all count as
     mac-organizer's own folders.
9. **No "user-created" flag:** candidates come from the folders on disk, so a
   folder created through a suggestion is a candidate on the next run.

## Left to the builder

- Prompt wording for the topic label.
- The exact normalisation function, within the stated behaviour.
- Dialog layout details, within AppleScript limits. If the file list is too
  long for `display dialog`, truncate it.
- Suggestion id format.
- How `misc-topics.jsonl` is pruned (drop entries whose item has left `Misc`).
