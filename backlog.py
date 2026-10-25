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
from dataclasses import dataclass, field, replace
from pathlib import Path

import classify
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
    problem: str = field(default="", compare=False)  # set by read_report: reject this line

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


def _settle_duplicates(lines: list[Line]) -> list[Line]:
    """Identical items in one run: the first MOVE stays a MOVE, the rest become TRASH.

    Earlier MOVEs into the same folder are simulated arrivals; they are compared
    with the same cheap checks (size, layout) before anything is hashed.
    """
    arrivals: dict[Path, list[tuple[Path, Path]]] = {}  # dest_dir -> [(src, planned path)]
    out: list[Line] = []
    for ln in lines:
        if ln.action != "MOVE":
            out.append(ln)
            continue
        extra = arrivals.setdefault(ln.dst.parent, [])
        twin = dupes.find_identical(ln.src, ln.dst.parent, extra=extra)
        if twin is not None:
            out.append(Line("TRASH", ln.src, twin, f"identical to {twin}"))
            continue
        extra.append((ln.src, ln.dst))
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
    header.append(f"planned-at: {now!r}")
    with fh:
        fh.write("".join(f"# {h}\n" for h in header))
        fh.write("".join(ln.tsv() + "\n" for ln in lines))
    return path


PLANNED_AT = "# planned-at: "


def read_report(report: Path) -> tuple[float, list[Line]]:
    """The plan time and the approved (non-``#``) lines.

    Raises ReportError if the report is not one ``plan`` wrote or a line is not
    a report line at all (nothing is applied then). Lines that are well formed
    but not normalised carry a ``problem`` and are rejected one by one on apply.
    """
    lines: list[Line] = []
    errors: list[str] = []
    planned_at = None
    text = Path(report).read_bytes().decode("utf-8")
    for n, raw in enumerate(text.split("\n"), 1):
        raw = raw.removesuffix("\r")
        if raw.startswith(PLANNED_AT):
            try:
                planned_at = float(raw[len(PLANNED_AT):])
            except ValueError:
                errors.append(f"line {n}: bad plan time: {raw!r}")
            continue
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        f = raw.split("\t")
        if len(f) == 4:
            f.append("")
        if len(f) != 5 or f[0] not in ACTIONS or not f[1].startswith("/") \
                or not f[2].startswith("/"):
            errors.append(f"line {n}: not a report line: {raw!r}")
            continue
        problem = ""
        if any(os.path.normpath(x) != x for x in f[1:3]):
            problem = "path is not normalised"
        lines.append(Line(f[0], Path(f[1]), Path(f[2]), f[3], f[4], problem))
    if planned_at is None and not errors:
        errors.append("no '# planned-at:' line: not a report written by --backlog")
    if errors:
        raise ReportError("\n".join(errors))
    return planned_at, lines


# ---------------------------------------------------------------- apply


def _identical(a: Path, b: Path) -> bool:
    return dupes.identical(a, b)  # never the same path: the only copy is never trashed


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


class _Reject(Exception):
    """The line is not one the planner could have produced."""


def _default_config():
    here = Path(route.__file__).resolve().parent
    return route.load_config(here / "rules.toml", here / "rules.local.toml")


class _Checker:
    """Re-validates each approved line against what planning could have produced."""

    def __init__(self, config, planned_at: float, now: float):
        self.config = config
        self.planned_at = planned_at
        self.now = now
        downloads = _downloads().resolve()
        self.leftovers = {(downloads / n) for n in LEFTOVER_DIRS}
        self.sources = {downloads, _inbox().resolve(), *self.leftovers,
                        *(r.source.resolve() for r in config.rules)}
        self.cands = {c.name: c.path for c in classify.candidates(config.smart.documents_root)}
        # routing without a model: smart items get their rule's fallback (or stay)
        off = replace(config.smart, enabled=False, model_path=None)
        self.router = route.SmartRouter(off, route.default_state_dir(), _no_model,
                                        read_only=True)

    def structure(self, ln: Line) -> None:
        if ln.problem:
            raise _Reject(ln.problem)
        if not route.within_home(ln.src) or not route.within_home(ln.dst):
            raise _Reject("outside home")
        parent = ln.src.parent.resolve()
        if parent not in self.sources:
            raise _Reject(f"{ln.src.parent} is not a folder the backlog sorts")
        if ln.action == "GROUP":
            if parent != _downloads().resolve():
                raise _Reject("project files are grouped from ~/Downloads only")
            if ln.dst.parent.parent != _development() or ln.dst.name != ln.src.name \
                    or ln.dst.parent.name in ("", ".", ".."):
                raise _Reject(f"must go to {_development()}/<name>/{ln.src.name}")
        elif ln.action == "TRASH":
            in_trash = ln.dst.parent.resolve() == _trash_dir().resolve()
            if in_trash and ln.dst.name != ln.src.name:
                raise _Reject("renamed on the way to the Trash")
        elif ln.folder:
            cand = self.cands.get(ln.folder)
            if cand is None:
                raise _Reject(f"'{ln.folder}' is not an existing sort folder")
            if ln.dst != cand / ln.src.name:
                raise _Reject(f"must go to {cand / ln.src.name}")

    def unchanged(self, ln: Line) -> None:
        src = ln.src
        if src.is_dir() and not src.is_symlink():
            changed = not route.dir_is_settled(src, self.now, self.now - self.planned_at)
        else:
            changed = os.lstat(src).st_mtime > self.planned_at
        if changed:
            raise _Skip("changed since planning")
        if ln.action == "GROUP":  # project dotfiles (.gitignore, .env) are expected here
            if not _settled(src, self.now):
                raise _Skip("still changing")
        elif route.should_skip(src, self.now):
            raise _Skip("skipped by the safety rules (hidden, partial or still changing)")

    def routed_here(self, ln: Line) -> None:
        """A MOVE without a folder must match what the rules decide for ``src`` now."""
        parent = ln.src.parent.resolve()
        inbox = parent == _inbox().resolve()
        source = _downloads() if inbox or parent in self.leftovers else ln.src.parent
        planned = route.plan_item(ln.src, self.config, self.router, self.now, source=source)
        if planned is None or planned.action.verb == "TRASH" or planned.action.dst != ln.dst \
                or (inbox and planned.rule.action == "smart"):
            raise _Reject("not where the rules send it")


def _no_model(*a, **kw):
    raise classify.ServerUnavailable("the backlog apply never starts the model")


def apply(report: Path, log_path: Path, config=None) -> list[Action]:
    """Perform the report's non-``#`` lines, each re-checked first. Returns what was done.

    A line is performed only if planning could have produced it, its source is
    still there and unchanged since the plan, and the safety rules allow it.
    """
    planned_at, lines = read_report(report)
    config = config if config is not None else _default_config()
    state = route.default_state_dir()
    now = time.time()
    check = _Checker(config, planned_at, now)
    done: list[Action] = []
    tally: Counter = Counter()
    moved: dict[Path, Path] = {}  # a line's src and planned dst -> where it really went
    groups: dict[Path, Path] = {}  # planned project folder -> folder created this run
    for ln in lines:
        try:
            check.structure(ln)
            if not os.path.lexists(ln.src):
                raise _Skip("missing (already moved or deleted)")
            check.unchanged(ln)
            if ln.action == "MOVE" and not ln.folder:
                check.routed_here(ln)
            outcome, action = _apply_line(ln, log_path, state, now, moved, groups)
        except _Reject as why:
            tally["rejected"] += 1
            print(f"REJECTED {ln.src}: {why}")
            continue
        except _Skip as why:
            tally["skipped"] += 1
            print(f"SKIPPED  {ln.src}: {why}")
            continue
        except Exception as exc:  # noqa: BLE001 - one line never stops the rest
            tally["failed"] += 1
            print(f"FAILED   {ln.src}: {exc!r}")
            try:
                route._log(log_path, "ERROR", ln.src, ln.dst)
            except OSError:
                pass
            continue
        tally[outcome] += 1
        done.append(action)
        print(f"{outcome.upper():<8} {action.src} -> {action.dst}")
    check.router.close()
    for d in sorted({ln.src.parent for ln in lines}):
        if d.name in LEFTOVER_DIRS and d.parent.resolve() == _downloads().resolve():
            _remove_if_empty(d)
    print("Backlog applied: " + ", ".join(
        f"{tally[k]} {label}" for k, label in [
            ("moved", "moved"), ("grouped", "grouped"), ("trashed", "to Trash"),
            ("duplicate", "duplicates to Trash"), ("skipped", "skipped"),
            ("rejected", "rejected"), ("failed", "failed")]))
    return done


def _apply_line(ln: Line, log_path: Path, state: Path, now: float,
                moved: dict[Path, Path], groups: dict[Path, Path]) -> tuple[str, Action]:
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
            raise _Reject(f"not byte-identical to {twin} (or it is not there)")
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
