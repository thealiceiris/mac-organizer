import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import extract


def _runner(stdout="", exc=None, calls=None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append((cmd, kw))
        if exc:
            raise exc
        return SimpleNamespace(stdout=stdout, returncode=0)
    return run


def _docx(path: Path, text="Quarterly budget review"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml",
                   f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    return path


def test_sample_docx_text(tmp_path):
    p = _docx(tmp_path / "a.docx")
    out = extract.sample(p)
    assert out.startswith("Name: a.docx\n")
    assert "Quarterly budget review" in out
    assert "<w:" not in out


def test_sample_pptx_text(tmp_path):
    p = tmp_path / "s.pptx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("ppt/slides/slide1.xml", "<p:sld><a:t>Launch plan</a:t></p:sld>")
        z.writestr("ppt/slides/slide2.xml", "<p:sld><a:t>Roadmap</a:t></p:sld>")
    out = extract.sample(p)
    assert "Launch plan" in out and "Roadmap" in out


def test_sample_pdf_uses_pdftotext(tmp_path):
    p = tmp_path / "paper.pdf"
    p.write_bytes(b"%PDF")
    calls = []
    out = extract.sample(p, runner=_runner("neural language models", calls=calls))
    assert "neural language models" in out
    cmd, kw = calls[0]
    assert cmd == ["pdftotext", "-l", "5", "-q", str(p), "-"]
    assert kw["timeout"] and kw["check"] is True


def test_sample_pdf_failure_is_name_only(tmp_path):
    p = tmp_path / "enc.pdf"
    p.write_bytes(b"%PDF")
    err = subprocess.CalledProcessError(1, "pdftotext")
    assert extract.sample(p, runner=_runner(exc=err)) == "Name: enc.pdf\n"
    to = subprocess.TimeoutExpired("pdftotext", 60)
    assert extract.sample(p, runner=_runner(exc=to)) == "Name: enc.pdf\n"


def test_sample_pdf_empty_text_is_name_only(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF")
    assert extract.sample(p, runner=_runner("  \n\f ")) == "Name: scan.pdf\n"


def test_sample_corrupt_docx_is_name_only(tmp_path):
    p = tmp_path / "bad.docx"
    p.write_bytes(b"not a zip")
    assert extract.sample(p) == "Name: bad.docx\n"


def test_sample_non_utf8_code(tmp_path):
    p = tmp_path / "x.py"
    p.write_bytes(b"# caf\xe9\nprint('hi')\n")
    out = extract.sample(p)
    assert "print('hi')" in out


def test_sample_code_first_150_lines(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("\n".join(f"line{i}" for i in range(300)))
    out = extract.sample(p)
    assert "line149" in out and "line150" not in out


def test_sample_truncates_to_max_words(tmp_path):
    p = tmp_path / "x.txt"
    p.write_text(" ".join(f"w{i}" for i in range(100)))
    out = extract.sample(p, max_words=10)
    body = out.split("\n", 1)[1]
    assert body.split() == [f"w{i}" for i in range(10)]


def test_sample_ipynb(tmp_path):
    p = tmp_path / "n.ipynb"
    p.write_text(json.dumps({"cells": [
        {"cell_type": "markdown", "source": ["# Title\n", "intro"]},
        {"cell_type": "code", "source": "import pandas"},
        {"cell_type": "raw", "source": "ignored"},
    ]}))
    out = extract.sample(p)
    assert "Title" in out and "import pandas" in out and "ignored" not in out


def test_sample_pages_is_name_only(tmp_path):
    p = tmp_path / "d.pages"
    p.write_bytes(b"x")
    assert extract.sample(p) == "Name: d.pages\n"


def test_sample_doc_uses_textutil(tmp_path):
    p = tmp_path / "old.doc"
    p.write_bytes(b"x")
    calls = []
    out = extract.sample(p, runner=_runner("legacy text", calls=calls))
    assert "legacy text" in out
    assert calls[0][0] == ["textutil", "-convert", "txt", "-stdout", str(p)]


def test_sample_zip_lists_members_without_extracting(tmp_path):
    p = tmp_path / "src" / "proj.zip"
    p.parent.mkdir()
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("proj/main.c", "int main(){}")
        z.writestr("proj/README", "hi")
    before = sorted(tmp_path.rglob("*"))
    out = extract.sample(p)
    assert "proj/main.c" in out and "proj/README" in out
    assert sorted(tmp_path.rglob("*")) == before


def test_sample_tar_lists_members(tmp_path):
    import tarfile
    f = tmp_path / "f.txt"
    f.write_text("x")
    p = tmp_path / "a.tgz"
    with tarfile.open(p, "w:gz") as t:
        t.add(f, arcname="inner/f.txt")
    assert "inner/f.txt" in extract.sample(p)


def test_sample_dir_includes_paths_and_doc_text(tmp_path):
    d = tmp_path / "proj"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "notes.txt").write_text("meeting minutes")
    _docx(d / "plan.docx", "roadmap details")
    out = extract.sample(d)
    assert out.startswith("Name: proj\n")
    assert "sub/notes.txt" in out and "plan.docx" in out
    assert "meeting minutes" in out and "roadmap details" in out


def test_sample_missing_file_never_raises(tmp_path):
    assert extract.sample(tmp_path / "nope.docx") == "Name: nope.docx\n"


def _zip(path, names):
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return path


def test_archive_has_installer_true_for_app_inside(tmp_path):
    p = _zip(tmp_path / "a.zip", ["Foo.app/Contents/Info.plist"])
    assert extract.archive_has_installer(p) is True
    q = _zip(tmp_path / "b.zip", ["setup.pkg"])
    assert extract.archive_has_installer(q) is True


def test_archive_has_installer_false_for_sources(tmp_path):
    p = _zip(tmp_path / "a.zip", ["src/main.c", "README.md"])
    assert extract.archive_has_installer(p) is False


def test_archive_has_installer_false_for_corrupt_zip(tmp_path):
    p = tmp_path / "a.zip"
    p.write_bytes(b"garbage")
    assert extract.archive_has_installer(p) is False
