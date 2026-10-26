"""Backlog clean-up: a reviewable dry-run report, then apply exactly the approved lines.

``plan`` routes the existing items in ``~/Documents/Inbox`` and ``~/Downloads``
(and the loose files directly in ``~/Documents``) through the same per-item
decision as live routing (``route.plan_item``) and writes a TSV report. It
moves nothing. The user vetoes lines by deleting them or prefixing them with
``#``; ``apply`` then performs only the remaining lines, re-checking each one
first.

Unsure items bound for ``~/Documents/Misc`` carry the model's topic. A topic
with enough items becomes a ``NEWFOLDER`` suggestion, written commented out:
nothing is created unless the user removes the ``#``. Planning
``~/Documents/Misc`` itself is a preview: its items get no lines of their own,
only placeholder lines (source == destination) that move only into an
accepted suggested folder.
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
import suggest
import topics
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
NEWFOLDER = "NEWFOLDER"
EXAMPLES = 3  # item names shown on a NEWFOLDER line
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


def _misc(config) -> Path:
    return Path(config.smart.documents_root) / "Misc"


@dataclass(frozen=True)
class Line:
    action: str  # MOVE | TRASH | GROUP
    src: Path
    dst: Path  # TRASH of a duplicate: the identical item that is kept
    reason: str = ""
    folder: str = ""  # candidate name for a smart decision, else empty
    topic: str = ""  # topic key (normalised) of an unsure item bound for Misc, else empty
    problem: str = field(default="", compare=False)  # set by read_report: reject this line
    label: str = field(default="", compare=False)  # the model's topic text (planning only)

    @property
    def placeholder(self) -> bool:
        """A Misc-preview line: the item stays unless its topic's NEWFOLDER is accepted."""
        return self.action == "MOVE" and self.src == self.dst and bool(self.topic)

    def tsv(self) -> str:
        reason = " ".join(self.reason.split())
        return "\t".join([self.action, str(self.src), str(self.dst), reason, self.folder,
                          self.topic])


@dataclass(frozen=True)
class NewFolder:
    """A suggested top-level ~/Documents folder for the items with topic ``key``."""

    key: str
    name: str
    count: int
    examples: str

    def tsv(self) -> str:
        return "\t".join([NEWFOLDER, self.key, self.name, str(self.count), self.examples])


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
    keep = memory.KeepList(state)
    held = [ln for ln in lines if keep.holds(ln.src)]
    if held:
        lines = [ln for ln in lines if not keep.holds(ln.src)]
        notes.extend(f"kept because you vetoed it earlier: {ln.src}" for ln in held)
    lines = _settle_duplicates(lines)
    lines, newfolders = _suggest_folders(lines, config)
    outage = smart is not None and smart.outage
    report = _write_report(state, now, lines, notes, outage, newfolders)
    counts = _counts(lines)
    print(report)
    print("  ".join(f"{a} {counts[a]}" for a in ACTIONS)
          + (f"  {NEWFOLDER} {len(newfolders)}" if newfolders else ""))
    return report


def _counts(lines: list[Line]) -> Counter:
    return Counter(ln.action for ln in lines if not ln.placeholder)


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
        # Inbox files go through the ~/Downloads file rules (unsure → Misc); folders stay.
        for child in children:
            if child.is_file():
                out += _plan_one(child, config, smart, now, _downloads(), notes)
        return out
    if resolved == _misc(config).resolve():
        return _plan_misc(children, config, smart, now, notes)
    if resolved == Path(config.smart.documents_root).resolve():
        # only the loose files: never a folder (nor Misc, Keys, Inbox and their contents)
        for child in children:
            if child.is_file() and not child.is_symlink():
                out += _plan_one(child, config, smart, now, _downloads(), notes)
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


def _plan_one(path: Path, config, smart, now: float, source: Path, notes: list[str]) -> list[Line]:
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
    if any(c in str(planned.action.dst) for c in UNSAFE_CHARS):
        notes.append(f"skipped (tab or line break in its destination): {str(path)!r}")
        return []
    if planned.action.verb == "TRASH":
        return [Line("TRASH", path, planned.action.dst, planned.reason)]
    key, label = "", ""
    if planned.topic and planned.folder is None \
            and planned.action.dst.parent.resolve() == _misc(config).resolve():
        key, label = _topic(planned.topic)
    return [Line("MOVE", path, planned.action.dst, planned.reason, planned.folder or "",
                 key, label=label)]


def _topic(text: str) -> tuple[str, str]:
    """(topic key, display text) for the model's topic; ("", "") if it has no key."""
    label = " ".join(text.split())
    key = topics.normalise(label)
    return (key, label) if key else ("", "")


def _plan_misc(children: list[Path], config, smart, now: float, notes: list[str]) -> list[Line]:
    """The Misc preview: placeholder lines for unsure items that have a topic, nothing else.

    Every regular file is classified with the ~/Downloads rules, but no item gets
    a line of its own: the Misc items stay put unless a suggested folder is accepted.
    """
    out: list[Line] = []
    for child in children:
        if not child.is_file() or child.is_symlink():
            continue
        if any(c in str(child) for c in UNSAFE_CHARS):
            notes.append(f"skipped (tab or line break in its name): {str(child)!r}")
            continue
        try:
            planned = route.plan_item(child, config, smart, now, source=_downloads())
        except Exception as exc:  # noqa: BLE001 - planning one item never stops the report
            print(f"ERROR  {child}: {exc!r}", file=sys.stderr)
            notes.append(f"not planned (error): {child}")
            continue
        pick = smart.pick(child) if smart is not None else None
        if planned is not None or pick is None or pick.folder is not None or not pick.topic:
            continue  # only an unsure item that stays in Misc feeds a suggestion
        key, label = _topic(pick.topic)
        if key:
            out.append(Line("MOVE", child, child, "stays in Misc unless you accept its new folder",
                            "", key, label=label))
    return out


def _suggest_folders(lines: list[Line], config) -> tuple[list[Line], list[NewFolder]]:
    """NEWFOLDER suggestions for topics with enough MOVE lines; topics without one are dropped.

    A topic whose name is not a valid new folder, or is close to an existing
    folder, is not suggested. Placeholder lines without a suggestion are removed.
    """
    by_key: dict[str, list[Line]] = {}
    for ln in lines:
        if ln.action == "MOVE" and ln.topic:
            by_key.setdefault(ln.topic, []).append(ln)
    root, expand = Path(config.smart.documents_root), config.smart.expand
    found: list[NewFolder] = []
    for key, items in by_key.items():
        if len(items) < config.suggest.min_items:
            continue
        labels = Counter(ln.label for ln in items if ln.label)
        name = labels.most_common(1)[0][0] if labels else key.title()
        name, err, similar = suggest.check_name(name, root, expand)
        if err or similar or any(c in name for c in UNSAFE_CHARS):
            continue
        names = [ln.src.name for ln in items]
        examples = ", ".join(names[:EXAMPLES]) + (", …" if len(names) > EXAMPLES else "")
        found.append(NewFolder(key, name, len(items), examples))
    found.sort(key=lambda nf: (-nf.count, nf.key))
    keys = {nf.key for nf in found}
    out = [ln if ln.topic in keys or not ln.topic else replace(ln, topic="", label="")
           for ln in lines if ln.topic in keys or not ln.placeholder]
    return out, found


def _settle_duplicates(lines: list[Line]) -> list[Line]:
    """Identical items in one run: the first MOVE stays a MOVE, the rest become TRASH.

    Earlier MOVEs into the same folder are simulated arrivals; they are compared
    with the same cheap checks (size, layout) before anything is hashed.
    """
    arrivals: dict[Path, list[tuple[Path, Path]]] = {}  # dest_dir -> [(src, planned path)]
    out: list[Line] = []
    for ln in lines:
        if ln.action != "MOVE" or ln.placeholder:  # a placeholder is already where it is
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
                  outage: bool, newfolders: list[NewFolder] = ()) -> Path:
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
    counts = _counts(lines)
    route_py = Path(route.__file__).resolve()
    header = [
        f"mac-organizer backlog report, planned {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}."
        " Nothing has moved yet.",
        "Each line is one proposed action:"
        " action<TAB>source<TAB>destination<TAB>reason<TAB>folder<TAB>topic",
        "  MOVE  = move the item there (a name already taken gets ' -2')",
        "  TRASH = move the item to the Trash (never deleted); for a duplicate, the",
        "          destination column is the identical copy that is kept",
        "  GROUP = move a loose project file into a NEW folder under ~/Documents/Development",
        "To veto a line: delete it, or put # at the start of it. Do not edit lines otherwise.",
        "To apply all lines that are left, run:",
        f"  python3 {shlex.quote(str(route_py))} --backlog --apply {shlex.quote(str(path))}",
        f"Counts: MOVE {counts['MOVE']}, TRASH {counts['TRASH']}, GROUP {counts['GROUP']}"
        f" ({sum(counts.values())} lines)",
        f"Leftover folders in ~/Downloads ({', '.join(LEFTOVER_DIRS)}) are removed on apply"
        " only if they end up empty.",
        "Vetoed items in ~/Downloads are remembered on apply: the regular sorting leaves"
        " them alone until they change.",
    ]
    if outage:
        header.append("WARNING: AI sorting was unavailable while planning: unsure items fell back"
                      " to their rule's folder or stay where they are.")
        if not newfolders:
            header.append("AI sorting was unavailable, so no new folders could be suggested.")
    if newfolders:
        header += [
            "Suggested new folders. To accept one, remove the # at the start of its line.",
            "  NEWFOLDER<TAB>topic<TAB>folder name<TAB>number of files<TAB>examples",
            "  You may edit the folder name (nothing else). Accepted folders are created in",
            "  ~/Documents on apply, and the lines with that topic (last column) move into it",
            "  instead of into Misc. Nothing is created for a line left commented out.",
        ]
    if any(ln.placeholder for ln in lines):
        header.append("A MOVE line whose source and destination are the same file in Misc is a"
                      " preview: that file stays in Misc unless you accept its new folder.")
    header += notes
    header.append(f"planned-at: {now!r}")
    with fh:
        fh.write("".join(f"# {h}\n" for h in header))
        fh.write("".join("#" + nf.tsv() + "\n" for nf in newfolders))  # opt-in: commented out
        fh.write("".join(ln.tsv() + "\n" for ln in lines))
    # what was planned, so apply can tell which lines the user deleted (vetoed)
    _planned_copy(path).write_text("".join(ln.tsv() + "\n" for ln in lines), encoding="utf-8")
    return path


def _planned_copy(report: Path) -> Path:
    return Path(report).with_name(Path(report).name + ".planned")


def _fields(raw: str) -> list[str] | None:
    """The six columns of an item line (v2 reports have 4 or 5), or None."""
    f = raw.split("\t")
    if len(f) in (4, 5):
        f += [""] * (6 - len(f))
    if len(f) != 6 or f[0] not in ACTIONS or not f[1].startswith("/") \
            or not f[2].startswith("/"):
        return None
    return f


def _parse_line(raw: str) -> Line | None:
    f = _fields(raw)
    return None if f is None else Line(f[0], Path(f[1]), Path(f[2]), f[3], f[4], f[5])


def vetoed_lines(report: Path, approved: list[Line]) -> list[Line]:
    """Planned lines the user vetoed: deleted (per the planned copy) or prefixed with #."""
    keep = {(ln.action, ln.src) for ln in approved}
    found: list[Line] = []
    try:
        planned = _planned_copy(report).read_text(encoding="utf-8").splitlines()
    except OSError:
        planned = []
    commented = [raw.lstrip()[1:].lstrip(" ") for raw in
                 Path(report).read_text(encoding="utf-8").splitlines()
                 if raw.lstrip().startswith("#")]
    for raw in planned + commented:
        ln = _parse_line(raw.removesuffix("\r"))
        if ln is not None and (ln.action, ln.src) not in keep:
            found.append(ln)
    return found


def _remember_vetoes(report: Path, approved: list[Line], state: Path) -> None:
    """Vetoed ~/Downloads items (and the leftover folders holding one) stay put in live sweeps."""
    downloads = _downloads()
    paths: set[Path] = set()
    for ln in vetoed_lines(report, approved):
        if ln.src.parent == downloads:
            paths.add(ln.src)
        elif ln.src.parent.parent == downloads and ln.src.parent.name in LEFTOVER_DIRS:
            paths.update({ln.src, ln.src.parent})
    if paths:
        memory.KeepList(state).add(sorted(p for p in paths if os.path.lexists(p)))


PLANNED_AT = "# planned-at: "


def read_report(report: Path) -> tuple[float, list[Line], list[NewFolder]]:
    """The plan time, the approved (non-``#``) lines and the accepted NEWFOLDER lines.

    Raises ReportError if the report is not one ``plan`` wrote or a line is not
    a report line at all (nothing is applied then). Lines that are well formed
    but not normalised carry a ``problem`` and are rejected one by one on apply;
    NEWFOLDER lines are checked one by one on apply.
    """
    lines: list[Line] = []
    newfolders: list[NewFolder] = []
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
        if raw.split("\t", 1)[0] == NEWFOLDER:
            f = raw.split("\t")
            if len(f) != 5 or not f[3].isdigit():
                errors.append(f"line {n}: not a NEWFOLDER line: {raw!r}")
                continue
            newfolders.append(NewFolder(f[1], f[2], int(f[3]), f[4]))
            continue
        f = _fields(raw)
        if f is None:
            errors.append(f"line {n}: not a report line: {raw!r}")
            continue
        problem = ""
        if any(os.path.normpath(x) != x for x in f[1:3]):
            problem = "path is not normalised"
        elif f[5] and topics.normalise(f[5]) != f[5]:
            problem = f"'{f[5]}' is not a topic key"
        lines.append(Line(f[0], Path(f[1]), Path(f[2]), f[3], f[4], f[5], problem))
    if planned_at is None and not errors:
        errors.append("no '# planned-at:' line: not a report written by --backlog")
    if errors:
        raise ReportError("\n".join(errors))
    return planned_at, lines, newfolders


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


class _Stay(Exception):
    """A Misc-preview line whose topic's folder was not accepted: the item stays."""


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
        self.documents = Path(config.smart.documents_root).resolve()
        self.misc = _misc(config).resolve()
        self.sources = {downloads, _inbox().resolve(), self.documents, *self.leftovers,
                        *(r.source.resolve() for r in config.rules)}
        self.cands = {c.name: c.path for c in classify.candidates(config.smart.documents_root, config.smart.expand)}
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
        if ln.placeholder:  # never checked against the rules: it only moves on acceptance
            if parent != self.misc or ln.folder:
                raise _Reject("a preview line (same source and destination) must be a file"
                              " directly in ~/Documents/Misc")
            return
        if ln.topic and (ln.action != "MOVE" or ln.folder or ln.dst.parent.resolve() != self.misc):
            raise _Reject("only an item going to ~/Documents/Misc has a topic")
        if parent not in self.sources:
            raise _Reject(f"{ln.src.parent} is not a folder the backlog sorts")
        if parent == self.documents and (ln.src.is_symlink() or ln.src.is_dir()):
            raise _Reject("only loose files are sorted from ~/Documents")
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

    def in_misc(self, ln: Line) -> None:
        """A preview line's item must still be a regular file directly in Misc."""
        if ln.src.is_symlink() or not ln.src.is_file() or ln.src.parent.resolve() != self.misc:
            raise _Reject("not a regular file directly in ~/Documents/Misc")

    def _replan(self, ln: Line):
        parent = ln.src.parent.resolve()
        inbox = parent == _inbox().resolve()
        downloads_rules = inbox or parent in self.leftovers or parent == self.documents
        source = _downloads() if downloads_rules else ln.src.parent
        return inbox, route.plan_item(ln.src, self.config, self.router, self.now, source=source)

    def routed_here(self, ln: Line) -> None:
        """A MOVE without a folder must match what the rules decide for ``src`` now."""
        _, planned = self._replan(ln)
        if planned is None or planned.action.verb == "TRASH" or planned.action.dst != ln.dst:
            raise _Reject("not where the rules send it")

    def trashed_by_rule(self, ln: Line) -> None:
        """A TRASH into the Trash (not a duplicate) must be what a trash rule decides now."""
        _, planned = self._replan(ln)
        if planned is None or planned.action.verb != "TRASH":
            raise _Reject("no rule sends it to the Trash")


def _no_model(*a, **kw):
    raise classify.ServerUnavailable("the backlog apply never starts the model")


def apply(report: Path, log_path: Path, config=None) -> list[Action]:
    """Perform the report's non-``#`` lines, each re-checked first. Returns what was done.

    A line is performed only if planning could have produced it, its source is
    still there and unchanged since the plan, and the safety rules allow it.
    """
    planned_at, lines, newfolders = read_report(report)
    config = config if config is not None else _default_config()
    state = route.default_state_dir()
    now = time.time()
    check = _Checker(config, planned_at, now)
    done: list[Action] = []
    tally: Counter = Counter()
    moved: dict[Path, Path] = {}  # a line's src and planned dst -> where it really went
    groups: dict[Path, Path] = {}  # planned project folder -> folder created this run
    # accepted suggestions first: topic key -> the folder created for it
    accepted = _create_new_folders(newfolders, lines, config, log_path, tally)
    for ln in lines:
        try:
            check.structure(ln)
            if ln.placeholder and ln.topic not in accepted:
                raise _Stay
            if not os.path.lexists(ln.src):
                raise _Skip("missing (already moved or deleted)")
            check.unchanged(ln)
            if ln.placeholder:
                check.in_misc(ln)
            elif ln.action == "MOVE" and not ln.folder:
                check.routed_here(ln)
            elif ln.action == "TRASH" and ln.dst.parent.resolve() == _trash_dir().resolve():
                check.trashed_by_rule(ln)
            if ln.action == "MOVE" and ln.topic in accepted:
                outcome, action = _move_into(ln, accepted[ln.topic], log_path, moved)
            else:
                outcome, action = _apply_line(ln, log_path, state, now, moved, groups)
        except _Stay:
            tally["stayed"] += 1
            print(f"STAYS    {ln.src}: stays in Misc (its new folder was not accepted)")
            continue
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
    try:
        _remember_vetoes(report, lines, state)  # after the moves: folders' final mtimes
    except Exception as exc:  # noqa: BLE001 - bookkeeping never undoes the apply
        print(f"ERROR  remembering vetoed lines: {exc!r}", file=sys.stderr)
    extra = [("newfolder", "new folders"), ("stayed", "stay in Misc")]
    print("Backlog applied: " + ", ".join(
        f"{tally[k]} {label}" for k, label in [
            ("moved", "moved"), ("grouped", "grouped"), ("trashed", "to Trash"),
            ("duplicate", "duplicates to Trash"), ("skipped", "skipped"),
            ("rejected", "rejected"), ("failed", "failed")]
        + [(k, label) for k, label in extra if tally[k]]))
    return done


def _create_new_folders(newfolders: list[NewFolder], lines: list[Line], config,
                        log_path: Path, tally: Counter) -> dict[str, Path]:
    """Create the folder of each valid un-commented NEWFOLDER line; topic key -> folder.

    Valid: its key is the topic of an approved line and appears on no earlier
    NEWFOLDER line, and its (possibly edited) name passes the same checks as
    accepting a live suggestion. Anything else is refused; the rest still applies.
    """
    root = Path(config.smart.documents_root)
    keys = {ln.topic for ln in lines if ln.topic and not ln.problem}
    seen: set[str] = set()
    accepted: dict[str, Path] = {}
    for nf in newfolders:
        try:
            if nf.key in seen:
                raise _Reject(f"topic '{nf.key}' already has a NEWFOLDER line")
            seen.add(nf.key)
            if nf.key not in keys:
                raise _Reject(f"no approved line has the topic '{nf.key}'")
            name, err, similar = suggest.check_name(nf.name, root, config.smart.expand)
            if err:
                raise _Reject(err)
            if similar:
                raise _Reject(f'a similar folder "{similar}" exists; veto this line and'
                              " move the files there by hand")
            folder = root / name
            if not route.within_home(folder):
                raise _Reject("outside home")
            try:
                folder.mkdir()  # plain mkdir: no parents, never an existing folder
            except FileExistsError:
                raise _Reject(f'a folder named "{name}" already exists') from None
            except OSError as exc:
                raise _Reject(f'could not create "{name}": {exc}') from None
        except _Reject as why:
            tally["rejected"] += 1
            print(f"REJECTED NEWFOLDER {nf.name.strip()}: {why}")
            continue
        tally["newfolder"] += 1
        accepted[nf.key] = folder
        print(f"NEWFOLDER {folder}")
        try:
            route._log(log_path, "NEWFOLDER", _misc(config), folder)
        except OSError as exc:
            print(f"ERROR  logging {folder}: {exc!r}", file=sys.stderr)
    return accepted


def _move_into(ln: Line, folder: Path, log_path: Path,
               moved: dict[Path, Path]) -> tuple[str, Action]:
    """Redirect a topic's MOVE (into Misc, or a Misc preview) into its accepted folder."""
    target = folder / ln.src.name
    if ln.src.is_dir() and not ln.src.is_symlink():
        final = fsops.move_dir_no_clobber(ln.src, target, mkdir=False)
    else:
        final = fsops.move_no_clobber(ln.src, target, mkdir=False)
    route._log(log_path, "SUGGEST", ln.src, final)
    moved[ln.src] = moved[ln.dst] = final
    return "moved", Action(ln.src, final, verb="SUGGEST")


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
