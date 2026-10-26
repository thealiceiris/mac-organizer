"""Suggest-and-ask folders: state, due, accept, decline, expire.

A folder is created only by ``accept`` (the user confirmed a name).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import fsops
import topics

SUGGESTIONS = "suggestions.json"
NEVER = "never.json"
DAY = 86400.0
Log = Callable[[str, object, object], None]


@dataclass
class Suggestion:
    key: str
    name: str
    items: list[str]
    sent_at: float | None = None


@dataclass
class AcceptResult:
    created: Path | None = None
    moved: list[Path] = field(default_factory=list)
    error: str | None = None
    similar: str | None = None


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _write(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load(state: Path) -> dict:
    """{"pending": {key: {name, items, sent_at}}, "snoozed": {key: until}, "last_sent": ts|None}."""
    raw = _read(Path(state) / SUGGESTIONS)
    raw = raw if isinstance(raw, dict) else {}
    pend = {}
    for k, v in (raw.get("pending") or {}).items() if isinstance(raw.get("pending"), dict) else []:
        if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("name"), str) \
                and isinstance(v.get("sent_at"), (int, float)):
            items = v.get("items")
            pend[k] = {"name": v["name"], "sent_at": float(v["sent_at"]),
                       "items": [i for i in items if isinstance(i, str)] if isinstance(items, list) else []}
    snoozed = {k: float(v) for k, v in (raw.get("snoozed") or {}).items()
               if isinstance(raw.get("snoozed"), dict) and isinstance(k, str)
               and isinstance(v, (int, float))} if isinstance(raw.get("snoozed"), dict) else {}
    last = raw.get("last_sent")
    return {"pending": pend, "snoozed": snoozed,
            "last_sent": float(last) if isinstance(last, (int, float)) else None}


def _save(state: Path, data: dict) -> None:
    _write(Path(state) / SUGGESTIONS, data)


def _never(state: Path) -> set[str]:
    raw = _read(Path(state) / NEVER)
    return {k for k in raw if isinstance(k, str)} if isinstance(raw, list) else set()


def due(state, misc_dir, documents_root, expand, settings, now) -> Suggestion | None:
    data = _load(state)
    if data["last_sent"] is not None and now - data["last_sent"] < DAY:
        return None
    never = _never(state)
    best = None
    for key, (name, items) in topics.clusters(state, misc_dir).items():
        if len(items) < settings.min_items or key in never or key in data["pending"]:
            continue
        if data["snoozed"].get(key, 0) > now:
            continue
        if topics.near_duplicate(name, documents_root, tuple(expand)):
            continue
        if best is None or len(items) > len(best[1]) or (len(items) == len(best[1]) and key < best[0]):
            best = (key, items, name)
    if best is None:
        return None
    return Suggestion(best[0], best[2], [str(p) for p in best[1]], None)


def mark_sent(state, s: Suggestion, now) -> None:
    data = _load(state)
    data["pending"][s.key] = {"name": s.name, "items": list(s.items), "sent_at": float(now)}
    data["last_sent"] = float(now)
    _save(state, data)


def pending(state, now, settings) -> list[Suggestion]:
    data = _load(state)
    expired = [k for k, v in data["pending"].items() if now - v["sent_at"] > settings.expire_days * DAY]
    for k in expired:
        del data["pending"][k]
        data["snoozed"][k] = now + settings.snooze_days * DAY
    if expired:
        _save(state, data)
    return [Suggestion(k, v["name"], v["items"], v["sent_at"]) for k, v in data["pending"].items()]


def decline(state, key, now, settings, never: bool) -> None:
    data = _load(state)
    data["pending"].pop(key, None)
    if never:
        keys = _never(state) | {key}
        _write(Path(state) / NEVER, sorted(keys))
    else:
        data["snoozed"][key] = now + settings.snooze_days * DAY
    _save(state, data)


def _drop(state, key) -> None:
    data = _load(state)
    if data["pending"].pop(key, None) is not None:
        _save(state, data)


def _remaining(state, key, misc_dir) -> list[Path]:
    got = topics.clusters(state, misc_dir).get(key)
    return [p for p in got[1] if p.exists()] if got else []


def _move_all(items, dest: Path, log) -> tuple[list[Path], str | None]:
    moved, err = [], None
    for src in items:
        try:
            used = fsops.move_item(src, dest / src.name)
        except OSError as exc:
            err = f"could not move {src.name}: {exc}"
            continue
        moved.append(used)
        log("SUGGEST", src, used)
    return moved, err


def accept(state, key, name, misc_dir, documents_root, expand, log) -> AcceptResult:
    if key not in _load(state)["pending"]:
        return AcceptResult(error="no such suggestion")
    items = _remaining(state, key, misc_dir)
    if not items:
        _drop(state, key)
        return AcceptResult(error="those files are no longer in Misc")
    name = (name or "").strip()
    if ":" in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return AcceptResult(error="The folder name cannot contain \":\" or control characters.")
    err = topics.valid_folder_name(name, documents_root)
    if err:
        return AcceptResult(error=err)
    similar = topics.near_duplicate(name, documents_root, tuple(expand))
    if similar:
        return AcceptResult(similar=similar)
    folder = Path(documents_root) / name
    try:
        folder.mkdir()
    except FileExistsError:
        return AcceptResult(error=f"A folder named \"{name}\" already exists.")
    except OSError as exc:
        return AcceptResult(error=f"Could not create \"{name}\": {exc}")
    log("NEWFOLDER", Path(misc_dir), folder)
    moved, err = _move_all(items, folder, log)
    _drop(state, key)
    return AcceptResult(created=folder, moved=moved, error=err)


def accept_into_existing(state, key, existing, misc_dir, documents_root, log) -> AcceptResult:
    if key not in _load(state)["pending"]:
        return AcceptResult(error="no such suggestion")
    items = _remaining(state, key, misc_dir)
    if not items:
        _drop(state, key)
        return AcceptResult(error="those files are no longer in Misc")
    root = Path(documents_root)
    parts = (existing or "").split("/")
    if not existing or any(p in ("", ".", "..") or p.startswith(".") for p in parts) or len(parts) > 2:
        return AcceptResult(error="that folder is not valid")
    dest = root.joinpath(*parts)
    if dest.is_symlink() or not dest.is_dir():
        return AcceptResult(error="that folder no longer exists")
    moved, err = _move_all(items, dest, log)
    _drop(state, key)
    return AcceptResult(moved=moved, error=err)
