# Suggest-and-ask Folders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** mac-organizer proposes new top-level `~/Documents` folders from clusters of unplaced files and creates them only when the user confirms. Confirmation can come from a daily click-to-review dialog, from opt-in `NEWFOLDER` lines in the backlog report, or from a starter-set question at install. The plan also makes subfolder expansion configurable and drops the unused `~/Documents/Inbox`.

**Architecture:** Two new stdlib modules:
- `topics.py` handles topic normalisation, the near-duplicate check and the record of topics for items in Misc.
- `suggest.py` holds suggestion state and decisions: which suggestion is due, plus accept, decline and expire.

`dialog.py` wraps the AppleScript dialog. Existing modules get small hooks:
- the classifier returns a topic
- the router records topics and offers a suggestion after `finish`
- the notifier sends a clickable suggestion notification
- the backlog gains `NEWFOLDER` lines
- setup and install ask the user

Everything external (osascript, terminal-notifier, the model) is injectable, so the tests never show real UI.

**Tech Stack:** Python 3.11+ stdlib only, pytest. External tools: `osascript`, `terminal-notifier`, `llama-server`.

**Spec:** `docs/specs/2026-10-26-suggest-folders-design.md` (builds on `docs/specs/2026-10-25-mac-organizer-v2-design.md`)

**Where to work:** worktree `~/.mac-organizer/.worktrees/suggest`, branch `suggest`. `~/.mac-organizer` is the live install, so never edit it or run `install.sh` there. Tests: `.venv/bin/python -m pytest -q`. If there's no `.venv`, use `~/.mac-organizer/.venv/bin/python`.

## Global Constraints

- All v2 constraints still hold:
  - stdlib only
  - the model is localhost-only and never left running
  - no overwrite, no merge, Trash instead of delete
  - never leave `$HOME`
  - the live sorter never touches `~/Documents`
- **A folder may be created only by:**
  1. "Create & move" in the review dialog
  2. `--accept-suggestion`
  3. an un-commented `NEWFOLDER` line on backlog apply
  4. a "y" to the starter question
  5. an approved backlog `GROUP` line (existing behaviour)

  Nothing else creates folders.
- Suggestions are for top-level `~/Documents` folders only.
- Thresholds (configurable under `[suggest]` in `rules.default.toml`):
  - `min_items = 3`
  - one notification per 24 h, counted from when it is sent
  - "Not now" snoozes for 7 days
  - an unanswered suggestion expires after 7 days, which counts as "Not now"
- **Exact copy:**
  - notification: `<n> files in Misc look like <Name>. Click to review.`
  - dialog title line: `Create a "<Name>" folder in Documents?`
  - dialog buttons: `Not now`, `Never suggest this`, `Create & move` (default)
  - near-duplicate prompt: `A similar folder "<Existing>" exists. Move the files there instead?` with buttons `Cancel`, `Use "<Existing>"`
  - starter question: `Your Documents folder is empty. Create starter folders <names>? [y/N]`
- **Normalisation:**
  1. lower case
  2. remove digits and punctuation
  3. collapse whitespace
  4. strip one trailing `s` from each word

  For example, "Taxes 2024" becomes `tax`.
- Near-duplicate check: compare the normalised name with every top-level `~/Documents` folder **and** every `expand` subfolder.
- `misc-topics.jsonl`, `suggestions.json` and `never.json` live in `~/.local/state/mac-organizer/`.
- Log verbs: `SUGGEST` (a move into an accepted folder) and `NEWFOLDER` (a folder created).
- `NEWFOLDER` report line: `NEWFOLDER<TAB><topic key><TAB><folder name><TAB><count><TAB><examples>`. Only the folder name is editable. Item lines get an optional 6th column, `topic`, and 5-column reports must still parse.

## Review Focus

1. **The user edits the folder name to something invalid** (empty, contains `/`, starts with `.`, an existing folder, a near-duplicate). Nothing is created. A near-duplicate gets the "use existing?" prompt; anything else gets a clear refusal. Tests: Task 4 (`accept`), Task 5 (dialog), Task 6 (report).
2. **Items leave Misc before the user answers** (moved, renamed or deleted). Only the items still there are moved. If none remain, nothing is created and the suggestion is dropped. Test: Task 4.
3. **A notification is clicked twice, or after its suggestion was already accepted, declined or expired.** This is a no-op with a message, and no second folder is created. Test: Task 5.
4. **A hand-edited `NEWFOLDER` line** (unknown topic key, a name with path parts, or a duplicate topic) is rejected and the rest of the report still applies. Test: Task 6.
5. **No terminal-notifier installed.** The sweep doesn't crash, the 24 h limit isn't consumed, and the suggestion is listed by `--suggestions`. Test: Task 5.

---

### Task 1: Configurable expansion, suggestion settings, no Documents/Inbox

**Files:**
- Modify: `classify.py` (`candidates`, remove `COURSES_DIR`), `route.py` (`SmartSettings`, `load_config`, the call sites of `candidates`), `setup.py` (`DIRS_TO_CREATE`), `rules.default.toml`, `memory.py` (`_owner`, if it uses `COURSES_DIR`)
- Test: `tests/test_classify.py`, `tests/test_route.py`, `tests/test_setup.py`

**Interfaces:**
- Produces:
  - `classify.candidates(documents_root: Path, expand: tuple[str, ...] = ("Academics",)) -> list[Candidate]`
  - `SmartSettings.expand: tuple[str, ...]`, from `[smart_sort] expand`, default `("Academics",)`
  - `route.SuggestSettings(min_items: int = 3, snooze_days: int = 7, expire_days: int = 7)` on `Config.suggest`, from `[suggest]`
  - `route.SetupSettings(starter_folders: tuple[str, ...])` on `Config.setup`, from `[setup] starter_folders`. The default is `("Work", "School", "Finance", "Health", "Personal", "Travel")`.

- [ ] **Step 1: Write the failing tests**
  - `test_candidates_expand_configurable`: with `expand=("School",)`, the result contains `School/Maths`, and `Academics/X` appears only as `Academics`.
  - `test_candidates_default_expand_is_academics`: matches the old behaviour.
  - `test_load_config_expand_and_suggest_defaults`: the defaults above, plus a local override `expand = ["Work"]`.
  - `test_setup_no_longer_creates_documents_inbox`: `make_dirs()` leaves `~/Documents/Inbox` absent.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** Thread `config.smart.expand` to every `classify.candidates(...)` call (route, backlog, memory). Add the `[smart_sort] expand`, `[suggest]` and `[setup]` tables to `rules.default.toml`.
- [ ] **Step 4: Run the full suite. Expect all to pass.**
- [ ] **Step 5: Commit** `feat: configurable subfolder expansion; suggestion settings; stop creating Documents/Inbox`

### Task 2: Topic labels from the model; normalisation and near-duplicates

**Files:**
- Modify: `classify.py` (`Verdict`, `_schema`, `SYSTEM_PROMPT`, `parse_verdict`)
- Create: `topics.py`
- Test: `tests/test_classify.py`, `tests/test_topics.py`

**Interfaces:**
- Produces:
  - `Verdict.topic: str | None = None`, the last field with a default so existing constructors still work.
  - The schema gains an optional property `topic` (a string, `maxLength` 40) and stays out of `required`.
  - `parse_verdict` sets `topic` (stripped, Title Case, ≤ 40 characters) **only when the result is unsure** (an explicit "unsure" or low confidence) and the model supplied a non-empty string. Malformed responses give `topic=None`.
  - `topics.normalise(text: str) -> str`
  - `topics.near_duplicate(name: str, documents_root: Path, expand: tuple[str, ...]) -> str | None` returns the existing folder's display name (e.g. `"Tax"` or `"Academics/Calculus"`) when its normalised form equals that of `name`.
  - `topics.valid_folder_name(name: str, documents_root: Path) -> str | None` returns an error message, or None if the name is OK. It rejects names that are empty, contain `/`, start with `.`, are longer than 60 characters, or already exist.

- [ ] **Step 1: Write the failing tests**
  - `test_normalise`: `normalise("Taxes 2024") == "tax"`, `normalise("  Car-Insurance! ") == "car insurance"`, `normalise("Recipes") == "recipe"`.
  - `test_near_duplicate_top_level_and_expand`: `Tax/` exists, so `near_duplicate("Taxes") == "Tax"`. With `Academics/Calculus/` and `expand=("Academics",)`, `near_duplicate("calculus") == "Academics/Calculus"`. An unrelated name gives None.
  - `test_valid_folder_name_rejections`: each rejection case.
  - `test_parse_verdict_topic_only_when_unsure`:
    - a confident folder: `topic is None`
    - explicit unsure with topic "taxes": `"Taxes"`
    - low confidence with a topic: the topic is set
    - malformed: None
  - `test_schema_topic_optional`: `"topic" not in schema["required"]`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** The prompt adds: "If you answer unsure, also give `topic`: a 1–3 word name for what the item is about, suitable as a folder name."
- [ ] **Step 4: Run the full suite. Expect all to pass.**
- [ ] **Step 5: Commit** `feat: model returns a topic for unsure items; topic normalisation and near-duplicate checks`

### Task 3: Record topics for items routed to Misc

**Files:**
- Modify: `topics.py`, `route.py` (`SmartRouter`), `memory.py` (`VerdictCache.put_unsure`)
- Test: `tests/test_topics.py`, `tests/test_smart_route.py`

**Interfaces:**
- Consumes: `Verdict.topic` (Task 2).
- Produces:
  - `topics.record(state: Path, item: Path, topic: str, now: float) -> None` appends `{path, size, mtime, topic, key: normalise(topic), at}` to `misc-topics.jsonl`.
  - `topics.clusters(state: Path, misc_dir: Path) -> dict[str, tuple[str, list[Path]]]` maps each topic key to `(display name, items)`. It covers only rows whose path still exists **directly in `misc_dir`**. The display name is the most common topic text for that key. It prunes rows for items that are gone, by rewriting the file.
  - `VerdictCache.put_unsure(..., topic: str | None = None)` stores the topic.
  - **Router behaviour:** when a smart item's fallback move lands in `~/Documents/Misc` (its final `dst.parent` equals the Misc path) **and** the verdict that led there has a topic, call `topics.record` with the item's final path. Outages, errors and type-rule moves record nothing.

- [ ] **Step 1: Write the failing tests**
  - `test_unsure_with_topic_into_misc_records_topic`: the fake classifier returns unsure + "Taxes", and `misc-topics.jsonl` gets a row with the Misc path and key `tax`.
  - `test_outage_and_type_rule_moves_record_no_topic`
  - `test_clusters_only_counts_items_still_in_misc`: 3 recorded items, one moved out of Misc, gives a cluster of 2, and the gone row is pruned.
  - `test_clusters_display_name_is_most_common_text`
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite. Expect all to pass.**
- [ ] **Step 5: Commit** `feat: remember the model's topic for items it sends to Misc`

### Task 4: Suggestion engine (state, due, accept, decline, expire)

**Files:**
- Create: `suggest.py`
- Test: `tests/test_suggest.py`

**Interfaces:**
- Consumes: `topics.clusters`, `topics.near_duplicate`, `topics.valid_folder_name`, `topics.normalise`, `SuggestSettings`, `fsops.move_item`.
- Produces:
  - `Suggestion(key: str, name: str, items: list[str], sent_at: float | None)`, a dataclass.
  - `suggest.due(state, misc_dir, documents_root, expand, settings, now) -> Suggestion | None`. This picks the largest qualifying cluster (≥ `min_items`; not in `never.json`; not snoozed; no near-duplicate) and returns None while another suggestion was sent within the last 24 h and is still pending.
  - `suggest.mark_sent(state, s, now) -> None` stores it in `suggestions.json`.
  - `suggest.pending(state, now, settings) -> list[Suggestion]` expires anything older than `expire_days`, turning it into a snooze.
  - `suggest.decline(state, key, now, settings, never: bool) -> None` either snoozes for `snooze_days` or adds the key to `never.json`, and removes it from pending.
  - `suggest.accept(state, key, name, misc_dir, documents_root, expand, log) -> AcceptResult`. `AcceptResult` has `created: Path | None`, `moved: list[Path]`, `error: str | None` and `similar: str | None`. It:
    1. recomputes the items for `key` that are still in Misc (zero → error `"those files are no longer in Misc"`)
    2. validates the name
    3. checks for a near-duplicate (on a hit it returns `similar=<existing>` and does nothing)
    4. otherwise creates `documents_root/name` with a plain `mkdir` (no parents)
    5. moves each item with `fsops.move_item` (no-clobber)
    6. logs `NEWFOLDER` and `SUGGEST`, and removes the suggestion from pending
  - `suggest.accept_into_existing(state, key, existing, misc_dir, documents_root, log) -> AcceptResult` moves the items into an existing folder (used after "Use existing").
  - `log` is a callable `(verb, src, dst)`, like `disks.eject_stale`.

- [ ] **Step 1: Write the failing tests**
  - `test_due_needs_min_items_and_picks_largest`
  - `test_due_respects_never_snooze_near_duplicate_and_daily_limit`
  - `test_pending_expires_after_7_days_into_snooze`
  - `test_accept_creates_folder_and_moves_remaining_items_only` (Review Focus 2)
  - `test_accept_with_no_remaining_items_creates_nothing` (Review Focus 2)
  - `test_accept_invalid_or_existing_name_creates_nothing` (Review Focus 1)
  - `test_accept_near_duplicate_returns_similar_and_creates_nothing` (Review Focus 1)
  - `test_accept_into_existing_moves_items`
  - `test_accept_twice_is_noop` (Review Focus 3): the second call returns error `"no such suggestion"` and no extra folder exists.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.** Keep state in JSON, write it atomically (temp file plus `os.replace`), and treat a corrupt file as empty.
- [ ] **Step 4: Run the full suite. Expect all to pass.**
- [ ] **Step 5: Commit** `feat: suggestion engine (due, accept, decline, expire)`

### Task 5: Notification, review dialog and CLI

**Files:**
- Create: `dialog.py`
- Modify: `notify.py` (`Notifier.suggest`), `route.py` (`main`: the post-run offer and the new CLI flags), `batch.py` (`Notifier` Protocol + `NullNotifier.suggest`), `tests/test_scripts.py` (`REPO_FILES`)
- Test: `tests/test_dialog.py`, `tests/test_notify.py`, `tests/test_suggest_cli.py`

**Interfaces:**
- Consumes: Task 4.
- Produces:
  - `Notifier.suggest(s: Suggestion, command: list[str]) -> bool` sends `<n> files in Misc look like <Name>. Click to review.` with `terminal-notifier -execute <shell-quoted command> -group mac-organizer-suggest`. It returns True if sent and False if there's no terminal-notifier or the send failed. There's **no osascript fallback**, because it can't be clicked.
  - `dialog.review(s: Suggestion, runner=subprocess.run) -> tuple[str, str]` returns `(button, name)`, where button is one of `"create"`, `"not_now"`, `"never"`, `"cancel"`. It shows up to 10 item names, then `and N more`.
  - `dialog.confirm_similar(existing: str, runner=...) -> bool`
  - Both build AppleScript `display dialog … default answer "<name>" buttons {…} default button "Create & move"` with every string escaped, and parse the `button returned:…, text returned:…` output. A non-zero exit (the user pressed Esc) counts as `"cancel"`.
  - `route.main` after a **real** (non-dry) run, after `router.finish`:
    - `s = suggest.due(...)`
    - if `s`, then `if notifier.suggest(s, review_command(s.key)): suggest.mark_sent(...)`
    - all of this is wrapped so it never fails the run
  - `route.review_command(key) -> list[str]` returns `[sys.executable, <abs path of route.py>, "--review-suggestion", key]`.
  - **CLI flags:**
    - `--suggestions` prints the pending suggestions and any due one: `<key>\t<name>\t<n> files`.
    - `--accept-suggestion KEY [--name NAME]`
    - `--decline-suggestion KEY [--never]`
    - `--review-suggestion KEY` opens the dialog, then accepts or declines. On `similar`, it calls `confirm_similar` and then `accept_into_existing`. It reports the outcome with a plain `display notification`.
  - The accept, review and decline flags take the route lock, waiting up to 60 s (poll `acquire_lock` every 0.5 s). If the lock is still held, they print and notify `mac-organizer is busy, try again shortly` and exit 1. They never start the model.

- [ ] **Step 1: Write the failing tests**
  - `test_dialog_parses_buttons_and_name`
  - `test_dialog_escapes_quotes_and_newlines`
  - `test_dialog_esc_is_cancel`
  - `test_notifier_suggest_uses_execute_and_exact_copy`
  - `test_no_terminal_notifier_does_not_mark_sent_and_cli_lists_it` (Review Focus 5)
  - `test_review_create_moves_files`
  - `test_review_near_duplicate_then_use_existing`
  - `test_review_never_and_not_now`
  - `test_review_stale_key_is_noop` (Review Focus 3)
  - `test_review_waits_for_lock_then_reports_busy` (with the lock held by the test)
  - `test_main_offers_at_most_one_suggestion_per_day`
  - `test_dry_run_never_offers`
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite. Expect all to pass.**
- [ ] **Step 5: Commit** `feat: suggestion notification, review dialog and CLI`

### Task 6: Backlog — loose Documents files, Misc preview, NEWFOLDER lines

**Files:**
- Modify: `backlog.py` (`Line`, `_plan_dir`, `plan`, `_write_report`, `read_report`, `apply`, the checker), `route.py` (the CLI help text, if it lists paths)
- Test: `tests/test_backlog.py`

**Interfaces:**
- Consumes: `Verdict.topic`, `topics.normalise`, `topics.near_duplicate`, `topics.valid_folder_name`, `SuggestSettings.min_items`.
- Produces:
  - `Line.topic: str = ""`, a 6th TSV column. `read_report` accepts 5 or 6 columns.
  - `NewFolder(key: str, name: str, count: int, examples: str)`. `read_report` returns `(planned_at, lines, newfolders)`. Update every caller.
  - **Planning `~/Documents`:** only regular files directly in it (no folders, nothing in `Misc`/`Keys`/`Inbox`), routed with `source=~/Downloads`.
  - **Planning `~/Documents/Misc`:** every regular file is classified (with Downloads rules, `source=~/Downloads`), but **no lines are emitted for these items**. Unsure items with a topic only feed the clusters; the Misc items stay put. Each qualifying cluster (≥ `min_items`, no near-duplicate) gets a `NEWFOLDER` line plus, for each of its items, a `MOVE <misc item> -> <misc item>` placeholder line with action `MOVE` and topic set. Apply skips placeholder lines (src == dst) unless their topic's folder was accepted.
  - **Other plans:** unsure items going to Misc carry their topic. Clusters from them become `NEWFOLDER` lines.
  - The header explains the suggestions. Every `NEWFOLDER` line is written **commented out** (`#NEWFOLDER…`).
  - **Apply:** an un-commented `NEWFOLDER` is valid only if its key matches the topic on at least one line in the report, its name passes `valid_folder_name` and the near-duplicate check, and its key hasn't appeared before. Valid ones:
    - get their folder created first (plain `mkdir`)
    - log `NEWFOLDER`
    - redirect every `MOVE` line with that topic (dst in Misc, or a placeholder) to `documents_root/name/<src.name>`, using no-clobber moves and logging `SUGGEST`

    Invalid ones are printed as `REJECTED NEWFOLDER <name>: <why>` and the rest still applies (Review Focus 4).

- [ ] **Step 1: Write the failing tests**
  - `test_plan_includes_loose_documents_files_only`
  - `test_plan_emits_commented_newfolder_for_cluster`
  - `test_apply_without_uncommenting_creates_no_folder`
  - `test_apply_accepted_newfolder_with_edited_name_redirects_moves`
  - `test_misc_preview_only_newfolder_and_items_stay_unless_accepted`
  - `test_hand_edited_newfolder_rejected_rest_applies` (Review Focus 4): an unknown key, `a/b`, `..`, and a duplicate key
  - `test_five_column_reports_still_parse`
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite. Expect all to pass.**
- [ ] **Step 5: Commit** `feat: backlog suggests new folders (opt-in NEWFOLDER lines); loose Documents files and Misc preview`

### Task 7: Install — busy-Mac question and starter folders; README

**Files:**
- Modify: `setup.py` (`main`, new `offer_starter_folders`), `install.sh`, `README.md`
- Test: `tests/test_setup.py`, `tests/test_scripts.py`, `tests/test_readme.py`

**Interfaces:**
- Produces:
  - `setup.documents_is_empty(documents_root: Path) -> bool` is True when there are no visible folders other than `Misc`, `Keys` and `Inbox`.
  - `setup.offer_starter_folders(names, documents_root, state, ask=input, interactive: bool) -> list[Path]` asks once (a `starter-asked` stamp in state). Only `y`/`yes` (case-insensitive) creates the folders; anything else does nothing. Not interactive means nothing is asked and nothing is created.
  - `setup.main` calls it with `interactive = sys.stdin.isatty()` after `make_dirs`.
  - **`install.sh`:** after the tool check and before `setup.py`, if stdin is a TTY, count the visible items in `~/Downloads` and the loose files in `~/Documents`. If there are more than 50 or more than 20, print the hint (spec §5) and ask `Load the background sorter now? [y/N]`.
    - If the answer isn't yes, run `setup.py` but skip loading the agents (unload any that are loaded), and print how to load them later (`./install.sh`).
    - When not interactive, keep the current behaviour.
    - `MAC_ORGANIZER_ASSUME_YES=1` skips the question (for tests and scripts).
  - **README:**
    - add a "Setting up your folders" section covering new users (the starter question), long-time users (preview with suggestions, removing `#` to accept, editing names), day to day (the notification, the dialog, `--suggestions` without terminal-notifier), and `expand`
    - update Quick start step 4 to `python3 route.py --backlog ~/Downloads ~/Documents` and mention `~/Documents/Misc` previews

- [ ] **Step 1: Write the failing tests**
  - `test_documents_is_empty_ignores_own_folders`
  - `test_starter_folders_only_on_yes_and_asked_once`
  - `test_starter_not_asked_when_not_interactive`
  - `test_install_busy_mac_no_skips_loading_agents` (fake TTY via env override `MAC_ORGANIZER_FORCE_TTY=1` plus piped "n")
  - `test_install_assume_yes_loads`
  - `test_readme_documents_suggestions_and_expand`
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite. Expect all to pass.**
- [ ] **Step 5: Commit** `feat: install asks before sorting a busy Mac and offers starter folders; README folder setup`

### Task 8: Cut-over (controller, user-gated)

There's no new code in this task. **⛔ = wait for the user.**
- [ ] **Step 1:** Run a live check with the real model on a throwaway HOME. Create 3 synthetic tax PDFs and confirm the model returns unsure with a topic. Record the result in the report.
- [ ] **Step 2 ⛔:** Unload the agents, merge `suggest` into `main`, regenerate `rules.toml` (back up the old one first), and run `./install.sh` (the user's Mac isn't busy now). Run the tests in `~/.mac-organizer`.
- [ ] **Step 3 ⛔:** Offer the user `route.py --backlog ~/Documents/Misc` for their existing Misc items, and walk through accepting suggestions.
- [ ] **Step 4 ⛔:** Tag `v2.2.0`, push, and create the release.
