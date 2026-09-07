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
    "~/Media/Screenshots",
    "~/Documents/Inbox",
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
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    return Path("~/Desktop").expanduser()


def make_dirs() -> None:
    for entry in DIRS_TO_CREATE:
        Path(entry).expanduser().mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)


def write_rules(default_path: Path, out_path: Path, screenshot_dir: Path) -> bool:
    if out_path.exists():
        return False
    text = default_path.read_text().replace("@@SCREENSHOT_DIR@@", str(screenshot_dir))
    out_path.write_text(text)
    return True


def agent_labels() -> list[str]:
    suffixes = [s.strip("~/").lower() for s in WATCHED_SOURCES]
    return (
        [f"{LABEL_PREFIX}.{s}" for s in suffixes]
        + [f"{LABEL_PREFIX}.screenshots", f"{LABEL_PREFIX}.sweep"]
    )


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

    watch_dirs = [Path(s).expanduser() for s in WATCHED_SOURCES]
    for source in watch_dirs:
        label = f"{LABEL_PREFIX}.{source.name.lower()}"
        target = agents_dir / f"{label}.plist"
        target.write_bytes(plist.render_watch(label, python, route_py, source, sd))
        written.append(target)

    ss_label = f"{LABEL_PREFIX}.screenshots"
    ss_target = agents_dir / f"{ss_label}.plist"
    ss_target.write_bytes(
        plist.render_watch(ss_label, python, route_py, screenshot_dir, sd)
    )
    written.append(ss_target)

    sweep_label = f"{LABEL_PREFIX}.sweep"
    sweep_target = agents_dir / f"{sweep_label}.plist"
    sweep_target.write_bytes(
        plist.render_sweep(
            sweep_label, python, route_py, watch_dirs + [screenshot_dir], interval, sd
        )
    )
    written.append(sweep_target)
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
    wrote_rules = write_rules(
        REPO / "rules.default.toml", REPO / "rules.toml", screenshot_dir
    )
    agents = install_agents(python, route_py, screenshot_dir)
    copy_vault_template(REPO / "vault-template", Path("~/Notes").expanduser())

    print(f"screenshot dir: {screenshot_dir}")
    print(f"rules.toml: {'created' if wrote_rules else 'left as-is (already exists)'}")
    for path in agents:
        print(f"agent: {path}")
    print(f"vault: {Path('~/Notes').expanduser()}")


if __name__ == "__main__":
    main()
