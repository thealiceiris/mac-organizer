"""Set up the mac-organizer environment: folders, rules, launchd agents, vault."""

from __future__ import annotations

import subprocess
from pathlib import Path

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
