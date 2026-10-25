"""Eject opened installer disk images (.dmg under $HOME) after they sit idle.

Only images reported by ``hdiutil info`` are considered, so USB, external,
Time Machine and system volumes are never touched. Never uses ``-force``;
a busy volume is simply retried on the next run. The .dmg file is untouched.
"""
from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _mounted_images(runner) -> dict[str, str]:
    """image path -> mount point, for eligible images only. {} on any failure."""
    try:
        res = runner(["hdiutil", "info", "-plist"], capture_output=True,
                     check=False, timeout=30)
        if res.returncode != 0:
            return {}
        stdout = res.stdout
        if isinstance(stdout, str):
            stdout = stdout.encode()
        images = plistlib.loads(stdout)["images"]
        home = Path(os.environ.get("HOME", str(Path.home()))).resolve()
        found: dict[str, str] = {}
        for img in images:
            image_path = img.get("image-path")
            if not isinstance(image_path, str) or not image_path.lower().endswith(".dmg"):
                continue
            try:
                Path(image_path).resolve().relative_to(home)
            except ValueError:
                continue
            for ent in img.get("system-entities", []):
                mp = ent.get("mount-point")
                if isinstance(mp, str) and mp.startswith("/Volumes/") and ".." not in mp:
                    found[image_path] = mp
                    break
        return found
    except Exception:  # noqa: BLE001 - garbled or failing hdiutil: eject nothing
        return {}


def eject_stale(state: Path, now: float, runner=subprocess.run,
                min_age: int = 900, log=None) -> list[str]:
    """Detach installer images first seen >= min_age seconds ago.

    ``log(verb, src, dst)`` is called for each successful eject. Returns the
    ejected mount points.
    """
    state = Path(state)
    mounted = _mounted_images(runner)
    seen = _load(state / "disks.json")
    seen = {img: seen.get(img, now) for img in mounted}  # also prunes unmounted
    ejected: list[str] = []
    for img, mount in mounted.items():
        if now - seen[img] < min_age:
            continue
        try:
            res = runner(["hdiutil", "detach", mount], capture_output=True,
                         check=False, timeout=60)
        except Exception:  # noqa: BLE001 - retry next run
            continue
        if res.returncode != 0:
            continue  # busy: keep the entry, retry next run
        del seen[img]
        ejected.append(mount)
        if log is not None:
            log("EJECT", Path(mount), Path(img))
    state.mkdir(parents=True, exist_ok=True)
    tmp = state / "disks.json.tmp"
    tmp.write_text(json.dumps(seen))
    tmp.replace(state / "disks.json")
    return ejected
