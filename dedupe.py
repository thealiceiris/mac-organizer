"""Byte-identical duplicate detection for files and directory trees."""

from __future__ import annotations

import hashlib
import os
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
    """Hash over sorted (relpath, size, file_digest) of every file, ignoring .DS_Store."""
    entries = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
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


def find_identical(
    src: Path, dest_dir: Path, extra: dict[str, Path] | None = None
) -> Path | None:
    """An existing item byte-identical to ``src``, or None.

    Files are compared only against same-size files in ``dest_dir``; directories
    against sibling directories by ``tree_digest``. ``extra`` maps digest ->
    planned path (simulated arrivals) and is checked as well.
    """
    try:
        src_is_dir = _is_dir(src)
        digest = tree_digest(src) if src_is_dir else file_digest(src)
        size = None if src_is_dir else src.stat().st_size
        if extra and digest in extra:
            return extra[digest]
        if not dest_dir.is_dir():
            return None
        for cand in sorted(dest_dir.iterdir()):
            if cand == src or cand.name in IGNORED:
                continue
            if src_is_dir:
                if _is_dir(cand) and tree_digest(cand) == digest:
                    return cand
            elif cand.is_file() and not cand.is_symlink():
                if cand.stat().st_size == size and file_digest(cand) == digest:
                    return cand
    except OSError:
        return None
    return None
