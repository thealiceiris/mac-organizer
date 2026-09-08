import plistlib
import subprocess
from pathlib import Path

import setup


class _FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout


def test_detect_screenshot_dir_reads_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    def fake_run(cmd, capture_output, text, check):
        assert cmd[:2] == ["defaults", "read"]
        return _FakeCompleted(str(tmp_path / "Pictures/Screenshots") + "\n")

    assert setup.detect_screenshot_dir(runner=fake_run) == tmp_path / "Pictures/Screenshots"


def test_detect_screenshot_dir_falls_back_to_desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    def fake_run(cmd, capture_output, text, check):
        raise subprocess.CalledProcessError(1, cmd)

    assert setup.detect_screenshot_dir(runner=fake_run) == tmp_path / "Desktop"


def test_make_dirs_creates_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    setup.make_dirs()
    for entry in setup.DIRS_TO_CREATE:
        assert Path(entry).expanduser().is_dir()
    assert setup.state_dir().is_dir()


def test_write_rules_substitutes_and_respects_existing(tmp_path):
    default = tmp_path / "rules.default.toml"
    default.write_text('source = "@@SCREENSHOT_DIR@@"\n')
    out = tmp_path / "rules.toml"

    assert setup.write_rules(default, out, Path("/home/me/Pictures/Screenshots")) is True
    assert out.read_text() == 'source = "/home/me/Pictures/Screenshots"\n'

    assert setup.write_rules(default, out, Path("/other")) is False
    assert "/home/me/Pictures/Screenshots" in out.read_text()


def test_install_agents_writes_five_valid_plists(monkeypatch, tmp_path):
    """A custom screenshot folder gets its own agent: 3 sources + shots + sweep."""
    monkeypatch.setenv("HOME", str(tmp_path))
    shots = tmp_path / "Pictures/Screenshots"
    written = setup.install_agents(
        "/opt/homebrew/bin/python3", tmp_path / "repo/route.py", shots
    )
    assert len(written) == 5
    labels = set()
    for path in written:
        assert path.parent == tmp_path / "Library/LaunchAgents"
        data = plistlib.loads(path.read_bytes())
        labels.add(data["Label"])
    assert labels == set(setup.agent_labels(shots))
    assert f"{setup.LABEL_PREFIX}.screenshots" in labels

    sweep = tmp_path / "Library/LaunchAgents/com.mac-organizer.sweep.plist"
    sweep_data = plistlib.loads(sweep.read_bytes())
    assert sweep_data["StartInterval"] == setup.SWEEP_INTERVAL
    assert str(shots) in sweep_data["ProgramArguments"]


def test_install_agents_does_not_duplicate_the_screenshot_dir(monkeypatch, tmp_path):
    """I5: stock Macs report ~/Desktop, which is already a watched source."""
    monkeypatch.setenv("HOME", str(tmp_path))
    desktop = tmp_path / "Desktop"
    written = setup.install_agents(
        "/opt/homebrew/bin/python3", tmp_path / "repo/route.py", desktop
    )

    assert len(written) == 4
    labels = {plistlib.loads(p.read_bytes())["Label"] for p in written}
    assert labels == set(setup.agent_labels(desktop))
    assert f"{setup.LABEL_PREFIX}.screenshots" not in labels

    watch_paths = [
        plistlib.loads(p.read_bytes()).get("WatchPaths", [None])[0] for p in written
    ]
    assert watch_paths.count(str(desktop)) == 1

    sweep_data = plistlib.loads(
        (tmp_path / "Library/LaunchAgents/com.mac-organizer.sweep.plist").read_bytes()
    )
    dirs = sweep_data["ProgramArguments"][2:]
    assert dirs == list(dict.fromkeys(dirs))
    assert dirs.count(str(desktop)) == 1


def test_install_agents_removes_agents_it_no_longer_generates(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    route_py = tmp_path / "repo/route.py"
    setup.install_agents("/usr/bin/python3", route_py, tmp_path / "Pictures/Screenshots")
    stale = tmp_path / "Library/LaunchAgents/com.mac-organizer.screenshots.plist"
    assert stale.exists()

    written = setup.install_agents("/usr/bin/python3", route_py, tmp_path / "Desktop")

    assert not stale.exists()
    assert sorted(
        (tmp_path / "Library/LaunchAgents").glob("com.mac-organizer.*.plist")
    ) == sorted(written)


def test_copy_vault_template_never_overwrites(tmp_path):
    src = tmp_path / "vault-template"
    (src / "_templates").mkdir(parents=True)
    (src / "_templates/daily.md").write_text("TEMPLATE")
    dest = tmp_path / "Notes"
    (dest / "_templates").mkdir(parents=True)
    (dest / "_templates/daily.md").write_text("USER EDIT")

    setup.copy_vault_template(src, dest)

    assert (dest / "_templates/daily.md").read_text() == "USER EDIT"


def test_copy_vault_template_copies_new_files(tmp_path):
    src = tmp_path / "vault-template"
    (src / ".obsidian").mkdir(parents=True)
    (src / ".obsidian/app.json").write_text("{}")
    dest = tmp_path / "Notes"

    setup.copy_vault_template(src, dest)

    assert (dest / ".obsidian/app.json").read_text() == "{}"
