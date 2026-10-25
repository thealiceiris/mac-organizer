"""Learning memory: placements, Finder corrections, and cached "unsure" verdicts.

All state is append-only JSONL under the state dir. Corrupt or partial lines are
skipped, never fatal. Nothing here touches the network.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

WINDOW = 30 * 86400.0
LOCATE_TIMEOUT = 20.0
PLACEMENTS = "placements.jsonl"
CORRECTIONS = "corrections.jsonl"
VERDICTS = "verdicts.jsonl"


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


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    for row in _read(pfile):
        try:
            at, path, file = float(row["at"]), Path(row["path"]), row["file"]
            folder = row["folder"]
        except (KeyError, TypeError, ValueError):
            continue
        if now - at > WINDOW:
            continue
        if os.path.lexists(path):
            keep.append(row)  # still where we put it
            continue
        now_at = _find(row, locate)
        if now_at is not None:
            right = _owner(now_at, cands)
            if right is not None and right != folder:
                new.append({"file": file, "summary": row.get("summary", ""),
                            "wrong": folder, "right": right, "at": now})
        # found elsewhere, moved outside Documents, or deleted: resolved
    for c in new:
        _append(state / CORRECTIONS, c)
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
    """Remembers "unsure" verdicts so unchanged items are not re-asked."""

    def __init__(self, state: Path):
        self.file = Path(state) / VERDICTS
        self.keys = {r["key"] for r in _read(self.file) if isinstance(r.get("key"), str)}

    @staticmethod
    def _key(path, size, mtime, cands_hash, corrections) -> str:
        blob = json.dumps([str(path), size, mtime, cands_hash, corrections])
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def get(self, path, size, mtime, cands_hash, corrections_count) -> bool:
        return self._key(path, size, mtime, cands_hash, corrections_count) in self.keys

    def put_unsure(self, path, size, mtime, cands_hash, corrections_count) -> None:
        key = self._key(path, size, mtime, cands_hash, corrections_count)
        if key not in self.keys:
            self.keys.add(key)
            _append(self.file, {"key": key, "path": str(path), "verdict": "unsure"})
