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
                 similar=None, review_code=0):
        self.review, self.similar, self.review_code = review, similar, review_code
        self.dialogs, self.notes = [], []

    def __call__(self, cmd, **kw):
        assert cmd[0] == "osascript"
        script = cmd[2]
        if script.startswith("display notification"):
            self.notes.append(script)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.dialogs.append(script)
        if "A similar folder" in script:
            out = self.similar or "button returned:Cancel"
            return subprocess.CompletedProcess(cmd, 0, out + "\n", "")
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
    osa = Osascript(review=f"button returned:Create & move, text returned:{name}")
    assert main(home, "--review-suggestion", "tax", runner=osa) == 1
    assert sorted(p.name for p in (home / "Documents").iterdir()) == before
    assert len(osa.notes) == 1
    assert "folder name" in osa.notes[0] or "already exists" in osa.notes[0]
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
    osa = Osascript(similar="button returned:Cancel")
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
