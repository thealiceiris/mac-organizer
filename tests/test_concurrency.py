"""Concurrency regression tests for C1: concurrent routers must not lose files."""

import fcntl
import os
import threading
import time
from pathlib import Path

import route

N_FILES = 200


def _write_rules(path, home):
    path.write_text(
        f"""
[[rule]]
source = "{home}/Downloads"
ext = ["pdf"]
action = "move"
dest = "{home}/Documents/Inbox"

[[rule]]
source = "{home}/Inbox"
ext = ["pdf"]
action = "move"
dest = "{home}/Documents/Inbox"
"""
    )


def _seed(directory: Path, tag: str) -> set[str]:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.time() - 3600
    contents = set()
    for i in range(N_FILES):
        body = f"{tag}-{i}"
        target = directory / f"f{i}.pdf"
        target.write_text(body)
        os.utime(target, (stamp, stamp))
        contents.add(body)
    return contents


def test_concurrent_routers_lose_no_files(tmp_path, monkeypatch):
    """Two routers draining same-basename files into one destination.

    Even with the single-instance lock bypassed (each run uses its own lock
    file), the atomic claim in ``move_no_clobber`` must preserve every file:
    nothing overwritten, nothing deleted.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = _seed(tmp_path / "Downloads", "downloads")
    expected |= _seed(tmp_path / "Inbox", "inbox")
    rules_file = tmp_path / "rules.toml"
    _write_rules(rules_file, tmp_path)
    dest = tmp_path / "Documents/Inbox"
    dest.mkdir(parents=True, exist_ok=True)

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def run(source: str, lock_name: str):
        argv = [
            "--rules", str(rules_file),
            "--log", str(tmp_path / "state/actions.log"),
            "--lock", str(tmp_path / "state" / lock_name),
            str(tmp_path / source),
        ]
        try:
            barrier.wait()
            route.main(argv)
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assert below
            errors.append(exc)

    threads = [
        threading.Thread(target=run, args=("Downloads", "a.lock")),
        threading.Thread(target=run, args=("Inbox", "b.lock")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    landed = [p for p in dest.iterdir() if p.is_file()]
    left = list((tmp_path / "Downloads").iterdir()) + list((tmp_path / "Inbox").iterdir())
    assert left == []
    assert len(landed) == 2 * N_FILES, "files were silently overwritten"
    assert {p.read_text() for p in landed} == expected


def test_concurrent_routers_log_the_path_actually_used(tmp_path, monkeypatch):
    """Every MOVE line must name a destination that really exists."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed(tmp_path / "Downloads", "downloads")
    _seed(tmp_path / "Inbox", "inbox")
    rules_file = tmp_path / "rules.toml"
    _write_rules(rules_file, tmp_path)
    log = tmp_path / "state/actions.log"

    barrier = threading.Barrier(2)

    def run(source: str, lock_name: str):
        barrier.wait()
        route.main([
            "--rules", str(rules_file),
            "--log", str(log),
            "--lock", str(tmp_path / "state" / lock_name),
            str(tmp_path / source),
        ])

    threads = [
        threading.Thread(target=run, args=("Downloads", "a.lock")),
        threading.Thread(target=run, args=("Inbox", "b.lock")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = [line for line in log.read_text().splitlines() if "  MOVE  " in line]
    assert len(lines) == 2 * N_FILES
    destinations = [line.split(" -> ", 1)[1] for line in lines]
    assert len(set(destinations)) == len(destinations), "two moves claimed one path"
    for dst in destinations:
        assert Path(dst).is_file(), dst


def test_second_concurrent_run_exits_immediately(tmp_path, monkeypatch, capsys):
    """The flock single-instance guard makes a concurrent second pass a no-op."""
    monkeypatch.setenv("HOME", str(tmp_path))
    _seed(tmp_path / "Downloads", "downloads")
    rules_file = tmp_path / "rules.toml"
    _write_rules(rules_file, tmp_path)
    lock_path = tmp_path / "state/route.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        rc = route.main([
            "--rules", str(rules_file),
            "--log", str(tmp_path / "state/actions.log"),
            "--lock", str(lock_path),
            str(tmp_path / "Downloads"),
        ])
    finally:
        os.close(fd)

    assert rc == 0
    assert len(list((tmp_path / "Downloads").iterdir())) == N_FILES
    assert not (tmp_path / "Documents/Inbox").exists()
    assert "already running" in capsys.readouterr().out


def test_lock_is_released_when_the_run_finishes(tmp_path, monkeypatch):
    """A sequential second run is not blocked by the first run's lock."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Downloads").mkdir()
    rules_file = tmp_path / "rules.toml"
    _write_rules(rules_file, tmp_path)
    argv = [
        "--rules", str(rules_file),
        "--log", str(tmp_path / "state/actions.log"),
        str(tmp_path / "Downloads"),
    ]

    assert route.main(argv) == 0
    make = tmp_path / "Downloads/late.pdf"
    make.write_text("late")
    stamp = time.time() - 3600
    os.utime(make, (stamp, stamp))

    assert route.main(argv) == 0
    assert (tmp_path / "Documents/Inbox/late.pdf").read_text() == "late"
