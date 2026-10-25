"""Task 10: backlog dry-run report, per-line veto, and apply (fake classifier only)."""

import json
import os
import time
from pathlib import Path

import pytest

import backlog
import route
from classify import Verdict

NLP = "Academics/Natural Language Processing"


# ---------------------------------------------------------------- fakes / setup


class FakeFactory:
    """Stands in for ``LlamaServer``. ``answer(first_line_of_sample)`` -> folder or None."""

    def __init__(self, answer=lambda name: None):
        self.answer = answer
        self.made = self.entered = self.exited = 0
        self.samples = []

    def __call__(self, model_path, **kw):
        self.made += 1
        return self

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self.exited += 1

    def classify(self, sample, cands, examples, threshold):
        self.samples.append(sample)
        folder = self.answer(sample.splitlines()[0])
        if folder is None:
            return Verdict(None, 0.2, "not sure")
        return Verdict(folder, 0.9, f"looks like {folder}")


def by_name(table):
    def answer(name_line):
        for key, folder in table.items():
            if key in name_line:
                return folder
        return None

    return answer


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    docs = tmp_path / "Documents"
    for d in [NLP, "Development", "Career", "Inbox"]:
        (docs / d).mkdir(parents=True)
    (tmp_path / "Downloads").mkdir()
    (tmp_path / ".Trash").mkdir()
    (tmp_path / "model.gguf").write_bytes(b"GGUF")
    src = Path(__file__).resolve().parent.parent / "rules.default.toml"
    (tmp_path / "rules.toml").write_text(
        src.read_text().replace("@@SCREENSHOT_DIR@@", str(tmp_path / "Pictures/Screenshots"))
    )
    (tmp_path / "rules.local.toml").write_text(
        f'[smart_sort]\nmodel_path = "{tmp_path / "model.gguf"}"\n'
    )
    return tmp_path


def state(home):
    return home / ".local/state/mac-organizer"


def config(home):
    return route.load_config(home / "rules.toml", home / "rules.local.toml")


def make_router(home, factory):
    return route.SmartRouter(config(home).smart, state(home), factory, read_only=True)


def do_plan(home, factory, *dirs):
    router = make_router(home, factory)
    return backlog.plan([home / d for d in dirs], config(home), router, time.time())


def do_apply(home, report):
    return backlog.apply(report, home / "actions.log", config=config(home))


def lines(report):
    out = []
    for ln in report.read_text().splitlines():
        if ln and not ln.startswith("#"):
            out.append(ln.split("\t"))
    return out


def by_src(report):
    return {Path(f[1]).name: f for f in lines(report)}


def rewrite(report, keep):
    """Rewrite the report, transforming each data line with ``keep(line) -> str | None``."""
    out = []
    for ln in report.read_text().splitlines():
        if ln and not ln.startswith("#"):
            new = keep(ln)
            if new is not None:
                out.append(new)
        else:
            out.append(ln)
    report.write_text("\n".join(out) + "\n")


def snapshot(home):
    """Every path under home except the state dir and the scratch files."""
    paths = set()
    for root, dirs, files in os.walk(home):
        rel = Path(root).relative_to(home)
        for n in dirs + files:
            paths.add(str(rel / n))
    return {p for p in paths if not p.startswith(".local")}


def rows(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


# ---------------------------------------------------------------- planning


def test_plan_moves_nothing(home, make_file, capsys):
    make_file(home / "Downloads/ngrams.pdf", content="ngrams")
    make_file(home / "Downloads/photo.png", content="png")
    make_file(home / "Downloads/unsure.zip", content="zip")
    make_file(home / "Downloads/Keys/id.pem", content="pem")
    make_file(home / "Downloads/package.json", content='{"name": "shop"}')
    make_file(home / "Downloads/src/index.ts", content="ts")
    make_file(home / "Documents/Inbox/lecture.pdf", content="lecture")
    before = snapshot(home)
    factory = FakeFactory(by_name({"ngrams": NLP, "lecture": NLP}))

    report = do_plan(home, factory, "Documents/Inbox", "Downloads")

    assert snapshot(home) == before
    assert report.parent == state(home) / "reports"
    assert report.name.startswith("backlog-") and report.suffix == ".tsv"
    assert str(report) in capsys.readouterr().out
    # side-effect free: no cache, no placements, no action log
    assert rows(state(home) / "verdicts.jsonl") == []
    assert rows(state(home) / "placements.jsonl") == []
    assert not (home / "actions.log").exists()
    # the model was started once and stopped
    assert (factory.made, factory.entered, factory.exited) == (1, 1, 1)
    header = [ln for ln in report.read_text().splitlines() if ln.startswith("#")]
    text = "\n".join(header)
    assert "--backlog --apply" in text and str(report) in text
    assert "delete" in text.lower() and "#" in text
    assert "MOVE" in text and "TRASH" in text and "GROUP" in text


def test_plan_does_not_start_model_when_nothing_needs_a_verdict(home, make_file):
    make_file(home / "Downloads/photo.png")
    factory = FakeFactory()
    report = do_plan(home, factory, "Downloads")
    assert factory.made == 0
    assert [f[0] for f in lines(report)] == ["MOVE"]


def test_identical_pair_in_one_run_is_move_plus_trash(home, make_file):
    """Review Focus 1."""
    make_file(home / "Downloads/X.pdf", content="same bytes")
    make_file(home / "Downloads/X -2.pdf", content="same bytes")
    report = do_plan(home, FakeFactory(lambda name: NLP), "Downloads")

    got = by_src(report)
    assert sorted(f[0] for f in got.values()) == ["MOVE", "TRASH"]
    moved = next(f for f in got.values() if f[0] == "MOVE")
    trashed = next(f for f in got.values() if f[0] == "TRASH")
    assert moved[2] == str(home / "Documents" / NLP / Path(moved[1]).name)
    assert moved[4] == NLP

    do_apply(home, report)
    nlp = sorted(p.name for p in (home / "Documents" / NLP).iterdir())
    assert nlp == [Path(moved[1]).name]
    assert (home / ".Trash" / Path(trashed[1]).name).exists()
    assert list((home / "Downloads").iterdir()) == []


def test_identical_to_existing_item_is_trash(home, make_file):
    make_file(home / "Documents" / NLP / "old.pdf", content="same")
    make_file(home / "Downloads/new.pdf", content="same")
    report = do_plan(home, FakeFactory(lambda name: NLP), "Downloads")
    assert by_src(report)["new.pdf"][0] == "TRASH"


def test_inbox_unsure_stays_and_type_rules_move(home, make_file):
    """Ruling 7."""
    make_file(home / "Documents/Inbox/lecture.pdf", content="l")
    make_file(home / "Documents/Inbox/mystery.pdf", content="m")
    make_file(home / "Documents/Inbox/script.py", content="s")
    make_file(home / "Documents/Inbox/pic.png", content="p")
    make_file(home / "Documents/Inbox/folder/inner.pdf", content="i")
    os.utime(home / "Documents/Inbox/folder", (time.time() - 3600,) * 2)
    report = do_plan(home, FakeFactory(by_name({"lecture": NLP})), "Documents/Inbox")

    got = by_src(report)
    assert set(got) == {"lecture.pdf", "pic.png"}
    assert got["lecture.pdf"][:3] == ["MOVE", str(home / "Documents/Inbox/lecture.pdf"),
                                      str(home / "Documents" / NLP / "lecture.pdf")]
    assert got["lecture.pdf"][4] == NLP
    assert got["pic.png"][2] == str(home / "Media/Images/pic.png")
    assert got["pic.png"][4] == ""


def test_plan_and_live_routing_agree(home, make_file):
    make_file(home / "Downloads/photo.png", content="p")
    make_file(home / "Downloads/ngrams.pdf", content="n")
    make_file(home / "Downloads/cv.pdf", content="c")
    make_file(home / "Downloads/sounds.mp3", content="s")
    answer = by_name({"ngrams": NLP})

    live_router = route.SmartRouter(config(home).smart, state(home), FakeFactory(answer))
    try:
        live = route.process_dir(home / "Downloads", config(home).rules, time.time(), True,
                                 home / "dry.log", smart=live_router)
    finally:
        live_router.close()
    live_dst = {a.src.name: str(a.dst) for a in live}

    report = do_plan(home, FakeFactory(answer), "Downloads")
    planned = {name: f[2] for name, f in by_src(report).items()}

    assert planned == live_dst
    assert set(planned) == {"photo.png", "ngrams.pdf", "cv.pdf", "sounds.mp3"}


def test_plan_item_is_shared_with_live_routing(home, make_file):
    png = make_file(home / "Downloads/photo.png")
    line = route.plan_item(png, config(home), None, time.time())
    assert line.action.dst == home / "Media/Images/photo.png"
    assert line.folder is None
    pdf = make_file(home / "Downloads/doc.pdf")
    assert route.plan_item(pdf, config(home), None, time.time()) is None  # smart, no router


# ---------------------------------------------------------------- apply


def test_vetoed_line_is_not_applied(home, make_file):
    make_file(home / "Downloads/a.png", content="a")
    make_file(home / "Downloads/b.png", content="b")
    make_file(home / "Downloads/c.png", content="c")
    report = do_plan(home, FakeFactory(), "Downloads")

    def veto(ln):
        name = Path(ln.split("\t")[1]).name
        if name == "a.png":
            return "#" + ln  # prefixed
        if name == "b.png":
            return None  # deleted
        return ln

    rewrite(report, veto)
    done = do_apply(home, report)

    assert [a.src.name for a in done] == ["c.png"]
    assert (home / "Downloads/a.png").exists()
    assert (home / "Downloads/b.png").exists()
    assert (home / "Media/Images/c.png").exists()


def test_apply_skips_missing_src_and_continues(home, make_file, capsys):
    make_file(home / "Downloads/a.png", content="a")
    make_file(home / "Downloads/b.png", content="b")
    report = do_plan(home, FakeFactory(), "Downloads")
    (home / "Downloads/a.png").unlink()

    done = do_apply(home, report)

    assert [a.src.name for a in done] == ["b.png"]
    assert (home / "Media/Images/b.png").exists()
    out = capsys.readouterr().out
    assert "a.png" in out and "missing" in out.lower()


def test_apply_refuses_dst_outside_home(home, make_file, tmp_path_factory):
    make_file(home / "Downloads/a.png", content="a")
    report = do_plan(home, FakeFactory(), "Downloads")
    outside = tmp_path_factory.mktemp("outside")
    rewrite(report, lambda ln: ln.replace(str(home / "Media/Images"), str(outside)))

    assert do_apply(home, report) == []
    assert (home / "Downloads/a.png").exists()
    assert list(outside.iterdir()) == []


def test_apply_never_creates_a_vanished_smart_folder(home, make_file):
    make_file(home / "Downloads/ngrams.pdf", content="n")
    report = do_plan(home, FakeFactory(lambda name: NLP), "Downloads")
    (home / "Documents" / NLP).rmdir()

    assert do_apply(home, report) == []
    assert (home / "Downloads/ngrams.pdf").exists()
    assert not (home / "Documents" / NLP).exists()


def test_apply_records_placement_and_logs(home, make_file):
    """Ruling 4: approved smart lines still teach the sorter."""
    make_file(home / "Downloads/ngrams.pdf", content="n")
    report = do_plan(home, FakeFactory(lambda name: NLP), "Downloads")
    do_apply(home, report)

    dst = home / "Documents" / NLP / "ngrams.pdf"
    assert dst.exists()
    placed = rows(state(home) / "placements.jsonl")
    assert [(r["path"], r["folder"]) for r in placed] == [(str(dst), NLP)]
    log = (home / "actions.log").read_text()
    assert f"  SMART  {home / 'Downloads/ngrams.pdf'} -> {dst}" in log


def test_apply_rejects_a_malformed_report_without_moving_anything(home, make_file):
    make_file(home / "Downloads/a.png", content="a")
    make_file(home / "Downloads/b.png", content="b")
    report = do_plan(home, FakeFactory(), "Downloads")
    rewrite(report, lambda ln: ln.replace("\t", "    ") if "b.png" in ln else ln)

    with pytest.raises(backlog.ReportError):
        do_apply(home, report)
    assert (home / "Downloads/a.png").exists()
    assert (home / "Downloads/b.png").exists()


def test_duplicate_trash_skipped_when_its_twin_was_vetoed(home, make_file):
    make_file(home / "Downloads/X.pdf", content="same")
    make_file(home / "Downloads/X -2.pdf", content="same")
    report = do_plan(home, FakeFactory(lambda name: NLP), "Downloads")
    rewrite(report, lambda ln: "#" + ln if ln.startswith("MOVE") else ln)

    do_apply(home, report)
    # the planned twin never arrived: keep both copies where they are
    assert (home / "Downloads/X.pdf").exists()
    assert (home / "Downloads/X -2.pdf").exists()


# ---------------------------------------------------------------- leftover dirs


def test_leftover_keys_dir_unpacked_to_documents_keys_and_removed_when_empty(home, make_file):
    make_file(home / "Downloads/Keys/id.pem", content="pem")
    make_file(home / "Downloads/Keys/.DS_Store", content="ds")
    report = do_plan(home, FakeFactory(), "Downloads")

    got = by_src(report)
    assert set(got) == {"id.pem"}
    assert got["id.pem"][:3] == ["MOVE", str(home / "Downloads/Keys/id.pem"),
                                 str(home / "Documents/Keys/id.pem")]

    do_apply(home, report)
    assert (home / "Documents/Keys/id.pem").exists()
    assert not (home / "Downloads/Keys").exists()


def test_leftover_dir_not_removed_if_vetoed_child_remains(home, make_file):
    make_file(home / "Downloads/Keys/a.pem", content="a")
    make_file(home / "Downloads/Keys/b.pem", content="b")
    report = do_plan(home, FakeFactory(), "Downloads")
    rewrite(report, lambda ln: "#" + ln if "b.pem" in ln else ln)

    do_apply(home, report)
    assert (home / "Documents/Keys/a.pem").exists()
    assert (home / "Downloads/Keys/b.pem").exists()


def test_leftover_documents_children_are_smart_sorted(home, make_file):
    make_file(home / "Downloads/Documents/ngrams.pdf", content="n")
    make_file(home / "Downloads/Images/cat.jpg", content="c")
    report = do_plan(home, FakeFactory(lambda name: NLP), "Downloads")
    got = by_src(report)
    assert got["ngrams.pdf"][2] == str(home / "Documents" / NLP / "ngrams.pdf")
    assert got["cat.jpg"][2] == str(home / "Media/Images/cat.jpg")
    assert "Documents" not in got and "Images" not in got


# ---------------------------------------------------------------- project groups


def _project(home, make_file, name='{"name": "@acme/shop-app"}'):
    make_file(home / "Downloads/package.json", content=name)
    make_file(home / "Downloads/package-lock.json", content="{}")
    make_file(home / "Downloads/tsconfig.json", content="{}")
    make_file(home / "Downloads/next.config.mjs", content="x")
    make_file(home / "Downloads/.gitignore", content="node_modules")
    make_file(home / "Downloads/src/index.ts", content="ts")
    make_file(home / "Downloads/components/Button.tsx", content="tsx")
    for d in ("src", "components"):
        os.utime(home / "Downloads" / d, (time.time() - 3600,) * 2)
    make_file(home / "Downloads/photo.png", content="p")


MEMBERS = {"package.json", "package-lock.json", "tsconfig.json", "next.config.mjs",
           ".gitignore", "src", "components"}


def test_project_group_named_from_package_json(home, make_file):
    _project(home, make_file)
    report = do_plan(home, FakeFactory(), "Downloads")

    got = by_src(report)
    groups = {n: f for n, f in got.items() if f[0] == "GROUP"}
    assert set(groups) == MEMBERS
    for name, f in groups.items():
        assert f[2] == str(home / "Documents/Development/acme-shop-app" / name)
    assert got["photo.png"][0] == "MOVE"
    assert not (home / "Documents/Development/acme-shop-app").exists()

    do_apply(home, report)
    proj = home / "Documents/Development/acme-shop-app"
    assert {p.name for p in proj.iterdir()} == MEMBERS
    assert (proj / "src/index.ts").exists()
    assert [p.name for p in (home / "Downloads").iterdir()] == []


def test_project_group_never_merges_into_existing_folder(home, make_file):
    """Ruling 6."""
    make_file(home / "Documents/Development/acme-shop-app/keep.txt", content="mine")
    _project(home, make_file)
    report = do_plan(home, FakeFactory(), "Downloads")
    groups = [f for f in lines(report) if f[0] == "GROUP"]
    assert all(Path(f[2]).parent.name == "acme-shop-app -2" for f in groups)

    do_apply(home, report)
    assert {p.name for p in (home / "Documents/Development/acme-shop-app").iterdir()} == {"keep.txt"}
    assert {p.name for p in (home / "Documents/Development/acme-shop-app -2").iterdir()} == MEMBERS


def test_project_group_falls_back_to_parent_folder_name(home, make_file):
    _project(home, make_file, name="not json")
    report = do_plan(home, FakeFactory(), "Downloads")
    groups = [f for f in lines(report) if f[0] == "GROUP"]
    assert groups and all(Path(f[2]).parent.name == "Downloads" for f in groups)


def test_vetoed_group_creates_no_folder(home, make_file):
    _project(home, make_file)
    report = do_plan(home, FakeFactory(), "Downloads")
    rewrite(report, lambda ln: "#" + ln if ln.startswith("GROUP") else ln)
    do_apply(home, report)
    assert not (home / "Documents/Development/acme-shop-app").exists()
    assert (home / "Downloads/package.json").exists()


# ---------------------------------------------------------------- CLI


def run_main(home, *args, factory=None):
    argv = ["--rules", str(home / "rules.toml"), "--log", str(home / "actions.log"),
            "--lock", str(home / "lock"), *[str(a) for a in args]]
    return route.main(argv, classifier_factory=factory or FakeFactory())


def test_cli_plan_then_apply(home, make_file, capsys):
    make_file(home / "Downloads/photo.png")
    assert run_main(home, "--backlog", "--dry-run", home / "Downloads") == 0
    assert (home / "Downloads/photo.png").exists()
    reports = list((state(home) / "reports").glob("backlog-*.tsv"))
    assert len(reports) == 1
    assert str(reports[0]) in capsys.readouterr().out

    assert run_main(home, "--backlog", "--apply", reports[0]) == 0
    assert (home / "Media/Images/photo.png").exists()
    out = capsys.readouterr().out
    assert "1 moved" in out


def test_cli_plan_without_dry_run_still_moves_nothing(home, make_file):
    make_file(home / "Downloads/photo.png")
    assert run_main(home, "--backlog", home / "Downloads") == 0
    assert (home / "Downloads/photo.png").exists()


def test_cli_apply_without_backlog_is_an_error(home, tmp_path):
    with pytest.raises(SystemExit) as exc:
        run_main(home, "--apply", tmp_path / "r.tsv")
    assert exc.value.code != 0


def test_cli_backlog_refuses_when_locked(home, make_file, capsys):
    make_file(home / "Downloads/photo.png")
    fd = route.acquire_lock(home / "lock")
    try:
        assert run_main(home, "--backlog", home / "Downloads") != 0
    finally:
        os.close(fd)
    assert "already running" in capsys.readouterr().err
    assert not (state(home) / "reports").exists()


# ---------------------------------------------------------------- fix round 1


def _append(report, *rows):
    with open(report, "a") as fh:
        for r in rows:
            fh.write("\t".join(str(x) for x in r) + "\n")


def test_apply_rejects_lines_the_planner_could_not_have_produced(home, make_file, capsys):
    make_file(home / "Downloads/a.png", content="a")
    report = do_plan(home, FakeFactory(), "Downloads")
    thesis = make_file(home / "Documents/Thesis/ch1.tex", content="t").parent
    ssh = make_file(home / ".ssh/id_ed25519", content="key")
    cv = make_file(home / "Documents/Career/cv.pdf", content="cv")
    b = make_file(home / "Downloads/b.png", content="b")
    other = make_file(home / "Downloads/c.png", content="different")
    loose = make_file(home / "Downloads/x.json", content="{}")
    dev = home / "Documents/Development"
    _append(report,
            ["MOVE", thesis, home / "Library/Caches/new/sub/Thesis", "r", ""],
            ["MOVE", ssh, home / "Media/Images/id_ed25519", "r", ""],
            ["TRASH", cv, home / ".Trash/cv.pdf", "r", ""],
            ["GROUP", thesis, dev / "foo/Thesis", "r", ""],
            ["GROUP", loose, f"{dev}/../x.json", "r", ""],
            ["MOVE", b, home / "Documents/Career/b.png", "r", ""],  # not where the rules send it
            ["MOVE", b, home / "Documents/Nope/b.png", "r", "Nope"],  # not a sort folder
            ["MOVE", b, home / "Documents/Career/renamed.png", "r", "Career"],  # renamed
            ["TRASH", b, other, "identical to c.png", ""])  # not identical

    done = do_apply(home, report)

    assert [a.src.name for a in done] == ["a.png"]
    for item in (thesis, ssh, cv, b, other, loose):
        assert item.exists()
    assert not (home / "Library/Caches").exists()
    assert sorted(p.name for p in dev.iterdir()) == []
    assert sorted(p.name for p in (home / ".Trash").iterdir()) == []
    out = capsys.readouterr().out
    assert out.count("REJECTED") == 9
    assert "9 rejected" in out


def test_apply_skips_src_changed_since_planning(home, make_file, capsys):
    make_file(home / "Downloads/a.png", content="a")
    make_file(home / "Downloads/data/rows.csv", content="r")
    os.utime(home / "Downloads/data", (time.time() - 3600,) * 2)
    report = do_plan(home, FakeFactory(lambda name: NLP), "Downloads")
    assert {f[0] for f in lines(report)} == {"MOVE"} and len(lines(report)) == 2
    # pretend the plan is two hours old: both items changed after it
    text = report.read_text()
    stamp = next(ln for ln in text.splitlines() if ln.startswith("# planned-at: "))
    old = float(stamp.split(": ")[1]) - 7200
    report.write_text(text.replace(stamp, f"# planned-at: {old!r}"))

    assert do_apply(home, report) == []
    assert (home / "Downloads/a.png").exists()
    assert (home / "Downloads/data/rows.csv").exists()
    out = capsys.readouterr().out
    assert out.count("changed since planning") == 2


def test_apply_skips_items_should_skip_rejects(home, make_file, capsys):
    report = do_plan(home, FakeFactory(), "Downloads")
    secret = make_file(home / "Downloads/.secret.png", content="s")
    _append(report, ["MOVE", secret, home / "Media/Images/.secret.png", "r", ""])
    assert do_apply(home, report) == []
    assert secret.exists()
    assert "SKIPPED" in capsys.readouterr().out


def test_unexpected_error_on_one_line_is_failed_and_the_rest_runs(home, make_file, monkeypatch,
                                                                  capsys):
    make_file(home / "Downloads/Keys/a.pem", content="a")
    make_file(home / "Downloads/Keys/b.pem", content="b")
    report = do_plan(home, FakeFactory(), "Downloads")
    real = route.apply_action

    def flaky(action, dry_run, log_path):
        if action.src.name == "a.pem":
            raise RuntimeError("boom")
        return real(action, dry_run, log_path)

    monkeypatch.setattr(route, "apply_action", flaky)
    done = do_apply(home, report)
    assert [a.src.name for a in done] == ["b.pem"]
    out = capsys.readouterr().out
    assert "FAILED" in out and "1 failed" in out and "1 moved" in out
    assert "KEPT" in out  # leftover cleanup still ran


def test_cli_malformed_report_moves_nothing(home, make_file, capsys):
    make_file(home / "Downloads/a.png", content="a")
    report = do_plan(home, FakeFactory(), "Downloads")
    report.write_text(report.read_text().replace("\t", "    "))
    assert run_main(home, "--backlog", "--apply", report) == 2
    assert "nothing moved" in capsys.readouterr().err
    assert (home / "Downloads/a.png").exists()


# ---------------------------------------------------------------- final review


def test_plan_and_apply_never_hash_big_unrelated_sibling_folders(home, make_file, monkeypatch):
    """C1: a folder headed for ~/Documents/Development is not compared byte by byte
    with every project already there."""
    for i in range(20):
        make_file(home / f"Documents/Development/big/src/f{i}.py", content="y" * 100)
    make_file(home / "Documents/Development/big/README.md", content="r")
    make_file(home / "Downloads/proj/README.md", content="r")
    make_file(home / "Downloads/proj/src/a.py", content="a")
    os.utime(home / "Downloads/proj", (time.time() - 3600,) * 2)
    os.utime(home / "Downloads/proj/src", (time.time() - 3600,) * 2)
    import dedupe
    hashed = []
    real = dedupe.file_digest
    monkeypatch.setattr(dedupe, "file_digest", lambda p: hashed.append(p) or real(p))

    report = do_plan(home, FakeFactory(lambda name: "Development"), "Downloads")
    assert by_src(report)["proj"][0] == "MOVE"
    do_apply(home, report)

    assert (home / "Documents/Development/proj/src/a.py").exists()
    assert not any("big" in str(p) for p in hashed)


def _live_sweep(home, factory):
    from batch import NullNotifier
    argv = ["--rules", str(home / "rules.toml"), "--log", str(home / "actions.log"),
            "--lock", str(home / "lock")]
    return route.main(argv, classifier_factory=factory, notifier=NullNotifier())


def test_vetoes_of_downloads_items_stick_in_live_sweeps(home, make_file):
    """I4 / Ruling 23: what the user vetoed is not moved by the next sweep anyway."""
    make_file(home / "Downloads/a.png", content="a")
    make_file(home / "Downloads/b.png", content="b")
    make_file(home / "Downloads/c.png", content="c")
    make_file(home / "Downloads/notes.pdf", content="n")
    make_file(home / "Downloads/Keys/k.pem", content="k")
    make_file(home / "Downloads/Keys/j.pem", content="j")
    make_file(home / "Documents/Inbox/lecture.pdf", content="l")
    _project(home, make_file)
    factory = FakeFactory(lambda name: NLP)
    report = do_plan(home, factory, "Documents/Inbox", "Downloads")
    vetoed = {"a.png", "notes.pdf", "k.pem", "src", "lecture.pdf"}

    def veto(ln):
        name = Path(ln.split("\t")[1]).name
        if name == "b.png":
            return None  # deleted
        return "#" + ln if name in vetoed else ln

    rewrite(report, veto)
    do_apply(home, report)
    assert (home / "Media/Images/c.png").exists()
    assert (home / "Documents/Keys/j.pem").exists()

    _live_sweep(home, FakeFactory(lambda s: "Development"))

    for kept in ("a.png", "b.png", "notes.pdf", "Keys/k.pem", "src/index.ts"):
        assert (home / "Downloads" / kept).exists(), kept
    assert (home / "Downloads/Keys").is_dir()  # the leftover folder holding a vetoed item
    assert (home / "Documents/Inbox/lecture.pdf").exists()

    # once an item changes, it is sorted normally again
    later = time.time() - 600
    os.utime(home / "Downloads/a.png", (later, later))
    _live_sweep(home, FakeFactory(lambda s: "Development"))
    assert (home / "Media/Images/a.png").exists()
    assert (home / "Downloads/b.png").exists()


def test_report_header_says_vetoes_are_remembered(home, make_file):
    make_file(home / "Downloads/a.png")
    report = do_plan(home, FakeFactory(), "Downloads")
    header = "\n".join(ln for ln in report.read_text().splitlines() if ln.startswith("#"))
    assert "remembered" in header and "until they change" in header
