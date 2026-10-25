"""Calm notifications: one "being sorted" ping, one summary with a clickable report."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from batch import Batch

TITLE = "mac-organizer"
START_TEXT = "Files are being sorted…"
UNAVAILABLE_TEXT = "AI sorting unavailable — files will wait in Inbox"
REPORT_KEEP_DAYS = 30
FALLBACK_PATHS = ("/opt/homebrew/bin/terminal-notifier", "/usr/local/bin/terminal-notifier")


def _label(folder: str | None) -> str:
    return Path(folder).name if folder else "Inbox"


def summary_text(batch: Batch) -> str:
    """Only smart-routed items are mentioned; rule-only moves stay silent."""
    smart = [e for e in batch.entries if e.smart]
    groups: dict[str, int] = {}
    inbox = dupes = stay = 0
    for e in smart:
        if e.kind == "smart":
            name = _label(e.folder)
            groups[name] = groups.get(name, 0) + 1
        elif e.kind == "inbox":
            inbox += 1
        elif e.kind == "duplicate":
            dupes += 1
        elif e.kind == "stay":
            stay += 1
    total = sum(groups.values()) + inbox + dupes
    parts = [f"{n} → {name}" for name, n in groups.items()]
    if inbox:
        parts.append(f"{inbox} left in Inbox")
    if dupes:
        parts.append(f"{dupes} duplicates to Trash")
    if total:
        text = f"{total} file{'' if total == 1 else 's'} sorted: " + ", ".join(parts)
        if stay:
            text += f", {stay} left in place"
    else:
        text = f"No files moved: {stay} left in place"
    if batch.ai_unavailable:
        text += ", AI sorting unavailable"
    return text


def _report_line(e) -> str:
    if e.kind == "duplicate":
        where = "Trash (duplicate)"
    elif e.kind == "inbox":
        where = "Inbox"
    else:
        where = _label(e.folder)
    line = f"{e.src.name}  →  {where}"
    return f"{line}  — {e.reason}" if e.reason else line


def _applescript_quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


class Notifier:
    def __init__(self, state: Path, runner=subprocess.run, which=shutil.which):
        self.state = Path(state)
        self.runner = runner
        self.which = which
        self._fallback_paths = FALLBACK_PATHS

    def start(self) -> None:
        self._send(START_TEXT)

    def unavailable(self) -> None:
        self._send(UNAVAILABLE_TEXT)

    def summary(self, batch: Batch) -> Path:
        report = self._write_report(batch)
        self._send(summary_text(batch), report)
        return report

    def _write_report(self, batch: Batch) -> Path:
        directory = self.state / "reports"
        directory.mkdir(parents=True, exist_ok=True)
        self._prune(directory)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        report = directory / f"{stamp}.txt"
        lines = [_report_line(e) for e in batch.entries
                 if e.smart and e.kind in ("smart", "inbox", "duplicate")]
        report.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return report

    @staticmethod
    def _prune(directory: Path) -> None:
        cutoff = time.time() - REPORT_KEEP_DAYS * 86400
        for old in directory.glob("*.txt"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass

    def _terminal_notifier(self) -> str | None:
        found = self.which("terminal-notifier")
        if found:
            return found
        for path in self._fallback_paths:
            if os.access(path, os.X_OK):
                return path
        return None

    def _send(self, message: str, report: Path | None = None) -> None:
        try:
            tn = self._terminal_notifier()
            if tn:
                cmd = [tn, "-title", TITLE, "-message", message, "-group", TITLE]
                if report is not None:
                    cmd += ["-open", f"file://{report}"]
            else:
                script = (f'display notification "{_applescript_quote(message)}" '
                          f'with title "{TITLE}"')
                cmd = ["osascript", "-e", script]
            result = self.runner(cmd, capture_output=True, timeout=10, check=False)
            if getattr(result, "returncode", 0):
                print(f"notification failed: exit {result.returncode}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - a notification never fails the run
            print(f"notification failed: {exc!r}", file=sys.stderr)
