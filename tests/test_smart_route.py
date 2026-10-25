"""Task 7: the smart action wired into routing (fake classifier, fake notifier)."""

import json
import os
import shutil
import signal
import time
from pathlib import Path

import pytest

import classify
import route
from batch import Batch
from classify import ServerUnavailable, Verdict

NLP = "Academics/Natural Language Processing"


# ---------------------------------------------------------------- fakes


class FakeNotifier:
    def __init__(self):
        self.calls = []
        self.batches = []

    def start(self):
        self.calls.append("start")

    def summary(self, batch):
        self.calls.append("summary")
        self.batches.append(batch)

    def unavailable(self):
        self.calls.append("unavailable")


class FakeFactory:
    """Stands in for ``LlamaServer``: a context manager with ``classify``.

    ``answer(sample, call_no)`` returns a folder name, None (unsure), or raises.
    """

    def __init__(self, answer=lambda sample, n: None, enter_error=None):
        self.answer = answer
        self.enter_error = enter_error
        self.made = 0
        self.entered = 0
        self.exited = 0
        self.samples = []
        self.kwargs = []

    def __call__(self, model_path, **kw):
        self.made += 1
        self.kwargs.append(kw)
        return self

    def __enter__(self):
        self.entered += 1
        if self.enter_error is not None:
            raise self.enter_error
        return self

    def __exit__(self, *exc):
        self.exited += 1

    def classify(self, sample, cands, examples, threshold):
        self.samples.append(sample)
        folder = self.answer(sample, len(self.samples))
        if folder is None:
            return Verdict(None, 0.2, "not sure")
        return Verdict(folder, 0.9, f"looks like {folder}")


def by_name(table):
    """Answer by the first key found in the sample's ``Name:`` line."""

    def answer(sample, n):
        name = sample.splitlines()[0]
        for key, folder in table.items():
            if key in name:
                return folder
        return None

    return answer


# ---------------------------------------------------------------- setup


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    docs = tmp_path / "Documents"
    for d in [NLP, "Development", "Career", "Inbox"]:
        (docs / d).mkdir(parents=True)
    (tmp_path / "Downloads").mkdir()
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


def settings(home, enabled=True, model=True):
    return route.SmartSettings(
        enabled=enabled,
        threshold=0.6,
        model_path=home / "model.gguf" if model else None,
        documents_root=home / "Documents",
    )


def run_main(home, factory, notifier, *paths):
    argv = ["--rules", str(home / "rules.toml"), "--log", str(home / "actions.log"),
            "--lock", str(home / "lock"), *[str(p) for p in paths]]
    return route.main(argv, classifier_factory=factory, notifier=notifier)


def route_dir(home, router, directory="Downloads"):
    rules = route.load_config(home / "rules.toml", home / "rules.local.toml").rules
    try:
        return route.process_dir(home / directory, rules, time.time(), False,
                                 home / "actions.log", smart=router)
    finally:
        router.close()


def log_text(home):
    p = home / "actions.log"
    return p.read_text() if p.exists() else ""


def rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------- Batch


def test_batch_notable_only_for_smart_routed_entries():
    b = Batch()
    b.add(Path("a.png"), Path("x/a.png"), None, "rule")
    b.add(Path("b.png"), Path("x/b.png"), None, "duplicate")  # rule-routed duplicate
    b.add(Path("c.zip"), Path("c.zip"), None, "stay", smart=True)
    assert not b.notable
    b.add(Path("d.pdf"), Path("Inbox/d.pdf"), None, "inbox", smart=True)
    assert b.notable
    dup = Batch()
    dup.add(Path("e.pdf"), Path("NLP/e.pdf"), None, "duplicate", smart=True)
    assert dup.notable
    assert dup.ai_unavailable is False


# ---------------------------------------------------------------- routing


def test_unplaced_pdf_moves_to_chosen_course(home, make_file):
    src = make_file(home / "Downloads/lecture3-ngrams.pdf")
    factory, notifier = FakeFactory(by_name({"ngrams": NLP})), FakeNotifier()

    assert run_main(home, factory, notifier) == 0

    dst = home / "Documents" / NLP / "lecture3-ngrams.pdf"
    assert dst.exists() and not src.exists()
    assert f"  SMART  {src} -> {dst}" in log_text(home)
    placed = rows(state(home) / "placements.jsonl")
    assert [(r["path"], r["folder"]) for r in placed] == [(str(dst), NLP)]
    assert notifier.calls == ["start", "summary"]
    [entry] = notifier.batches[0].entries
    assert (entry.src, entry.dst, entry.folder, entry.kind) == (src, dst, NLP, "smart")
    assert entry.reason == f"looks like {NLP}"
    assert factory.kwargs == [{"state_dir": state(home)}]


def test_unsure_pdf_goes_to_documents_inbox(home, make_file):
    src = make_file(home / "Downloads/mystery.pdf")
    factory, notifier = FakeFactory(), FakeNotifier()

    run_main(home, factory, notifier)

    dst = home / "Documents/Inbox/mystery.pdf"
    assert dst.exists()
    assert f"  MOVE  {src} -> {dst}" in log_text(home)
    assert rows(state(home) / "placements.jsonl") == []
    assert rows(state(home) / "verdicts.jsonl") == []
    assert [e.kind for e in notifier.batches[0].entries] == ["inbox"]


def test_unsure_zip_stays_and_is_cached(home, make_file):
    z = make_file(home / "Downloads/stuff.zip")
    factory, notifier = FakeFactory(), FakeNotifier()

    run_main(home, factory, notifier)

    assert z.exists()
    assert [r["path"] for r in rows(state(home) / "verdicts.jsonl")] == [str(z)]
    assert len(factory.samples) == 1
    # "Files are being sorted…" is always followed by a summary, even if nothing moved
    assert notifier.calls == ["start", "summary"]
    assert [e.kind for e in notifier.batches[0].entries] == ["stay"]


def test_cached_unsure_items_do_not_start_server_or_notify(home, make_file):
    """Review Focus 5: a quiet sweep over cached unsure items stays quiet."""
    make_file(home / "Downloads/stuff.zip")
    (home / "Downloads/project").mkdir()
    make_file(home / "Downloads/project/main.py")
    old = time.time() - 3600
    os.utime(home / "Downloads/project", (old, old))
    run_main(home, FakeFactory(), FakeNotifier())
    assert sorted(r["path"] for r in rows(state(home) / "verdicts.jsonl")) == [
        str(home / "Downloads/project"), str(home / "Downloads/stuff.zip")]

    factory, notifier = FakeFactory(), FakeNotifier()
    run_main(home, factory, notifier)

    assert factory.made == 0 and factory.entered == 0
    assert notifier.calls == []
    assert (home / "Downloads/stuff.zip").exists()
    assert (home / "Downloads/project").is_dir()


def test_server_started_once_for_many_items_and_closed(home, make_file):
    for i in range(5):
        make_file(home / f"Downloads/ngrams-{i}.pdf", content=f"lecture {i}")
    factory, notifier = FakeFactory(by_name({"ngrams": NLP})), FakeNotifier()

    run_main(home, factory, notifier)

    assert (factory.made, factory.entered, factory.exited) == (1, 1, 1)
    assert len(factory.samples) == 5
    assert notifier.calls == ["start", "summary"]
    assert len(list((home / "Documents" / NLP).iterdir())) == 5


def test_server_crash_mid_batch_routes_rest_to_fallback_and_flags_unavailable(home, make_file):
    """Review Focus 2: the server dies on the second item."""
    for name in ["a1.pdf", "a2.pdf", "a3.pdf", "z.zip"]:
        make_file(home / "Downloads" / name, content=name)

    def answer(sample, n):
        if n == 2:
            raise ServerUnavailable("llama-server exited with code -11")
        return NLP

    factory, notifier = FakeFactory(answer), FakeNotifier()
    run_main(home, factory, notifier)

    assert (home / "Documents" / NLP / "a1.pdf").exists()
    assert (home / "Documents/Inbox/a2.pdf").exists()
    assert (home / "Documents/Inbox/a3.pdf").exists()
    assert (home / "Downloads/z.zip").exists()
    assert len(factory.samples) == 2  # no retry, no restart
    assert (factory.entered, factory.exited) == (1, 1)
    assert rows(state(home) / "verdicts.jsonl") == []  # never cache an outage
    assert notifier.calls == ["start", "summary"]
    batch = notifier.batches[0]
    assert batch.ai_unavailable is True
    assert [e.kind for e in batch.entries] == ["smart", "inbox", "inbox", "stay"]


def test_server_unavailable_at_start_routes_all_to_fallback(home, make_file):
    make_file(home / "Downloads/a.pdf", content="a")
    make_file(home / "Downloads/b.pdf", content="b")
    factory = FakeFactory(enter_error=ServerUnavailable("not healthy after 120s"))
    notifier = FakeNotifier()

    run_main(home, factory, notifier)

    assert (home / "Documents/Inbox/a.pdf").exists()
    assert (home / "Documents/Inbox/b.pdf").exists()
    assert factory.entered == 1  # tried once, not once per item
    # final review I1: "being sorted" is sent only once the server is up
    assert notifier.calls == ["summary"]
    assert notifier.batches[0].ai_unavailable is True


def test_rule_only_batch_is_silent(home, make_file):
    shots = home / "Pictures/Screenshots"
    make_file(shots / "Screenshot 2026-03-09 at 14.05.06.png")
    make_file(home / "Downloads/cat.png", content="cat")
    make_file(home / "Media/Images/cat-old.png", content="cat")  # rule-routed duplicate
    factory, notifier = FakeFactory(), FakeNotifier()

    run_main(home, factory, notifier)

    assert not (home / "Downloads/cat.png").exists()
    assert list(shots.iterdir()) == []
    assert factory.made == 0
    assert notifier.calls == []


def test_smart_move_never_creates_folder(home, make_file):
    src = make_file(home / "Downloads/cv.pdf")
    career = home / "Documents/Career"

    def answer(sample, n):
        shutil.rmtree(career)  # the user deletes the folder mid-run
        return "Career"

    router = route.SmartRouter(settings(home), state(home), FakeFactory(answer), FakeNotifier())
    route_dir(home, router)

    assert not career.exists()
    assert not src.exists()
    assert (home / "Documents/Inbox/cv.pdf").exists()
    assert [e.kind for e in router.batch.entries] == ["inbox"]


def test_duplicate_at_smart_destination_goes_to_trash(home, make_file):
    """Ruling 7: a duplicate is recorded as such and never as a placement."""
    existing = make_file(home / "Documents" / NLP / "ngrams.pdf", content="same")
    src = make_file(home / "Downloads/ngrams (1).pdf", content="same")
    factory, notifier = FakeFactory(by_name({"ngrams": NLP})), FakeNotifier()

    run_main(home, factory, notifier)

    assert not src.exists()
    assert (home / ".Trash/ngrams (1).pdf").read_text() == "same"
    assert f"  DUPLICATE  {src} -> {existing}" in log_text(home)
    assert rows(state(home) / "placements.jsonl") == []
    [entry] = notifier.batches[0].entries
    assert (entry.kind, entry.dst, entry.folder) == ("duplicate", existing, NLP)
    assert notifier.calls == ["start", "summary"]


def test_desktop_dir_never_smart_routed(home, make_file):
    make_file(home / "Desktop/project/notes.pdf")
    old = time.time() - 3600
    os.utime(home / "Desktop/project", (old, old))
    # even a (mis)configured smart dir rule on the Desktop is ignored
    with open(home / "rules.local.toml", "a") as fh:
        fh.write(f'\n[[rule]]\nsource = "{home}/Desktop"\nkind = "dir"\naction = "smart"\ndest = ""\n')
    factory, notifier = FakeFactory(lambda s, n: "Development"), FakeNotifier()

    run_main(home, factory, notifier, home / "Desktop")

    assert (home / "Desktop/project/notes.pdf").exists()
    assert factory.made == 0
    assert notifier.calls == []


# ---------------------------------------------------------------- main


def test_main_detects_corrections_once_before_routing(home, make_file, monkeypatch):
    make_file(home / "Downloads/ngrams.pdf")
    events = []
    monkeypatch.setattr(route.memory, "detect_corrections",
                        lambda st, cands, now, **kw: events.append(("detect", st, [c.name for c in cands])) or [])

    def answer(sample, n):
        events.append(("classify",))
        return NLP

    run_main(home, FakeFactory(answer), FakeNotifier())

    assert events[0] == ("detect", state(home), ["Academics", NLP, "Career", "Development"])
    assert events[1:] == [("classify",)]


def test_dry_run_never_starts_model(home, make_file):
    src = make_file(home / "Downloads/ngrams.pdf")
    factory, notifier = FakeFactory(lambda s, n: NLP), FakeNotifier()

    route.main(["--dry-run", "--rules", str(home / "rules.toml"), "--log", str(home / "actions.log"),
                "--lock", str(home / "lock")], classifier_factory=factory, notifier=notifier)

    assert src.exists()
    assert factory.made == 0 and notifier.calls == []


def test_disabled_smart_sort_is_silent_fallback(home, make_file):
    """Fix round 1: AI turned off on purpose is not an outage."""
    (home / "rules.local.toml").write_text(
        f'[smart_sort]\nenabled = false\nmodel_path = "{home / "model.gguf"}"\n')
    make_file(home / "Downloads/stuff.zip")
    make_file(home / "Downloads/a.pdf")
    factory, notifier = FakeFactory(lambda s, n: NLP), FakeNotifier()

    run_main(home, factory, notifier)
    run_main(home, factory, notifier)

    assert factory.made == 0
    assert (home / "Downloads/stuff.zip").exists()
    assert (home / "Documents/Inbox/a.pdf").exists()
    assert rows(state(home) / "verdicts.jsonl") == []
    assert notifier.calls == ["summary"]  # the Inbox move; never "unavailable"
    assert notifier.batches[0].ai_unavailable is False
    assert not (state(home) / "unavailable-notified").exists()


def test_missing_model_falls_back_and_flags_unavailable_once_per_day(home, make_file):
    (home / "model.gguf").unlink()
    make_file(home / "Downloads/stuff.zip")
    make_file(home / "Downloads/a.pdf", content="a")
    first = FakeNotifier()
    factory = FakeFactory(lambda s, n: NLP)

    run_main(home, factory, first)

    assert factory.made == 0
    assert (home / "Downloads/stuff.zip").exists()
    assert (home / "Documents/Inbox/a.pdf").exists()
    assert rows(state(home) / "verdicts.jsonl") == []
    assert first.calls == ["summary"]  # no "sorting…" start: nothing is started
    assert first.batches[0].ai_unavailable is True

    # same day: another Inbox move is summarised, but the outage is not repeated
    make_file(home / "Downloads/b.pdf", content="b")
    second = FakeNotifier()
    run_main(home, factory, second)
    assert second.calls == ["summary"]
    assert second.batches[0].ai_unavailable is False


def test_permanent_unavailable_notice_at_most_once_per_day(home, make_file):
    (home / "model.gguf").unlink()
    make_file(home / "Downloads/stuff.zip")
    first, second = FakeNotifier(), FakeNotifier()

    run_main(home, FakeFactory(), first)
    run_main(home, FakeFactory(), second)

    assert first.calls == ["unavailable"]
    assert second.calls == []
    stamp = state(home) / "unavailable-notified"
    stamp.write_text(str(time.time() - 86401))
    third = FakeNotifier()
    run_main(home, FakeFactory(), third)
    assert third.calls == ["unavailable"]


def test_server_crash_flag_throttled_with_notice(home, make_file):
    """A crash within a day of the last outage notice still summarises, without the flag."""
    state(home).mkdir(parents=True)
    (state(home) / "unavailable-notified").write_text(str(time.time() - 60))
    make_file(home / "Downloads/a.pdf")

    def answer(sample, n):
        raise ServerUnavailable("dead")

    notifier = FakeNotifier()
    run_main(home, FakeFactory(answer), notifier)

    assert (home / "Documents/Inbox/a.pdf").exists()
    assert notifier.calls == ["start", "summary"]
    assert notifier.batches[0].ai_unavailable is False


def test_candidate_vanishing_after_decision_takes_fallback(home, make_file, monkeypatch):
    """Fix round 1: the folder disappears between the verdict and the move."""
    src = make_file(home / "Downloads/cv.pdf")
    zsrc = make_file(home / "Downloads/cv.zip", content="zip")
    career = home / "Documents/Career"
    router = route.SmartRouter(settings(home), state(home), FakeFactory(lambda s, n: "Career"),
                               FakeNotifier())
    real_route = router.route

    def route_then_delete(path, rule, now):
        action = real_route(path, rule, now)
        if career.exists():
            shutil.rmtree(career)
        return action

    monkeypatch.setattr(router, "route", route_then_delete)
    route_dir(home, router)

    assert not career.exists()
    assert not src.exists()
    assert (home / "Documents/Inbox/cv.pdf").exists()
    assert zsrc.exists()  # no fallback for archives: it stays
    assert rows(state(home) / "verdicts.jsonl") == []
    assert rows(state(home) / "placements.jsonl") == []
    assert sorted(e.kind for e in router.batch.entries) == ["inbox", "stay"]


def test_apply_action_no_mkdir_raises_and_leaves_item(home, make_file):
    src = make_file(home / "Downloads/cv.pdf")
    action = route.Action(src=src, dst=home / "Documents/Gone/cv.pdf", verb="SMART", no_mkdir=True)
    with pytest.raises(route.DestDirMissing):
        route.apply_action(action, False, home / "actions.log")
    assert src.exists()
    assert not (home / "Documents/Gone").exists()


def test_unexpected_error_for_one_item_is_unsure_uncached_and_run_continues(home, make_file):
    """Fix round 1: any exception on the smart path is contained to that item."""
    make_file(home / "Downloads/a-bad.pdf", content="a")
    make_file(home / "Downloads/b-bad.zip", content="b")
    make_file(home / "Downloads/c-ngrams.pdf", content="c")

    def answer(sample, n):
        if "bad" in sample.splitlines()[0]:
            raise RuntimeError("tokenizer exploded")
        return NLP

    factory, notifier = FakeFactory(answer), FakeNotifier()
    assert run_main(home, factory, notifier) == 0

    assert (home / "Documents/Inbox/a-bad.pdf").exists()
    assert (home / "Downloads/b-bad.zip").exists()
    assert (home / "Documents" / NLP / "c-ngrams.pdf").exists()
    assert rows(state(home) / "verdicts.jsonl") == []
    assert (factory.entered, factory.exited) == (1, 1)  # the server was not given up on
    log = log_text(home)
    assert f"  ERROR  {home / 'Downloads/a-bad.pdf'} -> -" in log
    assert f"  ERROR  {home / 'Downloads/b-bad.zip'} -> -" in log
    assert notifier.batches[0].ai_unavailable is False


def test_detect_corrections_failure_is_logged_and_routing_continues(home, make_file, monkeypatch, capsys):
    make_file(home / "Downloads/ngrams.pdf")

    def boom(*a, **kw):
        raise RuntimeError("corrupt state")

    monkeypatch.setattr(route.memory, "detect_corrections", boom)
    assert run_main(home, FakeFactory(lambda s, n: NLP), FakeNotifier()) == 0
    assert (home / "Documents" / NLP / "ngrams.pdf").exists()
    assert "corrupt state" in capsys.readouterr().err


def test_sigterm_mid_batch_stops_the_model(home, make_file):
    """Ruling 10a: launchd's SIGTERM must still run the server's __exit__."""
    make_file(home / "Downloads/a.pdf", content="a")
    make_file(home / "Downloads/b.pdf", content="b")
    before = signal.getsignal(signal.SIGTERM)

    def answer(sample, n):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.5)  # the handler fires here
        return NLP

    factory = FakeFactory(answer)
    with pytest.raises(SystemExit):
        run_main(home, factory, FakeNotifier())

    assert (factory.entered, factory.exited) == (1, 1)
    assert len(factory.samples) == 1
    assert signal.getsignal(signal.SIGTERM) == before


def test_notifier_failure_never_breaks_routing(home, make_file):
    make_file(home / "Downloads/ngrams.pdf")

    class Broken(FakeNotifier):
        def start(self):
            raise RuntimeError("no notification centre")

        def summary(self, batch):
            raise RuntimeError("no notification centre")

    assert run_main(home, FakeFactory(lambda s, n: NLP), Broken()) == 0
    assert (home / "Documents" / NLP / "ngrams.pdf").exists()


def test_router_passes_threshold_candidates_and_examples(home, make_file):
    make_file(home / "Downloads/ngrams.pdf")
    seen = {}

    class Spy(FakeFactory):
        def classify(self, sample, cands, examples, threshold):
            seen.update(sample=sample, cands=[c.name for c in cands], threshold=threshold,
                        examples=examples)
            return Verdict(None, 0.1, "x")

    router = route.SmartRouter(settings(home), state(home), Spy(), FakeNotifier())
    route_dir(home, router)

    assert seen["sample"].startswith("Name: ngrams.pdf")
    assert seen["cands"] == ["Academics", NLP, "Career", "Development"]
    assert seen["threshold"] == 0.6
    assert seen["examples"] == []


def test_classify_module_default_factory(home):
    router = route.SmartRouter(settings(home), state(home))
    assert router.factory is classify.LlamaServer
    router.close()  # closing an unstarted router is a no-op


def test_sigterm_restore_handles_none_and_sig_dfl():
    """Fix round 1: a previous handler of None/SIG_DFL is restored, not left as ours."""
    original = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        prev = route._install_sigterm()
        assert signal.getsignal(signal.SIGTERM) is route._raise_exit
        route._restore_sigterm(prev)
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL

        route._install_sigterm()
        route._restore_sigterm(None)  # handler not installed from Python
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    finally:
        signal.signal(signal.SIGTERM, original)


def test_sigterm_not_installed_off_main_thread():
    import threading
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("prev", route._install_sigterm()))
    t.start()
    t.join()
    before = signal.getsignal(signal.SIGTERM)
    route._restore_sigterm(out["prev"])  # the sentinel: a no-op
    assert out["prev"] is route._SIGTERM_NOT_INSTALLED
    assert signal.getsignal(signal.SIGTERM) == before


def test_shipped_local_template_leaves_model_unset_and_falls_back(home, make_file):
    """Fresh install: rules.local.default.toml sets no model_path -> 'model missing'."""
    tmpl = Path(__file__).resolve().parent.parent / "rules.local.default.toml"
    (home / "rules.local.toml").write_text(tmpl.read_text())
    make_file(home / "Downloads/a.pdf", content="a")
    notifier = FakeNotifier()
    factory = FakeFactory(lambda s, n: NLP)

    run_main(home, factory, notifier)

    assert factory.made == 0
    assert (home / "Documents/Inbox/a.pdf").exists()
    assert notifier.batches[0].ai_unavailable is True


# ---------------------------------------------------------------- final review I1


def _all_calls(*notifiers):
    return [c for n in notifiers for c in n.calls]


def test_server_failing_to_start_backs_off_for_a_day(home, make_file):
    """Three sweeps with a model that never starts: one start attempt, at most one notice."""
    make_file(home / "Downloads/stuff.zip")
    factory = FakeFactory(enter_error=ServerUnavailable("not healthy after 120s"))
    notifiers = [FakeNotifier() for _ in range(3)]

    for n in notifiers:
        assert run_main(home, factory, n) == 0

    assert factory.entered == 1
    calls = _all_calls(*notifiers)
    assert "start" not in calls
    assert len(calls) <= 1
    assert calls == ["unavailable"]
    assert (home / "Downloads/stuff.zip").exists()
    assert rows(state(home) / "verdicts.jsonl") == []
    assert (state(home) / route.BACKOFF_STAMP).exists()

    # a day later the model is tried again
    (state(home) / route.BACKOFF_STAMP).write_text(str(time.time() - 86401))
    run_main(home, factory, FakeNotifier())
    assert factory.entered == 2


def test_backoff_routes_fallbacks_without_starting_the_model(home, make_file):
    state(home).mkdir(parents=True)
    (state(home) / route.BACKOFF_STAMP).write_text(str(time.time() - 60))
    make_file(home / "Downloads/a.pdf", content="a")
    factory, notifier = FakeFactory(lambda s, n: NLP), FakeNotifier()

    run_main(home, factory, notifier)

    assert factory.made == 0
    assert (home / "Documents/Inbox/a.pdf").exists()
    assert notifier.calls == ["summary"]
    assert notifier.batches[0].ai_unavailable is True


def test_start_notice_only_after_server_is_up(home, make_file):
    make_file(home / "Downloads/stuff.zip")
    events = []

    class Factory(FakeFactory):
        def __enter__(self):
            events.append("enter")
            return super().__enter__()

    class Notifier(FakeNotifier):
        def start(self):
            events.append("start")
            super().start()

    run_main(home, Factory(), Notifier())
    assert events == ["enter", "start"]


def test_http_4xx_is_unsure_for_that_item_only(home, make_file):
    make_file(home / "Downloads/a-bad.zip", content="a")
    make_file(home / "Downloads/b-ngrams.pdf", content="b")

    def answer(sample, n):
        if "bad" in sample.splitlines()[0]:
            raise classify.ItemRejected("llama-server HTTP 400")
        return NLP

    factory, notifier = FakeFactory(answer), FakeNotifier()
    run_main(home, factory, notifier)

    assert (home / "Downloads/a-bad.zip").exists()
    assert (home / "Documents" / NLP / "b-ngrams.pdf").exists()
    assert len(factory.samples) == 2  # the server was not given up on
    assert rows(state(home) / "verdicts.jsonl") == []  # not cached
    assert notifier.batches[0].ai_unavailable is False  # not an outage
    assert not (state(home) / "unavailable-notified").exists()


@pytest.mark.parametrize("error", [RuntimeError("tokenizer exploded"),
                                   classify.ItemRejected("llama-server HTTP 400")])
def test_item_failing_three_times_is_left_alone_until_it_changes(home, make_file, error):
    bad = make_file(home / "Downloads/bad.zip", content="a")

    def answer(sample, n):
        raise error

    factory = FakeFactory(answer)
    notifiers = [FakeNotifier() for _ in range(5)]
    for n in notifiers:
        run_main(home, factory, n)

    assert factory.entered == 3
    assert len(factory.samples) == 3
    assert _all_calls(*notifiers[3:]) == []
    assert bad.exists()
    assert rows(state(home) / "verdicts.jsonl") == []

    later = time.time() - 600
    os.utime(bad, (later, later))  # the item changed: try again
    run_main(home, factory, FakeNotifier())
    assert len(factory.samples) == 4


def test_successful_verdict_resets_the_failure_count(home, make_file):
    bad = make_file(home / "Downloads/bad.zip", content="a")
    outcomes = iter([RuntimeError("1"), RuntimeError("2"), None, RuntimeError("3"),
                     RuntimeError("4"), RuntimeError("5")])

    def answer(sample, n):
        out = next(outcomes)
        if out is not None:
            raise out
        return None  # a genuine unsure, which is cached ...

    factory = FakeFactory(answer)
    for _ in range(3):
        run_main(home, factory, FakeNotifier())
    assert len(factory.samples) == 3
    # ... so the cache now holds it; drop the cache to see the counter restart
    (state(home) / "verdicts.jsonl").unlink()
    for _ in range(3):
        run_main(home, factory, FakeNotifier())
    assert len(factory.samples) == 6
    assert bad.exists()


def test_symlinked_folder_in_downloads_is_never_an_item(home, make_file):
    target = home / "Elsewhere/project"
    make_file(target / "main.py")
    old = time.time() - 3600
    os.utime(target, (old, old))
    (home / "Downloads/project-link").symlink_to(target, target_is_directory=True)
    factory, notifier = FakeFactory(lambda s, n: "Development"), FakeNotifier()

    run_main(home, factory, notifier)

    assert (home / "Downloads/project-link").is_symlink()
    assert factory.made == 0
    assert notifier.calls == []


def test_dir_rule_never_matches_a_symlink(tmp_path):
    now = time.time()
    real = tmp_path / "Foo.app"
    real.mkdir()
    link = tmp_path / "Bar.app"
    link.symlink_to(real, target_is_directory=True)
    rule = route.Rule(source=tmp_path, action="move", ext=frozenset({"app"}), kind="dir")
    os.utime(real, (now - 3600, now - 3600))
    assert route.rule_matches(rule, real, now)
    assert not route.rule_matches(rule, link, now)


# ---------------------------------------------------------------- final review I2


def _spy_settle(monkeypatch):
    walked = []
    real = route.dir_is_settled

    def spy(path, now, *a, **kw):
        walked.append(Path(path).name)
        return real(path, now, *a, **kw)

    monkeypatch.setattr(route, "dir_is_settled", spy)
    return walked


def test_cached_unsure_folder_is_not_walked(home, make_file, monkeypatch):
    make_file(home / "Downloads/project/main.py")
    old = time.time() - 3600
    os.utime(home / "Downloads/project", (old, old))
    run_main(home, FakeFactory(), FakeNotifier())  # unsure: cached
    assert [r["path"] for r in rows(state(home) / "verdicts.jsonl")] == [
        str(home / "Downloads/project")]

    walked = _spy_settle(monkeypatch)
    factory = FakeFactory()
    run_main(home, factory, FakeNotifier())

    assert walked == []
    assert factory.made == 0


def test_failing_folder_is_not_walked_once_blocked(home, make_file, monkeypatch):
    make_file(home / "Downloads/project/main.py")
    old = time.time() - 3600
    os.utime(home / "Downloads/project", (old, old))

    def answer(sample, n):
        raise RuntimeError("boom")

    for _ in range(3):
        run_main(home, FakeFactory(answer), FakeNotifier())
    walked = _spy_settle(monkeypatch)
    run_main(home, FakeFactory(answer), FakeNotifier())
    assert walked == []


def test_desktop_folders_without_a_matching_rule_are_not_walked(home, make_file, monkeypatch):
    make_file(home / "Desktop/stuff/a.txt")
    (home / "Desktop/Foo.app").mkdir()
    old = time.time() - 3600
    for d in ("stuff", "Foo.app"):
        os.utime(home / "Desktop" / d, (old, old))
    walked = _spy_settle(monkeypatch)

    run_main(home, FakeFactory(), FakeNotifier(), home / "Desktop")

    assert walked == ["Foo.app"]  # only the folder a rule matched
    assert (home / "Downloads/Installers/Foo.app").is_dir()
    assert (home / "Desktop/stuff").is_dir()


def test_settle_walk_prunes_vendored_dirs_and_is_capped(tmp_path, make_file, monkeypatch):
    d = tmp_path / "proj"
    for vendored in ("node_modules", ".git", ".venv", "venv", "__pycache__", "build",
                     "dist", ".next"):
        make_file(d / vendored / "deep/inner/recent.js", age_seconds=5)
        old = time.time() - 3600
        os.utime(d / vendored, (old, old))
    make_file(d / "src/main.py")
    for p in (d / "src", d):
        os.utime(p, (time.time() - 3600,) * 2)
    assert route.dir_is_settled(d, time.time()) is True
    assert route.DIR_WALK_LIMIT == 5_000
