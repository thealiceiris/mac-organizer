import os
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# Everything install.sh / uninstall.sh touch. Copied into a temp directory so a
# test run never writes rules.toml (or anything else) into the real checkout.
REPO_FILES = [
    "install.sh",
    "uninstall.sh",
    "setup.py",
    "route.py",
    "fsops.py",
    "extract.py",
    "dedupe.py",
    "classify.py",
    "memory.py",
    "batch.py",
    "notify.py",
    "plist.py",
    "rules.default.toml",
    "rules.local.default.toml",
]


@pytest.fixture
def repo_copy(tmp_path):
    dest = tmp_path / "repo"
    dest.mkdir()
    for name in REPO_FILES:
        shutil.copy2(REPO / name, dest / name)
    shutil.copytree(REPO / "vault-template", dest / "vault-template")
    return dest


def _stub(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _launchctl_stub(bin_dir: Path, fail_on: str = "") -> Path:
    """A launchctl that logs its arguments; optionally failing some subcommands."""
    guard = ""
    if fail_on:
        guard = f'case " {fail_on} " in *" $1 "*) exit 1 ;; esac\n'
    return _stub(
        bin_dir / "launchctl",
        '#!/bin/bash\necho "launchctl $@" >> "$LAUNCHCTL_LOG"\n' + guard,
    )


@pytest.fixture
def env(tmp_path, repo_copy):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "launchctl.log"
    log.write_text("")
    return {
        "home": home,
        "bin_dir": bin_dir,
        "log": log,
        "repo": repo_copy,
        "vars": {
            **os.environ,
            "HOME": str(home),
            "LAUNCHCTL_LOG": str(log),
        },
    }


def _run(script: Path, env) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(script)], env=env["vars"], capture_output=True, text=True
    )


def _agents(env) -> list[Path]:
    return sorted((env["home"] / "Library/LaunchAgents").glob("com.mac-organizer.*.plist"))


def test_install_then_uninstall(env):
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))

    install = _run(env["repo"] / "install.sh", env)
    assert install.returncode == 0, install.stderr
    # 5 agents, or 4 when the screenshot location is (or defaults to) ~/Desktop
    assert 4 <= len(_agents(env)) <= 5
    assert "DRYRUN" in install.stdout or "dry-run" in install.stdout.lower()
    assert env["log"].read_text().count("launchctl") >= 4
    assert (env["repo"] / "rules.toml").exists()
    assert not (env["home"] / "rules.toml").exists()

    uninstall = _run(env["repo"] / "uninstall.sh", env)
    assert uninstall.returncode == 0, uninstall.stderr
    assert _agents(env) == []


def test_install_is_idempotent_and_falls_back_when_bootstrap_fails(env):
    """I3: re-running must not abort, even when launchctl rejects bootstrap."""
    env["vars"]["LAUNCHCTL"] = str(
        _launchctl_stub(env["bin_dir"], fail_on="bootstrap bootout")
    )

    first = _run(env["repo"] / "install.sh", env)
    assert first.returncode == 0, first.stderr
    agents_after_first = _agents(env)
    assert agents_after_first

    second = _run(env["repo"] / "install.sh", env)
    assert second.returncode == 0, second.stderr
    assert _agents(env) == agents_after_first
    assert "DRYRUN" in second.stdout or "dry-run" in second.stdout.lower()

    calls = env["log"].read_text()
    assert "load -w" in calls, calls  # the bootstrap fallback was exercised


def test_uninstall_falls_back_to_unload_when_bootout_fails(env):
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"], fail_on="bootstrap bootout"))
    assert _run(env["repo"] / "install.sh", env).returncode == 0

    env["log"].write_text("")
    uninstall = _run(env["repo"] / "uninstall.sh", env)

    assert uninstall.returncode == 0, uninstall.stderr
    assert "unload -w" in env["log"].read_text()
    assert _agents(env) == []


def test_install_refuses_python_older_than_3_11(env):
    """I4: /usr/bin/python3 (3.9) has no tomllib; the agents must not be written."""
    _launchctl_stub(env["bin_dir"])
    _stub(
        env["bin_dir"] / "python3",
        '#!/bin/bash\n'
        'if [ "$1" = "-V" ]; then echo "Python 3.9.6"; exit 0; fi\n'
        'exit 1\n',
    )
    env["vars"]["LAUNCHCTL"] = str(env["bin_dir"] / "launchctl")
    env["vars"]["PATH"] = f"{env['bin_dir']}:{env['vars']['PATH']}"

    result = _run(env["repo"] / "install.sh", env)

    assert result.returncode == 1
    assert "3.11" in result.stderr
    assert "Python 3.9.6" in result.stderr
    assert not (env["home"] / "Library/LaunchAgents").exists()


def test_installed_agents_use_a_supported_interpreter(env):
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    assert _run(env["repo"] / "install.sh", env).returncode == 0

    for agent in _agents(env):
        args = plistlib.loads(agent.read_bytes())["ProgramArguments"]
        assert args[1] == str(env["repo"] / "route.py")
        version = subprocess.run(
            [args[0], "-c", "import sys; print(sys.version_info >= (3, 11))"],
            capture_output=True,
            text=True,
        )
        assert version.returncode == 0, version.stderr
        assert version.stdout.strip() == "True"


def test_repo_checkout_is_untouched_by_the_script_tests(env):
    """The scripts run against a copy; the checkout must not gain artifacts."""
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    before = {p.name for p in REPO.iterdir()}

    assert _run(env["repo"] / "install.sh", env).returncode == 0

    assert {p.name for p in REPO.iterdir()} == before
