"""Render launchd agent property lists for mac-organizer."""

from __future__ import annotations

import plistlib
from pathlib import Path


def _base(
    label: str,
    program_args: list[str],
    state_dir: Path,
    run_at_load: bool = False,
) -> dict:
    return {
        "Label": label,
        "ProgramArguments": program_args,
        "StandardOutPath": str(Path(state_dir) / f"{label}.out.log"),
        "StandardErrorPath": str(Path(state_dir) / f"{label}.err.log"),
        "RunAtLoad": run_at_load,
    }


def render_watch(
    label: str, python: str, route_py: Path, watch_dir: Path, state_dir: Path
) -> bytes:
    data = _base(label, [python, str(route_py), str(watch_dir)], state_dir)
    data["WatchPaths"] = [str(watch_dir)]
    return plistlib.dumps(data)


def render_sweep(
    label: str,
    python: str,
    route_py: Path,
    dirs: list[Path],
    interval: int,
    state_dir: Path,
) -> bytes:
    data = _base(
        label,
        [python, str(route_py), *(str(d) for d in dirs)],
        state_dir,
        run_at_load=True,
    )
    data["StartInterval"] = interval
    return plistlib.dumps(data)
