"""Automatic file router for macOS drop-zone folders."""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import os
import shutil
import sys
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    source: Path
    action: str
    dest: str = ""
    ext: frozenset[str] = frozenset()
    glob: str | None = None
    min_age_seconds: int = 0


def _norm_ext(values) -> frozenset[str]:
    return frozenset(str(v).lower().lstrip(".") for v in values)


def load_rules(path: Path) -> list[Rule]:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    rules: list[Rule] = []
    for entry in data.get("rule", []):
        rules.append(
            Rule(
                source=Path(entry["source"]).expanduser(),
                action=entry["action"],
                dest=entry.get("dest", ""),
                ext=_norm_ext(entry.get("ext", [])),
                glob=entry.get("glob"),
                min_age_seconds=int(entry.get("min_age_seconds", 0)),
            )
        )
    return rules


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
        if rule.action == "skip":
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


MIN_AGE_SECONDS = 60
SKIP_SUFFIXES = frozenset({".crdownload", ".part", ".download", ".tmp"})


def _age_seconds(path: Path, now: float) -> float:
    return now - path.stat().st_mtime


def should_skip(path: Path, now: float) -> bool:
    if path.name.startswith("."):
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    return _age_seconds(path, now) < MIN_AGE_SECONDS


def rule_matches(rule: Rule, path: Path, now: float) -> bool:
    if rule.ext and path.suffix.lower().lstrip(".") not in rule.ext:
        return False
    if rule.glob and not fnmatch.fnmatch(path.name, rule.glob):
        return False
    if rule.min_age_seconds and _age_seconds(path, now) < rule.min_age_seconds:
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


def candidate_names(dst: Path):
    """``dst``, then ``dst -2``, ``dst -3``, … forever."""
    yield dst
    counter = 2
    while True:
        yield dst.with_name(f"{dst.stem} -{counter}{dst.suffix}")
        counter += 1


def dedupe(path: Path) -> Path:
    """First free name for ``path``. Advisory only — racy by nature.

    Used for the ``--dry-run`` preview. Real moves go through
    :func:`move_no_clobber`, which claims the name atomically.
    """
    for candidate in candidate_names(path):
        if not candidate.exists():
            return candidate


def move_no_clobber(src: Path, dst: Path) -> Path:
    """Move ``src`` to ``dst``, never overwriting. Returns the path used.

    The destination name is claimed atomically (``os.link``, or an ``O_EXCL``
    create on the cross-device fallback path), so concurrent routers can never
    settle on the same name and clobber each other. ``src`` is unlinked only
    after its content is safely at the claimed name.
    """
    for candidate in candidate_names(dst):
        candidate.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, candidate)
        except FileExistsError:
            continue
        except FileNotFoundError:
            raise  # src vanished under us; caller logs and moves on
        except OSError:
            # Cross-device (EXDEV) or a filesystem without hard links:
            # claim the name with an exclusive create, then copy.
            try:
                fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                continue
            os.close(fd)
            try:
                shutil.copy2(src, candidate)
            except BaseException:
                os.unlink(candidate)  # never leave an empty claim behind
                raise
            os.unlink(src)
            return candidate
        os.unlink(src)
        return candidate


@dataclass(frozen=True)
class Action:
    src: Path
    dst: Path


class DestOutsideHome(ValueError):
    """A rule resolved to a destination outside ``$HOME``."""

    def __init__(self, dest: Path):
        super().__init__(f"destination outside home: {dest}")
        self.dest = dest


def plan_action(path: Path, rule: Rule, now: float) -> Action | None:
    """The *intended* destination. Collisions are resolved at move time."""
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
    """Perform ``action``. Returns it with ``dst`` set to the path really used."""
    if dry_run:
        preview = dedupe(action.dst)
        _log(log_path, "DRYRUN", action.src, preview)
        print(f"DRYRUN  {action.src} -> {preview}")
        return Action(src=action.src, dst=preview)
    final = move_no_clobber(action.src, action.dst)
    _log(log_path, "MOVE", action.src, final)
    return Action(src=action.src, dst=final)


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
    done: list[Action] = []
    for child in sorted(directory.iterdir()):
        intended: Path | None = None
        try:
            if not child.is_file():
                continue
            if should_skip(child, now):
                continue
            rule = match_rule(child, dir_rules, now)
            if rule is None:
                continue
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
            rules = load_rules(args.rules)
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
