"""The "Folder Suggestions" app: reviewing suggestions without relying on a notification click."""

import subprocess
import time

import notify
import setup
import suggest
import topics
from test_suggest_cli import Osascript, add, home, main, misc, offer, state, suggestions  # noqa: F401


def test_notification_points_to_the_app():
    s = suggest.Suggestion("tax", "Taxes", ["a", "b", "c", "d"])
    assert notify.suggest_text(s) == "4 files in Misc look like Taxes. Open Folder Suggestions to review."


def test_review_pending_reviews_the_waiting_suggestion(home):
    offer(home)
    assert main(home, "--review-pending", runner=Osascript()) == 0
    assert len(list((home / "Documents/Taxes").iterdir())) == 4
    assert suggestions(home)["pending"] == {}


def test_review_pending_offers_a_due_suggestion_that_was_never_sent(home):
    add(home, "Taxes", 3, "tax")
    assert main(home, "--review-pending", runner=Osascript()) == 0
    assert len(list((home / "Documents/Taxes").iterdir())) == 3
    assert suggestions(home)["last_sent"] is not None


def test_review_pending_with_nothing_waiting_says_so(home):
    osa = Osascript()
    assert main(home, "--review-pending", runner=osa) == 0
    assert len(osa.dialogs) == 1
    assert "No folder suggestions right now." in osa.dialogs[0]
    assert sorted(p.name for p in (home / "Documents").iterdir()) == ["Misc"]


def test_review_pending_not_now_creates_nothing(home):
    offer(home)
    osa = Osascript(review="button returned:Not now, text returned:Taxes")
    assert main(home, "--review-pending", runner=osa) == 0
    assert not (home / "Documents/Taxes").exists()
    assert "tax" in suggestions(home)["snoozed"]


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        app = cmd[cmd.index("-o") + 1]
        (subprocess.os.makedirs(app, exist_ok=True))
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_install_app_builds_folder_suggestions_with_absolute_paths(tmp_path):
    rec = Recorder()
    app = setup.install_app("/opt/py/python3", tmp_path / "repo/route.py",
                            apps_dir=tmp_path / "Applications", runner=rec)
    assert app == tmp_path / "Applications/Folder Suggestions.app"
    assert app.is_dir()
    cmd = rec.calls[0]
    assert cmd[0].endswith("osacompile") and "-e" in cmd
    script = cmd[cmd.index("-e") + 1]
    assert "do shell script" in script
    assert "/opt/py/python3" in script and str(tmp_path / "repo/route.py") in script
    assert "--review-pending" in script


def test_install_app_failure_never_raises(tmp_path):
    def boom(cmd, **kw):
        raise OSError("no osacompile")
    assert setup.install_app("/py", tmp_path / "route.py", apps_dir=tmp_path, runner=boom) is None


def test_remove_app(tmp_path):
    app = tmp_path / "Applications/Folder Suggestions.app"
    (app / "Contents").mkdir(parents=True)
    setup.remove_app(apps_dir=tmp_path / "Applications")
    assert not app.exists()
    setup.remove_app(apps_dir=tmp_path / "Applications")  # absent: no error
