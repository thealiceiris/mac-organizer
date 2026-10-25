import json
import plistlib
import subprocess

import pytest

import disks

NOW = 1_000_000.0


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def info(*images):
    out = []
    for path, mount in images:
        ents = [{"dev-entry": "/dev/disk9"}]
        if mount:
            ents.append({"dev-entry": "/dev/disk9s1", "mount-point": mount})
        out.append({"image-path": str(path), "system-entities": ents})
    return plistlib.dumps({"images": out})


class Fake:
    def __init__(self, plist, detach_rc=0):
        self.plist, self.detach_rc, self.calls = plist, detach_rc, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[1] == "info":
            return subprocess.CompletedProcess(cmd, 0, stdout=self.plist, stderr=b"")
        return subprocess.CompletedProcess(cmd, self.detach_rc, stdout=b"", stderr=b"busy")

    def detaches(self):
        return [c for c in self.calls if c[1] == "detach"]


def test_first_sighting_records_but_does_not_eject(home, tmp_path):
    img = home / "Downloads" / "App.dmg"
    r = Fake(info((img, "/Volumes/App")))
    assert disks.eject_stale(tmp_path / "st", NOW, runner=r) == []
    assert r.detaches() == []
    assert json.loads((tmp_path / "st" / "disks.json").read_text()) == {str(img): NOW}


def test_ejects_after_15_minutes(home, tmp_path):
    img = home / "Downloads" / "App.dmg"
    r = Fake(info((img, "/Volumes/App")))
    st = tmp_path / "st"
    disks.eject_stale(st, NOW, runner=r)
    logged = []
    out = disks.eject_stale(st, NOW + 900, runner=r, log=lambda *a: logged.append(a))
    assert out == ["/Volumes/App"]
    assert r.detaches() == [["hdiutil", "detach", "/Volumes/App"]]
    assert logged and logged[0][0] == "EJECT"


def test_not_before_15_minutes(home, tmp_path):
    img = home / "App.dmg"
    r = Fake(info((img, "/Volumes/App")))
    disks.eject_stale(tmp_path, NOW, runner=r)
    assert disks.eject_stale(tmp_path, NOW + 899, runner=r) == []


def test_never_passes_force(home, tmp_path):
    img = home / "App.dmg"
    r = Fake(info((img, "/Volumes/App")))
    disks.eject_stale(tmp_path, NOW, runner=r)
    disks.eject_stale(tmp_path, NOW + 5000, runner=r)
    for c in r.calls:
        assert "-force" not in c and "-f" not in c


def test_ignores_non_dmg_and_outside_home_images(home, tmp_path):
    r = Fake(info(
        ("/Library/Developer/CoreSimulator/Volumes/iOS_1/x.dmg", "/Volumes/iOS"),
        (home / "x.iso", "/Volumes/ISO"),
        (home / "ok.dmg", "/System/Volumes/Foo"),
        (home / ".." / "elsewhere.dmg", "/Volumes/E"),
    ))
    disks.eject_stale(tmp_path, NOW, runner=r)
    assert disks.eject_stale(tmp_path, NOW + 5000, runner=r) == []
    assert r.detaches() == []
    assert json.loads((tmp_path / "disks.json").read_text()) == {}


def test_ignores_non_image_volumes(home, tmp_path):
    # A USB drive is not in `hdiutil info`; only listed images are considered.
    r = Fake(info())
    assert disks.eject_stale(tmp_path, NOW + 5000, runner=r) == []
    assert r.detaches() == []


def test_busy_detach_retried_next_run(home, tmp_path):
    img = home / "App.dmg"
    plist = info((img, "/Volumes/App"))
    busy = Fake(plist, detach_rc=16)
    disks.eject_stale(tmp_path, NOW, runner=busy)
    assert disks.eject_stale(tmp_path, NOW + 1000, runner=busy) == []
    assert len(busy.detaches()) == 1
    ok = Fake(plist)
    assert disks.eject_stale(tmp_path, NOW + 1100, runner=ok) == ["/Volumes/App"]


def test_prunes_unmounted(home, tmp_path):
    img = home / "App.dmg"
    disks.eject_stale(tmp_path, NOW, runner=Fake(info((img, "/Volumes/App"))))
    disks.eject_stale(tmp_path, NOW + 10, runner=Fake(info()))
    assert json.loads((tmp_path / "disks.json").read_text()) == {}


def test_hdiutil_failure_or_garbage_ejects_nothing(home, tmp_path):
    assert disks.eject_stale(tmp_path, NOW, runner=Fake(b"not a plist")) == []

    def boom(cmd, **kw):
        raise OSError("no hdiutil")

    assert disks.eject_stale(tmp_path, NOW, runner=boom) == []


def test_main_calls_eject_once_and_survives_failure(tmp_path, monkeypatch, capsys):
    import route

    rules = tmp_path / "rules.toml"
    rules.write_text('[[rule]]\nsource="%s"\naction="move"\next=["zzz"]\ndest="%s"\n'
                     % (tmp_path / "in", tmp_path / "out"))
    monkeypatch.setenv("HOME", str(tmp_path))
    calls = []

    def boom(state, now, **kw):
        calls.append(1)
        raise RuntimeError("x")

    monkeypatch.setattr(disks, "eject_stale", boom)
    argv = ["--rules", str(rules), "--log", str(tmp_path / "l"), "--lock", str(tmp_path / "k")]
    assert route.main(argv) == 0
    assert calls == [1]
    assert route.main(argv + ["--dry-run"]) == 0
    assert calls == [1]
