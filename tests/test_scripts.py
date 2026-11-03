import os
import plistlib
import shutil
import stat
import subprocess
import sys
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
    "topics.py",
    "suggest.py",
    "dialog.py",
    "batch.py",
    "backlog.py",
    "notify.py",
    "disks.py",
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
            # a stand-in osacompile that just creates the .app folder
            "MAC_ORGANIZER_OSACOMPILE": str(_stub(
                bin_dir / "osacompile", '#!/bin/bash\nwhile [ "$1" != "-o" ]; do shift; done\nmkdir -p "$2"\n')),
            # never register test copies of the notifier app with Launch Services
            "MAC_ORGANIZER_LSREGISTER": str(_stub(bin_dir / "lsregister", "#!/bin/bash\n")),
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

    app = env["home"] / "Applications/Folder Suggestions.app"
    assert app.is_dir()

    uninstall = _run(env["repo"] / "uninstall.sh", env)
    assert uninstall.returncode == 0, uninstall.stderr
    assert _agents(env) == []
    assert not app.exists()


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


def _fake_python_bin(env) -> Path:
    """A PATH dir holding only python3 (a symlink to the real one) and launchctl."""
    import sys
    only = env["bin_dir"] / "only"
    only.mkdir()
    (only / "python3").symlink_to(sys.executable)
    return only


def test_install_warns_for_missing_tools(env):
    only = _fake_python_bin(env)
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    env["vars"]["PATH"] = f"{only}:/usr/bin:/bin"
    env["vars"]["MAC_ORGANIZER_TOOL_DIRS"] = str(env["bin_dir"] / "nowhere")

    result = _run(env["repo"] / "install.sh", env)

    assert result.returncode == 0, result.stderr
    assert "brew install llama.cpp poppler terminal-notifier" in result.stdout + result.stderr


def test_install_lists_only_missing_tools(env):
    only = _fake_python_bin(env)
    extra = env["bin_dir"] / "extra"
    extra.mkdir()
    _stub(extra / "pdftotext", "#!/bin/bash\n")
    _stub(extra / "llama-server", "#!/bin/bash\n")
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    env["vars"]["PATH"] = f"{only}:/usr/bin:/bin"
    env["vars"]["MAC_ORGANIZER_TOOL_DIRS"] = str(extra)  # found via the fallback dirs

    result = _run(env["repo"] / "install.sh", env)

    out = result.stdout + result.stderr
    assert result.returncode == 0, result.stderr
    assert "brew install terminal-notifier" in out
    assert "poppler" not in out and "llama.cpp" not in out


def test_install_warns_about_missing_tools_before_setup(env):
    only = _fake_python_bin(env)
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    env["vars"]["PATH"] = f"{only}:/usr/bin:/bin"
    env["vars"]["MAC_ORGANIZER_TOOL_DIRS"] = str(env["bin_dir"] / "nowhere")

    result = _run(env["repo"] / "install.sh", env)

    assert result.returncode == 0, result.stderr
    assert result.stdout.index("brew install") < result.stdout.index("screenshot dir:")


def _busy_home(env):
    downloads = env["home"] / "Downloads"
    downloads.mkdir()
    for i in range(51):
        (downloads / f"f{i}.bin").write_text("x")


def _run_input(script, env, text):
    return subprocess.run(
        ["bash", str(script)], env=env["vars"], input=text,
        capture_output=True, text=True,
    )


def test_install_busy_mac_no_skips_loading_agents(env):
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    env["vars"]["MAC_ORGANIZER_FORCE_TTY"] = "1"
    _busy_home(env)

    result = _run_input(env["repo"] / "install.sh", env, "n\n")
    assert result.returncode == 0, result.stderr
    assert "51 items in Downloads" in result.stdout
    assert "Load the background sorter now? [y/N]" in result.stdout
    assert "./install.sh" in result.stdout
    log = env["log"].read_text()
    assert "bootstrap" not in log and "load -w" not in log
    assert _agents(env) == []
    assert (env["repo"] / "rules.toml").exists()
    assert (env["home"] / "Documents" / "Misc").is_dir()


def test_install_busy_mac_no_unloads_loaded_agents(env):
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    env["vars"]["MAC_ORGANIZER_FORCE_TTY"] = "1"
    _busy_home(env)
    agents = env["home"] / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.mac-organizer.sweep.plist").write_text("x")

    result = _run_input(env["repo"] / "install.sh", env, "\n")  # empty answer is No
    assert result.returncode == 0, result.stderr
    log = env["log"].read_text()
    assert "bootout" in log and "bootstrap" not in log and "load -w" not in log
    assert _agents(env) == []  # the stale plist is gone too


def test_install_busy_mac_yes_loads(env):
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    env["vars"]["MAC_ORGANIZER_FORCE_TTY"] = "1"
    _busy_home(env)
    result = _run_input(env["repo"] / "install.sh", env, "Yes\n")
    assert result.returncode == 0, result.stderr
    assert "bootstrap" in env["log"].read_text()


def test_install_assume_yes_loads(env):
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    env["vars"]["MAC_ORGANIZER_FORCE_TTY"] = "1"
    env["vars"]["MAC_ORGANIZER_ASSUME_YES"] = "1"
    _busy_home(env)
    result = _run_input(env["repo"] / "install.sh", env, "")
    assert result.returncode == 0, result.stderr
    assert "Load the background sorter" not in result.stdout
    assert "bootstrap" in env["log"].read_text()


def test_install_quiet_mac_is_not_asked(env):
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    env["vars"]["MAC_ORGANIZER_FORCE_TTY"] = "1"
    result = _run_input(env["repo"] / "install.sh", env, "")
    assert result.returncode == 0, result.stderr
    assert "Load the background sorter" not in result.stdout
    assert "bootstrap" in env["log"].read_text()


def _setup(env, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(env["repo"] / "setup.py"), *args],
        env=env["vars"], capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )


def test_plain_setup_writes_no_agents_and_removes_none(env):
    """C1: `python3 setup.py` (the README's preview step) never arranges a sorter start."""
    result = _setup(env)
    assert result.returncode == 0, result.stderr
    assert _agents(env) == []
    assert (env["repo"] / "rules.toml").exists()
    # an existing install's agents are left exactly as they are
    agents = env["home"] / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "com.mac-organizer.sweep.plist").write_text("x")
    result = _setup(env)
    assert result.returncode == 0, result.stderr
    assert [p.name for p in _agents(env)] == ["com.mac-organizer.sweep.plist"]
    assert (agents / "com.mac-organizer.sweep.plist").read_text() == "x"


def test_setup_agents_flag_writes_and_no_agents_removes(env):
    result = _setup(env, "--agents")
    assert result.returncode == 0, result.stderr
    assert 4 <= len(_agents(env)) <= 5
    result = _setup(env, "--no-agents")
    assert result.returncode == 0, result.stderr
    assert _agents(env) == []


def test_install_busy_mac_yes_writes_and_loads_agents(env):
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    env["vars"]["MAC_ORGANIZER_FORCE_TTY"] = "1"
    _busy_home(env)
    result = _run_input(env["repo"] / "install.sh", env, "y\n")
    assert result.returncode == 0, result.stderr
    assert 4 <= len(_agents(env)) <= 5
    assert env["log"].read_text().count("bootstrap") == len(_agents(env))


# get.sh: the one-line installer. It clones a source repo (a local one here, not
# GitHub) into ~/.mac-organizer and hands over to install.sh.

def _git(*args, cwd):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )


@pytest.fixture
def get_env(env, tmp_path):
    source = env["repo"]
    _git("init", "-q", "-b", "main", cwd=source)
    _git("add", "-A", cwd=source)
    _git("commit", "-q", "-m", "init", cwd=source)
    brew_dir = tmp_path / "brewbin"
    brew_dir.mkdir()
    env["vars"].update({
        "LAUNCHCTL": str(_launchctl_stub(env["bin_dir"])),
        "MAC_ORGANIZER_REPO_URL": str(source),
        "MAC_ORGANIZER_BREW_DIRS": str(brew_dir),
        "MAC_ORGANIZER_FORCE_TTY": "1",
        "PATH": f"{env['bin_dir']}:{Path(sys.executable).parent}:/usr/bin:/bin",
    })
    env["source"] = source
    env["brew_dir"] = brew_dir
    env["checkout"] = env["home"] / ".mac-organizer"
    return env


def _get(env, text=""):
    return subprocess.run(
        ["bash", str(REPO / "get.sh")], env=env["vars"], input=text,
        capture_output=True, text=True,
    )


def _old_python(bin_dir: Path):
    _stub(bin_dir / "python3", "#!/bin/bash\nexit 1\n")


def test_get_clones_and_installs(get_env):
    result = _get(get_env)
    assert result.returncode == 0, result.stderr
    assert (get_env["checkout"] / "install.sh").exists()
    assert (get_env["checkout"] / "rules.toml").exists()
    assert 4 <= len(_agents(get_env)) <= 5


def test_get_updates_an_existing_checkout(get_env):
    assert _get(get_env).returncode == 0
    (get_env["source"] / "NEW.md").write_text("new")
    _git("add", "NEW.md", cwd=get_env["source"])
    _git("commit", "-q", "-m", "new", cwd=get_env["source"])

    result = _get(get_env)
    assert result.returncode == 0, result.stderr
    assert (get_env["checkout"] / "NEW.md").exists()


def test_get_keeps_the_safety_question(get_env):
    """The answer typed in the terminal reaches install.sh's question."""
    _busy_home(get_env)
    result = _get(get_env, "n\n")
    assert result.returncode == 0, result.stderr
    assert "Load the background sorter now? [y/N]" in result.stdout
    assert _agents(get_env) == []
    log = get_env["log"].read_text()
    assert "bootstrap" not in log and "load -w" not in log


def test_get_refuses_without_a_terminal(get_env):
    """`curl ... | bash` would skip install.sh's question, so refuse it."""
    del get_env["vars"]["MAC_ORGANIZER_FORCE_TTY"]
    result = _get(get_env)
    assert result.returncode != 0
    assert '/bin/bash -c "$(curl' in result.stderr
    assert not get_env["checkout"].exists()


def test_get_assume_yes_runs_without_a_terminal(get_env):
    del get_env["vars"]["MAC_ORGANIZER_FORCE_TTY"]
    get_env["vars"]["MAC_ORGANIZER_ASSUME_YES"] = "1"
    result = _get(get_env)
    assert result.returncode == 0, result.stderr
    assert 4 <= len(_agents(get_env)) <= 5


def test_get_refuses_other_systems(get_env):
    _stub(get_env["bin_dir"] / "uname", "#!/bin/bash\necho Linux\n")
    result = _get(get_env)
    assert result.returncode != 0
    assert "macOS" in result.stderr
    assert not get_env["checkout"].exists()


def test_get_refuses_a_folder_that_is_not_a_checkout(get_env):
    get_env["checkout"].mkdir()
    (get_env["checkout"] / "mine.txt").write_text("keep")
    result = _get(get_env)
    assert result.returncode != 0
    assert str(get_env["checkout"]) in result.stderr
    assert (get_env["checkout"] / "mine.txt").read_text() == "keep"


def test_get_explains_missing_git(get_env):
    _stub(get_env["bin_dir"] / "git", "#!/bin/bash\nexit 1\n")
    result = _get(get_env)
    assert result.returncode != 0
    assert "xcode-select --install" in result.stderr


def test_get_without_python_or_homebrew_points_to_brew_sh(get_env):
    _old_python(get_env["bin_dir"])
    result = _get(get_env)
    assert result.returncode != 0
    assert "https://brew.sh" in result.stderr
    assert not get_env["checkout"].exists()


def _brew_stub(env):
    """A brew that logs its arguments and 'installs' a Python 3.11+."""
    return _stub(
        env["brew_dir"] / "brew",
        f'#!/bin/bash\necho "brew $@" >> "{env["log"]}"\n'
        f'ln -sf "{sys.executable}" "{env["brew_dir"]}/python3"\n',
    )


def test_get_asks_before_installing_python_and_no_means_no(get_env):
    _old_python(get_env["bin_dir"])
    _brew_stub(get_env)
    result = _get(get_env, "n\n")
    assert result.returncode != 0
    assert "Install Python with Homebrew now? [y/N]" in result.stdout
    assert "brew install python" in result.stderr
    assert "brew " not in get_env["log"].read_text()
    assert not get_env["checkout"].exists()


def test_get_installs_python_on_yes_and_uses_it(get_env):
    _old_python(get_env["bin_dir"])
    _brew_stub(get_env)
    result = _get(get_env, "y\n")
    assert result.returncode == 0, result.stderr
    assert "brew install python" in get_env["log"].read_text()
    assert 4 <= len(_agents(get_env)) <= 5


def test_get_finds_homebrew_python_off_path(get_env):
    """A Homebrew Python that isn't first on PATH is still found and used."""
    _old_python(get_env["bin_dir"])
    (get_env["brew_dir"] / "python3").symlink_to(sys.executable)
    result = _get(get_env)
    assert result.returncode == 0, result.stderr
    plist = next(iter(_agents(get_env)))
    args = plistlib.loads(plist.read_bytes())["ProgramArguments"]
    assert Path(args[0]).resolve() == Path(sys.executable).resolve()
    assert not args[0].startswith(str(get_env["bin_dir"]))


def test_uninstall_removes_the_folder_icon_notifier(env):
    env["vars"]["LAUNCHCTL"] = str(_launchctl_stub(env["bin_dir"]))
    app = env["home"] / ".local/state/mac-organizer/mac-organizer.app"
    (app / "Contents").mkdir(parents=True)
    log = env["home"] / ".local/state/mac-organizer/actions.log"
    log.write_text("kept")
    result = _run(env["repo"] / "uninstall.sh", env)
    assert result.returncode == 0, result.stderr
    assert not app.exists()
    assert log.read_text() == "kept"
