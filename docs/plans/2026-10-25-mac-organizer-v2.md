# mac-organizer v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the v1 router so it reads unplaced documents, zips and Downloads folders and files them into existing `~/Documents` folders. It does this with a local model, and also discards identical duplicates, covers the leftover file types, notifies calmly, learns from Finder corrections, ejects stale installer disks, and cleans the backlog through an approved dry run.

**Architecture:** `route.py` stays the engine and entry point. New behaviour goes into focused stdlib-only modules that `route.py` calls:

| Module | Responsibility |
|---|---|
| `fsops.py` | No-clobber moves of files and directories, plus Trash |
| `dedupe.py` | Duplicate detection |
| `extract.py` | Text samples |
| `classify.py` | Candidate folders, the prompt, and the llama-server lifecycle and client |
| `memory.py` | Placements, corrections and the verdict cache |
| `notify.py` | Batch summary and notifications |
| `disks.py` | Ejecting installer disks |
| `backlog.py` | Backlog dry-run report and apply |

Every external tool is called through an injectable `runner`, following the existing `setup.detect_screenshot_dir(runner=...)` pattern, so tests never spawn real tools or a model.

**Tech Stack:** Python 3.11+ stdlib only at runtime (`tomllib`, `zipfile`, `hashlib`, `json`, `subprocess`, `urllib.request`, `ctypes`, `plistlib`). pytest. External tools are all from Homebrew or built into macOS: `llama-server` (llama.cpp 0.7.0), `pdftotext` (poppler), `terminal-notifier`, `textutil`, `hdiutil`, `mdfind`.

**Spec:** `docs/specs/2026-10-25-mac-organizer-v2-design.md` (v1 spec: `docs/specs/2026-09-07-mac-organizer-design.md`)

**Where to work:** `~/.mac-organizer` is the live install that launchd runs. Do all work in a git worktree on branch `v2`, created with `superpowers:using-git-worktrees`. Never edit, run `install.sh` in, or check out another branch in `~/.mac-organizer` until Task 12.

## Global Constraints

- Python **>= 3.11**. **Standard library only** at runtime. The only dev dependency is pytest.
- All 58 existing tests stay green. Existing public names in `route.py` keep their signatures (`load_rules`, `match_rule`, `plan_action`, `apply_action`, `move_no_clobber`, `process_dir`, `main`).
- **No file content leaves the Mac.** The only network traffic is HTTP to `127.0.0.1` (llama-server). The server is started with `--host 127.0.0.1`.
- **Never overwrite, never merge directories, never delete permanently.** Discarding means moving into `~/.Trash`, with ` -2` on a name clash.
- **The smart sort never creates a destination folder.** It may only choose existing candidate folders. The single exception is an approved backlog project group (Task 10).
- Destinations must resolve inside `$HOME` (v1 `within_home`).
- Safety skips: dotfiles, `.crdownload` `.part` `.download` `.tmp`, files under **60 s** old, directories whose newest contained mtime is under **300 s** old, and directories that are a rule's destination.
- Folder handling applies to **`~/Downloads` only**. `~/Desktop`, `~/Inbox` and screenshots stay files-only, except that `.app` bundles on the Desktop go to `~/Downloads/Installers`.
- Smart-sort confidence threshold default is **0.6**. Below it the result counts as unsure.
- The model is started only when a batch has at least one item needing a fresh verdict, and is stopped when the batch ends. It is never left running.
- Default model path: `~/Library/Application Support/Orb/models/gguf/google_gemma-4-E4B-it-Q4_K_M.gguf`. It is written only into git-ignored `rules.local.toml`.
- Action log format is unchanged: `<ISO8601-UTC>  <VERB>  <src> -> <dst>`. New verbs: `DUPLICATE` (dst = the existing identical item), `TRASH`, `SMART` (a move decided by the model), `EJECT` (src = mount point, dst = image path).
- Notification copy: start = `Files are being sorted…`. Summary = `<N> files sorted: <n> → <Folder>, …, <k> left in Inbox`, plus `, <d> duplicates to Trash` when d > 0, and `AI sorting unavailable` appended on server or model failure.
- State lives in `~/.local/state/mac-organizer/` (`placements.jsonl`, `corrections.jsonl`, `verdicts.jsonl`, `disks.json`, `reports/`).

## Review Focus

1. **Identical copies inside one backlog run.** `X.pdf` and `X -2.pdf` (identical) both classified into NLP: the report must show one MOVE and one TRASH, not two MOVEs. Test: Task 10.
2. **The model misbehaves.** Malformed JSON, a folder not in the candidate list, an HTTP error or a server crash mid-batch must each count as unsure for that item. The rest of the batch still routes, and the summary says AI unavailable when the server died. Tests: Tasks 5 and 7.
3. **Same-named folder at the destination.** Moving `~/Downloads/data/` into `Development/`, which already has a different `data/`, must give `data -2/`. It must never merge or replace, including an *empty* existing `data/`, which plain `rename(2)` would silently replace. Test: Task 2.
4. **Unreadable documents.** An encrypted or scanned PDF (pdftotext fails or returns nothing), a corrupt docx, or a non-UTF-8 code file must give a filename-only sample with no crash. Test: Task 4.
5. **Quiet sweeps.** A 10-minute sweep where every remaining item already has a cached "unsure" verdict must not start the server or notify. Test: Task 7.

---

### Task 1: Config — v2 rules, local overlay, new folders, migration

**Files:**
- Modify: `route.py` (`Rule`, `load_rules`), `rules.default.toml`, `setup.py`, `.gitignore`
- Create: `rules.local.default.toml`
- Test: `tests/test_route.py`, `tests/test_setup.py`

**Interfaces:**
- Produces:
  - `Rule` gains these fields:
    - `kind: str = "file"` (`"file"` | `"dir"`)
    - `name_regex: str | None = None`, case-insensitive search on the item name
    - `has_installer: bool | None = None`. Task 1 only parses and stores it. `rule_matches` starts honouring it in Task 4, which matches archives whose listing contains `.app`, `.pkg` or `.dmg`. Until then the Task 1 test checks only the rule order and fields for `x.zip`.
  - New `action` values: `"trash"` and `"smart"`. For `smart`, `dest` is the fallback directory, and `""` means leave it in place.
  - `load_config(rules_path: Path, local_path: Path | None) -> Config`, where `Config` is a frozen dataclass with:
    - `rules: list[Rule]`: the local `[[rule]]`s first, then the shipped ones
    - `smart: SmartSettings(enabled: bool, threshold: float, model_path: Path | None, documents_root: Path)`
  - `load_rules` stays as a thin wrapper returning `load_config(path, None).rules`.
  - `setup.write_local_rules(template: Path, out: Path) -> bool` never overwrites.
  - `setup.migrate_rules(out_path: Path) -> Path | None` renames a rules.toml that lacks the `# mac-organizer v2` header line to `rules.toml.v1.bak`, and returns the backup path.

- [ ] **Step 1: Write failing tests**
  - `test_load_config_local_rules_come_first`: local file has one `[[rule]]` and the shipped file has one. Assert `cfg.rules[0]` is the local one.
  - `test_load_config_smart_defaults`: no `[smart_sort]` table gives `enabled is True`, `threshold == 0.6` and `model_path is None`. Local `[smart_sort] model_path="~/m.gguf"` gives `Path.home()/"m.gguf"`.
  - `test_rule_kind_and_regex_parse`: assert `kind == "dir"`, `name_regex == "cv|resume"` and `has_installer is True` round-trip.
  - `test_default_rules_cover_v2_types`: load `rules.default.toml` and use `match_rule` over fake files in `~/Downloads`. Expect:

    | Item | Expected action / dest |
    |---|---|
    | `a.m4a` | `~/Media/Audio` |
    | `a.mov` | `~/Media/Video` |
    | `a.svg` | `~/Media/Images` |
    | `k.pem` | `~/Documents/Keys` |
    | `deck.key` | **not** Keys |
    | `a.ics` aged 2 days | `trash` |
    | `a.ics` aged 1 h | no match |
    | `a.pdf` | `smart` with dest `~/Documents/Inbox` |
    | `x.ipynb` | `smart` with dest `~/Documents/Development` |
    | `x.zip` | first rule needs `has_installer`, next is `smart` with dest `""` |

  - `test_default_rules_desktop_dirs`: a `Foo.app` dir on Desktop goes to `~/Downloads/Installers`. A plain dir on Desktop matches nothing.
  - `test_setup_creates_keys_dir_mode_700`: after `make_dirs()` with `HOME` set to a tmp dir, `~/Documents/Keys` mode is `0o700`, and `~/Media/Audio` and `~/Media/Video` exist.
  - `test_migrate_rules_backs_up_v1_file` and `test_migrate_rules_leaves_v2_file`
  - `test_write_local_rules_never_overwrites`

- [ ] **Step 2: Run them.** `.venv/bin/python -m pytest tests/test_route.py tests/test_setup.py -q` should fail on the new tests only.

- [ ] **Step 3: Implement.**
  - `rules.default.toml` starts with `# mac-organizer v2`. Its per-source rule order for `~/Downloads` and `~/Inbox` follows the spec's type table:
    1. images and svg
    2. audio
    3. video
    4. md/txt
    5. dmg/pkg
    6. keys
    7. ics (`trash`, `min_age_seconds = 86400`)
    8. archives with `has_installer = true` → Installers
    9. archives → `smart`, dest `""`
    10. documents (pdf doc docx pages xls xlsx csv ppt pptx key rtf) → `smart`, dest `~/Documents/Inbox`
    11. code (ipynb py m slx html php js ts json yaml yml sql sh) → `smart`, dest `~/Documents/Development`
  - `~/Downloads` alone adds `kind = "dir"` rules: `ext = ["app"]` → Installers, then any dir → `smart`, dest `""`.
  - `~/Desktop` adds `kind = "dir"`, `ext = ["app"]` → Installers before the existing catch-all file rule.
  - `rules.local.default.toml` holds the `[smart_sort]` model path and a commented example name rule.
  - Add `rules.local.toml` to `.gitignore`.
  - Add `~/Media/Audio`, `~/Media/Video` and `~/Documents/Keys` to `DIRS_TO_CREATE`, and chmod Keys to 700.
  - Call `migrate_rules` before `write_rules` in `setup.main`.

- [ ] **Step 4: Run the full suite.** `.venv/bin/python -m pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(config): v2 rule set, local overlay, keys/audio/video folders, v1 migration`

---

### Task 2: Directory items, Trash, and no-clobber directory moves

**Files:**
- Create: `fsops.py`
- Modify: `route.py` (`should_skip`, `rule_matches`, `process_dir`, `plan_action`, `apply_action`)
- Test: `tests/test_fsops.py`, `tests/test_route.py`

**Interfaces:**
- Consumes: `Rule.kind`, the `trash` action (Task 1)
- Produces:
  - `fsops.move_dir_no_clobber(src: Path, dst: Path) -> Path`: tries `dst`, `dst -2`, … and returns the path used.
  - `fsops.move_item(src: Path, dst: Path) -> Path`: dispatches to `route.move_no_clobber` for files and `move_dir_no_clobber` for dirs. Move `move_no_clobber` and `candidate_names` into `fsops` and re-export them from `route` so the old names keep working.
  - `fsops.trash(path: Path, trash_dir: Path = ~/.Trash) -> Path`
  - `route.dir_is_settled(path: Path, now: float, quiet_seconds: int = 300) -> bool`
  - `route.own_dest_dirs(rules) -> set[Path]`

- [ ] **Step 1: Write failing tests**
  - `test_move_dir_no_clobber_never_replaces_empty_dir`: an empty `dst/data/` exists. Moving `src/data/` gives `dst/data -2`, and `dst/data` is still empty.
  - `test_move_dir_no_clobber_suffixes`: `data`, then `data -2`, then `data -3`.
  - `test_trash_file_and_dir_with_clash`: a file and a dir both land in the tmp trash dir, and the second same-named file becomes ` -2`.
  - `test_dir_not_settled_when_child_recent`: a child file is 10 s old, so `dir_is_settled` is False. Aged 1 h, it is True.
  - `test_process_dir_ignores_dirs_without_dir_rule`: Desktop-style rules leave a subdir in place.
  - `test_process_dir_skips_own_dest_dir`: `~/Downloads/Installers` is never treated as an item, even under a dir catch-all rule.
  - `test_process_dir_moves_app_bundle_to_installers`
  - `test_trash_action_logs_TRASH`

- [ ] **Step 2: Run them.** They should fail.
- [ ] **Step 3: Implement.**
  - `move_dir_no_clobber` uses macOS `renamex_np(src, dst, RENAME_EXCL)` through `ctypes` (`RENAME_EXCL = 0x00000004`). On `EEXIST` it moves to the next candidate. On `EXDEV` it raises `OSError` (cross-volume dir moves are out of scope, because everything stays under `$HOME`).
  - `process_dir` iterates dirs only when a `kind="dir"` rule exists for that source.
  - Cap the `dir_is_settled` walk at 20,000 entries. Beyond that, fall back to the dir's own mtime.
- [ ] **Step 4: Run the full suite.** It should pass.
- [ ] **Step 5: Commit** `feat(route): directory items, trash action, atomic no-clobber dir moves`

---

### Task 3: Duplicate check

**Files:**
- Create: `dedupe.py`
- Modify: `route.py` (`apply_action`)
- Test: `tests/test_dedupe.py`, `tests/test_route.py`

**Interfaces:**
- Produces:
  - `dedupe.file_digest(path) -> str`: SHA-256 hex.
  - `dedupe.tree_digest(path) -> str`: hash over sorted `(relpath, size, file_digest)`, ignoring `.DS_Store`.
  - `dedupe.find_identical(src: Path, dest_dir: Path, extra: dict[str, Path] | None = None) -> Path | None`
    - Files are compared only against same-size files.
    - Dirs are compared against sibling dirs by `tree_digest`.
    - `extra` maps digest → planned path. It is used by the backlog simulation (Task 10).
  - `apply_action` behaviour: before moving, if `find_identical(src, dst.parent)` returns `existing`, then `fsops.trash(src)`, log `DUPLICATE src -> existing`, and return `Action(src, existing)` with `Action.duplicate = True` (a new field, default False).

- [ ] **Step 1: Write failing tests**
  - `test_identical_file_goes_to_trash_not_dash2`: dest has `cv.pdf` and the incoming `cv (1).pdf` has the same bytes. Result: the incoming file is in the trash, dest has no ` -2`, and the log has `DUPLICATE`.
  - `test_same_name_different_bytes_gets_dash2`
  - `test_identical_dir_detected_ignoring_ds_store`
  - `test_dry_run_reports_duplicate_without_moving`
- [ ] **Step 2: Run them.** They should fail.
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run the full suite.** It should pass.
- [ ] **Step 5: Commit** `feat: byte-identical duplicates go to Trash`

---

### Task 4: Text samples and archive inspection

**Files:**
- Create: `extract.py`
- Modify: `route.py` (`rule_matches` evaluates `has_installer` through `extract.archive_has_installer`)
- Test: `tests/test_extract.py`

**Interfaces:**
- Produces:
  - `extract.sample(path: Path, runner=subprocess.run, max_words: int = 1500) -> str`. It always starts with `"Name: <name>\n"`. Then it adds the body below, truncated to `max_words`. It never raises: on any error it returns the name line alone.

    | Item | Body |
    |---|---|
    | pdf | `pdftotext -l 5 -q <path> -` |
    | docx, pptx, xlsx | Text from `word/document.xml`, `ppt/slides/slide*.xml` and `xl/sharedStrings.xml`, read with `zipfile` and a regex that strips XML tags |
    | doc, rtf | `textutil -convert txt -stdout <path>` |
    | pages | Name only |
    | code | First 150 lines, decoded with `errors="replace"` |
    | ipynb | Markdown and code cell sources from the notebook JSON |
    | zip | Up to 200 member names |
    | tar, gz, tgz | Up to 200 member names, from `tarfile` |
    | dir | Up to 200 relative paths, plus `sample()` of up to 3 document files inside, each limited to 300 words |

  - `extract.archive_has_installer(path: Path) -> bool`

- [ ] **Step 1: Write failing tests.** Build fixtures in `tmp_path`: a real docx and pptx via `zipfile` with minimal XML, a zip, a notebook, and a dir. `pdftotext` and `textutil` are stubbed through `runner`.
  - `test_sample_docx_text`
  - `test_sample_pptx_text`
  - `test_sample_pdf_uses_pdftotext`
  - `test_sample_pdf_failure_is_name_only`: the runner raises `CalledProcessError`.
  - `test_sample_pdf_empty_text_is_name_only`
  - `test_sample_corrupt_docx_is_name_only`
  - `test_sample_non_utf8_code`
  - `test_sample_truncates_to_max_words`
  - `test_sample_zip_lists_members_without_extracting`: assert no new files appear in `tmp_path`.
  - `test_sample_dir_includes_paths_and_doc_text`
  - `test_archive_has_installer_true_for_app_inside`, `..._false_for_sources`
  - `test_archive_has_installer_false_for_corrupt_zip`
- [ ] **Step 2: Run them.** They should fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.** It should pass.
- [ ] **Step 5: Commit** `feat: on-device text sampling for documents, code, archives, folders`

---

### Task 5: Classifier — candidates, prompt, local server

**Files:**
- Create: `classify.py`
- Test: `tests/test_classify.py`, `tests/test_classify_live.py` (opt-in)

**Interfaces:**
- Consumes: `SmartSettings` (Task 1)
- Produces:
  - `Candidate(name: str, path: Path, examples: tuple[str, ...])`. `name` is relative to `documents_root`, e.g. `"Academics/Natural Language Processing"`.
  - `classify.candidates(documents_root: Path) -> list[Candidate]` collects:
    - top-level dirs, excluding `Inbox`, `Keys` and hidden dirs
    - first-level dirs of `Academics`
    - up to 5 example filenames each
    - output sorted by name
  - `candidates_hash(cands) -> str`
  - `Verdict(folder: str | None, confidence: float, reason: str)`. `folder is None` means unsure.
  - `classify.build_request(sample: str, cands, examples: list[dict]) -> dict` returns an OpenAI-style chat body with:
    - `temperature: 0`
    - `response_format = {"type": "json_schema", "json_schema": {"schema": SCHEMA}}`
    - `SCHEMA.folder` an `enum` of candidate names plus `"unsure"`
    - `confidence` a number from 0 to 1
    - `reason` a string of at most 200 characters
  - `classify.parse_verdict(body: dict, cands, threshold: float) -> Verdict`
  - `class LlamaServer` is a context manager: `LlamaServer(model_path, popen=subprocess.Popen, urlopen=urllib.request.urlopen, port=None)`.
    - `__enter__` picks a free port by binding `127.0.0.1:0`.
    - It launches `llama-server -m <model> --host 127.0.0.1 --port <p> -c 8192`.
    - It polls `GET /health` until 200, for up to 120 s.
    - `__exit__` terminates the process, then kills it after 10 s.
  - `LlamaServer.classify(sample, cands, examples, threshold) -> Verdict` POSTs to `/v1/chat/completions`.
  - `class ServerUnavailable(Exception)` is raised when the model file is missing, the process exits, or `/health` times out.

- [ ] **Step 1: Write failing tests.** Use fake `popen` and `urlopen`, with no real process.
  - `test_candidates_top_level_plus_academics_courses_excludes_inbox_keys_hidden`
  - `test_request_schema_enum_is_candidates_plus_unsure`
  - `test_request_never_targets_non_localhost`: assert the URL starts with `http://127.0.0.1:`.
  - `test_parse_verdict_below_threshold_is_unsure`: 0.59 → None and 0.6 → the folder.
  - `test_parse_verdict_unknown_folder_is_unsure`
  - `test_parse_verdict_malformed_json_is_unsure`
  - `test_classify_http_error_is_unsure`
  - `test_server_missing_model_raises_unavailable`
  - `test_server_health_timeout_raises_unavailable_and_kills_process`
  - `test_server_exit_terminates_process`
  - `test_classify_after_process_died_raises_unavailable`
- [ ] **Step 2: Run them.** They should fail.
- [ ] **Step 3: Implement.** Prompt wording is the builder's call. It must say:
  - pick exactly one folder from the list, or "unsure"
  - "unsure" is better than a wrong guess
  - each candidate is described by its name and example filenames
  - prior corrections are shown as "<file> belongs in <right>, not <wrong>"
- [ ] **Step 4: Add `test_classify_live.py`.** It is skipped unless `MAC_ORGANIZER_LIVE=1`. It starts the real server on the default model and classifies a synthetic OpenPGP lab text against candidates built from the real `~/Documents`. It asserts the verdict is `Academics/Cyber Security Essentials` or unsure, never another folder. Run it once by hand: `MAC_ORGANIZER_LIVE=1 .venv/bin/python -m pytest tests/test_classify_live.py -q -s`. If the chat template fails, add `--jinja` to the server arguments here.
- [ ] **Step 5: Run the full suite.** It should pass, with the live test skipped.
- [ ] **Step 6: Commit** `feat: local llama.cpp classifier with constrained output`

---

### Task 6: Memory — placements, corrections, verdict cache

**Files:**
- Create: `memory.py`
- Test: `tests/test_memory.py`

**Interfaces:**
- Produces (all paths are under the state dir passed in):
  - `record_placement(state: Path, dst: Path, folder: str, summary: str, now: float) -> None`. It stores the inode and size.
  - `detect_corrections(state: Path, cands: list[Candidate], now: float, locate=mdfind_locate) -> list[dict]`. It checks placements from the last 30 days that are missing at their path:
    - `locate(name) -> list[Path]` (default `mdfind -onlyin ~/Documents -name <name>`) finds a path with matching inode and size
    - if the item now sits under a *different* candidate, it appends `{file, summary, wrong, right, at}` to `corrections.jsonl` and marks the placement resolved
    - items moved outside `~/Documents` or deleted are marked resolved without a correction
  - `select_examples(state: Path, name: str, k: int = 10) -> list[dict]`: the most recent corrections, ranked by filename-token overlap with `name`.
  - `corrections_count(state) -> int`
  - `VerdictCache(state)` with:
    - `.get(path, size, mtime, cands_hash, corrections_count) -> bool`: True if an "unsure" verdict is cached for exactly this key
    - `.put_unsure(...)`

- [ ] **Step 1: Write failing tests**
  - `test_correction_recorded_when_moved_to_other_candidate`
  - `test_move_within_same_candidate_subfolder_is_not_a_correction`
  - `test_item_moved_outside_documents_is_ignored`
  - `test_placements_older_than_30_days_ignored`
  - `test_select_examples_prefers_token_overlap_and_caps_k`
  - `test_verdict_cache_hits_only_on_identical_key`: changing size, mtime, the candidate hash or the corrections count misses.
- [ ] **Step 2: Run them.** They should fail.
- [ ] **Step 3: Implement.** Use JSONL, append-only. Resolved placements are rewritten by compacting the file at the end of `detect_corrections`.
- [ ] **Step 4: Run the full suite.** It should pass.
- [ ] **Step 5: Commit** `feat: learn from Finder corrections; cache unsure verdicts`

---

### Task 7: Smart action wired into routing

**Files:**
- Modify: `route.py` (`process_dir`, `apply_action`, `main`)
- Create: `batch.py` (`Batch` accumulator)
- Test: `tests/test_smart_route.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `Batch` records `(src, dst, folder | None, kind)` with `kind` ∈ `{"rule", "smart", "inbox", "duplicate", "stay"}`. It also has `.notable: bool` (any smart, inbox or duplicate entry from a smart-routed item) and `.ai_unavailable: bool`.
  - `route.SmartRouter(settings, state, classifier_factory=LlamaServer, notifier=None)`:
    - It starts the server lazily, at most once per run, on the first item needing a verdict. Just before that it calls `notifier.start()` once.
    - `.route(path, rule, now) -> Action | None` works as follows:
      - Cache hit → None, and the item stays.
      - Verdict folder → `Action(path, cand.path / name)`.
      - Unsure → `Action(path, rule.dest / name)` when `rule.dest`, otherwise put unsure in the cache and return None.
      - `ServerUnavailable` → mark `batch.ai_unavailable`, treat this and all later items in the run as unsure, and **do not** cache them.
    - It records a placement for every smart move and logs verb `SMART`.
    - `.close()` stops the server.
  - `main` runs `memory.detect_corrections` once per run before routing. At the end it calls `notifier.summary(batch)` when `batch.notable`.
  - If `smart.enabled` is false or `model_path` is missing, it behaves as permanent `ServerUnavailable`, and notifies at most once per day (tracked in the state dir).

- [ ] **Step 1: Write failing tests** with a fake classifier and fake notifier.
  - `test_unplaced_pdf_moves_to_chosen_course`
  - `test_unsure_pdf_goes_to_documents_inbox`
  - `test_unsure_zip_stays_and_is_cached`
  - `test_cached_unsure_items_do_not_start_server_or_notify` (Review Focus 5)
  - `test_server_started_once_for_many_items_and_closed`
  - `test_server_crash_mid_batch_routes_rest_to_fallback_and_flags_unavailable` (Review Focus 2)
  - `test_rule_only_batch_is_silent`: screenshots and images only gives no notifier calls.
  - `test_smart_move_never_creates_folder`: a verdict for a candidate deleted mid-run → the item takes the fallback.
  - `test_duplicate_at_smart_destination_goes_to_trash`
  - `test_desktop_dir_never_smart_routed`
- [ ] **Step 2: Run them.** They should fail.
- [ ] **Step 3: Implement.** The v1 lock still wraps the whole run.
- [ ] **Step 4: Run the full suite.** It should pass.
- [ ] **Step 5: Commit** `feat: smart sort for unplaced documents, archives and Downloads folders`

---

### Task 8: Notifications and batch report

**Files:**
- Create: `notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `Batch` (Task 7)
- Produces: `Notifier(state: Path, runner=subprocess.run, which=shutil.which)` with:
  - `.start()`
  - `.summary(batch) -> Path`, which returns the report path
  - `summary_text(batch) -> str` (pure)

  The report is `reports/<UTC timestamp>.txt`, one line per item: `<name>  →  <folder or "Inbox" or "Trash (duplicate)">  — <reason>`. When `terminal-notifier` exists, the notifier runs `terminal-notifier -title mac-organizer -message <text> -group mac-organizer -open file://<report>`. Otherwise it falls back to `osascript -e 'display notification ...'` with no click action.

- [ ] **Step 1: Write failing tests**
  - `test_summary_text_groups_by_folder`: the exact string `7 files sorted: 5 → Natural Language Processing, 1 → Development, 1 left in Inbox`, using the folder's last path component.
  - `test_summary_mentions_duplicates_and_ai_unavailable`
  - `test_uses_terminal_notifier_with_open_report`
  - `test_falls_back_to_osascript_and_escapes_quotes`
  - `test_start_message_exact`: `Files are being sorted…`
- [ ] **Step 2: Run them.** They should fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.** It should pass.
- [ ] **Step 5: Commit** `feat: calm batch notifications with clickable report`

---

### Task 9: Eject stale installer disks

**Files:**
- Create: `disks.py`
- Modify: `route.py` (`main` calls `disks.eject_stale(...)` once per run, after routing)
- Test: `tests/test_disks.py`

**Interfaces:**
- Produces: `eject_stale(state: Path, now: float, runner=subprocess.run, min_age: int = 900) -> list[str]`
  - It parses `hdiutil info -plist` (`plistlib`). It considers only images whose `image-path` ends in `.dmg` and resolves under `$HOME`, and which have a `mount-point` under `/Volumes/`.
  - First-seen times are kept in `disks.json`, keyed by image path. Entries no longer mounted are pruned.
  - When an image is ≥ `min_age` since first seen, it runs `hdiutil detach <mount-point>`. **Never `-force`.**
  - A non-zero exit leaves the entry for the next run.
  - It logs `EJECT` for each success and returns the ejected mount points.

- [ ] **Step 1: Write failing tests** using plist fixtures as bytes.
  - `test_first_sighting_records_but_does_not_eject`
  - `test_ejects_after_15_minutes`
  - `test_never_passes_force`
  - `test_ignores_non_dmg_and_outside_home_images`: covers an image under `/Library` and a simulator runtime.
  - `test_ignores_non_image_volumes`: a USB drive does not appear in `hdiutil info`, so assert only listed images are considered.
  - `test_busy_detach_retried_next_run`
  - `test_prunes_unmounted`
- [ ] **Step 2: Run them.** They should fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.** It should pass.
- [ ] **Step 5: Commit** `feat: safely eject installer disk images after 15 minutes`

---

### Task 10: Backlog dry-run and apply

**Files:**
- Create: `backlog.py`
- Modify: `route.py` (`main` adds `--backlog`, and `--apply REPORT` for use with `--backlog`)
- Test: `tests/test_backlog.py`

**Interfaces:**
- Consumes: the routing pieces from Tasks 2–7, through a `plan_item(path, config, smart, now) -> PlannedLine | None` helper extracted from `process_dir`'s decision logic, so live and backlog routing cannot drift apart.
- Produces:
  - `backlog.plan(paths: list[Path], config, smart, now) -> Path` writes `reports/backlog-<ts>.tsv` and prints its path. It moves nothing.
    - Columns are `action<TAB>src<TAB>dst<TAB>reason`, with action ∈ `MOVE | TRASH | GROUP`.
    - The header comment explains that deleting a line or prefixing it with `#` vetoes it.
  - `backlog.apply(report: Path, log_path: Path) -> list[Action]` performs only the non-`#` lines. It re-checks that each `src` still exists, that `dst` is within home, and the duplicate check, and logs each result.
  - Planning rules:
    - `~/Documents/Inbox` is routed with the `~/Downloads` document and code rules. Its unsure items stay, because the fallback equals the source.
    - `LEFTOVER_DIRS = ("Keys", "Images", "Documents", "Installers & Archives")` under `~/Downloads`: each child is planned as if it sat directly in `~/Downloads`. On apply, a leftover dir left empty (ignoring `.DS_Store`) is removed with `rmdir`.
    - Project groups: a top-level marker in `~/Downloads` (`package.json`, `pyproject.toml`, `pubspec.yaml`, `Cargo.toml`, `go.mod`) collects siblings that match `PROJECT_SIBLINGS`. These are dirs `app components lib public src pages styles assets`, plus files matching `*.config.*`, `tsconfig*.json`, `*lock*`, `.env*`, `.gitignore`, `components.json`, `next-env.d.ts`, `postcss.config.*`.
    - Each group member is a `GROUP` line with dst `~/Documents/Development/<name>/<member>`. `<name>` comes from the marker's `name` field, sanitised, falling back to the marker's parent folder name.
    - A simulated-arrivals map `dest_dir → {digest: planned_path}` is passed to `find_identical(..., extra=)`, so identical items in one run produce one MOVE and the rest TRASH.

- [ ] **Step 1: Write failing tests** with the fake classifier.
  - `test_plan_moves_nothing`
  - `test_identical_pair_in_one_run_is_move_plus_trash` (Review Focus 1)
  - `test_vetoed_line_is_not_applied`
  - `test_apply_skips_missing_src_and_continues`
  - `test_leftover_keys_dir_unpacked_to_documents_keys_and_removed_when_empty`
  - `test_leftover_dir_not_removed_if_vetoed_child_remains`
  - `test_project_group_named_from_package_json`
  - `test_plan_and_live_routing_agree`: the same item gives the same destination through `process_dir --dry-run` and through `plan`.
- [ ] **Step 2: Run them.** They should fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite.** It should pass.
- [ ] **Step 5: Commit** `feat: backlog dry-run report with per-line veto and apply`

---

### Task 11: Install, README, version

**Files:**
- Modify: `install.sh`, `README.md`, `pyproject.toml`
- Test: `tests/test_scripts.py`, `tests/test_readme.py`

- [ ] **Step 1: Write failing tests**
  - `test_install_warns_for_missing_tools`: with a `PATH` lacking `llama-server`, `pdftotext` and `terminal-notifier`, the output contains `brew install llama.cpp poppler terminal-notifier`, listing only the missing ones, and the exit code is 0.
  - `test_readme_documents_v2`: the README mentions `rules.local.toml`, `--backlog`, `Documents/Keys` and `eject`, and no longer lists "content analysis" as a non-goal.
- [ ] **Step 2: Run them.** They should fail.
- [ ] **Step 3: Implement.**
  - The tool check in `install.sh` runs after the Python guard.
  - README:
    - update the routing table
    - add sections on smart sort and privacy (on-device, localhost only), duplicates, notifications, corrections, disk ejecting and backlog
    - add a "Changing the model" section
  - Set `pyproject.toml` version to `2.0.0`.
- [ ] **Step 4: Run the full suite.** It should pass.
- [ ] **Step 5: Commit** `docs: v2 README; install checks external tools`

---

### Task 12: Cut-over, backlog, release (user-gated)

There is no new code in this task. **Each ⛔ step waits for the user's explicit go-ahead.**

The order matters (final review C2, Ruling 22). The v2 sweep agent runs at load, so the agents must stay unloaded until the backlog is approved and applied. Otherwise the live sweep would move `~/Downloads` items before the user sees the report.

- [ ] **Step 1:** `brew install poppler terminal-notifier`. Verify with `which pdftotext terminal-notifier`.
- [ ] **Step 2:** Run the live model test against the real model: `MAC_ORGANIZER_LIVE=1 MAC_ORGANIZER_MODEL=<model path> .venv/bin/python -m pytest tests/test_classify_live.py -q -s`. Expected: pass.
- [ ] **Step 3 ⛔:** Unload all `com.mac-organizer.*` agents (`launchctl bootout`). Then merge `v2` into `main` in `~/.mac-organizer`, run `python3 setup.py` (which writes rules and plists), and immediately unload any agents it wrote **without loading them**. Verify:
  - `rules.toml.v1.bak` exists
  - `launchctl list | grep mac-organizer` is empty
  - the full test suite passes in `~/.mac-organizer`
- [ ] **Step 4:** Write the user's model path into the git-ignored `~/.mac-organizer/rules.local.toml`.
- [ ] **Step 5:** `python3 route.py --backlog ~/Documents/Inbox ~/Downloads`. Hand the report path to the user, and summarise the counts per destination plus the TRASH and GROUP lines.
- [ ] **Step 6 ⛔:** After the user approves (with any vetoes), run `python3 route.py --backlog --apply <report>`. Report the before and after counts for `~/Documents/Inbox` and `~/Downloads`.
- [ ] **Step 7 ⛔:** Load the agents with `./install.sh`. Verify that `launchctl list | grep mac-organizer` shows 5 agents and that the install dry-run preview looks sane.
- [ ] **Step 8:** `git tag -a v2.0.0 -m "mac-organizer v2.0.0"`
- [ ] **Step 9 ⛔:** The user runs `! gh auth login` and chooses public or private. Then:
  - `gh repo create mac-organizer --<visibility> --source ~/.mac-organizer --push`
  - `git push origin v1.0.0 v2.0.0`
  - create GitHub releases for both tags with short notes

  Before pushing, confirm with `git ls-files` that nothing personal is tracked: no `rules.toml`, `rules.local.toml`, state or logs. Also consider whether course names in the tracked docs and specs should be genericised first.
