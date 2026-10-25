import os
import time
from pathlib import Path

import pytest

import fsops
import route


def test_route_reexports_file_move_helpers():
    assert route.move_no_clobber is fsops.move_no_clobber
    assert route.candidate_names is fsops.candidate_names


def test_move_dir_no_clobber_never_replaces_empty_dir(tmp_path):
    (tmp_path / "dst" / "data").mkdir(parents=True)  # empty: plain rename would replace it
    src = tmp_path / "src" / "data"
    src.mkdir(parents=True)
    (src / "f.txt").write_text("x")

    used = fsops.move_dir_no_clobber(src, tmp_path / "dst" / "data")

    assert used == tmp_path / "dst" / "data -2"
    assert list((tmp_path / "dst" / "data").iterdir()) == []
    assert (used / "f.txt").read_text() == "x"
    assert not src.exists()


def test_move_dir_no_clobber_suffixes(tmp_path):
    used = []
    for i in range(3):
        src = tmp_path / f"s{i}" / "data"
        src.mkdir(parents=True)
        used.append(fsops.move_dir_no_clobber(src, tmp_path / "dst" / "data"))
    assert [p.name for p in used] == ["data", "data -2", "data -3"]


def test_move_item_dispatches_on_kind(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    d = tmp_path / "d"
    d.mkdir()
    assert fsops.move_item(f, tmp_path / "out" / "a.txt").read_text() == "x"
    assert fsops.move_item(d, tmp_path / "out" / "d").is_dir()


def test_trash_file_and_dir_with_clash(tmp_path):
    trash_dir = tmp_path / "Trash"
    f1 = tmp_path / "a" / "x.txt"
    f2 = tmp_path / "b" / "x.txt"
    for f in (f1, f2):
        f.parent.mkdir()
        f.write_text("x")
    d = tmp_path / "folder"
    d.mkdir()

    assert fsops.trash(f1, trash_dir) == trash_dir / "x.txt"
    assert fsops.trash(f2, trash_dir) == trash_dir / "x -2.txt"
    assert fsops.trash(d, trash_dir) == trash_dir / "folder"
    assert not f1.exists() and not f2.exists() and not d.exists()
