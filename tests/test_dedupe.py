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
    got = dedupe.find_identical(
        src, tmp_path / "missing", extra={dedupe.file_digest(src): planned}
    )
    assert got == planned
