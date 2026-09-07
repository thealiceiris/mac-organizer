import datetime as _dt
import os
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


def test_plan_action_move_uses_original_name(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = make_file(tmp_path / "Downloads" / "report.pdf")
    rule = _rule(action="move", dest=str(tmp_path / "Documents/Inbox"))

    action = route.plan_action(f, rule, time.time())

    assert action.dst == tmp_path / "Documents/Inbox" / "report.pdf"


def test_plan_action_timestamp_renames(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = make_file(tmp_path / "shots" / "Screenshot 2026.png")
    mtime = _dt.datetime(2026, 3, 9, 14, 5, 6).timestamp()

    os.utime(f, (mtime, mtime))
    rule = _rule(action="move_and_timestamp", dest=str(tmp_path / "Media/Screenshots/%Y-%m"))

    action = route.plan_action(f, rule, time.time())

    assert action.dst == tmp_path / "Media/Screenshots/2026-03" / "2026-03-09_14-05-06.png"


def test_plan_action_rejects_dest_outside_home(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = make_file(tmp_path / "Downloads" / "x.pdf")
    rule = _rule(action="move", dest="/tmp/escape")

    with pytest.raises(ValueError):
        route.plan_action(f, rule, time.time())


def test_apply_action_moves_and_logs(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = make_file(tmp_path / "Downloads" / "a.png")
    log = tmp_path / "state" / "actions.log"
    action = route.Action(src=f, dst=tmp_path / "Media/Images" / "a.png")

    route.apply_action(action, dry_run=False, log_path=log)

    assert not f.exists()
    assert (tmp_path / "Media/Images" / "a.png").read_text() == "x"
    assert " MOVE " in log.read_text()
    assert str(action.dst) in log.read_text()


def test_apply_action_dry_run_moves_nothing(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = make_file(tmp_path / "Downloads" / "a.png")
    log = tmp_path / "state" / "actions.log"
    action = route.Action(src=f, dst=tmp_path / "Media/Images" / "a.png")

    route.apply_action(action, dry_run=True, log_path=log)

    assert f.exists()
    assert not (tmp_path / "Media/Images" / "a.png").exists()
    assert "DRYRUN" in log.read_text()
