"""Task 5: the post-run offer and the suggestion CLI (fake osascript, fake notifier)."""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import notify
import route
import suggest
import topics

DAY = 86400.0


class Osascript:
    """Fake ``subprocess.run`` for osascript: scripted dialog answers, recorded notifications."""

    def __init__(self, review="button returned:Create & move, text returned:Taxes",
                 similar=None, review_code=0, on_review=None):
        # ``review``/``similar``: one answer for every dialog, or a list answered in
        # turn (an exhausted review list is Esc, an exhausted similar list is Cancel)
        self.review, self.similar, self.review_code = review, similar, review_code
        self.on_review = on_review
        self.dialogs, self.notes = [], []

    def __call__(self, cmd, **kw):
        assert cmd[0] == "osascript"
        script = cmd[2]
        if script.startswith("display notification"):
            self.notes.append(script)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.dialogs.append(script)
        if "A similar folder" in script and "Move the files there instead?" in script:
            out = self.similar
            if isinstance(out, list):
                out = out.pop(0) if out else None
            return subprocess.CompletedProcess(cmd, 0, (out or "button returned:Cancel") + "\n", "")
        if self.on_review is not None:
            self.on_review()
        if isinstance(self.review, list):
            if not self.review:
                return subprocess.CompletedProcess(cmd, 1, "", "User canceled.")
            return subprocess.CompletedProcess(cmd, 0, self.review.pop(0) + "\n", "")
        return subprocess.CompletedProcess(cmd, self.review_code, self.review + "\n", "")

    def note(self, text):
        return f'display notification "{text}" with title "mac-organizer"'


class FakeNotifier:
    def __init__(self, sends=True):
        self.sends, self.offers = sends, []

    def start(self):
        pass

    def summary(self, batch):
        pass

    def unavailable(self):
        pass

    def suggest(self, s, command):
        self.offers.append((s, command))
        return self.sends


def no_model(*a, **k):
    raise AssertionError("the model must never start here")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Documents/Misc").mkdir(parents=True)
    src = Path(__file__).resolve().parent.parent / "rules.default.toml"
    (tmp_path / "rules.toml").write_text(
        src.read_text().replace("@@SCREENSHOT_DIR@@", str(tmp_path / "Pictures/Screenshots")))
    monkeypatch.setattr(route, "LOCK_POLL", 0.05)
    return tmp_path


def state(home):
    return home / ".local/state/mac-organizer"


def misc(home):
    return home / "Documents/Misc"


def add(home, topic, n, prefix):
    state(home).mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        p = misc(home) / f"{prefix}-{i}.pdf"
        p.write_text("x")
        topics.record(state(home), p, topic, time.time())
        out.append(p)
    return out


def offer(home, topic="Taxes", n=4, prefix="tax", sent_at=None):
    items = add(home, topic, n, prefix)
    s = suggest.Suggestion(topics.normalise(topic), topic, [str(p) for p in items])
    suggest.mark_sent(state(home), s, time.time() if sent_at is None else sent_at)
    _reset_daily(home)
    return s


def _reset_daily(home):
    f = state(home) / "suggestions.json"
    data = json.loads(f.read_text())
    data["last_sent"] = None
    f.write_text(json.dumps(data))


def main(home, *argv, runner=None, notifier=None):
    base = ["--rules", str(home / "rules.toml"), "--log", str(home / "actions.log"),
            "--lock", str(home / "lock")]
    return route.main([*argv, *base], classifier_factory=no_model,
                      notifier=notifier, runner=runner or Osascript())


def suggestions(home):
    f = state(home) / "suggestions.json"
    return json.loads(f.read_text()) if f.exists() else {}


# ---------------------------------------------------------------- the offer


def test_review_command_is_absolute():
    cmd = route.review_command("tax")
    assert cmd == [sys.executable, str(Path(route.__file__).resolve()),
                   "--review-suggestion", "tax"]
    assert Path(cmd[0]).is_absolute() and Path(cmd[1]).is_absolute()


def test_main_offers_at_most_one_suggestion_per_day(home):
    add(home, "Taxes", 4, "tax")
    add(home, "Travel", 3, "trip")
    n = FakeNotifier()
    assert main(home, notifier=n) == 0
    assert len(n.offers) == 1
    s, command = n.offers[0]
    assert (s.key, s.name, len(s.items)) == ("tax", "Taxes", 4)
    assert command == route.review_command("tax")
    assert list(suggestions(home)["pending"]) == ["tax"]
    assert main(home, notifier=n) == 0
    assert len(n.offers) == 1  # the daily limit holds
    data = suggestions(home)
    data["last_sent"] -= DAY + 1
    (state(home) / "suggestions.json").write_text(json.dumps(data))
    assert main(home, notifier=n) == 0
    assert [o[0].key for o in n.offers] == ["tax", "travel"]
    assert not (home / "Documents/Taxes").exists()  # offering never creates a folder


def test_dry_run_never_offers(home):
    add(home, "Taxes", 4, "tax")
    n = FakeNotifier()
    assert main(home, "--dry-run", notifier=n) == 0
    assert n.offers == []
    assert suggestions(home) == {}


def test_offer_failure_never_fails_the_run(home, capsys):
    add(home, "Taxes", 4, "tax")

    class Broken(FakeNotifier):
        def suggest(self, s, command):
            raise RuntimeError("boom")

    assert main(home, notifier=Broken()) == 0
    assert "suggest" in capsys.readouterr().err
    assert suggestions(home) == {}


def test_no_terminal_notifier_does_not_mark_sent_and_cli_lists_it(home, capsys):
    add(home, "Taxes", 4, "tax")
    calls = []
    n = notify.Notifier(state(home), runner=lambda cmd, **kw: calls.append(cmd),
                        which=lambda name: None)
    n._fallback_paths = ()
    assert main(home, notifier=n) == 0
    assert calls == []
    assert suggestions(home).get("last_sent") is None
    assert suggestions(home).get("pending", {}) == {}
    capsys.readouterr()
    assert main(home, "--suggestions") == 0
    assert "tax\tTaxes\t4 files" in capsys.readouterr().out.splitlines()


def test_suggestions_lists_pending_and_due(home, capsys):
    offer(home, "Taxes", 4, "tax")
    add(home, "Travel", 3, "trip")
    assert main(home, "--suggestions") == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == ["tax\tTaxes\t4 files", "travel\tTravel\t3 files"]


# ---------------------------------------------------------------- review


def test_review_create_moves_files(home):
    s = offer(home)
    osa = Osascript()
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    folder = home / "Documents/Taxes"
    assert sorted(p.name for p in folder.iterdir()) == [f"tax-{i}.pdf" for i in range(4)]
    assert not any(Path(p).exists() for p in s.items)
    assert osa.notes == [osa.note('Created \\"Taxes\\" and moved 4 files')]
    log = (home / "actions.log").read_text()
    assert "NEWFOLDER" in log and log.count("SUGGEST") == 4
    assert suggestions(home)["pending"] == {}


def test_review_uses_the_edited_name(home):
    offer(home)
    osa = Osascript(review="button returned:Create & move, text returned:  Tax Returns ")
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    assert len(list((home / "Documents/Tax Returns").iterdir())) == 4


@pytest.mark.parametrize("name", ["", "a/b", ".hidden", "Misc"])
def test_review_invalid_name_creates_nothing(home, name):
    offer(home)
    before = sorted(p.name for p in (home / "Documents").iterdir())
    # the dialog comes back with the error; Esc there ends it
    osa = Osascript(review=[f"button returned:Create & move, text returned:{name}"])
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    assert sorted(p.name for p in (home / "Documents").iterdir()) == before
    assert len(osa.dialogs) == 2 and osa.notes == []
    assert "folder name" in osa.dialogs[1] or "already exists" in osa.dialogs[1]
    assert "tax" in suggestions(home)["pending"]  # still answerable


def test_review_near_duplicate_then_use_existing(home):
    offer(home)
    (home / "Documents/Tax").mkdir()
    osa = Osascript(similar='button returned:Use "Tax"')
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    assert not (home / "Documents/Taxes").exists()
    assert len(list((home / "Documents/Tax").iterdir())) == 4
    assert 'A similar folder \\"Tax\\" exists.' in osa.dialogs[1]
    assert osa.notes == [osa.note('Moved 4 files to \\"Tax\\"')]
    assert "NEWFOLDER" not in (home / "actions.log").read_text()


def test_review_near_duplicate_cancel_moves_nothing(home):
    s = offer(home)
    (home / "Documents/Tax").mkdir()
    osa = Osascript(review=["button returned:Create & move, text returned:Taxes"],
                    similar="button returned:Cancel")
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    assert list((home / "Documents/Tax").iterdir()) == []
    assert not (home / "Documents/Taxes").exists()
    assert all(Path(p).exists() for p in s.items)


def test_review_never_and_not_now(home):
    offer(home, "Taxes", 3, "tax")
    offer(home, "Travel", 3, "trip")
    osa = Osascript(review="button returned:Never suggest this, text returned:Taxes")
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    assert json.loads((state(home) / "never.json").read_text()) == ["tax"]
    osa = Osascript(review="button returned:Not now, text returned:Travel")
    assert main(home, "--review-suggestion", "travel", runner=osa) == 0
    data = suggestions(home)
    assert data["pending"] == {}
    assert data["snoozed"]["travel"] > time.time() + 6 * DAY
    assert sorted(p.name for p in (home / "Documents").iterdir()) == ["Misc"]


def test_review_cancel_leaves_it_pending(home):
    offer(home)
    osa = Osascript(review_code=1, review="")
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    assert "tax" in suggestions(home)["pending"]
    assert sorted(p.name for p in (home / "Documents").iterdir()) == ["Misc"]


def test_review_stale_key_is_noop(home, capsys):
    offer(home)
    assert main(home, "--review-suggestion", "tax", runner=Osascript()) == 0
    # clicked twice: no dialog, no second folder, a message
    osa = Osascript(review="button returned:Create & move, text returned:Taxes Again")
    assert main(home, "--review-suggestion", "tax", runner=osa) == 1
    assert osa.dialogs == []
    assert "no such suggestion" in capsys.readouterr().out
    assert len(osa.notes) == 1
    assert sorted(p.name for p in (home / "Documents").iterdir()) == ["Misc", "Taxes"]
    # never offered
    osa = Osascript()
    assert main(home, "--review-suggestion", "nope", runner=osa) == 1
    assert osa.dialogs == []
    # expired: counts as "Not now", the click does nothing
    offer(home, "Travel", 3, "trip", sent_at=time.time() - 8 * DAY)
    osa = Osascript(review="button returned:Create & move, text returned:Travel")
    assert main(home, "--review-suggestion", "travel", runner=osa) == 1
    assert osa.dialogs == []
    assert not (home / "Documents/Travel").exists()
    assert "travel" in suggestions(home)["snoozed"]


def test_review_waits_for_lock_then_reports_busy(home, monkeypatch, capsys):
    offer(home)
    monkeypatch.setattr(route, "LOCK_WAIT", 0.3)
    held = route.acquire_lock(home / "lock")
    try:
        osa = Osascript()
        started = time.monotonic()
        assert main(home, "--review-suggestion", "tax", runner=osa) == 1
        assert time.monotonic() - started >= 0.3
        assert osa.dialogs == []
        assert osa.notes == [osa.note("mac-organizer is busy, try again shortly")]
        assert "mac-organizer is busy, try again shortly" in capsys.readouterr().err
        assert not (home / "Documents/Taxes").exists()
    finally:
        import os
        os.close(held)
    # the lock is freed while it waits: the review goes ahead
    monkeypatch.setattr(route, "LOCK_WAIT", 5.0)
    held = route.acquire_lock(home / "lock")
    import os
    threading.Timer(0.3, os.close, [held]).start()
    assert main(home, "--review-suggestion", "tax", runner=Osascript()) == 0
    assert (home / "Documents/Taxes").is_dir()


# ---------------------------------------------------------------- accept / decline


def test_accept_suggestion_cli(home, capsys):
    offer(home)
    assert main(home, "--accept-suggestion", "tax", "--name", "My Taxes") == 0
    assert len(list((home / "Documents/My Taxes").iterdir())) == 4
    assert 'Created "My Taxes" and moved 4 files' in capsys.readouterr().out
    assert main(home, "--accept-suggestion", "tax") == 1
    assert "no such suggestion" in capsys.readouterr().out


def test_accept_due_suggestion_that_was_never_sent(home):
    add(home, "Taxes", 3, "tax")  # e.g. no terminal-notifier: listed, never sent
    assert main(home, "--accept-suggestion", "tax") == 0
    assert len(list((home / "Documents/Taxes").iterdir())) == 3


def test_decline_suggestion_cli(home):
    offer(home, "Taxes", 3, "tax")
    offer(home, "Travel", 3, "trip")
    assert main(home, "--decline-suggestion", "tax", "--never") == 0
    assert main(home, "--decline-suggestion", "travel") == 0
    assert json.loads((state(home) / "never.json").read_text()) == ["tax"]
    assert "travel" in suggestions(home)["snoozed"]
    assert main(home, "--decline-suggestion", "travel") == 1  # already answered


def test_suggestion_flags_reject_other_modes(home):
    for argv in (["--suggestions", "--dry-run"], ["--accept-suggestion", "x", "--backlog"],
                 ["--name", "x"], ["--never"], ["--decline-suggestion", "x", "--name", "y"]):
        with pytest.raises(SystemExit):
            main(home, *argv)


def test_expand_given_as_a_string_is_one_folder(tmp_path):
    """expand = "School" means ["School"], not ["S", "c", "h", ...]."""
    rules = tmp_path / "rules.toml"
    rules.write_text("")
    local = tmp_path / "rules.local.toml"
    local.write_text('[smart_sort]\nexpand = "School"\n')
    assert route.load_config(rules, local).smart.expand == ("School",)
    local.write_text('[smart_sort]\nexpand = ["School", "Work"]\n')
    assert route.load_config(rules, local).smart.expand == ("School", "Work")


# ---------------------------------------------------------------- final review fixes


def test_review_invalid_name_loops_back_with_the_error_and_typed_name(home):
    """I4: an invalid name re-shows the dialog, error first, prefilled with what was typed."""
    offer(home)
    osa = Osascript(review=["button returned:Create & move, text returned:a/b",
                            "button returned:Create & move, text returned:My Taxes"])
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    assert len(osa.dialogs) == 2
    second = osa.dialogs[1]
    assert second.startswith('display dialog "The folder name cannot contain \\"/\\"."')
    assert 'default answer "a/b"' in second
    assert len(list((home / "Documents/My Taxes").iterdir())) == 4
    assert osa.notes == [osa.note('Created \\"My Taxes\\" and moved 4 files')]


def test_review_loop_ends_on_not_now_and_never(home):
    offer(home, "Taxes", 3, "tax")
    osa = Osascript(review=["button returned:Create & move, text returned:.x",
                            "button returned:Not now, text returned:.x"])
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    assert "tax" in suggestions(home)["snoozed"] and suggestions(home)["pending"] == {}
    offer(home, "Travel", 3, "trip")
    osa = Osascript(review=["button returned:Create & move, text returned:",
                            "button returned:Never suggest this, text returned:"])
    assert main(home, "--review-suggestion", "travel", runner=osa) == 0
    assert json.loads((state(home) / "never.json").read_text()) == ["travel"]
    assert sorted(p.name for p in (home / "Documents").iterdir()) == ["Misc"]


def test_review_similar_cancel_returns_to_the_review_dialog(home):
    offer(home)
    (home / "Documents/Tax").mkdir()
    osa = Osascript(review=["button returned:Create & move, text returned:Taxes",
                            "button returned:Create & move, text returned:Tax Papers"],
                    similar=["button returned:Cancel"])
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    assert len(osa.dialogs) == 3  # review, similar (Cancel), review again
    assert 'default answer "Taxes"' in osa.dialogs[2]
    assert list((home / "Documents/Tax").iterdir()) == []
    assert len(list((home / "Documents/Tax Papers").iterdir())) == 4


def test_answer_waits_longer_for_the_lock_than_the_first_check(home, monkeypatch):
    """I3: after the user answers, a long sweep holding the lock does not drop the answer."""
    import os
    offer(home)
    assert route.ANSWER_LOCK_WAIT >= 15 * 60
    monkeypatch.setattr(route, "LOCK_WAIT", 0.05)
    monkeypatch.setattr(route, "ANSWER_LOCK_WAIT", 5.0)
    held = []

    def sweep_starts():  # a sweep takes the lock while the dialog is open
        if not held:
            held.append(route.acquire_lock(home / "lock"))
            threading.Timer(0.5, os.close, [held[0]]).start()

    osa = Osascript(on_review=sweep_starts)
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    assert (home / "Documents/Taxes").is_dir()


def test_answer_still_busy_says_exactly_what_to_run(home, monkeypatch, capsys):
    import os
    offer(home)
    monkeypatch.setattr(route, "ANSWER_LOCK_WAIT", 0.2)
    held = []
    osa = Osascript(review="button returned:Create & move, text returned:My \"Tax\" $files",
                    on_review=lambda: held or held.append(route.acquire_lock(home / "lock")))
    try:
        assert main(home, "--review-suggestion", "tax", runner=osa) == 1
    finally:
        os.close(held[0])
    out = capsys.readouterr()
    cmd = 'route.py --accept-suggestion tax --name "My \\"Tax\\" \\$files"'
    assert cmd in out.out + out.err
    assert len(osa.notes) == 1 and "--accept-suggestion tax --name" in osa.notes[0]
    assert "tax" in suggestions(home)["pending"]
    assert not any(p.name.startswith("My") for p in (home / "Documents").iterdir())
    # the printed command does the job
    assert main(home, "--accept-suggestion", "tax", "--name", 'My "Tax" $files') == 0


def test_decline_answer_still_busy_says_what_to_run(home, monkeypatch, capsys):
    import os
    offer(home)
    monkeypatch.setattr(route, "ANSWER_LOCK_WAIT", 0.2)
    held = []
    osa = Osascript(review="button returned:Never suggest this, text returned:Taxes",
                    on_review=lambda: held or held.append(route.acquire_lock(home / "lock")))
    try:
        assert main(home, "--review-suggestion", "tax", runner=osa) == 1
    finally:
        os.close(held[0])
    out = capsys.readouterr()
    assert "route.py --decline-suggestion tax --never" in out.out + out.err
    assert "--decline-suggestion tax --never" in osa.notes[0]


def test_review_lists_only_saved_items_that_still_exist_and_moves_only_those(home):
    """I7: the dialog shows the files saved at send time; a later arrival is not moved."""
    s = offer(home, "Taxes", 3, "tax")
    Path(s.items[0]).unlink()
    late = add(home, "Taxes", 1, "late")[0]
    osa = Osascript()
    assert main(home, "--review-suggestion", "tax", runner=osa) == 0
    shown = osa.dialogs[0]
    assert "tax-0.pdf" not in shown and "tax-1.pdf" in shown and "late-0.pdf" not in shown
    assert sorted(p.name for p in (home / "Documents/Taxes").iterdir()) == ["tax-1.pdf",
                                                                           "tax-2.pdf"]
    assert late.exists()
    assert osa.notes == [osa.note('Created \\"Taxes\\" and moved 2 files'
                                  ' (1 of the listed files are no longer in Misc)')]


def test_suggestions_list_takes_the_lock_or_reads_only(home, capsys):
    import os
    offer(home, "Taxes", 3, "tax", sent_at=time.time() - 8 * DAY)
    before = (state(home) / "suggestions.json").read_text()
    held = route.acquire_lock(home / "lock")
    try:
        assert main(home, "--suggestions") == 0
        assert (state(home) / "suggestions.json").read_text() == before  # busy: no writes
    finally:
        os.close(held)
    capsys.readouterr()
    assert main(home, "--suggestions") == 0
    assert "tax" in suggestions(home)["snoozed"]  # with the lock, the expiry is saved


def test_answer_command_quotes_a_multi_word_key():
    assert route._answer_command("car insurance", "not_now", "").endswith(
        "route.py --decline-suggestion 'car insurance'")
    assert route._answer_command("tax", "create", " A`b ").endswith(
        'route.py --accept-suggestion tax --name "A\\`b"')
