"""Automatic file router for macOS drop-zone folders."""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import dedupe as dupes
import extract
import fsops
from fsops import candidate_names, move_no_clobber  # noqa: F401  (re-exported)


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


def _parse_rules(data: dict) -> list[Rule]:
    rules: list[Rule] = []
    for entry in data.get("rule", []):
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
DIR_WALK_LIMIT = 20_000
SKIP_SUFFIXES = frozenset({".crdownload", ".part", ".download", ".tmp"})


def _age_seconds(path: Path, now: float) -> float:
    return now - path.stat().st_mtime


def dir_is_settled(path: Path, now: float, quiet_seconds: int = DIR_QUIET_SECONDS) -> bool:
    """True if nothing inside ``path`` (nor ``path``) changed in ``quiet_seconds``.

    The walk is capped at 20,000 entries; past that only the directory's own
    mtime is trusted.
    """
    newest = path.stat().st_mtime
    seen = 0
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            seen += 1
            if seen > DIR_WALK_LIMIT:
                return now - path.stat().st_mtime >= quiet_seconds
            try:
                newest = max(newest, os.lstat(os.path.join(root, name)).st_mtime)
            except OSError:
                continue  # vanished mid-walk: something is still changing
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
        if not path.is_dir():
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
            final = fsops.move_dir_no_clobber(action.src, action.dst)
        else:
            final = move_no_clobber(action.src, action.dst)
    _log(log_path, action.verb, action.src, final)
    return Action(src=action.src, dst=final, verb=action.verb)


def default_log_path() -> Path:
    return Path("~/.local/state/mac-organizer/actions.log").expanduser()


def default_lock_path() -> Path:
    return Path("~/.local/state/mac-organizer/route.lock").expanduser()


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


def process_dir(
    directory: Path,
    rules: list[Rule],
    now: float,
    dry_run: bool,
    log_path: Path,
) -> list[Action]:
    dir_rules = [r for r in rules if r.source.resolve() == directory.resolve()]
    has_dir_rules = any(r.kind == "dir" for r in dir_rules)
    skip_dirs = own_dest_dirs(rules)
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
        try:
            if child.is_dir():
                if not has_dir_rules or child.resolve() in skip_dirs:
                    continue
            elif not child.is_file():
                continue
            if should_skip(child, now):
                continue
            rule = match_rule(child, dir_rules, now)
            if rule is None or rule.action == "smart":
                continue  # smart sorting arrives in a later task
            action = plan_action(child, rule, now)
            if action is None:
                continue
            intended = action.dst
            done.append(apply_action(action, dry_run, log_path))
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
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route files in drop-zone folders.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(__file__).resolve().parent / "rules.toml",
    )
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--lock", type=Path, default=None)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)

    log_path = args.log or default_log_path()
    lock_path = args.lock or default_lock_path()

    # Several launchd agents can fire at once (a WatchPaths agent, the sweep,
    # and the moves themselves re-trigger watchers). A second concurrent pass
    # is redundant by construction, and launchd will fire again later.
    lock_fd = acquire_lock(lock_path)
    if lock_fd is None:
        print("another route.py run is already running; skipping this pass")
        return 0

    try:
        try:
            rules = load_config(args.rules, args.rules.parent / "rules.local.toml").rules
        except FileNotFoundError:
            print(
                f"rules file not found at {args.rules} — run ./install.sh first",
                file=sys.stderr,
            )
            return 1

        # stderr only: the action log records actions, one line per action.
        for warning in cycle_warnings(rules):
            print(warning, file=sys.stderr)

        targets = args.paths or sources(rules)
        now = time.time()
        for target in targets:
            directory = target.expanduser()
            if directory.is_dir():
                process_dir(directory, rules, now, args.dry_run, log_path)
        return 0
    finally:
        os.close(lock_fd)  # releases the flock


if __name__ == "__main__":
    raise SystemExit(main())
