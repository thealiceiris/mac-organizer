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
    assert all(r.action in {"move", "move_and_timestamp", "skip", "trash", "smart"} for r in rules)


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


def test_resolve_dest_leaves_literal_percent_alone():
    mtime = _dt.datetime(2026, 3, 9, 14, 5, 0).timestamp()
    assert route.resolve_dest("~/Media/100% keep", mtime) == Path("~/Media/100% keep").expanduser()
    assert route.has_strftime_token("~/x/%Y") is True
    assert route.has_strftime_token("~/100% done") is False


def test_move_no_clobber_renames_instead_of_overwriting(tmp_path):
    src = tmp_path / "src/a.png"
    src.parent.mkdir()
    src.write_text("new")
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    (dest_dir / "a.png").write_text("existing")

    used = route.move_no_clobber(src, dest_dir / "a.png")

    assert used == dest_dir / "a -2.png"
    assert (dest_dir / "a.png").read_text() == "existing"
    assert used.read_text() == "new"
    assert not src.exists()


def test_move_no_clobber_creates_parents_and_removes_source(tmp_path):
    src = tmp_path / "a.png"
    src.write_text("new")

    used = route.move_no_clobber(src, tmp_path / "deep/er/a.png")

    assert used.read_text() == "new"
    assert not src.exists()


def test_move_no_clobber_raises_when_source_vanished(tmp_path):
    with pytest.raises(FileNotFoundError):
        route.move_no_clobber(tmp_path / "gone.png", tmp_path / "dest/gone.png")
    assert not (tmp_path / "dest/gone.png").exists()


def test_apply_action_reports_the_path_actually_used(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    f = make_file(tmp_path / "Downloads" / "a.png")
    (tmp_path / "Media/Images").mkdir(parents=True)
    (tmp_path / "Media/Images/a.png").write_text("existing")
    log = tmp_path / "state" / "actions.log"

    done = route.apply_action(
        route.Action(src=f, dst=tmp_path / "Media/Images" / "a.png"), False, log
    )

    assert done.dst == tmp_path / "Media/Images/a -2.png"
    assert f"-> {tmp_path}/Media/Images/a -2.png" in log.read_text()
    assert (tmp_path / "Media/Images/a.png").read_text() == "existing"


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


def _write_rules(path, home):
    path.write_text(
        f"""
[[rule]]
source = "{home}/Downloads"
ext = ["png"]
action = "move"
dest = "{home}/Media/Images"

[[rule]]
source = "{home}/Downloads"
ext = ["pdf"]
action = "move"
dest = "{home}/Documents/Inbox"
"""
    )


def test_process_dir_routes_and_is_idempotent(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    make_file(tmp_path / "Downloads" / "a.png")
    make_file(tmp_path / "Downloads" / "b.pdf")
    make_file(tmp_path / "Downloads" / "c.iso")  # unmatched, stays
    rules_file = tmp_path / "rules.toml"
    _write_rules(rules_file, tmp_path)
    rules = route.load_rules(rules_file)
    log = tmp_path / "state/actions.log"

    first = route.process_dir(tmp_path / "Downloads", rules, time.time(), False, log)
    assert len(first) == 2
    assert (tmp_path / "Media/Images/a.png").exists()
    assert (tmp_path / "Documents/Inbox/b.pdf").exists()
    assert (tmp_path / "Downloads/c.iso").exists()

    lines_after_first = log.read_text().count("\n")
    second = route.process_dir(tmp_path / "Downloads", rules, time.time(), False, log)
    assert second == []
    assert log.read_text().count("\n") == lines_after_first


def test_process_dir_survives_a_file_that_vanishes(tmp_path, make_file, monkeypatch):
    """I2: one failing file must not abort the rest of the pass."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # distinct bytes: identical files would be (correctly) treated as duplicates
    make_file(tmp_path / "Downloads" / "a.png", content="a")
    make_file(tmp_path / "Downloads" / "b.png", content="b")
    make_file(tmp_path / "Downloads" / "c.png", content="c")
    rules_file = tmp_path / "rules.toml"
    _write_rules(rules_file, tmp_path)
    rules = route.load_rules(rules_file)
    log = tmp_path / "state/actions.log"

    real_move = route.move_no_clobber

    def flaky(src, dst):
        if src.name == "b.png":
            raise FileNotFoundError(2, "No such file or directory", str(src))
        return real_move(src, dst)

    monkeypatch.setattr(route, "move_no_clobber", flaky)

    done = route.process_dir(tmp_path / "Downloads", rules, time.time(), False, log)

    assert [a.src.name for a in done] == ["a.png", "c.png"]
    assert (tmp_path / "Media/Images/a.png").exists()
    assert (tmp_path / "Media/Images/c.png").exists()
    error_lines = [ln for ln in log.read_text().splitlines() if "  ERROR  " in ln]
    assert len(error_lines) == 1
    assert error_lines[0].endswith(f"{tmp_path}/Downloads/b.png -> {tmp_path}/Media/Images/b.png")


def test_process_dir_survives_an_unwritable_destination(tmp_path, make_file, monkeypatch):
    """I2: a PermissionError on one file must not abort the pass either."""
    monkeypatch.setenv("HOME", str(tmp_path))
    make_file(tmp_path / "Downloads" / "a.png")
    make_file(tmp_path / "Downloads" / "b.pdf")
    rules_file = tmp_path / "rules.toml"
    _write_rules(rules_file, tmp_path)
    rules = route.load_rules(rules_file)
    log = tmp_path / "state/actions.log"
    locked = tmp_path / "Media/Images"
    locked.mkdir(parents=True)
    locked.chmod(0o500)
    try:
        done = route.process_dir(tmp_path / "Downloads", rules, time.time(), False, log)
    finally:
        locked.chmod(0o700)

    assert [a.src.name for a in done] == ["b.pdf"]
    assert (tmp_path / "Documents/Inbox/b.pdf").exists()
    assert (tmp_path / "Downloads/a.png").read_text() == "x"  # untouched, not lost
    assert "  ERROR  " in log.read_text()


def test_process_dir_error_line_names_the_offending_destination(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    make_file(tmp_path / "Downloads" / "a.png")
    rules_file = tmp_path / "rules.toml"
    rules_file.write_text(
        f"""
[[rule]]
source = "{tmp_path}/Downloads"
ext = ["png"]
action = "move"
dest = "/tmp/escape"
"""
    )
    rules = route.load_rules(rules_file)
    log = tmp_path / "state/actions.log"

    assert route.process_dir(tmp_path / "Downloads", rules, time.time(), False, log) == []

    line = log.read_text().splitlines()[-1]
    assert line.split("  ")[1] == "ERROR"
    assert line.endswith(f"{tmp_path}/Downloads/a.png -> /tmp/escape")
    assert (tmp_path / "Downloads/a.png").exists()


def test_dry_run_does_not_log_outside_home_errors(tmp_path, make_file, monkeypatch):
    """Minor: the preview must stay side-effect free and not grow the log."""
    monkeypatch.setenv("HOME", str(tmp_path))
    make_file(tmp_path / "Downloads" / "a.png")
    rules_file = tmp_path / "rules.toml"
    rules_file.write_text(
        f"""
[[rule]]
source = "{tmp_path}/Downloads"
ext = ["png"]
action = "move"
dest = "/tmp/escape"
"""
    )
    rules = route.load_rules(rules_file)
    log = tmp_path / "state/actions.log"

    route.process_dir(tmp_path / "Downloads", rules, time.time(), True, log)

    assert not log.exists()


def test_process_dir_ignores_rules_for_other_directories(tmp_path, make_file, monkeypatch):
    """I8: a match-everything ~/Desktop rule must not sweep ~/Downloads."""
    monkeypatch.setenv("HOME", str(tmp_path))
    make_file(tmp_path / "Downloads" / "a.png")
    make_file(tmp_path / "Downloads" / "unmatched.iso")
    make_file(tmp_path / "Desktop" / "d.iso")
    rules_file = tmp_path / "rules.toml"
    rules_file.write_text(
        f"""
[[rule]]
source = "{tmp_path}/Downloads"
ext = ["png"]
action = "move"
dest = "{tmp_path}/Media/Images"

[[rule]]
source = "{tmp_path}/Desktop"
action = "move"
dest = "{tmp_path}/Inbox"
"""
    )
    rules = route.load_rules(rules_file)
    log = tmp_path / "state/actions.log"

    route.process_dir(tmp_path / "Downloads", rules, time.time(), False, log)

    assert (tmp_path / "Media/Images/a.png").exists()
    assert (tmp_path / "Downloads/unmatched.iso").exists()
    assert not (tmp_path / "Inbox").exists()

    route.process_dir(tmp_path / "Desktop", rules, time.time(), False, log)
    assert (tmp_path / "Inbox/d.iso").exists()


def test_cycle_warnings_flags_loops_but_not_chains(tmp_path):
    def rules_from(text):
        p = tmp_path / "r.toml"
        p.write_text(text)
        return route.load_rules(p)

    chain = rules_from(
        """
[[rule]]
source = "~/Desktop"
action = "move"
dest = "~/Inbox"

[[rule]]
source = "~/Inbox"
ext = ["pdf"]
action = "move"
dest = "~/Documents/Inbox"
"""
    )
    assert route.cycle_warnings(chain) == []

    loop = rules_from(
        """
[[rule]]
source = "~/Desktop"
action = "move"
dest = "~/Inbox"

[[rule]]
source = "~/Inbox"
ext = ["pdf"]
action = "move"
dest = "~/Desktop"
"""
    )
    warnings = route.cycle_warnings(loop)
    assert len(warnings) == 1
    assert "cycle" in warnings[0]
    assert str(Path("~/Desktop").expanduser()) in warnings[0]


def test_default_rules_have_no_cycle_warning(tmp_path):
    src = Path(__file__).resolve().parent.parent / "rules.default.toml"
    patched = tmp_path / "rules.toml"
    patched.write_text(src.read_text().replace("@@SCREENSHOT_DIR@@", "~/Desktop"))

    assert route.cycle_warnings(route.load_rules(patched)) == []


def test_main_reports_missing_rules_file(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    rc = route.main(["--rules", str(tmp_path / "nope.toml"), "--log", str(tmp_path / "l.log")])

    assert rc == 1
    err = capsys.readouterr().err
    assert "rules file not found" in err and "install.sh" in err


def test_main_dry_run_moves_nothing(tmp_path, make_file, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    make_file(tmp_path / "Downloads" / "a.png")
    rules_file = tmp_path / "rules.toml"
    _write_rules(rules_file, tmp_path)
    log = tmp_path / "state/actions.log"

    rc = route.main(
        ["--dry-run", "--rules", str(rules_file), "--log", str(log),
         str(tmp_path / "Downloads")]
    )

    assert rc == 0
    assert (tmp_path / "Downloads" / "a.png").exists()
    assert "DRYRUN" in capsys.readouterr().out


def test_main_defaults_to_rule_sources(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    make_file(tmp_path / "Downloads" / "a.png")
    rules_file = tmp_path / "rules.toml"
    _write_rules(rules_file, tmp_path)
    log = tmp_path / "state/actions.log"

    rc = route.main(["--rules", str(rules_file), "--log", str(log)])

    assert rc == 0
    assert (tmp_path / "Media/Images/a.png").exists()


def _default_rules(tmp_path, screenshot_dir):
    src = Path(__file__).resolve().parent.parent / "rules.default.toml"
    patched = tmp_path / "rules.toml"
    patched.write_text(src.read_text().replace("@@SCREENSHOT_DIR@@", str(screenshot_dir)))
    return route.load_rules(patched)


def test_default_table_routes_end_to_end(tmp_path, make_file, monkeypatch):
    """I8: the shipped rules, traced with real files, land where the README says."""
    monkeypatch.setenv("HOME", str(tmp_path))
    shots = tmp_path / "Pictures/Screenshots"
    rules = _default_rules(tmp_path, shots)
    log = tmp_path / "state/actions.log"
    now = time.time()

    make_file(tmp_path / "Desktop" / "invoice.pdf")
    make_file(tmp_path / "Downloads" / "notes.md")
    shot = make_file(shots / "Screenshot 2026-03-09 at 14.05.06.png")
    mtime = _dt.datetime(2026, 3, 9, 14, 5, 6).timestamp()
    os.utime(shot, (mtime, mtime))

    for directory in (shots, tmp_path / "Desktop", tmp_path / "Downloads"):
        route.process_dir(directory, rules, now, False, log)
    # second hop: ~/Desktop drops into ~/Inbox, which routes on the next pass
    route.process_dir(tmp_path / "Inbox", rules, now, False, log)

    # pdfs are "smart" items: process_dir without a SmartRouter leaves them in place
    assert (tmp_path / "Inbox/invoice.pdf").exists()
    assert (tmp_path / "Notes/00-Inbox/notes.md").exists()
    assert (tmp_path / "Media/Screenshots/2026-03/2026-03-09_14-05-06.png").exists()
    assert not any(p.is_file() for p in (tmp_path / "Desktop").iterdir())
    assert [p.name for p in (tmp_path / "Inbox").iterdir()] == ["invoice.pdf"]

    # one line per action, including the intermediate Desktop -> Inbox hop
    lines = log.read_text().splitlines()
    assert len(lines) == 3  # the pdf makes no second hop until smart sorting lands
    assert all("  MOVE  " in line for line in lines)


def test_process_dir_survives_an_unreadable_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    locked = tmp_path / "Downloads"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        done = route.process_dir(locked, [], time.time(), False, tmp_path / "l.log")
    finally:
        locked.chmod(0o700)

    assert done == []
    assert "ERROR" in capsys.readouterr().err


# --- v2 config -------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_load_config_local_rules_come_first(tmp_path):
    shipped = tmp_path / "rules.toml"
    shipped.write_text('[[rule]]\nsource = "~/Downloads"\naction = "move"\ndest = "~/Shipped"\n')
    local = tmp_path / "rules.local.toml"
    local.write_text('[[rule]]\nsource = "~/Downloads"\naction = "move"\ndest = "~/Local"\n')

    cfg = route.load_config(shipped, local)

    assert [r.dest for r in cfg.rules] == ["~/Local", "~/Shipped"]
    assert [r.dest for r in route.load_rules(shipped)] == ["~/Shipped"]


def test_load_config_smart_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    shipped = tmp_path / "rules.toml"
    shipped.write_text("")

    cfg = route.load_config(shipped, None)
    assert cfg.smart.enabled is True
    assert cfg.smart.threshold == 0.6
    assert cfg.smart.model_path is None
    assert cfg.smart.documents_root == tmp_path / "Documents"

    local = tmp_path / "rules.local.toml"
    local.write_text('[smart_sort]\nmodel_path = "~/m.gguf"\n')
    cfg = route.load_config(shipped, local)
    assert cfg.smart.model_path == Path.home() / "m.gguf"

    missing = route.load_config(shipped, tmp_path / "nope.toml")
    assert missing.smart.model_path is None


def test_rule_kind_and_regex_parse(tmp_path):
    toml = tmp_path / "r.toml"
    toml.write_text(
        '[[rule]]\nsource = "~/Downloads"\nkind = "dir"\nname_regex = "cv|resume"\n'
        'has_installer = true\naction = "smart"\n'
    )
    rule = route.load_rules(toml)[0]
    assert rule.kind == "dir"
    assert rule.name_regex == "cv|resume"
    assert rule.has_installer is True
    plain = route.Rule(source=Path("/x"), action="move")
    assert (plain.kind, plain.name_regex, plain.has_installer) == ("file", None, None)


@pytest.fixture
def default_rules(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    text = (REPO_ROOT / "rules.default.toml").read_text().replace("@@SCREENSHOT_DIR@@", "~/Desktop")
    path = tmp_path / "rules.toml"
    path.write_text(text)
    return route.load_rules(path)


def _for_source(rules, item):
    return [r for r in rules if r.source == item.parent]


def _dl(tmp_path, name, make_file, age=3600, folder="Downloads"):
    return make_file(tmp_path / folder / name, age_seconds=age)


def test_default_rules_cover_v2_types(default_rules, tmp_path, make_file):
    now = time.time()

    def match(name, age=3600):
        item = _dl(tmp_path, name, make_file, age)
        return route.match_rule(item, _for_source(default_rules, item), now)

    assert match("a.m4a").dest == "~/Media/Audio"
    assert match("a.mov").dest == "~/Media/Video"
    assert match("a.svg").dest == "~/Media/Images"
    assert match("k.pem").dest == "~/Documents/Keys"
    assert match("deck.key").dest != "~/Documents/Keys"
    assert match("a.ics", age=2 * 86400).action == "trash"
    assert match("a.ics", age=3600) is None
    pdf = match("a.pdf")
    assert (pdf.action, pdf.dest) == ("smart", "~/Documents/Misc")
    nb = match("x.ipynb")
    assert (nb.action, nb.dest) == ("smart", "~/Documents/Misc")

    candidates = [
        r for r in default_rules
        if r.source == tmp_path / "Downloads" and r.kind == "file" and "zip" in r.ext
    ]
    assert candidates[0].has_installer is True
    assert candidates[1].action == "smart" and candidates[1].dest == "~/Documents/Misc"


def test_default_rules_desktop_dirs(default_rules, tmp_path):
    now = time.time()
    app = tmp_path / "Desktop" / "Foo.app"
    app.mkdir(parents=True)
    plain = tmp_path / "Desktop" / "stuff"
    plain.mkdir()
    for p in (app, plain):
        os.utime(p, (now - 3600, now - 3600))

    hit = route.match_rule(app, _for_source(default_rules, app), now)
    assert hit is not None and hit.dest == "~/Downloads/Installers"
    assert route.match_rule(plain, _for_source(default_rules, plain), now) is None


def test_rule_kind_filters_items(tmp_path):
    now = time.time()
    d = tmp_path / "Foo.app"
    d.mkdir()
    f = tmp_path / "bar.app"
    f.write_text("x")
    for p in (d, f):
        os.utime(p, (now - 3600, now - 3600))
    dir_rule = route.Rule(source=tmp_path, action="move", ext=frozenset({"app"}), kind="dir")
    file_rule = route.Rule(source=tmp_path, action="move", ext=frozenset({"app"}))
    assert route.rule_matches(dir_rule, d, now) and not route.rule_matches(dir_rule, f, now)
    assert route.rule_matches(file_rule, f, now) and not route.rule_matches(file_rule, d, now)


# --- Task 2: directory items, trash, no-clobber dir moves -------------------

def _mkdir(path, age=3600):
    path.mkdir(parents=True, exist_ok=True)
    stamp = time.time() - age
    os.utime(path, (stamp, stamp))
    return path


def _dir_rules(tmp_path, body):
    f = tmp_path / "rules.toml"
    f.write_text(body)
    return route.load_rules(f)


def test_dir_not_settled_when_child_recent(tmp_path, make_file):
    d = _mkdir(tmp_path / "proj")
    child = make_file(d / "f.txt", age_seconds=10)
    os.utime(d, (time.time() - 3600,) * 2)
    now = time.time()
    assert route.dir_is_settled(d, now) is False
    old = now - 3600
    os.utime(child, (old, old))
    os.utime(d, (old, old))
    assert route.dir_is_settled(d, now) is True


def test_process_dir_ignores_dirs_without_dir_rule(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rules = _dir_rules(tmp_path, f"""
[[rule]]
source = "{tmp_path}/Desktop"
action = "move"
dest = "{tmp_path}/Inbox"
""")
    _mkdir(tmp_path / "Desktop" / "sub")
    make_file(tmp_path / "Desktop" / "a.txt")
    route.process_dir(tmp_path / "Desktop", rules, time.time(), False, tmp_path / "log")
    assert (tmp_path / "Desktop" / "sub").is_dir()
    assert (tmp_path / "Inbox" / "a.txt").exists()


def test_process_dir_skips_own_dest_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rules = _dir_rules(tmp_path, f"""
[[rule]]
source = "{tmp_path}/Downloads"
ext = ["dmg"]
action = "move"
dest = "{tmp_path}/Downloads/Installers"

[[rule]]
source = "{tmp_path}/Downloads"
kind = "dir"
action = "move"
dest = "{tmp_path}/Elsewhere"
""")
    _mkdir(tmp_path / "Downloads" / "Installers")
    _mkdir(tmp_path / "Downloads" / "other")
    route.process_dir(tmp_path / "Downloads", rules, time.time(), False, tmp_path / "log")
    assert (tmp_path / "Downloads" / "Installers").is_dir()
    assert (tmp_path / "Elsewhere" / "other").is_dir()


def test_process_dir_moves_app_bundle_to_installers(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rules = _dir_rules(tmp_path, f"""
[[rule]]
source = "{tmp_path}/Desktop"
kind = "dir"
ext = ["app"]
action = "move"
dest = "{tmp_path}/Downloads/Installers"
""")
    _mkdir(tmp_path / "Desktop" / "Foo.app")
    done = route.process_dir(tmp_path / "Desktop", rules, time.time(), False, tmp_path / "log")
    assert (tmp_path / "Downloads/Installers/Foo.app").is_dir()
    assert len(done) == 1


def test_process_dir_skips_unsettled_dir(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rules = _dir_rules(tmp_path, f"""
[[rule]]
source = "{tmp_path}/Downloads"
kind = "dir"
action = "move"
dest = "{tmp_path}/Elsewhere"
""")
    d = _mkdir(tmp_path / "Downloads" / "busy")
    make_file(d / "f", age_seconds=5)
    os.utime(d, (time.time() - 3600,) * 2)
    route.process_dir(tmp_path / "Downloads", rules, time.time(), False, tmp_path / "log")
    assert d.is_dir()


def test_trash_action_logs_TRASH(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rules = _dir_rules(tmp_path, f"""
[[rule]]
source = "{tmp_path}/Downloads"
ext = ["ics"]
action = "trash"
""")
    make_file(tmp_path / "Downloads" / "a.ics")
    log = tmp_path / "log"
    done = route.process_dir(tmp_path / "Downloads", rules, time.time(), False, log)
    assert (tmp_path / ".Trash" / "a.ics").exists()
    assert not (tmp_path / "Downloads" / "a.ics").exists()
    assert len(done) == 1
    assert "  TRASH  " in log.read_text()


def test_smart_rule_leaves_item_in_place(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    rules = _dir_rules(tmp_path, f"""
[[rule]]
source = "{tmp_path}/Downloads"
ext = ["pdf"]
action = "smart"
dest = ""
""")
    make_file(tmp_path / "Downloads" / "a.pdf")
    done = route.process_dir(tmp_path / "Downloads", rules, time.time(), False, tmp_path / "log")
    assert (tmp_path / "Downloads" / "a.pdf").exists() and done == []
    assert not (tmp_path / "log").exists()


def test_own_dest_dirs_and_cycle_warnings_ignore_empty_dest(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    rules = [
        route.Rule(source=tmp_path / "Downloads", action="smart", dest=""),
        route.Rule(source=tmp_path / "Downloads", action="move", dest=f"{tmp_path}/Media/%Y"),
        route.Rule(source=tmp_path, action="move", dest=f"{tmp_path}/Downloads"),
    ]
    assert route.own_dest_dirs(rules) == {tmp_path / "Media", tmp_path / "Downloads"}
    # source == CWD would make a bogus Path(".") edge look like a cycle
    assert route.cycle_warnings(rules[:1] + [route.Rule(source=Path("."), action="move", dest="")]) == []


def test_main_reads_local_overlay(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "rules.toml").write_text(f"""
[[rule]]
source = "{tmp_path}/Downloads"
ext = ["png"]
action = "move"
dest = "{tmp_path}/Media/Images"
""")
    (tmp_path / "rules.local.toml").write_text(f"""
[[rule]]
source = "{tmp_path}/Downloads"
ext = ["iso"]
action = "move"
dest = "{tmp_path}/Media/Isos"
""")
    make_file(tmp_path / "Downloads" / "a.iso")
    rc = route.main([
        "--rules", str(tmp_path / "rules.toml"),
        "--log", str(tmp_path / "log"), "--lock", str(tmp_path / "lock"),
    ])
    assert rc == 0
    assert (tmp_path / "Media/Isos/a.iso").exists()


def test_identical_file_goes_to_trash_not_dash2(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dest = tmp_path / "Documents"
    make_file(dest / "cv.pdf", content="same")
    f = make_file(tmp_path / "Downloads" / "cv (1).pdf", content="same")
    log = tmp_path / "state" / "actions.log"

    done = route.apply_action(route.Action(src=f, dst=dest / "cv (1).pdf"), False, log)

    assert done.duplicate is True
    assert done.dst == dest / "cv.pdf"
    assert not f.exists()
    assert (tmp_path / ".Trash" / "cv (1).pdf").read_text() == "same"
    assert sorted(p.name for p in dest.iterdir()) == ["cv.pdf"]
    assert f"  DUPLICATE  {f} -> {dest / 'cv.pdf'}" in log.read_text()


def test_same_name_different_bytes_gets_dash2(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dest = tmp_path / "Documents"
    make_file(dest / "cv.pdf", content="old")
    f = make_file(tmp_path / "Downloads" / "cv.pdf", content="new")

    done = route.apply_action(
        route.Action(src=f, dst=dest / "cv.pdf"), False, tmp_path / "l.log"
    )

    assert done.duplicate is False
    assert done.dst == dest / "cv -2.pdf"
    assert (dest / "cv.pdf").read_text() == "old"


def test_identical_dir_detected_ignoring_ds_store(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dest = tmp_path / "Development"
    make_file(dest / "proj" / "a.txt", content="a")
    src = tmp_path / "Downloads" / "proj copy"
    make_file(src / "a.txt", content="a")
    (src / ".DS_Store").write_bytes(b"junk")

    done = route.apply_action(
        route.Action(src=src, dst=dest / "proj copy"), False, tmp_path / "l.log"
    )

    assert done.duplicate is True
    assert done.dst == dest / "proj"
    assert not src.exists()
    assert (tmp_path / ".Trash" / "proj copy").is_dir()
    assert sorted(p.name for p in dest.iterdir()) == ["proj"]


def test_dry_run_reports_duplicate_without_moving(tmp_path, make_file, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    dest = tmp_path / "Documents"
    make_file(dest / "cv.pdf", content="same")
    f = make_file(tmp_path / "Downloads" / "cv (1).pdf", content="same")
    log = tmp_path / "state" / "actions.log"

    done = route.apply_action(route.Action(src=f, dst=dest / "cv (1).pdf"), True, log)

    assert done.duplicate is True
    assert f.exists()
    assert not (tmp_path / ".Trash").exists()
    assert "DUPLICATE" in capsys.readouterr().out
    assert "DUPLICATE" in log.read_text()


def test_trash_action_skips_duplicate_check(tmp_path, make_file, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    make_file(tmp_path / ".Trash" / "a.txt", content="same")
    f = make_file(tmp_path / "Downloads" / "a.txt", content="same")

    done = route.apply_action(
        route.Action(src=f, dst=tmp_path / ".Trash" / "a.txt", verb="TRASH"),
        False,
        tmp_path / "l.log",
    )

    assert done.duplicate is False
    assert done.verb == "TRASH"


def test_rule_matches_honours_has_installer(tmp_path):
    import zipfile
    app = tmp_path / "app.zip"
    with zipfile.ZipFile(app, "w") as z:
        z.writestr("Foo.app/Contents/Info.plist", "x")
    src = tmp_path / "src.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("main.c", "x")
    yes = route.Rule(source=tmp_path, ext=frozenset({"zip"}), action="move", dest="~/I", has_installer=True)
    no = route.Rule(source=tmp_path, ext=frozenset({"zip"}), action="move", dest="~/I", has_installer=False)
    assert route.rule_matches(yes, app, 0) and not route.rule_matches(yes, src, 0)
    assert route.rule_matches(no, src, 0) and not route.rule_matches(no, app, 0)


# --- final review: rules are validated when they load ------------------------


@pytest.mark.parametrize("body,needle", [
    ('kind = "folder"', "kind"),
    ('name_regex = "cv(resume"', "name_regex"),
])
def test_bad_rule_is_rejected_at_load_naming_the_rule(tmp_path, body, needle):
    toml = tmp_path / "r.toml"
    toml.write_text('[[rule]]\nsource = "~/Downloads"\naction = "move"\ndest = "~/x"\n\n'
                    f'[[rule]]\nsource = "~/Desktop"\naction = "move"\ndest = "~/y"\n{body}\n')
    with pytest.raises(ValueError, match=needle) as info:
        route.load_rules(toml)
    assert "rule 2" in str(info.value) and "Desktop" in str(info.value)


def test_main_reports_a_bad_rule_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    toml = tmp_path / "rules.toml"
    toml.write_text('[[rule]]\nsource = "~/Downloads"\nkind = "folder"\naction = "smart"\n')
    rc = route.main(["--rules", str(toml), "--log", str(tmp_path / "l.log"),
                     "--lock", str(tmp_path / "lock")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "rule 1" in err and "kind" in err
