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
    gone: int = 0  # listed files (shown in the dialog) that are no longer in Misc
    bad_name: bool = False  # the error is about the typed name: ask for another one


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


def _expire(data: dict, now: float, settings) -> bool:
    """Move suggestions unanswered for ``expire_days`` to snoozed; True if any moved.

    The snooze counts from when the suggestion expired, not from when this
    runs, so an ignored topic comes back on schedule however late it is seen.
    """
    expired = [k for k, v in data["pending"].items()
               if now - v["sent_at"] > settings.expire_days * DAY]
    for k in expired:
        sent_at = data["pending"].pop(k)["sent_at"]
        data["snoozed"][k] = sent_at + (settings.expire_days + settings.snooze_days) * DAY
    return bool(expired)


def _load_live(state, now, settings, write: bool) -> dict:
    data = _load(state)
    if _expire(data, now, settings) and write:
        _save(state, data)
    return data


def due(state, misc_dir, documents_root, expand, settings, now,
        write: bool = True, ignore_daily: bool = False) -> Suggestion | None:
    """The topic to offer now, if any. Expires ignored suggestions first (``write``: saved).

    ``ignore_daily``: the user asked (opened the app), so the once-a-day limit on
    unprompted notifications does not apply.
    """
    data = _load_live(state, now, settings, write)
    if not ignore_daily and data["last_sent"] is not None and now - data["last_sent"] < DAY:
        return None
    never = _never(state)
    best = None
    for key, (name, items) in topics.clusters(state, misc_dir).items():
        if len(items) < settings.min_items or key in never or key in data["pending"]:
            continue
        if data["snoozed"].get(key, 0) > now:
            continue
        name, err, similar = check_name(name, documents_root, expand)
        if err or similar:  # not a folder that could be created, or one already exists
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


def pending(state, now, settings, write: bool = True) -> list[Suggestion]:
    data = _load_live(state, now, settings, write)
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


def _remaining(state, key, misc_dir) -> tuple[list[Path], int]:
    """(items to move, how many listed items are gone).

    Only the files the user was shown (saved when the suggestion was sent)
    that are still in this topic in Misc: a file that arrived later is not moved.
    """
    listed = _load(state)["pending"][key]["items"]
    got = topics.clusters(state, misc_dir).get(key)
    current = {p for p in got[1] if p.exists()} if got else set()
    keep = [Path(p) for p in listed if Path(p) in current]
    return keep, len(listed) - len(keep)


def _move_all(items, dest: Path, log) -> tuple[list[Path], str | None]:
    moved, err = [], None
    for src in items:
        try:  # mkdir=False: a folder that vanished meanwhile is never recreated
            used = fsops.move_item(src, dest / src.name, mkdir=False)
        except OSError as exc:
            err = f"could not move {src.name}: {exc}"
            continue
        moved.append(used)
        log("SUGGEST", src, used)
    return moved, err


def _inside(path: Path, root: Path) -> bool:
    """``path`` resolves inside the resolved ``root`` and inside $HOME (symlinks followed)."""
    import route  # route imports this module; resolved at call time

    try:
        resolved, top = Path(path).resolve(), Path(root).resolve()
    except OSError:
        return False
    return (resolved == top or top in resolved.parents) and route.within_home(Path(path))


def check_name(name, documents_root, expand) -> tuple[str, str | None, str | None]:
    """(stripped name, error, similar existing folder) for a user-typed folder name.

    Shared by ``accept`` and the backlog's NEWFOLDER lines so both refuse the same names.
    """
    name = (name or "").strip()
    if ":" in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return name, "The folder name cannot contain \":\" or control characters.", None
    err = topics.valid_folder_name(name, documents_root)
    if err:
        return name, err, None
    return name, None, topics.near_duplicate(name, documents_root, tuple(expand))


def accept(state, key, name, misc_dir, documents_root, expand, log) -> AcceptResult:
    if key not in _load(state)["pending"]:
        return AcceptResult(error="no such suggestion")
    items, gone = _remaining(state, key, misc_dir)
    if not items:
        _drop(state, key)
        return AcceptResult(error="those files are no longer in Misc", gone=gone)
    name, err, similar = check_name(name, documents_root, expand)
    if err:
        return AcceptResult(error=err, bad_name=True)
    if similar:
        return AcceptResult(similar=similar)
    root = Path(documents_root)
    if not root.is_dir() or not _inside(root, root):
        return AcceptResult(error="the Documents folder is not inside your home folder")
    folder = root / name
    try:
        folder.mkdir()
    except FileExistsError:
        return AcceptResult(error=f"A folder named \"{name}\" already exists.", bad_name=True)
    except OSError as exc:
        return AcceptResult(error=f"Could not create \"{name}\": {exc}", bad_name=True)
    log("NEWFOLDER", Path(misc_dir), folder)
    moved, err = _move_all(items, folder, log)
    if moved:  # nothing moved: still pending, so it can be answered again
        _drop(state, key)
    return AcceptResult(created=folder, moved=moved, error=err, gone=gone)


def accept_into_existing(state, key, existing, misc_dir, documents_root, log) -> AcceptResult:
    if key not in _load(state)["pending"]:
        return AcceptResult(error="no such suggestion")
    items, gone = _remaining(state, key, misc_dir)
    if not items:
        _drop(state, key)
        return AcceptResult(error="those files are no longer in Misc", gone=gone)
    root = Path(documents_root)
    parts = (existing or "").split("/")
    if not existing or any(p in ("", ".", "..") or p.startswith(".") for p in parts) or len(parts) > 2:
        return AcceptResult(error="that folder is not valid")
    dest = root.joinpath(*parts)
    if dest.is_symlink() or not dest.is_dir():
        return AcceptResult(error="that folder no longer exists")
    if not _inside(dest, root):  # e.g. a symlinked Documents/Academics pointing elsewhere
        return AcceptResult(error="that folder is not inside Documents in your home folder")
    moved, err = _move_all(items, dest, log)
    if moved:
        _drop(state, key)
    return AcceptResult(moved=moved, error=err, gone=gone)
