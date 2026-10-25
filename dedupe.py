"""Byte-identical duplicate detection for files and directory trees."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

IGNORED = {".DS_Store"}


def file_digest(path: Path) -> str:
    """SHA-256 hex digest of a file's bytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(path: Path) -> str:
    """Hash over every entry below ``path`` (ignoring .DS_Store).

    Files contribute (relpath, size, file_digest), symlinks their target, and
    directories their relpath, so two trees with different folder layouts never
    hash alike.
    """
    entries = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in dirs:
            p = Path(root) / name
            rel = str(p.relative_to(path))
            if p.is_symlink():
                entries.append((rel, -1, os.readlink(p)))
            else:
                entries.append((rel + "/", -2, ""))
        for name in files:
            if name in IGNORED:
                continue
            p = Path(root) / name
            if p.is_symlink():
                entries.append((str(p.relative_to(path)), -1, os.readlink(p)))
                continue
            entries.append((str(p.relative_to(path)), p.stat().st_size, file_digest(p)))
    entries.sort()
    h = hashlib.sha256()
    for rel, size, digest in entries:
        h.update(f"{rel}\0{size}\0{digest}\n".encode())
    return h.hexdigest()


def _is_dir(p: Path) -> bool:
    return p.is_dir() and not p.is_symlink()


def _top_names(path: Path) -> frozenset[str]:
    """The names directly inside ``path``: one listdir, nothing read."""
    return frozenset(n for n in os.listdir(path) if n not in IGNORED)


def _tree_stats(path: Path, limit: tuple[int, int, int] | None = None):
    """(files, dirs, bytes) under ``path``, stat only; symlinks count as files of size 0.

    With ``limit``, the walk stops and returns None as soon as any count
    exceeds the limit's, so a huge tree costs no more than a small one.
    """
    files = dirs = size = 0
    stack = [path]
    while stack:
        with os.scandir(stack.pop()) as it:
            for e in it:
                if e.name in IGNORED:
                    continue
                if e.is_dir(follow_symlinks=False):
                    dirs += 1
                    stack.append(e.path)
                else:
                    files += 1
                    if e.is_file(follow_symlinks=False):
                        size += e.stat(follow_symlinks=False).st_size
                if limit is not None and (files > limit[0] or dirs > limit[1]
                                          or size > limit[2]):
                    return None
    return files, dirs, size


class _Lazy:
    """Computes the source's digest once, and only if a candidate needs it."""

    def __init__(self, fn, path: Path):
        self.fn, self.path, self.value = fn, path, None

    def __call__(self) -> str:
        if self.value is None:
            self.value = self.fn(self.path)
        return self.value


def _dest_entries(src: Path, dest_dir: Path | None) -> list[Path]:
    if dest_dir is None or not dest_dir.is_dir():
        return []
    return sorted(Path(e.path) for e in os.scandir(dest_dir)
                  if e.name not in IGNORED and Path(e.path) != src)


def _find_file(src: Path, dest_dir: Path | None, extra) -> Path | None:
    size = src.stat().st_size
    cands: list[tuple[Path, Path]] = []
    for existing, result in extra:
        try:
            if existing != src and existing.is_file() and not existing.is_symlink() \
                    and existing.stat().st_size == size:
                cands.append((existing, result))
        except OSError:
            continue
    for cand in _dest_entries(src, dest_dir):
        try:
            st = cand.lstat()
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode) and st.st_size == size:
            cands.append((cand, cand))
    if not cands:
        return None  # the source is never read
    digest = _Lazy(file_digest, src)
    for existing, result in cands:
        try:
            if file_digest(existing) == digest():
                return result
        except OSError:
            continue
    return None


def _find_dir(src: Path, dest_dir: Path | None, extra) -> Path | None:
    stats = _tree_stats(src)
    if stats[0] == 0:
        return None  # an empty or scaffold folder is never anyone's duplicate
    top = _top_names(src)
    pairs = [(e, r) for e, r in extra if e != src]
    pairs += [(c, c) for c in _dest_entries(src, dest_dir)]
    digest = _Lazy(tree_digest, src)
    for existing, result in pairs:
        try:
            if not _is_dir(existing) or _top_names(existing) != top:
                continue
            if _tree_stats(existing, limit=stats) != stats:
                continue
            if tree_digest(existing) == digest():
                return result
        except OSError:
            continue
    return None


def find_identical(
    src: Path, dest_dir: Path, extra: list[tuple[Path, Path]] | None = None
) -> Path | None:
    """An existing item byte-identical to ``src``, or None.

    Cheap checks come first and nothing is hashed unless a candidate survives
    them. Files are compared only with same-size files (no candidate: the
    source is never read). Directories are compared only with sibling folders
    holding the same top-level names (one listdir each) and the same file
    count, folder count and total bytes (a walk that stops as soon as the
    sibling outgrows the source); ``tree_digest`` runs on exact matches only.

    ``extra`` lists simulated arrivals as (item on disk now, planned path); a
    match returns the planned path. They are checked before ``dest_dir``.
    """
    try:
        if _is_dir(src):
            return _find_dir(src, dest_dir, extra or [])
        return _find_file(src, dest_dir, extra or [])
    except OSError:
        return None


def identical(a: Path, b: Path) -> bool:
    """True if ``a`` and ``b`` are distinct items with the same bytes (cheap checks first)."""
    try:
        if a.resolve() == b.resolve():
            return False
        if _is_dir(a):
            return _is_dir(b) and _find_dir(a, None, [(b, b)]) == b
        return b.is_file() and not b.is_symlink() and _find_file(a, None, [(b, b)]) == b
    except OSError:
        return False
