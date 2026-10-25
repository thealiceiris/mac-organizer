"""Filesystem moves that never overwrite, merge or permanently delete."""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import shutil
from pathlib import Path

RENAME_EXCL = 0x00000004  # renamex_np flag: fail with EEXIST instead of replacing


def candidate_names(dst: Path):
    """``dst``, then ``dst -2``, ``dst -3``, … forever."""
    yield dst
    counter = 2
    while True:
        yield dst.with_name(f"{dst.stem} -{counter}{dst.suffix}")
        counter += 1


def move_no_clobber(src: Path, dst: Path) -> Path:
    """Move file ``src`` to ``dst``, never overwriting. Returns the path used.

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


_renamex_np = None


def _rename_excl(src: Path, dst: Path) -> None:
    """``renamex_np(src, dst, RENAME_EXCL)``; raises OSError (EEXIST if dst exists)."""
    global _renamex_np
    if _renamex_np is None:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        fn.restype = ctypes.c_int
        _renamex_np = fn
    if _renamex_np(os.fsencode(src), os.fsencode(dst), RENAME_EXCL) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(dst))


def move_dir_no_clobber(src: Path, dst: Path) -> Path:
    """Move directory ``src`` to ``dst``, never replacing or merging.

    Plain ``rename(2)`` silently replaces an *empty* destination directory, so
    the name is claimed with ``RENAME_EXCL`` instead. Cross-volume moves raise
    ``OSError`` (EXDEV): everything stays under ``$HOME``.
    """
    for candidate in candidate_names(dst):
        candidate.parent.mkdir(parents=True, exist_ok=True)
        try:
            _rename_excl(src, candidate)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                continue
            raise
        return candidate


def move_item(src: Path, dst: Path) -> Path:
    if src.is_dir() and not src.is_symlink():
        return move_dir_no_clobber(src, dst)
    return move_no_clobber(src, dst)


def trash(path: Path, trash_dir: Path | None = None) -> Path:
    """Move ``path`` into the Trash (never deletes), `` -2`` on a name clash."""
    if trash_dir is None:
        trash_dir = Path("~/.Trash").expanduser()
    return move_item(path, trash_dir / path.name)
