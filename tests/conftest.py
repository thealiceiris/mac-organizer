import os
import time

import pytest


@pytest.fixture
def make_file():
    """Create a file with a chosen age (seconds since now) via mtime."""

    def _make(path, content="x", age_seconds=3600):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    return _make


@pytest.fixture(autouse=True)
def _no_real_hdiutil(request, monkeypatch):
    """route.main must never reach the real hdiutil from tests."""
    import disks

    if request.module.__name__ != "test_disks":
        monkeypatch.setattr(disks, "eject_stale", lambda *a, **k: [])
