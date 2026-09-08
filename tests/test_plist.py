import plistlib
from pathlib import Path

import plist


def test_render_watch_structure(tmp_path):
    data = plistlib.loads(
        plist.render_watch(
            "com.mac-organizer.downloads",
            "/opt/homebrew/bin/python3",
            Path("/repo/route.py"),
            Path("/home/me/Downloads"),
            tmp_path,
        )
    )
    assert data["Label"] == "com.mac-organizer.downloads"
    assert data["ProgramArguments"] == [
        "/opt/homebrew/bin/python3",
        "/repo/route.py",
        "/home/me/Downloads",
    ]
    assert data["WatchPaths"] == ["/home/me/Downloads"]
    assert data["RunAtLoad"] is False
    assert data["StandardOutPath"].startswith(str(tmp_path))


def test_render_sweep_structure(tmp_path):
    data = plistlib.loads(
        plist.render_sweep(
            "com.mac-organizer.sweep",
            "/opt/homebrew/bin/python3",
            Path("/repo/route.py"),
            [Path("/home/me/Downloads"), Path("/home/me/Desktop")],
            600,
            tmp_path,
        )
    )
    assert data["StartInterval"] == 600
    assert data["RunAtLoad"] is True
    assert "WatchPaths" not in data
    assert data["ProgramArguments"][2:] == ["/home/me/Downloads", "/home/me/Desktop"]
