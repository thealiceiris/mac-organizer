"""What one routing run did, for the notification summary and the batch report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

KINDS = frozenset({"rule", "smart", "inbox", "duplicate", "stay"})
NOTABLE_KINDS = frozenset({"smart", "inbox", "duplicate"})


@dataclass(frozen=True)
class Entry:
    src: Path
    dst: Path
    folder: str | None  # candidate name for "smart" (and smart duplicates), else None
    kind: str
    reason: str = ""
    smart: bool = False  # the item matched a smart rule


@dataclass
class Batch:
    entries: list[Entry] = field(default_factory=list)
    ai_unavailable: bool = False

    def add(self, src: Path, dst: Path, folder: str | None, kind: str,
            reason: str = "", smart: bool = False) -> Entry:
        if kind not in KINDS:
            raise ValueError(f"unknown batch entry kind: {kind}")
        entry = Entry(Path(src), Path(dst), folder, kind, reason, smart)
        self.entries.append(entry)
        return entry

    @property
    def notable(self) -> bool:
        """Worth a summary: something smart-routed moved, or went to the Trash."""
        return any(e.smart and e.kind in NOTABLE_KINDS for e in self.entries)


class Notifier(Protocol):
    def start(self) -> None: ...

    def summary(self, batch: Batch) -> object: ...

    def unavailable(self) -> None: ...


class NullNotifier:
    """Used when no notifier is configured: every call is a no-op."""

    def start(self) -> None:
        pass

    def summary(self, batch: Batch) -> None:
        pass

    def unavailable(self) -> None:
        pass
