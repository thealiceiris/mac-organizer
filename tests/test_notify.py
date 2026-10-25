import os
import subprocess
import time
from pathlib import Path

import notify
from batch import Batch


def _batch(*specs, unavailable=False):
    b = Batch(ai_unavailable=unavailable)
    for i, (kind, folder, smart) in enumerate(specs):
        b.add(Path(f"/in/f{i}.pdf"), Path(f"/out/{folder or 'x'}/f{i}.pdf"), folder, kind,
              f"reason {i}", smart)
    return b


def _big():
    return _batch(*([("smart", "/V/Natural Language Processing", True)] * 5
                    + [("smart", "/V/Development", True), ("inbox", None, True),
                       ("rule", None, False)]))


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0)


def test_summary_text_groups_by_folder():
    assert notify.summary_text(_big()) == (
        "7 files sorted: 5 → Natural Language Processing, 1 → Development, 1 left in Misc")


def test_summary_mentions_duplicates_and_ai_unavailable():
    b = _batch(("smart", "/V/Dev", True), ("duplicate", None, True), ("duplicate", None, True),
               unavailable=True)
    assert notify.summary_text(b) == (
        "3 files sorted: 1 → Dev, 2 duplicates to Trash, AI sorting unavailable")


def test_singular_and_none_moved():
    assert notify.summary_text(_batch(("smart", "/V/Dev", True))) == "1 file sorted: 1 → Dev"
    assert notify.summary_text(_batch(("stay", None, True), ("stay", None, True))) == (
        "No files moved: 2 left in place")


def test_uses_terminal_notifier_with_open_report(tmp_path):
    fake = Fake()
    n = notify.Notifier(tmp_path, runner=fake, which=lambda name: "/bin/terminal-notifier")
    report = n.summary(_big())
    cmd = fake.calls[0][0]
    assert cmd[0] == "/bin/terminal-notifier"
    assert cmd[cmd.index("-title") + 1] == "mac-organizer"
    assert cmd[cmd.index("-message") + 1] == notify.summary_text(_big())
    assert cmd[cmd.index("-group") + 1] == "mac-organizer"
    assert cmd[cmd.index("-open") + 1] == f"file://{report}"
    assert report.parent == tmp_path / "reports"
    lines = report.read_text().splitlines()
    assert lines[0] == "f0.pdf  →  Natural Language Processing  — reason 0"
    assert lines[6] == "f6.pdf  →  Misc  — reason 6"
    assert len(lines) == 7  # rule-only move is not reported


def test_falls_back_to_osascript_and_escapes_quotes(tmp_path):
    fake = Fake()
    n = notify.Notifier(tmp_path, runner=fake, which=lambda name: None)
    n._fallback_paths = ()
    b = _batch(("smart", '/V/Say "hi"', True))
    n.summary(b)
    cmd = fake.calls[0][0]
    assert cmd[0] == "osascript"
    assert '\\"hi\\"' in cmd[2]


def test_start_message_exact(tmp_path):
    fake = Fake()
    n = notify.Notifier(tmp_path, runner=fake, which=lambda name: "/bin/tn")
    n.start()
    cmd = fake.calls[0][0]
    assert cmd[cmd.index("-message") + 1] == "Files are being sorted…"
    assert "-open" not in cmd


def test_unavailable_message(tmp_path):
    fake = Fake()
    n = notify.Notifier(tmp_path, runner=fake, which=lambda name: "/bin/tn")
    n.unavailable()
    cmd = fake.calls[0][0]
    assert cmd[cmd.index("-message") + 1] == "AI sorting unavailable — files will wait in Misc"
    assert "-open" not in cmd


def test_failures_never_raise(tmp_path):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    n = notify.Notifier(tmp_path, runner=boom, which=lambda name: "/bin/tn")
    n.start()
    n.unavailable()
    assert n.summary(_big()).exists()

    def bad(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1)

    notify.Notifier(tmp_path, runner=bad, which=lambda name: None).start()


def test_fallback_to_homebrew_paths(tmp_path, monkeypatch):
    fake = Fake()
    exe = tmp_path / "terminal-notifier"
    exe.write_text("")
    exe.chmod(0o755)
    n = notify.Notifier(tmp_path, runner=fake, which=lambda name: None)
    n._fallback_paths = (str(exe),)
    n.start()
    assert fake.calls[0][0][0] == str(exe)


def test_old_reports_pruned(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    old = reports / "old.txt"
    old.write_text("x")
    new = reports / "recent.txt"
    new.write_text("x")
    t = time.time() - 31 * 86400
    os.utime(old, (t, t))
    notify.Notifier(tmp_path, runner=Fake(), which=lambda n: None).summary(_big())
    assert not old.exists() and new.exists()
