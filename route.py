"""Automatic file router for macOS drop-zone folders."""

from __future__ import annotations

import argparse
import fnmatch
import shutil
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


def resolve_dest(dest: str, mtime: float) -> Path:
    expanded = datetime.fromtimestamp(mtime).strftime(dest)
    return Path(expanded).expanduser()


def within_home(path: Path) -> bool:
    home = Path.home().resolve()
    resolved = path.resolve()
    return resolved == home or home in resolved.parents


def timestamp_name(mtime: float, suffix: str) -> str:
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d_%H-%M-%S") + suffix


def dedupe(path: Path) -> Path:
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem} -{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


@dataclass(frozen=True)
class Action:
    src: Path
    dst: Path


def plan_action(path: Path, rule: Rule, now: float) -> Action | None:
    mtime = path.stat().st_mtime
    dest_dir = resolve_dest(rule.dest, mtime)
    if not within_home(dest_dir):
        raise ValueError(f"destination outside home: {dest_dir}")
    if rule.action == "move_and_timestamp":
        target = dest_dir / timestamp_name(mtime, path.suffix)
    else:
        target = dest_dir / path.name
    if target == path:
        return None
    return Action(src=path, dst=dedupe(target))


def _log(log_path: Path, verb: str, src: Path, dst: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with open(log_path, "a") as fh:
        fh.write(f"{stamp}  {verb}  {src} -> {dst}\n")


def apply_action(action: Action, dry_run: bool, log_path: Path) -> None:
    if dry_run:
        _log(log_path, "DRYRUN", action.src, action.dst)
        print(f"DRYRUN  {action.src} -> {action.dst}")
        return
    action.dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(action.src), str(action.dst))
    _log(log_path, "MOVE", action.src, action.dst)


DEFAULT_LOG = Path("~/.local/state/mac-organizer/actions.log").expanduser()


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
        if not child.is_file():
            continue
        if should_skip(child, now):
            continue
        rule = match_rule(child, dir_rules, now)
        if rule is None:
            continue
        try:
            action = plan_action(child, rule, now)
        except ValueError as exc:
            _log(log_path, "ERROR", child, Path(str(exc)))
            continue
        if action is None:
            continue
        apply_action(action, dry_run, log_path)
        done.append(action)
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Route files in drop-zone folders.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(__file__).resolve().parent / "rules.toml",
    )
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)

    rules = load_rules(args.rules)
    targets = args.paths or sources(rules)
    now = time.time()
    for target in targets:
        directory = target.expanduser()
        if directory.is_dir():
            process_dir(directory, rules, now, args.dry_run, args.log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
