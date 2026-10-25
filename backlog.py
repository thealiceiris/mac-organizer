"""Backlog clean-up: a reviewable dry-run report, then apply exactly the approved lines.

``plan`` routes the existing items in ``~/Documents/Inbox`` and ``~/Downloads``
through the same per-item decision as live routing (``route.plan_item``) and
writes a TSV report. It moves nothing. The user vetoes lines by deleting them
or prefixing them with ``#``; ``apply`` then performs only the remaining lines,
re-checking each one first.
"""

from __future__ import annotations

import fnmatch
import itertools
import json
import os
import re
import shlex
import sys
import time
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import dedupe as dupes
import fsops
import memory
import route
from route import Action

LEFTOVER_DIRS = ("Keys", "Images", "Documents", "Installers & Archives")
PROJECT_MARKERS = ("package.json", "pyproject.toml", "pubspec.yaml", "Cargo.toml", "go.mod")
PROJECT_SIBLING_DIRS = frozenset({"app", "components", "lib", "public", "src", "pages",
                                  "styles", "assets"})
PROJECT_SIBLING_GLOBS = ("*.config.*", "tsconfig*.json", "*lock*", ".env*", ".gitignore",
                         "components.json", "next-env.d.ts", "postcss.config.*")
# "*lock*" alone would also catch "Blockchain notes.pdf": lock files have these suffixes.
LOCK_SUFFIXES = frozenset({"", ".json", ".yaml", ".yml", ".lock", ".lockb", ".toml"})
ACTIONS = ("MOVE", "TRASH", "GROUP")
UNSAFE_CHARS = ("\t", "\n", "\r")
IGNORED = ".DS_Store"


def _downloads() -> Path:
    return Path.home() / "Downloads"


def _inbox() -> Path:
    return Path.home() / "Documents" / "Inbox"


def _development() -> Path:
    return Path.home() / "Documents" / "Development"


def _trash_dir() -> Path:
    return Path.home() / ".Trash"


@dataclass(frozen=True)
class Line:
    action: str  # MOVE | TRASH | GROUP
    src: Path
    dst: Path  # TRASH of a duplicate: the identical item that is kept
    reason: str = ""
    folder: str = ""  # candidate name for a smart decision, else empty

    def tsv(self) -> str:
        reason = " ".join(self.reason.split())
        return "\t".join([self.action, str(self.src), str(self.dst), reason, self.folder])


class ReportError(ValueError):
    """The report has lines that are not valid report lines; nothing was applied."""


# ---------------------------------------------------------------- planning


def plan(paths: list[Path], config, smart, now: float, *, state: Path | None = None) -> Path:
    """Write ``reports/backlog-<ts>.tsv`` for ``paths`` and print its path. Moves nothing.

    The model (``smart``) starts lazily, at most once, and is always stopped here.
    """
    state = Path(state) if state is not None else route.default_state_dir()
    lines: list[Line] = []
    notes: list[str] = []
    try:
        for p in paths:
            lines.extend(_plan_dir(Path(p).expanduser(), config, smart, now, notes))
    finally:
        if smart is not None:
            smart.close()  # the model is never left running
    lines = _settle_duplicates(lines)
    outage = smart is not None and smart.outage
    report = _write_report(state, now, lines, notes, outage)
    counts = Counter(ln.action for ln in lines)
    print(report)
    print("  ".join(f"{a} {counts[a]}" for a in ACTIONS))
    return report


def _plan_dir(directory: Path, config, smart, now: float, notes: list[str]) -> list[Line]:
    try:
        children = sorted(directory.iterdir())
    except OSError as exc:
        print(f"ERROR  {directory}: {exc}", file=sys.stderr)
        notes.append(f"could not read {directory}: {exc}")
        return []
    resolved = directory.resolve()
    out: list[Line] = []
    if resolved == _inbox().resolve():
        # Inbox files go through the ~/Downloads file rules; folders stay.
        for child in children:
            if child.is_file():
                out += _plan_one(child, config, smart, now, _downloads(), notes, inbox=True)
        return out
    if resolved != _downloads().resolve():
        for child in children:
            out += _plan_one(child, config, smart, now, directory, notes)
        return out
    group, members = _project_group(directory, children, now)
    out += group
    for child in children:
        if child in members:
            continue
        if child.name in LEFTOVER_DIRS and child.is_dir() and not child.is_symlink():
            try:
                inner = sorted(child.iterdir())
            except OSError as exc:
                print(f"ERROR  {child}: {exc}", file=sys.stderr)
                continue
            for item in inner:  # unpacked: each planned as if it sat in ~/Downloads
                out += _plan_one(item, config, smart, now, directory, notes)
            continue
        out += _plan_one(child, config, smart, now, directory, notes)
    return out


def _plan_one(path: Path, config, smart, now: float, source: Path, notes: list[str],
              inbox: bool = False) -> list[Line]:
    if any(c in str(path) for c in UNSAFE_CHARS):
        notes.append(f"skipped (tab or line break in its name): {str(path)!r}")
        return []
    try:
        planned = route.plan_item(path, config, smart, now, source=source)
    except Exception as exc:  # noqa: BLE001 - planning one item never stops the report
        print(f"ERROR  {path}: {exc!r}", file=sys.stderr)
        notes.append(f"not planned (error): {path}")
        return []
    if planned is None:
        return []
    if inbox and planned.rule.action == "smart" and planned.folder is None:
        return []  # unsure: it stays in the Inbox
    if any(c in str(planned.action.dst) for c in UNSAFE_CHARS):
        notes.append(f"skipped (tab or line break in its destination): {str(path)!r}")
        return []
    if planned.action.verb == "TRASH":
        return [Line("TRASH", path, planned.action.dst, planned.reason)]
    return [Line("MOVE", path, planned.action.dst, planned.reason, planned.folder or "")]


def _digest(path: Path) -> str | None:
    try:
        if path.is_dir() and not path.is_symlink():
            return dupes.tree_digest(path)
        return dupes.file_digest(path)
    except OSError:
        return None


def _settle_duplicates(lines: list[Line]) -> list[Line]:
    """Identical items in one run: the first MOVE stays a MOVE, the rest become TRASH."""
    arrivals: dict[Path, dict[str, Path]] = {}  # dest_dir -> {digest: planned path}
    out: list[Line] = []
    for ln in lines:
        if ln.action != "MOVE":
            out.append(ln)
            continue
        extra = arrivals.setdefault(ln.dst.parent, {})
        twin = dupes.find_identical(ln.src, ln.dst.parent, extra=extra)
        if twin is not None:
            out.append(Line("TRASH", ln.src, twin, f"identical to {twin}"))
            continue
        digest = _digest(ln.src)
        if digest is not None:
            extra[digest] = ln.dst
        out.append(ln)
    return out


# ---------------------------------------------------------------- project groups


def _sanitise(name: str) -> str:
    name = re.sub(r"[^\w .-]+", "-", name.strip().lstrip("@"))
    return re.sub(r"-{2,}", "-", name).strip(" .-")[:100]


def project_name(marker: Path) -> str:
    """The ``name`` in a project marker, sanitised; else the marker's folder name."""
    name = None
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
        if marker.name == "package.json":
            name = json.loads(text).get("name")
        elif marker.name in ("pyproject.toml", "Cargo.toml"):
            data = tomllib.loads(text)
            name = (data.get("project", {}).get("name")
                    or data.get("tool", {}).get("poetry", {}).get("name")
                    or data.get("package", {}).get("name"))
        elif marker.name == "pubspec.yaml":
            m = re.search(r"^name:\s*['\"]?([^'\"\s#]+)", text, re.M)
            name = m.group(1) if m else None
        elif marker.name == "go.mod":
            m = re.search(r"^module\s+(\S+)", text, re.M)
            name = m.group(1).rstrip("/").rsplit("/", 1)[-1] if m else None
    except (OSError, ValueError, AttributeError):
        name = None
    clean = _sanitise(name) if isinstance(name, str) else ""
    return clean or _sanitise(marker.parent.name) or "project"


def _settled(path: Path, now: float) -> bool:
    try:
        if path.is_dir():
            return route.dir_is_settled(path, now)
        return now - path.stat().st_mtime >= route.MIN_AGE_SECONDS
    except OSError:
        return False


def _is_sibling(path: Path) -> bool:
    if path.is_symlink():
        return False
    if path.is_dir():
        return path.name in PROJECT_SIBLING_DIRS
    if not path.is_file():
        return False
    for pattern in PROJECT_SIBLING_GLOBS:
        if fnmatch.fnmatchcase(path.name, pattern):
            if pattern == "*lock*" and path.suffix.lower() not in LOCK_SUFFIXES:
                continue
            return True
    return False


def _free_group_dir(name: str) -> Path:
    base = _development() / name
    for n in itertools.count(1):
        cand = base if n == 1 else base.with_name(f"{name} -{n}")
        if not os.path.lexists(cand):
            return cand


def _project_group(directory: Path, children: list[Path], now: float):
    """GROUP lines for loose project files in ``directory``, and the members' paths."""
    markers = [directory / m for m in PROJECT_MARKERS
               if (directory / m).is_file() and _settled(directory / m, now)]
    if not markers:
        return [], set()
    members = [c for c in children
               if (c in markers or _is_sibling(c)) and _settled(c, now)
               and not any(ch in str(c) for ch in UNSAFE_CHARS)]
    group_dir = _free_group_dir(project_name(markers[0]))
    reason = f"project files next to {markers[0].name}"
    lines = [Line("GROUP", m, group_dir / m.name, reason) for m in members]
    return lines, set(members)


# ---------------------------------------------------------------- report


def _write_report(state: Path, now: float, lines: list[Line], notes: list[str],
                  outage: bool) -> Path:
    reports = state / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    for n in itertools.count(1):
        path = reports / (f"backlog-{stamp}.tsv" if n == 1 else f"backlog-{stamp}-{n}.tsv")
        try:
            fh = open(path, "x", encoding="utf-8", newline="\n")
        except FileExistsError:
            continue
        break
    counts = Counter(ln.action for ln in lines)
    route_py = Path(route.__file__).resolve()
    header = [
        f"mac-organizer backlog report, planned {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}."
        " Nothing has moved yet.",
        "Each line is one proposed action: action<TAB>source<TAB>destination<TAB>reason<TAB>folder",
        "  MOVE  = move the item there (a name already taken gets ' -2')",
        "  TRASH = move the item to the Trash (never deleted); for a duplicate, the",
        "          destination column is the identical copy that is kept",
        "  GROUP = move a loose project file into a NEW folder under ~/Documents/Development",
        "To veto a line: delete it, or put # at the start of it. Do not edit lines otherwise.",
        "To apply all lines that are left, run:",
        f"  python3 {shlex.quote(str(route_py))} --backlog --apply {shlex.quote(str(path))}",
        f"Counts: MOVE {counts['MOVE']}, TRASH {counts['TRASH']}, GROUP {counts['GROUP']}"
        f" ({len(lines)} lines)",
        f"Leftover folders in ~/Downloads ({', '.join(LEFTOVER_DIRS)}) are removed on apply"
        " only if they end up empty.",
    ]
    if outage:
        header.append("WARNING: AI sorting was unavailable while planning: unsure items fell back"
                      " to their rule's folder or stay where they are.")
    header += notes
    with fh:
        fh.write("".join(f"# {h}\n" for h in header))
        fh.write("".join(ln.tsv() + "\n" for ln in lines))
    return path


def read_report(report: Path) -> list[Line]:
    """The approved (non-``#``) lines. Raises ReportError if any line is not valid."""
    lines: list[Line] = []
    errors: list[str] = []
    text = Path(report).read_bytes().decode("utf-8")
    for n, raw in enumerate(text.split("\n"), 1):
        raw = raw.removesuffix("\r")
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        f = raw.split("\t")
        if len(f) == 4:
            f.append("")
        if len(f) != 5 or f[0] not in ACTIONS or not f[1].startswith("/") \
                or not f[2].startswith("/"):
            errors.append(f"line {n}: not a report line: {raw!r}")
            continue
        line = Line(f[0], Path(f[1]), Path(f[2]), f[3], f[4])
        if line.action == "GROUP" and (line.dst.parent.parent != _development()
                                       or line.dst.name != line.src.name):
            errors.append(f"line {n}: GROUP destination must be "
                          f"{_development()}/<name>/{line.src.name}")
            continue
        lines.append(line)
    if errors:
        raise ReportError("\n".join(errors))
    return lines


# ---------------------------------------------------------------- apply


def _identical(a: Path, b: Path) -> bool:
    try:
        if a.resolve() == b.resolve():
            return False  # never trash the only copy
        a_dir, b_dir = a.is_dir() and not a.is_symlink(), b.is_dir() and not b.is_symlink()
        if a_dir != b_dir:
            return False
        if a_dir:
            return dupes.tree_digest(a) == dupes.tree_digest(b)
        return (a.is_file() and b.is_file() and a.stat().st_size == b.stat().st_size
                and dupes.file_digest(a) == dupes.file_digest(b))
    except OSError:
        return False


def _claim_group_dir(planned: Path) -> Path:
    """Create the project folder; an existing folder of that name is never merged into."""
    for n in itertools.count(1):
        cand = planned if n == 1 else planned.with_name(f"{planned.name} -{n}")
        try:
            cand.mkdir()
        except FileExistsError:
            continue
        print(f"CREATED  {cand}")
        return cand


class _Skip(Exception):
    pass


def apply(report: Path, log_path: Path) -> list[Action]:
    """Perform the report's non-``#`` lines, each re-checked first. Returns what was done."""
    lines = read_report(report)
    state = route.default_state_dir()
    now = time.time()
    done: list[Action] = []
    tally: Counter = Counter()
    moved: dict[Path, Path] = {}  # a line's src and planned dst -> where it really went
    groups: dict[Path, Path] = {}  # planned project folder -> folder created this run
    for ln in lines:
        try:
            outcome, action = _apply_line(ln, log_path, state, now, moved, groups)
        except _Skip as why:
            tally["skipped"] += 1
            print(f"SKIPPED  {ln.src}: {why}")
            continue
        except OSError as exc:
            tally["failed"] += 1
            print(f"FAILED   {ln.src}: {exc}")
            route._log(log_path, "ERROR", ln.src, ln.dst)
            continue
        tally[outcome] += 1
        done.append(action)
        print(f"{outcome.upper():<8} {action.src} -> {action.dst}")
    for d in sorted({ln.src.parent for ln in lines}):
        if d.name in LEFTOVER_DIRS and d.parent.resolve() == _downloads().resolve():
            _remove_if_empty(d)
    print("Backlog applied: " + ", ".join(
        f"{tally[k]} {label}" for k, label in [
            ("moved", "moved"), ("grouped", "grouped"), ("trashed", "to Trash"),
            ("duplicate", "duplicates to Trash"), ("skipped", "skipped"), ("failed", "failed")]))
    return done


def _apply_line(ln: Line, log_path: Path, state: Path, now: float,
                moved: dict[Path, Path], groups: dict[Path, Path]) -> tuple[str, Action]:
    if not os.path.lexists(ln.src):
        raise _Skip("missing (already moved or deleted)")
    if not route.within_home(ln.src) or not route.within_home(ln.dst):
        raise _Skip(f"outside home: {ln.dst}")
    if ln.action == "MOVE":
        action = Action(ln.src, ln.dst, verb="SMART" if ln.folder else "MOVE",
                        no_mkdir=bool(ln.folder))
        try:
            result = route.apply_action(action, False, log_path)
        except fsops.DestDirMissing:
            raise _Skip(f"folder {ln.dst.parent} no longer exists (never recreated)") from None
        if result.duplicate:
            return "duplicate", result
        moved[ln.src] = moved[ln.dst] = result.dst
        if ln.folder:
            try:
                memory.record_placement(state, result.dst, ln.folder, ln.reason, now)
            except Exception as exc:  # noqa: BLE001 - bookkeeping never undoes a move
                print(f"ERROR  recording {result.dst}: {exc!r}", file=sys.stderr)
        return "moved", result
    if ln.action == "TRASH":
        if ln.dst.parent.resolve() == _trash_dir().resolve():
            return "trashed", route.apply_action(Action(ln.src, ln.dst, verb="TRASH"),
                                                 False, log_path)
        twin = moved.get(ln.dst, ln.dst)
        if not _identical(ln.src, twin):
            raise _Skip(f"kept: its identical copy {twin} is not there")
        fsops.trash(ln.src)
        route._log(log_path, "DUPLICATE", ln.src, twin)
        return "duplicate", Action(ln.src, twin, verb="TRASH", duplicate=True)
    # GROUP: the one place a folder is created, and only for an approved line
    folder = groups.get(ln.dst.parent)
    if folder is None:
        folder = groups[ln.dst.parent] = _claim_group_dir(ln.dst.parent)
    target = folder / ln.dst.name
    if ln.src.is_dir() and not ln.src.is_symlink():
        final = fsops.move_dir_no_clobber(ln.src, target, mkdir=False)
    else:
        final = fsops.move_no_clobber(ln.src, target, mkdir=False)
    route._log(log_path, "MOVE", ln.src, final)
    moved[ln.src] = moved[ln.dst] = final
    return "grouped", Action(ln.src, final)


def _remove_if_empty(directory: Path) -> None:
    """rmdir a leftover folder holding nothing but .DS_Store (which goes to the Trash)."""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    if any(e.name != IGNORED for e in entries):
        print(f"KEPT     {directory} (not empty)")
        return
    try:
        for e in entries:
            fsops.trash(e)
        directory.rmdir()
    except OSError as exc:
        print(f"FAILED   removing {directory}: {exc}")
        return
    print(f"REMOVED  {directory} (empty)")
