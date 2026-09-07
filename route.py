"""Automatic file router for macOS drop-zone folders."""

from __future__ import annotations

import fnmatch
import time
import tomllib
from dataclasses import dataclass
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
