"""Automatic file router for macOS drop-zone folders."""

from __future__ import annotations

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
