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


def test_review_pending_ignores_the_daily_limit_when_the_user_asks(home):
    add(home, "Taxes", 4, "tax")                       # due, offered by the app → marked sent
    assert main(home, "--review-pending", runner=Osascript()) == 0          # answers Taxes
    assert suggestions(home)["last_sent"] is not None                      # daily limit used
    add(home, "Recipes", 3, "rec")                                          # ready right after
    assert main(home, "--review-pending",
                runner=Osascript(review="button returned:Create & move, text returned:Recipes")) == 0
    assert len(list((home / "Documents/Recipes").iterdir())) == 3


def test_review_pending_never_exits_non_zero_and_explains_in_a_dialog(home, monkeypatch):
    offer(home)
    import route
    monkeypatch.setattr(route, "LOCK_WAIT", 0.1)
    fd = route.acquire_lock(home / "lock")  # a sort is running
    try:
        osa = Osascript()
        assert main(home, "--review-pending", runner=osa) == 0
        assert any(route.BUSY_TEXT in d for d in osa.dialogs)
    finally:
        import os
        os.close(fd)


def test_review_pending_similar_folder_refusal_is_a_dialog_not_an_error(home):
    offer(home)
    (home / "Documents/Tax").mkdir()
    osa = Osascript(review=["button returned:Create & move, text returned:Taxes"],
                    similar="button returned:Cancel")  # then Esc on the reopened dialog
    assert main(home, "--review-pending", runner=osa) == 0
    assert not (home / "Documents/Taxes").exists()


def test_install_app_quoting_round_trips_awkward_paths(tmp_path):
    import shlex
    rec = Recorder()
    py = tmp_path / "it's \"my\" $HOME py" / "python3"
    rp = tmp_path / "back\\slash dir" / "route.py"
    setup.install_app(str(py), rp, apps_dir=tmp_path / "Apps", runner=rec)
    script = rec.calls[0][rec.calls[0].index("-e") + 1]
    assert script.startswith('do shell script "') and script.endswith('"')
    literal = script[len('do shell script "'):-1]
    decoded = literal.replace('\\"', '"').replace("\\\\", "\\")
    assert shlex.split(decoded) == [str(py), str(rp), "--review-pending"]


def test_install_app_only_replaces_its_own_app(tmp_path):
    apps = tmp_path / "Applications"
    other = apps / "Folder Suggestions.app"
    other.mkdir(parents=True)
    (other / "important.txt").write_text("not ours")
    assert setup.install_app("/py", tmp_path / "route.py", apps_dir=apps, runner=Recorder()) is None
    assert (other / "important.txt").exists()


def test_set_model_path_writes_a_valid_setting(tmp_path):
    import tomllib
    local = tmp_path / "rules.local.toml"
    local.write_text('[smart_sort]\n# model_path = "~/models/your-model.gguf"  # required\n')
    model = tmp_path / "models" / "My Model.gguf"
    model.parent.mkdir()
    model.write_bytes(b"GGUF")
    assert setup.set_model_path(local, model) is None
    data = tomllib.loads(local.read_text())
    assert data["smart_sort"]["model_path"] == str(model)
    assert setup.set_model_path(local, model) is None        # twice: still one line
    assert local.read_text().count("model_path =") == 1
    assert "not found" in setup.set_model_path(local, tmp_path / "missing.gguf")


def test_review_pending_without_rules_explains_in_a_dialog(tmp_path, monkeypatch):
    import route
    monkeypatch.setenv("HOME", str(tmp_path))
    osa = Osascript()
    assert route.main(["--review-pending", "--rules", str(tmp_path / "nope.toml"),
                       "--lock", str(tmp_path / "lock")], runner=osa) == 0
    assert len(osa.dialogs) == 1 and "not set up yet" in osa.dialogs[0]
