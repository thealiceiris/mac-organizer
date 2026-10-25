"""Set up the mac-organizer environment: folders, rules, launchd agents, vault."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import plist

REPO = Path(__file__).resolve().parent
LABEL_PREFIX = "com.mac-organizer"
SWEEP_INTERVAL = 600

DIRS_TO_CREATE = [
    "~/Inbox",
    "~/Media/Images",
    "~/Media/Audio",
    "~/Media/Video",
    "~/Media/Screenshots",
    "~/Documents/Inbox",
    "~/Documents/Keys",
    "~/Downloads/Installers",
]

WATCHED_SOURCES = ["~/Downloads", "~/Desktop", "~/Inbox"]


def state_dir() -> Path:
    return Path("~/.local/state/mac-organizer").expanduser()


def launch_agents_dir() -> Path:
    return Path("~/Library/LaunchAgents").expanduser()


def detect_screenshot_dir(runner=subprocess.run) -> Path:
    try:
        result = runner(
            ["defaults", "read", "com.apple.screencapture", "location"],
            capture_output=True,
            text=True,
            check=True,
        )
        value = (result.stdout or "").strip()
        if value:
            return Path(value).expanduser()
    # FileNotFoundError is a subclass of OSError; named for documentation
    # (no `defaults` binary on PATH is the common non-macOS case).
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    return Path("~/Desktop").expanduser()


def make_dirs() -> None:
    for entry in DIRS_TO_CREATE:
        Path(entry).expanduser().mkdir(parents=True, exist_ok=True)
    Path("~/Documents/Keys").expanduser().chmod(0o700)
    state_dir().mkdir(parents=True, exist_ok=True)


V2_HEADER = "# mac-organizer v2"


def migrate_rules(out_path: Path) -> Path | None:
    """Move a v1 rules.toml (no v2 header line) aside; return the backup path."""
    if not out_path.exists():
        return None
    if V2_HEADER in out_path.read_text().splitlines():
        return None
    backup = out_path.with_name(out_path.name + ".v1.bak")
    out_path.rename(backup)
    return backup


def write_local_rules(template: Path, out: Path) -> bool:
    if out.exists():
        return False
    out.write_text(template.read_text())
    return True


def write_rules(default_path: Path, out_path: Path, screenshot_dir: Path) -> bool:
    if out_path.exists():
        return False
    text = default_path.read_text().replace("@@SCREENSHOT_DIR@@", str(screenshot_dir))
    out_path.write_text(text)
    return True


def watch_dirs(screenshot_dir: Path) -> list[Path]:
    """Directories to watch, in order, without duplicates.

    On a stock Mac the screenshot location is unset and resolves to
    ``~/Desktop`` — already a watched source. Writing a second agent for it
    would fire two routers on every Desktop change.
    """
    dirs = [Path(s).expanduser() for s in WATCHED_SOURCES]
    dirs.append(Path(screenshot_dir).expanduser())
    return list(dict.fromkeys(dirs))


def agent_label(directory: Path, screenshot_dir: Path) -> str:
    base = [Path(s).expanduser() for s in WATCHED_SOURCES]
    if directory not in base and directory == Path(screenshot_dir).expanduser():
        return f"{LABEL_PREFIX}.screenshots"
    return f"{LABEL_PREFIX}.{directory.name.lower()}"


def agent_labels(screenshot_dir: Path) -> list[str]:
    """The labels install_agents writes: one per watched dir, plus the sweep."""
    dirs = watch_dirs(screenshot_dir)
    return [agent_label(d, screenshot_dir) for d in dirs] + [f"{LABEL_PREFIX}.sweep"]


def install_agents(
    python: str,
    route_py: Path,
    screenshot_dir: Path,
    interval: int = SWEEP_INTERVAL,
) -> list[Path]:
    agents_dir = launch_agents_dir()
    agents_dir.mkdir(parents=True, exist_ok=True)
    sd = state_dir()
    written: list[Path] = []

    dirs = watch_dirs(screenshot_dir)
    for source in dirs:
        label = agent_label(source, screenshot_dir)
        target = agents_dir / f"{label}.plist"
        target.write_bytes(plist.render_watch(label, python, route_py, source, sd))
        written.append(target)

    sweep_label = f"{LABEL_PREFIX}.sweep"
    sweep_target = agents_dir / f"{sweep_label}.plist"
    sweep_target.write_bytes(
        plist.render_sweep(sweep_label, python, route_py, dirs, interval, sd)
    )
    written.append(sweep_target)

    # Drop agents left over from an earlier install with a different watch set
    # (e.g. a custom screenshot folder that has since gone back to ~/Desktop).
    for stale in sorted(agents_dir.glob(f"{LABEL_PREFIX}.*.plist")):
        if stale not in written:
            stale.unlink()
    return written


def copy_vault_template(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for item in sorted(src.rglob("*")):
        rel = item.relative_to(src)
        target = dest / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def main() -> None:
    python = sys.executable
    route_py = REPO / "route.py"
    screenshot_dir = detect_screenshot_dir()

    make_dirs()
    backup = migrate_rules(REPO / "rules.toml")
    wrote_rules = write_rules(
        REPO / "rules.default.toml", REPO / "rules.toml", screenshot_dir
    )
    wrote_local = write_local_rules(
        REPO / "rules.local.default.toml", REPO / "rules.local.toml"
    )
    agents = install_agents(python, route_py, screenshot_dir)
    copy_vault_template(REPO / "vault-template", Path("~/Notes").expanduser())

    print(f"screenshot dir: {screenshot_dir}")
    print(f"rules.toml: {'created' if wrote_rules else 'left as-is (already exists)'}")
    if backup:
        print(f"v1 rules.toml backed up to {backup}")
    print(f"rules.local.toml: {'created' if wrote_local else 'left as-is (already exists)'}")
    for path in agents:
        print(f"agent: {path}")
    print(f"vault: {Path('~/Notes').expanduser()}")


if __name__ == "__main__":
    main()
