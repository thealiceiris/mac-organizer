"""The folder-icon notifier: a copy of terminal-notifier.app with its own icon and id.

macOS takes a notification's icon from the app that sends it, so the copy is
the only way notifications can show a folder instead of the Terminal icon.
"""

import plistlib
import subprocess
from pathlib import Path

import notify
import setup


def _source_app(root: Path, version="3.1.0") -> Path:
    app = root / "Cellar/terminal-notifier" / version / "terminal-notifier.app"
    (app / "Contents/MacOS").mkdir(parents=True)
    (app / "Contents/Resources").mkdir()
    exe = app / "Contents/MacOS/terminal-notifier"
    exe.write_text("#!/bin/bash\n")
    exe.chmod(0o755)
    (app / "Contents/Resources/Terminal.icns").write_bytes(b"terminal")
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": "fr.julienxx.oss.terminal-notifier",
        "CFBundleName": "terminal-notifier",
        "CFBundleIconFile": "Terminal",
        "CFBundleShortVersionString": version,
        "CFBundleExecutable": "terminal-notifier",
    }))
    return app


def _icon(root: Path) -> Path:
    icon = root / "GenericFolderIcon.icns"
    icon.write_bytes(b"folder")
    return icon


class Runner:
    def __init__(self, fail_on=""):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, cmd, **kwargs):
        self.calls.append([str(c) for c in cmd])
        rc = 1 if self.fail_on and self.fail_on in str(cmd[0]) else 0
        return subprocess.CompletedProcess(cmd, rc, "", "")

    def ran(self, name):
        return [c for c in self.calls if name in c[0]]


def _info(app: Path) -> dict:
    return plistlib.loads((app / "Contents/Info.plist").read_bytes())


def test_builds_a_signed_copy_with_the_folder_icon(tmp_path):
    runner = Runner()
    state = tmp_path / "state"
    app = setup.install_notifier(state, _source_app(tmp_path), icon=_icon(tmp_path),
                                 runner=runner)
    assert app == state / notify.NOTIFIER_APP
    info = _info(app)
    assert info["CFBundleIdentifier"] == setup.NOTIFIER_ID
    assert info["CFBundleName"] == "mac-organizer"
    assert info["CFBundleIconFile"] == "Folder"
    assert (app / "Contents/Resources/Folder.icns").read_bytes() == b"folder"
    assert (app / notify.NOTIFIER_EXE).is_file()
    signs = runner.ran("codesign")
    # signed once, ad hoc, before it is moved into place
    assert len(signs) == 1 and signs[0][-3:-1] == ["--sign", "-"]
    assert [p.name for p in state.iterdir()] == [notify.NOTIFIER_APP]  # no temp left


def test_leaves_the_homebrew_original_untouched(tmp_path):
    source = _source_app(tmp_path)
    setup.install_notifier(tmp_path / "state", source, icon=_icon(tmp_path), runner=Runner())
    assert _info(source)["CFBundleIdentifier"] == "fr.julienxx.oss.terminal-notifier"
    assert not (source / "Contents/Resources/Folder.icns").exists()


def test_signing_failure_leaves_nothing_behind(tmp_path):
    state = tmp_path / "state"
    app = setup.install_notifier(state, _source_app(tmp_path), icon=_icon(tmp_path),
                                 runner=Runner(fail_on="codesign"))
    assert app is None
    assert not state.exists() or list(state.iterdir()) == []


def test_without_terminal_notifier_or_icon_builds_nothing(tmp_path):
    runner = Runner()
    state = tmp_path / "state"
    assert setup.install_notifier(state, None, icon=_icon(tmp_path), runner=runner) is None
    assert setup.install_notifier(state, _source_app(tmp_path), icon=tmp_path / "missing.icns",
                                  runner=runner) is None
    assert runner.calls == []
    assert not (state / notify.NOTIFIER_APP).exists()


def test_an_up_to_date_copy_is_kept_so_permission_is_not_asked_again(tmp_path):
    source, icon, state = _source_app(tmp_path), _icon(tmp_path), tmp_path / "state"
    setup.install_notifier(state, source, icon=icon, runner=Runner())
    again = Runner()
    assert setup.install_notifier(state, source, icon=icon, runner=again) == state / notify.NOTIFIER_APP
    assert again.ran("codesign") == []


def test_a_copy_of_an_older_version_is_rebuilt(tmp_path):
    icon, state = _icon(tmp_path), tmp_path / "state"
    setup.install_notifier(state, _source_app(tmp_path, "3.0.0"), icon=icon, runner=Runner())
    runner = Runner()
    app = setup.install_notifier(state, _source_app(tmp_path, "3.1.0"), icon=icon, runner=runner)
    assert _info(app)["CFBundleShortVersionString"] == "3.1.0"
    assert len(runner.ran("codesign")) == 1


def test_never_replaces_an_app_that_is_not_ours(tmp_path):
    state = tmp_path / "state"
    other = _source_app(tmp_path / "other")
    state.mkdir()
    other.rename(state / notify.NOTIFIER_APP)
    assert setup.install_notifier(state, _source_app(tmp_path), icon=_icon(tmp_path),
                                  runner=Runner()) is None
    assert _info(state / notify.NOTIFIER_APP)["CFBundleIdentifier"] == \
        "fr.julienxx.oss.terminal-notifier"


def test_finds_the_app_behind_the_homebrew_wrapper(tmp_path):
    source = _source_app(tmp_path)
    wrapper = source.parent / "bin/terminal-notifier"  # Cellar/<v>/bin, next to the .app
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/bash\n")
    link = tmp_path / "opt-bin/terminal-notifier"  # /opt/homebrew/bin is a symlink
    link.parent.mkdir()
    link.symlink_to(wrapper)
    assert setup.find_notifier_app(which=lambda name: str(link)) == source


def test_finds_the_app_from_its_own_executable(tmp_path):
    source = _source_app(tmp_path)
    exe = source / "Contents/MacOS/terminal-notifier"
    assert setup.find_notifier_app(which=lambda name: str(exe)) == source


def test_find_without_terminal_notifier(tmp_path):
    assert setup.find_notifier_app(which=lambda name: None, fallback_paths=()) is None


def test_remove_notifier(tmp_path):
    state = tmp_path / "state"
    setup.install_notifier(state, _source_app(tmp_path), icon=_icon(tmp_path), runner=Runner())
    setup.remove_notifier(state)
    assert not (state / notify.NOTIFIER_APP).exists()
    setup.remove_notifier(state)  # absent: no error


def test_notifications_are_sent_by_the_folder_icon_copy(tmp_path):
    exe = tmp_path / notify.NOTIFIER_APP / notify.NOTIFIER_EXE
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    exe.chmod(0o755)
    calls = []
    n = notify.Notifier(tmp_path, runner=lambda cmd, **kw: calls.append(cmd),
                        which=lambda name: "/opt/homebrew/bin/terminal-notifier")
    n.start()
    assert calls[0][0] == str(exe)


def test_lsregister_can_be_replaced(tmp_path, monkeypatch):
    """Script tests swap it out so they never register copies with Launch Services."""
    monkeypatch.setenv("MAC_ORGANIZER_LSREGISTER", "/stub/lsregister")
    runner = Runner()
    setup.install_notifier(tmp_path / "state", _source_app(tmp_path), icon=_icon(tmp_path),
                           runner=runner)
    assert [c[0] for c in runner.calls if "lsregister" in c[0]] == ["/stub/lsregister"]
