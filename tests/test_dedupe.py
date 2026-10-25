from pathlib import Path

import dedupe


def test_file_digest_matches_for_same_bytes(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello")
    b.write_bytes(b"hello")
    assert dedupe.file_digest(a) == dedupe.file_digest(b)
    b.write_bytes(b"hellO")
    assert dedupe.file_digest(a) != dedupe.file_digest(b)


def test_tree_digest_ignores_ds_store(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    for d in (a, b):
        (d / "sub").mkdir(parents=True)
        (d / "sub" / "f.txt").write_text("x")
    (b / ".DS_Store").write_bytes(b"junk")
    assert dedupe.tree_digest(a) == dedupe.tree_digest(b)
    (b / "sub" / "f.txt").write_text("y")
    assert dedupe.tree_digest(a) != dedupe.tree_digest(b)


def test_find_identical_file_and_none(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "cv.pdf").write_bytes(b"same")
    (dest / "other.pdf").write_bytes(b"diff")
    src = tmp_path / "cv (1).pdf"
    src.write_bytes(b"same")
    assert dedupe.find_identical(src, dest) == dest / "cv.pdf"
    src.write_bytes(b"nope")
    assert dedupe.find_identical(src, dest) is None
    assert dedupe.find_identical(src, tmp_path / "missing") is None


def test_find_identical_uses_extra(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hi")
    planned = tmp_path / "planned.txt"
    twin = tmp_path / "twin.txt"
    twin.write_text("hi")
    got = dedupe.find_identical(src, tmp_path / "missing", extra=[(twin, planned)])
    assert got == planned


# ---------------------------------------------------------------- final review C1: cheap checks


def _count_digests(monkeypatch):
    calls = []
    real = dedupe.file_digest

    def spy(path):
        calls.append(Path(path))
        return real(path)

    monkeypatch.setattr(dedupe, "file_digest", spy)
    return calls


def _tree(root, files):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def test_large_sibling_folders_are_never_hashed(tmp_path, monkeypatch):
    dest = tmp_path / "Development"
    # different top-level names: rejected after one listdir
    _tree(dest / "models", {f"m{i}.gguf": b"x" * 1000 for i in range(30)})
    # same top-level names but far bigger: the bounded walk aborts early
    _tree(dest / "bigproj", {"README.md": b"r", **{f"src/f{i}.py": b"y" * 500 for i in range(40)}})
    src = _tree(tmp_path / "Downloads/proj", {"README.md": b"r", "src/a.py": b"a"})
    calls = _count_digests(monkeypatch)

    assert dedupe.find_identical(src, dest) is None
    assert calls == []  # not even the source: no candidate survived the cheap checks


def test_bounded_walk_aborts_once_sibling_exceeds_source(tmp_path, monkeypatch):
    dest = tmp_path / "dest"
    _tree(dest / "big", {"README.md": b"r", **{f"src/d{i:03}/f.py": b"y" for i in range(200)}})
    src = _tree(tmp_path / "proj", {"README.md": b"r", "src/a.py": b"a"})
    seen = []
    real = dedupe.os.scandir

    def spy(path):
        seen.append(Path(path))
        return real(path)

    monkeypatch.setattr(dedupe.os, "scandir", spy)
    assert dedupe.find_identical(src, dest) is None
    inside_big = [p for p in seen if p == dest / "big" or dest / "big" in p.parents]
    assert 0 < len(inside_big) < 10  # stopped soon after it held more than the source


def test_identical_sibling_folder_is_found_hashing_only_the_pair(tmp_path, monkeypatch):
    dest = tmp_path / "dest"
    files = {"README.md": b"r", "src/a.py": b"a"}
    twin = _tree(dest / "proj", files)
    _tree(dest / "other", {"README.md": b"r", "src/b.py": b"bbbbbbbbbbbbb"})
    src = _tree(tmp_path / "proj copy", files)
    calls = _count_digests(monkeypatch)

    assert dedupe.find_identical(src, dest) == twin
    assert sorted(p.parent.parent.name if p.parent.name == "src" else p.parent.name
                  for p in calls) == ["proj", "proj", "proj copy", "proj copy"]


def test_large_file_without_same_size_candidate_is_never_read(tmp_path, monkeypatch):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "a.bin").write_bytes(b"1" * 10)
    (dest / "b.bin").write_bytes(b"2" * 20)
    src = tmp_path / "big.bin"
    src.write_bytes(b"z" * 100_000)
    calls = _count_digests(monkeypatch)

    assert dedupe.find_identical(src, dest) is None
    assert calls == []


def test_tree_digest_includes_directories(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    (a / "x").mkdir(parents=True)
    (b / "y").mkdir(parents=True)
    assert dedupe.tree_digest(a) != dedupe.tree_digest(b)


def test_empty_or_scaffold_folder_is_never_a_duplicate(tmp_path):
    dest = tmp_path / "dest"
    (dest / "empty").mkdir(parents=True)
    (dest / "scaffold/src").mkdir(parents=True)
    src = tmp_path / "new"
    src.mkdir()
    assert dedupe.find_identical(src, dest) is None
    (src / "src").mkdir()
    assert dedupe.find_identical(src, dest) is None


def test_extra_planned_arrivals_are_compared_cheaply(tmp_path, monkeypatch):
    first = tmp_path / "Downloads/a.pdf"
    first.parent.mkdir()
    first.write_bytes(b"same")
    other = tmp_path / "Downloads/c.pdf"
    other.write_bytes(b"different size")
    second = tmp_path / "Downloads/b.pdf"
    second.write_bytes(b"same")
    planned = tmp_path / "dest/a.pdf"
    extra = [(other, tmp_path / "dest/c.pdf"), (first, planned)]
    calls = _count_digests(monkeypatch)

    assert dedupe.find_identical(second, tmp_path / "missing", extra=extra) == planned
    assert other not in calls
