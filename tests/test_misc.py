"""Anything the router can't place lands in ~/Documents/Misc."""

import os
import time
from pathlib import Path

import pytest

import classify
import notify
import route
import setup
from batch import Batch

REPO_ROOT = Path(__file__).resolve().parent.parent
MISC = "~/Documents/Misc"


@pytest.fixture
def default_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    text = (REPO_ROOT / "rules.default.toml").read_text().replace("@@SCREENSHOT_DIR@@", "~/Desktop")
    path = tmp_path / "rules.toml"
    path.write_text(text)
    return route.load_rules(path)


def _match(rules, item, now):
    return route.match_rule(item, [r for r in rules if r.source == item.parent], now)


@pytest.mark.parametrize("folder", ["Downloads", "Inbox"])
def test_unsure_fallbacks_go_to_misc(default_rules, tmp_path, make_file, folder):
    now = time.time()
    for name in ("a.pdf", "x.ipynb", "bundle.zip"):
        hit = _match(default_rules, make_file(tmp_path / folder / name), now)
        assert (hit.action, hit.dest) == ("smart", MISC), name


@pytest.mark.parametrize("folder", ["Downloads", "Inbox"])
def test_files_with_no_rule_go_to_misc(default_rules, tmp_path, make_file, folder):
    now = time.time()
    for name in ("book.epub", "Node.class", "capture.cap", "sheet.xlsm", "noext"):
        hit = _match(default_rules, make_file(tmp_path / folder / name), now)
        assert (hit.action, hit.dest) == ("move", MISC), name


def test_known_types_still_win_over_misc(default_rules, tmp_path, make_file):
    now = time.time()
    hit = _match(default_rules, make_file(tmp_path / "Downloads" / "a.png"), now)
    assert hit.dest == "~/Media/Images"
    hit = _match(default_rules, make_file(tmp_path / "Downloads" / "a.dmg"), now)
    assert hit.dest == "~/Downloads/Installers"


def test_downloads_folders_fall_back_to_misc(default_rules, tmp_path):
    now = time.time()
    d = tmp_path / "Downloads" / "project"
    d.mkdir(parents=True)
    os.utime(d, (now - 3600, now - 3600))
    hit = _match(default_rules, d, now)
    assert (hit.action, hit.dest) == ("smart", MISC)


def test_desktop_files_still_go_to_inbox_hub(default_rules, tmp_path, make_file):
    hit = _match(default_rules, make_file(tmp_path / "Desktop" / "book.epub"), time.time())
    assert hit.dest == "~/Inbox"


def test_misc_is_never_a_candidate(tmp_path):
    for name in ("Misc", "Inbox", "Keys", "Career"):
        (tmp_path / name).mkdir()
    assert [c.name for c in classify.candidates(tmp_path)] == ["Career"]


def test_summary_and_unavailable_text_say_misc():
    b = Batch()
    b.add(Path("/d/a.pdf"), Path("/m/a.pdf"), None, "inbox", smart=True)
    assert notify.summary_text(b) == "1 file sorted: 1 left in Misc"
    assert "Misc" in notify.UNAVAILABLE_TEXT and "Inbox" not in notify.UNAVAILABLE_TEXT


def test_setup_creates_misc(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    setup.make_dirs()
    assert (tmp_path / "Documents" / "Misc").is_dir()


@pytest.mark.parametrize("folder", ["Downloads", "Inbox"])
def test_voice_notes_go_to_audio(default_rules, tmp_path, make_file, folder):
    now = time.time()
    for name in ("note.opus", "note.ogg", "memo.amr"):
        hit = _match(default_rules, make_file(tmp_path / folder / name), now)
        assert hit.dest == "~/Media/Audio", name
