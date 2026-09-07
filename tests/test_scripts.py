import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _make_launchctl_stub(path: Path) -> Path:
    stub = path / "launchctl"
    stub.write_text(
        "#!/bin/bash\necho \"launchctl $@\" >> \"$LAUNCHCTL_LOG\"\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


def test_install_then_uninstall(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_launchctl_stub(bin_dir)
    log = tmp_path / "launchctl.log"

    env = {
        **os.environ,
        "HOME": str(home),
        "LAUNCHCTL": str(bin_dir / "launchctl"),
        "LAUNCHCTL_LOG": str(log),
    }

    install = subprocess.run(
        ["bash", str(REPO / "install.sh")], env=env, capture_output=True, text=True
    )
    assert install.returncode == 0, install.stderr
    agents = sorted((home / "Library/LaunchAgents").glob("com.mac-organizer.*.plist"))
    assert len(agents) == 5
    assert "DRYRUN" in install.stdout or "dry-run" in install.stdout.lower()
    assert log.read_text().count("launchctl") >= 5

    uninstall = subprocess.run(
        ["bash", str(REPO / "uninstall.sh")], env=env, capture_output=True, text=True
    )
    assert uninstall.returncode == 0, uninstall.stderr
    assert not list((home / "Library/LaunchAgents").glob("com.mac-organizer.*.plist"))
