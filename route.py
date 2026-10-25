"""Automatic file router for macOS drop-zone folders."""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import os
import re
import signal
import sys
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import classify
import dedupe as dupes
import disks
import extract
import fsops
import memory
import notify
from batch import Batch, NullNotifier
from fsops import DestDirMissing, candidate_names, move_no_clobber  # noqa: F401  (re-exported)


@dataclass(frozen=True)
class Rule:
    source: Path
    action: str
    dest: str = ""
    ext: frozenset[str] = frozenset()
    glob: str | None = None
    min_age_seconds: int = 0
    kind: str = "file"  # "file" or "dir"
    name_regex: str | None = None  # case-insensitive search on the item name
    has_installer: bool | None = None  # archives only


@dataclass(frozen=True)
class SmartSettings:
    enabled: bool
    threshold: float
    model_path: Path | None
    documents_root: Path


@dataclass(frozen=True)
class Config:
    rules: list[Rule]
    smart: SmartSettings


def _norm_ext(values) -> frozenset[str]:
    return frozenset(str(v).lower().lstrip(".") for v in values)


def _read_toml(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


RULE_KINDS = ("file", "dir")


def _check_rule(n: int, entry: dict) -> None:
    """A rule that could never work fails at load, naming the rule, not every sweep."""
    where = f"rule {n} (source = {entry.get('source')!r})"
    kind = entry.get("kind", "file")
    if kind not in RULE_KINDS:
        raise ValueError(f"{where}: kind must be \"file\" or \"dir\", not {kind!r}")
    regex = entry.get("name_regex")
    if regex is not None:
        if not isinstance(regex, str):
            raise ValueError(f"{where}: name_regex must be a string")
        try:
            re.compile(regex)
        except re.error as exc:
            raise ValueError(f"{where}: name_regex {regex!r} does not compile: {exc}") from None


def _parse_rules(data: dict) -> list[Rule]:
    rules: list[Rule] = []
    for n, entry in enumerate(data.get("rule", []), 1):
        _check_rule(n, entry)
        has_installer = entry.get("has_installer")
        rules.append(
            Rule(
                source=Path(entry["source"]).expanduser(),
                action=entry["action"],
                dest=entry.get("dest", ""),
                ext=_norm_ext(entry.get("ext", [])),
                glob=entry.get("glob"),
                min_age_seconds=int(entry.get("min_age_seconds", 0)),
                kind=entry.get("kind", "file"),
                name_regex=entry.get("name_regex"),
                has_installer=None if has_installer is None else bool(has_installer),
            )
        )
    return rules


def load_config(rules_path: Path, local_path: Path | None) -> Config:
    """Shipped rules plus an optional local overlay; local rules win."""
    with open(rules_path, "rb") as fh:
        shipped = tomllib.load(fh)
    local = _read_toml(local_path)
    smart = {**shipped.get("smart_sort", {}), **local.get("smart_sort", {})}
    model = smart.get("model_path")
    settings = SmartSettings(
        enabled=bool(smart.get("enabled", True)),
        threshold=float(smart.get("threshold", 0.6)),
        model_path=Path(model).expanduser() if model else None,
        documents_root=Path(smart.get("documents_root", "~/Documents")).expanduser(),
    )
    return Config(rules=_parse_rules(local) + _parse_rules(shipped), smart=settings)


def load_rules(path: Path) -> list[Rule]:
    return load_config(path, None).rules


def sources(rules: list[Rule]) -> list[Path]:
    return sorted({r.source for r in rules})


def dest_root(dest: str) -> Path:
    """The literal directory prefix of ``dest``, before any strftime token."""
    cut = dest.find("%")
    literal = dest if cut == -1 else dest[:cut].rstrip("/")
    return Path(literal).expanduser()


def cycle_warnings(rules: list[Rule]) -> list[str]:
    """Warn about rule chains that route files back to a folder they came from.

    A chain (``~/Desktop`` → ``~/Inbox`` → ``~/Documents/Inbox``) is normal and
    is not reported; only a loop back onto an earlier source is.
    """
    edges: dict[Path, list[tuple[Path, Rule]]] = {}
    known = {r.source for r in rules}
    for rule in rules:
        if rule.action == "skip" or not rule.dest:
            continue
        target = dest_root(rule.dest)
        if target in known:
            edges.setdefault(rule.source, []).append((target, rule))

    def find_cycle(start: Path, node: Path, chain: list[Rule], seen: set[Path]):
        for target, rule in edges.get(node, []):
            step = chain + [rule]
            if target == start:
                return step
            if target not in seen:
                found = find_cycle(start, target, step, seen | {target})
                if found:
                    return found
        return None

    warnings: list[str] = []
    reported: set[Path] = set()
    for start in sorted(edges):
        if start in reported:
            continue
        cycle = find_cycle(start, start, [], {start})
        if cycle:
            reported.update(rule.source for rule in cycle)
            hops = " ; ".join(f"{r.source} -> {dest_root(r.dest)}" for r in cycle)
            warnings.append(f"WARNING: routing cycle in rules: {hops}")
    return warnings


def own_dest_dirs(rules: list[Rule]) -> set[Path]:
    """Folders some rule routes into; these are never treated as items."""
    return {
        dest_root(r.dest).resolve()
        for r in rules
        if r.dest and r.action not in ("skip", "trash")
    }


MIN_AGE_SECONDS = 60
DIR_QUIET_SECONDS = 300
DIR_WALK_LIMIT = 5_000
SKIP_SUFFIXES = frozenset({".crdownload", ".part", ".download", ".tmp"})
# tool-managed trees: never walked into (their own entry is still checked)
VENDORED_DIRS = extract.VENDORED_DIRS


def _age_seconds(path: Path, now: float) -> float:
    return now - path.stat().st_mtime


def dir_is_settled(path: Path, now: float, quiet_seconds: int = DIR_QUIET_SECONDS) -> bool:
    """True if nothing inside ``path`` (nor ``path``) changed in ``quiet_seconds``.

    Vendored trees (``VENDORED_DIRS``: node_modules, .git, build output, …)
    are not walked into. The walk is capped at ``DIR_WALK_LIMIT`` entries; past
    that only what was seen so far is trusted.
    """
    newest = path.stat().st_mtime
    seen = 0
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            seen += 1
            if seen > DIR_WALK_LIMIT:
                return now - newest >= quiet_seconds
            try:
                newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
            except OSError:
                continue  # vanished mid-walk: something is still changing
        dirs[:] = [d for d in dirs if d not in VENDORED_DIRS]
    return now - newest >= quiet_seconds


def should_skip(path: Path, now: float) -> bool:
    if path.name.startswith("."):
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    if path.is_dir():
        return not dir_is_settled(path, now)
    return _age_seconds(path, now) < MIN_AGE_SECONDS


def rule_matches(rule: Rule, path: Path, now: float) -> bool:
    if rule.kind == "dir":
        if not path.is_dir() or path.is_symlink():
            return False
    elif not path.is_file():
        return False
    if rule.name_regex and not re.search(rule.name_regex, path.name, re.IGNORECASE):
        return False
    if rule.ext and path.suffix.lower().lstrip(".") not in rule.ext:
        return False
    if rule.glob and not fnmatch.fnmatch(path.name, rule.glob):
        return False
    if rule.min_age_seconds and _age_seconds(path, now) < rule.min_age_seconds:
        return False
    if rule.has_installer is not None and extract.archive_has_installer(path) != rule.has_installer:
        return False
    return True


def match_rule(path: Path, rules: list[Rule], now: float) -> Rule | None:
    for rule in rules:
        if rule_matches(rule, path, now):
            return None if rule.action == "skip" else rule
    return None


STRFTIME_TOKENS = frozenset("aAbBcdfGHIjmMpSUuVwWxXyYzZ%")


def has_strftime_token(dest: str) -> bool:
    """True if ``dest`` contains a directive strftime would expand."""
    i = 0
    while (i := dest.find("%", i)) != -1:
        if i + 1 < len(dest) and dest[i + 1] in STRFTIME_TOKENS:
            return True
        i += 1
    return False


def resolve_dest(dest: str, mtime: float) -> Path:
    expanded = dest
    if has_strftime_token(dest):
        expanded = datetime.fromtimestamp(mtime).strftime(dest)
    return Path(expanded).expanduser()


def within_home(path: Path) -> bool:
    home = Path.home().resolve()
    resolved = path.resolve()
    return resolved == home or home in resolved.parents


def timestamp_name(mtime: float, suffix: str) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d_%H-%M-%S") + suffix


def dedupe(path: Path) -> Path:
    """First free name for ``path``. Advisory only — racy by nature.

    Used for the ``--dry-run`` preview. Real moves go through
    :func:`move_no_clobber`, which claims the name atomically.
    """
    for candidate in candidate_names(path):
        if not candidate.exists():
            return candidate


@dataclass(frozen=True)
class Action:
    src: Path
    dst: Path
    verb: str = "MOVE"
    duplicate: bool = False
    no_mkdir: bool = False  # smart moves: a missing folder raises DestDirMissing


class DestOutsideHome(ValueError):
    """A rule resolved to a destination outside ``$HOME``."""

    def __init__(self, dest: Path):
        super().__init__(f"destination outside home: {dest}")
        self.dest = dest


def plan_action(path: Path, rule: Rule, now: float) -> Action | None:
    """The *intended* destination. Collisions are resolved at move time."""
    if rule.action == "trash":
        return Action(src=path, dst=Path("~/.Trash").expanduser() / path.name, verb="TRASH")
    mtime = path.stat().st_mtime
    dest_dir = resolve_dest(rule.dest, mtime)
    if not within_home(dest_dir):
        raise DestOutsideHome(dest_dir)
    if rule.action == "move_and_timestamp":
        target = dest_dir / timestamp_name(mtime, path.suffix)
    else:
        target = dest_dir / path.name
    if target == path:
        return None
    return Action(src=path, dst=target)


def _log(log_path: Path, verb: str, src: Path, dst: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with open(log_path, "a") as fh:
        fh.write(f"{stamp}  {verb}  {src} -> {dst}\n")


def apply_action(action: Action, dry_run: bool, log_path: Path) -> Action:
    """Perform ``action``. Returns it with ``dst`` set to the path really used.

    A move whose source is byte-identical to an item already in the destination
    folder is not moved: the source goes to the Trash and ``DUPLICATE`` is logged
    with the existing item as ``dst`` (``Action.duplicate`` is set).
    """
    if action.verb != "TRASH":
        existing = dupes.find_identical(action.src, action.dst.parent)
        if existing is not None:
            if dry_run:
                _log(log_path, "DUPLICATE", action.src, existing)
                print(f"DUPLICATE  {action.src} -> {existing}")
            else:
                fsops.trash(action.src)
                _log(log_path, "DUPLICATE", action.src, existing)
            return Action(src=action.src, dst=existing, verb=action.verb, duplicate=True)
    if dry_run:
        preview = dedupe(action.dst)
        _log(log_path, "DRYRUN", action.src, preview)
        print(f"DRYRUN  {action.src} -> {preview}")
        return Action(src=action.src, dst=preview, verb=action.verb)
    if action.verb == "TRASH":
        final = fsops.trash(action.src)
    else:
        # File moves go through this module's move_no_clobber (patchable).
        is_dir = action.src.is_dir() and not action.src.is_symlink()
        if is_dir:
            final = fsops.move_dir_no_clobber(action.src, action.dst, mkdir=not action.no_mkdir)
        elif action.no_mkdir:
            final = move_no_clobber(action.src, action.dst, mkdir=False)
        else:
            final = move_no_clobber(action.src, action.dst)
    _log(log_path, action.verb, action.src, final)
    return Action(src=action.src, dst=final, verb=action.verb)


def default_log_path() -> Path:
    return Path("~/.local/state/mac-organizer/actions.log").expanduser()


def default_lock_path() -> Path:
    return Path("~/.local/state/mac-organizer/route.lock").expanduser()


def default_state_dir() -> Path:
    return Path("~/.local/state/mac-organizer").expanduser()


def acquire_lock(path: Path):
    """Take the single-instance lock. Returns an open fd, or None if held.

    The fd is deliberately kept open for the life of the process: the flock is
    released when the process exits, however it exits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


SUMMARY_CHARS = 300
UNAVAILABLE_STAMP = "unavailable-notified"
UNAVAILABLE_EVERY = 86400.0
BACKOFF_STAMP = "model-backoff"
BACKOFF_SECONDS = 86400.0


def _call(notifier, method: str, *args) -> None:
    """Notifications are best-effort: they never break routing."""
    try:
        getattr(notifier, method)(*args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR  notification {method} failed: {exc!r}", file=sys.stderr)


def _folders_sorted_from(rule: Rule) -> bool:
    """Folders are smart-sorted by the ~/Downloads rules only."""
    return rule.source.resolve() == (Path.home() / "Downloads").resolve()


@dataclass(frozen=True)
class _Pick:
    folder: str | None  # candidate name; None for the rule's fallback
    reason: str
    summary: str


class SmartRouter:
    """Decides where ``smart`` items go, starting the model at most once per run.

    A cached "unsure" verdict leaves the item in place without starting the
    model, and so does an unchanged item that failed ``memory.MAX_FAILURES``
    times in a row. Once the model is unavailable (disabled, missing, failed
    to start or died mid-run) every later item is treated as unsure and is not
    cached. A server that fails to start is not tried again for
    ``BACKOFF_SECONDS`` (live routing only). Disabled on purpose is not an
    outage: it is never flagged or notified. An outage is flagged
    (``batch.ai_unavailable``) and notified at most once a day.
    """

    def __init__(self, settings: SmartSettings, state: Path,
                 classifier_factory=classify.LlamaServer, notifier=None, read_only=False,
                 now: float | None = None):
        self.settings = settings
        self.read_only = read_only  # planning: never write the verdict cache
        self.state = Path(state)
        self.factory = classifier_factory
        self.notifier = notifier if notifier is not None else NullNotifier()
        self.batch = Batch()
        model = settings.model_path
        self.disabled = not settings.enabled
        self.backoff = not read_only and self._backoff_active(time.time() if now is None else now)
        self.permanent = (self.disabled or model is None or not model.is_file()
                          or self.backoff)
        self._down = self.permanent
        self.outage = False  # the model was needed but broken this run
        self._announced = False
        self._server = None
        self._ctx = None
        self._cands = None
        self._cache = None
        self._failures = None
        self._corrections = 0
        self._picks: dict[Path, _Pick] = {}

    def _backoff_active(self, now: float) -> bool:
        try:
            last = float((self.state / BACKOFF_STAMP).read_text().strip())
        except (OSError, ValueError):
            return False
        return now - last < BACKOFF_SECONDS

    def _start_failed(self, exc: BaseException) -> None:
        """The server did not come up: back off for a day (live routing only)."""
        if not self.read_only:
            try:
                self.state.mkdir(parents=True, exist_ok=True)
                (self.state / BACKOFF_STAMP).write_text(f"{time.time()}\n")
            except OSError:
                pass
        self._unavailable(exc)

    def candidates(self) -> list:
        if self._cands is None:
            self._cands = classify.candidates(self.settings.documents_root)
        return self._cands

    def _verdict_cache(self):
        if self._cache is None:
            self._cache = memory.VerdictCache(self.state, compact=not self.read_only)
            self._corrections = memory.corrections_count(self.state)
        return self._cache

    def failure_counter(self):
        if self._failures is None:
            self._failures = memory.FailureCounter(self.state, read_only=self.read_only)
        return self._failures

    def note_failure(self, path: Path) -> None:
        """One more failure for this unchanged item (best effort)."""
        try:
            st = path.stat()
            self.failure_counter().fail(path, st.st_size, st.st_mtime)
        except OSError:
            pass

    def leave_alone(self, path: Path, rule: Rule) -> bool:
        """True if routing ``path`` would certainly leave it in place without the model:
        a folder outside ~/Downloads, a cached "unsure", or an item that keeps failing.
        Cheap (a stat and in-memory lookups), so callers check it before costly work."""
        if path.is_dir() and not _folders_sorted_from(rule):
            return True
        st = path.stat()
        if self.failure_counter().blocked(path, st.st_size, st.st_mtime):
            return True
        return self._verdict_cache().get(*self._cache_key(path))

    def _cache_key(self, path: Path):
        st = path.stat()
        return (path, st.st_size, st.st_mtime,
                classify.candidates_hash(self.candidates()), self._corrections)

    def _unavailable(self, exc: BaseException) -> None:
        print(f"ERROR  smart sort unavailable: {exc}", file=sys.stderr)
        self._down = True
        self.outage = True
        self.close()

    def _ensure_server(self):
        if self._server is None and not self._down:
            try:
                ctx = self.factory(self.settings.model_path, state_dir=self.state)
                self._server = ctx.__enter__()
                self._ctx = ctx
            except Exception as exc:  # noqa: BLE001 - any start failure is an outage
                self._start_failed(exc)
                return None
            if not self._announced:  # "being sorted" only once the model is really up
                self._announced = True
                _call(self.notifier, "start")
        return self._server

    def _verdict(self, path: Path, sample: str):
        """A Verdict, or None if the model is unavailable for this item."""
        server = self._ensure_server()
        if server is None:
            return None
        examples = memory.select_examples(self.state, path.name)
        try:
            return server.classify(sample, self.candidates(), examples, self.settings.threshold)
        except classify.ServerUnavailable as exc:
            self._unavailable(exc)
            return None

    def route(self, path: Path, rule: Rule, now: float) -> Action | None:
        if self.leave_alone(path, rule):
            return None
        cache = self._verdict_cache()
        key = self._cache_key(path)
        if self._down:
            if not self.disabled:
                self.outage = True
            verdict, sample = None, ""
        else:
            sample = extract.sample(path)
            try:
                verdict = self._verdict(path, sample)
            except classify.ItemRejected as exc:
                # refused for this item only: unsure, never cached, not an outage
                print(f"ERROR  {path}: {exc}", file=sys.stderr)
                self.note_failure(path)
                return self.fallback(path, rule, "AI sorting could not read this item",
                                     sample[:SUMMARY_CHARS])
            if verdict is not None:
                self.failure_counter().clear(path)
        summary = sample[:SUMMARY_CHARS]
        if verdict is not None and verdict.folder is not None:
            cand = next((c for c in self.candidates() if c.name == verdict.folder), None)
            # never create a folder: the candidate must still exist right now
            if cand is not None and cand.path.is_dir() and within_home(cand.path):
                self._picks[path] = _Pick(cand.name, verdict.reason, summary)
                return Action(src=path, dst=cand.path / path.name, verb="SMART", no_mkdir=True)
        if verdict is None:
            reason = "AI sorting off" if self.disabled else "AI sorting unavailable"
        else:
            reason = verdict.reason
        if not rule.dest and verdict is not None and verdict.folder is None and not self.read_only:
            cache.put_unsure(*key)  # a genuine "unsure": never an outage or a vanished folder
        return self.fallback(path, rule, reason, summary)

    def fallback(self, path: Path, rule: Rule, reason: str, summary: str = "") -> Action | None:
        """The rule's fallback move, or leave the item in place. Never caches."""
        if rule.dest:
            dest_dir = resolve_dest(rule.dest, path.stat().st_mtime)
            if not within_home(dest_dir):
                raise DestOutsideHome(dest_dir)
            self._picks[path] = _Pick(None, reason, summary)
            return Action(src=path, dst=dest_dir / path.name)
        self._picks.pop(path, None)
        self.batch.add(path, path, None, "stay", reason, smart=True)
        return None

    def pick(self, path: Path) -> _Pick | None:
        """The decision behind the last action planned for ``path``."""
        return self._picks.get(path)

    def record(self, rule: Rule, done: Action, now: float) -> None:
        """Book a performed action into the batch (and placements for smart moves)."""
        if rule.action != "smart":
            self.batch.add(done.src, done.dst, None, "duplicate" if done.duplicate else "rule")
            return
        pick = self._picks.pop(done.src, _Pick(None, "", ""))
        if done.duplicate:
            self.batch.add(done.src, done.dst, pick.folder, "duplicate", pick.reason, smart=True)
        elif pick.folder is not None:
            memory.record_placement(self.state, done.dst, pick.folder, pick.summary, now)
            self.batch.add(done.src, done.dst, pick.folder, "smart", pick.reason, smart=True)
        else:
            self.batch.add(done.src, done.dst, None, "inbox", pick.reason, smart=True)

    def close(self) -> None:
        ctx, self._ctx, self._server = self._ctx, None, None
        if ctx is not None:
            ctx.__exit__(None, None, None)

    def finish(self, now: float) -> None:
        """End-of-run notification.

        A run that announced "Files are being sorted…" always ends with a
        summary. An outage is reported (flag in the summary, or a standalone
        notice) at most once a day.
        """
        report_outage = self.outage and self._unavailable_notice_due(now)
        self.batch.ai_unavailable = report_outage
        if self.batch.notable or self._announced:
            _call(self.notifier, "summary", self.batch)
        elif report_outage:
            _call(self.notifier, "unavailable")

    def _unavailable_notice_due(self, now: float) -> bool:
        stamp = self.state / UNAVAILABLE_STAMP
        try:
            last = float(stamp.read_text().strip())
        except (OSError, ValueError):
            last = None
        if last is not None and now - last < UNAVAILABLE_EVERY:
            return False
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(f"{now}\n")
        except OSError:
            pass
        return True


@dataclass(frozen=True)
class PlannedLine:
    """What routing decided for one item, before anything moves."""

    action: Action
    rule: Rule
    reason: str = ""
    folder: str | None = None  # candidate name for a smart decision


def _rule_reason(rule: Rule) -> str:
    return "type rule: trash" if rule.action == "trash" else f"type rule: {rule.dest}"


PREVIEW = "SMART?"


def plan_item(path: Path, config: Config, smart: SmartRouter | None, now: float, *,
              source: Path | None = None, on_error=None,
              preview: bool = False) -> PlannedLine | None:
    """Decide where ``path`` goes, moving nothing. None means it stays.

    ``source`` picks whose rules apply (default: the item's own folder). A
    failure while the smart sort decides is reported through ``on_error``
    and the item is treated as unsure (its rule's fallback, or it stays).
    Without a router, smart items stay; with ``preview`` they are returned as
    a ``SMART?`` action whose ``dst`` is the fallback (``path`` itself: stay).
    Shared by live routing and the backlog plan so they cannot drift apart.
    """
    source = (source or path.parent).resolve()
    rules = [r for r in config.rules if r.source.resolve() == source]
    if path.is_dir():
        if path.is_symlink():
            return None  # a link to a folder is never a folder item
        if not any(r.kind == "dir" for r in rules) or path.resolve() in own_dest_dirs(config.rules):
            return None
        # cheapest checks first: the settle walk runs only for a folder that
        # a rule matched and that is not going to be left alone anyway
        if path.name.startswith(".") or path.suffix.lower() in SKIP_SUFFIXES:
            return None
        rule = match_rule(path, rules, now)
        if rule is None:
            return None
        if rule.action == "smart" and smart is not None and smart.leave_alone(path, rule):
            return None
        if not dir_is_settled(path, now):
            return None
    elif not path.is_file():
        return None
    else:
        if should_skip(path, now):
            return None
        rule = match_rule(path, rules, now)
        if rule is None:
            return None
    if rule.action != "smart":
        action = plan_action(path, rule, now)
        return None if action is None else PlannedLine(action, rule, _rule_reason(rule))
    if smart is None:
        if not preview or (path.is_dir() and not _folders_sorted_from(rule)):
            return None  # without a router smart items are left in place
        dst = path
        if rule.dest:
            dest_dir = resolve_dest(rule.dest, path.stat().st_mtime)
            if not within_home(dest_dir):
                raise DestOutsideHome(dest_dir)
            dst = dest_dir / path.name
        return PlannedLine(Action(src=path, dst=dst, verb=PREVIEW), rule, "model decides")
    try:
        action = smart.route(path, rule, now)
    except DestOutsideHome:
        raise
    except Exception as exc:  # noqa: BLE001 - contained to this item
        (on_error or (lambda e: print(f"ERROR  {path}: {e!r}", file=sys.stderr)))(exc)
        smart.note_failure(path)  # an item that always fails is not retried forever
        action = smart.fallback(path, rule, "error while sorting")
    if action is None or (action.dst.name == path.name
                          and action.dst.parent.resolve() == path.parent.resolve()):
        return None  # the fallback is where it already is
    pick = smart.pick(path)
    return PlannedLine(action, rule, pick.reason if pick else "", pick.folder if pick else None)


def process_dir(
    directory: Path,
    rules: list[Rule],
    now: float,
    dry_run: bool,
    log_path: Path,
    smart: SmartRouter | None = None,
    keep: memory.KeepList | None = None,
) -> list[Action]:
    """Route every item in ``directory``. Items on ``keep`` (vetoed in a backlog
    report and unchanged since) are left alone."""
    config = Config(rules=rules, smart=smart.settings if smart is not None else None)
    done: list[Action] = []
    try:
        children = sorted(directory.iterdir())
    except OSError as exc:
        # Unreadable or removed between main()'s is_dir() and here: report it
        # and let the other target directories still be processed.
        print(f"ERROR  {directory}: {exc}", file=sys.stderr)
        return done
    for child in children:
        intended: Path | None = None
        rule: Rule | None = None
        try:
            if keep is not None and keep.holds(child):
                continue
            line = plan_item(child, config, smart, now, source=directory,
                             on_error=lambda exc: _item_error(child, exc, dry_run, log_path),
                             preview=dry_run and smart is None)
            if line is None:
                continue
            rule = line.rule
            if line.action.verb == PREVIEW:
                # a dry run never starts the model: say what it would decide
                fb = "stay" if line.action.dst == child else str(line.action.dst.parent)
                print(f"{PREVIEW} {child} (model decides; fallback {fb})")
                continue
            if rule.action == "smart":
                result = _apply_smart(child, line, dry_run, log_path, smart)
            else:
                intended = line.action.dst
                result = apply_action(line.action, dry_run, log_path)
            if result is None:
                continue
            done.append(result)
            if smart is not None:
                try:
                    smart.record(rule, result, now)
                except Exception as exc:  # noqa: BLE001 - bookkeeping never undoes a move
                    print(f"ERROR  recording {child}: {exc!r}", file=sys.stderr)
        except DestOutsideHome as exc:
            # Configuration error: it would repeat on every run, so it is
            # reported once per run to stderr and kept out of the action log
            # during a dry run (which must stay side-effect free to re-read).
            print(f"ERROR  {child} -> {exc.dest} (outside home)", file=sys.stderr)
            if not dry_run:
                _log(log_path, "ERROR", child, exc.dest)
        except OSError as exc:
            # One unreadable / vanished / locked file must not abort the pass:
            # the sweep is the safety net for everything else in this folder.
            print(f"ERROR  {child}: {exc}", file=sys.stderr)
            if not dry_run:
                _log(log_path, "ERROR", child, intended or Path("-"))
        except Exception as exc:  # noqa: BLE001
            if rule is None or rule.action != "smart":
                raise
            _item_error(child, exc, dry_run, log_path)  # left in place, nothing cached
            if smart is not None:
                smart.note_failure(child)
    return done


def _item_error(child: Path, exc: BaseException, dry_run: bool, log_path: Path) -> None:
    print(f"ERROR  {child}: {exc!r}", file=sys.stderr)
    if not dry_run:
        _log(log_path, "ERROR", child, Path("-"))


def _apply_smart(child: Path, line: PlannedLine, dry_run: bool, log_path: Path,
                 smart: SmartRouter) -> Action | None:
    """Perform a planned smart action; a chosen folder that vanished is never recreated."""
    action = line.action
    try:
        return apply_action(action, dry_run, log_path)
    except DestDirMissing:
        if not action.no_mkdir:
            raise
        # the chosen folder vanished before the move: never recreate it
        action = smart.fallback(child, line.rule, "chosen folder no longer exists")
        return apply_action(action, dry_run, log_path) if action is not None else None


def _raise_exit(signum, frame):
    raise SystemExit(128 + signum)


_SIGTERM_NOT_INSTALLED = object()


def _install_sigterm():
    """launchd stops a job with SIGTERM: turn it into SystemExit so cleanup runs.

    Returns the previous handler, or ``_SIGTERM_NOT_INSTALLED`` when not on the
    main thread (signal handlers can only be set there).
    """
    if threading.current_thread() is not threading.main_thread():
        return _SIGTERM_NOT_INSTALLED
    return signal.signal(signal.SIGTERM, _raise_exit)


def _restore_sigterm(previous) -> None:
    if previous is _SIGTERM_NOT_INSTALLED:
        return
    # None means the old handler was not set from Python: the default is closest
    signal.signal(signal.SIGTERM, signal.SIG_DFL if previous is None else previous)


def main(argv: list[str] | None = None, *, classifier_factory=None, notifier=None) -> int:
    parser = argparse.ArgumentParser(description="Route files in drop-zone folders.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(__file__).resolve().parent / "rules.toml",
    )
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--lock", type=Path, default=None)
    parser.add_argument("--backlog", action="store_true",
                        help="plan a one-off clean-up of PATHS into a report (moves nothing)")
    parser.add_argument("--apply", type=Path, default=None, metavar="REPORT",
                        help="with --backlog: perform the lines left in REPORT")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)
    if args.apply is not None and not args.backlog:
        parser.error("--apply works only with --backlog")
    if args.backlog and args.apply is not None and args.paths:
        parser.error("--backlog --apply takes only the report")
    if args.backlog and args.apply is None and not args.paths:
        parser.error("--backlog needs the folders to plan, e.g. ~/Documents/Inbox ~/Downloads")

    log_path = args.log or default_log_path()
    lock_path = args.lock or default_lock_path()

    # Several launchd agents can fire at once (a WatchPaths agent, the sweep,
    # and the moves themselves re-trigger watchers). A second concurrent pass
    # is redundant by construction, and launchd will fire again later.
    lock_fd = acquire_lock(lock_path)
    if lock_fd is None:
        if args.backlog:
            print("another route.py run is already running; try the backlog again shortly",
                  file=sys.stderr)
            return 1
        print("another route.py run is already running; skipping this pass")
        return 0

    previous_sigterm = _install_sigterm()
    try:
        try:
            config = load_config(args.rules, args.rules.parent / "rules.local.toml")
        except FileNotFoundError:
            print(
                f"rules file not found at {args.rules} — run ./install.sh first",
                file=sys.stderr,
            )
            return 1
        except ValueError as exc:  # a bad rule, or TOML that does not parse
            print(f"rules not loaded, nothing moved: {exc}", file=sys.stderr)
            return 1
        rules = config.rules
        if not args.dry_run and config.smart.model_path is not None:
            try:  # a server orphaned by a SIGKILLed run never outlives it by long
                classify.reap_stale(default_state_dir(), config.smart.model_path)
            except Exception as exc:  # noqa: BLE001 - never blocks routing
                print(f"ERROR  reaping a stale llama-server: {exc!r}", file=sys.stderr)
        if args.apply is not None:
            return _backlog_apply(args.apply, log_path, config)
        if args.backlog:
            return _backlog_plan(args.paths, config, classifier_factory)

        # stderr only: the action log records actions, one line per action.
        for warning in cycle_warnings(rules):
            print(warning, file=sys.stderr)

        targets = args.paths or sources(rules)
        now = time.time()
        keep = memory.KeepList(default_state_dir())  # read only here
        router = None
        if not args.dry_run:
            # A dry run never starts the model, writes state or notifies.
            state = default_state_dir()
            router = SmartRouter(config.smart, state,
                                 classifier_factory or classify.LlamaServer,
                                 notifier if notifier is not None else notify.Notifier(state))
            try:
                memory.detect_corrections(state, router.candidates(), now)
            except Exception as exc:  # noqa: BLE001 - learning never blocks routing
                print(f"ERROR  detecting corrections: {exc!r}", file=sys.stderr)
        try:
            for target in targets:
                directory = target.expanduser()
                if directory.is_dir():
                    process_dir(directory, rules, now, args.dry_run, log_path, smart=router,
                                keep=keep)
        finally:
            if router is not None:
                router.close()  # the model is never left running
        if router is not None:
            router.finish(now)
        if not args.dry_run:
            try:
                disks.eject_stale(default_state_dir(), now,
                                  log=lambda verb, src, dst: _log(log_path, verb, src, dst))
            except Exception as exc:  # noqa: BLE001 - ejecting never fails the run
                print(f"ERROR  ejecting disk images: {exc!r}", file=sys.stderr)
        return 0
    finally:
        _restore_sigterm(previous_sigterm)
        os.close(lock_fd)  # releases the flock


def _backlog_plan(paths: list[Path], config: Config, classifier_factory) -> int:
    """Write the backlog report. Moves nothing, records nothing, never notifies."""
    import backlog

    router = SmartRouter(config.smart, default_state_dir(),
                         classifier_factory or classify.LlamaServer, NullNotifier(),
                         read_only=True)
    try:
        backlog.plan(paths, config, router, time.time())
    finally:
        router.close()  # the model is never left running
    return 0


def _backlog_apply(report: Path, log_path: Path, config: Config) -> int:
    import backlog

    try:
        backlog.read_report(report)
    except (OSError, ValueError) as exc:  # unreadable or invalid report
        print(f"report not applied, nothing moved: {exc}", file=sys.stderr)
        return 2
    backlog.apply(report, log_path, config)  # each line's failure is contained and counted
    return 0


if __name__ == "__main__":
    # backlog.py imports this module as ``route``: share this instance with it
    sys.modules.setdefault("route", sys.modules[__name__])
    raise SystemExit(main())
