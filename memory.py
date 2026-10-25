"""Learning memory: placements, Finder corrections, and cached "unsure" verdicts.

All state is append-only JSONL under the state dir. Corrupt or partial lines are
skipped, never fatal. Nothing here touches the network.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

WINDOW = 30 * 86400.0
MISSING_GRACE = 24 * 3600.0  # Spotlight may lag behind a fresh Finder move
LOCATE_TIMEOUT = 20.0
PLACEMENTS = "placements.jsonl"
CORRECTIONS = "corrections.jsonl"
VERDICTS = "verdicts.jsonl"
FAILURES = "failures.jsonl"
MAX_FAILURES = 3


def _read(path: Path) -> list[dict]:
    rows = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


@contextlib.contextmanager
def _locked(path: Path):
    """Exclusive lock on a sibling lock file; held only for short read-modify-write steps."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _sig(row: dict) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False)


def _append(path: Path, row: dict) -> None:
    with _locked(path):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rewrite(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    os.replace(tmp, path)


def mdfind_locate(name: str) -> list[Path]:
    """Spotlight lookup under ~/Documents; any failure yields no results."""
    try:
        out = subprocess.run(
            ["mdfind", "-onlyin", str(Path.home() / "Documents"), "-name", name],
            capture_output=True, text=True, timeout=LOCATE_TIMEOUT, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [Path(line) for line in out.splitlines() if line.strip()]


def record_placement(state: Path, dst: Path, folder: str, summary: str, now: float) -> None:
    try:
        st = os.lstat(dst)
    except OSError:
        return
    _append(Path(state) / PLACEMENTS, {
        "path": str(dst), "file": Path(dst).name, "folder": folder, "summary": summary,
        "inode": st.st_ino, "size": st.st_size, "at": now,
    })


def _owner(path: Path, cands) -> str | None:
    """Name of the deepest candidate whose path is an ancestor of `path`."""
    best, depth = None, -1
    for c in cands:
        cp = Path(c.path)
        if cp in path.parents and len(cp.parts) > depth:
            best, depth = c.name, len(cp.parts)
    return best


def _find(row: dict, locate) -> Path | None:
    try:
        found = locate(row["file"])
    except Exception:
        return None
    for p in found:
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if st.st_ino == row.get("inode") and st.st_size == row.get("size"):
            return Path(p)
    return None


def detect_corrections(state: Path, cands, now: float, locate=mdfind_locate) -> list[dict]:
    state = Path(state)
    pfile = state / PLACEMENTS
    if not pfile.exists():
        return []
    keep, new = [], []
    snapshot = _read(pfile)
    seen = {_sig(r) for r in snapshot}  # before rows are mutated below
    for row in snapshot:
        try:
            at, path, file = float(row["at"]), Path(row["path"]), row["file"]
            folder = row["folder"]
        except (KeyError, TypeError, ValueError):
            continue
        if now - at > WINDOW:
            continue
        if os.path.lexists(path):
            row.pop("missing_since", None)
            keep.append(row)  # still where we put it
            continue
        now_at = _find(row, locate)
        if now_at is None:
            # Not found: maybe not indexed yet. Resolve only after a grace period.
            since = row.get("missing_since")
            if not isinstance(since, (int, float)):
                row["missing_since"] = now
                keep.append(row)
            elif now - since < MISSING_GRACE:
                keep.append(row)
            continue
        if now_at is not None:
            right = _owner(now_at, cands)
            if right is not None and right != folder:
                new.append({"file": file, "summary": row.get("summary", ""),
                            "wrong": folder, "right": right, "at": now})
        # found elsewhere, moved outside Documents, or deleted: resolved
    for c in new:
        _append(state / CORRECTIONS, c)
    with _locked(pfile):
        # rows appended by record_placement while we were locating survive
        keep.extend(r for r in _read(pfile) if _sig(r) not in seen)
        _rewrite(pfile, keep)
    return new


def corrections_count(state: Path) -> int:
    return len(_read(Path(state) / CORRECTIONS))


def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", s.lower()) if len(t) > 1}


def select_examples(state: Path, name: str, k: int = 10) -> list[dict]:
    want = _tokens(name)
    rows = [r for r in _read(Path(state) / CORRECTIONS)
            if all(isinstance(r.get(f), str) for f in ("file", "wrong", "right"))]
    scored = []
    for i, r in enumerate(rows):
        have = _tokens(r["file"]) | _tokens(r["right"]) | _tokens(r["wrong"])
        scored.append((len(want & have), r.get("at", 0) if isinstance(r.get("at"), (int, float)) else 0, i, r))
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [{"file": r["file"], "summary": r.get("summary", ""), "wrong": r["wrong"], "right": r["right"]}
            for *_, r in scored[:k]]


class VerdictCache:
    """Remembers "unsure" verdicts so unchanged items are not re-asked.

    One entry per path (the newest); entries older than 30 days are dropped.
    """

    def __init__(self, state: Path, compact: bool = True):
        self.file = Path(state) / VERDICTS
        cutoff = time.time() - WINDOW
        self.rows: dict[str, dict] = {}
        rows = _read(self.file)
        for r in rows:
            if isinstance(r.get("key"), str) and isinstance(r.get("path"), str) \
                    and isinstance(r.get("at"), (int, float)) and r["at"] >= cutoff:
                self.rows[r["path"]] = r  # later lines win
        if compact and self.file.exists() and len(self.rows) != len(rows):
            self._save()

    def _save(self) -> None:
        with _locked(self.file):
            _rewrite(self.file, list(self.rows.values()))

    @staticmethod
    def _key(path, size, mtime, cands_hash, corrections) -> str:
        blob = json.dumps([str(path), size, mtime, cands_hash, corrections])
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, path, size, mtime, cands_hash, corrections_count) -> bool:
        r = self.rows.get(str(path))
        return r is not None and r["key"] == self._key(path, size, mtime, cands_hash, corrections_count)

    def put_unsure(self, path, size, mtime, cands_hash, corrections_count) -> None:
        key = self._key(path, size, mtime, cands_hash, corrections_count)
        if self.get(path, size, mtime, cands_hash, corrections_count):
            return
        self.rows[str(path)] = {"key": key, "path": str(path), "verdict": "unsure", "at": time.time()}
        self._save()


class FailureCounter:
    """Consecutive smart-sort failures per unchanged item, keyed by (path, size, mtime).

    After ``MAX_FAILURES`` failures in a row the item is left alone, like a
    cached "unsure", until it changes (or a verdict succeeds and clears it).
    Entries older than 30 days are dropped. ``read_only`` never writes.
    """

    def __init__(self, state: Path, read_only: bool = False):
        self.file = Path(state) / FAILURES
        self.read_only = read_only
        cutoff = time.time() - WINDOW
        self.rows: dict[str, dict] = {}
        for r in _read(self.file):
            if isinstance(r.get("path"), str) and isinstance(r.get("count"), int) \
                    and isinstance(r.get("at"), (int, float)) and r["at"] >= cutoff:
                self.rows[r["path"]] = r

    def _row(self, path, size, mtime) -> dict | None:
        r = self.rows.get(str(path))
        if r is not None and r.get("size") == size and r.get("mtime") == mtime:
            return r
        return None

    def count(self, path, size, mtime) -> int:
        r = self._row(path, size, mtime)
        return r["count"] if r is not None else 0

    def blocked(self, path, size, mtime) -> bool:
        return self.count(path, size, mtime) >= MAX_FAILURES

    def fail(self, path, size, mtime) -> int:
        n = self.count(path, size, mtime) + 1
        if not self.read_only:
            self.rows[str(path)] = {"path": str(path), "size": size, "mtime": mtime,
                                    "count": n, "at": time.time()}
            self._save()
        return n

    def clear(self, path) -> None:
        if not self.read_only and self.rows.pop(str(path), None) is not None:
            self._save()

    def _save(self) -> None:
        with _locked(self.file):
            _rewrite(self.file, list(self.rows.values()))
