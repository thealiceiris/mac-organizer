import datetime as _dt
import time
from pathlib import Path

import pytest

import route


def test_load_rules_parses_fields_and_order(tmp_path):
    toml = tmp_path / "r.toml"
    toml.write_text(
        """
[[rule]]
source = "~/Downloads"
ext = ["PNG", ".jpg"]
action = "move"
dest = "~/Media/Images"

[[rule]]
source = "~/Desktop"
glob = "Screen*"
action = "move_and_timestamp"
dest = "~/Media/Screenshots/%Y-%m"
min_age_seconds = 60
"""
    )
    rules = route.load_rules(toml)

    assert len(rules) == 2
    assert rules[0].source == Path("~/Downloads").expanduser()
    assert rules[0].ext == frozenset({"png", "jpg"})
    assert rules[0].action == "move"
    assert rules[0].dest == "~/Media/Images"
    assert rules[0].glob is None
    assert rules[0].min_age_seconds == 0
    assert rules[1].glob == "Screen*"
    assert rules[1].min_age_seconds == 60


def test_sources_are_unique_and_sorted(tmp_path):
    toml = tmp_path / "r.toml"
    toml.write_text(
        """
[[rule]]
source = "~/Inbox"
action = "skip"

[[rule]]
source = "~/Downloads"
ext = ["pdf"]
action = "move"
dest = "~/Documents/Inbox"

[[rule]]
source = "~/Downloads"
ext = ["png"]
action = "move"
dest = "~/Media/Images"
"""
    )
    assert route.sources(route.load_rules(toml)) == [
        Path("~/Downloads").expanduser(),
        Path("~/Inbox").expanduser(),
    ]


def test_default_rules_file_loads(tmp_path):
    src = Path(__file__).resolve().parent.parent / "rules.default.toml"
    patched = tmp_path / "rules.toml"
    patched.write_text(src.read_text().replace("@@SCREENSHOT_DIR@@", "~/Pictures/Screenshots"))

    rules = route.load_rules(patched)

    assert Path("~/Downloads").expanduser() in route.sources(rules)
    assert Path("~/Desktop").expanduser() in route.sources(rules)
    assert Path("~/Inbox").expanduser() in route.sources(rules)
    assert all(r.action in {"move", "move_and_timestamp", "skip"} for r in rules)


def _rule(**kw):
    kw.setdefault("source", Path("~/Downloads").expanduser())
    kw.setdefault("action", "move")
    return route.Rule(**kw)


def test_should_skip_new_and_partial_and_dotfiles(tmp_path, make_file):
    now = time.time()
    fresh = make_file(tmp_path / "a.png", age_seconds=5)
    old = make_file(tmp_path / "b.png", age_seconds=3600)
    partial = make_file(tmp_path / "c.crdownload", age_seconds=3600)
    dot = make_file(tmp_path / ".DS_Store", age_seconds=3600)

    assert route.should_skip(fresh, now) is True
    assert route.should_skip(old, now) is False
    assert route.should_skip(partial, now) is True
    assert route.should_skip(dot, now) is True


def test_rule_matches_ext_glob_and_age(tmp_path, make_file):
    now = time.time()
    f = make_file(tmp_path / "Screenshot 2026.png", age_seconds=3600)

    assert route.rule_matches(_rule(ext=frozenset({"png"})), f, now) is True
    assert route.rule_matches(_rule(ext=frozenset({"pdf"})), f, now) is False
    assert route.rule_matches(_rule(glob="Screenshot *"), f, now) is True
    assert route.rule_matches(_rule(glob="Recording *"), f, now) is False
    assert route.rule_matches(_rule(min_age_seconds=99999), f, now) is False


def test_match_rule_first_match_wins_and_skip_shadows(tmp_path, make_file):
    now = time.time()
    f = make_file(tmp_path / "keep.png", age_seconds=3600)

    rules = [
        _rule(glob="keep.*", action="skip"),
        _rule(ext=frozenset({"png"}), dest="~/Media/Images"),
    ]
    assert route.match_rule(f, rules, now) is None

    rules2 = [
        _rule(ext=frozenset({"png"}), dest="~/Media/Images"),
        _rule(ext=frozenset({"png"}), dest="~/other"),
    ]
    assert route.match_rule(f, rules2, now).dest == "~/Media/Images"


def test_resolve_dest_expands_user_and_strftime():
    mtime = _dt.datetime(2026, 3, 9, 14, 5, 0).timestamp()
    out = route.resolve_dest("~/Media/Screenshots/%Y-%m", mtime)
    assert out == Path("~/Media/Screenshots/2026-03").expanduser()


def test_within_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert route.within_home(tmp_path / "Media" / "x") is True
    assert route.within_home(Path("/etc/passwd")) is False


def test_timestamp_name():
    mtime = _dt.datetime(2026, 3, 9, 14, 5, 6).timestamp()
    assert route.timestamp_name(mtime, ".png") == "2026-03-09_14-05-06.png"


def test_dedupe_inserts_counter(tmp_path):
    (tmp_path / "a.png").write_text("x")
    assert route.dedupe(tmp_path / "b.png") == tmp_path / "b.png"
    assert route.dedupe(tmp_path / "a.png") == tmp_path / "a -2.png"
    (tmp_path / "a -2.png").write_text("x")
    assert route.dedupe(tmp_path / "a.png") == tmp_path / "a -3.png"
