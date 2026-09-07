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
